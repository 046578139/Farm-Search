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
class EncroachmentLayers:
    planned_sewer: Optional[BaseGeometry] = None
    existing_sewer: Optional[BaseGeometry] = None
    pfa: Optional[BaseGeometry] = None
    growth_area: Optional[BaseGeometry] = None
    pipeline: Optional[gpd.GeoDataFrame] = None      # columns: units, geometry
    favorable: Optional[BaseGeometry] = None          # permanent agricultural easements (Stage 2 favorable layers)


@dataclass
class Stage7Result:
    parcels: gpd.GeoDataFrame
    missing_layers: list[str] = field(default_factory=list)


def _union_of(sources: list[LayerSource], cfg: Config, clip: BaseGeometry, missing: list[str], what: str) -> Optional[BaseGeometry]:
    geoms = []
    any_loaded = False
    for src in sources:
        try:
            g = read_layer(src, cfg.working_crs, clip, clip_mode="clip")
        except LayerNotAvailable as e:
            log.warning("%s layer %s unavailable: %s", what, src.name, e)
            missing.append(src.name)
            continue
        any_loaded = True
        g = clean_geometries(g)
        if len(g):
            geoms.append(unary_union(list(g.geometry.values)))
        log.info("%s layer %s: %d features", what, src.name, len(g))
    if not any_loaded:
        return None
    return unary_union(geoms) if geoms else unary_union([])


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
        log.info("pipeline layer %s: %d features, %.0f units", ul.source.name, len(g), float(units.sum()))
    if parts:
        L.pipeline = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=cfg.working_crs)
    elif e.pipeline_layers:
        L.pipeline = None
    else:
        L.pipeline = None
    if favorable_layers:
        geoms = [unary_union(list(g.geometry.values)) for g in favorable_layers.values() if len(g)]
        L.favorable = unary_union(geoms) if geoms else unary_union([])
    return L, missing


def load_favorable_layers(cfg: Config, clip: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    """The Stage 2 layers whose implication is favorable: permanent agricultural easements."""
    out = {}
    for c in cfg.constraints:
        if c.implication != "favorable" or c.source is None:
            continue
        try:
            out[c.name] = clean_geometries(read_layer(c.source, cfg.working_crs, clip, clip_mode="clip"))
        except LayerNotAvailable as e:
            log.warning("favorable layer %s unavailable for Stage 7: %s", c.name, e)
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
            "adjacent_existing_sewer": None, "subject_in_pfa": None, "adjacent_pfa_acres": np.nan,
            "subject_in_growth_area": None, "adjacent_in_growth_area": None,
            "approved_unbuilt_units_within_2mi": np.nan, "adjacent_permanently_eased_acres": np.nan,
            "adjacent_permanently_eased_pct": np.nan, "adjacent_zoning_codes": "", "encroachment_flags": None}
    for k, v in cols.items():
        P[k] = v
    P["encroachment_flags"] = [[] for _ in range(len(P))]

    # ---- neighbours of every scored parcel ----------------------------------
    _ = A.sindex
    acct_all = A["account_id"].values
    neigh: dict[int, list[int]] = {}
    for i, (acct, pg) in enumerate(zip(P["account_id"], P.geometry)):
        cands = A.sindex.query(pg.buffer(tol), predicate="intersects")
        neigh[i] = [int(c) for c in cands if acct_all[c] != acct]
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
    def covered(geom: BaseGeometry, layer: Optional[BaseGeometry]) -> float:
        if layer is None or layer.is_empty:
            return 0.0
        return geom.intersection(layer).area
    ng = dict(zip(N["_pos"].values, N.geometry.values))
    nacct = dict(zip(N["_pos"].values, N["account_id"].values))
    pipe = layers.pipeline
    if pipe is not None and len(pipe):
        _ = pipe.sindex
    radius = ft_to_m(e.pipeline_radius_ft)
    for i, pg in enumerate(P.geometry.values):
        if i and i % 500 == 0:
            log.info("Stage 7: %d/%d parcels", i, len(P))
        idx = neigh[i]
        P.at[i, "adjacent_parcel_count"] = len(idx)
        acres = 0.0; res_acres = 0.0; pfa_acres = 0.0; eased = 0.0
        planned = existing = growth = False
        codes = []
        for c in idx:
            g = ng[c]; a_ac = m2_to_acres(g.area); acres += a_ac
            code = zcode.get(nacct[c])
            if code is not None:
                codes.append(str(code))
                res_acres += a_ac * residential_share(nacct[c])
            if layers.planned_sewer is not None and covered(g, layers.planned_sewer) > 0.01 * g.area:
                planned = True
            if layers.existing_sewer is not None and covered(g, layers.existing_sewer) > 0.01 * g.area:
                existing = True
            if layers.pfa is not None:
                pfa_acres += m2_to_acres(covered(g, layers.pfa))
            if layers.growth_area is not None and covered(g, layers.growth_area) > 0.01 * g.area:
                growth = True
            if layers.favorable is not None:
                eased += m2_to_acres(covered(g, layers.favorable))
        P.at[i, "adjacent_acres"] = round(acres, 2)
        P.at[i, "adjacent_zoning_codes"] = ";".join(sorted(set(codes)))
        if zoning_layers:
            P.at[i, "adjacent_residential_zoning_acres"] = round(res_acres, 2)
            P.at[i, "adjacent_residential_zoning_pct"] = round(100 * res_acres / acres, 1) if acres else 0.0
        if layers.planned_sewer is not None:
            P.at[i, "subject_planned_sewer"] = bool(covered(pg, layers.planned_sewer) > 0.01 * pg.area)
            P.at[i, "adjacent_planned_sewer"] = bool(planned)
        if layers.existing_sewer is not None:
            P.at[i, "adjacent_existing_sewer"] = bool(existing)
        if layers.pfa is not None:
            P.at[i, "subject_in_pfa"] = bool(covered(pg, layers.pfa) > 0.5 * pg.area)
            P.at[i, "adjacent_pfa_acres"] = round(pfa_acres, 2)
        if layers.growth_area is not None:
            P.at[i, "subject_in_growth_area"] = bool(covered(pg, layers.growth_area) > 0.5 * pg.area)
            P.at[i, "adjacent_in_growth_area"] = bool(growth)
        if layers.favorable is not None:
            P.at[i, "adjacent_permanently_eased_acres"] = round(eased, 2)
            P.at[i, "adjacent_permanently_eased_pct"] = round(100 * eased / acres, 1) if acres else 0.0
        if pipe is not None:
            hits = pipe.sindex.query(pg.buffer(radius), predicate="intersects")
            units = float(pipe["units"].values[hits].sum()) if len(hits) else 0.0
            P.at[i, "approved_unbuilt_units_within_2mi"] = round(units, 0)
        flags = P.at[i, "encroachment_flags"]
        if res_acres > 0:
            flags.append("adjacent_residential_zoning")
        if planned:
            flags.append("adjacent_planned_sewer_service")
        if P.at[i, "subject_planned_sewer"]:
            flags.append("subject_in_planned_sewer_service")
        if P.at[i, "subject_in_pfa"]:
            flags.append("inside_priority_funding_area")
        if P.at[i, "subject_in_growth_area"] or growth:
            flags.append("designated_growth_area_adjoining")
    return Stage7Result(parcels=P, missing_layers=missing_layers or [])
