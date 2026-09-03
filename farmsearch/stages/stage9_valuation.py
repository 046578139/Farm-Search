"""Stage 9 — Valuation from arms-length agricultural sales.

SDAT assessed values understate farmland badly (agricultural use
assessment), so they are never a price proxy. Instead: recent arms-length
sales of agricultural land across the three counties (the SDAT transfer
record carried by the property-sales layer: consideration, transfer date,
conveyance code), reduced to a LAND price per acre by deducting the
assessed improvement value at sale, then banded by whether the sold parcel
was under a permanent agricultural easement (Stage 2 favorable layers).
Each scored parcel gets the median band of its own segment (county first,
pooled when a county segment has too few comps), an estimated market value
(land plus its own assessed improvements) and a comp_basis string; the
optional price ceiling is applied against that estimate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import Config
from ..io.loaders import LayerNotAvailable, clean_geometries, read_layer

log = logging.getLogger(__name__)


@dataclass
class CompSet:
    comps: pd.DataFrame                      # account_id, county, sale_date, price, acres, land_price_per_acre, eased, improved
    bands: dict[tuple[str, str], dict]       # (county|'ALL', 'eased'|'uneased') -> {n, median, p25, p75}
    missing_layers: list[str] = field(default_factory=list)


@dataclass
class Stage9Result:
    parcels: gpd.GeoDataFrame
    comps: CompSet


def _parse_date(v) -> Optional[date]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10] if fmt != "%Y%m%d" else s[:8], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(int(float(s)) / 1000.0).date()      # epoch ms
    except (ValueError, OSError, OverflowError):
        return None


def build_comps(cfg: Config, clip: BaseGeometry, parcels_all: gpd.GeoDataFrame,
                favorable: Optional[BaseGeometry]) -> CompSet:
    q = cfg.valuation
    missing: list[str] = []
    frames = []
    for src in q.sales_layers:
        try:
            g = clean_geometries(read_layer(src, cfg.working_crs, clip, clip_mode="intersects"), kind="any")
        except LayerNotAvailable as e:
            log.warning("sales layer %s unavailable: %s", src.name, e)
            missing.append(src.name)
            continue
        frames.append(g)
        log.info("sales layer %s: %d transfer records", src.name, len(g))
    if not frames:
        return CompSet(comps=pd.DataFrame(), bands={}, missing_layers=missing)
    S = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=cfg.working_crs)
    need = [q.account_field, q.price_field, q.date_field, q.conveyance_field, q.acres_field, q.land_use_field]
    for f in need:
        if f not in S.columns:
            log.warning("sales layer lacks field %r; Stage 9 comps unavailable (fields: %s)", f, list(S.columns)[:30])
            return CompSet(comps=pd.DataFrame(), bands={}, missing_layers=missing + [f"sales:{f}"])
    ref = datetime.strptime(q.reference_date, "%Y-%m-%d").date() if q.reference_date else date.today()
    S["sale_date"] = S[q.date_field].map(_parse_date)
    S["price"] = pd.to_numeric(S[q.price_field], errors="coerce")
    S["acres"] = pd.to_numeric(S[q.acres_field], errors="coerce")
    S["conv"] = pd.to_numeric(S[q.conveyance_field], errors="coerce")
    imp = pd.to_numeric(S[q.improvement_value_field], errors="coerce").fillna(0.0) if q.improvement_value_field in S.columns else pd.Series(0.0, index=S.index)
    sq = pd.to_numeric(S[q.structure_sqft_field], errors="coerce").fillna(0.0) if q.structure_sqft_field in S.columns else pd.Series(0.0, index=S.index)
    age_ok = S["sale_date"].map(lambda d: d is not None and (ref - d).days <= q.max_age_years * 365.25 and d <= ref)
    keep = (age_ok & S["conv"].isin(q.arms_length_codes) & (S["price"] >= q.min_price) & (S["acres"] >= q.min_comp_acres)
            & S[q.land_use_field].astype("string").fillna("").isin(q.agricultural_land_uses))
    C = S[keep].copy()
    C["improved"] = (sq > 0)[keep].values | (imp > 0)[keep].values
    land = C["price"] - imp[keep].values
    C["land_price"] = np.where(land > 0.2 * C["price"], land, C["price"] * 0.8)   # never let a stale improvement value erase the sale
    C["land_price_per_acre"] = C["land_price"] / C["acres"]
    C["account_id"] = C[q.account_field].astype(str).str.strip()
    # eased status from the sold parcel's polygon (our fabric) against the favorable easement union
    geom_by = dict(zip(parcels_all["account_id"].astype(str).values, parcels_all.geometry.values))
    county_by = dict(zip(parcels_all["account_id"].astype(str).values, parcels_all["county"].values)) if "county" in parcels_all.columns else {}
    eased = []
    counties = []
    for acct, pt in zip(C["account_id"].values, C.geometry.values):
        pg = geom_by.get(acct)
        cty = county_by.get(acct)
        if pg is None or favorable is None or favorable.is_empty:
            eased.append(False if pg is not None or favorable is None else bool(favorable.intersects(pt)))
        else:
            eased.append(bool(pg.intersection(favorable).area >= q.eased_share_threshold * pg.area))
        counties.append(cty)
    C["eased"] = eased
    C["county"] = counties
    # a sale point outside our fabric (beyond the context buffer) keeps its JURSCODE county when present
    if "JURSCODE" in C.columns:
        C["county"] = [c if c is not None else str(j) for c, j in zip(C["county"], C["JURSCODE"].values)]
    bands: dict[tuple[str, str], dict] = {}
    def band(df):
        v = df["land_price_per_acre"].dropna()
        v = v[(v > 0) & (v < v.quantile(0.99) * 1.0001 + 1)] if len(v) > 10 else v
        return {"n": int(len(v)), "median": float(v.median()) if len(v) else None,
                "p25": float(v.quantile(0.25)) if len(v) else None, "p75": float(v.quantile(0.75)) if len(v) else None}
    for seg, sub in C.groupby(C["eased"].map({True: "eased", False: "uneased"})):
        bands[("ALL", seg)] = band(sub)
        for cty, sub2 in sub.groupby("county"):
            if cty is not None:
                bands[(str(cty), seg)] = band(sub2)
    log.info("Stage 9 comps: %d arms-length agricultural sales since %s; bands %s", len(C), (ref.replace(year=ref.year - int(q.max_age_years))).isoformat(),
             {f"{k[0]}/{k[1]}": (v["n"], round(v["median"]) if v["median"] else None) for k, v in bands.items()})
    cols = ["account_id", "county", "sale_date", "price", "acres", "land_price", "land_price_per_acre", "eased", "improved"]
    return CompSet(comps=pd.DataFrame(C[cols]), bands=bands, missing_layers=missing)


def run_stage9(cfg: Config, scored: gpd.GeoDataFrame, comps: CompSet) -> Stage9Result:
    q = cfg.valuation
    P = scored.reset_index(drop=True).copy()
    for k, v in {"est_market_value": np.nan, "est_per_acre": np.nan, "est_land_value": np.nan, "comp_basis": None,
                 "valuation_segment": None, "comp_count": np.nan, "comp_band_p25": np.nan, "comp_band_p75": np.nan,
                 "valuation_flags": None}.items():
        P[k] = v
    P["valuation_flags"] = [[] for _ in range(len(P))]
    if not comps.bands:
        for i in range(len(P)):
            P.at[i, "valuation_flags"].append("valuation_unavailable_no_comps")
        return Stage9Result(parcels=P, comps=comps)
    fav = P["favorable_easement_acres"] if "favorable_easement_acres" in P.columns else pd.Series(0.0, index=P.index)
    gross = P["gross_acres"]
    imp = pd.to_numeric(P["assessed_improvement_value"], errors="coerce").fillna(0.0) if "assessed_improvement_value" in P.columns else pd.Series(0.0, index=P.index)
    for i in range(len(P)):
        seg = "eased" if (gross.iloc[i] and fav.iloc[i] / gross.iloc[i] >= q.eased_share_threshold) else "uneased"
        cty = str(P["county"].iloc[i])
        b = comps.bands.get((cty, seg))
        basis_scope = cty
        if b is None or b["n"] < q.min_comps_per_segment or b["median"] is None:
            b = comps.bands.get(("ALL", seg))
            basis_scope = "three counties"
            if b is None or b["median"] is None:
                other = comps.bands.get(("ALL", "uneased" if seg == "eased" else "eased"))
                if other is None or other["median"] is None:
                    P.at[i, "valuation_flags"].append("valuation_unavailable_no_comps")
                    continue
                b = other
                basis_scope = "three counties, other segment"
                P.at[i, "valuation_flags"].append("valuation_segment_borrowed")
        per_acre = b["median"]
        land = per_acre * float(gross.iloc[i])
        P.at[i, "est_per_acre"] = round(per_acre, 0)
        P.at[i, "est_land_value"] = round(land, 0)
        P.at[i, "est_market_value"] = round(land + float(imp.iloc[i]), 0)
        P.at[i, "comp_band_p25"] = round(b["p25"], 0) if b["p25"] is not None else np.nan
        P.at[i, "comp_band_p75"] = round(b["p75"], 0) if b["p75"] is not None else np.nan
        P.at[i, "comp_count"] = b["n"]
        P.at[i, "valuation_segment"] = seg
        P.at[i, "comp_basis"] = (f"{seg} agricultural land, {basis_scope}, n={b['n']} arms-length sales within "
                                 f"{q.max_age_years:g} yr, median ${per_acre:,.0f}/ac (IQR ${b['p25'] or 0:,.0f}-${b['p75'] or 0:,.0f}); "
                                 f"improvements at assessed ${float(imp.iloc[i]):,.0f}")
        if b["n"] < q.min_comps_per_segment:
            P.at[i, "valuation_flags"].append("valuation_thin_comp_set")
        if q.price_ceiling is not None and P.at[i, "est_market_value"] > q.price_ceiling:
            P.at[i, "valuation_flags"].append("estimated_value_above_price_ceiling")
        if q.price_ceiling_per_acre is not None and per_acre > q.price_ceiling_per_acre:
            P.at[i, "valuation_flags"].append("estimated_per_acre_above_ceiling")
    return Stage9Result(parcels=P, comps=comps)
