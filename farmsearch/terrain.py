"""Terrain: DEM windows, line of sight and viewsheds.

Used by Stage 6 (dwellings with line of sight to candidate firing points,
backstop slopes) and Stage 8 (line of sight to a studied transmission
route). The DEM comes from the same source as Stage 3's slope: a local
GeoTIFF (slope.dem_path) or the Maryland ImageServer read one window at a
time (slope.dem_url) and cached on disk.

A line of sight is clear when no terrain sample along the ground path
rises above the straight line between the observer's eye and the target
point; both are raised above ground by configurable heights. Trees and
buildings are not in a bare-earth DEM, so a "clear" result is the
pessimistic (noise- and safety-relevant) answer.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from rasterio.io import MemoryFile
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from .config import Config
from .slope import IMAGESERVER_NODATA
from .units import ft_to_m

log = logging.getLogger(__name__)


@dataclass
class DEMWindow:
    arr: np.ndarray          # elevation in metres, nodata -> nan
    x0: float                # left edge (metres, working CRS)
    y1: float                # top edge
    cell: float              # cell size (metres)

    def sample(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Bilinear elevation at points (nan outside the window / nodata)."""
        cols = (np.asarray(xs) - self.x0) / self.cell - 0.5
        rows = (self.y1 - np.asarray(ys)) / self.cell - 0.5
        h, w = self.arr.shape
        c0 = np.clip(np.floor(cols).astype(int), 0, w - 2)
        r0 = np.clip(np.floor(rows).astype(int), 0, h - 2)
        fc = np.clip(cols - c0, 0, 1)
        fr = np.clip(rows - r0, 0, 1)
        z00 = self.arr[r0, c0]; z01 = self.arr[r0, c0 + 1]; z10 = self.arr[r0 + 1, c0]; z11 = self.arr[r0 + 1, c0 + 1]
        z = (z00 * (1 - fc) * (1 - fr) + z01 * fc * (1 - fr) + z10 * (1 - fc) * fr + z11 * fc * fr)
        out = np.where((cols < -0.5) | (rows < -0.5) | (cols > w - 0.5) | (rows > h - 0.5), np.nan, z)
        return out

    def slope_aspect(self) -> tuple[np.ndarray, np.ndarray]:
        """Slope (percent) and aspect (degrees clockwise from north, downhill direction) per cell."""
        gy, gx = np.gradient(self.arr, self.cell)
        slope_pct = 100.0 * np.hypot(gx, gy)
        # gradient points uphill; aspect is the downhill direction: -gx, and rows increase southward so downhill north = +gy
        aspect = (np.degrees(np.arctan2(-gx, gy)) + 360.0) % 360.0
        return slope_pct, aspect


