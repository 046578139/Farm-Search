"""Unit constants. All geometry math happens in a projected CRS in meters."""

ACRE_M2 = 4046.8564224
FT_M = 0.3048
YD_M = 0.9144
MILE_M = 1609.344


def m2_to_acres(m2: float) -> float:
    return float(m2) / ACRE_M2


def acres_to_m2(acres: float) -> float:
    return float(acres) * ACRE_M2


def ft_to_m(ft: float) -> float:
    return float(ft) * FT_M


def m_to_ft(m: float) -> float:
    return float(m) / FT_M


def yd_to_m(yd: float) -> float:
    return float(yd) * YD_M


def m_to_yd(m: float) -> float:
    return float(m) / YD_M
