"""Slope derivation from a LiDAR DEM, per parcel.

The statewide DEM is far too large to load whole; each parcel reads only its
own window (plus a margin so edge gradients are correct), optionally resampled
to a coarser cell size, computes slope in percent and polygonizes cells above
the threshold.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
import geopandas as gpd

log = logging.getLogger(__name__)


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
        if np.all(np.isnan(arr)):
            return None
        # Pixel size in meters for slope (DEM horizontal units -> meters)
        px_m = xres / unit_factor
        py_m = yres / unit_factor
        slope = slope_percent(np.nan_to_num(arr, nan=np.nanmean(arr)), px_m, py_m, vertical_factor)
        mask = (slope > slope_max_pct) & ~np.isnan(arr)
        if not mask.any():
            return None
        polys = [shape(s) for s, v in features.shapes(mask.astype("uint8"), mask=mask, transform=transform) if v == 1]
        if not polys:
            return None
        steep = unary_union(polys)
        steep = gpd.GeoSeries([steep], crs=ds.crs).to_crs(geom_crs).iloc[0]
        steep = steep.intersection(geom)
        return None if steep.is_empty else steep
