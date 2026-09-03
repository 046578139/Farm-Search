"""Reading layers into the working CRS, clipped to the study area."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from ..config import Config, DeriveFromLines, LayerSource
from ..units import ft_to_m
from .arcgis import ArcGISLayer, fetch_layer_gdf

log = logging.getLogger(__name__)

_VECTOR_EXT = {".gpkg", ".shp", ".geojson", ".json", ".gdb", ".fgb", ".parquet"}
DEFAULT_PAGE_CAP = 5000


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


_AREAL = {"Polygon", "MultiPolygon"}
_LINEAL = {"LineString", "MultiLineString"}


def _parts_of_kind(geom, kind: str):
    """Polygonal / lineal parts of any geometry (GeometryCollections from
    ambiguous ESRI JSON rings, make_valid output), unioned; None if none."""
    from shapely import get_parts
    from shapely.geometry import MultiLineString, MultiPolygon
    want = _AREAL if kind == "areal" else _LINEAL
    parts = [g for g in get_parts(geom) if g.geom_type in want and not g.is_empty]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return unary_union(parts) if kind == "areal" else MultiLineString(
        [ln for pt in parts for ln in (pt.geoms if pt.geom_type == "MultiLineString" else [pt])])


def coerce_geometry_kind(gdf: gpd.GeoDataFrame, kind: Optional[str] = None) -> gpd.GeoDataFrame:
    """Reduce every geometry to the layer's kind ("areal" or "lineal"; by
    default the majority kind present) by keeping only parts of that kind.
    GeometryCollections are what GDAL returns for ESRI JSON rings it cannot
    organize; downstream clipping and overlay need homogeneous types."""
    if gdf.empty:
        return gdf
    types = gdf.geometry.geom_type
    if kind == "any":
        return gdf                      # points, lines and polygons alike (route lines, substation points)
    if kind is None:
        n_areal = int(types.isin(_AREAL).sum())
        n_lineal = int(types.isin(_LINEAL).sum())
        if n_areal == 0 and n_lineal == 0:
            return gdf
        kind = "areal" if n_areal >= n_lineal else "lineal"
    want = _AREAL if kind == "areal" else _LINEAL
    off = ~types.isin(want)
    if not off.any():
        return gdf
    gdf = gdf.copy()
    fixed = gdf.geometry[off].apply(lambda g: _parts_of_kind(g, kind))
    gdf.loc[off, gdf.geometry.name] = fixed
    dropped = int(fixed.isna().sum())
    if dropped:
        log.warning("dropped %d features with no %s parts", dropped, kind)
    return gdf[~gdf.geometry.isna()]


def clean_geometries(gdf: gpd.GeoDataFrame, kind: Optional[str] = None) -> gpd.GeoDataFrame:
    """make_valid, drop empties/nulls, keep only areal or lineal parts as appropriate."""
    if gdf.empty:
        return gdf
    geoms = gdf.geometry
    bad = ~geoms.is_valid
    if bad.any():
        gdf = gdf.copy()
        gdf.loc[bad, gdf.geometry.name] = geoms[bad].apply(make_valid)
    gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
    gdf = coerce_geometry_kind(gdf, kind)
    return gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]


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
    if source.where and not gdf.empty:
        try:
            gdf = gdf.query(source.where)
        except Exception as e:  # pandas UndefinedVariableError, syntax errors
            raise LayerNotAvailable(f"{source.name}: `where` {source.where!r} cannot be evaluated on columns "
                                    f"{[c for c in gdf.columns if c != gdf.geometry.name]}: {e}") from e
    gdf = gdf.to_crs(working_crs)
    gdf = clean_geometries(gdf)
    if source.dedupe_geometry and not gdf.empty:
        wkb = gdf.geometry.to_wkb()
        dup = wkb.duplicated()
        if dup.any():
            log.info("%s: dropped %d rows with duplicate geometry", source.name, int(dup.sum()))
            gdf = gdf[~dup]
    if clip_geom is not None and not gdf.empty:
        if clip_mode == "clip":
            gdf = gpd.clip(gdf, clip_geom, keep_geom_type=True)
        else:
            idx = gdf.sindex.query(clip_geom, predicate="intersects")
            gdf = gdf.iloc[sorted(idx)]
        gdf = clean_geometries(gdf)
    return gdf.reset_index(drop=True)


def erase_layer(gdf: gpd.GeoDataFrame, erase, working_crs: str, clip_geom: Optional[BaseGeometry],
                what: str = "") -> gpd.GeoDataFrame:
    """Subtract the (buffered) footprint of `erase.source` from every geometry
    of gdf. A missing erase layer is a warning, never a failure: the layer is
    used un-erased and the caller records it as missing."""
    if erase is None or gdf.empty:
        return gdf
    try:
        e = read_layer(erase.source, working_crs, clip_geom, clip_mode="clip")
    except LayerNotAvailable as ex:
        log.warning("erase layer %s for %s unavailable (%s); using the layer un-erased", erase.source.name, what, ex)
        return gdf
    if e.empty:
        return gdf
    footprint = unary_union(list(e.geometry.values))
    if erase.buffer_ft:
        footprint = footprint.buffer(ft_to_m(erase.buffer_ft))
    before = float(gdf.geometry.area.sum())
    out = gdf.copy()
    hit = out.sindex.query(footprint, predicate="intersects")
    if len(hit):
        out.iloc[hit, out.columns.get_loc(out.geometry.name)] = out.geometry.iloc[hit].difference(footprint).values
    out = clean_geometries(out, kind="areal" if out.geometry.geom_type.isin(_AREAL).any() else None)
    log.info("%s: erased %s (%d features touched; area %.0f -> %.0f m2)", what, erase.source.name, len(hit),
             before, float(out.geometry.area.sum()))
    return out.reset_index(drop=True)


def read_layer_fields(source: LayerSource) -> list[str]:
    """Field names of a local layer without loading rows."""
    if source.path is None:
        raise LayerNotAvailable(f"{source.name}: no path configured")
    f = _iter_files(source.path)[0]
    info = pyogrio.read_info(str(f), layer=source.layer)
    return list(info["fields"])


def context_geometry(cfg: Config, study_geom: BaseGeometry, margin_ft: Optional[float] = None) -> BaseGeometry:
    """The study polygon grown by the context buffer (working CRS metres)."""
    from ..units import ft_to_m
    m = cfg.context_buffer_ft if margin_ft is None else margin_ft
    return study_geom.buffer(ft_to_m(m)) if m and m > 0 else study_geom


def study_bbox_4326(cfg: Config, margin_ft: float = 0.0) -> tuple[float, float, float, float]:
    """Lon/lat bounds of the study polygon grown by margin_ft, for REST fetches."""
    sa = gpd.read_file(str(cfg.study_area_path))
    if sa.crs is None:
        sa = sa.set_crs("EPSG:4326")
    if margin_ft and margin_ft > 0:
        from ..units import ft_to_m
        sa = sa.to_crs(cfg.working_crs)
        sa = gpd.GeoDataFrame(geometry=sa.geometry.buffer(ft_to_m(margin_ft)), crs=cfg.working_crs)
    return tuple(float(x) for x in sa.to_crs("EPSG:4326").total_bounds)


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
    # `where` is pandas syntax applied on read; only `rest_where` (SQL-92) goes
    # to the service. Cap pages so heavy polygon layers do not produce
    # 100 MB responses.
    page = source.page_size or min(info.max_record_count, DEFAULT_PAGE_CAP)
    log.info("fetching %s (%s): maxRecordCount=%d pagination=%s page=%d rest_where=%s", source.name, info.name,
             info.max_record_count, info.supports_pagination, page, source.rest_where)
    gdf = fetch_layer_gdf(layer, where=source.rest_where or "1=1", bbox_4326=bbox_4326, page_size=page)
    if gdf.empty:
        log.warning("%s: no features in the study-area bbox", source.name)
    else:
        kind = {"esriGeometryPolygon": "areal", "esriGeometryPolyline": "lineal"}.get(info.geometry_type or "")
        gdf = coerce_geometry_kind(gdf, kind)
    source.path.parent.mkdir(parents=True, exist_ok=True)
    out = source.path if source.path.suffix else source.path.with_suffix(".gpkg")
    tmp = out.with_name(out.stem + ".partial" + out.suffix)
    gdf.to_file(str(tmp), driver="GPKG")
    tmp.replace(out)                         # a failed fetch never leaves a bad cache behind
    missing = getattr(layer, "missing_ids", []) or []
    if missing:
        log.warning("%s: cached without %d unservable features (ids %s...)", source.name, len(missing), missing[:5])
    if source.where:
        cols = {c for c in gdf.columns if c != gdf.geometry.name}
        quoted = set(re.findall(r"`([^`]+)`", source.where))
        bare = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", re.sub(r"`[^`]+`", " ", source.where)))
        referenced = (quoted | bare) - {"in", "not", "and", "or", "isnull", "notnull", "isna", "notna", "True", "False", "None"}
        unknown = sorted(r for r in referenced if r not in cols and (r in quoted or r[0].isupper()))
        if unknown:
            log.warning("%s: `where` references columns not in the layer: %s (layer columns: %s)",
                        source.name, unknown, sorted(cols)[:30])
    log.info("wrote %d features to %s", len(gdf), out)
    return out
