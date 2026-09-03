"""Deliverables: ranked shortlist, deduplicated owner list, per-parcel PDF dossiers.

  shortlist   weighted sum of min-max normalised metrics (weights in config;
              sign gives direction). Hard rules: MPRP tier 1 excluded (spec),
              and by default the largest reachable block must clear
              acreage_min. Every flag is still a reason to look, never a
              reason to drop a row: excluded parcels are listed with the
              reason in shortlist_excluded.csv.
  owner list  parcels collapsed by owner_key (normalised owner + mailing
              address): one letter per mailbox.
  dossiers    one PDF page-set per shortlist parcel: map of the parcel with
              encumbrances coloured by type, usable area, dischargeable
              envelope, access diagram (frontage classes, entry nodes, ROW,
              blocking parcels, strips), and the record's key fields/flags.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger(__name__)

SHORTLIST_COLUMNS = [
    "account_id", "county", "owner_name", "owner_mailing_address", "owner_type", "gross_acres", "usable_acres",
    "largest_contiguous_reachable_acres", "dischargeable_envelope_acres", "dischargeable_envelope_longest_dim_yards",
    "dwellings_with_line_of_sight", "mprp_tier", "adjacent_residential_zoning_acres", "adjacent_planned_sewer",
    "approved_unbuilt_units_within_2mi", "adjacent_permanently_eased_acres", "est_market_value", "est_per_acre",
    "commute_bwi_peak_min", "commute_langley_peak_min", "commute_nova_peak_min", "route_redundancy",
    "corridor_durability_score", "landlocked_apparent", "frontage_blocked_by_foreign_parcel", "sdat_url",
]


def _num(s: pd.Series) -> pd.Series:
    if s.dtype == bool or str(s.dtype) == "boolean":
        return s.astype(float)
    return pd.to_numeric(s.map(lambda v: {True: 1.0, False: 0.0}.get(v, v)), errors="coerce")


def rank_shortlist(scored: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (shortlist, excluded). Score = sum(weight * normalised metric),
    where each metric is min-max scaled over the eligible parcels and missing
    values sit at the neutral midpoint. Components are kept per parcel."""
    sl = cfg.shortlist
    df = pd.DataFrame(scored.drop(columns=[c for c in (getattr(scored, "geometry", None) is not None and [scored.geometry.name] or [])], errors="ignore"))
    reasons = pd.Series([""] * len(df), index=df.index, dtype=object)
    if sl.exclude_mprp_tier_1 and "mprp_tier" in df.columns:
        m = _num(df["mprp_tier"]) == 1
        reasons[m] = "mprp_tier_1_intersects_studied_route"
    if sl.require_reachable_acres_min and "largest_contiguous_reachable_acres" in df.columns:
        m = (_num(df["largest_contiguous_reachable_acres"]) < cfg.acreage_min) & (reasons == "")
        reasons[m] = "largest_reachable_block_below_acreage_min"
    eligible = df[reasons == ""].copy()
    excluded = df[reasons != ""].copy()
    excluded["exclusion_reason"] = reasons[reasons != ""]
    components = {}
    score = pd.Series(0.0, index=eligible.index)
    for col, w in sl.weights.items():
        if w == 0 or col not in eligible.columns:
            continue
        v = _num(eligible[col])
        if v.notna().sum() == 0:
            continue
        lo, hi = float(v.min()), float(v.max())
        norm = (v - lo) / (hi - lo) if hi > lo else pd.Series(0.5, index=v.index)
        norm = norm.fillna(0.5)
        components[f"score_{col}"] = (w * norm).round(3)
        score = score + w * norm
    out = eligible.copy()
    for k, v in components.items():
        out[k] = v
    out["shortlist_score"] = score.round(3)
    out = out.sort_values("shortlist_score", ascending=False)
    out["rank"] = np.arange(1, len(out) + 1)
    cols = ["rank", "shortlist_score"] + [c for c in SHORTLIST_COLUMNS if c in out.columns] + \
           [c for c in out.columns if c.startswith("score_")] + (["manual_verification_flags"] if "manual_verification_flags" in out.columns else [])
    return out[cols].head(sl.top_n), excluded[[c for c in ["account_id", "county", "gross_acres", "exclusion_reason"] if c in excluded.columns]]


