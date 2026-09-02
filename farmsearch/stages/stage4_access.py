"""Stage 4 — Access connectivity (the core algorithm).

One routine, three failure modes:
  1. Entry nodes: parcel boundary x public right-of-way.
  2. Landlock: no direct contact with any public ROW -> landlocked_apparent.
  3. Frontage blockage: share of the road-facing boundary covered by a hostile
     constraint or by a separately-owned parcel; reserve-strip detection with
     the offending account ID attached.
  4. Internal connectivity: connected components of the usable polygon seeded
     at the entry nodes -> largest contiguous reachable block, unreachable
     islands.

Every flag is a reason to look at the deed, never a reason to delete the row.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import Config
from ..geometry.connectivity import connected_components, reachable_usable_via, seed_components
from ..geometry.frontage import analyze_frontage
from ..geometry.strips import is_strip, strip_metrics
from ..io.loaders import erase_layer, LayerNotAvailable, clean_geometries, read_layer
from ..geometry.position import merge_lines
from ..owners import owners_match
from ..units import ACRE_M2, ft_to_m, m2_to_acres, m_to_ft

log = logging.getLogger(__name__)

STRIP_MIN_SHARED_FRACTION = 0.25   # strip must run along >= this share of its length against the subject


@dataclass
class Stage4Result:
    parcels: gpd.GeoDataFrame
    frontage: gpd.GeoDataFrame
    entry_points: gpd.GeoDataFrame
    strips: pd.DataFrame
    islands: pd.DataFrame
    missing_layers: list[str] = field(default_factory=list)


def load_row_layers(cfg: Config, study_geom: BaseGeometry) -> tuple[gpd.GeoDataFrame, list[str]]:
    parts = []
    missing = []
    for r in cfg.access.row_layers:
        try:
            g = read_layer(r.source, cfg.working_crs, study_geom, clip_mode="clip")
        except LayerNotAvailable as e:
            log.warning("ROW layer %s unavailable: %s", r.source.name, e)
            missing.append(r.source.name)
            continue
        if r.geometry == "line":
            g = g.copy()
            g[g.geometry.name] = g.geometry.buffer(ft_to_m(r.row_width_ft) / 2.0, cap_style="flat")
        if r.erase is not None:
            g = erase_layer(g, r.erase, cfg.working_crs, study_geom, what=r.source.name)
        g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        n = len(g)
        g = gpd.GeoDataFrame({"row_layer": [r.source.name] * n, "authority": [r.authority] * n, "public": [r.public] * n},
                             geometry=g.geometry.values, crs=cfg.working_crs)
        parts.append(g)
        log.info("ROW layer %s: %d features", r.source.name, len(g))
    if not parts:
        return gpd.GeoDataFrame({"row_layer": [], "authority": [], "public": []}, geometry=[], crs=cfg.working_crs), missing
    out = pd.concat(parts, ignore_index=True)
    return clean_geometries(gpd.GeoDataFrame(out, geometry="geometry", crs=cfg.working_crs)).reset_index(drop=True), missing


def _merge_count(lines: list[LineString]) -> int:
    """Number of connected runs in a set of line pieces."""
    if not lines:
        return 0
    m = merge_lines(unary_union(lines))
    if m.is_empty:
        return 0
    return 1 if isinstance(m, LineString) else len(m.geoms)


def run_stage4(cfg: Config, parcels_all: gpd.GeoDataFrame, target_mask: pd.Series,
               s3geoms: dict[str, dict], rows: gpd.GeoDataFrame, missing_layers: Optional[list[str]] = None) -> Stage4Result:
    """parcels_all: every parcel in the study area (neighbors matter even when
    they fail Stage 1 — a 2-acre reserve strip never passes the acreage cut).
    target_mask: which rows to score (stage1_pass by default)."""
    a = cfg.access
    P = parcels_all.reset_index(drop=True).copy()
    mask = target_mask.reset_index(drop=True).values.astype(bool)
    public_rows = rows[rows["public"]].reset_index(drop=True)
    _ = P.sindex
    if not public_rows.empty:
        _ = public_rows.sindex
    sliver_m2 = a.sliver_acres * ACRE_M2

    cols = {
        "road_facing_ft": 0.0, "row_contact_ft": 0.0, "frontage_open_ft": 0.0, "frontage_encumbered_ft": 0.0,
        "frontage_foreign_ft": 0.0, "frontage_same_owner_ft": 0.0, "frontage_gap_ft": 0.0,
        "frontage_blocked_pct": np.nan, "frontage_foreign_pct": np.nan,
        "no_public_row_nearby": False, "landlocked_apparent": False, "frontage_blocked_by_foreign_parcel": False,
        "blocking_parcel_account_id": None, "blocking_parcel_owner": None, "blocking_parcels_json": "[]",
        "access_via_same_owner_parcel": False, "frontage_authorities": "", "entry_node_count": 0,
        "reserve_strip_detected": False, "reserve_strips_json": "[]",
        "largest_contiguous_reachable_acres": 0.0, "reachable_usable_acres": 0.0,
        "unreachable_island_count": 0, "unreachable_island_acres": 0.0, "unreachable_islands_json": "[]",
        "sliver_acres": 0.0, "reachable_if_crossings_permitted_acres": 0.0,
        "largest_reachable_if_crossings_permitted_acres": 0.0, "islands_reconnectable_by_permit_acres": 0.0,
        "access_flags": None,
    }
    for k, v in cols.items():
        P[k] = v
    P["access_flags"] = [[] for _ in range(len(P))]

    front_rows, entry_rows, strip_rows, island_rows = [], [], [], []
    # Neighbours that ARE road: a polygon whose area is mostly inside the
    # public ROW (roads held as assessment accounts, unbuffered tax-map
    # slivers). They are road contact for the frontage probe and never
    # reserve strips. Computed lazily; most parcels are never probed.
    _row_like_cache: dict[int, bool] = {}

    def is_row_like(idx: int) -> bool:
        if idx in _row_like_cache:
            return _row_like_cache[idx]
        val = False
        if not public_rows.empty:
            g = P.geometry.values[idx]
            hits = public_rows.sindex.query(g, predicate="intersects")
            if len(hits) and g.area > 0:
                inter = g.intersection(unary_union(list(public_rows.geometry.values[hits]))).area
                val = inter / g.area >= a.row_parcel_overlap
        _row_like_cache[idx] = val
        return val

    owners = P["owner_name"].values
    addrs = P["owner_mailing_address"].values if "owner_mailing_address" in P.columns else np.array([None] * len(P))
    deeds = P["deed_ref"].values if "deed_ref" in P.columns else np.array([None] * len(P))
    accts = P["account_id"].values

    for i in np.flatnonzero(mask):
        acct = accts[i]
        pg = P.geometry.values[i]
        g3 = s3geoms.get(acct, {"usable": pg, "traversable": pg, "hostile": {}})
        hostile = g3["hostile"]
        flags: list[str] = []

        # -- 1-3. Frontage ------------------------------------------------
        fr = analyze_frontage(int(i), pg, owners[i], addrs[i], P, public_rows, hostile,
                              search_ft=a.frontage_search_ft, sample_ft=a.frontage_sample_ft,
                              contact_tol_ft=a.contact_tolerance_ft, open_gap_ft=a.open_gap_ft,
                              subject_deed=deeds[i], row_like=is_row_like)
        L = fr.length_by_class()
        facing = fr.road_facing_m
        direct = L["open"] + L["encumbered"]
        P.at[i, "road_facing_ft"] = round(m_to_ft(facing), 1)
        P.at[i, "row_contact_ft"] = round(m_to_ft(fr.row_contact_m), 1)
        for k in ("open", "encumbered", "foreign_parcel", "same_owner_parcel", "gap"):
            col = {"foreign_parcel": "frontage_foreign_ft", "same_owner_parcel": "frontage_same_owner_ft"}.get(k, f"frontage_{k}_ft")
            P.at[i, col] = round(m_to_ft(L[k]), 1)
        P.at[i, "no_public_row_nearby"] = not fr.nearby_row
        determinate = facing - L["gap"]
        if determinate > 0:
            P.at[i, "frontage_blocked_pct"] = round(100 * (L["encumbered"] + L["foreign_parcel"]) / determinate, 1)
            P.at[i, "frontage_foreign_pct"] = round(100 * L["foreign_parcel"] / determinate, 1)
        landlocked = direct < ft_to_m(a.min_contact_ft)
        P.at[i, "landlocked_apparent"] = bool(landlocked)
        if landlocked:
            flags.append("landlocked_apparent_check_deeded_access")
        if L["same_owner_parcel"] > 0 and landlocked:
            P.at[i, "access_via_same_owner_parcel"] = True
            flags.append("access_via_separately_deeded_same_owner_parcel")
        blocked = determinate > 0 and (L["foreign_parcel"] / determinate) >= a.frontage_blocked_threshold
        P.at[i, "frontage_blocked_by_foreign_parcel"] = bool(blocked)
        bl = fr.blocking_lengths()
        if bl:
            top = max(bl, key=bl.get)
            P.at[i, "blocking_parcel_account_id"] = top
            j = int(np.flatnonzero(accts == top)[0])
            P.at[i, "blocking_parcel_owner"] = owners[j]
            P.at[i, "blocking_parcels_json"] = json.dumps([{"account_id": k, "frontage_ft": round(m_to_ft(v), 1)}
                                                           for k, v in sorted(bl.items(), key=lambda kv: -kv[1])])
            if blocked:
                flags.append("frontage_blocked_confirm_ownership_and_deeded_access")
        P.at[i, "frontage_authorities"] = ";".join(fr.authorities())
        if fr.authorities() and "state" in fr.authorities():
            flags.append("entrance_permit_sha_state_road")
        if L["gap"] > 0.5 * facing and facing > 0:
            flags.append("frontage_indeterminate_row_gap")
        entry_pts = fr.entry_points()
        P.at[i, "entry_node_count"] = _merge_count([s.geom for s in fr.subsegments if s.is_entry])

        for s in fr.subsegments:
            front_rows.append({"account_id": acct, "class": s.cls, "outside": s.outside, "inside_constraint": s.inside_constraint,
                               "blocking_account_id": s.blocking_account_id, "authority": s.authority,
                               "row_distance_ft": s.row_distance_ft, "length_ft": round(m_to_ft(s.geom.length), 1),
                               "geometry": s.geom})
        for pt in entry_pts:
            entry_rows.append({"account_id": acct, "geometry": pt})

        # -- Reserve strips -----------------------------------------------
        cand = {s.blocking_index for s in fr.subsegments if s.blocking_index is not None}
        if not public_rows.empty:
            tol = ft_to_m(a.contact_tolerance_ft)
            for c in P.sindex.query(pg.buffer(tol), predicate="intersects"):
                c = int(c)
                if c != i and len(public_rows.sindex.query(P.geometry.values[c].buffer(tol), predicate="intersects")):
                    cand.add(c)
        strips_here = []
        for c in sorted(cand):
            cg = P.geometry.values[c]
            m = strip_metrics(cg)
            if not is_strip(m, a.strip_max_width_ft, a.strip_min_aspect):
                continue
            # Miles-long "strips" are road, rail or utility corridors; a
            # neighbour that is mostly public ROW is the road itself.
            if m["est_length_ft"] > a.strip_max_length_ft or is_row_like(int(c)):
                continue
            # A strip merely touching the subject at a corner is someone else's
            # problem: it must block the subject's probes, or run along a
            # substantial share of the subject's boundary.
            shared = pg.boundary.intersection(cg.buffer(ft_to_m(a.contact_tolerance_ft))).length
            blocked_ft = m_to_ft(bl.get(accts[c], 0.0))
            if blocked_ft <= 0 and m_to_ft(shared) < STRIP_MIN_SHARED_FRACTION * m["est_length_ft"]:
                continue
            same = owners_match(owners[i], owners[c], addrs[i], addrs[c], deed_a=deeds[i], deed_b=deeds[c])
            rec = {"account_id": acct, "strip_account_id": accts[c], "strip_owner": owners[c], "same_owner": bool(same),
                   "frontage_ft_blocked": round(blocked_ft, 1), "shared_boundary_ft": round(m_to_ft(shared), 1), **m}
            strips_here.append(rec)
            strip_rows.append(rec)
        if strips_here:
            P.at[i, "reserve_strips_json"] = json.dumps(strips_here)
            if any(not r["same_owner"] for r in strips_here):
                P.at[i, "reserve_strip_detected"] = True
                flags.append("reserve_strip_foreign_owner")

        # -- 4. Internal connectivity --------------------------------------
        usable = g3["usable"]
        comps, sliver = connected_components(usable, sliver_m2)
        conn = seed_components(comps, entry_pts, tol_m=1.0)
        P.at[i, "largest_contiguous_reachable_acres"] = round(m2_to_acres(conn.largest_reachable_area), 2)
        P.at[i, "reachable_usable_acres"] = round(m2_to_acres(conn.reachable_area), 2)
        P.at[i, "unreachable_island_count"] = len(conn.islands)
        isl = [round(m2_to_acres(x), 2) for x in conn.island_areas]
        P.at[i, "unreachable_island_acres"] = round(sum(isl), 2)
        P.at[i, "unreachable_islands_json"] = json.dumps([{"acres": x} for x in isl])
        P.at[i, "sliver_acres"] = round(m2_to_acres(sliver), 2)
        for n, x in enumerate(isl):
            island_rows.append({"account_id": acct, "island_rank": n + 1, "acres": x})
        tot_x, largest_x = reachable_usable_via(g3["traversable"], usable, entry_pts, sliver_m2)
        P.at[i, "reachable_if_crossings_permitted_acres"] = round(m2_to_acres(tot_x), 2)
        P.at[i, "largest_reachable_if_crossings_permitted_acres"] = round(m2_to_acres(largest_x), 2)
        recon = max(0.0, m2_to_acres(tot_x) - m2_to_acres(conn.reachable_area))
        P.at[i, "islands_reconnectable_by_permit_acres"] = round(recon, 2)
        if conn.islands and recon > a.sliver_acres:
            flags.append("islands_reconnectable_via_mde_crossing_permit")
        if usable.area > 0 and conn.reachable_area == 0 and not landlocked:
            flags.append("usable_area_unreachable_from_frontage")
        P.at[i, "access_flags"] = flags

    crs = P.crs
    frontage = gpd.GeoDataFrame(front_rows, geometry="geometry", crs=crs) if front_rows else \
        gpd.GeoDataFrame({"account_id": [], "class": []}, geometry=[], crs=crs)
    entry = gpd.GeoDataFrame(entry_rows, geometry="geometry", crs=crs) if entry_rows else \
        gpd.GeoDataFrame({"account_id": []}, geometry=[], crs=crs)
    return Stage4Result(parcels=P, frontage=frontage, entry_points=entry,
                        strips=pd.DataFrame(strip_rows), islands=pd.DataFrame(island_rows),
                        missing_layers=missing_layers or [])
