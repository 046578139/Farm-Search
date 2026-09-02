"""Minimal ArcGIS REST (MapServer / FeatureServer layer) client.

Used for layers small enough to page through (zoning, ROW, protected lands
clipped to the study-area bbox). NOT for the statewide parcel layer — its
MaxRecordCount of 1000 against ~2.29M records is why the spec says to use the
bulk download.

Everything here is driven by what the service reports about itself
(maxRecordCount, pagination support, field list, coded-value domains); nothing
about a layer is assumed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import requests


class ArcGISError(RuntimeError):
    pass


@dataclass
class LayerInfo:
    url: str
    name: str
    geometry_type: Optional[str]
    max_record_count: int
    supports_pagination: bool
    fields: list[dict]
    srid: Optional[int]

    def field_names(self) -> list[str]:
        return [f["name"] for f in self.fields]

    def domains(self) -> dict[str, dict[str, str]]:
        """field name -> {code: description} for coded-value domains."""
        out: dict[str, dict[str, str]] = {}
        for f in self.fields:
            d = f.get("domain")
            if d and d.get("type") == "codedValue":
                out[f["name"]] = {str(cv["code"]): str(cv.get("name", "")) for cv in d.get("codedValues", [])}
        return out


class ArcGISLayer:
    def __init__(self, url: str, session: Optional[requests.Session] = None, timeout: int = 120):
        self.url = url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self._info: Optional[LayerInfo] = None

    # ------------------------------------------------------------------
    def _get(self, url: str, params: dict) -> dict:
        params = {**params, "f": "json"}
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise ArcGISError(f"non-JSON response from {url}: {r.text[:200]}") from e
        if isinstance(data, dict) and "error" in data:
            raise ArcGISError(f"{url}: {data['error']}")
        return data

    def info(self) -> LayerInfo:
        if self._info is None:
            d = self._get(self.url, {})
            aqc = d.get("advancedQueryCapabilities", {}) or {}
            ext = d.get("extent") or {}
            sr = ext.get("spatialReference") or {}
            self._info = LayerInfo(
                url=self.url,
                name=d.get("name", ""),
                geometry_type=d.get("geometryType"),
                max_record_count=int(d.get("maxRecordCount", 1000)),
                supports_pagination=bool(aqc.get("supportsPagination", False)),
                fields=d.get("fields", []) or [],
                srid=sr.get("latestWkid") or sr.get("wkid"),
            )
        return self._info

    # ------------------------------------------------------------------
    def iter_features(self, where: str = "1=1", bbox_4326: Optional[tuple[float, float, float, float]] = None,
                      out_fields: str = "*", out_sr: int = 4326, page_size: Optional[int] = None) -> Iterator[dict]:
        """Yield GeoJSON features, paging by resultOffset when the service
        supports it, otherwise by walking OBJECTID ranges."""
        info = self.info()
        page = min(page_size or info.max_record_count, info.max_record_count)
        base = {"where": where, "outFields": out_fields, "outSR": out_sr, "returnGeometry": "true"}
        if bbox_4326:
            xmin, ymin, xmax, ymax = bbox_4326
            base.update({"geometry": f"{xmin},{ymin},{xmax},{ymax}", "geometryType": "esriGeometryEnvelope",
                         "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
        qurl = f"{self.url}/query"
        if info.supports_pagination:
            offset = 0
            while True:
                d = self._get(qurl, {**base, "resultOffset": offset, "resultRecordCount": page, "f": "geojson"})
                feats = d.get("features", [])
                yield from feats
                if len(feats) < page:
                    break
                offset += page
        else:
            ids = self._get(qurl, {**base, "returnIdsOnly": "true"})
            oid_field = ids.get("objectIdFieldName", "OBJECTID")
            oids = sorted(ids.get("objectIds") or [])
            for i in range(0, len(oids), page):
                chunk = oids[i:i + page]
                d = self._get(qurl, {**base, "objectIds": ",".join(map(str, chunk)), "f": "geojson"})
                yield from d.get("features", [])

    def fetch_geojson(self, **kw) -> dict:
        return {"type": "FeatureCollection", "features": list(self.iter_features(**kw))}

    def distinct_values(self, field: str, where: str = "1=1") -> list[Any]:
        """Distinct attribute values (for zoning-domain discovery when the
        service publishes no coded-value domain)."""
        d = self._get(f"{self.url}/query", {"where": where, "outFields": field, "returnGeometry": "false",
                                            "returnDistinctValues": "true"})
        vals = [f.get("attributes", {}).get(field) for f in d.get("features", [])]
        return sorted({v for v in vals if v is not None}, key=str)
