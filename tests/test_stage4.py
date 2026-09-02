import json

import pytest


def flags(scored, acct):
    return scored.loc[acct, "manual_verification_flags"]


def test_clean_corner_lot(scored):
    a = scored.loc["FRED-A"]
    assert not a.landlocked_apparent and not a.frontage_blocked_by_foreign_parcel
    assert a.frontage_open_ft == pytest.approx(2 * 640 / 0.3048, rel=0.03)
    assert a.frontage_foreign_ft == 0 and a.frontage_encumbered_ft == 0
    assert set(a.frontage_authorities.split(";")) == {"county", "state"}
    assert "entrance_permit_sha_state_road" in flags(scored, "FRED-A")
    assert a.largest_contiguous_reachable_acres == pytest.approx(a.usable_acres, abs=0.1)
    assert a.unreachable_island_count == 0


def test_bisected_parcel_reports_largest_reachable_block(scored):
    b = scored.loc["FRED-B"]
    assert b.usable_acres == pytest.approx(91.6, abs=0.3)
    assert b.largest_contiguous_reachable_acres == pytest.approx(55.6, abs=0.5)
    assert b.unreachable_island_count == 1
    assert json.loads(b.unreachable_islands_json)[0]["acres"] == pytest.approx(36.0, abs=0.5)
    # a permitted stream crossing would reconnect the north half
    assert b.largest_reachable_if_crossings_permitted_acres == pytest.approx(91.6, abs=0.3)
    assert b.islands_reconnectable_by_permit_acres == pytest.approx(36.0, abs=0.5)
    assert "islands_reconnectable_via_mde_crossing_permit" in flags(scored, "FRED-B")


def test_frontage_encumbered_by_forest_easement(scored):
    c = scored.loc["FRED-C"]
    assert not c.landlocked_apparent                       # it touches the road...
    assert c.frontage_blocked_pct == pytest.approx(100, abs=1)
    assert not c.frontage_blocked_by_foreign_parcel       # ...but not because of a foreign parcel
    assert c.frontage_encumbered_ft > 2000 and c.frontage_open_ft == 0
    assert c.entry_node_count == 0
    assert c.largest_contiguous_reachable_acres == 0       # 91 usable acres, none reachable
    assert c.unreachable_island_acres == pytest.approx(c.usable_acres, abs=0.1)
    assert c.reachable_if_crossings_permitted_acres == 0   # an FCA easement is not a permit-crossable stream
    assert "usable_area_unreachable_from_frontage" in flags(scored, "FRED-C")


def test_reserve_strip_foreign_owner(scored, result):
    d = scored.loc["FRED-D"]
    assert d.landlocked_apparent
    assert d.frontage_blocked_by_foreign_parcel
    assert d.blocking_parcel_account_id == "FRED-S"
    assert d.blocking_parcel_owner == "DELTA DEVELOPMENT INC"
    assert d.frontage_foreign_pct == pytest.approx(100, abs=1)
    assert d.reserve_strip_detected
    strips = json.loads(d.reserve_strips_json)
    assert strips[0]["strip_account_id"] == "FRED-S" and not strips[0]["same_owner"]
    assert strips[0]["est_width_ft"] == pytest.approx(48, abs=2)
    assert strips[0]["aspect"] > 40
    assert d.largest_contiguous_reachable_acres == 0
    assert {"landlocked_apparent_check_deeded_access", "frontage_blocked_confirm_ownership_and_deeded_access",
            "reserve_strip_foreign_owner"} <= set(flags(scored, "FRED-D"))
    s = result["stage4"].strips
    assert set(s[s.account_id == "FRED-D"].strip_account_id) == {"FRED-S"}


def test_same_owner_strip_is_not_blocking(scored):
    e = scored.loc["FRED-E"]
    assert e.landlocked_apparent                           # zero direct contact, reported honestly
    assert e.access_via_same_owner_parcel
    assert not e.frontage_blocked_by_foreign_parcel
    assert not e.reserve_strip_detected                    # strip exists but is the same family
    assert json.loads(e.reserve_strips_json)[0]["same_owner"]
    assert e.frontage_same_owner_ft > 2000
    assert e.largest_contiguous_reachable_acres == pytest.approx(e.usable_acres, abs=0.1)
    assert "access_via_separately_deeded_same_owner_parcel" in flags(scored, "FRED-E")


def test_interior_parcel_landlocked(scored):
    h = scored.loc["FRED-H"]
    assert h.landlocked_apparent and h.no_public_row_nearby
    assert h.road_facing_ft == 0 and not h.frontage_blocked_by_foreign_parcel
    assert h.largest_contiguous_reachable_acres == 0


def test_malpf_parcel_fully_reachable(scored):
    i = scored.loc["FRED-I"]
    assert not i.landlocked_apparent
    assert i.largest_contiguous_reachable_acres == pytest.approx(101.2, abs=0.3)


def test_hilltop_island(scored):
    j = scored.loc["FRED-J"]
    assert not j.landlocked_apparent
    assert j.unreachable_island_count == 1
    assert 2.3 <= j.unreachable_island_acres <= 3.8          # analytic plateau ~3.0 ac
    assert j.largest_contiguous_reachable_acres == pytest.approx(j.usable_acres - j.unreachable_island_acres, abs=0.1)
    assert j.islands_reconnectable_by_permit_acres == 0      # steep ground is not permit-crossable


def test_partially_encumbered_frontage(scored):
    m = scored.loc["FRED-M"]
    assert not m.landlocked_apparent
    assert 10 < m.frontage_blocked_pct < 20                  # floodplain strip covers ~15% of the north frontage
    assert m.largest_contiguous_reachable_acres == pytest.approx(m.usable_acres, abs=0.1)


def test_outputs_written(fixture_dir):
    import geopandas as gpd
    import pandas as pd
    out = fixture_dir / "outputs"
    for f in ("parcels_stage1.gpkg", "encumbrances.csv", "usable_area.gpkg", "frontage.gpkg", "entry_points.gpkg",
              "reserve_strips.csv", "islands.csv", "parcels_scored.gpkg", "parcels_scored.csv", "parcels_scored.geojson",
              "summary.json", "summary.md"):
        assert (out / f).exists(), f
    csv = pd.read_csv(out / "parcels_scored.csv")
    assert list(csv.columns[:7]) == ["account_id", "owner_name", "owner_mailing_address", "owner_type", "owner_key", "gross_acres", "zoning"]
    assert "largest_contiguous_reachable_acres" in csv.columns and "manual_verification_flags" in csv.columns
    fr = gpd.read_file(out / "frontage.gpkg")
    assert set(fr["class"]) >= {"open", "encumbered", "foreign_parcel", "same_owner_parcel"}
    md = (out / "summary.md").read_text()
    assert "Stage 4" in md and "cannot determine" in md
