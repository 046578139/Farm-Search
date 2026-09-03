"""Stage 6 — Viewshed (noise and safety).

From candidate firing points inside the dischargeable envelope, which
neighbouring dwellings have a direct line of sight? A house 500 yards away
over a ridge is quieter than one 900 yards away across open ground.
Terrain shielding is computed on the bare-earth LiDAR DEM (no trees, no
buildings: the pessimistic answer). Also reported: steep ground inside the
envelope whose uphill side faces away from the nearest dwellings, i.e. a
candidate natural backstop.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from ..config import Config
from ..geometry.position import polygon_parts
from ..terrain import TerrainSampler, observer_points
from ..units import ft_to_m, m2_to_acres

log = logging.getLogger(__name__)
YARD_M = 0.9144


@dataclass
class Stage6Result:
    parcels: gpd.GeoDataFrame
    terrain_mode: str = "none"
    windows_failed: int = 0


def run_stage6(cfg: Config, scored: gpd.GeoDataFrame, envelopes: gpd.GeoDataFrame, structures: gpd.GeoDataFrame,
               sampler: Optional[TerrainSampler] = None) -> Stage6Result:
    v = cfg.envelope
    P = scored.reset_index(drop=True).copy()
    for k, val in {"dwellings_within_viewshed_distance": np.nan, "dwellings_with_line_of_sight": np.nan,
                   "nearest_dwelling_yards": np.nan, "candidate_backstop_slopes": None,
                   "candidate_backstop_acres": np.nan, "viewshed_flags": None}.items():
        P[k] = val
    P["viewshed_flags"] = [[] for _ in range(len(P))]
    try:
        ts = sampler or TerrainSampler(cfg, cell_m=v.dem_cell_m)
    except Exception as e:  # noqa: BLE001
        log.warning("Stage 6 skipped: %s", e)
        for i in range(len(P)):
            P.at[i, "viewshed_flags"].append("viewshed_not_evaluated_no_dem")
        return Stage6Result(parcels=P, terrain_mode="none")
    env_by = dict(zip(envelopes["account_id"].values, envelopes.geometry.values)) if len(envelopes) else {}
    dwell = structures[structures["kind"].isin(["dwelling", "church", "church_point"])] if len(structures) else structures
    if len(dwell):
        _ = dwell.sindex
    maxd = v.viewshed_max_distance_yards * YARD_M
    failed = 0
    okeys = P["owner_key"].values if "owner_key" in P.columns else np.array([None] * len(P))
    for i, (acct, pg) in enumerate(zip(P["account_id"].values, P.geometry.values)):
        if i and i % 100 == 0:
            log.info("Stage 6: %d/%d parcels (windows failed %d)", i, len(P), failed)
        env = env_by.get(acct)
        flags = P.at[i, "viewshed_flags"]
        if env is None or env.is_empty:
            flags.append("viewshed_not_evaluated_no_envelope")
            continue
        parts = sorted(polygon_parts(env), key=lambda p: -p.area)
        firing = observer_points(parts[0], v.firing_points)
        if len(parts) > 1:
            firing += observer_points(parts[1], max(1, v.firing_points // 2))
        # neighbouring dwellings within range of the envelope
        cands = []
        if len(dwell):
            for h in dwell.sindex.query(env.buffer(maxd), predicate="intersects"):
                if dwell["account_id"].values[h] == acct:
                    continue
                if v.exclude_same_owner and okeys[i] and dwell["owner_key"].values[h] == okeys[i]:
                    continue
                g = dwell.geometry.values[h]
                if g.distance(env) <= maxd:
                    cands.append(g.representative_point() if g.geom_type != "Point" else g)
        P.at[i, "dwellings_within_viewshed_distance"] = len(cands)
        if cands:
            P.at[i, "nearest_dwelling_yards"] = round(min(c.distance(env) for c in cands) / YARD_M, 0)
        try:
            b = env.bounds
            pad = maxd + 20
            win = ts.window((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("DEM window failed for %s: %s", acct, e)
            flags.append("viewshed_dem_window_failed")
            continue
        seen = 0
        for c in cands:
            vis = None
            for f in firing:
                r = ts.line_of_sight(win, f, c, v.observer_height_m, v.target_height_m)
                if r:
                    vis = True
                    break
                if r is False:
                    vis = False
            if vis:
                seen += 1
        P.at[i, "dwellings_with_line_of_sight"] = seen
        if cands and seen == 0:
            flags.append("all_nearby_dwellings_terrain_shielded")
        # backstops: steep cells in or just beyond the envelope (steep ground is never
        # usable, so the hillside sits at the envelope's edge) whose uphill side
        # faces away from the nearest dwelling
        slope, aspect = win.slope_aspect()
        search = parts[0].buffer(ft_to_m(v.backstop_search_ft)).intersection(pg.buffer(ft_to_m(v.backstop_search_ft)))
        h, w = slope.shape
        xs = win.x0 + (np.arange(w) + 0.5) * win.cell
        ys = win.y1 - (np.arange(h) + 0.5) * win.cell
        X, Y = np.meshgrid(xs, ys)
        steep = slope >= v.backstop_slope_min_pct
        if steep.any() and cands:
            from shapely import contains_xy
            inside = contains_xy(search.buffer(0), X[steep], Y[steep])
            sx, sy, sa = X[steep][inside], Y[steep][inside], aspect[steep][inside]
            if len(sx):
                # uphill direction = aspect + 180; away from the nearest dwelling when the angle between the
                # uphill vector and the vector to that dwelling exceeds 90 degrees
                cx = np.array([c.x for c in cands]); cy = np.array([c.y for c in cands])
                acres = 0.0
                cell_ac = m2_to_acres(win.cell ** 2)
                for x, y, a in zip(sx, sy, sa):
                    d2 = (cx - x) ** 2 + (cy - y) ** 2
                    j = int(np.argmin(d2))
                    up = math.radians((a + 180.0) % 360.0)
                    ux, uy = math.sin(up), math.cos(up)          # bearing -> east, north components
                    dx, dy = cx[j] - x, cy[j] - y
                    if ux * dx + uy * dy < 0:
                        acres += cell_ac
                P.at[i, "candidate_backstop_acres"] = round(acres, 2)
                P.at[i, "candidate_backstop_slopes"] = bool(acres >= v.backstop_min_acres)
            else:
                P.at[i, "candidate_backstop_acres"] = 0.0
                P.at[i, "candidate_backstop_slopes"] = False
        else:
            P.at[i, "candidate_backstop_acres"] = 0.0
            P.at[i, "candidate_backstop_slopes"] = False
        if P.at[i, "candidate_backstop_slopes"]:
            flags.append("natural_backstop_candidate")
    return Stage6Result(parcels=P, terrain_mode=ts.mode, windows_failed=failed)