def owner_list(scored: pd.DataFrame, shortlist: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Collapse by owner_key: one row per owner + mailing address."""
    df = pd.DataFrame(scored.drop(columns=[scored.geometry.name], errors="ignore")) if hasattr(scored, "geometry") else pd.DataFrame(scored)
    if "owner_key" not in df.columns:
        df["owner_key"] = df["account_id"]
    key = df["owner_key"].fillna("").astype(str)
    key = key.where(key.str.strip("|").str.strip() != "", "acct:" + df["account_id"].astype(str))
    df = df.assign(_key=key)
    ranks = {}
    if shortlist is not None and len(shortlist):
        ranks = dict(zip(shortlist["account_id"].astype(str), shortlist["rank"]))
    df["_rank"] = df["account_id"].astype(str).map(ranks)
    rows = []
    for k, g in df.groupby("_key", sort=False):
        best = g["_rank"].min()
        rows.append({
            "owner_key": k,
            "owner_name": next((x for x in g.get("owner_name", pd.Series([None] * len(g))).dropna().astype(str) if x.strip()), None),
            "owner_mailing_address": next((x for x in g.get("owner_mailing_address", pd.Series([None] * len(g))).dropna().astype(str) if x.strip()), None),
            "owner_type": g["owner_type"].iloc[0] if "owner_type" in g.columns else None,
            "parcel_count": int(len(g)),
            "account_ids": ";".join(sorted(g["account_id"].astype(str))),
            "gross_acres_total": round(float(_num(g["gross_acres"]).sum()), 2) if "gross_acres" in g.columns else None,
            "largest_reachable_acres_max": round(float(_num(g["largest_contiguous_reachable_acres"]).max()), 2) if "largest_contiguous_reachable_acres" in g.columns else None,
            "best_shortlist_rank": (int(best) if pd.notna(best) else None),
            "on_shortlist": bool(pd.notna(best)),
            "sdat_urls": ";".join(str(x) for x in g.get("sdat_url", pd.Series([], dtype=object)).dropna().astype(str).head(6)),
            "counties": ";".join(sorted(set(g["county"].astype(str)))) if "county" in g.columns else None,
        })
    out = pd.DataFrame(rows).sort_values(["on_shortlist", "best_shortlist_rank", "gross_acres_total"], ascending=[False, True, False])
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
ENC_COLORS = {
    "ag_preservation_malpf": "#1b9e77", "county_ag_preservation": "#66a61e", "rural_legacy": "#a6d854", "met_easement": "#e6ab02",
    "forest_conservation": "#7570b3", "forest_banking": "#8da0cb", "wetlands": "#1f78b4", "floodplain": "#a6cee3",
    "riparian_buffer": "#33a02c", "crep_enrolled_farm": "#b2df8a", "dnr_lands": "#fdbf6f", "federal_lands": "#ff7f00",
    "local_protected_lands": "#fb9a99", "other_easement": "#cab2d6", "steep_slope": "#e31a1c",
}
FRONTAGE_COLORS = {"open": "#2ca02c", "encumbered": "#ff7f0e", "foreign_parcel": "#d62728", "same_owner_parcel": "#9467bd", "gap": "#7f7f7f"}


def _read(out_dir: Path, name: str, **kw):
    p = out_dir / name
    return gpd.read_file(p, **kw) if p.exists() else None


def render_dossiers(cfg: Config, out_dir: Path, shortlist: pd.DataFrame, pdf_path: Optional[Path] = None,
                    parcels_path: Optional[Path] = None, rows: Optional[gpd.GeoDataFrame] = None) -> Path:
    """One PDF, two pages per shortlist parcel: map + record."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    import pyogrio

    out_dir = Path(out_dir)
    pdf_path = Path(pdf_path or out_dir / "dossiers.pdf")
    P = gpd.read_file(out_dir / "parcels_scored.gpkg").set_index("account_id")
    U = _read(out_dir, "usable_area.gpkg")
    E = _read(out_dir, "envelope.gpkg")
    F = _read(out_dir, "frontage.gpkg")
    EP = _read(out_dir, "entry_points.gpkg")
    ENC = _read(out_dir, "encumbrances.gpkg")
    ROW = rows if rows is not None else _read(out_dir, "row_public.gpkg")
    STR = _read(out_dir, "occupied_structures.gpkg")
    parcels_path = Path(parcels_path or out_dir / "parcels_stage1.gpkg")
    R = pd.read_csv(out_dir / "reserve_strips.csv", dtype=str) if (out_dir / "reserve_strips.csv").exists() else pd.DataFrame(columns=["account_id", "strip_account_id"])
    with PdfPages(pdf_path) as pdf:
        for _, row in shortlist.iterrows():
            acct = str(row["account_id"])
            if acct not in P.index:
                continue
            g = P.loc[acct]
            geom = g.geometry
            fig, ax = plt.subplots(figsize=(11, 8.5))
            b = geom.buffer(200); minx, miny, maxx, maxy = b.bounds
            try:
                nb = pyogrio.read_dataframe(str(parcels_path), bbox=(minx, miny, maxx, maxy), columns=["account_id", "is_account"])
                nb = nb[nb["account_id"] != acct]
                nb.plot(ax=ax, facecolor="#f4f4f4", edgecolor="#9a9a9a", linewidth=0.5)
                if "is_account" in nb.columns:
                    nb[nb["is_account"] == False].plot(ax=ax, facecolor="#ffe680", edgecolor="#b3a000", linewidth=0.6)  # noqa: E712
            except Exception as ex:  # noqa: BLE001
                log.warning("neighbours unavailable for %s: %s", acct, ex)
            if ROW is not None and len(ROW):
                rr = ROW[ROW.intersects(b)]
                if len(rr):
                    rr.plot(ax=ax, facecolor="#4d4d4d", edgecolor="none", alpha=0.45)
            gpd.GeoSeries([geom], crs=P.crs).plot(ax=ax, facecolor="#ffffff", edgecolor="black", linewidth=2, alpha=0.6)
            legend = []
            if U is not None and len(U):
                uu = U[U["account_id"] == acct]
                if len(uu):
                    uu.plot(ax=ax, facecolor="#c7e9c0", edgecolor="none", alpha=0.9)
                    legend.append(Patch(facecolor="#c7e9c0", label=f"usable {g.get('usable_acres', float('nan')):.0f} ac"))
            if ENC is not None and len(ENC):
                ee = ENC[ENC["account_id"] == acct]
                for t, sub in ee.groupby("type"):
                    col = ENC_COLORS.get(t, "#999999")
                    sub.plot(ax=ax, facecolor=col, edgecolor=col, alpha=0.45, hatch="//" if t in ("forest_conservation", "steep_slope") else None)
                    legend.append(Patch(facecolor=col, alpha=0.6, label=f"{t} {sub['acres'].astype(float).sum():.1f} ac"))
            if E is not None and len(E):
                en = E[E["account_id"] == acct]
                if len(en):
                    en.plot(ax=ax, facecolor="none", edgecolor="#006d2c", linewidth=1.8, linestyle="--")
                    legend.append(Line2D([0], [0], color="#006d2c", linestyle="--", linewidth=1.8,
                                         label=f"dischargeable envelope {g.get('dischargeable_envelope_acres', float('nan')):.0f} ac"))
            if F is not None and len(F):
                ff = F[F["account_id"] == acct]
                for cls, col in FRONTAGE_COLORS.items():
                    sub = ff[ff["class"] == cls]
                    if len(sub):
                        sub.plot(ax=ax, color=col, linewidth=4)
                        legend.append(Line2D([0], [0], color=col, linewidth=4, label=f"frontage {cls} {sub['length_ft'].astype(float).sum():.0f} ft"))
            if EP is not None and len(EP):
                ep = EP[EP["account_id"] == acct]
                if len(ep):
                    ep.plot(ax=ax, color="#1f77b4", markersize=14, zorder=6)
                    legend.append(Line2D([0], [0], marker="o", color="w", markerfacecolor="#1f77b4", markersize=8, label=f"entry nodes ({len(ep)})"))
            strips = R[R["account_id"] == acct]
            if len(strips):
                try:
                    sg = pyogrio.read_dataframe(str(parcels_path), bbox=(minx, miny, maxx, maxy), columns=["account_id"])
                    sg = sg[sg["account_id"].isin(set(strips["strip_account_id"]))]
                    if len(sg):
                        sg.plot(ax=ax, facecolor="none", edgecolor="#d62728", linewidth=2.5, linestyle="--")
                        legend.append(Line2D([0], [0], color="#d62728", linestyle="--", linewidth=2.5, label="reserve strip candidate"))
                except Exception:  # noqa: BLE001
                    pass
            bid = g.get("blocking_parcel_account_id")
            if isinstance(bid, str) and bid:
                try:
                    bg = pyogrio.read_dataframe(str(parcels_path), bbox=(minx, miny, maxx, maxy), columns=["account_id"])
                    bg = bg[bg["account_id"] == bid]
                    if len(bg):
                        bg.plot(ax=ax, facecolor="#f4a582", edgecolor="#d62728", linewidth=1.5, alpha=0.6)
                        legend.append(Patch(facecolor="#f4a582", label=f"blocking parcel {bid}"))
                except Exception:  # noqa: BLE001
                    pass
            if STR is not None and len(STR):
                st = STR[STR.intersects(b)]
                if len(st):
                    st.representative_point().plot(ax=ax, color="#8c510a", marker="s", markersize=10, zorder=5)
                    legend.append(Line2D([0], [0], marker="s", color="w", markerfacecolor="#8c510a", markersize=7, label="occupied structure (safety zone source)"))
            ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
            ax.set_title(f"#{int(row['rank'])}  {acct}  ({g.get('county')})  {g.get('gross_acres', 0):.1f} ac gross · "
                         f"largest reachable {g.get('largest_contiguous_reachable_acres', 0):.1f} ac · score {row['shortlist_score']:.2f}", fontsize=11)
            if legend:
                ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False)
            fig.tight_layout()
            pdf.savefig(fig); plt.close(fig)
            # page 2: the record
            fig = plt.figure(figsize=(11, 8.5)); fig.text(0.04, 0.96, f"{acct} — record", fontsize=13, weight="bold")
            keys = [c for c in SHORTLIST_COLUMNS if c in P.columns and c != "account_id"] + \
                   ["reachable_if_crossings_permitted_acres", "unreachable_island_count", "hostile_easement_acres", "favorable_easement_acres",
                    "steep_slope_acres", "frontage_authorities", "zoning", "zoning_codes_all", "comp_basis", "mprp_route_variant",
                    "adjacent_zoning_codes", "corridor_road", "commute_basis"]
            y = 0.92
            for k in keys:
                if k not in P.columns:
                    continue
                v = g.get(k)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                txt = str(v)
                if len(txt) > 110:
                    txt = txt[:107] + "..."
                fig.text(0.04, y, f"{k}: {txt}", fontsize=8, family="monospace"); y -= 0.022
                if y < 0.30:
                    break
            flags = g.get("manual_verification_flags")
            if isinstance(flags, str):
                try:
                    flags = json.loads(flags)
                except Exception:  # noqa: BLE001
                    flags = [flags]
            fig.text(0.04, 0.27, "manual verification flags:", fontsize=9, weight="bold")
            yy = 0.245
            for fl in (flags or [])[:14]:
                fig.text(0.06, yy, f"• {fl}", fontsize=8); yy -= 0.017
            pdf.savefig(fig); plt.close(fig)
    return pdf_path
