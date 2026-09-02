"""Connected components of the usable-area polygon, seeded at entry nodes.

Every commercial tool reports gross acres and unencumbered acres. The number
that matters is the largest contiguous block you can actually drive to from
the road without crossing something you are not allowed to cross.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from .position import polygon_parts


@dataclass
class ConnectivityResult:
    reachable: list[BaseGeometry] = field(default_factory=list)
    islands: list[BaseGeometry] = field(default_factory=list)
    sliver_area: float = 0.0            # m2 of fragments below the sliver threshold

    @property
    def reachable_area(self) -> float:
        return sum(g.area for g in self.reachable)

    @property
    def largest_reachable_area(self) -> float:
        return max((g.area for g in self.reachable), default=0.0)

    @property
    def island_areas(self) -> list[float]:
        return sorted((g.area for g in self.islands), reverse=True)


def connected_components(usable: BaseGeometry, sliver_m2: float) -> tuple[list[BaseGeometry], float]:
    parts = polygon_parts(usable)
    keep = [p for p in parts if p.area >= sliver_m2]
    sliver = sum(p.area for p in parts if p.area < sliver_m2)
    keep.sort(key=lambda p: p.area, reverse=True)
    return keep, sliver


def seed_components(components: list[BaseGeometry], entry_points: list[Point], tol_m: float = 1.0) -> ConnectivityResult:
    """A component is reachable if an entry point (a point just inside the
    parcel at open road frontage) lies in or within tol_m of it."""
    res = ConnectivityResult()
    for comp in components:
        hit = any(comp.distance(pt) <= tol_m for pt in entry_points)
        (res.reachable if hit else res.islands).append(comp)
    return res


def reachable_usable_via(traversable: BaseGeometry, usable: BaseGeometry, entry_points: list[Point],
                         sliver_m2: float, tol_m: float = 1.0) -> tuple[float, float]:
    """Crossings-permitted variant. `traversable` is the parcel minus only the
    constraints that can never be crossed (forest conservation easement, steep
    slope). Returns (total usable m2 inside reachable traversable components,
    largest usable m2 within a single reachable traversable component)."""
    comps, _ = connected_components(traversable, sliver_m2)
    total = 0.0
    largest = 0.0
    for comp in comps:
        if any(comp.distance(pt) <= tol_m for pt in entry_points):
            a = usable.intersection(comp).area
            total += a
            largest = max(largest, a)
    return total, largest
