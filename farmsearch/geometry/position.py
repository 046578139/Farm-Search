"""Where an encumbrance sits relative to its parcel.

A 12-acre forest easement in a back corner is irrelevant. Twelve acres
bisecting the property is fatal. Area alone cannot tell the two apart, so
each encumbrance row reports:

  sector               compass octant of the encumbrance centroid relative to
                       the parcel centroid, or "center"
  centroid_offset_pct  distance between the two centroids as a percentage of
                       the parcel's equivalent radius (0 = centered, ~100 = edge)
  touches_boundary     encumbrance reaches the parcel boundary
  boundary_contact_ft  length of parcel boundary the encumbrance covers
  fragments_if_removed number of pieces the parcel falls into when this
                       encumbrance is removed (ignoring slivers)
  bisects              fragments_if_removed >= 2
  largest_fragment_pct largest remaining piece as % of parcel
"""
from __future__ import annotations

import math

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from ..units import m2_to_acres, m_to_ft

_SECTORS = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]


def polygon_parts(geom: BaseGeometry) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(polygon_parts(g))
        return out
    return []


def compass_sector(dx: float, dy: float) -> str:
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    idx = int(((ang + 22.5) % 360) // 45)
    return _SECTORS[idx]


def relative_position(parcel: BaseGeometry, part: BaseGeometry, sliver_m2: float = 1000.0,
                      center_pct: float = 25.0, contact_tol_m: float = 0.5) -> dict:
    pc = parcel.centroid
    ec = part.centroid
    r_eq = math.sqrt(max(parcel.area, 1e-9) / math.pi)
    dx, dy = ec.x - pc.x, ec.y - pc.y
    dist = math.hypot(dx, dy)
    offset_pct = 100.0 * dist / r_eq
    sector = "center" if offset_pct < center_pct else compass_sector(dx, dy)

    boundary = parcel.boundary
    contact = boundary.intersection(part.buffer(contact_tol_m))
    contact_len = contact.length if not contact.is_empty else 0.0
    touches = contact_len > contact_tol_m * 2

    remainder = parcel.difference(part)
    frags = [p for p in polygon_parts(remainder) if p.area >= sliver_m2]
    frags.sort(key=lambda p: p.area, reverse=True)
    largest_pct = 100.0 * frags[0].area / parcel.area if frags else 0.0

    return {
        "position": sector,
        "centroid_offset_pct": round(offset_pct, 1),
        "touches_boundary": bool(touches),
        "boundary_contact_ft": round(m_to_ft(contact_len), 1),
        "fragments_if_removed": len(frags),
        "bisects": len(frags) >= 2,
        "largest_fragment_pct": round(largest_pct, 1),
        "largest_fragment_acres": round(m2_to_acres(frags[0].area), 2) if frags else 0.0,
    }


def describe_position(info: dict) -> str:
    """Human-readable summary for the dossier / CSV."""
    if info["bisects"]:
        return f"{info['position']}; BISECTS parcel into {info['fragments_if_removed']} pieces"
    if info["touches_boundary"]:
        return f"{info['position']} edge"
    return f"{info['position']} interior"


def line_parts(geom):
    """Lineal parts of any geometry (LineString / MultiLineString /
    GeometryCollection with stray points), as a list of LineStrings."""
    from shapely import get_parts
    from shapely.geometry import LineString
    if geom is None or geom.is_empty:
        return []
    return [g for g in get_parts(geom) if isinstance(g, LineString) and not g.is_empty]


def merge_lines(geom):
    """linemerge that tolerates a bare LineString, a GeometryCollection and
    an empty input (shapely.ops.linemerge raises on a single LineString).
    Returns a LineString, a MultiLineString, or an empty LineString."""
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge
    parts = line_parts(geom)
    if not parts:
        return LineString()
    if len(parts) == 1:
        return parts[0]
    merged = linemerge(MultiLineString(parts))
    return merged
