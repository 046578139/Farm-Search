"""Build the study-area polygon from real county boundaries.

The spec's initial study area is "all of Frederick, only the Mount Airy corner
of Carroll, and the Sharpsburg / Keedysville / Boonsboro / Rohrersville area of
Washington". Rather than hand-drawing that, the config lists one part per
county, optionally clipped to a lon/lat box, and this module pulls the county
polygons from the iMAP county-boundary layer and assembles the union.

Parcels are still never cut by this polygon; it only selects them.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import box, mapping
from shapely.validation import make_valid

from ..config import Config, ConfigError
from .arcgis import ArcGISLayer, fetch_layer_gdf

log = logging.getLogger(__name__)


def fetch_county_polygons(url: str, county_field: str, names: list[str], session=None) -> gpd.GeoDataFrame:
    layer = ArcGISLayer(url, session=session)
    quoted = ",".join("'" + n.replace("'", "''") + "'" for n in names)
    gdf = fetch_layer_gdf(layer, where=f"{county_field} IN ({quoted})", out_fields=county_field, out_sr=4326)
    if gdf.empty:
        raise ConfigError(f"no county polygons returned from {url} for {names}")
    found = set(gdf[county_field].astype(str))
    missing = [n for n in names if n not in found]
    if missing:
        raise ConfigError(f"county boundary layer has no rows for {missing}; values present: {sorted(found)}")
    return gdf


def build_study_area(cfg: Config, variant: str, out: Optional[Path] = None, session=None) -> Path:
    sab = cfg.study_area_build
    if sab is None:
        raise ConfigError("study_area_build is not configured")
    if variant not in sab.variants:
        raise ConfigError(f"unknown study-area variant {variant!r}; configured: {list(sab.variants)}")
    parts = sab.variants[variant]
    counties = fetch_county_polygons(sab.boundaries_url, sab.county_field, [p.county for p in parts], session=session)
    pieces = []
    for p in parts:
        g = counties.loc[counties[sab.county_field].astype(str) == p.county].geometry.union_all()
        g = make_valid(g)
        if p.clip_bbox:
            g = g.intersection(box(*p.clip_bbox))
        if g.is_empty:
            raise ConfigError(f"study-area part {p.county} is empty after clipping to {p.clip_bbox}")
        pieces.append(g)
    geom = make_valid(gpd.GeoSeries(pieces, crs="EPSG:4326").union_all())
    desc = "; ".join(f"{p.county}" + (f" clipped to {list(p.clip_bbox)}" if p.clip_bbox else " (whole county)") for p in parts)
    fc = {"type": "FeatureCollection", "name": f"study_area_{variant}",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": [{"type": "Feature",
                        "properties": {"name": variant, "built_from": sab.boundaries_url, "parts": desc},
                        "geometry": mapping(geom)}]}
    out = Path(out) if out else cfg.study_area_path
    out.write_text(json.dumps(fc))
    area_km2 = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(cfg.working_crs).area.iloc[0] / 1e6
    log.info("wrote %s (%s; %.0f km2)", out, desc, area_km2)
    return out
