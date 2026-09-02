"""Reading layers into the working CRS, clipped to the study area."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from ..config import Config, DeriveFromLines, LayerSource
from ..units import ft_to_m
from .arcgis import ArcGISLayer

log = logging.getLogger(__name__)

_VECTOR_EXT = {".gpkg", ".shp", ".geojson", ".json", ".gdb", ".fgb", ".parquet"}


class LayerNotAvailable(FileNotFoundError):
    pass


# ----------------------------------------------------------------------------
def _iter_files(path: Path) -> list[Path]:
    if path.is_file() or path.suffix.lower() == ".gdb":
        return [path]
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in _VECTOR_EXT)
        if not files:
            raise LayerNotAvailable(f"no vector files found in {path}")
        return files
    raise LayerNotAvailable(f"layer path does not exist: {path}")


def _bbox_in_layer_crs(file: Path, layer: Optional[str], clip_geom: Optional[BaseGeometry], clip_crs: str):
    if clip_geom is None:
        return None
    try:
        info = pyogrio.read_info(str(file), layer=layer)
        lcrs = info.get("crs")
    except Exception:  # pragma: no cover - unusual driver
        return None
    if not lcrs:
        return None
    b = gpd.GeoSeries([clip_geom], crs=clip_crs).to_crs(lcrs).total_bounds
    return tuple(float(x) for x in b)


def clean_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """make_valid, drop empties/nulls, keep only areal or lineal parts as appropriate."""
    if gdf.empty:
        return gdf
    geoms = gdf.geometry
    bad = ~geoms.is_valid
    if bad.any():
        gdf = gdf.copy()
        gdf.loc[bad, gdf.geometry.name] = geoms[bad].apply(make_valid)
    gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
    return gdf


def read_layer(source: LayerSource, working_crs: str, clip_geom: Optional[BaseGeometry] = None,
               clip_mode: str = "intersects", columns: Optional[list[str]] = None) -> gpd.GeoDataFrame:
    """Read a local layer, reproject, optionally restrict to a clip geometry.

    clip_mode:
      intersects — keep features whose geometry intersects clip_geom (no cutting)
      clip       — geometric clip (for constraint/ROW layers, never for parcels)
    """
    if source.path is None:
        raise LayerNotAvailable(f"{source.name}: no path configured (url={source.url}); run `farmsearch fetch`")
    parts = []
    for f in _iter_files(source.path):
        bbox = _bbox_in_layer_crs(f, source.layer, clip_geom, working_crs)
        kw = {"bbox": bbox} if bbox else {}
        if source.layer:
            kw["layer"] = source.layer
        if columns:
            kw["columns"] = columns
        g = gpd.read_file(str(f), engine="pyogrio", **kw)
        if g.crs is None:
            raise ValueError(f"{f}: layer has no CRS; cannot reproject")
        g["_source_file"] = f.name
        parts.append(g)
    gdf = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    gdf = gpd.GeoDataFrame(gdf, geometry=parts[0].geometry.name, crs=parts[0].crs)
    if source.where:
        gdf = gdf.query(source.where)
    gdf = gdf.to_crs(working_crs)
    gdf = clean_geometries(gdf)
    if clip_geom is not None and not gdf.empty:
        if clip_mode == "clip":
            gdf = gpd.clip(gdf, clip_geom, keep_geom_type=True)
        else:
            idx = gdf.sindex.query(clip_geom, predicate="intersects")
            gdf = gdf.iloc[sorted(idx)]
        gdf = clean_geometries(gdf)
    return gdf.reset_index(drop=True)


def read_layer_fields(source: LayerSource) -> list[str]:
    """Field names of a local layer without loading rows."""
    if source.path is None:
        raise LayerNotAvailable(f"{source.name}: no path configured")
    f = _iter_files(source.path)[0]
    info = pyogrio.read_info(str(f), layer=source.layer)
    return list(info["fields"])


def load_study_area(cfg: Config) -> BaseGeometry:
    if not cfg.study_area_path.exists():
        raise LayerNotAvailable(f"study area polygon not found: {cfg.study_area_path}")
    sa = gpd.read_file(str(cfg.study_area_path))
    if sa.crs is None:
        sa = sa.set_crs("EPSG:4326")
    sa = sa.to_crs(cfg.working_crs)
    geom = sa.geometry.union_all()
    if geom.is_empty:
        raise ValueError("study area polygon is empty")
    return make_valid(geom)


def derive_buffer_layer(dfl: DeriveFromLines, working_crs: str, clip_geom: Optional[BaseGeometry],
                        type_tag: str) -> gpd.GeoDataFrame:
    """Riparian-buffer inference: fixed-width strip either side of stream lines."""
    lines = read_layer(dfl.source, working_crs, clip_geom, clip_mode="clip")
    if lines.empty:
        return gpd.GeoDataFrame({"type": []}, geometry=[], crs=working_crs)
    buf = lines.geometry.buffer(ft_to_m(dfl.buffer_ft), cap_style="flat")
    out = gpd.GeoDataFrame({"type": [type_tag] * len(buf), "buffer_ft": [dfl.buffer_ft] * len(buf)},
                           geometry=buf.values, crs=working_crs)
    return clean_geometries(out)


# ----------------------------------------------------------------------------
def fetch_layer_to_cache(source: LayerSource, bbox_4326: tuple[float, float, float, float],
                         session=None) -> Path:
    """Download an ArcGIS REST layer (clipped to bbox) into source.path as GPKG."""
    if not source.url:
        raise LayerNotAvailable(f"{source.name}: no url configured")
    if source.path is None:
        raise LayerNotAvailable(f"{source.name}: no cache path configured")
    layer = ArcGISLayer(source.url, session=session)
    info = layer.info()
    log.info("fetching %s (%s): maxRecordCount=%d pagination=%s", source.name, info.name,
             info.max_record_count, info.supports_pagination)
    fc = layer.fetch_geojson(where=source.where or "1=1", bbox_4326=bbox_4326)
    gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326")
    source.path.parent.mkdir(parents=True, exist_ok=True)
    out = source.path if source.path.suffix else source.path.with_suffix(".gpkg")
    gdf.to_file(str(out), driver="GPKG")
    log.info("wrote %d features to %s", len(gdf), out)
    return out
