"""Stage 10 on the synthetic fixture (provider none: redundancy and durability only) plus injected durations."""
import numpy as np
import pytest

from farmsearch.pipeline import run_pipeline


@pytest.fixture(scope="module")
def r10(cfg, fixture_dir):
    res = run_pipeline(cfg, stages=(1, 2, 3, 4, 7, 10), out_dir=fixture_dir / "outputs_10", write=True)
    return res["scored"].set_index("account_id"), res["summary"], res


def test_redundancy_from_the_road_graph(r10):
    sc, summ, res = r10
    # A fronts MAIN RD next to the MD 999 junction: two independent ways onto the state road -> redundant
    assert sc.loc["FRED-A", "route_redundancy"] == "redundant" and sc.loc["FRED-A", "egress_paths"] >= 2
    # E is 2.5 km east on MAIN RD: the only way to a state road is west along MAIN RD -> single egress
    assert sc.loc["FRED-E", "route_redundancy"] == "single_egress"
    assert "single_egress_no_incident_tolerance" in sc.loc["FRED-E", "manual_verification_flags"]
    assert summ["stage10"]["route_redundancy"].get("single_egress", 0) >= 1


def test_corridor_durability_and_commute_columns_without_an_engine(r10):
    sc, summ, res = r10
    a = sc.loc["FRED-A"]
    assert a["corridor_road"] == "MD 999" and a["corridor_aadt"] == 10400 and a["corridor_aadt_trend_pct"] == pytest.approx(30.0, abs=0.1)
    assert a["corridor_capacity_projects"] == 1
    # 100 - 45*(units/1000) - 35*min(1, 0.30/0.30) + 20 ; units near A's access point = 40 (North Farms)
    assert a["corridor_durability_score"] == pytest.approx(100 - 45 * 0.04 - 35 + 20, abs=0.2)
    assert np.isnan(a["commute_bwi_peak_min"]) and "commute_unavailable" in a["manual_verification_flags"]
    assert summ["stage10"]["engine"] == "none"


def test_injected_durations_apply_the_peak_factor(cfg, fixture_dir):
    from farmsearch.stages.stage10_commute import load_commute_layers, run_stage10
    from farmsearch.io.loaders import load_study_area, context_geometry
    import geopandas as gpd
    res = run_pipeline(cfg, stages=(1, 2, 3, 4), out_dir=fixture_dir / "outputs_10b", write=False)
    scored = res["scored"]
    study = load_study_area(cfg)
    layers = load_commute_layers(cfg, context_geometry(cfg, study))
    fn = lambda origins, dests: np.full((len(origins), len(dests)), 60.0)
    out = run_stage10(cfg, scored, res["stage4"].entry_points, layers, durations_fn=fn).parcels
    assert (out["commute_bwi_freeflow_min"] == 60.0).all()
    assert (out["commute_bwi_peak_min"] == 60.0).all()               # injected minutes are taken as given (traffic-aware)
    assert out["commute_engine"].iloc[0] == "injected"
