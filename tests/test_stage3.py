import pytest


def test_usable_subtracts_hostile_not_favorable(scored):
    assert scored.loc["FRED-A", "usable_acres"] == pytest.approx(101.2 - 11.96 - 0.6, abs=0.2)   # FCA + stream buffer leaking in from B
    assert scored.loc["FRED-I", "usable_acres"] == pytest.approx(101.2, abs=0.2)     # MALPF not subtracted
    assert scored.loc["FRED-I", "ag_easement_within_usable_acres"] == pytest.approx(94.6, abs=0.2)
    assert scored.loc["FRED-C", "usable_acres"] == pytest.approx(101.2 - 9.5 - 0.9, abs=0.2)
    assert scored.loc["FRED-B", "usable_acres"] == pytest.approx(101.2 - 9.65, abs=0.3)
    assert scored.loc["FRED-M", "usable_acres"] == pytest.approx(101.2 - 23.0, abs=0.4)


def test_slope_from_dem(result, scored):
    assert result["stage3"].slope_source == "dem"
    j = scored.loc["FRED-J"]
    assert j.slope_evaluated
    assert 46 <= j.steep_slope_acres <= 54          # analytic ring ~50.7 ac
    assert j.usable_acres == pytest.approx(101.2 - j.steep_slope_acres, abs=0.3)
    assert j.usable_components == 2                  # ring leaves a plateau island
    assert scored.loc["FRED-A", "steep_slope_acres"] == 0


def test_usable_layer_written(fixture_dir):
    import geopandas as gpd
    u = gpd.read_file(fixture_dir / "outputs" / "usable_area.gpkg")
    assert set(u.account_id) >= {"FRED-A", "FRED-J", "CARR-O"}
