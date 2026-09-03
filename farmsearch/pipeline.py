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
  checkpoint_stage{1,2,3}.pkl  in-memory state after each stage, so that
                               `run --stages 4 --resume` continues a run
                               that was interrupted (a 30-minute run in an
                               environment that may restart underneath it)
"""
from __future__ import annotations

import json
import logging
import pickle
import time
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import unary_union

from .units import ft_to_m, m2_to_acres

from .config import Config
from .io.loaders import context_geometry, load_study_area
from .stages.stage1_base_filter import Stage1Result, run_stage1
from .stages.stage2_encumbrance import load_constraint_layers, run_stage2
from .stages.stage3_usable_area import SlopeProvider, run_stage3
from .stages.stage4_access import load_row_layers, run_stage4
from .stages.stage5_envelope import load_occupied_structures, run_stage5
from .stages.stage6_viewshed import run_stage6
from .stages.stage7_encroachment import load_encroachment_layers, load_favorable_layers, run_stage7
from .stages.stage9_valuation import build_comps, run_stage9
from .stages.stage10_commute import load_commute_layers, run_stage10
from .stages.stage8_transmission import load_transmission_layers, run_stage8

log = logging.getLogger(__name__)

# Column order of the per-parcel record (Stages 1-4 portion of the spec).
RECORD_COLUMNS = [
    "account_id", "owner_name", "owner_mailing_address", "owner_type", "owner_key",
    "gross_acres", "zoning", "county",
    "encumbrances_json", "usable_acres", "largest_contiguous_reachable_acres", "unreachable_islands_json",
    "landlocked_apparent", "frontage_blocked_by_foreign_parcel", "blocking_parcel_account_id",
    "dischargeable_envelope_acres", "dischargeable_envelope_longest_dim_yards", "dwellings_with_line_of_sight",
    "candidate_backstop_slopes",
    "same_owner_structures_within_safety_zone", "dwellings_line_of_sight_unevaluated",
    "subject_pfa_acres", "approved_unbuilt_units_radius_ft", "adjacent_boundary_covered_pct",
    "mprp_tier", "adjacent_residential_zoning_acres", "adjacent_planned_sewer", "approved_unbuilt_units_within_2mi",
    "adjacent_permanently_eased_acres",
    "est_market_value", "est_per_acre", "comp_basis",
    "commute_bwi_peak_min", "commute_langley_peak_min", "commute_nova_peak_min", "route_redundancy",
    "corridor_durability_score",
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
                out[c] = s.map(lambda v: None if (v is None or (isinstance(v, float) and pd.isna(v))) else bool(v)).astype("boolean")
            elif s.isna().all():
                out[c] = s.astype("string")
    return out


def _drop_private(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gdf[[c for c in gdf.columns if not c.startswith("_")]]


def _checkpoint_path(out_dir: Path, n: int) -> Path:
    return out_dir / f"checkpoint_stage{n}.pkl"


def _save_checkpoint(out_dir: Path, n: int, payload: dict) -> None:
    p = _checkpoint_path(out_dir, n)
    tmp = p.with_suffix(".partial")
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(p)
    log.info("checkpoint after Stage %d written to %s", n, p)


def _load_checkpoint(out_dir: Path, n: int) -> dict:
    p = _checkpoint_path(out_dir, n)
    if not p.exists():
        raise FileNotFoundError(f"cannot resume: no checkpoint after Stage {n} in {out_dir} (run the earlier stages first)")
    with open(p, "rb") as f:
        return pickle.load(f)


# Stage 5 writes these, and Stage 6 reads them back when resumed on its own:
# they describe the same state a checkpoint does and are retired with them.
LATER_STAGE_OUTPUTS = ("envelope.gpkg", "occupied_structures.gpkg")


def _drop_checkpoints_after(out_dir: Path, n: int) -> None:
    """Stage n has just been rewritten, so every later checkpoint describes a
    state that no longer exists: remove them rather than let a later --resume
    silently reload the stale frame."""
    for k in range(n + 1, 11):
        p = _checkpoint_path(out_dir, k)
        if p.exists():
            p.unlink()
            log.info("removed stale %s (Stage %d was re-run)", p.name, n)
    if n < 5:
        for name in LATER_STAGE_OUTPUTS:
            p = out_dir / name
            if p.exists():
                p.unlink()
                log.info("removed stale %s (Stage %d was re-run)", name, n)


def _latest_checkpoint_at_or_before(out_dir: Path, n: int) -> int:
    """Stages 5-10 each depend on Stages 1-4 but not on one another, so a
    resume for Stage 8 may start from the state after Stage 4, 5, 6 or 7 —
    whichever was written last. Stage 4 is the floor: its access columns are
    required, and a Stage 1-3 checkpoint would silently produce a record with
    no access screening at all."""
    floor = 4 if n >= 4 else 1
    cands = [k for k in range(n, floor - 1, -1) if _checkpoint_path(out_dir, k).exists()]
    if not cands:
        raise FileNotFoundError(
            f"cannot resume Stage {n + 1}+: no checkpoint between Stage {floor} and Stage {n} in {out_dir}. "
            f"Stages 5-10 need the state after Stage 4 — run `farmsearch run --stages 1-4` first.")
    # the newest by mtime, never an older file with a higher stage number
    return max(cands, key=lambda k: _checkpoint_path(out_dir, k).stat().st_mtime)


def _grow_context(context, parcels: gpd.GeoDataFrame, cfg: Config):
    """The layer-loading extent: the study polygon's context band plus every
    parcel that will be scored, grown by the frontage search radius so a farm
    straddling the study line still meets its own constraints and its road."""
    sel = parcels["stage1_pass"] if "stage1_pass" in parcels.columns else parcels["in_study_area"]
    if cfg.run.process_all and "in_study_area" in parcels.columns:
        sel = sel | (parcels["in_study_area"] & parcels["is_account"])
    g = parcels.loc[sel.fillna(False).astype(bool)]
    if not len(g):
        return context
    # Only the parcels that actually reach outside the band matter: unioning all
    # 2,576 outlines would node thousands of vertices into the clip geometry for
    # no new area, and every later layer read pays for them.
    inside = np.zeros(len(g), dtype=bool)
    inside[g.sindex.query(context, predicate="contains")] = True
    out = g[~inside]
    if not len(out):
        return context
    grown = unary_union([context, out.geometry.union_all().buffer(ft_to_m(cfg.access.frontage_search_ft))])
    log.info("layer context: study band %.0f km2 -> %.0f km2 covering the %d of %d scored parcels that reach past it",
             context.area / 1e6, grown.area / 1e6, int(len(out)), int(len(g)))
    return grown


def run_pipeline(cfg: Config, stages: Iterable[int] = (1, 2, 3, 4), out_dir: Optional[Path] = None,
                 parcels_raw: Optional[gpd.GeoDataFrame] = None, write: bool = True, resume: bool = False) -> dict:
    """Run the requested stages. Stage 1 always runs unless `resume` is set
    and the first requested stage is later than 1, in which case the state
    saved after the preceding stage (checkpoint_stage{n}.pkl in out_dir) is
    loaded instead and the run continues from there."""
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
    # Layers and neighbour parcels are loaded out to the context buffer so a
    # parcel on the study line still meets its road and its neighbours.
    context = context_geometry(cfg, study)
    s2 = s3 = s4 = None
    resume_from = min(stages) - 1 if (resume and min(stages) > 1) else 0

    if resume_from:
        # ---- Resume: the state after Stage `resume_from` (or the latest earlier one) ----
        # (context is grown to the loaded parcels below, once they are available)
        if resume_from >= 4:
            resume_from = _latest_checkpoint_at_or_before(out_dir, resume_from)
        ck1 = _load_checkpoint(out_dir, 1)
        ck = ck1 if resume_from == 1 else _load_checkpoint(out_dir, resume_from)
        parcels = ck1["parcels"]
        scored = ck["scored"]
        if resume_from >= 4 and "largest_contiguous_reachable_acres" not in scored.columns:
            raise ValueError(f"the Stage {resume_from} checkpoint in {out_dir} has no Stage 4 access columns; "
                             f"re-run `farmsearch run --stages 1-4` before resuming Stages 5-10")
        s2, s3 = ck.get("s2"), ck.get("s3")
        for k, v in ck["summary"].items():
            if (k.startswith("stage") and k != "stages_run") or k == "missing_layers":
                summary[k] = v
        prior = [int(x[5:]) for x in ck["summary"] if x.startswith("stage") and x[5:].isdigit()]
        summary["stages_run"] = sorted(set(prior) | set(stages))
        summary["resumed_from_stage"] = resume_from
        s1 = Stage1Result(parcels=parcels, summary=summary["stage1"])
        context = _grow_context(context, parcels, cfg)
        log.info("resumed from the Stage %d checkpoint: %d parcels, %d scored", resume_from, len(parcels), len(scored))
    else:
        # ---- Stage 1 --------------------------------------------------
        s1 = run_stage1(cfg, study, parcels_raw=parcels_raw, context_geom=context)
        parcels = s1.parcels
        summary["stage1"] = s1.summary
        log.info("Stage 1: %d parcels in study area (+%d context), %d pass",
                 int(parcels["in_study_area"].sum()), int((~parcels["in_study_area"]).sum()), int(parcels["stage1_pass"].sum()))
        if write:
            _writable(_drop_private(parcels)).to_file(out_dir / "parcels_stage1.gpkg", driver="GPKG")
        targets = parcels["stage1_pass"] | (cfg.run.process_all & parcels["in_study_area"] & parcels["is_account"])
        scored = parcels[targets].reset_index(drop=True)
        # A scored parcel may reach well past the fixed context band (a 300-acre farm
        # is ~3,600 ft across). Grow the context so Stages 2-4 load constraints, ROW
        # and slope over all of it, not merely the part near the study line.
        context = _grow_context(context, parcels, cfg)
        if write:
            _drop_checkpoints_after(out_dir, 1)
            _save_checkpoint(out_dir, 1, {"parcels": parcels, "scored": scored, "s2": None, "s3": None,
                                          "summary": {"stage1": summary["stage1"], "missing_layers": []}})
    result = {"study_area": study, "stage1": s1}
    if s2 is not None:
        result["stage2"] = s2
    if s3 is not None:
        result["stage3"] = s3

    # ---- Stage 2 ------------------------------------------------------
    if 2 in stages:
        layers, missing = load_constraint_layers(cfg, context)
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
            enc_rows = [{"account_id": a, "type": t, "acres": round(m2_to_acres(g.area), 2), "geometry": g}
                        for a, d in s2.geoms.items() for t, g in d.items() if g is not None and not g.is_empty]
            if enc_rows:
                _writable(gpd.GeoDataFrame(enc_rows, geometry="geometry", crs=cfg.working_crs)).to_file(out_dir / "encumbrances.gpkg", driver="GPKG")
            _drop_checkpoints_after(out_dir, 2)
            _save_checkpoint(out_dir, 2, {"scored": scored, "s2": s2, "s3": None,
                                          "summary": {k: summary[k] for k in ("stage1", "stage2", "missing_layers")}})
        result["stage2"] = s2

    # ---- Stage 3 ------------------------------------------------------
    if 3 in stages:
        if s2 is None:
            raise ValueError("Stage 3 requires Stage 2")
        sp = SlopeProvider(cfg, context)
        s3 = run_stage3(cfg, scored, s2.geoms, slope_provider=sp, study_geom=context)
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
            _drop_checkpoints_after(out_dir, 3)
            _save_checkpoint(out_dir, 3, {"scored": scored, "s2": s2, "s3": s3,
                                          "summary": {k: summary[k] for k in ("stage1", "stage2", "stage3", "missing_layers")}})
        result["stage3"] = s3

    # ---- Stage 4 ------------------------------------------------------
    if 4 in stages:
        if s3 is None:
            raise ValueError("Stage 4 requires Stage 3")
        rows, missing = load_row_layers(cfg, context)
        summary["missing_layers"] += missing
        # Neighbors: all study-area parcels, carrying the scored attributes for targets
        # Stages 2-3 also MUTATE columns that Stage 1 created (manual_flags), so the
        # scored frame wins for every non-geometry column of a scored row, while
        # unscored rows (blockers, context) keep Stage 1's value. Blocker polygons
        # share an account_id (314 'UNK' rows on the real fabric), so this is done
        # positionally on the merge result, never by re-indexing on account_id.
        gname = parcels.geometry.name
        overlap = [c for c in scored.columns if c in parcels.columns and c not in ("account_id", gname)]
        new_cols = [c for c in scored.columns if c not in parcels.columns]
        merged = parcels.merge(scored[["account_id"] + new_cols], on="account_id", how="left")
        merged = gpd.GeoDataFrame(merged, geometry=gname, crs=parcels.crs)
        mask = merged["account_id"].isin(scored["account_id"]).to_numpy(dtype=bool)
        # Overwrite in place rather than through a suffixed merge: a left join
        # NaN-pads the 130k unscored rows, which would turn every int column of
        # the record into float and every bool into object.
        sc = scored.set_index("account_id")
        scored_rows = merged.index[mask]          # NB `rows` is the ROW layer frame
        for c in overlap:
            merged.loc[scored_rows, c] = sc[c].reindex(merged.loc[scored_rows, "account_id"]).values
        s4 = run_stage4(cfg, merged, pd.Series(mask, index=merged.index), s3.geoms, rows, missing)
        scored = s4.parcels[mask].reset_index(drop=True)
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
            pub = rows[rows["public"]] if "public" in rows.columns else rows
            if len(pub):
                _writable(pub).to_file(out_dir / "row_public.gpkg", driver="GPKG")
            if len(s4.entry_points):
                _writable(s4.entry_points).to_file(out_dir / "entry_points.gpkg", driver="GPKG")
            s4.strips.to_csv(out_dir / "reserve_strips.csv", index=False)
            s4.islands.to_csv(out_dir / "islands.csv", index=False)
            _drop_checkpoints_after(out_dir, 4)
            _save_checkpoint(out_dir, 4, {"scored": scored, "s2": s2, "s3": s3,
                                          "summary": {k: v for k, v in summary.items() if k.startswith("stage") or k == "missing_layers"}})
        result["stage4"] = s4

    def _require_stage4(n: int) -> None:
        """Stages 5-10 read Stage 3's usable geometry and Stage 4's access columns.
        Without them the record would be written with no usable area and no access
        screening at all, silently overwriting a complete earlier run."""
        if s3 is None or "largest_contiguous_reachable_acres" not in scored.columns:
            raise ValueError(f"Stage {n} requires Stages 2-4: run `--stages 1-{n}`, or `--stages {n}-10 --resume` "
                             f"after a run that reached Stage 4")

    def _later_checkpoint(n: int) -> None:
        if write:
            _save_checkpoint(out_dir, n, {"scored": scored, "s2": s2, "s3": s3,
                                          "summary": {k: v for k, v in summary.items() if k.startswith("stage") or k == "missing_layers"}})

    # ---- Stage 5: dischargeable envelope ------------------------------
    s5 = None
    if 5 in stages:
        _require_stage4(5)
        occ = load_occupied_structures(cfg, parcels, context)
        summary["missing_layers"] += occ.missing_layers
        s5 = run_stage5(cfg, scored, s3.geoms, occ)
        scored = s5.parcels
        summary["stage5"] = {
            "layers_missing": occ.missing_layers, "footprints_available": occ.footprints_available,
            "occupied_structures": int(len(occ.structures)), "school_features": int(len(occ.schools)),
            "envelope_acres_total": float(scored["dischargeable_envelope_acres"].fillna(0).sum()),
            "parcels_envelope_below_min": int((scored["dischargeable_envelope_acres"] < cfg.envelope.min_dischargeable_acres).sum()),
            "parcels_envelope_too_short": int((scored["dischargeable_envelope_longest_dim_yards"] < cfg.envelope.min_envelope_length_yards).sum()),
            "safety_buffer_yards": cfg.envelope.safety_buffer_yards, "school_buffer_yards": cfg.envelope.school_buffer_yards,
        }
        if write:
            _writable(s5.envelopes).to_file(out_dir / "envelope.gpkg", driver="GPKG")
            if len(occ.structures):
                _writable(occ.structures).to_file(out_dir / "occupied_structures.gpkg", driver="GPKG")
        result["stage5"] = s5
        _later_checkpoint(5)

    # ---- Stage 6: viewshed ----------------------------------------------
    if 6 in stages:
        _require_stage4(6)
        envelopes = structures = None
        if s5 is not None:
            envelopes, structures = s5.envelopes, s5.structures.structures
        elif resume_from >= 5 and (out_dir / "envelope.gpkg").exists():
            # Stage 6 is the DEM-heavy stage and the likeliest to be interrupted:
            # its inputs are re-read from the Stage 5 outputs rather than redone
            envelopes = gpd.read_file(out_dir / "envelope.gpkg")
            structures = gpd.read_file(out_dir / "occupied_structures.gpkg") if (out_dir / "occupied_structures.gpkg").exists() \
                else gpd.GeoDataFrame({"kind": [], "account_id": [], "owner_key": [], "located_by": []}, geometry=[], crs=cfg.working_crs)
            log.info("Stage 6: re-using envelope.gpkg (%d) and occupied_structures.gpkg (%d) from %s",
                     len(envelopes), len(structures), out_dir)
        if envelopes is None:
            raise ValueError("Stage 6 requires Stage 5: run it in the same run, or resume in a directory holding envelope.gpkg")
        s6 = run_stage6(cfg, scored, envelopes, structures)
        scored = s6.parcels
        summary["stage6"] = {
            "terrain_mode": s6.terrain_mode, "windows_failed": s6.windows_failed,
            "parcels_with_visible_dwellings": int((scored["dwellings_with_line_of_sight"].fillna(0) > 0).sum()),
            "parcels_all_dwellings_shielded": int(scored["viewshed_flags"].map(lambda f: "all_nearby_dwellings_terrain_shielded" in (f or [])).sum()),
            "parcels_with_backstop_candidate": int((scored["candidate_backstop_slopes"] == True).sum()),  # noqa: E712
        }
        result["stage6"] = s6
        _later_checkpoint(6)

    # ---- Stage 7: future encroachment ---------------------------------
    if 7 in stages:
        _require_stage4(7)
        from .stages.stage1_base_filter import load_zoning_layers
        zl = load_zoning_layers(cfg, context)
        fav = load_favorable_layers(cfg, context, summary["missing_layers"])
        layers7, missing = load_encroachment_layers(cfg, context, favorable_layers=fav)
        summary["missing_layers"] += missing
        s7 = run_stage7(cfg, scored, parcels, zl, layers7, missing)
        scored = s7.parcels
        summary["stage7"] = {
            "layers_missing": missing,
            "adjacent_residential_zoning": int((scored["adjacent_residential_zoning_acres"].fillna(0) > 0).sum()),
            "adjacent_planned_sewer": int((scored["adjacent_planned_sewer"] == True).sum()),  # noqa: E712
            "subject_in_pfa": int((scored["subject_in_pfa"] == True).sum()),  # noqa: E712
            "adjacent_permanently_eased": int((scored["adjacent_permanently_eased_acres"].fillna(0) > 0).sum()),
            "median_units_within_radius": (float(scored["approved_unbuilt_units_within_2mi"].median())
                                           if scored["approved_unbuilt_units_within_2mi"].notna().any() else None),
            "parcels_without_pipeline_coverage": int(scored["approved_unbuilt_units_within_2mi"].isna().sum()),
            "pipeline_radius_ft": cfg.encroachment.pipeline_radius_ft,
        }
        result["stage7"] = s7
        _later_checkpoint(7)

    # ---- Stage 8: transmission and industrial exposure ----------------
    if 8 in stages:
        _require_stage4(8)
        layers8, missing = load_transmission_layers(cfg, context)
        summary["missing_layers"] += missing
        los = None
        try:
            from .terrain import line_of_sight_factory
            los = line_of_sight_factory(cfg)
        except Exception as ex:  # noqa: BLE001 - LOS is optional
            log.warning("line of sight unavailable: %s", ex)
        s8 = run_stage8(cfg, scored, layers8, missing, line_of_sight=los)
        scored = s8.parcels
        tiers = scored["mprp_tier"].value_counts(dropna=False).to_dict()
        summary["stage8"] = {
            "layers_missing": missing,
            "routes_loaded": [f"{n} ({v})" for n, v, _ in layers8.routes],
            "mprp_tier_counts": {str(k): int(v) for k, v in tiers.items()},
            "near_existing_hv": int(scored["transmission_flags"].map(lambda f: "near_existing_hv_transmission_corridor" in (f or [])).sum()),
            "near_substation": int(scored["transmission_flags"].map(lambda f: "near_substation" in (f or [])).sum()),
            "near_data_center": int(scored["transmission_flags"].map(lambda f: "near_data_center_development" in (f or [])).sum()),
            "status_note": cfg.transmission.status_note,
        }
        result["stage8"] = s8
        _later_checkpoint(8)

    # ---- Stage 9: valuation ---------------------------------------------
    if 9 in stages:
        _require_stage4(9)
        comp_reach = context.buffer(ft_to_m(max((s.fetch_margin_ft or 0) for s in cfg.valuation.sales_layers)
                                             if cfg.valuation.sales_layers else 0))
        fav_layers = load_favorable_layers(cfg, comp_reach, summary["missing_layers"])
        fav_geoms = [unary_union(list(g.geometry.values)) for g in fav_layers.values() if len(g)]
        fav_union = unary_union(fav_geoms) if fav_geoms else None
        # the sales layer is fetched miles beyond the study polygon so the market,
        # not the boundary, defines the band: clip the comps to the same reach
        comps = build_comps(cfg, comp_reach, parcels, fav_union)
        summary["missing_layers"] += comps.missing_layers
        s9 = run_stage9(cfg, scored, comps)
        scored = s9.parcels
        summary["stage9"] = {
            "layers_missing": comps.missing_layers, "comps": int(len(comps.comps)),
            "bands": {f"{k[0]}/{k[1]}": v for k, v in comps.bands.items()},
            "parcels_valued": int(scored["est_market_value"].notna().sum()),
            "median_est_per_acre": (float(scored["est_per_acre"].median()) if scored["est_per_acre"].notna().any() else None),
            "above_price_ceiling": int(scored["valuation_flags"].map(lambda f: "estimated_value_above_price_ceiling" in (f or [])).sum()),
        }
        if write and len(comps.comps):
            comps.comps.to_csv(out_dir / "valuation_comps.csv", index=False)
        result["stage9"] = s9
        _later_checkpoint(9)

    # ---- Stage 10: commute (reported, never a filter) ------------------
    if 10 in stages:
        _require_stage4(10)
        pipe = None
        if cfg.encroachment.pipeline_layers:
            try:
                l7, _m = load_encroachment_layers(cfg, context.buffer(ft_to_m(cfg.commute.corridor_search_ft)))
                pipe = l7.pipeline
            except Exception as ex:  # noqa: BLE001
                log.warning("pipeline layers unavailable for Stage 10: %s", ex)
        layers10 = load_commute_layers(cfg, context, pipeline=pipe)
        summary["missing_layers"] += layers10.missing_layers
        ep = s4.entry_points if s4 is not None else None
        if ep is None and (out_dir / "entry_points.gpkg").exists():
            ep = gpd.read_file(out_dir / "entry_points.gpkg")
        s10 = run_stage10(cfg, scored, ep, layers10)
        scored = s10.parcels
        rr = scored["route_redundancy"].value_counts(dropna=False).to_dict()
        summary["stage10"] = {
            "engine": s10.engine, "routed": s10.routed, "layers_missing": layers10.missing_layers,
            "destinations": [d.column for d in cfg.commute.destinations],
            "median_minutes": {d.column: (float(scored[d.column].median()) if scored[d.column].notna().any() else None) for d in cfg.commute.destinations},
            "route_redundancy": {str(k): int(v) for k, v in rr.items()},
            "median_durability": (float(scored["corridor_durability_score"].median()) if scored["corridor_durability_score"].notna().any() else None),
        }
        result["stage10"] = s10

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
        try:
            from .deliverables import owner_list, rank_shortlist
            short, excl = rank_shortlist(scored, cfg)
            short.to_csv(out_dir / "shortlist.csv", index=False)
            excl.to_csv(out_dir / "shortlist_excluded.csv", index=False)
            owner_list(scored, short).to_csv(out_dir / "owner_list.csv", index=False)
            summary["shortlist"] = {"listed": int(len(short)), "excluded": int(len(excl)),
                                    "owners": int(owner_list(scored, short).shape[0]), "top_n": cfg.shortlist.top_n}
        except Exception as ex:  # noqa: BLE001
            log.warning("shortlist / owner list not written: %s", ex)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
        (out_dir / "summary.md").write_text(render_summary(summary))
    return result


def assemble_record(scored: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    scored = scored.copy()
    # A permit can only ADD crossings, so neither permitted figure may sit below
    # its strict counterpart. Removing a constraint occasionally re-cuts a narrow
    # neck differently, which would otherwise leave the record contradicting
    # itself (one parcel in 2,576 on the real data).
    for strict_c, permitted_c in (("largest_contiguous_reachable_acres", "largest_reachable_if_crossings_permitted_acres"),
                                  ("reachable_usable_acres", "reachable_if_crossings_permitted_acres")):
        if strict_c in scored.columns and permitted_c in scored.columns:
            a = pd.to_numeric(scored[strict_c], errors="coerce")
            b = pd.to_numeric(scored[permitted_c], errors="coerce")
            below = (b < a - 0.005) & a.notna() & b.notna()
            if below.any():
                log.info("%d parcels where permitting crossings re-cut a narrow neck: %s held at %s",
                         int(below.sum()), permitted_c, strict_c)
                scored.loc[below, permitted_c] = a[below]
    flag_cols = [c for c in ("manual_flags", "encumbrance_flags", "access_flags", "encroachment_flags",
                             "transmission_flags", "envelope_flags", "valuation_flags", "commute_flags") if c in scored.columns]
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
    if "stage5" in s:
        s5 = s["stage5"]
        L += ["", "## Stage 5 — dischargeable envelope", "",
              f"occupied structures {s5['occupied_structures']} · school features {s5['school_features']} · "
              f"envelope {s5['envelope_acres_total']:.0f} ac total · below minimum {s5['parcels_envelope_below_min']} · too short {s5['parcels_envelope_too_short']} "
              f"(safety {s5['safety_buffer_yards']:.0f} yd, school {s5['school_buffer_yards']:.0f} yd)"]
    if "stage6" in s:
        s6 = s["stage6"]
        L += ["", "## Stage 6 — viewshed", "",
              f"terrain {s6['terrain_mode']} (windows failed {s6['windows_failed']}) · parcels with a visible dwelling {s6['parcels_with_visible_dwellings']} · "
              f"all nearby dwellings shielded {s6['parcels_all_dwellings_shielded']} · backstop candidates {s6['parcels_with_backstop_candidate']}"]
    if "stage7" in s:
        s7 = s["stage7"]
        L += ["", "## Stage 7 — future encroachment", "",
              f"adjacent residential zoning {s7['adjacent_residential_zoning']} · adjacent planned sewer {s7['adjacent_planned_sewer']} · "
              f"inside PFA {s7['subject_in_pfa']} · adjacent permanently eased {s7['adjacent_permanently_eased']} · "
              f"median approved-unbuilt units within {s7['pipeline_radius_ft']:.0f} ft: {s7['median_units_within_radius']}"]
        if s7.get("layers_missing"):
            L.append(f"layers missing: {', '.join(s7['layers_missing'])}")
    if "stage8" in s:
        s8 = s["stage8"]
        L += ["", "## Stage 8 — transmission and industrial exposure", "",
              f"routes: {', '.join(s8['routes_loaded']) or 'none'} · MPRP tiers {s8['mprp_tier_counts']} · "
              f"near existing HV {s8['near_existing_hv']} · near substation {s8['near_substation']} · near data center {s8['near_data_center']}"]
        if s8.get("status_note"):
            L.append(f"MPRP status (re-verify at run time): {s8['status_note']}")
        if s8.get("layers_missing"):
            L.append(f"layers missing: {', '.join(s8['layers_missing'])}")
    if "stage9" in s:
        s9 = s["stage9"]
        bands = "; ".join(f"{k}: n={v['n']} ${(v['median'] or 0):,.0f}/ac" for k, v in s9["bands"].items() if k.startswith("ALL"))
        L += ["", "## Stage 9 — valuation", "",
              f"{s9['comps']} arms-length agricultural comps · {bands or 'no bands'} · parcels valued {s9['parcels_valued']} · "
              f"median est ${(s9['median_est_per_acre'] or 0):,.0f}/ac · above price ceiling {s9['above_price_ceiling']}"]
        if s9.get("layers_missing"):
            L.append(f"layers missing: {', '.join(s9['layers_missing'])}")
    if "stage10" in s:
        s10 = s["stage10"]
        meds = ", ".join(f"{k.replace('commute_', '').replace('_peak_min', '')} {v:.0f} min" for k, v in s10["median_minutes"].items() if v is not None)
        L += ["", "## Stage 10 — commute (reported, never a filter)", "",
              f"engine {s10['engine']} · routed {s10['routed']} · median peak {meds or 'n/a'} · redundancy {s10['route_redundancy']} · "
              f"median corridor durability {s10['median_durability']}"]
        if s10.get("layers_missing"):
            L.append(f"layers missing: {', '.join(s10['layers_missing'])}")
    if "shortlist" in s:
        sh = s["shortlist"]
        L += ["", "## Shortlist", "", f"{sh['listed']} parcels listed (top {sh['top_n']}), {sh['excluded']} excluded by hard rules, "
              f"{sh['owners']} distinct owners across all scored parcels (owner_list.csv)"]
    L += ["", "## What this pipeline cannot determine", ""]
    L += [f"- {x}" for x in s["cannot_determine"]]
    return "\n".join(L) + "\n"
