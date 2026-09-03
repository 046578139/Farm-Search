"""Stage 10 — Commute (reported, never a filter).

Three things, all reported as separate columns:

  commute_<destination>_peak_min   drive time from the parcel's road entrance
      to each destination at the configured peak departure (Tuesday 07:00).
      A departure-time-aware engine (Google Routes, with a key in the
      environment) gives traffic-aware minutes directly; OSRM (public demo
      server or self-hosted) gives free-flow minutes which are multiplied by
      the destination's configured peak_factor — a documented approximation,
      recorded in commute_basis so nobody mistakes it for measured traffic.
  route_redundancy   single_egress | redundant | no_route: edge-disjoint
      paths from the parcel's access node to the state-road network within
      redundancy_radius_ft on the public road centerlines. The Stage 4
      connectivity question one scale up.
  corridor_durability_score   0-100: how likely the parcel's commute
      corridor is to degrade next. Approved-but-unbuilt units near the
      corridor access point, the growth trend of the nearest AADT count on a
      state road, and whether a programmed capacity project exists nearby.

Land characteristics are permanent; jobs are not: nothing here excludes a
parcel.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from ..config import Config, Destination
from ..io.loaders import LayerNotAvailable, clean_geometries, read_layer
from ..units import ft_to_m, m_to_ft

log = logging.getLogger(__name__)


@dataclass
class CommuteLayers:
    roads: Optional[gpd.GeoDataFrame] = None        # public centerlines: authority, geometry (lines)
    aadt: Optional[gpd.GeoDataFrame] = None
    ctp: Optional[gpd.GeoDataFrame] = None
    pipeline: Optional[gpd.GeoDataFrame] = None     # units, geometry (Stage 7 layers)
    missing_layers: list[str] = field(default_factory=list)


@dataclass
class Stage10Result:
    parcels: gpd.GeoDataFrame
    engine: str = "none"
    routed: int = 0
    missing_layers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
def load_commute_layers(cfg: Config, clip: BaseGeometry, pipeline: Optional[gpd.GeoDataFrame] = None) -> CommuteLayers:
    c = cfg.commute
    L = CommuteLayers(pipeline=pipeline)
    reach = clip.buffer(ft_to_m(max(c.redundancy_radius_ft, c.corridor_search_ft)))
    parts = []
    for r in cfg.access.row_layers:
        if r.geometry != "line" or not r.public:
            continue
        try:
            g = clean_geometries(read_layer(r.source, cfg.working_crs, reach, clip_mode="intersects"), kind="lineal")
        except LayerNotAvailable as e:
            log.warning("road layer %s unavailable for Stage 10: %s", r.source.name, e)
            L.missing_layers.append(r.source.name)
            continue
        major = np.full(len(g), str(r.authority).lower() in c.major_road_authorities)
        if r.major_where:
            try:
                major = major | g.eval(r.major_where, engine="python").values.astype(bool)
            except Exception as ex:  # noqa: BLE001
                log.warning("road layer %s: major_where %r failed (%s); state segments not identified", r.source.name, r.major_where, ex)
        parts.append(gpd.GeoDataFrame({"authority": [r.authority] * len(g), "major": major}, geometry=g.geometry.values, crs=cfg.working_crs))
    if parts:
        L.roads = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=cfg.working_crs)
        log.info("Stage 10 road graph source: %d centerline features, %d major (state) segments", len(L.roads), int(L.roads["major"].sum()))
        if not L.roads["major"].any():
            log.warning("Stage 10: no major-road segments identified (set major_where on the centerline layers); every parcel will read no_route")
    for src in c.aadt_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, reach, clip_mode="intersects"), kind="lineal")
        except LayerNotAvailable as e:
            log.warning("AADT layer %s unavailable: %s", src.name, e)
            L.missing_layers.append(src.name)
            continue
        L.aadt = g if L.aadt is None else gpd.GeoDataFrame(pd.concat([L.aadt, g], ignore_index=True), geometry="geometry", crs=cfg.working_crs)
    for src in c.ctp_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, reach, clip_mode="intersects"), kind="any")
        except LayerNotAvailable as e:
            log.warning("CTP layer %s unavailable: %s", src.name, e)
            L.missing_layers.append(src.name)
            continue
        if c.ctp_capacity_where:
            try:
                g = g.query(c.ctp_capacity_where, engine="python")
            except Exception as ex:  # noqa: BLE001
                log.warning("ctp_capacity_where %r failed (%s); keeping all projects", c.ctp_capacity_where, ex)
        L.ctp = g if L.ctp is None else gpd.GeoDataFrame(pd.concat([L.ctp, g], ignore_index=True), geometry="geometry", crs=cfg.working_crs)
    return L


# ---------------------------------------------------------------------------
class RoadGraph:
    """Undirected graph of public centerlines; nodes are snapped endpoints."""

    def __init__(self, roads: gpd.GeoDataFrame, major_authorities: list[str], snap_m: float = 2.0):
        import networkx as nx
        self.G = nx.Graph()
        self.snap = snap_m
        self.major_nodes: set = set()
        self._node_xy: dict = {}
        majors = roads["major"].values.astype(bool) if "major" in roads.columns else \
            np.array([str(a).lower() in major_authorities for a in roads["authority"].values])
        for auth, geom, is_major in zip(roads["authority"].values, roads.geometry.values, majors):
            lines = list(geom.geoms) if isinstance(geom, MultiLineString) else [geom]
            for ln in lines:
                if not isinstance(ln, LineString) or ln.length <= 0:
                    continue
                a = self._key(ln.coords[0]); b = self._key(ln.coords[-1])
                if a == b:
                    continue
                if self.G.has_edge(a, b):
                    if ln.length < self.G[a][b]["length"]:
                        self.G[a][b]["length"] = ln.length
                else:
                    self.G.add_edge(a, b, length=ln.length)
                if is_major:
                    self.major_nodes.add(a); self.major_nodes.add(b)
        self._nodes = np.array([self._node_xy[n] for n in self.G.nodes]) if self.G.number_of_nodes() else np.zeros((0, 2))
        self._node_list = list(self.G.nodes)
        # straight edge geometries for attaching an origin mid-segment
        self._edges = [(a, b) for a, b in self.G.edges]
        self._edge_geoms = gpd.GeoSeries([LineString([self._node_xy[a], self._node_xy[b]]) for a, b in self._edges]) \
            if self._edges else gpd.GeoSeries([], dtype="geometry")
        if len(self._edge_geoms):
            _ = self._edge_geoms.sindex

    def _key(self, xy):
        k = (round(xy[0] / self.snap), round(xy[1] / self.snap))
        self._node_xy.setdefault(k, (xy[0], xy[1]))
        return k

    def nearest_node(self, pt: Point, max_m: float):
        if len(self._nodes) == 0:
            return None
        d = np.hypot(self._nodes[:, 0] - pt.x, self._nodes[:, 1] - pt.y)
        j = int(np.argmin(d))
        return self._node_list[j] if d[j] <= max_m else None

    def attach_origin(self, pt: Point, max_m: float):
        """Attach an origin to the graph where the parcel actually meets the
        road: the nearest edge is split at the projection of `pt`. Returns
        (node, undo) with undo() restoring the graph, or (None, None) when no
        edge lies within max_m."""
        if not len(self._edge_geoms):
            return None, None
        j = int(self._edge_geoms.sindex.nearest(pt, return_all=False)[1][0])
        seg = self._edge_geoms.iloc[j]
        if seg.distance(pt) > max_m:
            return None, None
        a, b = self._edges[j]
        proj = seg.interpolate(seg.project(pt))
        for end in (a, b):
            if Point(self._node_xy[end]).distance(proj) <= self.snap:
                return end, (lambda: None)
        o = ("ORIGIN", round(proj.x, 2), round(proj.y, 2))
        self._node_xy[o] = (proj.x, proj.y)
        if o in self.G:
            return o, (lambda: None)
        if not self.G.has_edge(a, b):
            return self.nearest_node(pt, max_m), (lambda: None)
        length = self.G[a][b]["length"]
        la = Point(self._node_xy[a]).distance(proj); lb = Point(self._node_xy[b]).distance(proj)
        self.G.remove_edge(a, b)
        self.G.add_edge(a, o, length=la); self.G.add_edge(o, b, length=lb)
        if a in self.major_nodes and b in self.major_nodes:
            self.major_nodes.add(o)

        def undo():
            if self.G.has_node(o):
                self.G.remove_node(o)
            self.G.add_edge(a, b, length=length)
            self.major_nodes.discard(o)
        return o, undo

    def egress_paths(self, origins, radius_m: float) -> int:
        """Edge-disjoint paths from the parcel's road entrances (one or more
        attached origin nodes, joined by a virtual source) to any major-road
        node within radius_m. 0 = no state road reachable in the graph."""
        import networkx as nx
        origins = [o for o in (origins if isinstance(origins, (list, tuple, set)) else [origins]) if o is not None and o in self.G]
        if not origins:
            return 0
        ego = None
        for o in origins:
            g = nx.ego_graph(self.G, o, radius=radius_m, distance="length")
            ego = g if ego is None else nx.compose(ego, g)
        majors = [n for n in ego.nodes if n in self.major_nodes and n not in origins]
        if not majors:
            return 0
        # unit capacity on every road edge, unbounded source/sink edges: the max
        # flow is the number of edge-disjoint road paths from the entrances
        H = nx.DiGraph()
        for a, b in ego.edges:
            H.add_edge(a, b, capacity=1); H.add_edge(b, a, capacity=1)
        sink = ("SINK",); source = ("SOURCE",)
        big = 10 ** 6
        for m in majors:
            H.add_edge(m, sink, capacity=big)
        for o in origins:
            H.add_edge(source, o, capacity=big)
        try:
            return int(nx.maximum_flow_value(H, source, sink))
        except (nx.NetworkXError, nx.NetworkXUnbounded):
            return 0


# ---------------------------------------------------------------------------
def _next_departure(cfg_c) -> datetime:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg_c.timezone)
    now = datetime.now(tz)
    hh, mm = (int(x) for x in cfg_c.departure_time.split(":"))
    days = (cfg_c.departure_weekday - now.weekday()) % 7
    dep = (now + timedelta(days=days)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if dep <= now:
        dep += timedelta(days=7)
    return dep


def osrm_durations(base_url: str, origins_ll: list[tuple[float, float]], dests_ll: list[tuple[float, float]],
                   batch: int = 90, session=None, timeout: int = 60) -> np.ndarray:
    """Free-flow minutes, origins x destinations, via the OSRM table service."""
    import requests
    s = session or requests.Session()
    out = np.full((len(origins_ll), len(dests_ll)), np.nan)
    nd = len(dests_ll)
    step = max(1, batch - nd)
    for i0 in range(0, len(origins_ll), step):
        chunk = origins_ll[i0:i0 + step]
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in chunk + dests_ll)
        src = ";".join(str(k) for k in range(len(chunk)))
        dst = ";".join(str(len(chunk) + k) for k in range(nd))
        url = f"{base_url}/table/v1/driving/{coords}"
        r = s.get(url, params={"sources": src, "destinations": dst, "annotations": "duration"}, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        if d.get("code") != "Ok":
            raise RuntimeError(f"OSRM table error: {d.get('code')} {d.get('message')}")
        m = np.array(d["durations"], dtype=float) / 60.0
        out[i0:i0 + len(chunk), :] = m
    return out


def google_durations(api_key: str, origins_ll: list[tuple[float, float]], dests_ll: list[tuple[float, float]],
                     departure: datetime, session=None, timeout: int = 120, batch: int = 50) -> np.ndarray:
    """Traffic-aware minutes via the Routes API computeRouteMatrix (departureTime honoured)."""
    import requests
    s = session or requests.Session()
    out = np.full((len(origins_ll), len(dests_ll)), np.nan)
    url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": api_key,
               "X-Goog-FieldMask": "originIndex,destinationIndex,duration,condition"}
    dep = departure.astimezone().isoformat()
    for i0 in range(0, len(origins_ll), batch):
        chunk = origins_ll[i0:i0 + batch]
        body = {"origins": [{"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lon}}}} for lon, lat in chunk],
                "destinations": [{"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lon}}}} for lon, lat in dests_ll],
                "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE_OPTIMAL", "departureTime": dep}
        r = s.post(url, json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        for el in r.json():
            if el.get("condition") == "ROUTE_EXISTS" and "duration" in el:
                secs = float(str(el["duration"]).rstrip("s"))
                out[i0 + int(el["originIndex"]), int(el["destinationIndex"])] = secs / 60.0
    return out


# ---------------------------------------------------------------------------
def _aadt_trend(row) -> tuple[Optional[float], Optional[float]]:
    """(latest AADT, fractional change from the earliest available year)."""
    years = sorted((c for c in row.index if str(c).upper().startswith("AADT_") and str(c)[5:9].isdigit()), key=lambda c: int(str(c)[5:9]))
    vals = [(int(str(c)[5:9]), pd.to_numeric(row[c], errors="coerce")) for c in years]
    vals = [(y, v) for y, v in vals if pd.notna(v) and v > 0]
    latest = pd.to_numeric(row.get("AADT"), errors="coerce") if "AADT" in row.index else np.nan
    if pd.isna(latest) and vals:
        latest = vals[-1][1]
    if not vals or pd.isna(latest):
        return (None if pd.isna(latest) else float(latest)), None
    first = vals[0][1]
    return float(latest), float((latest - first) / first) if first else None


def durability_score(units: Optional[float], trend: Optional[float], capacity_projects: int) -> Optional[float]:
    """0-100. Starts at 100; approved-unbuilt units near the corridor take up to
    45 points (saturating at 1,000 units), AADT growth up to 35 (saturating at
    +30% over the record), and a programmed capacity project gives back 20.
    A heuristic, documented here and in the summary; tune in config comments."""
    if units is None and trend is None:
        return None
    score = 100.0
    if units is not None:
        score -= 45.0 * min(1.0, max(0.0, units) / 1000.0)
    if trend is not None:
        score -= 35.0 * min(1.0, max(0.0, trend) / 0.30)
    if capacity_projects > 0:
        score += 20.0
    return float(max(0.0, min(100.0, round(score, 1))))


def run_stage10(cfg: Config, scored: gpd.GeoDataFrame, entry_points: Optional[gpd.GeoDataFrame], layers: CommuteLayers,
                durations_fn=None) -> Stage10Result:
    """durations_fn(origins_ll, dests_ll) -> minutes matrix; injected for tests, else chosen by provider."""
    c = cfg.commute
    P = scored.reset_index(drop=True).copy()
    for d in c.destinations:
        P[d.column] = np.nan
        P[d.column.replace("_peak_min", "_freeflow_min")] = np.nan
    for k, v in {"commute_engine": None, "commute_basis": None, "route_redundancy": None, "egress_paths": np.nan,
                 "corridor_durability_score": np.nan, "corridor_aadt": np.nan, "corridor_aadt_trend_pct": np.nan,
                 "corridor_road": None, "corridor_units_nearby": np.nan, "corridor_capacity_projects": np.nan,
                 "commute_flags": None}.items():
        P[k] = v
    P["commute_flags"] = [[] for _ in range(len(P))]

    # origins: the parcel's entry points (one per parcel for routing; all of them,
    # deduplicated per road segment, for redundancy), else the point of the parcel
    # nearest a public road, else the representative point
    origin_pts: list[Point] = []
    ep_by: dict = {}
    ep_all: dict = {}
    if entry_points is not None and len(entry_points):
        for acct, g in zip(entry_points["account_id"].values, entry_points.geometry.values):
            ep_by.setdefault(acct, g)
            ep_all.setdefault(acct, []).append(g)
    roads = layers.roads
    for acct, pg in zip(P["account_id"].values, P.geometry.values):
        pt = ep_by.get(acct)
        if pt is None and roads is not None and len(roads):
            near = roads.iloc[list(roads.sindex.query(pg.buffer(500), predicate="intersects"))]
            if len(near):
                _, rp = nearest_points(pg, near.geometry.unary_union if hasattr(near.geometry, "unary_union") else near.geometry.union_all())
                pt = rp
        origin_pts.append(pt if pt is not None else pg.representative_point())

    # ---- durations -------------------------------------------------------
    engine = "none"
    if c.destinations:
        origins_ll = [(float(p.x), float(p.y)) for p in gpd.GeoSeries(origin_pts, crs=P.crs).to_crs(4326)]
        dests_ll = [(d.lon, d.lat) for d in c.destinations]
        n = len(origins_ll) if c.max_parcels is None else min(len(origins_ll), c.max_parcels)
        M = None
        try:
            if durations_fn is not None:
                M = durations_fn(origins_ll[:n], dests_ll); engine = "injected"
            elif c.provider == "google":
                key = os.environ.get(c.google_api_key_env)
                if key:
                    M = google_durations(key, origins_ll[:n], dests_ll, _next_departure(c)); engine = "google_routes_traffic_aware"
                else:
                    log.warning("commute provider google: no key in $%s; falling back to OSRM free-flow", c.google_api_key_env)
            if M is None and c.provider in ("google", "osrm"):
                M = osrm_durations(c.osrm_url, origins_ll[:n], dests_ll, batch=c.osrm_batch); engine = f"osrm_freeflow_x_peak_factor ({c.osrm_url})"
        except Exception as e:  # noqa: BLE001
            log.warning("commute routing failed: %s", e)
            M = None
            engine = f"failed: {e}"
        if M is not None:
            for j, d in enumerate(c.destinations):
                ff = M[:, j]
                peak = ff if engine.startswith(("google", "injected")) else ff * d.peak_factor
                P.loc[: n - 1, d.column.replace("_peak_min", "_freeflow_min")] = np.round(ff, 1)
                P.loc[: n - 1, d.column] = np.round(peak, 1)
            basis = ("traffic-aware, departure Tuesday 07:00 local" if engine.startswith("google")
                     else "free-flow minutes x configured peak_factor per destination (no traffic engine); a placeholder, not a measurement")
            P["commute_basis"] = basis
            for i in range(n, len(P)):
                P.at[i, "commute_flags"].append("commute_not_routed_cap")
        else:
            for i in range(len(P)):
                P.at[i, "commute_flags"].append("commute_unavailable")
        P["commute_engine"] = engine

    # ---- redundancy --------------------------------------------------------
    if roads is not None and len(roads):
        graph = RoadGraph(roads, c.major_road_authorities)
        radius = ft_to_m(c.redundancy_radius_ft)
        for i, pt in enumerate(origin_pts):
            acct = P["account_id"].values[i]
            cands = ep_all.get(acct) or [pt]
            # one origin per distinct road segment the entrances sit on (at most 6)
            nodes, undos, seen_edges = [], [], set()
            for q in cands:
                if len(nodes) >= 6:
                    break
                j = int(graph._edge_geoms.sindex.nearest(q, return_all=False)[1][0]) if len(graph._edge_geoms) else None
                if j is None or j in seen_edges:
                    continue
                seen_edges.add(j)
                node, undo = graph.attach_origin(q, max_m=150.0)
                if node is not None:
                    nodes.append(node)
                    if undo is not None:
                        undos.append(undo)
            try:
                k = graph.egress_paths(nodes, radius)
            finally:
                for u in reversed(undos):
                    u()
            P.at[i, "egress_paths"] = k
            P.at[i, "route_redundancy"] = "redundant" if k >= 2 else ("single_egress" if k == 1 else "no_route")
            if k == 1:
                P.at[i, "commute_flags"].append("single_egress_no_incident_tolerance")
            elif k == 0:
                P.at[i, "commute_flags"].append("no_state_road_reached_in_graph")
    else:
        for i in range(len(P)):
            P.at[i, "commute_flags"].append("redundancy_not_evaluated_no_roads")

    # ---- corridor durability --------------------------------------------------
    A = layers.aadt
    if A is not None and len(A):
        _ = A.sindex
    pipe = layers.pipeline
    if pipe is not None and len(pipe):
        _ = pipe.sindex
    ctp = layers.ctp
    if ctp is not None and len(ctp):
        _ = ctp.sindex
    search = ft_to_m(c.corridor_search_ft)
    uradius = ft_to_m(c.corridor_units_radius_ft)
    for i, pt in enumerate(origin_pts):
        aadt = trend = None
        access_pt = pt
        if A is not None and len(A):
            hits = A.sindex.query(pt.buffer(search), predicate="intersects")
            if len(hits):
                sub = A.iloc[list(hits)]
                j = int(np.argmin([g.distance(pt) for g in sub.geometry.values]))
                row = sub.iloc[j]
                aadt, trend = _aadt_trend(row)
                _, access_pt = nearest_points(pt, row.geometry)
                P.at[i, "corridor_road"] = str(row.get("ROADNAME") or row.get("ROAD_NAME") or row.get("ID_PREFIX", "") + str(row.get("ID_RTE_NO", "")))
                if aadt is not None:
                    P.at[i, "corridor_aadt"] = round(aadt, 0)
                if trend is not None:
                    P.at[i, "corridor_aadt_trend_pct"] = round(100 * trend, 1)
        units = None
        if pipe is not None and len(pipe):
            hits = pipe.sindex.query(access_pt.buffer(uradius), predicate="intersects")
            units = float(pipe["units"].values[hits].sum()) if len(hits) else 0.0
            P.at[i, "corridor_units_nearby"] = round(units, 0)
        cap = 0
        if ctp is not None and len(ctp):
            cap = int(len(ctp.sindex.query(access_pt.buffer(search), predicate="intersects")))
            P.at[i, "corridor_capacity_projects"] = cap
        score = durability_score(units, trend, cap)
        if score is not None:
            P.at[i, "corridor_durability_score"] = score
            if score < 50:
                P.at[i, "commute_flags"].append("commute_corridor_likely_to_degrade")
    return Stage10Result(parcels=P, engine=engine, routed=int(P[c.destinations[0].column].notna().sum()) if c.destinations else 0,
                         missing_layers=layers.missing_layers)
