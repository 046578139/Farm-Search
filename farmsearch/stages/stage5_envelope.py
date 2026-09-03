"""Stage 5 — Dischargeable envelope (hunting safety zones).

Maryland Natural Resources §10-410 (verified 2026-09-03 at
mgaleg.maryland.gov): no hunting within 150 yards of "a dwelling house,
residence, church, or other building or camp occupied by human beings"
without the owner's or occupant's permission, and within 300 yards of a
public or nonpublic school during school hours or school activities.
Archery: 50 yards in Frederick, Carroll and Washington counties (elevated
position required in Washington). Target and practical shooting are NOT
hunting: county discharge ordinances and zoning govern them and differ by
county, so every parcel is flagged for that manual check.

Occupied structures come from the parcel fabric itself, so the answer is
uniform across the three counties:
  dwellings   parcels whose SDAT record carries a structure (SQFTSTRC >=
              dwelling_min_structure_sqft) under a residential/agricultural
              land use — located by the building footprints inside them (any
              footprint >= footprint_min_sqft counts as a candidate occupied
              building; the parcel point when no footprint is mapped)
  churches    parcels whose exemption class names a church, synagogue,
              parsonage etc. (SDAT DESCEXCL), located the same way, plus any
              configured church point layer
  schools     parcels whose exemption class names a school or college: the
              whole parcel (the grounds) buffered 300 yd, plus school points
The subject parcel's own structures, and (exclude_same_owner) structures
on the owner's other parcels, are exempt.

Envelope = usable area (Stage 3) minus every off-parcel safety zone.
Reported: envelope acres, largest block, longest straight dimension inside
the largest block (a 30 ac ribbon is useless), archery-zone envelope acres,
and counts of the structures and schools that shaped it.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import Config
from ..geometry.position import polygon_parts
from ..io.loaders import LayerNotAvailable, clean_geometries, read_layer
from ..units import ACRE_M2, m2_to_acres

log = logging.getLogger(__name__)
YARD_M = 0.9144


@dataclass
class OccupiedStructures:
    structures: gpd.GeoDataFrame     # kind (dwelling|church), account_id, owner_key, geometry (polygon or point)
    schools: gpd.GeoDataFrame        # kind (school_parcel|school_point), account_id, geometry
    footprints_available: bool = False
    missing_layers: list[str] = field(default_factory=list)


@dataclass
class Stage5Result:
    parcels: gpd.GeoDataFrame
    envelopes: gpd.GeoDataFrame
    structures: OccupiedStructures
    missing_layers: list[str] = field(default_factory=list)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def load_occupied_structures(cfg: Config, parcels_all: gpd.GeoDataFrame, clip: BaseGeometry) -> OccupiedStructures:
    """Dwelling / church / school geometry from the parcel fabric + footprints + point layers."""
    v = cfg.envelope
    missing: list[str] = []
    A = parcels_all.reset_index(drop=True)
    lu = A["land_use_desc"].astype("string").fillna("") if "land_use_desc" in A.columns else pd.Series([""] * len(A))
    sqft = _num(A["structure_sqft"]) if "structure_sqft" in A.columns else pd.Series(0.0, index=A.index)
    exc = A["exempt_class_desc"].astype("string").fillna("") if "exempt_class_desc" in A.columns else pd.Series([""] * len(A))
    is_acct = A["is_account"].astype(bool) if "is_account" in A.columns else pd.Series(True, index=A.index)
    dwelling = is_acct & (sqft >= v.dwelling_min_structure_sqft) & lu.isin(v.dwelling_land_uses).astype(bool)
    church = is_acct & exc.str.contains(v.church_exempt_regex, case=False, regex=True).astype(bool)
    school = is_acct & exc.str.contains(v.school_exempt_regex, case=False, regex=True).astype(bool)
    # a church-owned camp / school parcel is occupied too; treat schools separately (300 yd)
    dwelling = dwelling & ~school
    church = church & ~school
    log.info("occupied structures: %d dwelling parcels, %d church parcels, %d school parcels (of %d)",
             int(dwelling.sum()), int(church.sum()), int(school.sum()), len(A))

    # footprints
    fp_parts = []
    for src in v.footprint_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, clip, clip_mode="intersects"), kind="areal")
        except LayerNotAvailable as e:
            log.warning("footprint layer %s unavailable: %s", src.name, e)
            missing.append(src.name)
            continue
        g = g[g.geometry.area >= v.footprint_min_sqft * 0.09290304]
        fp_parts.append(g[[g.geometry.name]])
        log.info("footprint layer %s: %d footprints >= %.0f sq ft", src.name, len(g), v.footprint_min_sqft)
    fps = gpd.GeoDataFrame(pd.concat(fp_parts, ignore_index=True), geometry="geometry", crs=cfg.working_crs) if fp_parts else None

    owner_key = A["owner_key"] if "owner_key" in A.columns else pd.Series([None] * len(A))
    rows = []
    for kind, mask in (("dwelling", dwelling), ("church", church)):
        sub = A[mask]
        if sub.empty:
            continue
        if fps is not None and len(fps):
            pairs = fps.sindex.query(sub.geometry.values, predicate="intersects")
            by = {}
            for a, b in zip(pairs[0], pairs[1]):
                by.setdefault(int(a), []).append(int(b))
        else:
            by = {}
        for a in range(len(sub)):
            acct = sub["account_id"].values[a]
            ok = owner_key.values[sub.index[a]] if len(owner_key) else None
            pg = sub.geometry.values[a]
            hits = by.get(a, [])
            if hits:
                for b in hits:
                    fg = fps.geometry.values[b]
                    inter = fg.intersection(pg)
                    if inter.area >= 0.5 * fg.area:      # the footprint belongs to this parcel
                        rows.append({"kind": kind, "account_id": acct, "owner_key": ok, "geometry": fg})
            else:
                rows.append({"kind": kind, "account_id": acct, "owner_key": ok, "geometry": pg.representative_point()})
    for src in v.church_point_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, clip, clip_mode="intersects"), kind="any")
        except LayerNotAvailable as e:
            log.warning("church point layer %s unavailable: %s", src.name, e)
            missing.append(src.name)
            continue
        for geom in g.geometry.values:
            rows.append({"kind": "church_point", "account_id": None, "owner_key": None, "geometry": geom})
    structures = gpd.GeoDataFrame(rows, geometry="geometry", crs=cfg.working_crs) if rows else \
        gpd.GeoDataFrame({"kind": [], "account_id": [], "owner_key": []}, geometry=[], crs=cfg.working_crs)

    srows = [{"kind": "school_parcel", "account_id": acct, "geometry": pg}
             for acct, pg in zip(A.loc[school, "account_id"].values, A.geometry.values[school.values])]
    for src in v.school_point_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, clip, clip_mode="intersects"), kind="any")
        except LayerNotAvailable as e:
            log.warning("school point layer %s unavailable: %s", src.name, e)
            missing.append(src.name)
            continue
        for geom in g.geometry.values:
            srows.append({"kind": "school_point", "account_id": None, "geometry": geom})
    schools = gpd.GeoDataFrame(srows, geometry="geometry", crs=cfg.working_crs) if srows else \
        gpd.GeoDataFrame({"kind": [], "account_id": []}, geometry=[], crs=cfg.working_crs)
    return OccupiedStructures(structures=structures, schools=schools, footprints_available=fps is not None, missing_layers=missing)


def longest_interior_chord_m(poly: BaseGeometry, max_pts: int = 64) -> float:
    """Length of the longest straight segment that stays inside `poly`
    (the largest part if multipart). Endpoints are sampled on the boundary;
    candidate pairs are tested longest-first against a prepared geometry, so
    the first covered chord is the answer."""
    from shapely.prepared import prep
    parts = sorted(polygon_parts(poly), key=lambda p: -p.area)
    if not parts:
        return 0.0
    P = parts[0]
    tol = math.sqrt(P.area) / 50.0
    Q = P.simplify(tol, preserve_topology=True) if tol > 0 else P
    ring = Q.exterior
    n = max(8, min(max_pts, len(ring.coords)))
    pts = np.array([[pt.x, pt.y] for pt in (ring.interpolate(i / n, normalized=True) for i in range(n))])
    d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    iu = np.triu_indices(n, k=1)
    order = np.argsort(-d[iu])
    covers = prep(P.buffer(0.05)).covers
    for k in order:
        i, j = iu[0][k], iu[1][k]
        if covers(LineString([pts[i], pts[j]])):
            return float(d[i, j])
    return 0.0


def run_stage5(cfg: Config, scored: gpd.GeoDataFrame, s3geoms: dict[str, dict], occ: OccupiedStructures,
               missing_layers: Optional[list[str]] = None) -> Stage5Result:
    v = cfg.envelope
    P = scored.reset_index(drop=True).copy()
    safety = v.safety_buffer_yards * YARD_M
    school_b = v.school_buffer_yards * YARD_M
    archery = v.archery_buffer_yards * YARD_M
    cols = {"dischargeable_envelope_acres": np.nan, "dischargeable_envelope_largest_block_acres": np.nan,
            "dischargeable_envelope_longest_dim_yards": np.nan, "archery_envelope_acres": np.nan,
            "occupied_structures_within_safety_zone": 0, "schools_within_school_zone": 0,
            "own_structures_exempted": 0, "envelope_flags": None}
    for k, val in cols.items():
        P[k] = val
    P["envelope_flags"] = [[] for _ in range(len(P))]
    S = occ.structures
    Sc = occ.schools
    if len(S):
        _ = S.sindex
    if len(Sc):
        _ = Sc.sindex
    okeys = P["owner_key"].values if "owner_key" in P.columns else np.array([None] * len(P))
    env_rows = []
    for i, (acct, pg) in enumerate(zip(P["account_id"].values, P.geometry.values)):
        g3 = s3geoms.get(acct, {})
        usable = g3.get("usable", pg)
        flags = P.at[i, "envelope_flags"]
        reach = usable.buffer(max(safety, school_b))
        zones = []
        zones_archery = []
        n_struct = n_own = 0
        if len(S):
            hits = S.sindex.query(reach, predicate="intersects")
            for h in hits:
                if S["account_id"].values[h] == acct:
                    n_own += 1
                    continue
                if v.exclude_same_owner and okeys[i] and S["owner_key"].values[h] == okeys[i]:
                    n_own += 1
                    continue
                geom = S.geometry.values[h]
                if geom.distance(usable) <= safety:
                    n_struct += 1
                    zones.append(geom.buffer(safety))
                    zones_archery.append(geom.buffer(archery))
        n_school = 0
        if len(Sc):
            hits = Sc.sindex.query(reach, predicate="intersects")
            for h in hits:
                if Sc["account_id"].values[h] == acct:
                    continue
                geom = Sc.geometry.values[h]
                if geom.distance(usable) <= school_b:
                    n_school += 1
                    zones.append(geom.buffer(school_b))
                    zones_archery.append(geom.buffer(school_b))
        env = usable.difference(unary_union(zones)) if zones else usable
        env_a = usable.difference(unary_union(zones_archery)) if zones_archery else usable
        parts = sorted(polygon_parts(env), key=lambda p: -p.area)
        largest = parts[0] if parts else None
        P.at[i, "dischargeable_envelope_acres"] = round(m2_to_acres(env.area), 2)
        P.at[i, "dischargeable_envelope_largest_block_acres"] = round(m2_to_acres(largest.area), 2) if largest is not None else 0.0
        P.at[i, "dischargeable_envelope_longest_dim_yards"] = round(longest_interior_chord_m(env) / YARD_M, 0) if largest is not None else 0.0
        P.at[i, "archery_envelope_acres"] = round(m2_to_acres(env_a.area), 2)
        P.at[i, "occupied_structures_within_safety_zone"] = n_struct
        P.at[i, "schools_within_school_zone"] = n_school
        P.at[i, "own_structures_exempted"] = n_own
        if m2_to_acres(env.area) < v.min_dischargeable_acres:
            flags.append("dischargeable_envelope_below_minimum")
        if largest is None or P.at[i, "dischargeable_envelope_longest_dim_yards"] < v.min_envelope_length_yards:
            flags.append("dischargeable_envelope_too_short_for_range_bay")
        flags.append("target_shooting_verify_county_discharge_ordinance_and_zoning")
        if not occ.footprints_available:
            flags.append("safety_zones_from_parcel_points_no_footprints")
        env_rows.append({"account_id": acct, "envelope_acres": P.at[i, "dischargeable_envelope_acres"], "geometry": env})
    envelopes = gpd.GeoDataFrame(env_rows, geometry="geometry", crs=P.crs) if env_rows else \
        gpd.GeoDataFrame({"account_id": [], "envelope_acres": []}, geometry=[], crs=P.crs)
    return Stage5Result(parcels=P, envelopes=envelopes, structures=occ, missing_layers=(missing_layers or []) + occ.missing_layers)
