"""Pull the Maryland parcel layer per county from the iMAP REST service.

The statewide Parcel Boundaries layer (2.29M records, maxRecordCount 1000)
supports resultOffset pagination and returns a 1000-record page in about a
second when `outFields` is restricted to the fields the pipeline uses and the
geometry is requested as ESRI JSON in the working CRS. A county is therefore
a few minutes of paging rather than a bulk-license download, and the field
schema is exactly the one `farmsearch verify-schema` checks against.

Two outputs per county:
  <parcels_dir>/parcels_<CODE>.gpkg       every row except road right-of-way
  <row_dir>/parcel_row_<CODE>.gpkg        rows whose account ID is one of
                                          parcels.row_account_ids ('ROW',
                                          'ROW_ALLEY'): tax-map road right-of-
                                          way slivers, usable as a ROW layer
Pages are parsed with GDAL's ESRIJSON driver (via pyogrio) from a temp file.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import pandas as pd
import requests

from ..config import Config
from .arcgis import ArcGISError, ArcGISLayer, esrijson_to_gdf
from .schema import load_schema_spec, resolve_schema

log = logging.getLogger(__name__)


def _epsg(crs: str) -> int:
    import pyproj
    return int(pyproj.CRS.from_user_input(crs).to_epsg())


def wanted_fields(cfg: Config, live_fields: Iterable[str], extra: Iterable[str] = ("OBJECTID",)) -> list[str]:
    """Every schema candidate that exists in the live layer (case-insensitive),
    in schema order, plus `extra`. Field names as the service spells them."""
    spec = load_schema_spec(cfg.parcels.schema_path)
    live = list(live_fields)
    upper = {f.upper(): f for f in live}
    out: list[str] = []
    for group in ("required", "optional"):
        for cands in spec.get(group, {}).values():
            for c in cands:
                hit = upper.get(str(c).upper())
                if hit and hit not in out:
                    out.append(hit)
    for e in extra:
        hit = upper.get(e.upper())
        if hit and hit not in out:
            out.append(hit)
    return out


def _split_row(gdf: gpd.GeoDataFrame, account_field: str, row_ids: Iterable[str]):
    """Separate road right-of-way placeholder rows (parcels_row layer) from
    everything else. Other placeholders (WATER, RAILROAD, UNK, ...) stay in
    the parcel file and are excluded by Stage 1's non_account_mask."""
    acct = gdf[account_field]
    is_row = acct.astype("string").fillna("").str.strip().str.upper().isin({str(x).upper() for x in row_ids})
    return gdf[~is_row].reset_index(drop=True), gdf[is_row].reset_index(drop=True)


def fetch_county_pages(layer: ArcGISLayer, where: str, out_fields: list[str], out_epsg: int,
                       bbox_4326: Optional[tuple[float, float, float, float]] = None,
                       page_size: Optional[int] = None, tmp_dir: Optional[Path] = None,
                       max_retries: int = 4, progress_every: int = 10):
    """Yield GeoDataFrames, one per page, walking resultOffset."""
    for payload in layer.iter_pages(where=where, bbox_4326=bbox_4326, out_fields=",".join(out_fields),
                                    out_sr=out_epsg, page_size=page_size, max_retries=max_retries,
                                    progress_every=progress_every, mode="keyset"):
        gdf = esrijson_to_gdf(payload, tmp_dir)
        if len(gdf):
            yield gdf


def fetch_parcels(cfg: Config, county_codes: Optional[Iterable[str]] = None, parcels_dir: Optional[Path] = None,
                  row_dir: Optional[Path] = None, bbox_4326: Optional[tuple[float, float, float, float]] = None,
                  force: bool = False, session: Optional[requests.Session] = None,
                  page_size: Optional[int] = None) -> dict:
    """Download every configured county (cfg.counties) from cfg.parcels.url."""
    if not cfg.parcels.url:
        raise ArcGISError("parcels.url is not set in the config")
    parcels_dir = Path(parcels_dir or cfg.parcels.source.path)
    row_dir = Path(row_dir) if row_dir else parcels_dir.parent / (parcels_dir.name + "_row")
    parcels_dir.mkdir(parents=True, exist_ok=True)
    row_dir.mkdir(parents=True, exist_ok=True)
    layer = ArcGISLayer(cfg.parcels.url, session=session)
    info = layer.info()
    live = info.field_names()
    spec = load_schema_spec(cfg.parcels.schema_path)
    res = resolve_schema(spec, live)
    if not res.ok:
        raise ArcGISError("required parcel fields missing from the live layer:\n" + res.report())
    county_field = res.mapping["county_code"]
    account_field = res.mapping["account_id"]
    fields = wanted_fields(cfg, live)
    out_epsg = _epsg(cfg.working_crs)
    codes = list(county_codes or cfg.counties)
    summary: dict = {"layer": info.name, "url": cfg.parcels.url, "fields": fields, "counties": {}}
    log.info("parcel layer %s: %d live fields, %d requested; counties %s", info.name, len(live), len(fields), codes)
    for code in codes:
        out = parcels_dir / f"parcels_{code}.gpkg"
        out_row = row_dir / f"parcel_row_{code}.gpkg"
        if out.exists() and not force:
            log.info("skip %s: cached at %s", code, out)
            summary["counties"][code] = {"skipped": True, "path": str(out)}
            continue
        where = f"{county_field}='{code}'"
        t0 = time.time()
        pages = list(fetch_county_pages(layer, where, fields, out_epsg, bbox_4326=bbox_4326, page_size=page_size))
        if not pages:
            log.warning("%s: no features returned for %s", code, where)
            summary["counties"][code] = {"features": 0}
            continue
        gdf = gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), geometry=pages[0].geometry.name, crs=pages[0].crs)
        keep, row = _split_row(gdf, account_field, cfg.parcels.row_account_ids)
        keep.to_file(str(out), driver="GPKG")
        if len(row):
            row.to_file(str(out_row), driver="GPKG")
        dups = int(keep[account_field].duplicated().sum())
        summary["counties"][code] = {"features": int(len(gdf)), "parcels": int(len(keep)), "row_polygons": int(len(row)),
                                     "multi_polygon_account_rows": dups, "path": str(out),
                                     "row_path": str(out_row) if len(row) else None,
                                     "elapsed_s": round(time.time() - t0, 1)}
        log.info("%s: %d features -> %d parcels (%s), %d ROW/unlinked polygons (%s) in %.0fs",
                 code, len(gdf), len(keep), out, len(row), out_row, time.time() - t0)
    return summary
