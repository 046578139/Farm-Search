"""Stage 2 — Encumbrance accounting.

For each parcel, intersecting area BY CONSTRAINT LAYER, reported separately
with acreage and position relative to the parcel. Favorable agricultural
easements and hostile forest-conservation easements are never collapsed into
one flag; they are different legal instruments with opposite implications.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import Config
from ..geometry.position import describe_position, polygon_parts, relative_position
from ..io.loaders import LayerNotAvailable, derive_buffer_layer, read_layer
from ..units import ACRE_M2, m2_to_acres

log = logging.getLogger(__name__)

MIN_INTERSECTION_M2 = 10.0


@dataclass
class Stage2Result:
    parcels: gpd.GeoDataFrame
    encumbrances: pd.DataFrame
    geoms: dict[str, dict[str, BaseGeometry]] = field(default_factory=dict)   # account_id -> {constraint: geom}
    missing_layers: list[str] = field(default_factory=list)


def load_constraint_layers(cfg: Config, study_geom: BaseGeometry) -> tuple[dict[str, gpd.GeoDataFrame], list[str]]:
    layers: dict[str, gpd.GeoDataFrame] = {}
    missing: list[str] = []
    for c in cfg.constraints:
        try:
            if c.derive_from_lines is not None:
                g = derive_buffer_layer(c.derive_from_lines, cfg.working_crs, study_geom, c.type)
            else:
                g = read_layer(c.source, cfg.working_crs, study_geom, clip_mode="clip")
        except LayerNotAvailable as e:
            log.warning("constraint layer %s unavailable: %s", c.name, e)
            missing.append(c.name)
            continue
        g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
        layers[c.name] = g
        log.info("constraint %s: %d features in study area", c.name, len(g))
    return layers, missing


def _areal(geom: BaseGeometry) -> BaseGeometry:
    parts = polygon_parts(geom)
    if not parts:
        return MultiPolygon()
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def run_stage2(cfg: Config, parcels: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame],
               missing_layers: list[str] | None = None) -> Stage2Result:
    parcels = parcels.reset_index(drop=True).copy()
    sliver_m2 = cfg.access.sliver_acres * ACRE_M2
    rows: list[dict] = []
    geoms: dict[str, dict[str, BaseGeometry]] = {a: {} for a in parcels["account_id"]}
    pgeoms = parcels.geometry.values
    accts = parcels["account_id"].values

    for spec in cfg.constraints:
        layer = layers.get(spec.name)
        if layer is None or layer.empty:
            continue
        pairs = layer.sindex.query(pgeoms, predicate="intersects")
        by_parcel: dict[int, list[int]] = {}
        for a, b in zip(pairs[0], pairs[1]):
            by_parcel.setdefault(int(a), []).append(int(b))
        # fillna first: pandas 3 keeps missing values as NaN through astype(str),
        # and a NaN among strings breaks sorted()
        names_col = (layer[spec.name_field].fillna("").astype(str).str.strip().values
                     if spec.name_field and spec.name_field in layer.columns else None)
        for a, blist in by_parcel.items():
            pg = pgeoms[a]
            feat = unary_union(list(layer.geometry.values[blist]))
            inter = _areal(pg.intersection(feat))
            if inter.is_empty or inter.area < MIN_INTERSECTION_M2:
                continue
            geoms[accts[a]][spec.name] = inter
            pos = relative_position(pg, inter, sliver_m2=sliver_m2)
            rows.append({
                "account_id": accts[a],
                "source_layer": spec.name,
                "type": spec.type,
                "category": spec.category,
                "implication": spec.implication,
                "subtract_from_usable": spec.subtract_from_usable,
                "crossable_with_permit": spec.crossable_with_permit,
                "acres": round(m2_to_acres(inter.area), 2),
                "pct_of_parcel": round(100 * inter.area / pg.area, 1),
                "position": describe_position(pos),
                **pos,
                "feature_names": ("; ".join(sorted({names_col[b] for b in blist if names_col[b]})) if names_col is not None else None),
                "feature_count": len(blist),
            })

    enc = pd.DataFrame(rows)
    if enc.empty:
        enc = pd.DataFrame(columns=["account_id", "source_layer", "type", "category", "implication", "acres", "pct_of_parcel", "position"])

    # Per-parcel roll-ups ---------------------------------------------------
    types = sorted({c.type for c in cfg.constraints})
    for t in types:
        parcels[f"enc_{t}_acres"] = 0.0
    parcels["favorable_easement_acres"] = 0.0
    parcels["hostile_easement_acres"] = 0.0
    parcels["physical_constraint_acres"] = 0.0
    parcels["bisecting_constraints"] = ""
    parcels["encumbrances_json"] = "[]"
    parcels["encumbrance_flags"] = [[] for _ in range(len(parcels))]

    spec_by_name = {c.name: c for c in cfg.constraints}
    acct_to_pos = {a: i for i, a in enumerate(accts)}
    if not enc.empty:
        for acct, g in enc.groupby("account_id"):
            i = acct_to_pos[acct]
            pg = pgeoms[i]
            for t, ga in g.groupby("type"):
                parcels.at[i, f"enc_{t}_acres"] = round(float(ga["acres"].sum()), 2)
            # Union by implication so overlapping layers of the same kind are not double-counted
            for impl, col in (("favorable", "favorable_easement_acres"), ("hostile", "hostile_easement_acres")):
                gs = [geoms[acct][n] for n in g.loc[(g["implication"] == impl) & (g["category"] == "legal"), "source_layer"]]
                if gs:
                    parcels.at[i, col] = round(m2_to_acres(unary_union(gs).area), 2)
            gs = [geoms[acct][n] for n in g.loc[g["category"] == "physical", "source_layer"]]
            if gs:
                parcels.at[i, "physical_constraint_acres"] = round(m2_to_acres(unary_union(gs).area), 2)
            bis = g.loc[g["bisects"] & g["subtract_from_usable"], "type"].tolist()
            parcels.at[i, "bisecting_constraints"] = ";".join(sorted(set(bis)))
            recs = g[["type", "source_layer", "acres", "pct_of_parcel", "position", "centroid_offset_pct",
                      "touches_boundary", "fragments_if_removed", "largest_fragment_pct"]].to_dict("records")
            parcels.at[i, "encumbrances_json"] = json.dumps(recs)
            flags = []
            for n in g["source_layer"]:
                mf = spec_by_name[n].manual_flag
                if mf and mf not in flags:
                    flags.append(mf)
            if bis:
                flags.append("hostile_constraint_bisects_parcel")
            parcels.at[i, "encumbrance_flags"] = flags

    return Stage2Result(parcels=parcels, encumbrances=enc, geoms=geoms, missing_layers=missing_layers or [])
