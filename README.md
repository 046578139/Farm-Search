# Farm-Search — farmland acquisition screening pipeline

Screens agricultural parcels in Frederick, Carroll and southeastern Washington
County, Maryland, and produces a per-parcel record scoring usability,
encumbrance and access. **Screening tool, not a title report**: it reduces
thousands of parcels to dozens worth paying a professional to examine. Every
flag is a reason to look at the deed, never a reason to delete a row.

This repository currently implements **Stages 1–4** of the spec
(`docs/farm_parcel_screening_spec.md`), which together eliminate the large
majority of parcels:

| Stage | What it does | Where |
|---|---|---|
| 1 | Study-area selection, acreage filter, per-county agricultural-zoning mapping, owner typing. Every parcel is retained with flags. | `farmsearch/stages/stage1_base_filter.py` |
| 2 | Per-parcel intersection with every constraint layer, **reported separately by type** with acreage and position (sector, offset, boundary contact, whether it bisects the parcel). | `farmsearch/stages/stage2_encumbrance.py` |
| 3 | `usable = parcel − forest conservation − riparian − wetlands − floodplain − slope > max`. Agricultural preservation easements are **not** subtracted. Slope comes from the LiDAR DEM per parcel. | `farmsearch/stages/stage3_usable_area.py`, `farmsearch/slope.py` |
| 4 | Entry nodes, landlock test, frontage blockage by encumbrance or **separately-owned parcel**, reserve-strip detection with the offending account ID, connected components of the usable area seeded at the entry nodes → **largest contiguous reachable block**, unreachable islands. | `farmsearch/stages/stage4_access.py`, `farmsearch/geometry/` |

Stages 5–10 (dischargeable envelope, viewshed, future encroachment, MPRP,
valuation, commute, dossiers) are not built yet.

## Install

```bash
pip install -e ".[dev]"
python -m pytest            # validates Stages 1–4 against the synthetic fixture
```

Requires GeoPandas ≥ 1.0, Shapely ≥ 2.0, pyogrio and rasterio (GDAL comes
with the wheels).

## Try it without any data

A synthetic "county" reproduces every Stage 1–4 failure mode with known
answers (bisecting stream, forest easement along the frontage, reserve strip
with a foreign owner, same-owner strip, interior landlocked parcel, MALPF
easement, hilltop island from a synthetic DEM, split zoning, undersized and
residential parcels, a Carroll parcel with a differently-named zoning field):

```bash
farmsearch make-fixture --out data/fixture
farmsearch run --config data/fixture/pipeline.yaml --stages 1-4
cat data/fixture/outputs/summary.md
```

`farmsearch/fixtures/synthetic.py` documents what each parcel tests.

## Running on real data

Every source below was located and verified live (layer JSON read, field
list checked, feature count over the study area confirmed) on 2026-09-02;
`config/pipeline.yaml` carries the URLs and `docs/HANDOFF.md` the notes.

**Host note.** The spec cites `geodata.md.gov`; that host was returning
HTTP 503 "Site Maintenance". `mdgeodata.md.gov` serves the identical iMAP
service tree (same paths) and is what the config uses.

1. **Study area.** Built from real county boundaries, not hand-drawn:
   ```bash
   farmsearch build-study-area --config config/pipeline.yaml                 # initial
   farmsearch build-study-area --config config/pipeline.yaml --variant expanded_carroll \
       --out config/study_area_expanded_carroll.geojson
   ```
   `study_area_build:` in the config lists one part per county with an
   optional lon/lat clip box (Mount Airy corner of Carroll, SE Washington).
2. **Parcels (foundation layer).** The MDP/SDAT Maryland Parcel Boundaries
   layer is pulled per county straight from the iMAP REST service:
   ```bash
   farmsearch fetch-parcels --config config/pipeline.yaml        # ~265 pages, six minutes
   ```
   Whole counties, always (the cache is keyed by county, so it must not
   depend on the study area). The service pages 1000 records at ~1 s/page
   with keyset paging on OBJECTID once `outFields` is limited to the
   schema-mapped fields, so the "MaxRecordCount is 1000" warning in the
   spec is a non-issue per county. Rows whose `ACCTID` is null or the
   literal `ROW` (tax-map road right-of-way slivers) are split into
   `data/raw/parcels_row/` and reused as a right-of-way layer in Stage 4.
   The MdProperty View / FINDER Quantum bulk download pages on
   planning.maryland.gov returned 404; nothing depends on them.
