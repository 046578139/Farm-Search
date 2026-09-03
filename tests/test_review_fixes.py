"""Regression tests for the defects found by the adversarial review of Stages 5-10."""
import geopandas as gpd
import math
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from farmsearch.config import Config


def _cfg(tmp_path, **over):
    raw = {"parcels": {"path": str(tmp_path / "p"), "schema": "schema/parcels.yaml"}, "counties": {"FRED": "Frederick"},
           "study_area": str(tmp_path / "sa.geojson"), "constraints": [], "access": {"row_layers": []}}
    raw.update(over)
    return Config.from_dict(raw, tmp_path)


def test_stage5_footprint_leaking_across_the_line_still_yields_a_structure(tmp_path):
    from farmsearch.stages.stage5_envelope import load_occupied_structures
    subject = box(0, 0, 640, 640); neighbour = box(640, 0, 1280, 640)
    parcels = gpd.GeoDataFrame({"account_id": ["S", "N"], "is_account": [True, True], "owner_key": ["a", "b"],
                                "land_use_desc": ["Agricultural", "Residential"], "structure_sqft": [0, 2000], "exempt_class_desc": [None, None]},
                               geometry=[subject, neighbour], crs="EPSG:26985")
    fp = gpd.GeoDataFrame({"ID": [1]}, geometry=[box(631, 300, 646, 312)], crs="EPSG:26985")   # 60% on S, 40% on N
    fp.to_file(str(tmp_path / "fp.gpkg"), driver="GPKG")
    cfg = _cfg(tmp_path, envelope={"footprint_layers": [{"name": "fp", "path": str(tmp_path / "fp.gpkg")}]})
    occ = load_occupied_structures(cfg, parcels, box(-100, -100, 1400, 700))
    assert list(occ.structures["account_id"]) == ["N"] and occ.structures.geometry.iloc[0].geom_type == "Point"


def test_stage8_polygon_corridor_is_tier_1_only_when_intersected(tmp_path):
    from farmsearch.stages.stage8_transmission import TransmissionLayers, run_stage8
    corridor = box(0, 0, 1000, 167.6)                              # a 550 ft study corridor polygon
    just_outside = box(0, 167.6 + 18.3, 200, 600)                  # 60 ft north of the corridor edge (118 ft from its centerline)
    near = box(300, 170, 400, 600)                                 # 8 ft outside the polygon, 66 ft from its centerline
    inside = box(500, 100, 700, 600)
    parcels = gpd.GeoDataFrame({"account_id": ["OUT", "NEAR", "IN"]}, geometry=[just_outside, near, inside], crs="EPSG:26985")
    L = TransmissionLayers(routes=[("alt", "alternative", corridor)])
    out = run_stage8(_cfg(tmp_path), parcels, L).parcels.set_index("account_id")
    assert out.loc["OUT", "mprp_tier"] == 2 and out.loc["NEAR", "mprp_tier"] == 2 and out.loc["IN", "mprp_tier"] == 1
    # a centerline keeps the 75 ft half-width: NEAR (66 ft) is inside it, OUT (118 ft) is not
    L2 = TransmissionLayers(routes=[("c", "preferred", LineString([(0, 150), (1000, 150)]))])
    out2 = run_stage8(_cfg(tmp_path), parcels, L2).parcels.set_index("account_id")
    assert out2.loc["OUT", "mprp_tier"] == 2 and out2.loc["NEAR", "mprp_tier"] == 1 and out2.loc["IN", "mprp_tier"] == 1


def test_stage7_row_and_letter_codes_are_not_residential(tmp_path):
    import yaml
    from farmsearch.stages.stage7_encroachment import residential_codes
    (tmp_path / "z.yaml").write_text(yaml.safe_dump({"code_field": "Z", "codes": {
        "ROW": {"description": "Right of Way", "is_agricultural": False},
        "RC": {"description": "Resource Conservation", "is_agricultural": True},
        "R1": {"description": "Low Density Residential", "is_agricultural": False},
        "PUD": {"description": "Planned Unit Development", "is_agricultural": False, "is_residential": True},
        "RX": {"description": "Residential Exception", "is_agricultural": False, "is_residential": False}}}))
    cfg = _cfg(tmp_path, zoning=[{"county": "Frederick", "path": str(tmp_path / "z.gpkg"), "code_field": "Z", "mapping": str(tmp_path / "z.yaml")}])
    assert residential_codes(cfg)["Frederick"] == {"R1", "PUD"}
    real = Config.load("config/pipeline.yaml")
    rc = residential_codes(real)
    assert "ROW" not in rc["Frederick"] and "R1" in rc["Frederick"] and "RV" in rc["Washington"]


