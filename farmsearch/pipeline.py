"""Orchestrates Stages 1-4 and writes outputs.

Outputs (in run.output_dir):
  parcels_stage1.gpkg          every parcel in the study area with Stage 1 flags
  encumbrances.csv             one row per parcel x constraint layer (Stage 2)
  usable_area.gpkg             usable polygon per scored parcel (Stage 3)
  frontage.gpkg                classified road-facing boundary pieces (Stage 4)
  entry_points.gpkg            usable entry nodes (Stage 4)
  reserve_strips.csv           candidate access-control strips (Stage 4)
  islands.csv                  unreachable usable islands (Stage 4)
  parcels_scored.gpkg/.csv/.geojson   per-parcel record (spec "Output")
  summary.json / summary.md    counts, per county, for the sanity check
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import pandas as pd

from .config import Config
from .io.loaders import load_study_area
from .stages.stage1_base_filter import run_stage1
from .stages.stage2_encumbrance import load_constraint_layers, run_stage2
from .stages.stage3_usable_area import SlopeProvider, run_stage3
from .stages.stage4_access import load_row_layers, run_stage4

log = logging.getLogger(__name__)

# Column order of the per-parcel record (Stages 1-4 portion of the spec).
RECORD_COLUMNS = [
    "account_id", "owner_name", "owner_mailing_address", "owner_type", "owner_key",
    "gross_acres", "zoning", "county",
    "encumbrances_json", "usable_acres", "largest_contiguous_reachable_acres", "unreachable_islands_json",
    "landlocked_apparent", "frontage_blocked_by_foreign_parcel", "blocking_parcel_account_id",
    "manual_verification_flags",
    # supporting detail
    "stage1_pass", "stage1_pass_reason", "meets_acreage", "is_agricultural", "zoning_ag_pct", "zoning_codes_all",
    "gross_acres_sdat", "gross_acres_geom", "acreage_basis", "acreage_disagreement_pct", "in_study_area_pct",
    "favorable_easement_acres", "hostile_easement_acres", "physical_constraint_acres", "bisecting_constraints",
    "usable_pct", "usable_components", "steep_slope_acres", "slope_evaluated", "ag_easement_within_usable_acres",
    "reachable_usable_acres", "unreachable_island_count", "unreachable_island_acres", "sliver_acres",
    "reachable_if_crossings_permitted_acres", "largest_reachable_if_crossings_permitted_acres",
    "islands_reconnectable_by_permit_acres",
    "road_facing_ft", "row_contact_ft", "frontage_open_ft", "frontage_encumbered_ft", "frontage_foreign_ft",
    "frontage_same_owner_ft", "frontage_gap_ft", "frontage_blocked_pct", "frontage_foreign_pct",
    "no_public_row_nearby", "access_via_same_owner_parcel", "blocking_parcel_owner", "blocking_parcels_json",
    "frontage_authorities", "entry_node_count", "reserve_strip_detected", "reserve_strips_json",
]

CANNOT_DETERMINE = [
    "Deeded access easements over neighboring land are recorded in land records, not mapped: a landlocked_apparent parcel may have good deeded access. Flag, never auto-delete.",
    "Entrance permit feasibility: a perc approval says nothing about whether a driveway can be permitted. Call county public works (or SHA on state-numbered roads).",
    "Stream crossing permits: crossing a stream generally requires an MDE nontidal wetlands and waterways permit regardless of easement status; agricultural crossings are routinely permitted.",
    "County discharge ordinances and range zoning: verify with Carroll, Frederick and Washington County zoning offices.",
    "Individual easement terms: MET and donated easements vary widely. Read them.",
    "CREP riparian buffers are inferred from stream geometry; FSA enrollment data is not available parcel-by-parcel. Confirm with seller.",
]


def _writable(gdf: gpd.GeoDataFrame | pd.DataFrame) -> gpd.GeoDataFrame | pd.DataFrame:
    """GPKG/CSV cannot hold Python lists or None-typed object columns: serialize."""
    out = gdf.copy()
    geom_name = out.geometry.name if isinstance(out, gpd.GeoDataFrame) else None
    for c in out.columns:
        if c == geom_name:
            continue
        s = out[c]
        if s.dtype == object:
            if s.map(lambda v: isinstance(v, (list, dict))).any():
                out[c] = s.map(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
            elif s.map(lambda v: isinstance(v, bool)).any():
                out[c] = s.map(lambda v: None if v is None else bool(v)).astype("boolean")
            elif s.isna().all():
                out[c] = s.astype("string")
    return out


def _drop_private(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf[[c for c in gdf.columns if not c.startswith("_")]]


def run_pipeline(cfg: Config, stages: Iterable[int] = (1, 2, 3, 4), out_dir: Optional[Path] = None,
                 parcels_raw: Optional[gpd.GeoDataFrame] = None, write: bool = True) -> dict:
    stages = set(stages)
    out_dir = Path(out_dir or cfg.run.output_dir)
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    summary: dict = {"config": {"acreage_min": cfg.acreage_min, "acreage_max": cfg.acreage_max,
                                "slope_max_pct": cfg.slope_max_pct, "study_area": str(cfg.study_area_path),
                                "study_area_selection": cfg.study_area_selection, "working_crs": cfg.working_crs},
                     "stages_run": sorted(stages), "missing_layers": [], "cannot_determine": CANNOT_DETERMINE}
    study = load_study_area(cfg)

    # ---- Stage 1 ------------------------------------------------------
    s1 = run_stage1(cfg, study, parcels_raw=parcels_raw)
    parcels = s1.parcels
    summary["stage1"] = s1.summary
    log.info("Stage 1: %d parcels in study area, %d pass", len(parcels), int(parcels["stage1_pass"].sum()))
    if write:
        _writable(_drop_private(parcels)).to_file(out_dir / "parcels_stage1.gpkg", driver="GPKG")
    targets = parcels["stage1_pass"] | cfg.run.process_all
    scored = parcels[targets].reset_index(drop=True)
    result = {"study_area": study, "stage1": s1}

    # ---- Stage 2 ------------------------------------------------------
    s2 = s3 = s4 = None
    if 2 in stages:
        layers, missing = load_constraint_layers(cfg, study)
        summary["missing_layers"] += missing
        s2 = run_stage2(cfg, scored, layers, missing)
        scored = s2.parcels
        summary["stage2"] = {
            "constraint_layers_loaded": sorted(layers), "constraint_layers_missing": missing,
            "encumbrance_rows": int(len(s2.encumbrances)),
            "parcels_with_hostile_easement": int((scored["hostile_easement_acres"] > 0).sum()),
            "parcels_with_favorable_easement": int((scored["favorable_easement_acres"] > 0).sum()),
            "parcels_bisected_by_hostile_constraint": int((scored["bisecting_constraints"] != "").sum()),
        }
        if write:
            s2.encumbrances.to_csv(out_dir / "encumbrances.csv", index=False)
        result["stage2"] = s2

    # ---- Stage 3 ------------------------------------------------------
    if 3 in stages:
        if s2 is None:
            raise ValueError("Stage 3 requires Stage 2")
        sp = SlopeProvider(cfg, study)
        s3 = run_stage3(cfg, scored, s2.geoms, slope_provider=sp, study_geom=study)
        scored = s3.parcels
        summary["stage3"] = {
            "slope_source": s3.slope_source,
            "slope_windows_failed": s3.slope_windows_failed,
            "parcels_not_slope_evaluated": int((~scored["slope_evaluated"].astype(bool)).sum()),
            "usable_acres_total": float(scored["usable_acres"].sum()),
            "gross_acres_total": float(scored["gross_acres"].sum()),
            "parcels_usable_below_acreage_min": int((scored["usable_acres"] < cfg.acreage_min).sum()),
        }
        if write:
            _writable(s3.usable).to_file(out_dir / "usable_area.gpkg", driver="GPKG")
        result["stage3"] = s3

    # ---- Stage 4 ------------------------------------------------------
    if 4 in stages:
        if s3 is None:
            raise ValueError("Stage 4 requires Stage 3")
        rows, missing = load_row_layers(cfg, study)
        summary["missing_layers"] += missing
        # Neighbors: all study-area parcels, carrying the scored attributes for targets
        new_cols = [c for c in scored.columns if c not in parcels.columns]
        merged = parcels.merge(scored[["account_id"] + new_cols], on="account_id", how="left")
        merged = gpd.GeoDataFrame(merged, geometry=parcels.geometry.name, crs=parcels.crs)
        mask = merged["account_id"].isin(scored["account_id"])
        s4 = run_stage4(cfg, merged, mask, s3.geoms, rows, missing)
        scored = s4.parcels[mask.values].reset_index(drop=True)
        summary["stage4"] = {
            "row_layers_missing": missing, "row_features": int(len(rows)),
            "landlocked_apparent": int(scored["landlocked_apparent"].sum()),
            "frontage_blocked_by_foreign_parcel": int(scored["frontage_blocked_by_foreign_parcel"].sum()),
            "reserve_strip_detected": int(scored["reserve_strip_detected"].sum()),
            "parcels_with_unreachable_islands": int((scored["unreachable_island_count"] > 0).sum()),
            "largest_reachable_below_acreage_min": int((scored["largest_contiguous_reachable_acres"] < cfg.acreage_min).sum()),
            "largest_reachable_ge_acreage_min": int((scored["largest_contiguous_reachable_acres"] >= cfg.acreage_min).sum()),
        }
        if write:
            _writable(s4.frontage).to_file(out_dir / "frontage.gpkg", driver="GPKG")
            if len(s4.entry_points):
                _writable(s4.entry_points).to_file(out_dir / "entry_points.gpkg", driver="GPKG")
            s4.strips.to_csv(out_dir / "reserve_strips.csv", index=False)
            s4.islands.to_csv(out_dir / "islands.csv", index=False)
        result["stage4"] = s4

    # ---- Final record --------------------------------------------------
    scored = assemble_record(scored)
    result["scored"] = scored
    summary["scored_parcels"] = int(len(scored))
    summary["elapsed_s"] = round(time.time() - t0, 1)
    result["summary"] = summary
    if write:
        w = _writable(_drop_private(scored))
        w.to_file(out_dir / "parcels_scored.gpkg", driver="GPKG")
        w.to_file(out_dir / "parcels_scored.geojson", driver="GeoJSON")
        w.drop(columns=[w.geometry.name]).to_csv(out_dir / "parcels_scored.csv", index=False)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
        (out_dir / "summary.md").write_text(render_summary(summary))
    return result


def assemble_record(scored: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    scored = scored.copy()
    flag_cols = [c for c in ("manual_flags", "encumbrance_flags", "access_flags") if c in scored.columns]
    merged = []
    for _, r in scored.iterrows():
        out: list[str] = []
        for c in flag_cols:
            v = r[c]
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    v = [v]
            for f in (v or []):
                if f not in out:
                    out.append(f)
        merged.append(out)
    scored["manual_verification_flags"] = merged
    scored = scored.drop(columns=flag_cols)
    for c in RECORD_COLUMNS:
        if c not in scored.columns:
            scored[c] = None
    other = [c for c in scored.columns if c not in RECORD_COLUMNS and c != scored.geometry.name]
    return scored[RECORD_COLUMNS + other + [scored.geometry.name]]


def render_summary(s: dict) -> str:
    L = ["# Farm-Search run summary", ""]
    c = s["config"]
    L.append(f"- acreage_min: {c['acreage_min']}  acreage_max: {c['acreage_max']}  slope_max_pct: {c['slope_max_pct']}")
    L.append(f"- study area: {c['study_area']} ({c['study_area_selection']})")
    L.append(f"- stages run: {s['stages_run']}   elapsed: {s.get('elapsed_s')}s")
    if s.get("missing_layers"):
        L.append(f"- **layers missing (stages degraded):** {', '.join(s['missing_layers'])}")
    if "stage1" in s:
        s1 = s["stage1"]
        L += ["", "## Stage 1 — base filter", "",
              f"loaded {s1['parcels_loaded']} · in study area {s1['parcels_in_study_area']} · "
              f"meets acreage {s1['meets_acreage']} · pass {s1['stage1_pass']} · "
              f"unique owners passing {s1['unique_owner_keys_passing']}", "",
              "| county | in study area | ≥ acreage | ag-zoned | zoning unknown | pass | median ac | total ac |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for k, v in s1["per_county"].items():
            med = f"{v['median_acres_passing']:.0f}" if v["median_acres_passing"] is not None else "-"
            L.append(f"| {k} | {v['in_study_area']} | {v['meets_acreage']} | {v['meets_acreage_and_ag_zoned']} | "
                     f"{v['meets_acreage_zoning_unknown']} | {v['stage1_pass']} | {med} | {v['acres_passing_total']:.0f} |")
    if "stage2" in s:
        s2 = s["stage2"]
        L += ["", "## Stage 2 — encumbrances", "",
              f"layers: {', '.join(s2['constraint_layers_loaded']) or 'none'}",
              f"rows {s2['encumbrance_rows']} · hostile-eased parcels {s2['parcels_with_hostile_easement']} · "
              f"favorably-eased parcels {s2['parcels_with_favorable_easement']} · bisected {s2['parcels_bisected_by_hostile_constraint']}"]
    if "stage3" in s:
        s3 = s["stage3"]
        L += ["", "## Stage 3 — usable area", "",
              f"slope source: {s3['slope_source']} (windows failed: {s3.get('slope_windows_failed', 0)}) · usable {s3['usable_acres_total']:.0f} of {s3['gross_acres_total']:.0f} gross acres · "
              f"parcels whose usable area falls below acreage_min: {s3['parcels_usable_below_acreage_min']}"]
    if "stage4" in s:
        s4 = s["stage4"]
        L += ["", "## Stage 4 — access connectivity", "",
              f"landlocked_apparent {s4['landlocked_apparent']} · frontage blocked by foreign parcel {s4['frontage_blocked_by_foreign_parcel']} · "
              f"reserve strips {s4['reserve_strip_detected']} · parcels with islands {s4['parcels_with_unreachable_islands']}",
              f"largest reachable block ≥ acreage_min: {s4['largest_reachable_ge_acreage_min']} · below: {s4['largest_reachable_below_acreage_min']}"]
    L += ["", "## What this pipeline cannot determine", ""]
    L += [f"- {x}" for x in s["cannot_determine"]]
    return "\n".join(L) + "\n"