class TerrainSampler:
    """Fetches DEM windows (local file or ImageServer) at a working cell size."""

    def __init__(self, cfg: Config, cell_m: float = 10.0, max_valid_m: float = 1500.0):
        self.cfg = cfg
        self.cell = float(cell_m)
        self.min_valid = cfg.slope.dem_min_valid_m
        self.max_valid = max_valid_m
        self.vscale = float(cfg.slope.dem_vertical_unit_to_m or 1.0)
        self.epsg = int(str(cfg.working_crs).split(":")[-1])
        self._local = None
        self._image = None
        self._cache: dict[tuple, DEMWindow] = {}
        if cfg.slope.dem_path is not None and cfg.slope.dem_path.exists():
            import rasterio
            self._local = rasterio.open(cfg.slope.dem_path)
        elif cfg.slope.dem_url:
            from .slope import ImageServerDEM
            self._image = ImageServerDEM(cfg.slope.dem_url, cache_dir=cfg.slope.dem_cache_dir)
        else:
            raise RuntimeError("no DEM configured (slope.dem_path or slope.dem_url)")

    @property
    def mode(self) -> str:
        return "local" if self._local is not None else "imageserver"

    def _clean(self, arr: np.ndarray, nodata) -> np.ndarray:
        raw = arr.astype("float64")
        a = raw * self.vscale
        bad = ~np.isfinite(a)
        if nodata is not None:
            bad |= np.isclose(raw, float(nodata))
        bad |= raw <= -9000.0            # ImageServer nodata (-9999) with or without a TIFF tag
        if self.min_valid is not None:
            bad |= a < self.min_valid
        bad |= a > self.max_valid
        a[bad] = np.nan
        return a

    def window(self, bounds: tuple[float, float, float, float]) -> DEMWindow:
        """DEM covering bounds (working CRS metres), snapped to the cell grid."""
        minx, miny, maxx, maxy = bounds
        c = self.cell
        minx, miny = math.floor(minx / c) * c, math.floor(miny / c) * c
        maxx, maxy = math.ceil(maxx / c) * c, math.ceil(maxy / c) * c
        key = (int(round(minx / c)), int(round(miny / c)), int(round(maxx / c)), int(round(maxy / c)))   # the snapped window
        if key in self._cache:
            return self._cache[key]
        if self._local is not None:
            from rasterio.warp import Resampling, reproject, transform_bounds
            from rasterio.transform import from_origin
            from rasterio.windows import Window, from_bounds
            w = max(2, int(round((maxx - minx) / c))); h = max(2, int(round((maxy - miny) / c)))
            dst = np.full((h, w), np.nan, dtype="float64")
            src_nodata = self._local.nodata
            # read only the source window the request needs (plus a two-cell pad), never the whole raster
            try:
                sb = transform_bounds(self.cfg.working_crs, self._local.crs, minx, miny, maxx, maxy, densify_pts=5)
                pad = 2.0 * max(abs(self._local.res[0]), abs(self._local.res[1]))
                wr = from_bounds(sb[0] - pad, sb[1] - pad, sb[2] + pad, sb[3] + pad, transform=self._local.transform)
                wr = wr.round_offsets().round_lengths().intersection(Window(0, 0, self._local.width, self._local.height))
            except Exception:  # noqa: BLE001 - outside the raster
                wr = None
            if wr is not None and wr.width > 0 and wr.height > 0:
                reproject(source=self._local.read(1, window=wr), destination=dst,
                          src_transform=self._local.window_transform(wr), src_crs=self._local.crs,
                          src_nodata=src_nodata, dst_transform=from_origin(minx, maxy, c, c), dst_crs=self.cfg.working_crs,
                          dst_nodata=np.nan, resampling=Resampling.bilinear)
            win = DEMWindow(self._clean(dst, None), minx, maxy, c)
        else:
            payload = self._image.export((minx, miny, maxx, maxy), self.epsg, c)
            with MemoryFile(payload) as mf, mf.open() as ds:
                arr = ds.read(1)
                t = ds.transform
                nodata = ds.nodata if ds.nodata is not None else IMAGESERVER_NODATA
                win = DEMWindow(self._clean(arr, nodata), float(t.c), float(t.f), float(abs(t.a)))
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = win
        return win

    # ------------------------------------------------------------------
    def _profile(self, win: DEMWindow, p1: Point, p2: Point, step: Optional[float] = None):
        """(t, z) ground samples along p1 -> p2; None when the endpoints have no valid elevation."""
        d = p1.distance(p2)
        step = step or self.cell
        n = max(2, int(math.ceil(d / step)) + 1)
        t = np.linspace(0.0, 1.0, n)
        xs = p1.x + (p2.x - p1.x) * t
        ys = p1.y + (p2.y - p1.y) * t
        z = win.sample(xs, ys)
        if not np.isfinite(z[0]) or not np.isfinite(z[-1]):
            return None, None
        return t, z

    def line_of_sight(self, win: DEMWindow, p1: Point, p2: Point, h1: float, h2: float, step: Optional[float] = None) -> Optional[bool]:
        """True if the straight line from p1 (eye h1 above ground) to p2
        (target h2 above ground) clears the terrain profile; None when the
        profile has no valid samples."""
        if p1.distance(p2) < 1e-6:
            return True
        t, z = self._profile(win, p1, p2, step)
        if t is None:
            return None
        z0 = z[0] + h1
        z1 = z[-1] + h2
        line = z0 + (z1 - z0) * t
        inner = z[1:-1]
        ok = np.isfinite(inner)
        if not ok.any():
            return True
        # earth curvature/refraction over < 3 km is < 0.5 m: ignored
        return bool(np.all(inner[ok] <= line[1:-1][ok] + 0.01))

    def profile_has_ridge(self, win: DEMWindow, p1: Point, p2: Point, rise_m: float = 3.0) -> Optional[bool]:
        """Is there terrain at least rise_m above the straight ground line between p1 and p2?"""
        if p1.distance(p2) < 1e-6:
            return False
        t, z = self._profile(win, p1, p2)
        if t is None:
            return None
        line = z[0] + (z[-1] - z[0]) * t
        rise = z[1:-1] - line[1:-1]
        ok = np.isfinite(rise)
        if not ok.any():
            return False
        return bool(np.max(rise[ok]) >= rise_m)


# ----------------------------------------------------------------------------
def observer_points(area: BaseGeometry, n: int = 5) -> list[Point]:
    """A handful of points spread over an area: its representative point and
    up to n-1 further interior points on a coarse grid."""
    if area is None or area.is_empty:
        return []
    pts = [area.representative_point()]
    minx, miny, maxx, maxy = area.bounds
    k = max(2, int(math.ceil(math.sqrt(n * 2))))
    for i in range(k):
        for j in range(k):
            if len(pts) >= n:
                break
            p = Point(minx + (i + 0.5) * (maxx - minx) / k, miny + (j + 0.5) * (maxy - miny) / k)
            if area.contains(p) and all(p.distance(q) > 1.0 for q in pts):
                pts.append(p)
    return pts


def line_of_sight_factory(cfg: Config, observer_h_m: float = 1.7, target_h_m: float = 40.0,
                          n_observers: int = 5) -> Callable[[BaseGeometry, BaseGeometry], Optional[bool]]:
    """Stage 8 hook: does any observer point inside the parcel see the
    nearest point of the route (a 500 kV structure is ~40 m tall)?"""
    ts = TerrainSampler(cfg, cell_m=10.0)

    def los(parcel: BaseGeometry, route: BaseGeometry) -> Optional[bool]:
        pts = observer_points(parcel, n_observers)
        if not pts:
            return None
        _, tgt = nearest_points(parcel.representative_point(), route)
        minx = min(parcel.bounds[0], tgt.x) - 30; miny = min(parcel.bounds[1], tgt.y) - 30
        maxx = max(parcel.bounds[2], tgt.x) + 30; maxy = max(parcel.bounds[3], tgt.y) + 30
        win = ts.window((minx, miny, maxx, maxy))
        seen_any = None
        for p in pts:
            r = ts.line_of_sight(win, p, tgt, observer_h_m, target_h_m)
            if r:
                return True
            if r is False:
                seen_any = False
        return seen_any
    return los
