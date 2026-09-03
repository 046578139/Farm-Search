"""Stage 1 — Base filter.

Clip parcels to the study area, filter to acreage >= acreage_min and
agricultural zoning per each county's own code mapping. Every parcel in the
study area is RETAINED with flags; nothing is dropped. Downstream stages run on
stage1_pass == True by default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from ..config import ZoningSpec, Config, ConfigError
from ..io.loaders import LayerNotAvailable, clean_geometries, read_layer
from ..io.schema import SchemaError, apply_schema, verify_parcels_schema
from ..accounts import non_account_mask, owner_type_from_exemption
from ..owners import classify_owner, join_address, owner_key
from ..units import ACRE_M2, m2_to_acres

log = logging.getLogger(__name__)

ADDRESS_FIELDS = ["owner_addr_line1", "owner_addr_line2", "owner_city", "owner_state", "owner_zip"]


@dataclass
class Stage1Result:
    parcels: gpd.GeoDataFrame
    summary: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
def load_parcels(cfg: Config, study_geom: BaseGeometry, parcels_raw: Optional[gpd.GeoDataFrame] = None) -> gpd.GeoDataFrame:
    """Read parcels, verify the schema against the real field list, rename to
    canonical names, dissolve multi-row accounts into one geometry each."""
    if parcels_raw is None:
        res = verify_parcels_schema(cfg)
        log.info("\n%s", res.report())
        gdf = read_layer(cfg.parcels.source, cfg.working_crs, study_geom, clip_mode="intersects")
    else:
        res = verify_parcels_schema(cfg, actual_fields=list(parcels_raw.columns))
        gdf = parcels_raw.to_crs(cfg.working_crs)
        gdf = clean_geometries(gdf)
        idx = gdf.sindex.query(study_geom, predicate="intersects")
        gdf = gdf.iloc[sorted(idx)].reset_index(drop=True)
    gdf = apply_schema(gdf, res)
    # Non-parcel polygons: null / placeholder account IDs ('ROW', 'WATER',
    # 'RAILROAD', 'UNK', condominium common elements, ...). They are not
    # accounts and must not become "parcels" or "neighbouring parcels".
    non_parcel = non_account_mask(gdf["account_id"],
                                  list(cfg.parcels.row_account_ids) + list(cfg.parcels.non_parcel_account_ids),
                                  cfg.parcels.account_id_regex)
    # Named placeholders (RAILROAD, WATER, GCE, PRIVATE ROW, UNK, ...) are not
    # accounts, but they are real polygons that can sit between a farm and
    # the road: keep them as NON-ACCOUNT rows so Stage 4 sees them as foreign
    # blockers. Road right-of-way placeholders and null IDs are dropped (the
    # ROW polygons come back in as a ROW layer).
    acct_str = gdf["account_id"].astype("string").fillna("").str.strip()
    row_ids = {x.upper() for x in cfg.parcels.row_account_ids}
    blocker = non_parcel & (acct_str != "") & ~acct_str.str.upper().isin(row_ids) & ~acct_str.str.upper().isin({"NONE", "NAN", "NULL", "<NULL>"})
    drop = non_parcel & ~blocker
    # Unlinked polygons (an account-shaped id with no SDAT record): the MD
    # layer marks them with a null PTYPE.
    unlinked = pd.Series(False, index=gdf.index)
    for f in cfg.parcels.require_non_null:
        if f in gdf.columns:
            unlinked |= gdf[f].isna() & ~non_parcel
    if drop.any() or unlinked.any() or blocker.any():
        seen = gdf.loc[non_parcel, "account_id"].astype("string").fillna("<null>").value_counts().head(12).to_dict()
        log.info("non-parcel polygons: %d dropped (ROW / null id), %d unlinked (null %s) dropped, %d kept as blockers only; ids: %s",
                 int(drop.sum()), int(unlinked.sum()), cfg.parcels.require_non_null, int(blocker.sum()), seen)
    n_non_parcel = int(drop.sum())
    n_unlinked = int(unlinked.sum())
    gdf = gdf[~drop & ~unlinked].copy()
    gdf["is_account"] = ~blocker[gdf.index].values
    gdf["account_id"] = gdf["account_id"].astype(str).str.strip()
    # Keep only areal geometry
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    # Dissolve multi-row accounts (an account can be several polygons).
    # Placeholder blockers (UNK, GCE, WATER, RAILROAD, ...) share an id but
    # are unrelated polygons scattered over the county: never dissolved, one
    # row each, so Stage 4 meets them one polygon at a time.
    gdf["sdat_acreage_inconsistent"] = False
    gdf["sdat_acreage_summed"] = False
    blockers = gdf[~gdf["is_account"].values].copy()
    gdf = gdf[gdf["is_account"].values].copy()
    dup = gdf["account_id"].duplicated(keep=False)
    if dup.any():
        log.info("dissolving %d rows belonging to multi-polygon accounts", int(dup.sum()))
        per_acct = None
        if "acreage_sdat" in gdf.columns:
            num = pd.to_numeric(gdf["acreage_sdat"], errors="coerce")
            per_acct = num.groupby(gdf["account_id"]).agg(["first", "sum", "nunique", "size"])
        attrs = gdf.drop(columns=[gdf.geometry.name]).groupby("account_id", sort=False).first()
        geoms = gdf.groupby("account_id", sort=False)[gdf.geometry.name].agg(lambda s: s.union_all())
        gdf = gpd.GeoDataFrame(attrs.join(geoms), geometry=gdf.geometry.name, crs=gdf.crs).reset_index()
        if per_acct is not None:
            # SDAT acreage is an account attribute and should repeat on every
            # row of an account. Some sources carry per-polygon acreage instead.
            # Decide per account by comparing both readings with the dissolved
            # geometry: if the row SUM matches the geometry better than the
            # repeated value does, the values were per-polygon -> use the sum.
            # If the values differ across rows, trust neither -> geometry.
            thr = cfg.parcels.acreage_disagreement_pct / 100.0
            geom_ac = gdf.geometry.area / ACRE_M2
            for i, acct in enumerate(gdf["account_id"]):
                if acct not in per_acct.index or per_acct.at[acct, "size"] < 2:
                    continue
                first, total = per_acct.at[acct, "first"], per_acct.at[acct, "sum"]
                g = max(float(geom_ac.iloc[i]), 1e-6)
                if per_acct.at[acct, "nunique"] > 1:
                    gdf.at[i, "sdat_acreage_inconsistent"] = True
                elif pd.notna(first) and abs(total - g) / g < abs(first - g) / g and abs(total - g) / g <= thr:
                    gdf.at[i, "acreage_sdat"] = total
                    gdf.at[i, "sdat_acreage_summed"] = True
    if len(blockers):
        gdf = gpd.GeoDataFrame(pd.concat([gdf, blockers], ignore_index=True), geometry=gdf.geometry.name, crs=gdf.crs)
    gdf = gdf.reset_index(drop=True)
    gdf.attrs["non_parcel_polygons_excluded"] = n_non_parcel   # set last: constructors above drop attrs
    gdf.attrs["unlinked_polygons_excluded"] = n_unlinked
    return gdf


def load_zoning_layers(cfg: Config, study_geom: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    """Keyed by the county name for the primary layer (the historical key)
    and by the spec's source name for fill layers."""
    out = {}
    for z in cfg.zoning:
        try:
            g = read_layer(z.source, cfg.working_crs, study_geom, clip_mode="intersects")
        except LayerNotAvailable as e:
            log.warning("zoning layer %s for %s unavailable: %s", z.source.name, z.county, e)
            continue
        out[z.county if z.role == "primary" else z.source.name] = g
    return out


