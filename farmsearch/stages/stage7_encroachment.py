"""Stage 7 — Future encroachment.

Present buildout is the wrong question. For every scored parcel this stage
looks at the land that ADJOINS it and asks what it is likely to become:

  adjacent_residential_zoning_acres   residential zoning on adjoining land
                                      (the threat is zoning, not houses)
  adjacent_planned_sewer              any adjoining parcel inside a planned
                                      sewer service category — the strongest
                                      available predictor of subdivision
  subject_in_pfa / adjacent_pfa_acres Priority Funding Area status
  approved_unbuilt_units_within_2mi   units approved but not built, from the
                                      county development pipelines, within
                                      pipeline_radius_ft of the parcel
  adjacent_permanently_eased_acres    adjoining land under a permanent
                                      agricultural easement (neighbours who
                                      will still be neighbours in 20 years)

Adjoining parcels come from the full Stage 1 frame, context rows included,
so a parcel on the study line sees its real neighbours. Their zoning is
resolved here with the same layers and mappings Stage 1 uses; a code is
residential when the mapping says `is_residential`, else when its code is
R-prefixed or its description mentions "residential".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import Config, LayerSource
from ..io.loaders import LayerNotAvailable, clean_geometries, read_layer
from ..units import ACRE_M2, ft_to_m, m2_to_acres

log = logging.getLogger(__name__)


@dataclass
class Coverage:
    """A layer built from several per-county sources. `counties` is None when at
    least one loaded source is statewide; otherwise it lists the counties whose
    source loaded, and a parcel in any other county gets a null column rather
    than a confident False / 0."""
    geom: Optional[BaseGeometry] = None
    counties: Optional[set] = None

    def covers(self, county) -> bool:
        if self.geom is None:
            return False
        return self.counties is None or str(county) in self.counties

    def __bool__(self) -> bool:
        return self.geom is not None


@dataclass
class EncroachmentLayers:
    planned_sewer: Coverage = field(default_factory=Coverage)
    existing_sewer: Coverage = field(default_factory=Coverage)
    pfa: Coverage = field(default_factory=Coverage)
    growth_area: Coverage = field(default_factory=Coverage)
    pipeline: Optional[gpd.GeoDataFrame] = None      # columns: units, geometry
    pipeline_counties: Optional[set] = None          # None = every county covered
    favorable: Optional[BaseGeometry] = None          # permanent agricultural easements (Stage 2 favorable layers)


@dataclass
class Stage7Result:
    parcels: gpd.GeoDataFrame
    missing_layers: list[str] = field(default_factory=list)


def _union_of(sources: list[LayerSource], cfg: Config, clip: BaseGeometry, missing: list[str], what: str) -> Coverage:
    geoms = []
    any_loaded = False
    counties: Optional[set] = set()
    for src in sources:
        try:
            g = read_layer(src, cfg.working_crs, clip, clip_mode="clip")
        except LayerNotAvailable as e:
            log.warning("%s layer %s unavailable: %s", what, src.name, e)
            missing.append(src.name)
            continue
        any_loaded = True
        if counties is not None:
            if src.county:
                counties.add(str(src.county))
            else:
                counties = None          # a statewide source covers every county
        g = clean_geometries(g)
        if len(g):
            geoms.append(unary_union(list(g.geometry.values)))
        log.info("%s layer %s: %d features", what, src.name, len(g))
    if not any_loaded:
        return Coverage()
    uncovered = sorted(set(cfg.counties.values()) - counties) if counties is not None else []
    if uncovered:
        log.warning("%s: no source loaded for %s; the column stays null there", what, ", ".join(uncovered))
    return Coverage(geom=unary_union(geoms) if geoms else unary_union([]), counties=counties)


def load_encroachment_layers(cfg: Config, clip: BaseGeometry, favorable_layers: Optional[dict[str, gpd.GeoDataFrame]] = None
                             ) -> tuple[EncroachmentLayers, list[str]]:
    e = cfg.encroachment
    missing: list[str] = []
    L = EncroachmentLayers()
    L.planned_sewer = _union_of(e.sewer_layers, cfg, clip, missing, "planned sewer")
    L.existing_sewer = _union_of(e.sewer_existing_layers, cfg, clip, missing, "existing sewer")
    L.pfa = _union_of(e.pfa_layers, cfg, clip, missing, "PFA")
    L.growth_area = _union_of(e.growth_area_layers, cfg, clip, missing, "growth area")
    parts = []
    pipe_counties: Optional[set] = set()
    for ul in e.pipeline_layers:
        try:
            g = read_layer(ul.source, cfg.working_crs, clip.buffer(ft_to_m(e.pipeline_radius_ft)), clip_mode="intersects")
        except LayerNotAvailable as ex:
            log.warning("pipeline layer %s unavailable: %s", ul.source.name, ex)
            missing.append(ul.source.name)
            continue
        if ul.units_field not in g.columns:
            log.warning("pipeline layer %s: units field %r missing; fields %s", ul.source.name, ul.units_field, list(g.columns)[:20])
            missing.append(ul.source.name)
            continue
        units = pd.to_numeric(g[ul.units_field], errors="coerce").fillna(0.0)
        parts.append(gpd.GeoDataFrame({"units": units.values, "source": ul.source.name}, geometry=g.geometry.values, crs=cfg.working_crs))
        pipe_counties = None if (pipe_counties is None or not ul.source.county) else pipe_counties | {str(ul.source.county)}
        log.info("pipeline layer %s: %d features, %.0f units", ul.source.name, len(g), float(units.sum()))
    if parts:
        L.pipeline = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=cfg.working_crs)
        L.pipeline_counties = pipe_counties
        if pipe_counties is not None:
            gap = sorted(set(cfg.counties.values()) - pipe_counties)
            if gap:
                log.warning("approved-unbuilt units: no pipeline layer for %s; the column stays null there", ", ".join(gap))
    else:
        L.pipeline = None
    if favorable_layers:
        geoms = [unary_union(list(g.geometry.values)) for g in favorable_layers.values() if len(g)]
        L.favorable = unary_union(geoms) if geoms else unary_union([])
    return L, missing


def load_favorable_layers(cfg: Config, clip: BaseGeometry, missing: Optional[list[str]] = None) -> dict[str, gpd.GeoDataFrame]:
    """The Stage 2 layers whose implication is favorable: permanent agricultural easements."""
    out = {}
    for c in cfg.constraints:
        if c.implication != "favorable" or c.source is None:
            continue
        try:
            out[c.name] = clean_geometries(read_layer(c.source, cfg.working_crs, clip, clip_mode="clip"))
        except LayerNotAvailable as e:
            log.warning("favorable layer %s unavailable for Stage 7: %s", c.name, e)
            if missing is not None:
                missing.append(c.source.name)
    return out


_RES_RE = re.compile(r"resid", re.I)


def residential_codes(cfg: Config) -> dict[str, set[str]]:
    """county -> zoning codes counted as residential, from every zoning spec's mapping."""
    out: dict[str, set[str]] = {}
    explicit_false: dict[str, set[str]] = {}
    for spec in cfg.zoning:
        try:
            spec.load_mapping()
        except Exception as e:  # noqa: BLE001
            log.warning("zoning mapping %s: %s", spec.source.name, e)
            continue
        codes = out.setdefault(spec.county, set())
        for code, m in spec.codes.items():
            res = m.get("is_residential")
            if res is False:
                explicit_false.setdefault(spec.county, set()).add(code)
                continue
            if res is None:
                # inferred from the description only ("Residential", "Residence"); a code
                # letter is not evidence: ROW is a right of way, RC resource conservation
                desc = m.get("description") or ""
                res = (m.get("is_agricultural") is not True) and bool(_RES_RE.search(desc))
            if res:
                codes.add(code)
    for county, bad in explicit_false.items():
        out.get(county, set()).difference_update(bad)
    return out


