"""Stage 9 valuation on the synthetic fixture."""
import pytest

from farmsearch.pipeline import run_pipeline


@pytest.fixture(scope="module")
def r9(cfg, fixture_dir):
    res = run_pipeline(cfg, stages=(1, 2, 3, 4, 9), out_dir=fixture_dir / "outputs_9", write=True)
    return res["scored"].set_index("account_id"), res["summary"], res


def test_comps_are_arms_length_recent_agricultural_and_banded_by_easement(r9):
    sc, summ, res = r9
    comps = res["stage9"].comps.comps
    # C ($0, code 4) and M (2015) are excluded; I, B, E, H remain
    assert set(comps["account_id"]) == {"FRED-I", "FRED-B", "FRED-E", "FRED-H"}
    assert bool(comps.set_index("account_id").loc["FRED-I", "eased"]) is True       # MALPF covers 94% of I
    assert bool(comps.set_index("account_id").loc["FRED-B", "eased"]) is False
    bands = res["stage9"].comps.bands
    assert bands[("ALL", "eased")]["n"] == 1 and bands[("ALL", "eased")]["median"] == pytest.approx(700000 / 101.2, rel=0.01)
    # un-eased: B 1.2M/101.2, E 1.1M/98.8, H (1.0M - 120k improvements)/85.7
    assert bands[("ALL", "uneased")]["n"] == 3
    assert 10000 < bands[("ALL", "uneased")]["median"] < 12000


def test_parcels_get_segment_medians_and_the_ceiling_flag(r9):
    sc, summ, res = r9
    a = sc.loc["FRED-A"]                      # un-eased, 88.7 ac
    assert a["valuation_segment"] == "uneased" and a["est_per_acre"] == pytest.approx(res["stage9"].comps.bands[("ALL", "uneased")]["median"], abs=1)
    assert a["est_market_value"] == pytest.approx(a["est_per_acre"] * a["gross_acres"], rel=0.01)
    assert "Frederick, n=3" in a["comp_basis"]              # the county band has enough comps (min 2 in the fixture)
    assert "estimated_value_above_price_ceiling" in a["manual_verification_flags"]        # 101 ac x $11k > $1M ceiling
    m = sc.loc["FRED-M"]                      # 78 ac -> ~$870k, under the ceiling
    assert "estimated_value_above_price_ceiling" not in m["manual_verification_flags"]
    i = sc.loc["FRED-I"]                      # eased (MALPF): the eased band, thin (n=1 < 2) -> flagged thin? n=1 < min 2 -> falls back to ALL eased (same) and thin flag
    assert i["valuation_segment"] == "eased" and i["est_per_acre"] == pytest.approx(700000 / 101.2, rel=0.01)
    assert "valuation_thin_comp_set" in i["manual_verification_flags"]
    b = sc.loc["FRED-B"]                      # 101 ac un-eased -> ~1.1M+ > 1,000,000 ceiling
    assert "estimated_value_above_price_ceiling" in b["manual_verification_flags"]
    assert summ["stage9"]["comps"] == 4 and summ["stage9"]["parcels_valued"] == len(sc)
