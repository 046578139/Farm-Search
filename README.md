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

1. **Parcels (foundation layer).** Download the MdProperty View / FINDER
   Quantum bulk files for Frederick, Carroll and Washington from
   <https://planning.maryland.gov/Pages/OurProducts/DownloadFiles.aspx> and
   put them under `data/raw/parcels/`. Read the dataset's custom license
   first. Do not point the pipeline at the iMAP REST layer for a production
   run: `MaxRecordCount` is 1000 against ~2.29M records.
2. **Verify the schema.** Nothing about field names is assumed.
   ```bash
   farmsearch verify-schema --config config/pipeline.yaml
   ```
   prints how each canonical field resolved and, on failure, the real field
   list. Edit `config/schema/parcels.yaml` until every required field resolves.
   Stage 1 refuses to run otherwise. Also confirm the jurisdiction codes in
   the data match `counties:` in the config (a code not listed there aborts).
3. **Zoning.** Set each county's layer `url:`/`path:` in `config/pipeline.yaml`,
   then pull the district codes from the live layer into a mapping template:
   ```bash
   farmsearch zoning-domains --config config/pipeline.yaml --county Frederick --code-field <FIELD> --write
   ```
   Fill in `is_agricultural: true/false` for every code in
   `config/zoning/<county>.yaml`. A code seen in the data but left unmapped
   aborts Stage 1 (`on_unmapped_zoning: error`) so the mapping cannot be
   silently incomplete.
4. **Other layers.** Set `url:` for each constraint / ROW layer (the config
   marks each with `VERIFY`), then
   ```bash
   farmsearch fetch --config config/pipeline.yaml
   ```
   downloads them clipped to the study-area bbox into `data/raw/`. A layer
   that is missing at run time is skipped with a warning and listed under
   `missing_layers` in the summary; the affected stage degrades rather than
   fails. Local files (`path:`) work for anything you already have.
5. **Slope.** Point `slope.dem_path` at the statewide LiDAR DEM (GeoTIFF/VRT/COG;
   `/vsicurl/` URLs work). It is read one parcel window at a time. Set
   `dem_vertical_unit_to_m: 0.3048` if the DEM is in feet.
6. **Run.**
   ```bash
   farmsearch run --config config/pipeline.yaml --stages 1-4
   ```
   Check `outputs/summary.md` — the per-county counts are the Stage 1 sanity
   check the spec asks for.

### Study area

`config/study_area.geojson` is a **placeholder envelope** shaped to the spec
(all of Frederick, only the Mount Airy corner of Carroll, and the
Sharpsburg / Keedysville / Boonsboro / Rohrersville area of Washington).
Replace it with real county-boundary geometry before a production run. An
expanded northern-Carroll run is a one-line change:

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
  io/              ArcGIS REST client, loaders, runtime schema verification
  geometry/        encumbrance position, frontage probing, reserve strips, connectivity
  stages/          stage1..stage4
  slope.py         DEM → steep-area polygons
  pipeline.py      orchestration + outputs
  fixtures/        synthetic validation dataset
tests/             pytest suite (runs against the fixture)
docs/              the spec
```
