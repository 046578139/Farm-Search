"""Regression tests for the defects found by the adversarial review of Stages 5-10."""
import geopandas as gpd
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
    assert sc.loc["FRED-J", "candidate_backstop_slopes"] == True  # noqa: E712
