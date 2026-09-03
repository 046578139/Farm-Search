"""Stages 5-6 and the terrain module on the synthetic fixture."""
import pytest
from shapely.geometry import Point, box

from farmsearch.pipeline import run_pipeline


@pytest.fixture(scope="module")
def r56(cfg, fixture_dir):
    out = fixture_dir / "outputs_56"
    res = run_pipeline(cfg, stages=(1, 2, 3, 4, 5, 6), out_dir=out, write=True)
    return res["scored"].set_index("account_id"), res["summary"], res


def test_terrain_line_of_sight_over_the_fixture_hill(cfg):
    from farmsearch.terrain import TerrainSampler
    from farmsearch.fixtures.synthetic import OX, OY
    ts = TerrainSampler(cfg, cell_m=5.0)
    win = ts.window((OX + 800, OY + 600, OX + 2200, OY + 1350))
    # flat ground: clear; across the 60 m Gaussian hill under J: blocked
    assert ts.line_of_sight(win, Point(OX + 900, OY + 700), Point(OX + 1200, OY + 700), 1.7, 2.0) is True
    assert ts.line_of_sight(win, Point(OX + 1000, OY + 978), Point(OX + 2100, OY + 978), 1.7, 2.0) is False
    z = win.sample([OX + 1600, OX + 900], [OY + 978, OY + 700])
    assert 155 < z[0] < 161 and 99.5 < z[1] < 100.5


def test_stage5_safety_zones_shape_the_envelope(r56):
    sc, summ, res = r56
    # H: K's house (150 yd = 137 m north of H's north edge at y=1200; house at y~1249) removes a disc from H's north side
    h = sc.loc["FRED-H"]
    assert h["occupied_structures_within_safety_zone"] >= 1
    assert h["dischargeable_envelope_acres"] < h["usable_acres"] - 3
    assert h["dischargeable_envelope_acres"] > h["usable_acres"] - 12
    # A: church CH at (20-40, 1340-1360) is > 150 yd from A (A's top is y=658) -> no effect; nothing else near A
    a = sc.loc["FRED-A"]
    assert a["occupied_structures_within_safety_zone"] == 0 and a["schools_within_school_zone"] == 0
    assert a["dischargeable_envelope_acres"] == pytest.approx(a["usable_acres"], abs=0.05)
    assert a["dischargeable_envelope_longest_dim_yards"] > 600          # ~640 m diagonal-ish of a 640x640 lot -> > 600 yd
    # I: the church (150 yd) and K's house (150 yd) both bite I's north-east corner region? church at x 0-80,y 1320-1400 is 22 m north of I's top (1298) -> yes
    i = sc.loc["FRED-I"]
    assert i["occupied_structures_within_safety_zone"] >= 1 and i["dischargeable_envelope_acres"] < i["usable_acres"]
    # L's own farmhouse is exempt for L; J (72 m west of it) sees it; M (550 m east) does not
    l = sc.loc["FRED-L"]
    assert l["own_structures_exempted"] >= 1 and l["dischargeable_envelope_acres"] == pytest.approx(l["usable_acres"], abs=0.05)
    assert sc.loc["FRED-J", "occupied_structures_within_safety_zone"] >= 1
    assert sc.loc["FRED-M", "occupied_structures_within_safety_zone"] == 0
    # school (SCH parcel + point at 4100-4160, 100-140; 300 yd = 274 m): G is not scored; F fails acreage; E (2560-3200) is > 800 m away -> 0
    e = sc.loc["FRED-E"]
    assert e["schools_within_school_zone"] == 0
    assert "target_shooting_verify_county_discharge_ordinance_and_zoning" in a["manual_verification_flags"]
    assert summ["stage5"]["footprints_available"] is True and summ["stage5"]["layers_missing"] == []
    assert (res["stage5"].envelopes["account_id"] == "FRED-H").any()


def test_stage6_hill_shields_the_far_house(r56):
    sc, summ, res = r56
    h = sc.loc["FRED-H"]
    # K's house (just north of H), the church (~620 m north-west) and L's farmhouse (~720 m east, behind
    # J's hill) are within 1,000 yd; the hill hides L's house
    assert h["dwellings_within_viewshed_distance"] == 3
    assert h["dwellings_with_line_of_sight"] == 2
    assert h["nearest_dwelling_yards"] < 200
    j = sc.loc["FRED-J"]
    assert j["candidate_backstop_slopes"] is True or j["candidate_backstop_slopes"] == True  # noqa: E712  steep ring of the hill
    assert summ["stage6"]["terrain_mode"] == "local" and summ["stage6"]["windows_failed"] == 0