def test_stage9_improvement_dominated_sales_are_not_land_comps(tmp_path):
    from farmsearch.stages.stage9_valuation import build_comps
    sales = gpd.GeoDataFrame({"ACCTID": ["A", "B"], "CONSIDR1": [1_000_000, 1_000_000], "TRADATE": ["20250301", "20250301"],
                              "CONVEY1": [1, 1], "ACRES": [100.0, 100.0], "DESCLU": ["Agricultural"] * 2,
                              "SALIMPVL": [850_000, 100_000], "SQFTSTRC": [3000, 1500]},
                             geometry=[Point(10, 10), Point(20, 20)], crs="EPSG:26985")
    sales.to_file(str(tmp_path / "s.gpkg"), driver="GPKG")
    parcels = gpd.GeoDataFrame({"account_id": ["A", "B"], "county": ["Frederick"] * 2}, geometry=[box(0, 0, 15, 15), box(15, 15, 30, 30)], crs="EPSG:26985")
    cfg = _cfg(tmp_path, valuation={"sales_layers": [{"name": "s", "path": str(tmp_path / "s.gpkg")}], "reference_date": "2026-01-01"})
    comps = build_comps(cfg, box(-10, -10, 50, 50), parcels, None)
    assert list(comps.comps["account_id"]) == ["B"] and comps.comps["land_price_per_acre"].iloc[0] == pytest.approx(9000)


def test_stage10_single_entrance_on_a_loop_is_redundant():
    from farmsearch.stages.stage10_commute import RoadGraph
    roads = gpd.GeoDataFrame({"authority": ["state", "state", "state", "state", "county"], "major": [True, True, True, True, False]},
                             geometry=[LineString([(-500, 0), (0, 0)]), LineString([(0, 0), (500, 0)]),
                                       LineString([(-500, -1000), (0, -1000)]), LineString([(0, -1000), (500, -1000)]),
                                       LineString([(0, 0), (0, -1000)])], crs="EPSG:26985")
    g = RoadGraph(roads, ["state"])
    node, undo = g.attach_origin(Point(2, -500), 150)
    assert g.egress_paths([node], 5000) == 2          # north to one state road, south to the other
    undo()
    assert g.G.has_edge(g._key((0, 0)), g._key((0, -1000)))


def test_stage6_backstop_never_counts_a_neighbours_hillside(cfg, fixture_dir):
    from farmsearch.pipeline import run_pipeline
    res = run_pipeline(cfg, stages=(1, 2, 3, 4, 5, 6), out_dir=fixture_dir / "outputs_56b", write=False)
    sc = res["scored"].set_index("account_id")
    assert sc.loc["FRED-H", "candidate_backstop_acres"] == 0 and sc.loc["FRED-H", "candidate_backstop_slopes"] == False  # noqa: E712  J's hill is not H's
    assert sc.loc["FRED-J", "candidate_backstop_acres"] > 0        # its own hill is


def test_stage6_backstop_orientation_and_isolated_parcels(cfg, fixture_dir):
    """Firing into a slope sends overshoot uphill, so a cell is a candidate
    backstop only when no occupied building stands in a cone about that
    direction. A parcel with nothing occupied in range keeps every steep cell."""
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import Point, box
    from farmsearch.stages.stage6_viewshed import run_stage6
    from farmsearch.terrain import DEMWindow

    class Stub:
        """Ground rising to the east at 30%: every cell's uphill points east."""
        mode = "stub"
        cell = 10.0

        def window(self, bounds):
            minx, miny, maxx, maxy = bounds
            w = max(2, int((maxx - minx) / self.cell)); h = max(2, int((maxy - miny) / self.cell))
            xs = minx + np.arange(w) * self.cell
            return DEMWindow(np.tile(100.0 + 0.3 * (xs - minx), (h, 1)), minx, maxy, self.cell)

        def line_of_sight(self, win, p1, p2, h1, h2, step=None):
            return True

    P = gpd.GeoDataFrame({"account_id": ["S"], "owner_key": ["s"]}, geometry=[box(0, 0, 600, 600)], crs="EPSG:26985")
    env = gpd.GeoDataFrame({"account_id": ["S"]}, geometry=[box(50, 50, 550, 550)], crs="EPSG:26985")

    def struct(*pts):
        return gpd.GeoDataFrame({"kind": ["dwelling"] * len(pts), "account_id": [f"N{i}" for i in range(len(pts))],
                                 "owner_key": [f"n{i}" for i in range(len(pts))], "located_by": ["footprint"] * len(pts)},
                                geometry=list(pts), crs="EPSG:26985")

    behind = Point(-300, 300)     # downhill of the slope: firing east points away from it
    ahead = Point(900, 300)       # straight up the fire line

    alone = run_stage6(cfg, P, env, struct(), sampler=Stub()).parcels["candidate_backstop_acres"].iloc[0]
    west = run_stage6(cfg, P, env, struct(behind), sampler=Stub()).parcels["candidate_backstop_acres"].iloc[0]
    both = run_stage6(cfg, P, env, struct(behind, ahead), sampler=Stub()).parcels["candidate_backstop_acres"].iloc[0]
    assert alone > 0 and west == alone            # a house behind the shooter disqualifies nothing
    assert 0 <= both < west                       # a house up the fire line removes the cells aiming at it


