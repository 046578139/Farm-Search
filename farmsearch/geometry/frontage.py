"""Road frontage classification by outward probing.

For every short piece of a parcel's road-facing boundary we ask two
questions, independently:

  OUTSIDE: walking from this edge toward the nearest public right-of-way, do
           we cross another parcel? If so, whose?
             row                 — reached the ROW directly (within open_gap_ft)
             foreign_parcel      — crossed a parcel with a different owner
             same_owner_parcel   — crossed a parcel with the same owner
             gap                 — no parcel, but the ROW is farther than
                                   open_gap_ft (unmapped land / ROW gap)
  INSIDE:  is the ground just inside this edge covered by a hostile constraint
           (forest conservation easement, wetland, floodplain, riparian buffer,
           steep slope)?

The pair collapses to a class:
  open               row + clear inside          -> a usable entry node
  encumbered         clear outside + hostile inside
  foreign_parcel     someone else's land between you and the road
  same_owner_parcel  your other deed between you and the road (entry, flagged)
  gap                indeterminate
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from .position import merge_lines
from ..owners import owners_match
from ..units import ft_to_m, m_to_ft

INSIDE_OFFSET_M = 1.5
INSIDE_OFFSET_FALLBACK_M = 0.3
OUTSIDE_OFFSET_M = 0.3
PARALLEL_COS = 0.9   # |cos| above this: probe runs along the edge -> not frontage


@dataclass
class Subsegment:
    geom: LineString
    midpoint: Point
    inside_pt: Point
    outside_pt: Point
    outside: str = "gap"                  # row | foreign_parcel | same_owner_parcel | gap
    inside_constraint: Optional[str] = None
    blocking_index: Optional[int] = None  # index into the parcels frame
    blocking_account_id: Optional[str] = None
    blocking_owner: Optional[str] = None
    authority: Optional[str] = None
    row_distance_ft: float = 0.0

    @property
    def cls(self) -> str:
        if self.outside == "foreign_parcel":
            return "foreign_parcel"
        if self.inside_constraint:
            return "encumbered"
        if self.outside == "row":
            return "open"
        if self.outside == "same_owner_parcel":
            return "same_owner_parcel"
        return "gap"

    @property
    def is_entry(self) -> bool:
        return self.cls in ("open", "same_owner_parcel")


@dataclass
class FrontageResult:
    subsegments: list[Subsegment] = field(default_factory=list)
    road_facing_m: float = 0.0
    row_contact_m: float = 0.0
    nearby_row: bool = True

    def length_by_class(self) -> dict[str, float]:
        out = {k: 0.0 for k in ("open", "encumbered", "foreign_parcel", "same_owner_parcel", "gap")}
        for s in self.subsegments:
            out[s.cls] += s.geom.length
        return out

    def entry_points(self) -> list[Point]:
        return [s.inside_pt for s in self.subsegments if s.is_entry]

    def authorities(self) -> list[str]:
        return sorted({s.authority for s in self.subsegments if s.authority and s.cls in ("open", "encumbered", "same_owner_parcel")})

    def blocking_lengths(self) -> dict[str, float]:
        """Frontage length behind each separately-owned neighbour."""
        return self._lengths_behind(("foreign_parcel",))

    def crossed_lengths(self) -> dict[str, float]:
        """Frontage length behind each neighbour, whoever owns it (the
        reserve-strip test looks at shape first and ownership second)."""
        return self._lengths_behind(("foreign_parcel", "same_owner_parcel"))

    def _lengths_behind(self, classes: tuple[str, ...]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in self.subsegments:
            if s.cls in classes and s.blocking_account_id is not None:
                out[s.blocking_account_id] = out.get(s.blocking_account_id, 0.0) + s.geom.length
        return out


# ----------------------------------------------------------------------------
def _lines(geom: BaseGeometry) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(_lines(g))
        return out
    return []


def _split_line(line: LineString, step_m: float) -> list[LineString]:
    n = max(1, int(math.ceil(line.length / step_m)))
    step = line.length / n
    out = []
    for i in range(n):
        a, b = i * step, (i + 1) * step
        seg = _substring(line, a, b)
        if seg is not None and seg.length > 1e-6:
            out.append(seg)
    return out


def _substring(line: LineString, a: float, b: float) -> Optional[LineString]:
    from shapely.ops import substring
    try:
        seg = substring(line, a, b)
    except Exception:
        return None
    if isinstance(seg, LineString):
        return seg
    return None


def _tangent(seg: LineString) -> tuple[float, float]:
    c = np.asarray(seg.coords)
    dx, dy = c[-1][0] - c[0][0], c[-1][1] - c[0][1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (1.0, 0.0)


def _offset_points(parcel: BaseGeometry, mid: Point, tangent: tuple[float, float]) -> tuple[Point, Point]:
    tx, ty = tangent
    for nx, ny in ((-ty, tx), (ty, -tx)):
        out = Point(mid.x + nx * OUTSIDE_OFFSET_M, mid.y + ny * OUTSIDE_OFFSET_M)
        if not parcel.contains(out):
            inside = Point(mid.x - nx * INSIDE_OFFSET_M, mid.y - ny * INSIDE_OFFSET_M)
            if not parcel.contains(inside):
                inside = Point(mid.x - nx * INSIDE_OFFSET_FALLBACK_M, mid.y - ny * INSIDE_OFFSET_FALLBACK_M)
            return out, inside
    # Degenerate (spike): fall back to midpoint both ways
    return mid, mid


# ----------------------------------------------------------------------------
def analyze_frontage(subject_idx: int, subject: BaseGeometry, subject_owner: Optional[str], subject_addr: Optional[str],
                     parcels: gpd.GeoDataFrame, rows: gpd.GeoDataFrame, hostile: dict[str, BaseGeometry],
                     search_ft: float, sample_ft: float, contact_tol_ft: float, open_gap_ft: float,
                     subject_deed: Optional[str] = None, row_like=None) -> FrontageResult:
    """Classify the road-facing boundary of one parcel.

    parcels: all parcels (account_id, owner_name, owner_mailing_address, geometry) with a spatial index
    rows:    public ROW polygons (authority, geometry) with a spatial index
    hostile: constraint name -> geometry of subtracted constraints within this parcel
    row_like: optional callable(index) -> True when that neighbouring polygon is
             itself public road (a road held as an assessment account, a
             tax-map ROW sliver): a probe that hits it has reached the road.
    """
    search_m, sample_m = ft_to_m(search_ft), ft_to_m(sample_ft)
    contact_m, open_gap_m = ft_to_m(contact_tol_ft), ft_to_m(open_gap_ft)
    res = FrontageResult()

    near = rows.iloc[sorted(rows.sindex.query(subject.buffer(search_m), predicate="intersects"))]
    if near.empty:
        res.nearby_row = False
        return res
    row_union = unary_union(list(near.geometry))
    boundary = subject.boundary
    contact = boundary.intersection(row_union.buffer(contact_m))
    res.row_contact_m = contact.length if not contact.is_empty else 0.0
    facing = boundary.intersection(row_union.buffer(search_m))
    if facing.is_empty:
        return res
    facing = merge_lines(facing)
    if facing.is_empty:
        return res

    hostile_items = [(k, g) for k, g in hostile.items() if g is not None and not g.is_empty]
    for line in _lines(facing):
        for seg in _split_line(line, sample_m):
            mid = seg.interpolate(0.5, normalized=True)
            tangent = _tangent(seg)
            out_pt, in_pt = _offset_points(subject, mid, tangent)
            sub = Subsegment(geom=seg, midpoint=mid, inside_pt=in_pt, outside_pt=out_pt)
            # Outside probe toward nearest ROW point
            _, row_pt = nearest_points(out_pt, row_union)
            d = out_pt.distance(row_pt)
            if d > contact_m:
                # A side edge near a road corner "faces" the road only in the
                # buffer sense: its probe runs along the edge, not across it.
                # Such pieces are not frontage and are skipped entirely.
                px, py = (row_pt.x - out_pt.x) / d, (row_pt.y - out_pt.y) / d
                if abs(px * tangent[0] + py * tangent[1]) > PARALLEL_COS:
                    continue
            res.road_facing_m += seg.length
            sub.row_distance_ft = round(m_to_ft(d), 1)
            # Inside test
            for k, g in hostile_items:
                if g.intersects(in_pt):
                    sub.inside_constraint = k
                    break
            # Authority of the nearest ROW feature
            try:
                j = int(near.sindex.nearest(row_pt, return_all=False)[1][0])
                sub.authority = str(near.iloc[j].get("authority", "unknown"))
            except Exception:
                sub.authority = None
            probe = LineString([out_pt, row_pt]) if d > 1e-6 else None
            hit_idx = None
            if probe is not None and d > contact_m:
                cands = parcels.sindex.query(probe, predicate="intersects")
                best_d = None
                for c in cands:
                    c = int(c)
                    if c == subject_idx:
                        continue
                    g = parcels.geometry.iloc[c]
                    x = g.intersection(probe)
                    if x.is_empty or x.length < 0.1:
                        continue
                    dd = out_pt.distance(x)
                    if best_d is None or dd < best_d:
                        best_d, hit_idx = dd, c
            if hit_idx is not None and row_like is not None and row_like(int(hit_idx)):
                # The "neighbour" is the road itself (an account-held road
                # right-of-way); the parcel touches the road here.
                sub.outside = "row"
                hit_idx = None
            if hit_idx is not None:
                rowc = parcels.iloc[hit_idx]
                sub.blocking_index = hit_idx
                sub.blocking_account_id = str(rowc.get("account_id"))
                sub.blocking_owner = rowc.get("owner_name")
                same = owners_match(subject_owner, rowc.get("owner_name"), subject_addr, rowc.get("owner_mailing_address"),
                                    deed_a=subject_deed, deed_b=rowc.get("deed_ref"))
                sub.outside = "same_owner_parcel" if same else "foreign_parcel"
            elif d <= open_gap_m:
                sub.outside = "row"
            else:
                sub.outside = "gap"
            res.subsegments.append(sub)
    return res
