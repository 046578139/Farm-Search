"""Reserve-strip detection.

A deliberate access-control strip is a separately-owned sliver of land lying
between a parcel and the road (e.g. 50 ft x 1,300 ft). Shape is the tell:
width estimated as 2*area/perimeter (robust to curved or L-shaped strips,
unlike a rotated bounding box) and an extreme length-to-width ratio.
"""
from __future__ import annotations

from shapely.geometry.base import BaseGeometry

from ..units import m_to_ft


def strip_metrics(geom: BaseGeometry) -> dict:
    area = geom.area
    perim = geom.length
    if area <= 0 or perim <= 0:
        return {"est_width_ft": 0.0, "est_length_ft": 0.0, "aspect": 0.0, "perimeter_area_ratio_ft": 0.0}
    w = 2.0 * area / perim            # meters; exact for a long thin rectangle
    length = area / w
    return {
        "est_width_ft": round(m_to_ft(w), 1),
        "est_length_ft": round(m_to_ft(length), 1),
        "aspect": round(length / w, 1),
        "perimeter_area_ratio_ft": round(m_to_ft(perim) / (area / 4046.8564224), 1),  # ft per acre
    }


def is_strip(metrics: dict, max_width_ft: float, min_aspect: float) -> bool:
    return 0 < metrics["est_width_ft"] <= max_width_ft and metrics["aspect"] >= min_aspect
