import pytest


def enc(result, acct, layer):
    e = result["stage2"].encumbrances
    r = e[(e.account_id == acct) & (e.source_layer == layer)]
    assert len(r) == 1, f"expected one {layer} row for {acct}, got {len(r)}"
    return r.iloc[0]


def test_back_corner_easement_is_reported_with_position(result):
    r = enc(result, "FRED-A", "forest_conservation")
    assert r.acres == pytest.approx(11.96, abs=0.1)
    assert r.position.startswith("NE")
    assert r.touches_boundary and not r.bisects
    assert r.implication == "hostile" and r.category == "legal"
    assert r.feature_names == "FCA-A"


def test_riparian_buffer_bisects(result, scored):
    r = enc(result, "FRED-B", "riparian_presumed")
    assert r.type == "riparian_buffer"
    assert r.bisects and r.fragments_if_removed == 2
    assert r.acres == pytest.approx(9.65, abs=0.2)
    assert "riparian_buffer" in scored.loc["FRED-B", "bisecting_constraints"]
    assert "riparian_buffer_presumed_confirm_with_seller" in scored.loc["FRED-B", "manual_verification_flags"]
    assert "hostile_constraint_bisects_parcel" in scored.loc["FRED-B", "manual_verification_flags"]


def test_favorable_and_hostile_never_collapse(result, scored):
    r = enc(result, "FRED-I", "malpf")
    assert r.implication == "favorable"
    assert r.acres == pytest.approx(94.6, abs=0.2)
    assert scored.loc["FRED-I", "favorable_easement_acres"] == pytest.approx(94.6, abs=0.2)
    assert scored.loc["FRED-I", "hostile_easement_acres"] == 0
    assert scored.loc["FRED-A", "hostile_easement_acres"] == pytest.approx(11.96, abs=0.1)
    assert scored.loc["FRED-A", "favorable_easement_acres"] == 0


def test_frontage_easement_position(result):
    r = enc(result, "FRED-C", "forest_conservation")
    assert r.position.startswith("S") and r.touches_boundary
    assert r.acres == pytest.approx(9.5, abs=0.1)


def test_physical_constraints_filtered_by_where(result, scored):
    fp = enc(result, "FRED-M", "floodplain")
    assert fp.acres == pytest.approx(15.0, abs=0.2)        # only zone AE; zone X excluded by `where`
    wl = enc(result, "FRED-M", "wetlands")
    assert wl.position == "center" and not wl.touches_boundary
    assert scored.loc["FRED-M", "physical_constraint_acres"] == pytest.approx(23.0, abs=0.3)
