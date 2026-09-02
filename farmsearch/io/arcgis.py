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
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

log = logging.getLogger(__name__)


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
    oid_field: Optional[str] = None
    supports_order_by: bool = False

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
        params = {"f": "json", **params}          # an explicit f= (geojson) wins
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
            fields = d.get("fields", []) or []
            oid = d.get("objectIdField") or next((f["name"] for f in fields if f.get("type") == "esriFieldTypeOID"), None)
            self._info = LayerInfo(
                url=self.url,
                name=d.get("name", ""),
                geometry_type=d.get("geometryType"),
                max_record_count=int(d.get("maxRecordCount", 1000)),
                supports_pagination=bool(aqc.get("supportsPagination", False)),
                fields=fields,
                srid=sr.get("latestWkid") or sr.get("wkid"),
                oid_field=oid,
                supports_order_by=bool(aqc.get("supportsOrderBy", False)),
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
                # A service may return FEWER than `page` features and still
                # have more (response-size limit): exceededTransferLimit says so.
                exceeded = bool(d.get("exceededTransferLimit") or (d.get("properties") or {}).get("exceededTransferLimit"))
                if not feats or (len(feats) < page and not exceeded):
                    break
                offset += len(feats)
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
        service publishes no coded-value domain). NB: on the iMAP MapServers
        returnDistinctValues ignores any spatial filter."""
        d = self._get(f"{self.url}/query", {"where": where, "outFields": field, "returnGeometry": "false",
                                            "returnDistinctValues": "true"})
        vals = [f.get("attributes", {}).get(field) for f in d.get("features", [])]
        return sorted({v for v in vals if v is not None}, key=str)

    # ------------------------------------------------------------------
    def object_ids(self, where: str = "1=1", bbox_4326: Optional[tuple[float, float, float, float]] = None) -> list[int]:
        """All object ids matching the query (returnIdsOnly). Not subject to
        maxRecordCount, and — unlike returnCountOnly / returnDistinctValues on
        the iMAP MapServers — it does honour the spatial filter."""
        params = {"where": where, "returnIdsOnly": "true"}
        if bbox_4326:
            xmin, ymin, xmax, ymax = bbox_4326
            params.update({"geometry": f"{xmin},{ymin},{xmax},{ymax}", "geometryType": "esriGeometryEnvelope",
                           "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
        d = self._get(f"{self.url}/query", params)
        return sorted(int(x) for x in (d.get("objectIds") or []))

    def iter_pages(self, where: str = "1=1", bbox_4326: Optional[tuple[float, float, float, float]] = None,
                   out_fields: str = "*", out_sr: int = 4326, page_size: Optional[int] = None,
                   max_retries: int = 4, progress_every: int = 10, keyset: Optional[bool] = None,
                   mode: Optional[str] = None) -> Iterator[bytes]:
        """Yield raw ESRI JSON (f=json) FeatureSet pages.

        mode:
          "ids"     fetch the matching object ids first (returnIdsOnly), then
                    the features in chunks of `page_size` by objectIds (POST).
                    Immune to offset/transfer-limit quirks, verifiable (the
                    number of features must equal the number of ids), and the
                    only thing some heavy-polygon MapServers can serve at all.
                    Default for layer downloads.
          "keyset"  WHERE ... AND OID > last ORDER BY OID. Same cost for
                    every page; used for the parcel pull (100k+ rows).
          "offset"  resultOffset paging; fallback when neither is possible.
        Stops only on an empty page: servers truncate pages to a size limit
        without setting exceededTransferLimit.
        ESRI JSON is what every ArcGIS server produces reliably (GeoJSON
        output is optional and, on some MapServers, breaks on large
        geometries); GDAL reads it natively."""
        info = self.info()
        page = min(page_size or info.max_record_count, info.max_record_count)
        if mode is None:
            mode = "keyset" if keyset else ("offset" if keyset is False else "ids")
        if mode == "ids":
            yield from self._iter_pages_by_ids(where, bbox_4326, out_fields, out_sr, page, max_retries, progress_every)
            return
        use_keyset = mode == "keyset" and bool(info.oid_field and info.supports_order_by)
        if not use_keyset and not info.supports_pagination:
            raise ArcGISError(f"{self.url}: service supports neither orderBy nor pagination")
        oid = info.oid_field
        if use_keyset and out_fields != "*" and oid and oid.upper() not in {f.strip().upper() for f in out_fields.split(",")}:
            out_fields = f"{oid},{out_fields}"
        params = {"where": where, "outFields": out_fields, "returnGeometry": "true", "outSR": out_sr}
        if bbox_4326:
            xmin, ymin, xmax, ymax = bbox_4326
            params.update({"geometry": f"{xmin},{ymin},{xmax},{ymax}", "geometryType": "esriGeometryEnvelope",
                           "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
        qurl = f"{self.url}/query"
        offset, n_pages, t0 = 0, 0, time.time()
        last_oid: Optional[int] = None
        while True:
            if use_keyset:
                w = where if last_oid is None else f"({where}) AND {oid} > {last_oid}"
                q = {**params, "where": w, "orderByFields": f"{oid} ASC", "resultRecordCount": page, "f": "json"}
            else:
                q = {**params, "resultOffset": offset, "resultRecordCount": page, "f": "json"}
            payload = None
            last_err: Optional[Exception] = None
            for attempt in range(max_retries):
                try:
                    r = self.session.get(qurl, params=q, timeout=self.timeout)
                    r.raise_for_status()
                    content = r.content
                    if b'"error"' in content[:300]:
                        raise ArcGISError(f"service error: {content[:300]!r}")
                    payload = content          # only a validated body becomes the page
                    break
                except (requests.RequestException, ArcGISError) as e:
                    last_err = e
                    wait = 2 ** attempt
                    log.warning("%s: page at offset %d failed (%s); retry in %ds", self.url, offset, e, wait)
                    time.sleep(wait)
            if payload is None:
                raise ArcGISError(f"{self.url}: giving up at offset {offset} after {max_retries} attempts: {last_err}")
            n, max_oid, exceeded = inspect_page(payload, oid if use_keyset else None)
            n_pages += 1
            if n_pages % progress_every == 0:
                log.info("  %s: %d pages, %d features so far (%.0fs)", info.name, n_pages, offset + n, time.time() - t0)
            if n:
                yield payload
            # Stop ONLY on an empty page. A short page is not proof of the end:
            # servers truncate to a response-size limit and do not always set
            # exceededTransferLimit (observed on the iMAP NWI layer: 831 of
            # 2000 with no flag, 70k features still to come). One extra
            # request per layer buys immunity to silent truncation.
            if n == 0:
                break
            if n < page and not exceeded:
                log.info("  %s: short page (%d < %d) without exceededTransferLimit; continuing", info.name, n, page)
            offset += n
            if use_keyset:
                if max_oid is None or (last_oid is not None and max_oid <= last_oid):
                    raise ArcGISError(f"{self.url}: keyset paging did not advance (last {last_oid}, page max {max_oid})")
                last_oid = max_oid


    def _iter_pages_by_ids(self, where, bbox_4326, out_fields, out_sr, page, max_retries, progress_every) -> Iterator[bytes]:
        info = self.info()
        ids = self.object_ids(where, bbox_4326)
        log.info("  %s: %d matching ids; fetching in chunks of %d", info.name, len(ids), page)
        self.missing_ids = []
        n_total, t0 = 0, time.time()
        for k, i in enumerate(range(0, len(ids), page), 1):
            for payload, n in self._fetch_ids(ids[i:i + page], out_fields, out_sr, max_retries):
                n_total += n
                yield payload
            if k % progress_every == 0:
                log.info("  %s: %d/%d features (%.0fs)", info.name, n_total, len(ids), time.time() - t0)
        if self.missing_ids:
            log.warning("%s: %d of %d features could not be served by the service even one at a time "
                        "(first ids: %s); the layer is cached WITHOUT them", info.name, len(self.missing_ids),
                        len(ids), self.missing_ids[:10])
        if n_total + len(self.missing_ids) != len(ids):
            raise ArcGISError(f"{self.url}: fetched {n_total} features (+{len(self.missing_ids)} unservable) for {len(ids)} ids")

    def _post_chunk(self, chunk: list[int], out_fields: str, out_sr: int, max_retries: int,
                    generalize: bool = False) -> Optional[bytes]:
        """One objectIds request with retries; None when the service keeps failing."""
        data = {"objectIds": ",".join(map(str, chunk)), "outFields": out_fields, "returnGeometry": "true",
                "outSR": out_sr, "f": "json"}
        if generalize:
            # ~1 m in degrees / meters: a generalized outline is far better than no feature
            data["maxAllowableOffset"] = 0.00001 if out_sr == 4326 else 1
        for attempt in range(max_retries):
            try:
                r = self.session.post(f"{self.url}/query", data=data, timeout=self.timeout)
                r.raise_for_status()
                content = r.content
                if b'"error"' in content[:300]:
                    raise ArcGISError(f"service error: {content[:300]!r}")
                n, _, _ = inspect_page(content)
                if n != len(chunk):
                    raise ArcGISError(f"chunk returned {n} features for {len(chunk)} ids")
                return content
            except (requests.RequestException, ArcGISError) as e:
                wait = 2 ** attempt
                log.warning("%s: chunk of %d ids%s failed (%s); retry in %ds", self.url, len(chunk),
                            " (generalized)" if generalize else "", e, wait)
                time.sleep(wait)
        return None

    def _fetch_ids(self, chunk: list[int], out_fields: str, out_sr: int, max_retries: int):
        """Yield (payload, n) for a chunk; on persistent failure split the
        chunk in half, down to single features, then try a generalized
        geometry, then give the feature up (recorded in self.missing_ids)."""
        payload = self._post_chunk(chunk, out_fields, out_sr, max_retries)
        if payload is not None:
            yield payload, len(chunk)
            return
        if len(chunk) > 1:
            mid = len(chunk) // 2
            log.warning("%s: splitting a failing chunk of %d ids", self.url, len(chunk))
            yield from self._fetch_ids(chunk[:mid], out_fields, out_sr, max_retries)
            yield from self._fetch_ids(chunk[mid:], out_fields, out_sr, max_retries)
            return
        payload = self._post_chunk(chunk, out_fields, out_sr, max_retries=2, generalize=True)
        if payload is not None:
            log.warning("%s: feature %s served only as a generalized geometry", self.url, chunk[0])
            yield payload, 1
            return
        self.missing_ids.append(chunk[0])


def inspect_page(payload: bytes, oid_field: Optional[str] = None) -> tuple[int, Optional[int], bool]:
    """(feature count, max object id, exceededTransferLimit) of an ESRI JSON
    FeatureSet. One json.loads per page; pages can be a few MB."""
    try:
        d = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ArcGISError(f"non-JSON page: {payload[:200]!r}") from e
    feats = d.get("features", [])
    max_oid = None
    if oid_field and feats:
        vals = [f.get("attributes", {}).get(oid_field) for f in feats]
        vals = [v for v in vals if v is not None]
        if not vals:
            # the service may spell the OID field differently in attributes
            key = next((k for k in feats[0].get("attributes", {}) if k.upper() == oid_field.upper()), None)
            vals = [f["attributes"][key] for f in feats if key and f["attributes"].get(key) is not None]
        max_oid = max(vals) if vals else None
    return len(feats), max_oid, bool(d.get("exceededTransferLimit"))


def count_features(payload: bytes) -> int:
    return inspect_page(payload)[0]


def esrijson_to_gdf(payload: bytes, tmp_dir: Optional[Path] = None):
    """Parse an ESRI JSON FeatureSet (f=json) into a GeoDataFrame via GDAL's
    ESRIJSON driver (pyogrio needs a file path for this driver). The file is
    closed before GDAL opens it (re-opening an open temp file fails on Windows)."""
    import os
    import pyogrio
    fd, name = tempfile.mkstemp(suffix=".json", dir=str(tmp_dir) if tmp_dir else None)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        return pyogrio.read_dataframe(name)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def fetch_layer_gdf(layer: "ArcGISLayer", where: str = "1=1", bbox_4326=None, out_fields: str = "*",
                    out_sr: int = 4326, page_size: Optional[int] = None, tmp_dir: Optional[Path] = None,
                    mode: str = "ids"):
    """All pages of a layer as one GeoDataFrame in EPSG:<out_sr> (empty
    GeoDataFrame when nothing intersects)."""
    import geopandas as gpd
    import pandas as pd
    parts = [esrijson_to_gdf(pg, tmp_dir) for pg in layer.iter_pages(where=where, bbox_4326=bbox_4326,
                                                                      out_fields=out_fields, out_sr=out_sr,
                                                                      page_size=page_size, mode=mode)]
    parts = [g for g in parts if len(g)]
    if not parts:
        # Empty result: keep the service's attribute columns so a `where`
        # on the cached file still evaluates (to nothing) instead of failing.
        cols = [f["name"] for f in layer.info().fields if f.get("type") not in ("esriFieldTypeGeometry",)
                and "(" not in f["name"]]
        return gpd.GeoDataFrame({c: pd.Series([], dtype="object") for c in cols}, geometry=[], crs=f"EPSG:{out_sr}")
    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry=parts[0].geometry.name, crs=parts[0].crs)
    return gdf
