"""Tests for the live-data plumbing added when the real Maryland sources were
wired up: schema reconciliation against the real field list, Stage 1 without
owner names, non-parcel row exclusion, ESRI JSON page parsing, REST paging
with exceededTransferLimit, the ImageServer slope reader and the study-area
builder. Nothing here touches the network."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from farmsearch.config import Config
from farmsearch.io.arcgis import ArcGISLayer
from farmsearch.io.arcgis import esrijson_to_gdf, fetch_layer_gdf
from farmsearch.io.parcels_rest import fetch_county_pages, wanted_fields
from farmsearch.io.schema import load_schema_spec, resolve_schema
from farmsearch.slope import ImageServerDEM, steep_polygons_from_imageserver
from farmsearch.stages.stage1_base_filter import attribute_owners, load_parcels

ROOT = Path(__file__).resolve().parents[1]

# Field names of PlanningCadastre/MD_ParcelBoundaries/MapServer/0 as served on
# 2026-09-02 (117 fields; the ones the schema map can touch).
LIVE_PARCEL_FIELDS = [
    "OBJECTID", "JURSCODE", "ACCTID", "CT2020", "BG2020", "GEOGCODE", "OOI", "RESITYP", "ADDRESS", "STRTNUM",
    "CITY", "ZIPCODE", "OWNADD1", "OWNADD2", "OWNCITY", "OWNSTATE", "OWNERZIP", "OWNZIP2", "PREMSNUM", "LEGAL1",
    "DR1LIBER", "DR1FOLIO", "TOWNCODE", "PLAT", "SECTION", "BLOCK", "LOT", "MAP", "GRID", "PARCEL", "ZONING",
    "CIUSE", "EXCLASS", "DESCEXCL", "LU", "DESCLU", "ACRES", "LANDAREA", "LUOM", "WIDTH", "DEPTH", "PFUW", "PFUS",
    "PFLW", "PFSP", "PFSU", "YEARBLT", "SQFTSTRC", "TRADATE", "CONSIDR1", "NFMLNDVL", "NFMIMPVL", "NFMTTLVL",
    "CRTARCOD", "FCMACODE", "AGFNDAREA", "PTYPE", "SDATWEBADR", "MDPVDATE", "SDATDATE", "POLYDATE", "POLYACRES",
    "POLYID", "Shape", "Shape.STArea()", "Shape.STLength()",
]


def _cfg(tmp_path: Path, **overrides) -> Config:
    raw = {
        "parcels": {"path": str(tmp_path / "parcels"), "schema": str(ROOT / "config/schema/parcels.yaml"),
                    "url": "https://example.invalid/MapServer/0"},
        "counties": {"FRED": "Frederick", "CARR": "Carroll", "WASH": "Washington"},
        "study_area": str(tmp_path / "sa.geojson"),
        "constraints": [], "access": {"row_layers": []},
    }
    raw.update(overrides)
    return Config.from_dict(raw, tmp_path)


# ---------------------------------------------------------------------------
def test_schema_resolves_against_live_parcel_fields():
    spec = load_schema_spec(ROOT / "config/schema/parcels.yaml")
    res = resolve_schema(spec, LIVE_PARCEL_FIELDS)
    assert res.ok, res.report()
    assert res.mapping["owner_zip"] == "OWNERZIP"
    assert res.mapping["county_code"] == "JURSCODE"
    assert res.mapping["polygon_acres"] == "POLYACRES"
    assert res.mapping["sdat_url"] == "SDATWEBADR"
    # The public layer carries no owner name: optional, reported as missing, never fatal.
    assert "owner_name" in res.missing_optional
    assert "owner_name" not in res.mapping


def test_wanted_fields_are_live_spellings(tmp_path):
    cfg = _cfg(tmp_path)
    fields = wanted_fields(cfg, LIVE_PARCEL_FIELDS)
    assert fields[:2] == ["ACCTID", "JURSCODE"]
    assert "OWNERZIP" in fields and "OWNZIP" not in fields
    assert "OBJECTID" in fields and "Shape" not in fields
    assert len(fields) == len(set(fields))


# ---------------------------------------------------------------------------
def _parcels_raw():
    sq = lambda x, y, s=300: Polygon([(x, y), (x + s, y), (x + s, y + s), (x, y + s)])
    return gpd.GeoDataFrame({
        "ACCTID": ["1101000001", "1101000002", "ROW", None, "1101000002", "WATER", "RAILROAD", "GCE", "1102502WH"],
        "JURSCODE": ["FRED"] * 9,
        "OWNADD1": ["1 FARM RD", "PO BOX 9", None, None, "PO BOX 9", None, None, None, "9 RAIL ST"],
        "OWNCITY": ["FREDERICK", "MOUNT AIRY", None, None, "MOUNT AIRY", None, None, None, "BRUNSWICK"],
        "OWNSTATE": ["MD", "MD", None, None, "MD", None, None, None, "MD"],
        "OWNERZIP": ["21701", "21771", None, None, "21771", None, None, None, "21716"],
        "ACRES": [22.2, 44.5, None, None, 44.5, 2600.0, 700.0, 1.0, 50.0],
        "DR1LIBER": ["01234", "09876", None, None, "09876", None, None, None, None],
        "DR1FOLIO": ["0010", "0450", None, None, "0450", None, None, None, None],
        "DESCEXCL": [None, None, None, None, None, None, None, None, "STA Parks"],
    }, geometry=[sq(0, 0), sq(1000, 0), sq(0, 1000, 50), sq(2000, 2000, 10), sq(1400, 0),
                 sq(3000, 0, 400), sq(3000, 500, 400), sq(3000, 1000, 20), sq(4000, 0)], crs="EPSG:26985")


def test_stage1_excludes_row_and_null_accounts_and_dissolves(tmp_path):
    cfg = _cfg(tmp_path)
    gdf = load_parcels(cfg, box(-10, -10, 5000, 5000), parcels_raw=_parcels_raw())
    assert sorted(gdf["account_id"]) == ["1101000001", "1101000002", "1102502WH"]   # letters are fine, digits required
    assert gdf.attrs["non_parcel_polygons_excluded"] == 5                            # ROW, null, WATER, RAILROAD, GCE
    two = gdf.set_index("account_id").loc["1101000002"]
    assert two.geometry.geom_type == "MultiPolygon"          # two rows dissolved into one account


def test_stage1_without_owner_name_degrades_to_address(tmp_path):
    cfg = _cfg(tmp_path)
    gdf = load_parcels(cfg, box(-10, -10, 5000, 5000), parcels_raw=_parcels_raw())
    assert "owner_name" not in gdf.columns or gdf["owner_name"].isna().all()
    out = attribute_owners(gdf).set_index("account_id")
    assert out.loc["1101000002", "owner_type"] == "unknown" and out.loc["1101000002", "owner_type_basis"] == "none"
    assert out.loc["1102502WH", "owner_type"] == "government" and out.loc["1102502WH", "owner_type_basis"] == "exemption_class"
    assert not out["owner_name_available"].any()
    assert out.loc["1101000002", "owner_key"] == "|POBOX 9 MOUNT AIRY MD 21771"
    assert out.loc["1101000002", "deed_ref"] == "9876/450"
    assert out.loc["1101000001", "deed_ref"] == "1234/10"


# ---------------------------------------------------------------------------
ESRI_PAGE = {
    "displayFieldName": "ACCTID", "geometryType": "esriGeometryPolygon",
    "spatialReference": {"wkid": 26985, "latestWkid": 26985},
    "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}, {"name": "ACCTID", "type": "esriFieldTypeString", "length": 16},
               {"name": "ACRES", "type": "esriFieldTypeDouble"}],
    "features": [
        {"attributes": {"OBJECTID": 1, "ACCTID": "1101000001", "ACRES": 41.0},
         "geometry": {"rings": [[[0, 0], [0, 100], [100, 100], [100, 0], [0, 0]]]}},
        {"attributes": {"OBJECTID": 2, "ACCTID": "ROW", "ACRES": None},
         "geometry": {"rings": [[[200, 0], [200, 10], [300, 10], [300, 0], [200, 0]]]}},
    ],
}


def test_esrijson_page_parses_with_crs(tmp_path):
    gdf = esrijson_to_gdf(json.dumps(ESRI_PAGE).encode(), tmp_dir=tmp_path)
    assert len(gdf) == 2 and gdf.crs.to_epsg() == 26985
    assert list(gdf["ACCTID"]) == ["1101000001", "ROW"]
    assert gdf.geometry.iloc[0].area == pytest.approx(10000)


class _Resp:
    def __init__(self, content: bytes):
        self.content = content
    def raise_for_status(self):
        pass
    def json(self):
        return json.loads(self.content)


class _Session:
    """Fake requests.Session returning canned pages keyed by resultOffset."""
    def __init__(self, info: dict, pages: dict[int, bytes]):
        self.info, self.pages, self.calls = info, pages, []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/query"):
            if params.get("returnIdsOnly"):
                return _Resp(json.dumps({"objectIdFieldName": "OBJECTID", "objectIds": self.pages["ids"]}).encode())
            if "resultOffset" in params:
                return _Resp(self.pages[int(params["resultOffset"])])
            # keyset mode: pages keyed by the "OID > n" bound (None for the first page)
            w = params["where"]
            bound = int(w.rsplit(">", 1)[1]) if "OBJECTID >" in w else None
            return _Resp(self.pages[bound])
        return _Resp(json.dumps(self.info).encode())

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, dict(data or {})))
        ids = [int(x) for x in data["objectIds"].split(",")]
        key = ("chunk", ids[0], "gen") if "maxAllowableOffset" in data else ("chunk", ids[0])
        if key not in self.pages and ("chunk", ids[0]) in self.pages:
            key = ("chunk", ids[0])
        if key not in self.pages:
            return _Resp(json.dumps({"error": {"code": 500, "message": "Error performing query operation"}}).encode())
        return _Resp(self.pages[key])


def _page(n_feats: int, exceeded: bool, start: int = 0) -> bytes:
    feats = [{"attributes": {"OBJECTID": start + i, "ACCTID": f"A{start + i}", "ACRES": 50.0},
              "geometry": {"rings": [[[i * 10, 0], [i * 10, 5], [i * 10 + 5, 5], [i * 10 + 5, 0], [i * 10, 0]]]}}
             for i in range(n_feats)]
    d = {**ESRI_PAGE, "features": feats, "exceededTransferLimit": exceeded}
    return json.dumps(d).encode()


def test_fetch_county_pages_offset_mode_honours_exceeded_transfer_limit(tmp_path):
    # no OID field / orderBy -> resultOffset paging
    info = {"name": "Parcel Boundaries", "maxRecordCount": 3, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "ACCTID", "type": "esriFieldTypeString"}], "extent": {"spatialReference": {"wkid": 3857}}}
    # 3 full, then a SHORT page that still says exceededTransferLimit, then the last one
    pages = {0: _page(3, True, 0), 3: _page(2, True, 3), 5: _page(1, False, 5), 6: _page(0, False, 6)}
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    got = list(fetch_county_pages(layer, "JURSCODE='FRED'", ["OBJECTID", "ACCTID", "ACRES"], 26985, tmp_dir=tmp_path))
    assert [len(g) for g in got] == [3, 2, 1]
    offsets = [c[1]["resultOffset"] for c in layer.session.calls if c[0].endswith("/query")]
    assert offsets == [0, 3, 5, 6]                     # a short page is never taken as the end


def test_offset_paging_survives_silent_truncation(tmp_path):
    """A short page WITHOUT exceededTransferLimit followed by more data (what
    the iMAP wetlands MapServer does) must not end the walk."""
    info = {"name": "L", "maxRecordCount": 4, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "ACCTID", "type": "esriFieldTypeString"}], "extent": {}}
    pages = {0: _page(2, False, 0), 2: _page(4, False, 2), 6: _page(1, False, 6), 7: _page(0, False, 7)}
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    gdf = fetch_layer_gdf(layer, tmp_dir=tmp_path, mode="offset")
    assert len(gdf) == 7


def test_fetch_county_pages_keyset_mode(tmp_path):
    info = {"name": "Parcel Boundaries", "maxRecordCount": 3,
            "advancedQueryCapabilities": {"supportsPagination": True, "supportsOrderBy": True},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}, {"name": "ACCTID", "type": "esriFieldTypeString"}],
            "extent": {"spatialReference": {"wkid": 3857}}}
    # OIDs 0,1,2 | 3,4 (short but exceeded) | 5 (last)
    pages = {None: _page(3, True, 0), 2: _page(2, True, 3), 4: _page(1, False, 5), 5: _page(0, False, 6)}
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    got = list(fetch_county_pages(layer, "JURSCODE='FRED'", ["ACCTID", "ACRES"], 26985, tmp_dir=tmp_path))
    assert [len(g) for g in got] == [3, 2, 1]
    q = [c[1] for c in layer.session.calls if c[0].endswith("/query")]
    assert [x["where"] for x in q] == ["JURSCODE='FRED'", "(JURSCODE='FRED') AND OBJECTID > 2",
                                       "(JURSCODE='FRED') AND OBJECTID > 4", "(JURSCODE='FRED') AND OBJECTID > 5"]
    assert all(x["orderByFields"] == "OBJECTID ASC" and "resultOffset" not in x for x in q)
    assert q[0]["outFields"].startswith("OBJECTID,")          # the OID is added so the bound can be read back


def test_keyset_paging_refuses_to_loop(tmp_path):
    info = {"name": "L", "maxRecordCount": 3,
            "advancedQueryCapabilities": {"supportsPagination": True, "supportsOrderBy": True},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}], "extent": {}}
    pages = {None: _page(3, True, 0), 2: _page(3, True, 0)}     # server ignores the bound and repeats
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    with pytest.raises(Exception, match="did not advance"):
        list(layer.iter_pages(mode="keyset"))


def test_iter_features_geojson_exceeded_transfer_limit():
    info = {"name": "L", "maxRecordCount": 2, "advancedQueryCapabilities": {"supportsPagination": True}, "fields": [], "extent": {}}
    def fc(n, exceeded, start):
        return json.dumps({"type": "FeatureCollection", "properties": {"exceededTransferLimit": exceeded},
                           "features": [{"type": "Feature", "properties": {"id": start + i},
                                         "geometry": {"type": "Point", "coordinates": [0, 0]}} for i in range(n)]}).encode()
    pages = {0: fc(1, True, 0), 1: fc(2, False, 1), 3: fc(0, False, 3)}
    layer = ArcGISLayer("https://example.invalid/FeatureServer/0", session=_Session(info, pages))
    feats = list(layer.iter_features())
    assert [f["properties"]["id"] for f in feats] == [0, 1, 2]
    # the explicit f=geojson must survive _get's default
    assert all(c[1].get("f") == "geojson" for c in layer.session.calls if c[0].endswith("/query"))


def test_fetch_layer_gdf_pages_esrijson(tmp_path):
    info = {"name": "L", "maxRecordCount": 3, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "ACCTID", "type": "esriFieldTypeString"}], "extent": {"spatialReference": {"wkid": 3857}}}
    pages = {0: _page(3, True, 0), 3: _page(3, False, 3), 6: _page(0, False, 6)}
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    gdf = fetch_layer_gdf(layer, bbox_4326=(-77.9, 39.2, -76.9, 39.8), tmp_dir=tmp_path, mode="offset")
    assert len(gdf) == 6 and gdf.crs.to_epsg() == 26985
    q = [c[1] for c in layer.session.calls if c[0].endswith("/query")]
    assert [x["resultOffset"] for x in q] == [0, 3, 6] and all(x["f"] == "json" for x in q)
    assert q[0]["geometryType"] == "esriGeometryEnvelope"


# ---------------------------------------------------------------------------
def _synthetic_tiff(bounds, cell, nodata=-9999.0) -> bytes:
    minx, miny, maxx, maxy = bounds
    w, h = int((maxx - minx) / cell), int((maxy - miny) / cell)
    xs = np.arange(w) * cell
    # flat on the west half, 40% grade rising eastward on the east half
    z = np.where(xs < (maxx - minx) / 2, 100.0, 100.0 + 0.4 * (xs - (maxx - minx) / 2))
    arr = np.tile(z, (h, 1)).astype("float32")
    arr[0, 0] = nodata
    transform = from_origin(minx, maxy, cell, cell)
    with MemoryFile() as mf:
        with mf.open(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs="EPSG:26985",
                     transform=transform, nodata=nodata) as ds:
            ds.write(arr, 1)
        return mf.read()


def test_imageserver_slope_reader(monkeypatch, tmp_path):
    dem = ImageServerDEM("https://example.invalid/ImageServer", cache_dir=tmp_path / "cache")
    calls = []
    def fake_export(bounds, epsg, cell_m):
        calls.append((bounds, epsg, cell_m))
        return _synthetic_tiff(bounds, cell_m)
    monkeypatch.setattr(dem, "export", fake_export)
    parcel = box(1000, 1000, 1400, 1200)          # 400 m x 200 m
    steep = steep_polygons_from_imageserver(dem, parcel, "EPSG:26985", slope_max_pct=15, resample_m=5.0, margin_m=30)
    assert calls and calls[0][1] == 26985 and calls[0][2] == 5.0
    assert steep is not None
    # steep ground is the eastern half (x > 1230 or so, given the margin shifts the midpoint)
    assert steep.bounds[0] > 1150 and steep.bounds[2] == pytest.approx(1400, abs=1)
    assert steep.area == pytest.approx(parcel.area * 0.5, rel=0.15)


def test_imageserver_export_caches_windows(tmp_path, monkeypatch):
    dem = ImageServerDEM("https://example.invalid/ImageServer", cache_dir=tmp_path / "cache")
    payload = _synthetic_tiff((0, 0, 100, 100), 5.0)
    class S:
        n = 0
        def get(self, url, params=None, timeout=None):
            S.n += 1
            class R:
                content = payload
                headers = {"content-type": "image/tiff"}
                def raise_for_status(self): pass
            return R()
    dem.session = S()
    a = dem.export((0, 0, 100, 100), 26985, 5.0)
    b = dem.export((0, 0, 100, 100), 26985, 5.0)
    assert a == b == payload and S.n == 1
    assert len(list((tmp_path / "cache").glob("*.tif"))) == 1


# ---------------------------------------------------------------------------
def test_build_study_area_clips_parts(tmp_path, monkeypatch):
    from farmsearch.io import study_area as sa
    counties = gpd.GeoDataFrame({"COUNTY": ["Frederick", "Carroll"]},
                                geometry=[box(-77.7, 39.2, -77.2, 39.7), box(-77.2, 39.2, -76.8, 39.7)], crs="EPSG:4326")
    monkeypatch.setattr(sa, "fetch_county_polygons", lambda url, field, names, session=None: counties)
    cfg = _cfg(tmp_path, study_area_build={
        "boundaries_url": "https://example.invalid/MapServer/0", "county_field": "COUNTY",
        "variants": {"initial": [{"county": "Frederick"}, {"county": "Carroll", "clip_bbox": [-77.2, 39.3, -77.0, 39.5]}]}})
    out = sa.build_study_area(cfg, "initial")
    g = gpd.read_file(out)
    assert g.crs.to_epsg() == 4326
    geom = g.geometry.iloc[0]
    assert geom.bounds == pytest.approx((-77.7, 39.2, -77.0, 39.7))
    assert geom.area == pytest.approx(0.5 * 0.5 + 0.2 * 0.2)


def test_geometry_collections_are_coerced_to_layer_kind():
    from shapely.geometry import GeometryCollection, LineString, Point
    from farmsearch.io.loaders import clean_geometries, coerce_geometry_kind
    poly = box(0, 0, 10, 10)
    gc = GeometryCollection([box(20, 20, 30, 30), LineString([(0, 0), (1, 1)]), Point(5, 5)])
    gdf = gpd.GeoDataFrame({"id": [1, 2, 3]}, geometry=[poly, gc, Point(50, 50)], crs="EPSG:26985")
    out = clean_geometries(gdf)
    assert list(out["id"]) == [1, 2]                      # the bare point has no areal part -> dropped
    assert set(out.geometry.geom_type) == {"Polygon"}
    assert out.geometry.iloc[1].area == pytest.approx(100)
    lines = gpd.GeoDataFrame({"id": [1]}, geometry=[GeometryCollection([LineString([(0, 0), (5, 0)]), box(0, 0, 1, 1)])], crs="EPSG:26985")
    out = coerce_geometry_kind(lines, "lineal")
    assert out.geometry.iloc[0].geom_type == "LineString" and out.geometry.iloc[0].length == pytest.approx(5)


def test_persistent_service_error_raises_not_empty(tmp_path, monkeypatch):
    """A page that keeps coming back as {"error": ...} must raise after the
    retries, never be treated as an empty (final) page."""
    import farmsearch.io.arcgis as arcgis
    monkeypatch.setattr(arcgis.time, "sleep", lambda s: None)
    info = {"name": "L", "maxRecordCount": 3, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "ACCTID", "type": "esriFieldTypeString"}], "extent": {}}
    err = json.dumps({"error": {"code": 500, "message": "Error performing query operation", "details": []}}).encode()
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, {0: err}))
    with pytest.raises(Exception, match="giving up"):
        list(layer.iter_pages(max_retries=2, mode="offset"))


def test_ids_mode_fetches_every_id_in_chunks_and_verifies(tmp_path):
    info = {"name": "L", "maxRecordCount": 1000, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}], "extent": {}}
    ids = [5, 9, 12, 20, 21]
    pages = {"ids": ids, ("chunk", 5): _page(2, False, 5), ("chunk", 12): _page(2, False, 12), ("chunk", 21): _page(1, False, 21)}
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    gdf = fetch_layer_gdf(layer, bbox_4326=(-77.9, 39.2, -76.9, 39.8), tmp_dir=tmp_path, page_size=2)
    assert len(gdf) == 5
    posts = [c[1] for c in layer.session.calls if "objectIds" in c[1]]
    assert [p["objectIds"] for p in posts] == ["5,9", "12,20", "21"]
    idq = [c[1] for c in layer.session.calls if c[1].get("returnIdsOnly")]
    assert idq and idq[0]["geometryType"] == "esriGeometryEnvelope"


def test_ids_mode_splits_failing_chunks_and_records_unservable(tmp_path, monkeypatch):
    """Chunk [1,2,3,4] fails -> [1,2] ok, [3,4] fails -> [3] ok, [4] fails, generalized [4] fails -> missing."""
    import farmsearch.io.arcgis as arcgis
    monkeypatch.setattr(arcgis.time, "sleep", lambda s: None)
    info = {"name": "L", "maxRecordCount": 1000, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}], "extent": {}}
    pages = {"ids": [1, 2, 3, 4], ("chunk", 1): _page(2, False, 1), ("chunk", 3): _page(1, False, 3)}
    # ("chunk", 1) serves [1,2] only when asked for 2 ids: make the 4-id request fail by returning a short page
    class S(_Session):
        def post(self, url, data=None, timeout=None):
            ids = [int(x) for x in data["objectIds"].split(",")]
            if len(ids) == 4:
                return _Resp(_page(2, False, 1))       # short -> treated as failure
            return super().post(url, data, timeout)
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=S(info, pages))
    gdf = fetch_layer_gdf(layer, tmp_dir=tmp_path, page_size=4)
    assert len(gdf) == 3 and layer.missing_ids == [4]


def test_ids_mode_generalized_fallback_for_a_stubborn_feature(tmp_path, monkeypatch):
    import farmsearch.io.arcgis as arcgis
    monkeypatch.setattr(arcgis.time, "sleep", lambda s: None)
    info = {"name": "L", "maxRecordCount": 1000, "advancedQueryCapabilities": {"supportsPagination": True},
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}], "extent": {}}
    pages = {"ids": [7], ("chunk", 7, "gen"): _page(1, False, 7)}   # plain request errors, generalized works
    layer = ArcGISLayer("https://example.invalid/MapServer/0", session=_Session(info, pages))
    gdf = fetch_layer_gdf(layer, tmp_dir=tmp_path, page_size=10)
    assert len(gdf) == 1 and layer.missing_ids == []
    gen = [c[1] for c in layer.session.calls if "maxAllowableOffset" in c[1]]
    assert gen and gen[0]["maxAllowableOffset"] == 0.00001


def test_null_zoning_code_polygons_are_ignored(tmp_path):
    """A zoning polygon with a null code must not abort Stage 1 as 'unmapped'."""
    from farmsearch.stages.stage1_base_filter import assign_zoning
    (tmp_path / "z.yaml").write_text("code_field: TYPE\ncodes:\n  A: {description: Agricultural, is_agricultural: true}\n")
    cfg = _cfg(tmp_path, zoning=[{"county": "Frederick", "path": str(tmp_path / "z.gpkg"), "code_field": "TYPE",
                                  "mapping": str(tmp_path / "z.yaml")}], on_unmapped_zoning="error")
    parcels = gpd.GeoDataFrame({"account_id": ["p1"], "county": ["Frederick"]}, geometry=[box(0, 0, 100, 100)], crs="EPSG:26985")
    layer = gpd.GeoDataFrame({"TYPE": ["A", None]}, geometry=[box(0, 0, 100, 100), box(10, 10, 12, 12)], crs="EPSG:26985")
    out = assign_zoning(parcels, {"Frederick": layer}, cfg)
    assert out.loc[0, "zoning"] == "A" and out.loc[0, "is_agricultural"] is True and not out.loc[0, "zoning_unmapped"]


def test_non_account_mask_and_exemption_typing():
    from farmsearch.accounts import non_account_mask, owner_type_from_exemption
    import pandas as pd
    acct = pd.Series(["1101000098", "1102502WH", "ROW", "WATER_CANAL", "PRIVATE ROW", "RAILROAD", "UNK", None, "", "12345678", "1234567"])
    m = non_account_mask(acct, ["ROW"])
    assert list(m) == [False, False, True, True, True, True, True, True, True, False, True]
    assert list(non_account_mask(pd.Series(["FRED-A", "ROW", None]), ["ROW"], regex=None)) == [False, True, True]
    assert owner_type_from_exemption("STA Parks") == "government"
    assert owner_type_from_exemption("JUR Schools (Public, including Junior College)") == "government"
    assert owner_type_from_exemption("MUN Public Works Properties") == "government"
    assert owner_type_from_exemption("PUB Military Installations") == "government"
    assert owner_type_from_exemption("NPF Private Schools") == "religious_nonprofit"
    assert owner_type_from_exemption("PVT Churches, Synagogues, & Parsonages") == "religious_nonprofit"
    assert owner_type_from_exemption("PVT Other") is None
    assert owner_type_from_exemption("OTH Conservation Tax Credit") is None
    assert owner_type_from_exemption(None) is None and owner_type_from_exemption("nan") is None