3. **Verify the schema.** Nothing about field names is assumed.
   ```bash
   farmsearch verify-schema --config config/pipeline.yaml
   ```
   `config/schema/parcels.yaml` has been reconciled with the live 117-field
   layer (e.g. the owner zip is `OWNERZIP`, not `OWNZIP`; `POLYACRES`,
   `LANDAREA`/`LUOM`, `DR1LIBER`/`DR1FOLIO`, `FCMACODE`, `AGFNDAREA`,
   `SDATWEBADR` are mapped). Jurisdiction codes are `FRED`, `CARR`, `WASH`.

   **Owner names are not public.** The parcel layer, the Parcel Points layer
   and the SDAT open-data extract all publish the owner's mailing address but
   not the owner's name. `owner_name` is therefore optional: without it
   `owner_type` comes from SDAT's exemption class where there is one
   (state / county / municipal / federal → `government`, nonprofit or church
   → `religious_nonprofit`; `owner_type_basis` says so) and is `unknown`
   otherwise; `owner_key` and the Stage 4 same-owner tests use the
   normalized mailing address (plus the deed liber/folio, `deed_ref`); and
   every parcel carries the flag `owner_name_unavailable_lookup_sdat` with
   its SDAT page URL (`sdat_url`) for the shortlist. A licensed extract with
   `OWNNAME1` upgrades everything automatically.

   **Placeholder account IDs** (`ROW`, `WATER`, `RAILROAD`, `UNK`, condominium
   common elements, …) are not parcels: `parcels.account_id_regex` keeps
   only SDAT-shaped IDs, and `row_account_ids` route the road right-of-way
   rows to a ROW layer.
4. **Zoning.** Each county's own GIS layer is configured (Frederick County
   GIS `TYPE`, Carroll County `Zoning`, Washington County `Zone`). The code
   lists in `config/zoning/<county>.yaml` were generated from the live layers:
   ```bash
   farmsearch zoning-domains --config config/pipeline.yaml --county Frederick --write
   ```
   `is_agricultural` per code was filled in from each county's zoning
   ordinance (citations in the YAML). A code seen in the data but left
   unmapped aborts Stage 1 (`on_unmapped_zoning: error`).
5. **Other layers.**
   ```bash
   farmsearch fetch --config config/pipeline.yaml
   ```
   downloads every constraint / ROW layer clipped to the study-area bbox
   into `data/raw/` (ESRI JSON pages through GDAL; `rest_where` is sent to
   the service, `where` is applied on read). A layer that is missing at run
   time is skipped with a warning and listed under `missing_layers` in the
   summary; the affected stage degrades rather than fails.
6. **Slope.** The statewide LiDAR DEM is read from the iMAP ImageServer one
   parcel window at a time (`slope.dem_url`, `exportImage` in the working
   CRS at `dem_resample_m`, cached under `data/raw/lidar/cache/`). A local
   DEM (`dem_path`) or precomputed steep polygons take precedence when
   present. The meters service is configured; set
   `dem_vertical_unit_to_m: 0.3048` if you switch to `MD_statewide_dem_ft`.
7. **Run.**
   ```bash
   farmsearch run --config config/pipeline.yaml --stages 1-4
   ```
   Check `outputs/summary.md` — the per-county counts are the Stage 1 sanity
   check the spec asks for.

### Data sources used (all verified live)