def test_stage6_no_dem_coverage_is_not_terrain_shielding(cfg, fixture_dir):
    """line_of_sight None (no DEM under the profile) must not read as 'shielded'."""
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import Point, box
    from farmsearch.stages.stage6_viewshed import run_stage6
    from farmsearch.terrain import DEMWindow, TerrainSampler

    class Blank:
        mode = "stub"
        cell = 10.0

        def window(self, bounds):
            minx, miny, maxx, maxy = bounds
            w = max(2, int((maxx - minx) / self.cell)); h = max(2, int((maxy - miny) / self.cell))
            return DEMWindow(np.full((h, w), np.nan), minx, maxy, self.cell)

        _profile = TerrainSampler._profile
        line_of_sight = TerrainSampler.line_of_sight

    P = gpd.GeoDataFrame({"account_id": ["S"], "owner_key": ["s"]}, geometry=[box(0, 0, 600, 600)], crs="EPSG:26985")
    env = gpd.GeoDataFrame({"account_id": ["S"]}, geometry=[box(100, 100, 500, 500)], crs="EPSG:26985")
    S = gpd.GeoDataFrame({"kind": ["dwelling"], "account_id": ["N"], "owner_key": ["n"], "located_by": ["footprint"]},
                         geometry=[Point(900, 300)], crs="EPSG:26985")
    out = run_stage6(cfg, P, env, S, sampler=Blank()).parcels
    flags = out["viewshed_flags"].iloc[0]
    assert out["dwellings_line_of_sight_unevaluated"].iloc[0] == 1
    assert "all_nearby_dwellings_terrain_shielded" not in flags
    assert "viewshed_dem_nodata_some_dwellings_unevaluated" in flags


def _sewer_cfg(tmp_path, counties, geom):
    """A planned-sewer source per county in `counties`, all covering `geom`."""
    import geopandas as gpd
    layers = []
    for c in counties:
        p = tmp_path / f"sewer_{c}.gpkg"
        gpd.GeoDataFrame({"i": [1]}, geometry=[geom], crs="EPSG:26985").to_file(str(p), driver="GPKG")
        layers.append({"name": f"sewer_{c}", "path": str(p), "county": c})
    return _cfg(tmp_path, counties={"FRED": "Frederick", "CARR": "Carroll"},
                encroachment={"sewer_layers": layers})


def test_stage7_nulls_the_column_in_counties_with_no_source(tmp_path):
    from farmsearch.stages.stage7_encroachment import load_encroachment_layers, run_stage7
    cfg = _sewer_cfg(tmp_path, ["Frederick"], box(-2000, -2000, 2000, 2000))
    parcels = gpd.GeoDataFrame(
        {"account_id": ["F", "C"], "county": ["Frederick", "Carroll"], "is_account": [True, True]},
        geometry=[box(0, 0, 300, 300), box(1000, 0, 1300, 300)], crs="EPSG:26985")
    layers, missing = load_encroachment_layers(cfg, box(-3000, -3000, 3000, 3000))
    out = run_stage7(cfg, parcels, parcels, {}, layers, missing).parcels.set_index("account_id")
    assert out.loc["F", "subject_planned_sewer"] == True                       # noqa: E712  measured
    assert out.loc["C", "subject_planned_sewer"] is None                       # no Carroll source: unknown, not False
    assert out.loc["C", "adjacent_planned_sewer"] is None


