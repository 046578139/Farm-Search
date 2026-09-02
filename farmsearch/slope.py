"""Slope derivation from a LiDAR DEM, per parcel.

The statewide DEM is far too large to load whole; each parcel reads only its
own window (plus a margin so edge gradients are correct), optionally resampled
to a coarser cell size, computes slope in percent and polygonizes cells above
the threshold.
"""
from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from rasterio.windows import from_bounds
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
import geopandas as gpd

log = logging.getLogger(__name__)

IMAGESERVER_NODATA = -9999.0


def slope_percent(dem: np.ndarray, xres: float, yres: float, vertical_factor: float = 1.0) -> np.ndarray:
    """Slope in percent from a 2-D elevation array (central differences)."""
    z = dem.astype("float64") * vertical_factor
    dzdy, dzdx = np.gradient(z, abs(yres), abs(xres))
    return 100.0 * np.hypot(dzdx, dzdy)


def steep_polygons_from_dem(dem_path: Path | str, geom: BaseGeometry, geom_crs: str, slope_max_pct: float,
                            vertical_factor: float = 1.0, resample_m: Optional[float] = None,
                            margin_m: float = 30.0) -> Optional[BaseGeometry]:
    """Union of areas within `geom` with slope > slope_max_pct, in geom_crs.
    Returns None when the DEM does not cover the parcel."""
    with rasterio.open(str(dem_path)) as ds:
        if ds.crs is None or ds.crs.is_geographic:
            raise ValueError("DEM must be in a projected CRS (meters or feet); slope needs linear pixel units")
        g_in_dem = gpd.GeoSeries([geom], crs=geom_crs).to_crs(ds.crs).iloc[0]
        minx, miny, maxx, maxy = g_in_dem.bounds
        # ds units might be feet; margin given in meters
        unit_factor = 1.0
        try:
            unit_factor = 1.0 / ds.crs.linear_units_factor[1]
        except Exception:
            pass
        m = margin_m * unit_factor
        minx, miny, maxx, maxy = minx - m, miny - m, maxx + m, maxy + m
        win = from_bounds(minx, miny, maxx, maxy, ds.transform)
        win = win.round_offsets().round_lengths()
        # Clip to raster extent
        from rasterio.windows import Window
        full = Window(0, 0, ds.width, ds.height)
        try:
            win = win.intersection(full)
        except Exception:
            return None
        if win.width <= 2 or win.height <= 2:
            return None
        xres, yres = ds.res
        out_shape = None
        if resample_m:
            rs = resample_m * unit_factor
            out_h = max(3, int(round(win.height * yres / rs)))
            out_w = max(3, int(round(win.width * xres / rs)))
            out_shape = (out_h, out_w)
        data = ds.read(1, window=win, out_shape=out_shape, resampling=Resampling.average, masked=True)
        transform = ds.window_transform(win)
        if out_shape:
            sx = win.width / out_shape[1]
            sy = win.height / out_shape[0]
            transform = transform * transform.scale(sx, sy)
            xres, yres = xres * sx, yres * sy
        arr = np.ma.filled(data.astype("float64"), np.nan)
        # Pixel size in meters for slope (DEM horizontal units -> meters)
        return steep_polygons_from_array(arr, transform, xres / unit_factor, yres / unit_factor, ds.crs,
                                         geom, geom_crs, slope_max_pct, vertical_factor)


def steep_polygons_from_array(arr: np.ndarray, transform, px_m: float, py_m: float, arr_crs,
                              geom: BaseGeometry, geom_crs: str, slope_max_pct: float,
                              vertical_factor: float = 1.0) -> Optional[BaseGeometry]:
    """Shared tail of the DEM readers: elevation array (NaN = nodata) -> union
    of cells with slope > slope_max_pct, clipped to `geom`, in geom_crs."""
    if arr.size == 0 or np.all(np.isnan(arr)):
        return None
    slope = slope_percent(np.nan_to_num(arr, nan=np.nanmean(arr)), px_m, py_m, vertical_factor)
    mask = (slope > slope_max_pct) & ~np.isnan(arr)
    if not mask.any():
        return None
    polys = [shape(s) for s, v in features.shapes(mask.astype("uint8"), mask=mask, transform=transform) if v == 1]
    if not polys:
        return None
    steep = unary_union(polys)
    steep = gpd.GeoSeries([steep], crs=arr_crs).to_crs(geom_crs).iloc[0]
    steep = steep.intersection(geom)
    return None if steep.is_empty else steep


