"""Synthetic validation dataset.

A small "county" laid out in EPSG:26985 (NAD83 / Maryland, meters) that
reproduces every Stage 1-4 failure mode the spec describes, with known
answers, so the pipeline can be validated end to end without network access.
Each parcel is named for what it tests:

  A  clean, 12-ac forest easement in the NE back corner, corner lot (county + state road)
  B  riparian buffer bisects the parcel: south half reachable, north half an island
  C  forest easement strip along the entire road frontage: frontage encumbered,
     usable area unreachable
  D  behind a 49 ft x 2,100 ft strip owned by someone else: landlocked +
     frontage_blocked_by_foreign_parcel + reserve strip
  E  behind an identical strip owned by the same family: landlocked but
     access_via_same_owner_parcel, NOT flagged as blocked
  F  31 acres: fails acreage
  G  residential zoning: fails zoning
  H  interior parcel with no road anywhere near it: landlocked
  I  94-ac MALPF easement: favorable, NOT subtracted from usable
  J  hill: ring of >15% slope around a 3-ac plateau island (from a synthetic DEM)
  K  15-ac parcel between H and the north road: fails acreage, blocks H
  L  split-zoned 60% agricultural / 40% residential: majority ag
  M  wetland in the middle + floodplain along one edge; SDAT acreage disagrees
  N  outside the study area
  O  Carroll County parcel, zoning from a differently-named field
  S  the strip in front of D (different owner)
  S2 the strip in front of E (same owner)
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin
from shapely.geometry import LineString, box
from shapely.ops import unary_union

CRS = "EPSG:26985"
# Shift the layout into real Maryland coordinates (roughly central Frederick County)
OX, OY = 320000.0, 195000.0


def B(x0, y0, x1, y1):
    return box(OX + x0, OY + y0, OX + x1, OY + y1)


def _parcel(acct, jurs, owner1, owner2, addr, city, zipc, geom, acres=None, lu="AG", zoning="A"):
    return {
        "ACCTID": acct, "JURSCODE": jurs, "OWNNAME1": owner1, "OWNNAME2": owner2,
        "OWNADD1": addr, "OWNADD2": None, "OWNCITY": city, "OWNSTATE": "MD", "OWNZIP": zipc,
        "ACRES": (round(geom.area / 4046.8564224, 2) if acres is None else acres),
        "LU": lu, "ZONING": zoning, "geometry": geom,
    }


def build_fixture(out: Path) -> Path:
    out = Path(out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    raw = out / "raw"

    # ---- Parcels -------------------------------------------------------
    rows = [
        # A split into two rows with the same account (tests dissolve)
        _parcel("FRED-A", "FRED", "ALPHA FARMS LLC", None, "100 ALPHA RD", "FREDERICK", "21701", B(0, 18, 320, 658)),
        _parcel("FRED-A", "FRED", "ALPHA FARMS LLC", None, "100 ALPHA RD", "FREDERICK", "21701", B(320, 18, 640, 658)),
        _parcel("FRED-B", "FRED", "BRAVO BETTY", None, "200 BRAVO LN", "FREDERICK", "21701", B(640, 18, 1280, 658)),
        _parcel("FRED-C", "FRED", "CHARLIE CHRIS", "CHARLIE CHRIS JR", "300 CHARLIE CT", "FREDERICK", "21701", B(1280, 18, 1920, 658)),
        _parcel("FRED-D", "FRED", "DOG DAVID", None, "400 DOG DR", "FREDERICK", "21701", B(1920, 33, 2560, 658)),
        _parcel("FRED-S", "FRED", "DELTA DEVELOPMENT INC", None, "1 CORPORATE WAY", "BALTIMORE", "21201", B(1920, 18, 2560, 33), lu="RES"),
        _parcel("FRED-E", "FRED", "ECHO EDWARD", None, "500 ECHO RD", "FREDERICK", "21701", B(2560, 33, 3200, 658)),
        _parcel("FRED-S2", "FRED", "ECHO EDWARD", "ECHO MARY", "500 ECHO RD", "FREDERICK", "21701", B(2560, 18, 3200, 33), lu="RES"),
        _parcel("FRED-F", "FRED", "FOXTROT FRANK", None, "600 FOX RD", "FREDERICK", "21701", B(3200, 18, 3400, 658)),
        _parcel("FRED-G", "FRED", "GOLF GARY", None, "700 GOLF RD", "FREDERICK", "21701", B(3400, 18, 4040, 658), zoning="R1"),
        _parcel("FRED-I", "FRED", "INDIA IRENE", None, "900 INDIA RD", "THURMONT", "21788", B(0, 658, 640, 1298)),
        _parcel("FRED-H", "FRED", "HOTEL HANK", None, "800 HOTEL RD", "FREDERICK", "21701", B(640, 658, 1280, 1200)),
        _parcel("FRED-K", "FRED", "KILO KAREN", None, "1100 KILO RD", "FREDERICK", "21701", B(640, 1200, 1280, 1298)),
        _parcel("FRED-J", "FRED", "JULIET JOAN", None, "1000 JULIET RD", "FREDERICK", "21701", B(1280, 658, 1920, 1298)),
        _parcel("FRED-L", "FRED", "LIMA LARRY", None, "1200 LIMA RD", "FREDERICK", "21701", B(1920, 658, 2560, 1298)),
        _parcel("FRED-M", "FRED", "MIKE MARTHA", None, "1300 MIKE RD", "FREDERICK", "21701", B(2560, 658, 3200, 1298), acres=80.0),
        _parcel("CARR-O", "CARR", "OSCAR OLIVIA TRUST", None, "1500 OSCAR RD", "MOUNT AIRY", "21771", B(3200, 658, 3840, 1298), zoning="AG"),
        _parcel("FRED-N", "FRED", "NOVEMBER NED", None, "1400 NOV RD", "FREDERICK", "21701", B(6000, 18, 6640, 658)),
    ]
    parcels = gpd.GeoDataFrame(rows, geometry="geometry", crs=CRS)
    (raw / "parcels").mkdir(exist_ok=True)
    parcels.to_file(raw / "parcels" / "parcels.gpkg", driver="GPKG")

    # ---- Zoning ----------------------------------------------------------
    extent = B(-300, -300, 4700, 1700)
    r1 = unary_union([B(3400, 18, 4040, 658), B(2304, 658, 2560, 1298)])   # G + east 40% of L
    fred_zoning = gpd.GeoDataFrame({"ZONING": ["A", "R1"], "DESCR": ["Agricultural", "Residential"]},
                                   geometry=[extent.difference(r1), r1], crs=CRS)
    carr_zoning = gpd.GeoDataFrame({"ZONE_": ["AG", "R-40"]},
                                   geometry=[B(3100, 600, 3900, 1400), B(3100, 1400, 3900, 1700)], crs=CRS)
    (raw / "zoning").mkdir(exist_ok=True)
    fred_zoning.to_file(raw / "zoning" / "frederick.gpkg", driver="GPKG")
    carr_zoning.to_file(raw / "zoning" / "carroll.gpkg", driver="GPKG")

    # ---- Constraints -----------------------------------------------------
    (raw / "constraints").mkdir(exist_ok=True)
    gpd.GeoDataFrame({"EASEMENT_ID": ["MALPF-1"]}, geometry=[B(0, 700, 640, 1298)], crs=CRS) \
        .to_file(raw / "constraints" / "malpf.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"FCA_ID": ["FCA-A", "FCA-C"]},
                     geometry=[B(420, 438, 640, 658), B(1280, 18, 1920, 78)], crs=CRS) \
        .to_file(raw / "constraints" / "fca_easements.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"WETLAND_TYPE": ["PEM1"]}, geometry=[B(2800, 900, 2980, 1080)], crs=CRS) \
        .to_file(raw / "constraints" / "nwi_wetlands.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"FLD_ZONE": ["AE", "X"]},
                     geometry=[B(2560, 658, 2655, 1298), B(2655, 658, 2700, 1298)], crs=CRS) \
        .to_file(raw / "constraints" / "fema_nfhl.gpkg", driver="GPKG")
    (raw / "hydro").mkdir(exist_ok=True)
    gpd.GeoDataFrame({"GNIS_NAME": ["Bravo Run"]},
                     geometry=[LineString([(OX + 600, OY + 400), (OX + 1340, OY + 400)])], crs=CRS) \
        .to_file(raw / "hydro" / "nhd_flowlines.gpkg", driver="GPKG")

    # ---- DEM: flat at 100 m with a Gaussian hill under parcel J ------------
    (raw / "lidar").mkdir(exist_ok=True)
    res = 5.0
    x0, y1 = OX - 200, OY + 1600
    ncol, nrow = int(4800 / res), int(1800 / res)
    xs = x0 + res * (np.arange(ncol) + 0.5)
    ys = y1 - res * (np.arange(nrow) + 0.5)
    X, Y = np.meshgrid(xs, ys)
    cx, cy, sigma, amp = OX + 1600, OY + 978, 150.0, 60.0
    Z = 100.0 + amp * np.exp(-((X - cx) ** 2 + (Y - cy) ** 2) / (2 * sigma ** 2))
    with rasterio.open(raw / "lidar" / "dem.tif", "w", driver="GTiff", height=nrow, width=ncol, count=1,
                       dtype="float32", crs=CRS, transform=from_origin(x0, y1, res, res), nodata=-9999.0) as ds:
        ds.write(Z.astype("float32"), 1)

    # ---- Rights-of-way -----------------------------------------------------
    (raw / "access").mkdir(exist_ok=True)
    gpd.GeoDataFrame({"ROAD_NAME": ["MAIN RD"], "OWNERSHIP": ["COUNTY"]},
                     geometry=[LineString([(OX - 100, OY + 9), (OX + 4200, OY + 9)])], crs=CRS) \
        .to_file(raw / "access" / "county_centerlines.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"ROAD_NAME": ["NORTH RD"]}, geometry=[B(-20, 1298, 4100, 1316)], crs=CRS) \
        .to_file(raw / "access" / "county_row_polygons.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"ROUTE": ["MD 999"]}, geometry=[B(-20, -50, 0, 1400)], crs=CRS) \
        .to_file(raw / "access" / "sha_row_polygons.gpkg", driver="GPKG")

    # ---- Stage 7-8 layers ----------------------------------------------------
    # Planned sewer (S-3) over the east end (G, O, F), existing sewer nowhere;
    # PFA over G and the 40% residential part of L; growth area = same as PFA;
    # pipeline: 120 approved units next to G, 40 units 1 km north of I (beyond
    # 2 mi of nothing in this small county, so within radius of all).
    (raw / "planning").mkdir(exist_ok=True)
    gpd.GeoDataFrame({"CATEGORY": ["S-3", "S-1", "S-6"]},
                     geometry=[B(3150, -50, 4200, 700), B(4200, -50, 4700, 700), B(-300, 1400, 4700, 1700)], crs=CRS) \
        .to_file(raw / "planning" / "sewer_service.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"PFA": ["Y"]}, geometry=[unary_union([B(3400, 18, 4040, 658), B(2304, 658, 2560, 1298)])], crs=CRS) \
        .to_file(raw / "planning" / "pfa.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"NAME": ["Growth Area East"]}, geometry=[B(3400, 18, 4040, 658)], crs=CRS) \
        .to_file(raw / "planning" / "growth_areas.gpkg", driver="GPKG")
    from shapely.geometry import Point
    gpd.GeoDataFrame({"PROJECT": ["Golf Estates", "North Farms"], "UNITS_REMAINING": [120, 40], "STATUS": ["Approved", "Approved"]},
                     geometry=[Point(OX + 4100, OY + 300), Point(OX + 320, OY + 2300)], crs=CRS) \
        .to_file(raw / "planning" / "pipeline.gpkg", driver="GPKG")
    # MPRP: preferred route crosses parcel I (north-west) diagonally; an
    # alternative runs 400 m north of the north road. Existing HV line along
    # x = 4500 (east of everything); substation 1 km east of O; a data-center
    # polygon 2 km east of G.
    (raw / "transmission").mkdir(exist_ok=True)
    gpd.GeoDataFrame({"ROUTE": ["Preferred"]}, geometry=[LineString([(OX - 200, OY + 900), (OX + 640, OY + 1500)])], crs=CRS) \
        .to_file(raw / "transmission" / "mprp_preferred.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"ROUTE": ["Alt B"]}, geometry=[LineString([(OX - 200, OY + 1700), (OX + 4700, OY + 1700)])], crs=CRS) \
        .to_file(raw / "transmission" / "mprp_alternative.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"VOLTAGE": [230]}, geometry=[LineString([(OX + 4100, OY - 500), (OX + 4100, OY + 2000)])], crs=CRS) \
        .to_file(raw / "transmission" / "hv_lines.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"NAME": ["Fixture Sub"]}, geometry=[Point(OX + 4300, OY + 978)], crs=CRS) \
        .to_file(raw / "transmission" / "substations.gpkg", driver="GPKG")
    gpd.GeoDataFrame({"NAME": ["Quantum Fixture"]}, geometry=[B(6000, 800, 6400, 1200)], crs=CRS) \
        .to_file(raw / "transmission" / "data_centers.gpkg", driver="GPKG")

    # ---- Study area (EPSG:4326, like a user-drawn polygon) ------------------
    sa = gpd.GeoDataFrame({"name": ["fixture study area"]}, geometry=[B(-100, -100, 4500, 1500)], crs=CRS).to_crs("EPSG:4326")
    sa.to_file(out / "study_area.geojson", driver="GeoJSON")

    # ---- Schema + zoning mappings + config ---------------------------------
    (out / "schema").mkdir(exist_ok=True)
    (out / "schema" / "parcels.yaml").write_text(yaml.safe_dump({
        "required": {"account_id": ["ACCTID"], "county_code": ["JURSCODE"], "owner_name": ["OWNNAME1"],
                     "owner_addr_line1": ["OWNADD1"], "owner_city": ["OWNCITY"], "owner_state": ["OWNSTATE"], "owner_zip": ["OWNZIP"]},
        "optional": {"owner_name2": ["OWNNAME2"], "owner_addr_line2": ["OWNADD2"], "acreage_sdat": ["ACRES"],
                     "land_use_code": ["LU"], "zoning_sdat": ["ZONING"], "assessed_total_value": ["NFMTTLVL"]},
    }))
    (out / "zoning").mkdir(exist_ok=True)
    (out / "zoning" / "frederick.yaml").write_text(yaml.safe_dump({
        "code_field": "ZONING",
        "codes": {"A": {"description": "Agricultural", "is_agricultural": True},
                  "R1": {"description": "Residential", "is_agricultural": False}}}))
    (out / "zoning" / "carroll.yaml").write_text(yaml.safe_dump({
        "code_field": "ZONE_",
        "codes": {"AG": {"description": "Agricultural", "is_agricultural": True},
                  "R-40": {"description": "Residential 40k", "is_agricultural": False}}}))

    cfg = {
        "acreage_min": 40, "acreage_max": None, "slope_max_pct": 15,
        "study_area": "study_area.geojson", "study_area_selection": "intersects", "working_crs": CRS,
        "parcels": {"path": "raw/parcels", "schema": "schema/parcels.yaml", "acreage_source": "sdat", "acreage_disagreement_pct": 10,
                    "account_id_regex": None},   # synthetic ids like FRED-A are not SDAT-shaped
        "counties": {"FRED": "Frederick", "CARR": "Carroll", "WASH": "Washington"},
        "zoning": [
            {"county": "Frederick", "path": "raw/zoning/frederick.gpkg", "code_field": "ZONING", "mapping": "zoning/frederick.yaml"},
            {"county": "Carroll", "path": "raw/zoning/carroll.gpkg", "code_field": "ZONE_", "mapping": "zoning/carroll.yaml"},
        ],
        "on_unmapped_zoning": "error",
        "constraints": [
            {"name": "malpf", "type": "ag_preservation_malpf", "category": "legal", "implication": "favorable",
             "subtract_from_usable": False, "crossable_with_permit": False, "path": "raw/constraints/malpf.gpkg", "name_field": "EASEMENT_ID"},
            {"name": "forest_conservation", "type": "forest_conservation", "category": "legal", "implication": "hostile",
             "subtract_from_usable": True, "crossable_with_permit": False, "path": "raw/constraints/fca_easements.gpkg", "name_field": "FCA_ID"},
            {"name": "wetlands", "type": "wetlands", "category": "physical", "implication": "physical",
             "subtract_from_usable": True, "crossable_with_permit": True, "path": "raw/constraints/nwi_wetlands.gpkg"},
            {"name": "floodplain", "type": "floodplain", "category": "physical", "implication": "physical",
             "subtract_from_usable": True, "crossable_with_permit": True, "path": "raw/constraints/fema_nfhl.gpkg",
             "where": "FLD_ZONE in ['A', 'AE', 'AO', 'AH']"},
            {"name": "riparian_presumed", "type": "riparian_buffer", "category": "physical", "implication": "hostile",
             "subtract_from_usable": True, "crossable_with_permit": True,
             "manual_flag": "riparian_buffer_presumed_confirm_with_seller",
             "derive_from_lines": {"path": "raw/hydro/nhd_flowlines.gpkg", "buffer_ft": 100}},
        ],
        "slope": {"dem_path": "raw/lidar/dem.tif", "dem_vertical_unit_to_m": 1.0, "dem_resample_m": None, "crossable": False},
        "access": {
            "row_layers": [
                {"name": "county_centerlines", "authority": "county", "public": True, "geometry": "line", "row_width_ft": 60,
                 "path": "raw/access/county_centerlines.gpkg"},
                {"name": "county_row_polygons", "authority": "county", "public": True, "geometry": "polygon",
                 "path": "raw/access/county_row_polygons.gpkg"},
                {"name": "sha_row_polygons", "authority": "state", "public": True, "geometry": "polygon",
                 "path": "raw/access/sha_row_polygons.gpkg"},
            ],
            "contact_tolerance_ft": 3, "open_gap_ft": 25, "min_contact_ft": 20, "frontage_search_ft": 250,
            "frontage_sample_ft": 15, "frontage_blocked_threshold": 0.95, "strip_max_width_ft": 100,
            "strip_min_aspect": 6, "sliver_acres": 0.25,
        },
        "encroachment": {
            "sewer_layers": [{"name": "sewer_planned", "path": "raw/planning/sewer_service.gpkg", "where": "CATEGORY in ['S-3', 'S-4', 'S-5']"}],
            "sewer_existing_layers": [{"name": "sewer_existing", "path": "raw/planning/sewer_service.gpkg", "where": "CATEGORY == 'S-1'"}],
            "pfa_layers": [{"name": "pfa", "path": "raw/planning/pfa.gpkg"}],
            "growth_area_layers": [{"name": "growth_areas", "path": "raw/planning/growth_areas.gpkg"}],
            "pipeline_layers": [{"name": "pipeline", "path": "raw/planning/pipeline.gpkg", "units_field": "UNITS_REMAINING"}],
            "pipeline_radius_ft": 10560,
        },
        "transmission": {
            "mprp_routes": [{"name": "mprp_preferred", "path": "raw/transmission/mprp_preferred.gpkg", "variant": "preferred"},
                            {"name": "mprp_alternative", "path": "raw/transmission/mprp_alternative.gpkg", "variant": "alternative"}],
            "mprp_corridor_width_ft": 150, "mprp_exclusion_buffer_ft": 2000, "mprp_general_corridor_ft": 5280,
            "hv_line_layers": [{"name": "hv_lines", "path": "raw/transmission/hv_lines.gpkg"}],
            "hv_line_buffer_ft": 1000,
            "substation_layers": [{"name": "substations", "path": "raw/transmission/substations.gpkg"}],
            "substation_buffer_ft": 2640,
            "data_center_layers": [{"name": "data_centers", "path": "raw/transmission/data_centers.gpkg"}],
            "data_center_buffer_ft": 15840,
            "points_of_concern": [{"name": "Fixture Doubs", "lon": -77.45, "lat": 39.25, "buffer_ft": 100}],
            "status_note": "fixture",
        },
        "run": {"process_all": False, "output_dir": "outputs"},
    }
    cfg_path = out / "pipeline.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return cfg_path