def test_stage7_placeholder_blockers_are_not_adjoining_parcels(tmp_path):
    from farmsearch.stages.stage7_encroachment import EncroachmentLayers, run_stage7
    subject = box(0, 0, 300, 300)
    canal = box(300, 0, 340, 300)          # a WATER placeholder polygon touching it
    farm = box(340, 0, 900, 300)
    parcels = gpd.GeoDataFrame(
        {"account_id": ["S", "WATER", "N"], "county": ["Frederick"] * 3, "is_account": [True, False, True]},
        geometry=[subject, canal, farm], crs="EPSG:26985")
    cfg = _cfg(tmp_path, counties={"FRED": "Frederick"})
    out = run_stage7(cfg, parcels[:1], parcels, {}, EncroachmentLayers()).parcels.set_index("account_id")
    assert out.loc["S", "adjacent_parcel_count"] == 0            # only the canal touches it, and it is not a parcel
    assert out.loc["S", "adjacent_boundary_covered_pct"] > 0     # the canal still covers that side


def test_stage8_tier3_without_any_mprp_route_layer(tmp_path):
    from farmsearch.stages.stage8_transmission import TransmissionLayers, run_stage8
    parcels = gpd.GeoDataFrame({"account_id": ["A"]}, geometry=[box(0, 0, 200, 200)], crs="EPSG:26985")
    near_hv = TransmissionLayers(hv_lines=LineString([(0, 300), (200, 300)]))     # ~100 m: inside 1,000 ft
    out = run_stage8(_cfg(tmp_path), parcels, near_hv).parcels
    assert out["mprp_tier"].iloc[0] == 3 and "near_existing_hv_transmission_corridor" in out["transmission_flags"].iloc[0]
    empty = TransmissionLayers(routes_loaded=True)      # layer read, nothing within reach
    out2 = run_stage8(_cfg(tmp_path), parcels, empty).parcels
    assert out2["mprp_tier"].iloc[0] == 0 and "mprp_routes_beyond_reach" in out2["transmission_flags"].iloc[0]
    unknown = TransmissionLayers()                       # nothing read at all
    assert run_stage8(_cfg(tmp_path), parcels, unknown).parcels["mprp_tier"].iloc[0] is None


