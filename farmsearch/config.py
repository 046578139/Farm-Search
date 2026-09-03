"""Configuration loading and validation.

The YAML file is the single place thresholds, field names and layer sources
live. This module turns it into typed objects and resolves relative paths
against the YAML file's directory so the pipeline can be run from anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


class ConfigError(ValueError):
    pass


def _opt_path(base: Path, p: Optional[str]) -> Optional[Path]:
    if p is None or p == "":
        return None
    q = Path(p)
    return q if q.is_absolute() else (base / q).resolve()


@dataclass
class LayerSource:
    """A vector layer that may come from a local file or an ArcGIS REST layer."""
    name: str
    path: Optional[Path] = None
    url: Optional[str] = None
    layer: Optional[str] = None
    where: Optional[str] = None        # pandas-query filter applied AFTER download / on read
    rest_where: Optional[str] = None   # SQL-92 filter sent to the REST service at fetch time
    page_size: Optional[int] = None    # override the service maxRecordCount when paging (heavy geometries)
    dedupe_geometry: bool = False      # drop rows whose geometry duplicates an earlier row (double-published easements)
    fetch_margin_ft: Optional[float] = None   # fetch bbox margin beyond the study area (default: context_buffer_ft)

    @classmethod
    def from_dict(cls, base: Path, d: dict, name: Optional[str] = None) -> "LayerSource":
        return cls(name=name or d.get("name") or "layer",
                   path=_opt_path(base, d.get("path")),
                   url=d.get("url"), layer=d.get("layer"), where=d.get("where"),
                   rest_where=d.get("rest_where"),
                   page_size=(int(d["page_size"]) if d.get("page_size") else None),
                   dedupe_geometry=bool(d.get("dedupe_geometry", False)),
                   fetch_margin_ft=(float(d["fetch_margin_ft"]) if d.get("fetch_margin_ft") is not None else None))


@dataclass
class EraseSpec:
    """A layer whose (optionally buffered) footprint is subtracted from another
    layer at load time: released forest easements erased from the easement
    layer, controlled-access highway corridors erased from the state ROW."""
    source: LayerSource
    buffer_ft: float = 0.0

    @classmethod
    def from_dict(cls, base: Path, d: Optional[dict], name: str) -> Optional["EraseSpec"]:
        if not d:
            return None
        return cls(source=LayerSource.from_dict(base, d, name=name), buffer_ft=float(d.get("buffer_ft", 0) or 0))

    def available(self) -> bool:
        return self.path is not None and self.path.exists()


@dataclass
class ParcelsConfig:
    source: LayerSource
    schema_path: Path
    acreage_source: str = "sdat"
    acreage_disagreement_pct: float = 10.0
    # Placeholder IDs that mark ROAD RIGHT-OF-WAY polygons in the source (the
    # MD parcel layer uses 'ROW' / 'ROW_ALLEY'); `fetch-parcels` splits them
    # into the parcels_row layer.
    row_account_ids: list[str] = field(default_factory=lambda: ["ROW", "ROW_ALLEY"])
    # Further placeholder IDs to exclude from the parcel set in Stage 1, on
    # top of account_id_regex (farmsearch.accounts): anything that does not
    # match the pattern is not an account. null disables the pattern.
    non_parcel_account_ids: list[str] = field(default_factory=list)
    account_id_regex: Optional[str] = r"^(?=.*\d)[0-9A-Za-z]{8,}$"
    # Canonical fields that must be non-null for a row to count as a parcel
    # (the MD layer's PTYPE is null exactly on polygons with no SDAT record).
    require_non_null: list[str] = field(default_factory=list)
    # Optional ArcGIS REST layer the parcels can be pulled from with
    # `farmsearch fetch-parcels` (paginated per county into `path`).
    url: Optional[str] = None


@dataclass
class ZoningSpec:
    county: str
    source: LayerSource
    code_field: Optional[str]
    mapping_path: Path
    # primary: the county layer. fill: a municipal layer consulted only where
    # the primary layer maps the parcel to "unknown" (a MUN / TOWN placeholder)
    # or does not cover it at all. Several fill layers may follow, in order.
    role: str = "primary"
    # Loaded lazily: code -> {description, is_agricultural}
    codes: dict = field(default_factory=dict)

    def load_mapping(self) -> None:
        if not self.mapping_path.exists():
            raise ConfigError(f"zoning mapping for {self.county} not found: {self.mapping_path}")
        m = yaml.safe_load(self.mapping_path.read_text()) or {}
        if self.code_field is None:
            self.code_field = m.get("code_field")
        self.codes = {}
        # is_agricultural: true / false / "unknown" (a deliberate "the county has
        # no opinion here", e.g. municipal placeholders — the parcel is retained
        # and flagged) / null (NOT decided yet — aborts under on_unmapped_zoning: error)
        for code, v in (m.get("codes") or {}).items():
            val = v.get("is_agricultural") if isinstance(v, dict) else v
            if isinstance(val, str):
                val = val.strip().lower()
                if val == "unknown":
                    val = "unknown"
                elif val in ("true", "yes"):
                    val = True
                elif val in ("false", "no"):
                    val = False
                else:
                    raise ConfigError(f"zoning {self.county}: code {code!r} has is_agricultural={val!r}; "
                                      f"use true, false, unknown or null")
            res = v.get("is_residential") if isinstance(v, dict) else None
            if isinstance(res, str):
                res = res.strip().lower() in ("true", "yes")
            self.codes[str(code)] = {"description": (v.get("description") if isinstance(v, dict) else None),
                                     "is_agricultural": val,
                                     # Stage 7: residential zoning on adjoining land is the threat.
                                     # Explicit in the YAML when given; else inferred from the code /
                                     # description (R-prefixed codes, "residential" in the text).
                                     "is_residential": (bool(res) if res is not None else None)}


@dataclass
class DeriveFromLines:
    source: LayerSource
    buffer_ft: float


@dataclass
class ConstraintSpec:
    name: str
    type: str
    category: str                 # legal | physical
    implication: str              # favorable | hostile | varies | physical
    subtract_from_usable: bool
    crossable_with_permit: bool
    # Does the constraint stop a vehicle? A stream, a wetland or a forest
    # easement does; a mapped floodplain zone does not (fill and structures
    # are regulated there, driving across it is not). Governs the base
    # reachability of Stage 4; the "crossings permitted" variant only
    # honours the constraints that can never be crossed.
    blocks_travel: bool = True
    source: Optional[LayerSource] = None
    derive_from_lines: Optional[DeriveFromLines] = None
    manual_flag: Optional[str] = None
    name_field: Optional[str] = None
    erase: Optional[EraseSpec] = None

    @classmethod
    def from_dict(cls, base: Path, d: dict) -> "ConstraintSpec":
        for k in ("name", "type", "category", "implication", "subtract_from_usable", "crossable_with_permit"):
            if k not in d:
                raise ConfigError(f"constraint entry missing '{k}': {d}")
        if d["category"] not in ("legal", "physical"):
            raise ConfigError(f"constraint {d['name']}: category must be legal|physical")
        if d["implication"] not in ("favorable", "hostile", "varies", "physical"):
            raise ConfigError(f"constraint {d['name']}: implication must be favorable|hostile|varies|physical")
        dfl = None
        if d.get("derive_from_lines"):
            dd = d["derive_from_lines"]
            dfl = DeriveFromLines(source=LayerSource.from_dict(base, dd, name=f"{d['name']}_lines"),
                                  buffer_ft=float(dd.get("buffer_ft", 100)))
        src = None
        if d.get("path") or d.get("url"):
            src = LayerSource.from_dict(base, d)
        if src is None and dfl is None:
            raise ConfigError(f"constraint {d['name']}: needs path/url or derive_from_lines")
        return cls(name=d["name"], type=d["type"], category=d["category"],
                   implication=d["implication"],
                   subtract_from_usable=bool(d["subtract_from_usable"]),
                   crossable_with_permit=bool(d["crossable_with_permit"]),
                   blocks_travel=bool(d.get("blocks_travel", True)),
                   source=src, derive_from_lines=dfl,
                   manual_flag=d.get("manual_flag"), name_field=d.get("name_field"),
                   erase=EraseSpec.from_dict(base, d.get("erase"), name=f"{d['name']}_erase"))


@dataclass
class SlopeConfig:
    dem_path: Optional[Path]
    dem_vertical_unit_to_m: float = 1.0
    dem_resample_m: Optional[float] = None
    steep_polygons_path: Optional[Path] = None
    crossable: bool = False       # can a >slope_max_pct area be driven across? (Stage 4 crossings variant)
    # ArcGIS ImageServer DEM (Maryland statewide LiDAR), read per parcel with
    # exportImage when no local dem_path exists. Windows are cached under
    # dem_cache_dir.
    dem_url: Optional[str] = None
    dem_cache_dir: Optional[Path] = None
    dem_min_valid_m: Optional[float] = None   # elevations below this are artefacts (masked as nodata)


@dataclass
class StudyAreaPart:
    county: str
    clip_bbox: Optional[tuple[float, float, float, float]] = None   # lon/lat box the county is clipped to


@dataclass
class StudyAreaBuild:
    """How `farmsearch build-study-area` assembles the study polygon from a
    county-boundary layer: one part per county, optionally clipped to a box."""
    boundaries_url: str
    county_field: str
    variants: dict[str, list[StudyAreaPart]]


@dataclass
class RowLayer:
    source: LayerSource
    authority: str                # state | county | municipal | unknown
    public: bool
    geometry: str                 # polygon | line
    row_width_ft: Optional[float] = None
    erase: Optional[EraseSpec] = None   # e.g. controlled-access corridors: ROW there is not access


@dataclass
class AccessConfig:
    row_layers: list[RowLayer]
    contact_tolerance_ft: float = 3
    open_gap_ft: float = 25
    min_contact_ft: float = 12            # a driveway's width: less direct ROW contact = landlocked_apparent
    narrow_contact_ft: float = 30         # direct contact below this (but above min) is flagged, not failed
    frontage_search_ft: float = 250
    frontage_sample_ft: float = 15
    frontage_blocked_threshold: float = 0.95
    strip_max_width_ft: float = 100
    strip_min_aspect: float = 6
    strip_max_length_ft: float = 5000     # longer "strips" are road / rail / utility corridors, not access control
    strip_exclude_improved: bool = True   # a candidate with a structure / improvement value is a house lot, not a strip
    row_parcel_overlap: float = 0.5       # a neighbour with this share of its area inside public ROW IS the road
    sliver_acres: float = 0.25


@dataclass
class UnitsLayer:
    """A residential-pipeline layer: features carrying approved-but-unbuilt units."""
    source: LayerSource
    units_field: str
    status_field: Optional[str] = None


@dataclass
class EncroachmentConfig:
    """Stage 7 inputs. Every layer is optional: a missing one degrades that
    column to null and is listed under missing_layers."""
    sewer_layers: list[LayerSource] = field(default_factory=list)      # each with `where` selecting PLANNED (or existing) service
    sewer_existing_layers: list[LayerSource] = field(default_factory=list)
    pfa_layers: list[LayerSource] = field(default_factory=list)
    growth_area_layers: list[LayerSource] = field(default_factory=list)
    pipeline_layers: list[UnitsLayer] = field(default_factory=list)
    pipeline_radius_ft: float = 10560                                   # 2 miles
    adjacency_tolerance_ft: float = 3


@dataclass
class RouteLayer:
    source: LayerSource
    variant: str = "studied"        # preferred | alternative | studied (any)


@dataclass
class TransmissionConfig:
    """Stage 8 inputs."""
    mprp_routes: list[RouteLayer] = field(default_factory=list)
    mprp_corridor_width_ft: float = 150
    mprp_exclusion_buffer_ft: float = 2000
    mprp_general_corridor_ft: float = 5280
    hv_line_layers: list[LayerSource] = field(default_factory=list)
    hv_line_buffer_ft: float = 1000
    substation_layers: list[LayerSource] = field(default_factory=list)
    substation_buffer_ft: float = 2640
    data_center_layers: list[LayerSource] = field(default_factory=list)
    data_center_buffer_ft: float = 15840
    points_of_concern: list[dict] = field(default_factory=list)         # [{name, lon, lat, buffer_ft}] e.g. Doubs substation
    status_note: Optional[str] = None                                   # PSC case status, re-verified at run time by hand


@dataclass
class EnvelopeConfig:
    """Stage 5 (hunting safety zones, NR 10-410) and Stage 6 (viewshed)."""
    safety_buffer_yards: float = 150          # dwelling, residence, church, other building occupied by people
    school_buffer_yards: float = 300          # public or nonpublic school during school hours / activities
    archery_buffer_yards: float = 50          # Frederick, Carroll, Washington: archery safety zone
    min_dischargeable_acres: float = 10
    min_envelope_length_yards: float = 200
    footprint_layers: list[LayerSource] = field(default_factory=list)      # building footprints (polygons)
    school_point_layers: list[LayerSource] = field(default_factory=list)   # school points (supplement to exempt-class parcels)
    church_point_layers: list[LayerSource] = field(default_factory=list)
    dwelling_land_uses: list[str] = field(default_factory=lambda: [
        "Residential", "Town House", "Agricultural", "Residential Condominium", "Apartments",
        "Commercial/Residential", "Residential/Commercial", "Residential/Agricultural"])
    dwelling_min_structure_sqft: float = 400
    footprint_min_sqft: float = 400
    church_exempt_regex: str = r"church|synagogue|parsonage|religious|mosque|temple"
    school_exempt_regex: str = r"school|college"
    exclude_same_owner: bool = True           # the owner/occupant exemption extends to the owner's other parcels
    # Stage 6
    viewshed_max_distance_yards: float = 1000
    observer_height_m: float = 1.7
    target_height_m: float = 2.0
    dem_cell_m: float = 10.0
    firing_points: int = 5
    backstop_slope_min_pct: float = 15
    backstop_min_acres: float = 0.25
    backstop_search_ft: float = 300           # steep ground this far beyond the envelope edge still serves as a backstop


@dataclass
class RunConfig:
    process_all: bool = False
    output_dir: Path = Path("outputs")


@dataclass
class Config:
    base_dir: Path
    acreage_min: float
    acreage_max: Optional[float]
    slope_max_pct: float
    study_area_path: Path
    study_area_selection: str
    working_crs: str
    # Parcels, rights-of-way and constraints are loaded this far beyond the
    # study polygon so a parcel on the edge still sees its road and its
    # neighbours (they are context only: never scored, never counted).
    context_buffer_ft: float
    parcels: ParcelsConfig
    counties: dict[str, str]
    zoning: list[ZoningSpec]
    on_unmapped_zoning: str
    constraints: list[ConstraintSpec]
    slope: SlopeConfig
    access: AccessConfig
    run: RunConfig
    encroachment: EncroachmentConfig = field(default_factory=EncroachmentConfig)
    transmission: TransmissionConfig = field(default_factory=TransmissionConfig)
    envelope: EnvelopeConfig = field(default_factory=EnvelopeConfig)
    study_area_build: Optional[StudyAreaBuild] = None
    raw: dict = field(default_factory=dict)   # untouched YAML for later stages

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path).resolve()
        if not path.exists():
            raise ConfigError(f"config not found: {path}")
        raw = yaml.safe_load(path.read_text()) or {}
        base = path.parent
        return cls.from_dict(raw, base)

    @classmethod
    def from_dict(cls, raw: dict, base: Path) -> "Config":
        base = Path(base)
        try:
            p = raw["parcels"]
            parcels = ParcelsConfig(
                source=LayerSource.from_dict(base, p, name="parcels"),
                schema_path=_opt_path(base, p.get("schema", "schema/parcels.yaml")),
                acreage_source=p.get("acreage_source", "sdat"),
                acreage_disagreement_pct=float(p.get("acreage_disagreement_pct", 10)),
                row_account_ids=[str(x) for x in (["ROW", "ROW_ALLEY"] if p.get("row_account_ids") is None else p["row_account_ids"])],
                non_parcel_account_ids=[str(x) for x in (p.get("non_parcel_account_ids") or [])],
                account_id_regex=(p["account_id_regex"] if "account_id_regex" in p else r"^(?=.*\d)[0-9A-Za-z]{8,}$"),
                require_non_null=[str(x) for x in (p.get("require_non_null") or [])],
                url=p.get("url"),
            )
            if parcels.acreage_source not in ("sdat", "geometry"):
                raise ConfigError("parcels.acreage_source must be sdat|geometry")
            zoning = []
            for z in raw.get("zoning", []) or []:
                role = str(z.get("role", "primary")).lower()
                if role not in ("primary", "fill"):
                    raise ConfigError(f"zoning {z.get('county')}: role must be primary|fill")
                zname = z.get("name") or (f"zoning_{z['county']}" if role == "primary" else f"zoning_{z['county']}_fill{len(zoning)}")
                zoning.append(ZoningSpec(county=z["county"],
                                         source=LayerSource.from_dict(base, z, name=zname),
                                         code_field=z.get("code_field"),
                                         mapping_path=_opt_path(base, z["mapping"]),
                                         role=role))
            for c in {z.county for z in zoning}:
                if sum(1 for z in zoning if z.county == c and z.role == "primary") > 1:
                    raise ConfigError(f"zoning {c}: only one primary layer per county (mark the others role: fill)")
            constraints = [ConstraintSpec.from_dict(base, c) for c in (raw.get("constraints") or [])]
            names = [c.name for c in constraints]
            if len(names) != len(set(names)):
                raise ConfigError("constraint names must be unique")
            s = raw.get("slope", {}) or {}
            slope = SlopeConfig(dem_path=_opt_path(base, s.get("dem_path")),
                                dem_vertical_unit_to_m=float(s.get("dem_vertical_unit_to_m", 1.0)),
                                dem_resample_m=(None if s.get("dem_resample_m") in (None, "null") else float(s["dem_resample_m"])),
                                steep_polygons_path=_opt_path(base, s.get("steep_polygons_path")),
                                crossable=bool(s.get("crossable", False)),
                                dem_url=s.get("dem_url") or None,
                                dem_cache_dir=_opt_path(base, s.get("dem_cache_dir")),
                                dem_min_valid_m=(None if s.get("dem_min_valid_m") in (None, "null") else float(s["dem_min_valid_m"])))
            sab = None
            sb = raw.get("study_area_build")
            if sb:
                variants = {}
                for vname, parts in (sb.get("variants") or {}).items():
                    lst = []
                    for pt in parts or []:
                        bb = pt.get("clip_bbox")
                        if bb is not None and len(bb) != 4:
                            raise ConfigError(f"study_area_build variant {vname}: clip_bbox needs 4 numbers")
                        lst.append(StudyAreaPart(county=str(pt["county"]),
                                                 clip_bbox=tuple(float(x) for x in bb) if bb else None))
                    variants[str(vname)] = lst
                if not sb.get("boundaries_url") or not variants:
                    raise ConfigError("study_area_build needs boundaries_url and at least one variant")
                sab = StudyAreaBuild(boundaries_url=str(sb["boundaries_url"]),
                                     county_field=str(sb.get("county_field", "COUNTY")), variants=variants)
            a = raw.get("access", {}) or {}
            rows = []
            for r in a.get("row_layers", []) or []:
                geom = r.get("geometry", "polygon")
                if geom not in ("polygon", "line"):
                    raise ConfigError(f"row layer {r.get('name')}: geometry must be polygon|line")
                if geom == "line" and not r.get("row_width_ft"):
                    raise ConfigError(f"row layer {r.get('name')}: line layers need row_width_ft")
                rows.append(RowLayer(source=LayerSource.from_dict(base, r),
                                     authority=r.get("authority", "unknown"),
                                     public=bool(r.get("public", True)),
                                     geometry=geom,
                                     row_width_ft=(float(r["row_width_ft"]) if r.get("row_width_ft") else None),
                                     erase=EraseSpec.from_dict(base, r.get("erase"), name=f"{r.get('name', 'row')}_erase")))
            access = AccessConfig(
                row_layers=rows,
                contact_tolerance_ft=float(a.get("contact_tolerance_ft", 3)),
                open_gap_ft=float(a.get("open_gap_ft", 25)),
                min_contact_ft=float(a.get("min_contact_ft", 12)),
                narrow_contact_ft=float(a.get("narrow_contact_ft", 30)),
                frontage_search_ft=float(a.get("frontage_search_ft", 250)),
                frontage_sample_ft=float(a.get("frontage_sample_ft", 15)),
                frontage_blocked_threshold=float(a.get("frontage_blocked_threshold", 0.95)),
                strip_max_width_ft=float(a.get("strip_max_width_ft", 100)),
                strip_min_aspect=float(a.get("strip_min_aspect", 6)),
                strip_max_length_ft=float(a.get("strip_max_length_ft", 5000)),
                strip_exclude_improved=bool(a.get("strip_exclude_improved", True)),
                row_parcel_overlap=float(a.get("row_parcel_overlap", 0.5)),
                sliver_acres=float(a.get("sliver_acres", 0.25)),
            )
            def _layers(lst):
                return [LayerSource.from_dict(base, d) for d in (lst or [])]
            e = raw.get("encroachment", {}) or {}
            encroachment = EncroachmentConfig(
                sewer_layers=_layers(e.get("sewer_layers")),
                sewer_existing_layers=_layers(e.get("sewer_existing_layers")),
                pfa_layers=_layers(e.get("pfa_layers")),
                growth_area_layers=_layers(e.get("growth_area_layers")),
                pipeline_layers=[UnitsLayer(source=LayerSource.from_dict(base, d), units_field=str(d["units_field"]),
                                            status_field=d.get("status_field")) for d in (e.get("pipeline_layers") or [])],
                pipeline_radius_ft=float(e.get("pipeline_radius_ft", 10560)),
                adjacency_tolerance_ft=float(e.get("adjacency_tolerance_ft", 3)),
            )
            t = raw.get("transmission", {}) or {}
            transmission = TransmissionConfig(
                mprp_routes=[RouteLayer(source=LayerSource.from_dict(base, d), variant=str(d.get("variant", "studied")))
                             for d in (t.get("mprp_routes") or [])],
                mprp_corridor_width_ft=float(t.get("mprp_corridor_width_ft", 150)),
                mprp_exclusion_buffer_ft=float(t.get("mprp_exclusion_buffer_ft", raw.get("mprp_exclusion_buffer_ft", 2000))),
                mprp_general_corridor_ft=float(t.get("mprp_general_corridor_ft", 5280)),
                hv_line_layers=_layers(t.get("hv_line_layers")),
                hv_line_buffer_ft=float(t.get("hv_line_buffer_ft", 1000)),
                substation_layers=_layers(t.get("substation_layers")),
                substation_buffer_ft=float(t.get("substation_buffer_ft", 2640)),
                data_center_layers=_layers(t.get("data_center_layers")),
                data_center_buffer_ft=float(t.get("data_center_buffer_ft", 15840)),
                points_of_concern=[dict(x) for x in (t.get("points_of_concern") or [])],
                status_note=t.get("status_note"),
            )
            v = raw.get("envelope", {}) or {}
            envelope = EnvelopeConfig(
                safety_buffer_yards=float(v.get("safety_buffer_yards", raw.get("safety_buffer_yards", 150))),
                school_buffer_yards=float(v.get("school_buffer_yards", raw.get("school_buffer_yards", 300))),
                archery_buffer_yards=float(v.get("archery_buffer_yards", 50)),
                min_dischargeable_acres=float(v.get("min_dischargeable_acres", raw.get("min_dischargeable_acres", 10))),
                min_envelope_length_yards=float(v.get("min_envelope_length_yards", raw.get("min_envelope_length_yards", 200))),
                footprint_layers=_layers(v.get("footprint_layers")),
                school_point_layers=_layers(v.get("school_point_layers")),
                church_point_layers=_layers(v.get("church_point_layers")),
                dwelling_land_uses=[str(x) for x in (v.get("dwelling_land_uses") or EnvelopeConfig().dwelling_land_uses)],
                dwelling_min_structure_sqft=float(v.get("dwelling_min_structure_sqft", 400)),
                footprint_min_sqft=float(v.get("footprint_min_sqft", 400)),
                church_exempt_regex=str(v.get("church_exempt_regex", EnvelopeConfig().church_exempt_regex)),
                school_exempt_regex=str(v.get("school_exempt_regex", EnvelopeConfig().school_exempt_regex)),
                exclude_same_owner=bool(v.get("exclude_same_owner", True)),
                viewshed_max_distance_yards=float(v.get("viewshed_max_distance_yards", 1000)),
                observer_height_m=float(v.get("observer_height_m", 1.7)),
                target_height_m=float(v.get("target_height_m", 2.0)),
                dem_cell_m=float(v.get("dem_cell_m", 10.0)),
                firing_points=int(v.get("firing_points", 5)),
                backstop_slope_min_pct=float(v.get("backstop_slope_min_pct", 15)),
                backstop_min_acres=float(v.get("backstop_min_acres", 0.25)),
                backstop_search_ft=float(v.get("backstop_search_ft", 300)),
            )
            rc = raw.get("run", {}) or {}
            run = RunConfig(process_all=bool(rc.get("process_all", False)),
                            output_dir=_opt_path(base, rc.get("output_dir", "../outputs")))
            sel = raw.get("study_area_selection", "intersects")
            if sel not in ("intersects", "centroid", "within"):
                raise ConfigError("study_area_selection must be intersects|centroid|within")
            cfg = cls(
                base_dir=base,
                acreage_min=float(raw.get("acreage_min", 40)),
                acreage_max=(None if raw.get("acreage_max") in (None, "null") else float(raw["acreage_max"])),
                slope_max_pct=float(raw.get("slope_max_pct", 15)),
                study_area_path=_opt_path(base, raw.get("study_area", "study_area.geojson")),
                study_area_selection=sel,
                working_crs=str(raw.get("working_crs", "EPSG:26985")),
                context_buffer_ft=float(raw.get("context_buffer_ft", 2000)),
                parcels=parcels,
                counties={str(k): str(v) for k, v in (raw.get("counties") or {}).items()},
                zoning=zoning,
                on_unmapped_zoning=raw.get("on_unmapped_zoning", "error"),
                constraints=constraints,
                slope=slope,
                access=access,
                run=run,
                encroachment=encroachment,
                transmission=transmission,
                envelope=envelope,
                study_area_build=sab,
                raw=raw,
            )
        except KeyError as e:
            raise ConfigError(f"missing config key: {e}") from e
        if cfg.on_unmapped_zoning not in ("error", "flag"):
            raise ConfigError("on_unmapped_zoning must be error|flag")
        if not cfg.counties:
            raise ConfigError("counties: at least one jurisdiction code -> name entry is required")
        return cfg

    # Convenience -------------------------------------------------------
    def all_layer_sources(self) -> list[LayerSource]:
        """Every REST-fetchable layer the configured stages consume, in order
        (zoning, constraints and their erase layers, ROW, Stage 7-8 layers)."""
        out: list[LayerSource] = []
        for z in self.zoning:
            out.append(z.source)
        for c in self.constraints:
            if c.source is not None:
                out.append(c.source)
            if c.derive_from_lines is not None:
                out.append(c.derive_from_lines.source)
            if c.erase is not None:
                out.append(c.erase.source)
        for r in self.access.row_layers:
            out.append(r.source)
            if r.erase is not None:
                out.append(r.erase.source)
        e = self.encroachment
        out += e.sewer_layers + e.sewer_existing_layers + e.pfa_layers + e.growth_area_layers + [u.source for u in e.pipeline_layers]
        t = self.transmission
        out += [r.source for r in t.mprp_routes] + t.hv_line_layers + t.substation_layers + t.data_center_layers
        v = self.envelope
        out += v.footprint_layers + v.school_point_layers + v.church_point_layers
        seen: set = set()
        uniq = []
        for src in out:
            key = (src.name, str(src.path), src.url, src.rest_where)
            if key not in seen:
                seen.add(key)
                uniq.append(src)
        return uniq

    def constraint(self, name: str) -> ConstraintSpec:
        for c in self.constraints:
            if c.name == name:
                return c
        raise KeyError(name)

    def zoning_for(self, county: str) -> Optional[ZoningSpec]:
        """The county's primary zoning layer."""
        for z in self.zoning:
            if z.county.lower() == county.lower() and z.role == "primary":
                return z
        return None

    def zoning_specs_for(self, county: str) -> list[ZoningSpec]:
        """Primary layer first, then the fill (municipal) layers in config order."""
        specs = [z for z in self.zoning if z.county.lower() == county.lower()]
        return [z for z in specs if z.role == "primary"] + [z for z in specs if z.role != "primary"]
