"""Stages 7-8 on the synthetic fixture."""
import pytest

from farmsearch.pipeline import run_pipeline


@pytest.fixture(scope="module")
def r78(cfg, fixture_dir):
    out = fixture_dir / "outputs_78"
    res = run_pipeline(cfg, stages=(1, 2, 3, 4, 7, 8), out_dir=out, write=True)
    return res["scored"].set_index("account_id"), res["summary"]


def test_stage7_adjacent_zoning_sewer_pfa_units(r78):
    sc, summ = r78
    # A (corner lot): neighbours B and I only; nothing residential, no planned sewer, no PFA
    a = sc.loc["FRED-A"]
    assert a["adjacent_parcel_count"] >= 2 and a["adjacent_residential_zoning_acres"] == 0
    assert a["adjacent_planned_sewer"] is False or a["adjacent_planned_sewer"] == False  # noqa: E712
    assert a["subject_in_pfa"] == False and a["adjacent_pfa_acres"] == 0  # noqa: E712
    # I: neighbour A + H (+ K); MALPF on I itself, not adjacent — but H/A carry no easement; A's FCA is not favorable
    # M: adjacent to L (split 60% A / 40% R1: 40% of L's ~95 ac counts) and O (Carroll AG)
    m = sc.loc["FRED-M"]
    assert 30 < m["adjacent_residential_zoning_acres"] < 45
    assert m["adjacent_pfa_acres"] > 30                                    # L's R1 part is PFA
    # E: adjacent to F (planned sewer S-3 over x>=3150 covers F) -> adjacent planned sewer
    e = sc.loc["FRED-E"]
    assert e["adjacent_planned_sewer"] == True  # noqa: E712
    # units within 2 mi: everyone sees both projects (fixture is ~4 km wide)
    assert a["approved_unbuilt_units_within_2mi"] == 40 and m["approved_unbuilt_units_within_2mi"] == 160
    # H is adjacent to I (MALPF, favorable): adjacent permanently eased acres > 0
    h = sc.loc["FRED-H"]
    assert h["adjacent_permanently_eased_acres"] > 80
    assert "adjacent_planned_sewer_service" in e["manual_verification_flags"]
    assert summ["stage7"]["adjacent_planned_sewer"] >= 1 and summ["stage7"]["layers_missing"] == []


def test_stage8_mprp_tiers_and_exposure(r78):
    sc, summ = r78
    i = sc.loc["FRED-I"]
    assert i["mprp_tier"] == 1 and i["mprp_route_variant"] == "preferred"
    assert "mprp_tier1_intersects_studied_route_exclude" in i["manual_verification_flags"]
    # A lies within 2,000 ft of the preferred route's south end -> tier 2 (distance), not LOS-dependent
    a = sc.loc["FRED-A"]
    assert a["mprp_tier"] == 2 and a["mprp_nearest_route_ft"] <= 2000
    # O (Carroll, east): 400 m south of the alternative route -> tier 2 by distance; also 260 m from the
    # HV line and 460 m from the substation -> both existing-corridor flags
    o = sc.loc["CARR-O"]
    assert o["mprp_tier"] == 2 and o["mprp_route_variant"] == "alternative"
    assert {"near_substation", "near_existing_hv_transmission_corridor"} <= set(o["manual_verification_flags"])
    assert o["hv_line_nearest_ft"] <= 1000 and o["substation_nearest_ft"] <= 2640
    # E: 1,042 m from the alternative route (> 2,000 ft, < 1 mile) and far from HV -> tier 3, general corridor
    e = sc.loc["FRED-E"]
    assert e["mprp_tier"] == 3 and "mprp_tier3_general_corridor" in e["manual_verification_flags"]
    # the alternative route 400 m north of the north road: parcels along the north (I, H, J, L, M, O) within 2,000 ft -> tier >= 2
    assert sc.loc["FRED-J", "mprp_tier"] in (2, 3)
    # data center 2 km east of G: G within 3 mi -> flagged
    m = sc.loc["FRED-M"]
    assert m["data_center_nearest_name"] == "Quantum Fixture" and "near_data_center_development" in m["manual_verification_flags"]
    assert summ["stage8"]["mprp_tier_counts"].get("1") == 1
    assert summ["stage8"]["status_note"] == "fixture"