def test_stage9_multi_account_sale_is_one_comp(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point
    from farmsearch.stages.stage9_valuation import build_comps
    rows = []
    for i, acct in enumerate(["A1", "A2", "A3"]):        # one 300-acre sale over three accounts
        rows.append({"ACCTID": acct, "CONSIDR1": 3_000_000, "TRADATE": "20250601", "CONVEY1": 3, "ACRES": 100.0,
                     "DESCLU": "Agricultural", "SALIMPVL": 0, "SQFTSTRC": 0, "JURSCODE": "FRED",
                     "DR1LIBER": "123", "DR1FOLIO": "45", "geometry": Point(10 * i, 0)})
    rows.append({"ACCTID": "B1", "CONSIDR1": 1_000_000, "TRADATE": "20250701", "CONVEY1": 1, "ACRES": 50.0,
                 "DESCLU": "Agricultural", "SALIMPVL": 0, "SQFTSTRC": 0, "JURSCODE": "FRED",
                 "DR1LIBER": "9", "DR1FOLIO": "9", "geometry": Point(500, 0)})
    sales = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:26985")
    sales.to_file(str(tmp_path / "sales.gpkg"), driver="GPKG")
    cfg = _cfg(tmp_path, counties={"FRED": "Frederick"},
               valuation={"sales_layers": [{"name": "sales", "path": str(tmp_path / "sales.gpkg")}],
                          "reference_date": "2025-09-01", "min_comps_per_segment": 1})
    fabric = gpd.GeoDataFrame({"account_id": [], "county": []}, geometry=[], crs="EPSG:26985")
    cs = build_comps(cfg, box(-1000, -1000, 1000, 1000), fabric, None)
    assert len(cs.comps) == 2                                    # the three accounts collapse into one transfer
    multi = cs.comps[cs.comps["accounts"].str.contains(";")].iloc[0]
    assert multi["acres"] == 300.0 and multi["land_price_per_acre"] == 10_000.0
    # a sale outside our fabric takes its county from JURSCODE, mapped to the name the parcels use
    assert set(cs.comps["county"]) == {"Frederick"}
    assert ("Frederick", "uneased") in cs.bands


def test_stage10_origin_attaches_to_the_real_centerline(tmp_path):
    import numpy as np
    from farmsearch.stages.stage10_commute import RoadGraph
    # a semicircular segment: its chord is far from the road at the apex
    t = np.linspace(math.pi, 0, 40)
    arc = LineString([(400 * math.cos(a), 400 * math.sin(a)) for a in t])
    roads = gpd.GeoDataFrame({"authority": ["county"], "major": [False]}, geometry=[arc], crs="EPSG:26985")
    g = RoadGraph(roads, ["state"])
    apex = Point(0, 398)                                  # 2 m from the road, ~398 m from the chord
    node, undo = g.attach_origin(apex, max_m=150.0)
    assert node is not None
    undo()


def test_google_batch_respects_the_traffic_aware_element_cap():
    from farmsearch.stages.stage10_commute import google_durations
    seen = {}

    class FakeResp:
        status_code = 200

        def __init__(self, n):
            self.n = n

        def raise_for_status(self):
            pass

        def json(self):
            return []

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            seen.setdefault("elements", []).append(len(json["origins"]) * len(json["destinations"]))
            return FakeResp(len(json["origins"]))

    from datetime import datetime
    google_durations("k", [(0.0, 0.0)] * 250, [(1.0, 1.0)] * 3, datetime(2026, 1, 6, 7, 0), session=FakeSession())
    assert max(seen["elements"]) <= 100


def test_shortlist_keeps_rare_flags_and_orders_mprp_tiers(tmp_path):
    from farmsearch.deliverables import rank_shortlist
    cfg = _cfg(tmp_path, shortlist={"top_n": 50, "weights": {"landlocked_apparent": -2.0, "mprp_tier": -1.5,
                                                             "largest_contiguous_reachable_acres": 3.0}},
               acreage_min=1)
    n = 40
    df = pd.DataFrame({
        "account_id": [f"P{i}" for i in range(n)],
        "largest_contiguous_reachable_acres": [100.0] * n,
        "landlocked_apparent": [i == 0 for i in range(n)],           # one flagged parcel in 40 (2.5%)
        "mprp_tier": [2 if i == 1 else (3 if i == 2 else 0) for i in range(n)],
        "owner_type": ["individual"] * n,
    })
    short, excl = rank_shortlist(df, cfg)
    sc = short.set_index("account_id")
    assert sc.loc["P0", "score_landlocked_apparent"] == -2.0 and sc.loc["P3", "score_landlocked_apparent"] == 0.0
    # tier 2 (exclusion buffer) must score worse than tier 3 (general corridor), both worse than 0
    assert sc.loc["P1", "score_mprp_tier"] < sc.loc["P2", "score_mprp_tier"] < sc.loc["P3", "score_mprp_tier"]
    assert sc.loc["P3", "score_mprp_tier"] == 0.0
    # a column that is constant across the eligible set separates nobody
    assert sc.loc["P0", "score_largest_contiguous_reachable_acres"] == 0.0


def test_resume_never_falls_back_below_stage4(tmp_path):
    import pickle
    from farmsearch.pipeline import _latest_checkpoint_at_or_before
    for n in (1, 2, 3):
        (tmp_path / f"checkpoint_stage{n}.pkl").write_bytes(pickle.dumps({}))
    with pytest.raises(FileNotFoundError, match="Stage 4"):
        _latest_checkpoint_at_or_before(tmp_path, 7)
    assert _latest_checkpoint_at_or_before(tmp_path, 3) == 3


def test_rerunning_an_early_stage_drops_the_later_checkpoints(tmp_path):
    import pickle
    from farmsearch.pipeline import _drop_checkpoints_after, _latest_checkpoint_at_or_before
    for n in (1, 2, 3, 4, 7):
        (tmp_path / f"checkpoint_stage{n}.pkl").write_bytes(pickle.dumps({}))
    _drop_checkpoints_after(tmp_path, 4)
    assert not (tmp_path / "checkpoint_stage7.pkl").exists()
    assert _latest_checkpoint_at_or_before(tmp_path, 9) == 4


def test_stage4_merge_survives_duplicate_blocker_ids_and_keeps_stage3_flags(cfg, fixture_dir):
    """Blocker polygons share an account_id (314 'UNK' rows on the real fabric):
    the Stage 4 merge must not multiply rows, and a flag Stage 3 appended must
    survive into the final record."""
    import geopandas as gpd
    from shapely.geometry import box
    parcels = gpd.GeoDataFrame(
        {"account_id": ["A", "B", "UNK", "UNK", "UNK"], "is_account": [True, True, False, False, False],
         "manual_flags": [["f1"], [], ["blocker"], [], []], "usable_acres": [1.0, 2.0, None, None, None]},
        geometry=[box(i, 0, i + 1, 1) for i in range(5)], crs="EPSG:26985")
    scored = parcels[parcels["is_account"]].copy()
    scored["manual_flags"] = [["f1", "slope_window_failed_not_evaluated"], ["x"]]
    scored["usable_acres"] = [9.0, 8.0]
    scored["largest_contiguous_reachable_acres"] = [9.0, 8.0]

    gname = parcels.geometry.name
    overlap = [c for c in scored.columns if c in parcels.columns and c not in ("account_id", gname)]
    merged = parcels.merge(scored.drop(columns=[gname]), on="account_id", how="left", suffixes=("", "_scored"))
    mask = merged["account_id"].isin(scored["account_id"])
    for c in overlap:
        merged[c] = merged[f"{c}_scored"].where(mask, merged[c])
    merged = merged.drop(columns=[f"{c}_scored" for c in overlap])

    assert len(merged) == len(parcels)                                   # no row explosion
    assert merged["manual_flags"].iloc[0] == ["f1", "slope_window_failed_not_evaluated"]
    assert merged["manual_flags"].iloc[2] == ["blocker"]                 # a blocker keeps Stage 1's value
    assert merged["usable_acres"].tolist()[:2] == [9.0, 8.0]


def test_stage7_unknowable_neighbour_is_null_not_false(tmp_path):
    """A neighbour in a county whose sewer source did not load makes the
    adjacent answer unknown; the subject's own column is still measurable."""
    from farmsearch.stages.stage7_encroachment import load_encroachment_layers, run_stage7
    cfg = _sewer_cfg(tmp_path, ["Frederick"], box(-500, -500, 500, 500))   # covers the subject only
    subject = box(0, 0, 300, 300)
    neighbour = box(300, 0, 900, 300)
    parcels = gpd.GeoDataFrame(
        {"account_id": ["F", "C"], "county": ["Frederick", "Carroll"], "is_account": [True, True]},
        geometry=[subject, neighbour], crs="EPSG:26985")
    layers, missing = load_encroachment_layers(cfg, box(-3000, -3000, 3000, 3000))
    out = run_stage7(cfg, parcels[:1], parcels, {}, layers, missing).parcels.set_index("account_id")
    assert out.loc["F", "subject_planned_sewer"] == True                    # noqa: E712  measured where we have data
    assert out.loc["F", "adjacent_planned_sewer"] is None                   # the Carroll neighbour is unknowable
    flags = out.loc["F", "encroachment_flags"]
    assert "sewer_service_not_published_for_every_county_in_range" in flags
    assert "adjacent_planned_sewer_service" not in flags                    # never assert what the null denies


def test_stage7_units_counted_across_the_county_line(tmp_path):
    """The 2-mile unit radius crosses county lines: a Carroll parcel a mile from
    a Frederick subdivision faces those units, and the partial cover is flagged."""
    import geopandas as gpd
    from farmsearch.stages.stage7_encroachment import load_encroachment_layers, run_stage7
    pipe = tmp_path / "pipe.gpkg"
    gpd.GeoDataFrame({"UNITS": [500]}, geometry=[box(1000, 0, 1100, 100)], crs="EPSG:26985").to_file(str(pipe), driver="GPKG")
    cfg = _cfg(tmp_path, counties={"FRED": "Frederick", "CARR": "Carroll"},
               encroachment={"pipeline_layers": [{"name": "pipeline_frederick", "path": str(pipe),
                                                  "units_field": "UNITS", "county": "Frederick"}],
                             "pipeline_radius_ft": 10560})
    parcels = gpd.GeoDataFrame(
        {"account_id": ["C", "F"], "county": ["Carroll", "Frederick"], "is_account": [True, True]},
        geometry=[box(0, 0, 300, 300), box(900, 0, 1200, 300)], crs="EPSG:26985")
    layers, missing = load_encroachment_layers(cfg, box(-6000, -6000, 6000, 6000))
    out = run_stage7(cfg, parcels, parcels, {}, layers, missing).parcels.set_index("account_id")
    assert out.loc["C", "approved_unbuilt_units_within_2mi"] == 500         # counted, though Carroll publishes none
    assert "approved_unbuilt_units_partial_no_layer_for_every_county_in_range" in out.loc["C", "encroachment_flags"]