# ----------------------------------------------------------------------------
def attribute_owners(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Owner name, mailing address, entity type and collapse key.

    The public Maryland parcel layer publishes the owner's MAILING ADDRESS but
    not the owner's NAME (owner_name is optional in the schema map). Without a
    name, owner_type is "unknown", owner_key and the Stage 4 same-owner tests
    fall back to the normalized mailing address, and the parcel is flagged
    owner_name_unavailable_lookup_sdat so the shortlist gets a name lookup.
    """
    gdf = gdf.copy()
    if "owner_name" not in gdf.columns:
        gdf["owner_name"] = None
    name2 = gdf["owner_name2"] if "owner_name2" in gdf.columns else None
    full = gdf["owner_name"].astype("string").fillna("")
    if name2 is not None:
        n2 = name2.astype("string").fillna("").str.strip()
        full = (full + np.where(n2 != "", " & " + n2, "")).astype("string")
    gdf["owner_name"] = full.str.strip()
    # Vectorized join of the address pieces (join_address row-by-row is slow on 200k rows)
    addr = None
    for f in ADDRESS_FIELDS:
        if f not in gdf.columns:
            continue
        col = gdf[f].astype("string").fillna("").str.strip()
        col = col.where(col.str.lower() != "nan", "")
        addr = col if addr is None else (addr + " " + col)
    gdf["owner_mailing_address"] = (addr.str.replace(r"\s+", " ", regex=True).str.strip() if addr is not None
                                    else pd.Series("", index=gdf.index, dtype="string")).astype(str)
    gdf["owner_type"] = gdf["owner_name"].map(classify_owner)
    gdf["owner_type_basis"] = np.where(gdf["owner_type"] != "unknown", "name", "none")
    # No name: SDAT's exemption class still says who holds exempt land
    # (state / county / municipal / federal -> government, nonprofit, church).
    if "exempt_class_desc" in gdf.columns:
        from_ex = gdf["exempt_class_desc"].map(owner_type_from_exemption)
        use = (gdf["owner_type"] == "unknown") & from_ex.notna()
        gdf.loc[use, "owner_type"] = from_ex[use]
        gdf.loc[use, "owner_type_basis"] = "exemption_class"
    keys = [owner_key(n, a) for n, a in zip(gdf["owner_name"], gdf["owner_mailing_address"])]
    # No name AND no mailing address: never collapse those parcels into one
    # owner ('|'); key them by account instead and count them.
    keys = [k if k != "|" else f"acct:{acct}" for k, acct in zip(keys, gdf["account_id"])]
    gdf["owner_key"] = keys
    gdf["owner_key_available"] = ~gdf["owner_key"].str.startswith("acct:")
    gdf["owner_name_available"] = gdf["owner_name"].astype(str).str.strip().ne("")
    # Deed reference: parcels conveyed by the same instrument share an owner
    # even when no name is published (owners_match treats an identical
    # deed liber/folio as a match, alongside name and address).
    if "deed_liber" in gdf.columns and "deed_folio" in gdf.columns:
        lib = gdf["deed_liber"].astype("string").fillna("").str.strip().str.lstrip("0")
        fol = gdf["deed_folio"].astype("string").fillna("").str.strip().str.lstrip("0")
        gdf["deed_ref"] = np.where((lib != "") & (fol != ""), lib + "/" + fol, None)
    else:
        gdf["deed_ref"] = None
    return gdf


def attribute_acreage(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    gdf["gross_acres_geom"] = gdf.geometry.area.map(m2_to_acres).round(2)
    sdat = pd.to_numeric(gdf["acreage_sdat"], errors="coerce") if "acreage_sdat" in gdf.columns else pd.Series(np.nan, index=gdf.index)
    if "sdat_acreage_inconsistent" in gdf.columns:
        sdat = sdat.where(~gdf["sdat_acreage_inconsistent"].astype(bool), np.nan)
    gdf["gross_acres_sdat"] = sdat.round(2)
    if cfg.parcels.acreage_source == "sdat":
        gdf["gross_acres"] = np.where(sdat > 0, sdat, gdf["gross_acres_geom"]).round(2)
        gdf["acreage_basis"] = np.where(sdat > 0, "sdat", "geometry")
    else:
        gdf["gross_acres"] = gdf["gross_acres_geom"]
        gdf["acreage_basis"] = "geometry"
    diff = (sdat - gdf["gross_acres_geom"]).abs() / gdf["gross_acres_geom"].clip(lower=0.01) * 100
    gdf["acreage_disagreement_pct"] = diff.round(1)
    gdf["acreage_disagrees"] = (sdat > 0) & (diff > cfg.parcels.acreage_disagreement_pct)
    ok = gdf["gross_acres"] >= cfg.acreage_min
    if cfg.acreage_max is not None:
        ok &= gdf["gross_acres"] <= cfg.acreage_max
    gdf["meets_acreage"] = ok
    return gdf


def attribute_county(gdf: gpd.GeoDataFrame, cfg: Config) -> gpd.GeoDataFrame:
    """An unmapped jurisdiction inside the study area is a configuration error.
    The context band necessarily reaches into neighbouring counties (Montgomery,
    Howard): those rows are never scored or counted, so they keep county=None
    instead of aborting the run."""
    gdf = gdf.copy()
    codes = gdf["county_code"].astype(str).str.strip()
    scored_rows = gdf["in_study_area"].astype(bool) if "in_study_area" in gdf.columns else pd.Series(True, index=gdf.index)
    unknown = sorted(set(codes[scored_rows].unique()) - set(cfg.counties))
    if unknown:
        raise ConfigError(f"jurisdiction codes in the data are not listed under `counties:` in the config: {unknown}. "
                          f"Known: {list(cfg.counties)}. Verify the county field ({cfg.parcels.schema_path}) and mapping.")
    outside = sorted(set(codes[~scored_rows].unique()) - set(cfg.counties))
    if outside:
        log.info("context rows from jurisdictions outside `counties:` (kept as neighbours only): %s", outside)
    gdf["county_code"] = codes
    gdf["county"] = codes.map(cfg.counties)
    return gdf


def attribute_study_area(gdf: gpd.GeoDataFrame, study_geom: BaseGeometry, mode: str) -> gpd.GeoDataFrame:
    """Share of each parcel inside the study polygon.

    The study polygon is a detailed county boundary (tens of thousands of
    vertices); intersecting every parcel with it is the slowest step of
    Stage 1 by far. Parcels entirely inside (the vast majority) or entirely
    outside need no intersection at all, so only boundary-straddling parcels
    are intersected."""
    gdf = gdf.copy()
    area = gdf.geometry.area
    inter = pd.Series(0.0, index=gdf.index)
    n = len(gdf)
    if n:
        tree = gdf.sindex
        inside = np.zeros(n, dtype=bool)
        inside[tree.query(study_geom, predicate="contains")] = True
        touching = np.zeros(n, dtype=bool)
        touching[tree.query(study_geom, predicate="intersects")] = True
        straddle = touching & ~inside
        inter[inside] = area[inside]
        if straddle.any():
            inter[straddle] = gdf.geometry[straddle].intersection(study_geom).area
    gdf["in_study_area_pct"] = (100 * inter / area).round(1)
    if mode == "intersects":
        gdf["in_study_area"] = inter > 0
    elif mode == "centroid":
        gdf["in_study_area"] = gdf.geometry.centroid.within(study_geom)
    else:
        gdf["in_study_area"] = gdf["in_study_area_pct"] >= 99.9
    return gdf


def _zoning_areas(pgeoms, layer: gpd.GeoDataFrame, spec: ZoningSpec, county: str) -> dict[int, dict[str, float]]:
    """Per parcel (positional index into pgeoms): zoning code -> intersected area."""
    if spec.code_field not in layer.columns:
        raise SchemaError(f"zoning {county} ({spec.source.name}): field {spec.code_field!r} not in layer; fields: {list(layer.columns)}")
    pairs = layer.sindex.query(pgeoms, predicate="intersects")
    by_parcel: dict[int, list[int]] = {}
    for a, b in zip(pairs[0], pairs[1]):
        by_parcel.setdefault(int(a), []).append(int(b))
    raw_codes = layer[spec.code_field]
    # A zoning polygon with no code (seen once in the live Frederick layer:
    # a 0.003 ac sliver) carries no information; it must not become the
    # unmapped code "None" and abort the run.
    has_code = (raw_codes.notna() & (raw_codes.astype(str).str.strip() != "")).values
    codes_col = raw_codes.astype(str).str.strip().values
    n_blank = int((~has_code).sum())
    if n_blank:
        log.warning("zoning %s (%s): %d polygons with a null/blank %s ignored", county, spec.source.name, n_blank, spec.code_field)
    out: dict[int, dict[str, float]] = {}
    for a, blist in by_parcel.items():
        pg = pgeoms[a]
        areas: dict[str, float] = {}
        for b in blist:
            if not has_code[b]:
                continue
            x = pg.intersection(layer.geometry.values[b])
            if x.is_empty:
                continue
            areas[codes_col[b]] = areas.get(codes_col[b], 0.0) + x.area
        if areas:
            out[a] = areas
    return out


def assign_zoning(gdf: gpd.GeoDataFrame, zoning_layers: dict[str, gpd.GeoDataFrame], cfg: Config,
                  scope_mask: Optional[pd.Series] = None) -> gpd.GeoDataFrame:
    """Majority-area zoning district per parcel, mapped through each county's
    is_agricultural table. Split-zoned parcels report zoning_ag_pct.

    A county's primary layer decides first. Where it maps the parcel to
    "unknown" (a MUN / TOWN municipal placeholder) or does not cover it, the
    fill layers (municipal zoning) are consulted in config order; the first
    that covers the parcel with a code that is not itself "unknown" wins and
    zoning_source records which layer answered."""
    gdf = gdf.copy()
    gdf["zoning"] = None
    gdf["zoning_ag_pct"] = np.nan
    gdf["is_agricultural"] = None
    gdf["zoning_codes_all"] = None
    gdf["zoning_source"] = None
    gdf["zoning_unmapped"] = False
    gdf["zoning_layer_missing"] = False
    scope = scope_mask if scope_mask is not None else pd.Series(True, index=gdf.index)
    unmapped_seen: dict[str, set] = {}
    for county in sorted(gdf["county"].dropna().unique()):
        sel = (gdf["county"] == county) & scope
        if not sel.any():
            continue
        specs = cfg.zoning_specs_for(county)
        primary = cfg.zoning_for(county)
        if primary is None or zoning_layers.get(county) is None or zoning_layers.get(county).empty:
            gdf.loc[sel, "zoning_layer_missing"] = True
            continue
        pidx = np.flatnonzero(sel.values)
        pgeoms = gdf.geometry.values[pidx]
        undecided = set(range(len(pidx)))          # parcels still without a decisive answer
        for spec in specs:
            if not undecided:
                break
            key = county if spec.role == "primary" else spec.source.name
            layer = zoning_layers.get(key)
            if layer is None or layer.empty:
                if spec.role != "primary":
                    log.warning("zoning fill layer %s for %s not loaded; municipal holes stay unknown", spec.source.name, county)
                continue
            spec.load_mapping()
            if not spec.code_field:
                raise ConfigError(f"zoning {county} ({spec.source.name}): code_field not set (run `farmsearch zoning-domains`)")
            sub = sorted(undecided)
            areas_by = _zoning_areas(pgeoms[sub], layer, spec, county)
            for a_sub, areas in areas_by.items():
                a = sub[a_sub]
                total = sum(areas.values())
                major = max(areas, key=areas.get)
                mm = spec.codes.get(major)
                major_unknown = mm is not None and mm.get("is_agricultural") == "unknown"
                already = gdf.at[gdf.index[pidx[a]], "zoning"] is not None
                if major_unknown and spec.role == "primary":
                    # Record the placeholder; a fill layer may still answer.
                    pass
                elif major_unknown and already:
                    continue                      # a fill layer's own "unknown" adds nothing
                ag_area = 0.0
                unmapped = False
                for code, ar in areas.items():
                    m = spec.codes.get(code)
                    if m is None or m.get("is_agricultural") is None:
                        unmapped = True
                        unmapped_seen.setdefault(f"{county}/{spec.source.name}", set()).add(code)
                    elif m["is_agricultural"] is True:
                        ag_area += ar
                i = gdf.index[pidx[a]]
                gdf.at[i, "zoning"] = major
                gdf.at[i, "zoning_codes_all"] = ";".join(f"{c}:{100*ar/total:.0f}%" for c, ar in sorted(areas.items(), key=lambda kv: -kv[1]))
                gdf.at[i, "zoning_ag_pct"] = round(100 * ag_area / total, 1)
                gdf.at[i, "zoning_source"] = spec.source.name
                if mm is None or mm.get("is_agricultural") is None:
                    gdf.at[i, "zoning_unmapped"] = True
                    gdf.at[i, "is_agricultural"] = None
                    undecided.discard(a)
                elif major_unknown:
                    gdf.at[i, "is_agricultural"] = None          # known-unknown (municipal hole): retained, flagged
                    gdf.at[i, "zoning_unmapped"] = False
                else:
                    gdf.at[i, "is_agricultural"] = bool(mm["is_agricultural"])
                    gdf.at[i, "zoning_unmapped"] = False
                    undecided.discard(a)
    if unmapped_seen:
        msg = "; ".join(f"{c}: {sorted(v)}" for c, v in unmapped_seen.items())
        if cfg.on_unmapped_zoning == "error":
            raise ConfigError(f"zoning codes present in the data but not mapped to is_agricultural: {msg}. "
                              f"Run `farmsearch zoning-domains` and fill in config/zoning/<county>.yaml.")
        log.warning("unmapped zoning codes (parcels retained, flagged): %s", msg)
    return gdf


# ----------------------------------------------------------------------------
def run_stage1(cfg: Config, study_geom: BaseGeometry, parcels_raw: Optional[gpd.GeoDataFrame] = None,
               zoning_layers: Optional[dict[str, gpd.GeoDataFrame]] = None,
               context_geom: Optional[BaseGeometry] = None) -> Stage1Result:
    """context_geom: the study polygon grown by the context buffer. Parcels
    inside it but outside the study polygon are kept as CONTEXT rows (a
    neighbour, a road-side lot across the study line) with
    stage1_pass_reason = outside_study_area; they are never scored or
    counted."""
    load_geom = context_geom if context_geom is not None else study_geom
    gdf = load_parcels(cfg, load_geom, parcels_raw)
    n_loaded = len(gdf)
    excluded = gdf.attrs.get("non_parcel_polygons_excluded", 0)
    unlinked = gdf.attrs.get("unlinked_polygons_excluded", 0)
    gdf = attribute_study_area(gdf, study_geom, cfg.study_area_selection)
    if context_geom is not None:
        n = len(gdf)
        in_ctx = np.zeros(n, dtype=bool)
        if n:
            in_ctx[gdf.sindex.query(context_geom, predicate="intersects")] = True
        gdf["in_context_area"] = in_ctx
        gdf = gdf[gdf["in_study_area"] | gdf["in_context_area"]].reset_index(drop=True)
    else:
        gdf = gdf[gdf["in_study_area"]].reset_index(drop=True)
        gdf["in_context_area"] = True
    gdf = attribute_county(gdf, cfg)
    gdf = attribute_owners(gdf)
    gdf = attribute_acreage(gdf, cfg)
    if zoning_layers is None:
        # the context geometry, so a parcel straddling the study line is covered by
        # zoning over its whole area (a partial cover flips the majority code)
        zoning_layers = load_zoning_layers(cfg, load_geom)
    if "is_account" not in gdf.columns:
        gdf["is_account"] = True
    # context rows are never scored: an unmapped code out there must not abort the run
    gdf = assign_zoning(gdf, zoning_layers, cfg,
                        scope_mask=gdf["meets_acreage"] & gdf["is_account"] & gdf["in_study_area"].astype(bool))

    flags = [[] for _ in range(len(gdf))]
    for i, (dis, unm, miss, inc) in enumerate(zip(gdf["acreage_disagrees"], gdf["zoning_unmapped"],
                                                   gdf["zoning_layer_missing"], gdf["sdat_acreage_inconsistent"])):
        if not gdf["owner_name_available"].iloc[i]:
            flags[i].append("owner_name_unavailable_lookup_sdat")
        if inc:
            flags[i].append("sdat_acreage_inconsistent_across_rows")
        if gdf["sdat_acreage_summed"].iloc[i]:
            flags[i].append("sdat_acreage_summed_across_rows")
        if dis:
            flags[i].append("sdat_acreage_disagrees_with_geometry")
        if unm:
            flags[i].append("zoning_unmapped")
        if miss:
            flags[i].append("zoning_layer_missing")
    # Constraint, ROW and slope layers are loaded out to the context band; a parcel
    # reaching past it keeps whatever those layers cover, so say so.
    if context_geom is not None:
        inside_ctx = np.zeros(len(gdf), dtype=bool)
        if len(gdf):
            inside_ctx[gdf.sindex.query(context_geom, predicate="contains")] = True
        beyond = (gdf["is_account"] & gdf["in_study_area"]).values & ~inside_ctx
        for i in np.flatnonzero(beyond):
            flags[i].append("extends_beyond_layer_context_verify_constraints_there")
        if beyond.any():
            log.info("%d parcels extend beyond the context band: their far end is covered only as far as each layer was fetched",
                     int(beyond.sum()))
    gdf["manual_flags"] = flags
    ag = gdf["is_agricultural"]
    # Unknown zoning (layer missing / unmapped under flag mode) is retained: never auto-delete.
    inside = gdf["in_study_area"].astype(bool)
    gdf["stage1_pass"] = inside & gdf["is_account"] & gdf["meets_acreage"] & (ag.isna() | (ag == True))  # noqa: E712
    gdf["stage1_pass_reason"] = np.select(
        [~inside, ~gdf["is_account"], ~gdf["meets_acreage"], ag == False, ag.isna()],  # noqa: E712
        ["outside_study_area", "non_parcel_polygon",
         "below_acreage_min" if cfg.acreage_max is None else "outside_acreage_band",
         "not_agricultural_zoning", "zoning_unknown_retained"],
        default="pass")

    summary = summarize_stage1(gdf, n_loaded, cfg)
    summary["non_parcel_polygons_excluded"] = int(excluded)
    summary["unlinked_polygons_excluded"] = int(unlinked)
    summary["blocker_polygons_retained"] = int((~gdf["is_account"]).sum())
    summary["context_parcels_retained"] = int((gdf["is_account"] & ~gdf["in_study_area"]).sum())
    acc = gdf[gdf["is_account"] & gdf["in_study_area"]]   # blockers and context rows are never counted
    summary["owner_name_available_pct"] = round(100 * float(acc["owner_name_available"].mean()), 1) if len(acc) else None
    summary["owner_key_unavailable"] = int((~acc["owner_key_available"]).sum())
    return Stage1Result(parcels=gdf, summary=summary)


def summarize_stage1(gdf: gpd.GeoDataFrame, n_loaded: int, cfg: Config) -> dict:
    per_county = {}
    counted = gdf["is_account"] if "is_account" in gdf.columns else pd.Series(True, index=gdf.index)
    if "in_study_area" in gdf.columns:
        counted = counted & gdf["in_study_area"].astype(bool)
    accounts = gdf[counted]
    for county, g in accounts.groupby("county"):
        ma = g["meets_acreage"]
        per_county[county] = {
            "in_study_area": int(len(g)),
            "meets_acreage": int(ma.sum()),
            "meets_acreage_and_ag_zoned": int((ma & (g["is_agricultural"] == True)).sum()),  # noqa: E712
            "meets_acreage_zoning_unknown": int((ma & g["is_agricultural"].isna()).sum()),
            "stage1_pass": int(g["stage1_pass"].sum()),
            "median_acres_passing": float(g.loc[g["stage1_pass"], "gross_acres"].median()) if g["stage1_pass"].any() else None,
            "acres_passing_total": float(g.loc[g["stage1_pass"], "gross_acres"].sum()),
            "owner_types_passing": {k: int(v) for k, v in g.loc[g["stage1_pass"], "owner_type"].value_counts().items()},
        }
    return {
        "parcels_loaded": int(n_loaded),
        "parcels_in_study_area": int(len(accounts)),
        "acreage_min": cfg.acreage_min,
        "acreage_max": cfg.acreage_max,
        "meets_acreage": int(accounts["meets_acreage"].sum()),
        "stage1_pass": int(accounts["stage1_pass"].sum()),
        "acreage_disagreements": int(accounts["acreage_disagrees"].sum()),
        "unique_owner_keys_passing": int(accounts.loc[accounts["stage1_pass"], "owner_key"].nunique()),
        "per_county": per_county,
    }