| Need | Source | Layer / filter |
|---|---|---|
| Parcels | iMAP `PlanningCadastre/MD_ParcelBoundaries/MapServer/0` | per county, `JURSCODE` |
| Zoning, Frederick | Frederick County GIS `PlanningAndPermitting/Zoning/MapServer/1` | `TYPE` (coded domain) |
| Zoning, Carroll | Carroll County AGOL `Zoning/FeatureServer/0` | `Zoning` |
| Zoning, Washington | Washington County AGOL `Washington_County_Zoning/FeatureServer/21` | `Zone` / `Zone_Full` |
| MALPF easements | iMAP `Environment/MD_ProtectedLands/MapServer/4` | favorable |
| County PDR/TDR/IPP/Critical Farms | `MD_ProtectedLands/MapServer/9` | favorable, `OthrPrgNm != 'CREP'` |
| Rural Legacy | `MD_ProtectedLands/MapServer/1` | favorable |
| MET easements | `MD_ProtectedLands/MapServer/2` | varies |
| Forest conservation easements | `MD_ProtectedLands/MapServer/3` + Frederick `ForestResource/2`, Carroll `Forest_Conservation_Easement/0` (recorded), Washington `Forest_Conservation_Easements_View/0` (unreleased) | hostile |
| CREP enrollments (mapped) | `MD_ProtectedLands/MapServer/9`, `OthrPrgNm == 'CREP'` | hostile strip |
| DNR lands, Frederick other easements | `MD_ProtectedLands/MapServer/0`, Frederick `OtherEasementsOrRestrictions/0` | varies |
| Wetlands | iMAP `Hydrology/MD_Wetlands/MapServer/2` (NWI) | physical |
| Floodplain | iMAP `Hydrology/MD_Floodplain/MapServer/1` | `FLD_ZONE in A, AE, AH, AO` |
| Streams (riparian inference) | iMAP `Hydrology/MD_Waterbodies/MapServer/2` (detailed) | 100 ft each side |
| State road ROW | MDOT SHA `MDOT_SHA_Right-Of-Way_(Polygons)/FeatureServer/32` | polygons per SHA grid |
| Tax-map road ROW | `ACCTID = 'ROW'` rows of the parcel layer | polygons |
| County roads | Frederick `Basemap/Centerlines/0` (`OWNERSHIP`), Carroll `Roads_CarrollCounty/0` (`ROADCLASS`), Washington `Road_Centerlines_Public_View/0` (`Road_Code`) | public only, no interstates/ramps |
| DEM | iMAP LiDAR `Statewide/MD_statewide_dem_m/ImageServer` | per-parcel `exportImage` |
| County boundaries | iMAP `Boundaries/MD_PhysicalBoundaries/MapServer/0` | study area |

SHA plat boundaries, the HPMS access-control layer, statewide centerlines,
MDP generalized zoning, the MPRP route, the residential pipeline and other
later-stage layers are listed under `reference_layers:` in the config.

### Study area

`config/study_area.geojson` is built from the iMAP detailed county
boundaries (all of Frederick, the Mount Airy corner of Carroll, and the
Sharpsburg / Keedysville / Boonsboro / Rohrersville area of Washington;
2,248 km²). An expanded northern-Carroll run (3,241 km²) is a one-line change:

```yaml
study_area: study_area_expanded_carroll.geojson
```

Parcels are never geometrically cut by the study area (that would corrupt
acreage); `study_area_selection` chooses intersects / centroid / within.

## Outputs (`outputs/`)

| File | Contents |
|---|---|
| `parcels_stage1.gpkg` | every parcel in the study area with Stage 1 flags (`stage1_pass`, `stage1_pass_reason`) |
| `encumbrances.csv` | one row per parcel × constraint layer: type, implication, acres, % of parcel, position, offset, boundary contact, fragments if removed, bisects |
| `usable_area.gpkg` | usable polygon per scored parcel |
| `frontage.gpkg` | road-facing boundary pieces classified `open` / `encumbered` / `foreign_parcel` / `same_owner_parcel` / `gap`, with blocking account ID and road authority |
| `entry_points.gpkg` | usable entry nodes |
| `reserve_strips.csv` | candidate access-control strips: strip account ID, owner, same-owner test, estimated width/length/aspect, frontage blocked |
| `islands.csv` | unreachable usable islands per parcel |
| `parcels_scored.gpkg` / `.csv` / `.geojson` | the per-parcel record |
| `summary.json` / `summary.md` | counts per stage and county, missing layers, and the list of things the pipeline cannot determine |

### Per-parcel record (Stages 1–4 portion of the spec)

