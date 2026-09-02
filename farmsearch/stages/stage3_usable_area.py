"""Stage 3 — Usable area.

    usable = parcel
             - forest_conservation_easements
             - riparian_buffers
             - wetlands
             - floodplain
             - slope > slope_max_pct

Agricultural preservation easements are NOT subtracted: they are farmable and
drivable; they restrict subdivision, not use. Which layers are subtracted is
driven entirely by `subtract_from_usable` in the config, so the rule above is
data, not code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import Config
from ..geometry.position import polygon_parts
from ..io.loaders import LayerNotAvailable, LayerSource, read_layer
from ..slope import steep_polygons_from_dem
from ..units import ACRE_M2, m2_to_acres

log = logging.getLogger(__name__)

STEEP_KEY = "steep_slope"


@dataclass
class Stage3Result:
    parcels: gpd.GeoDataFrame
    usable: gpd.GeoDataFrame
    geoms: dict[str, dict] = field(default_factory=dict)   # account_id -> {usable, traversable, hostile: {name: geom}}
    slope_source: str = "none"
    slope_windows_failed: int = 0


def _areal(geom: BaseGeometry) -> BaseGeometry:
    parts = polygon_parts(geom)
    if not parts:
        return MultiPolygon()
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


class SlopeWindowError(RuntimeError):
    """The slope source could not be read for one parcel (network / service
    failure). The parcel is reported as NOT slope-evaluated, never as flat."""


class SlopeProvider:
    """Steep-area geometry per parcel from either a precomputed polygon layer
    or a DEM, whichever the config supplies."""

    def __init__(self, cfg: Config, study_geom: Optional[BaseGeometry]):
        self.cfg = cfg
        self.mode = "none"
        self.steep_layer: Optional[gpd.GeoDataFrame] = None
        s = cfg.slope
        if s.steep_polygons_path is not None and s.steep_polygons_path.exists():
            self.steep_layer = read_layer(LayerSource("steep_slopes", path=s.steep_polygons_path), cfg.working_crs,
                                          study_geom, clip_mode="clip")
            self.mode = "polygons"
        elif s.dem_path is not None and s.dem_path.exists():
            self.mode = "dem"
        elif s.dem_url:
            from ..slope import ImageServerDEM
            self.dem_service = ImageServerDEM(s.dem_url, cache_dir=s.dem_cache_dir)
            try:
                info = self.dem_service.info()
                log.info("slope from ImageServer %s (pixel %.3g, sr %s)", info.get("name"), info.get("pixelSizeX"),
                         (info.get("spatialReference") or {}).get("latestWkid"))
                self.mode = "imageserver"
            except Exception as e:  # noqa: BLE001 - degrade, do not fail the run
                log.warning("DEM ImageServer %s unreachable (%s); slope not evaluated", s.dem_url, e)
        else:
            log.warning("no slope source available (dem_path=%s, dem_url=%s, steep_polygons_path=%s); slope not evaluated",
                        s.dem_path, s.dem_url, s.steep_polygons_path)

    def steep(self, geom: BaseGeometry) -> Optional[BaseGeometry]:
        if self.mode == "polygons":
            idx = self.steep_layer.sindex.query(geom, predicate="intersects")
            if len(idx) == 0:
                return None
            x = _areal(geom.intersection(unary_union(list(self.steep_layer.geometry.values[idx]))))
            return None if x.is_empty else x
        if self.mode == "dem":
            s = self.cfg.slope
            return steep_polygons_from_dem(s.dem_path, geom, self.cfg.working_crs, self.cfg.slope_max_pct,
                                           vertical_factor=s.dem_vertical_unit_to_m, resample_m=s.dem_resample_m)
        if self.mode == "imageserver":
            from ..slope import steep_polygons_from_imageserver
            s = self.cfg.slope
            try:
                return steep_polygons_from_imageserver(self.dem_service, geom, self.cfg.working_crs, self.cfg.slope_max_pct,
                                                       vertical_factor=s.dem_vertical_unit_to_m,
                                                       resample_m=s.dem_resample_m or 5.0,
                                                       min_valid=s.dem_min_valid_m)
            except Exception as e:  # noqa: BLE001 - one bad window must not kill the run
                log.warning("slope window failed for parcel bounds %s: %s", geom.bounds, e)
                self.failures = getattr(self, "failures", 0) + 1
                raise SlopeWindowError(str(e)) from e
        return None


def run_stage3(cfg: Config, parcels: gpd.GeoDataFrame, enc_geoms: dict[str, dict[str, BaseGeometry]],
               slope_provider: Optional[SlopeProvider] = None, study_geom: Optional[BaseGeometry] = None) -> Stage3Result:
    parcels = parcels.reset_index(drop=True).copy()
    sp = slope_provider or SlopeProvider(cfg, study_geom)
    subtract_specs = [c for c in cfg.constraints if c.subtract_from_usable]
    noncross = {c.name for c in subtract_specs if not c.crossable_with_permit}
    if not cfg.slope.crossable:
        noncross.add(STEEP_KEY)

    for c in subtract_specs:
        parcels[f"subtracted_{c.name}_acres"] = 0.0
    parcels["steep_slope_acres"] = 0.0
    parcels["slope_evaluated"] = sp.mode != "none"
    parcels["usable_acres"] = 0.0
    parcels["usable_pct"] = 0.0
    parcels["usable_components"] = 0
    parcels["ag_easement_within_usable_acres"] = 0.0

    geoms: dict[str, dict] = {}
    usable_rows = []
    favorable = [c.name for c in cfg.constraints if c.implication == "favorable"]
    for i, (acct, pg) in enumerate(zip(parcels["account_id"], parcels.geometry)):
        hostile: dict[str, BaseGeometry] = {}
        for c in subtract_specs:
            g = enc_geoms.get(acct, {}).get(c.name)
            if g is not None and not g.is_empty:
                hostile[c.name] = g
                parcels.at[i, f"subtracted_{c.name}_acres"] = round(m2_to_acres(g.area), 2)
        try:
            steep = sp.steep(pg)
        except SlopeWindowError:
            steep = None
            parcels.at[i, "slope_evaluated"] = False
            flags = parcels.at[i, "manual_flags"] if "manual_flags" in parcels.columns else None
            if isinstance(flags, list):
                flags.append("slope_window_failed_not_evaluated")
            else:
                parcels.at[i, "manual_flags"] = ["slope_window_failed_not_evaluated"]
        if steep is not None and not steep.is_empty:
            hostile[STEEP_KEY] = steep
            parcels.at[i, "steep_slope_acres"] = round(m2_to_acres(steep.area), 2)
        sub_all = unary_union(list(hostile.values())) if hostile else None
        usable = _areal(pg.difference(sub_all)) if sub_all is not None else pg
        sub_nc = [g for k, g in hostile.items() if k in noncross]
        traversable = _areal(pg.difference(unary_union(sub_nc))) if sub_nc else pg
        parcels.at[i, "usable_acres"] = round(m2_to_acres(usable.area), 2)
        parcels.at[i, "usable_pct"] = round(100 * usable.area / pg.area, 1) if pg.area else 0.0
        parcels.at[i, "usable_components"] = sum(1 for p in polygon_parts(usable) if p.area >= cfg.access.sliver_acres * ACRE_M2)
        fav = [enc_geoms.get(acct, {}).get(n) for n in favorable]
        fav = [g for g in fav if g is not None and not g.is_empty]
        if fav:
            parcels.at[i, "ag_easement_within_usable_acres"] = round(m2_to_acres(unary_union(fav).intersection(usable).area), 2)
        geoms[acct] = {"usable": usable, "traversable": traversable, "hostile": hostile}
        usable_rows.append({"account_id": acct, "usable_acres": parcels.at[i, "usable_acres"], "geometry": usable})

    usable_gdf = gpd.GeoDataFrame(usable_rows, geometry="geometry", crs=parcels.crs) if usable_rows else \
        gpd.GeoDataFrame({"account_id": [], "usable_acres": []}, geometry=[], crs=parcels.crs)
    return Stage3Result(parcels=parcels, usable=usable_gdf, geoms=geoms, slope_source=sp.mode,
                        slope_windows_failed=int(getattr(sp, "failures", 0)))
