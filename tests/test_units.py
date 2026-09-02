import json

import pytest
from shapely.geometry import box

from farmsearch.geometry.strips import is_strip, strip_metrics
from farmsearch.io.arcgis import ArcGISLayer
from farmsearch.owners import classify_owner, owner_key, owners_match


def test_strip_metrics():
    m = strip_metrics(box(0, 0, 15.24, 396.24))   # 50 ft x 1,300 ft
    assert m["est_width_ft"] == pytest.approx(48.2, abs=0.5)
    assert m["aspect"] > 25
    assert is_strip(m, max_width_ft=100, min_aspect=6)
    sq = strip_metrics(box(0, 0, 400, 400))
    assert not is_strip(sq, 100, 6)


def test_owner_classification_and_matching():
    assert classify_owner("SMITH JOHN & MARY") == "individual"
    assert classify_owner("SMITH FAMILY REV TRUST") == "trust"
    assert classify_owner("SMITH FARMS L.L.C.") == "llc"
    assert classify_owner("FREDERICK COUNTY COMMISSIONERS") == "government"
    assert owners_match("SMITH JOHN A & MARY B", "SMITH JOHN A ET AL")
    assert not owners_match("SMITH JOHN", "JONES ROBERT")
    assert owners_match("X LLC", "Y LLC", "123 Main Road, Mt Airy MD 21771", "123 MAIN RD MT AIRY MD 21771-1234")
    assert owner_key("Smith, John", "1 Main St") == owner_key("SMITH JOHN", "1 MAIN STREET")


class _Resp:
    def __init__(self, data):
        self._d = data
        self.text = json.dumps(data)

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Session:
    """Fake ArcGIS service: 2,500 features, maxRecordCount 1000, pagination supported."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.endswith("/query"):
            off = int(params.get("resultOffset", 0))
            n = int(params.get("resultRecordCount", 1000))
            feats = [{"type": "Feature", "properties": {"OBJECTID": i}, "geometry": None} for i in range(off, min(off + n, 2500))]
            return _Resp({"type": "FeatureCollection", "features": feats})
        return _Resp({"name": "Fake", "maxRecordCount": 1000, "geometryType": "esriGeometryPolygon",
                      "advancedQueryCapabilities": {"supportsPagination": True},
                      "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"},
                                 {"name": "ZONE", "type": "esriFieldTypeString",
                                  "domain": {"type": "codedValue", "codedValues": [{"code": "A", "name": "Agricultural"}]}}],
                      "extent": {"spatialReference": {"wkid": 26985}}})


def test_arcgis_client_pages_by_max_record_count():
    s = _Session()
    layer = ArcGISLayer("https://example.invalid/rest/services/X/MapServer/0", session=s)
    info = layer.info()
    assert info.max_record_count == 1000 and info.supports_pagination
    assert info.domains() == {"ZONE": {"A": "Agricultural"}}
    feats = list(layer.iter_features(bbox_4326=(-77.5, 39.3, -77.2, 39.6)))
    assert len(feats) == 2500
    queries = [p for u, p in s.calls if u.endswith("/query")]
    assert len(queries) == 3 and queries[0]["geometryType"] == "esriGeometryEnvelope"