```
account_id, owner_name, owner_mailing_address, owner_type, owner_key
gross_acres, zoning, county
encumbrances_json                          [{type, source_layer, acres, pct_of_parcel, position, ...}]
usable_acres
largest_contiguous_reachable_acres         <- primary usability metric
unreachable_islands_json                   [{acres}]
landlocked_apparent
frontage_blocked_by_foreign_parcel
blocking_parcel_account_id
manual_verification_flags                  [...]
```

followed by supporting detail: acreage basis and SDAT/geometry disagreement,
split-zoning shares, favorable vs hostile easement acres, per-layer
subtracted acres, steep-slope acres, frontage lengths by class, road
authorities (state ⇒ SHA entrance permit), reserve-strip details, and the
**crossings-permitted variant** of reachability (what the largest block
becomes if a stream/wetland crossing is permitted — an apparently bisected
parcel is not automatically unusable).

`owner_key` (normalized name + mailing address) is the collapse key for the
deduplicated owner list.

### Manual-verification flags raised by Stages 1–4

`landlocked_apparent_check_deeded_access`,
`frontage_blocked_confirm_ownership_and_deeded_access`,
`reserve_strip_foreign_owner`, `access_via_separately_deeded_same_owner_parcel`,
`usable_area_unreachable_from_frontage`,
`islands_reconnectable_via_mde_crossing_permit`,
`entrance_permit_sha_state_road`, `frontage_indeterminate_row_gap`,
`hostile_constraint_bisects_parcel`,
`riparian_buffer_presumed_confirm_with_seller`, `met_easement_read_terms`,
`other_easement_read_terms`, `crep_enrollment_confirm_contract_term`,
`owner_name_unavailable_lookup_sdat`, `slope_window_failed_not_evaluated`,
`sdat_acreage_disagrees_with_geometry`, `zoning_unmapped`,
`zoning_layer_missing`.

## Design notes

- **All thresholds and field names live in `config/pipeline.yaml`.** Stage
  logic reads `subtract_from_usable`, `implication`, `crossable_with_permit`
  per layer, so which layers count against usable area is data, not code.
- **Different legal instruments are never collapsed.** MALPF / county / Rural
  Legacy easements are `favorable` (subdivision restricted, farming and
  vehicles not); forest conservation easements are `hostile`; MET is
  `varies` and raises a read-the-instrument flag.
- **Position matters as much as area.** Each encumbrance row reports compass
  sector, centroid offset, boundary contact and how many pieces the parcel
  falls into when that encumbrance is removed.
- **Frontage classification probes outward.** Each ~15 ft piece of the
  road-facing boundary asks: does the walk to the nearest public ROW cross
  another parcel (and whose), and is the ground just inside covered by a
  hostile constraint? Side edges near a road corner, whose probe runs along
  the edge, are not frontage.
- **Reserve strips** are found by shape (width ≈ 2·area/perimeter, which
  survives curved and L-shaped strips) plus an owner comparison that tolerates
  SDAT name formatting; a strip that only touches at a corner is ignored.
- **Landlocked means zero direct contact**, reported honestly even when the
  subject reaches the road through the same family's other deed
  (`access_via_same_owner_parcel`).
- CREP riparian buffers cannot be obtained parcel-by-parcel; they are
  inferred as a fixed-width strip along NHD flowlines and always flagged.
- The riparian / wetland / floodplain constraints are `crossable_with_permit`;
  the record reports both strict reachability and the crossings-permitted
  variant, plus the acres that a permit would reconnect.

## Layout

```
config/            pipeline.yaml, study areas, parcel schema map, zoning mappings
farmsearch/        package
  io/              ArcGIS REST client (ESRI JSON paging), loaders, schema verification,
                   per-county parcel pull, study-area builder
  geometry/        encumbrance position, frontage probing, reserve strips, connectivity
  stages/          stage1..stage4
  slope.py         DEM (local file or iMAP ImageServer) → steep-area polygons
  pipeline.py      orchestration + outputs
  fixtures/        synthetic validation dataset
tests/             pytest suite (runs against the fixture)
docs/              the spec
```
