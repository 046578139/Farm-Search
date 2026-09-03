"""Stage 8 — Transmission and industrial exposure.

The MPRP (Maryland Piedmont Reliability Project, PSC Case 9773) is a 500 kV
line whose studied routes cross central Carroll and southern Frederick
County to the Doubs substation at Adamstown. An agricultural easement does
not protect against a utility with condemnation authority, so this is
screened independently of encumbrance status, against EVERY studied route:

  tier 1  parcel intersects a studied route corridor            -> exclude
  tier 2  within mprp_exclusion_buffer_ft, or line of sight     -> flag
  tier 3  general corridor, or near existing HV lines/substations -> flag

plus proximity flags for named points of concern (Doubs / Adamstown) and
known data-center sites. Line of sight uses the terrain module when a DEM
is configured (Stage 6 machinery); otherwise it is reported as null.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from ..config import Config
from ..io.loaders import LayerNotAvailable, clean_geometries, read_layer
from ..units import ft_to_m, m_to_ft

log = logging.getLogger(__name__)


@dataclass
class TransmissionLayers:
    routes: list[tuple[str, str, BaseGeometry]] = field(default_factory=list)   # (source name, variant, geometry)
    hv_lines: Optional[BaseGeometry] = None
    substations: Optional[BaseGeometry] = None
    data_centers: Optional[gpd.GeoDataFrame] = None    # columns: name, geometry
    points: list[tuple[str, Point, float]] = field(default_factory=list)       # (name, point, buffer_m)


@dataclass
class Stage8Result:
    parcels: gpd.GeoDataFrame
    missing_layers: list[str] = field(default_factory=list)


def _name_column(g: gpd.GeoDataFrame) -> Optional[str]:
    for c in g.columns:
        if c.lower() in ("name", "site", "site_name", "project", "project_name", "facility", "label", "title"):
            return c
    for c in g.columns:
        if c != g.geometry.name and g[c].dtype == object:
            return c
    return None


def load_transmission_layers(cfg: Config, clip: BaseGeometry) -> tuple[TransmissionLayers, list[str]]:
    t = cfg.transmission
    missing: list[str] = []
    L = TransmissionLayers()
    reach = clip.buffer(ft_to_m(max(t.mprp_general_corridor_ft, t.hv_line_buffer_ft, t.substation_buffer_ft, t.data_center_buffer_ft)))
    for r in t.mprp_routes:
        try:
            g = clean_geometries(read_layer(r.source, cfg.working_crs, reach, clip_mode="intersects"), kind="any")
        except LayerNotAvailable as e:
            log.warning("MPRP route layer %s unavailable: %s", r.source.name, e)
            missing.append(r.source.name)
            continue
        if len(g):
            L.routes.append((r.source.name, r.variant, unary_union(list(g.geometry.values))))
        log.info("MPRP route layer %s (%s): %d features", r.source.name, r.variant, len(g))

    def union(sources, what):
        geoms, loaded = [], False
        for src in sources:
            try:
                g = clean_geometries(read_layer(src, cfg.working_crs, reach, clip_mode="intersects"), kind="any")
            except LayerNotAvailable as e:
                log.warning("%s layer %s unavailable: %s", what, src.name, e)
                missing.append(src.name)
                continue
            loaded = True
            if len(g):
                geoms.append(unary_union(list(g.geometry.values)))
            log.info("%s layer %s: %d features", what, src.name, len(g))
        if not loaded:
            return None
        return unary_union(geoms) if geoms else unary_union([])
    L.hv_lines = union(t.hv_line_layers, "HV line")
    L.substations = union(t.substation_layers, "substation")
    parts = []
    for src in t.data_center_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, reach, clip_mode="intersects"), kind="any")
        except LayerNotAvailable as e:
            log.warning("data center layer %s unavailable: %s", src.name, e)
            missing.append(src.name)
            continue
        nc = _name_column(g)
        parts.append(gpd.GeoDataFrame({"name": (g[nc].astype(str).values if nc else [src.name] * len(g))},
                                      geometry=g.geometry.values, crs=cfg.working_crs))
    if parts:
        L.data_centers = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=cfg.working_crs)
    for pt in t.points_of_concern:
        p = gpd.GeoSeries([Point(float(pt["lon"]), float(pt["lat"]))], crs="EPSG:4326").to_crs(cfg.working_crs).iloc[0]
        L.points.append((str(pt["name"]), p, ft_to_m(float(pt.get("buffer_ft", 15840)))))
    return L, missing


def run_stage8(cfg: Config, scored: gpd.GeoDataFrame, layers: TransmissionLayers,
               missing_layers: Optional[list[str]] = None,
               line_of_sight: Optional[Callable[[BaseGeometry, BaseGeometry], Optional[bool]]] = None) -> Stage8Result:
    """line_of_sight(parcel_geom, route_geom) -> True/False/None (terrain module, optional)."""
    t = cfg.transmission
    P = scored.reset_index(drop=True).copy()
    for k, v in {"mprp_tier": None, "mprp_nearest_route_ft": np.nan, "mprp_route_variant": None,
                 "mprp_line_of_sight": None, "hv_line_nearest_ft": np.nan, "substation_nearest_ft": np.nan,
                 "data_center_nearest_ft": np.nan, "data_center_nearest_name": None,
                 "points_of_concern_within": "", "transmission_flags": None}.items():
        P[k] = v
    P["transmission_flags"] = [[] for _ in range(len(P))]
    half = ft_to_m(t.mprp_corridor_width_ft / 2.0)
    excl = ft_to_m(t.mprp_exclusion_buffer_ft)
    general = ft_to_m(t.mprp_general_corridor_ft)
    hv_b = ft_to_m(t.hv_line_buffer_ft)
    sub_b = ft_to_m(t.substation_buffer_ft)
    dc_b = ft_to_m(t.data_center_buffer_ft)
    have_routes = bool(layers.routes)
    dcs = layers.data_centers
    if dcs is not None and len(dcs):
        _ = dcs.sindex
    for i, pg in enumerate(P.geometry.values):
        flags = P.at[i, "transmission_flags"]
        tier = 0 if have_routes else None
        if have_routes:
            best = None
            for name, variant, rg in layers.routes:
                d = pg.distance(rg)
                if best is None or d < best[0]:
                    best = (d, variant, name, rg)
            d, variant, name, rg = best
            P.at[i, "mprp_nearest_route_ft"] = round(m_to_ft(d), 0)
            P.at[i, "mprp_route_variant"] = variant
            if d <= half:
                tier = 1
                flags.append("mprp_tier1_intersects_studied_route_exclude")
            elif d <= excl:
                tier = 2
                flags.append("mprp_tier2_within_exclusion_buffer")
            else:
                los = None
                if line_of_sight is not None and d <= general:
                    try:
                        los = line_of_sight(pg, rg)
                    except Exception as ex:  # noqa: BLE001
                        log.warning("line of sight failed for %s: %s", P.at[i, "account_id"], ex)
                        los = None
                P.at[i, "mprp_line_of_sight"] = los
                if los:
                    tier = 2
                    flags.append("mprp_tier2_line_of_sight_to_studied_route")
                elif d <= general:
                    tier = 3
                    flags.append("mprp_tier3_general_corridor")
        if layers.hv_lines is not None and not layers.hv_lines.is_empty:
            d = pg.distance(layers.hv_lines)
            P.at[i, "hv_line_nearest_ft"] = round(m_to_ft(d), 0)
            if d <= hv_b:
                flags.append("near_existing_hv_transmission_corridor")
                if tier is not None and tier == 0:
                    tier = 3
        if layers.substations is not None and not layers.substations.is_empty:
            d = pg.distance(layers.substations)
            P.at[i, "substation_nearest_ft"] = round(m_to_ft(d), 0)
            if d <= sub_b:
                flags.append("near_substation")
                if tier is not None and tier == 0:
                    tier = 3
        if dcs is not None and len(dcs):
            j = int(dcs.sindex.nearest(pg, return_all=False)[1][0])
            d = pg.distance(dcs.geometry.values[j])
            P.at[i, "data_center_nearest_ft"] = round(m_to_ft(d), 0)
            P.at[i, "data_center_nearest_name"] = str(dcs["name"].values[j])
            if d <= dc_b:
                flags.append("near_data_center_development")
        within = []
        for name, pt, buf in layers.points:
            if pg.distance(pt) <= buf:
                within.append(name)
                flags.append(f"near_{name.lower().replace(' ', '_').replace('/', '_')}")
        P.at[i, "points_of_concern_within"] = ";".join(within)
        P.at[i, "mprp_tier"] = tier
    return Stage8Result(parcels=P, missing_layers=missing_layers or [])