# ----------------------------------------------------------------------------
class ImageServerDEM:
    """Read DEM windows from an ArcGIS ImageServer (the Maryland statewide
    LiDAR DEM is published this way) with `exportImage`, one parcel at a time.

    Each request asks for a GeoTIFF of the parcel's bounding box (plus margin)
    in the working CRS at `resample_m` cell size, so the service does the
    resampling and only a few hundred KB cross the wire per parcel. Windows are
    cached on disk (keyed by URL, bbox and size) so re-runs are free.
    """

    def __init__(self, url: str, cache_dir: Optional[Path] = None, session=None, timeout: int = 120,
                 max_size: int = 4000):
        import requests
        self.url = url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_size = max_size
        self._info: Optional[dict] = None

    def info(self) -> dict:
        if self._info is None:
            r = self.session.get(self.url, params={"f": "json"}, timeout=self.timeout)
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(f"{self.url}: {d['error']}")
            self._info = d
            self.max_size = min(self.max_size, int(d.get("maxImageWidth", self.max_size)), int(d.get("maxImageHeight", self.max_size)))
        return self._info

    def _cache_path(self, key: str) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".tif")

    def export(self, bounds: tuple[float, float, float, float], epsg: int, cell_m: float) -> bytes:
        """GeoTIFF bytes covering `bounds` (in EPSG:`epsg`, meters) at `cell_m`."""
        minx, miny, maxx, maxy = bounds
        w = max(3, int(math.ceil((maxx - minx) / cell_m)))
        h = max(3, int(math.ceil((maxy - miny) / cell_m)))
        if max(w, h) > self.max_size:
            f = self.max_size / max(w, h)
            w, h = max(3, int(w * f)), max(3, int(h * f))
        key = f"{self.url}|{minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f}|{epsg}|{w}x{h}"
        cp = self._cache_path(key)
        if cp is not None and cp.exists():
            return cp.read_bytes()
        params = {"bbox": f"{minx},{miny},{maxx},{maxy}", "bboxSR": epsg, "imageSR": epsg, "size": f"{w},{h}",
                  "format": "tiff", "pixelType": "F32", "noData": int(IMAGESERVER_NODATA),
                  "interpolation": "RS_BilinearInterpolation", "f": "image"}
        r = self.session.get(f"{self.url}/exportImage", params=params, timeout=self.timeout)
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/tiff"):
            raise RuntimeError(f"exportImage did not return a GeoTIFF: {r.headers.get('content-type')} {r.text[:200]}")
        if cp is not None:
            cp.write_bytes(r.content)
        return r.content


def steep_polygons_from_imageserver(dem: ImageServerDEM, geom: BaseGeometry, geom_crs: str, slope_max_pct: float,
                                    vertical_factor: float = 1.0, resample_m: Optional[float] = 5.0,
                                    margin_m: float = 30.0) -> Optional[BaseGeometry]:
    """Same contract as steep_polygons_from_dem, reading the window from an
    ImageServer in the parcel's own (projected, metric) CRS."""
    import pyproj
    crs = pyproj.CRS.from_user_input(geom_crs)
    if crs.is_geographic:
        raise ValueError("working CRS must be projected (meters) for ImageServer slope")
    epsg = crs.to_epsg()
    minx, miny, maxx, maxy = geom.bounds
    bounds = (minx - margin_m, miny - margin_m, maxx + margin_m, maxy + margin_m)
    cell = float(resample_m or 5.0)
    payload = dem.export(bounds, epsg, cell)
    with MemoryFile(payload) as mf, mf.open() as ds:
        arr = ds.read(1).astype("float64")
        nodata = ds.nodata if ds.nodata is not None else IMAGESERVER_NODATA
        arr[(arr == nodata) | (arr <= -9000)] = np.nan
        if np.all(np.isnan(arr)):
            return None
        xres, yres = ds.res
        return steep_polygons_from_array(arr, ds.transform, float(xres), float(yres), ds.crs,
                                         geom, geom_crs, slope_max_pct, vertical_factor)