def run_stage7(cfg: Config, scored: gpd.GeoDataFrame, parcels_all: gpd.GeoDataFrame,
               zoning_layers: dict[str, gpd.GeoDataFrame], layers: EncroachmentLayers,
               missing_layers: Optional[list[str]] = None) -> Stage7Result:
    from .stage1_base_filter import assign_zoning
    e = cfg.encroachment
    P = scored.reset_index(drop=True).copy()
    A = parcels_all.reset_index(drop=True)
    tol = ft_to_m(e.adjacency_tolerance_ft)
    cols = {"adjacent_parcel_count": 0, "adjacent_acres": 0.0, "adjacent_residential_zoning_acres": np.nan,
            "adjacent_residential_zoning_pct": np.nan, "subject_planned_sewer": None, "adjacent_planned_sewer": None,
            "adjacent_existing_sewer": None, "subject_in_pfa": None, "subject_pfa_acres": np.nan, "adjacent_pfa_acres": np.nan,
            "subject_in_growth_area": None, "adjacent_in_growth_area": None,
            "approved_unbuilt_units_within_2mi": np.nan, "approved_unbuilt_units_radius_ft": np.nan,
            "adjacent_permanently_eased_acres": np.nan,
            "adjacent_permanently_eased_pct": np.nan, "adjacent_zoning_codes": "",
            "adjacent_boundary_covered_pct": np.nan, "encroachment_flags": None}
    for k, v in cols.items():
        P[k] = v
    P["encroachment_flags"] = [[] for _ in range(len(P))]

    # ---- neighbours of every scored parcel ----------------------------------
    _ = A.sindex
    acct_all = A["account_id"].values
    # Placeholder polygons (UNK, WATER, RAILROAD, common elements) are blockers,
    # not adjoining parcels: they would inflate the counts and dilute every share.
    is_acct_all = A["is_account"].values.astype(bool) if "is_account" in A.columns else np.ones(len(A), bool)
    neigh: dict[int, list[int]] = {}
    touch: dict[int, list[int]] = {}
    for i, (acct, pg) in enumerate(zip(P["account_id"], P.geometry)):
        cands = [int(c) for c in A.sindex.query(pg.buffer(tol), predicate="intersects") if acct_all[c] != acct]
        neigh[i] = [c for c in cands if is_acct_all[c]]
        touch[i] = cands            # blockers included: they still cover the boundary
    all_neigh = sorted({c for lst in neigh.values() for c in lst})
    N = A.iloc[all_neigh].copy()
    N["_pos"] = all_neigh
    # zoning of the neighbours (any size): same layers and mappings as Stage 1
    res_codes = residential_codes(cfg)
    if len(N) and zoning_layers:
        cfg_flag = cfg.on_unmapped_zoning
        try:
            cfg.on_unmapped_zoning = "flag"      # a neighbour with an unmapped code must not abort the run
            Z = assign_zoning(N[["account_id", "county", A.geometry.name]] if "county" in N.columns else N, zoning_layers, cfg)
        finally:
            cfg.on_unmapped_zoning = cfg_flag
        zcode = dict(zip(Z["account_id"].values, Z["zoning"].values))
        zcounty = dict(zip(Z["account_id"].values, Z["county"].values))
        zall = dict(zip(Z["account_id"].values, Z["zoning_codes_all"].values))
    else:
        zcode, zcounty, zall = {}, {}, {}

    zunmapped = dict(zip(Z["account_id"].values, Z["zoning_unmapped"].values)) if len(N) and zoning_layers else {}

    def residential_share(acct: str) -> float:
        """Share of the neighbour's area under residential codes: a split-zoned
        neighbour (60% A / 40% R1) contributes 40% of its acres."""
        cty = zcounty.get(acct)
        codes = res_codes.get(cty, set())
        spec = zall.get(acct)
        if not spec or not codes:
            code = zcode.get(acct)
            return 1.0 if (code is not None and str(code) in codes) else 0.0
        share = 0.0
        for part in str(spec).split(";"):
            if ":" not in part:
                continue
            code, pct = part.rsplit(":", 1)
            try:
                if code.strip() in codes:
                    share += float(pct.strip().rstrip("%")) / 100.0
            except ValueError:
                continue
        return min(1.0, share)
    # planned/existing sewer, PFA, growth area, easements per neighbour
    def covered(geom: BaseGeometry, layer) -> float:
        g = layer.geom if isinstance(layer, Coverage) else layer
        if g is None or g.is_empty:
            return 0.0
        return geom.intersection(g).area
    ng = dict(zip(N["_pos"].values, N.geometry.values))
    nacct = dict(zip(N["_pos"].values, N["account_id"].values))
    ag = dict(zip(range(len(A)), A.geometry.values))
    road_facing = P["road_facing_ft"].values if "road_facing_ft" in P.columns else np.zeros(len(P))
    pipe = layers.pipeline
    if pipe is not None and len(pipe):
        _ = pipe.sindex
    radius = ft_to_m(e.pipeline_radius_ft)
    if abs(e.pipeline_radius_ft - 10560.0) > 1.0:
        # the spec fixes the column name at 2 miles; the record carries the real radius
        log.warning("encroachment.pipeline_radius_ft is %.0f ft, not the 10,560 ft the column name "
                    "approved_unbuilt_units_within_2mi implies; approved_unbuilt_units_radius_ft records the value used",
                    e.pipeline_radius_ft)
    for i, pg in enumerate(P.geometry.values):
        if i and i % 500 == 0:
            log.info("Stage 7: %d/%d parcels", i, len(P))
        idx = neigh[i]
        cty = str(P["county"].values[i]) if "county" in P.columns else None
        P.at[i, "adjacent_parcel_count"] = len(idx)
        acres = 0.0; res_acres = 0.0; pfa_acres = 0.0; eased = 0.0
        planned = existing = growth = False
        codes = []
        any_unmapped = False
        for c in idx:
            g = ng[c]; a_ac = m2_to_acres(g.area); acres += a_ac
            code = zcode.get(nacct[c])
            if zunmapped.get(nacct[c]):
                any_unmapped = True         # an unmapped district may well be residential
            if code is not None:
                codes.append(str(code))
                res_acres += a_ac * residential_share(nacct[c])
            if layers.planned_sewer and covered(g, layers.planned_sewer) > 0.01 * g.area:
                planned = True
            if layers.existing_sewer and covered(g, layers.existing_sewer) > 0.01 * g.area:
                existing = True
            if layers.pfa:
                pfa_acres += m2_to_acres(covered(g, layers.pfa))
            if layers.growth_area and covered(g, layers.growth_area) > 0.01 * g.area:
                growth = True
            if layers.favorable is not None:
                eased += m2_to_acres(covered(g, layers.favorable))
        # how much of the subject's boundary is accounted for by polygons we hold:
        # a low share means the fabric stops here (a county outside `counties:`)
        try:
            ring = pg.exterior if pg.geom_type == "Polygon" else unary_union([p.exterior for p in pg.geoms])
            if ring.length:
                cov = ring.intersection(unary_union([ag[c] for c in touch[i]])).length if touch[i] else 0.0
                # boundary on a public road is accounted for too: the ROW polygons are
                # not parcels, so they would otherwise read as a hole in the fabric
                cov += ft_to_m(float(road_facing[i] or 0.0))
                P.at[i, "adjacent_boundary_covered_pct"] = round(100.0 * min(1.0, cov / ring.length), 1)
        except Exception as ex:  # noqa: BLE001 - a degenerate ring must not stop the stage
            log.debug("boundary coverage failed for %s: %s", P["account_id"].values[i], ex)
        P.at[i, "adjacent_acres"] = round(acres, 2)
        P.at[i, "adjacent_zoning_codes"] = ";".join(sorted(set(codes)))
        if zoning_layers:
            P.at[i, "adjacent_residential_zoning_acres"] = round(res_acres, 2)
            P.at[i, "adjacent_residential_zoning_pct"] = round(100 * res_acres / acres, 1) if acres else 0.0
        if layers.planned_sewer.covers(cty):
            P.at[i, "subject_planned_sewer"] = bool(covered(pg, layers.planned_sewer) > 0.01 * pg.area)
            P.at[i, "adjacent_planned_sewer"] = bool(planned)
        if layers.existing_sewer.covers(cty):
            P.at[i, "adjacent_existing_sewer"] = bool(existing)
        if layers.pfa.covers(cty):
            sub_pfa = covered(pg, layers.pfa)
            P.at[i, "subject_pfa_acres"] = round(m2_to_acres(sub_pfa), 2)
            P.at[i, "subject_in_pfa"] = bool(sub_pfa > 0.5 * pg.area)
            P.at[i, "adjacent_pfa_acres"] = round(pfa_acres, 2)
        if layers.growth_area.covers(cty):
            P.at[i, "subject_in_growth_area"] = bool(covered(pg, layers.growth_area) > 0.5 * pg.area)
            P.at[i, "adjacent_in_growth_area"] = bool(growth)
        if layers.favorable is not None:
            P.at[i, "adjacent_permanently_eased_acres"] = round(eased, 2)
            P.at[i, "adjacent_permanently_eased_pct"] = round(100 * eased / acres, 1) if acres else 0.0
        if pipe is not None and (layers.pipeline_counties is None or cty in layers.pipeline_counties):
            hits = pipe.sindex.query(pg.buffer(radius), predicate="intersects")
            units = float(pipe["units"].values[hits].sum()) if len(hits) else 0.0
            P.at[i, "approved_unbuilt_units_within_2mi"] = round(units, 0)
            P.at[i, "approved_unbuilt_units_radius_ft"] = e.pipeline_radius_ft
        flags = P.at[i, "encroachment_flags"]
        if res_acres > 0:
            flags.append("adjacent_residential_zoning")
        if any_unmapped:
            flags.append("adjacent_zoning_unmapped_residential_share_may_be_understated")
        if pipe is None or (layers.pipeline_counties is not None and cty not in layers.pipeline_counties):
            flags.append("approved_unbuilt_units_not_published_for_this_county")
        # Under half the boundary accounted for by a parcel or a road: the fabric
        # probably stops here (a county outside `counties:`), so the adjacency
        # columns describe only what we can see. ~1% of parcels on real data.
        if float(P.at[i, "adjacent_boundary_covered_pct"] or 0) < 50.0:
            flags.append("adjoining_parcels_incomplete_check_neighbouring_jurisdiction")
        if planned:
            flags.append("adjacent_planned_sewer_service")
        if P.at[i, "subject_planned_sewer"]:
            flags.append("subject_in_planned_sewer_service")
        if P.at[i, "subject_in_pfa"]:
            flags.append("inside_priority_funding_area")
        if P.at[i, "subject_in_growth_area"] or growth:
            flags.append("designated_growth_area_adjoining")
    return Stage7Result(parcels=P, missing_layers=missing_layers or [])
