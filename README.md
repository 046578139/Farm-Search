# Farm-Search — farmland acquisition screening pipeline

Screens agricultural parcels in Frederick, Carroll and southeastern Washington
County, Maryland, and produces a per-parcel record scoring usability,
encumbrance and access. **Screening tool, not a title report**: it reduces
thousands of parcels to dozens worth paying a professional to examine. Every
flag is a reason to look at the deed, never a reason to delete a row.

This repository implements **all ten stages** of the spec
(`docs/farm_parcel_screening_spec.md`) plus the deliverables (ranked
shortlist, deduplicated owner list, PDF dossiers). Stages 1–4 eliminate the
large majority of parcels; Stages 5–10 score what is left:

| Stage | What it does | Where |
|---|---|---|
| 1 | Study-area selection, acreage filter, per-county agricultural-zoning mapping, owner typing. Every parcel is retained with flags. | `farmsearch/stages/stage1_base_filter.py` |
| 2 | Per-parcel intersection with every constraint layer, **reported separately by type** with acreage and position (sector, offset, boundary contact, whether it bisects the parcel). | `farmsearch/stages/stage2_encumbrance.py` |
| 3 | `usable = parcel − forest conservation − riparian − wetlands − floodplain − slope > max`. Agricultural preservation easements are **not** subtracted. Slope comes from the LiDAR DEM per parcel. | `farmsearch/stages/stage3_usable_area.py`, `farmsearch/slope.py` |
| 4 | Entry nodes, landlock test, frontage blockage by encumbrance or **separately-owned parcel**, reserve-strip detection with the offending account ID, connected components of the usable area seeded at the entry nodes → **largest contiguous reachable block**, unreachable islands. Reachability runs through the *passable* polygon (a mapped flood zone is not a barrier to driving; a stream, wetland or forest easement is). | `farmsearch/stages/stage4_access.py`, `farmsearch/geometry/` |
| 5 | **Dischargeable envelope** (NR §10-410): usable area minus 150 yd around every off-parcel dwelling, church and occupied building and 300 yd around schools; the owner's own structures are exempt. Envelope acres, largest block, longest interior dimension in yards, archery-zone (50 yd) acres. Target shooting is flagged for the county ordinance check. | `farmsearch/stages/stage5_envelope.py` |
| 6 | **Viewshed**: line of sight on the LiDAR DEM from candidate firing points in the envelope to every dwelling within 1,000 yd; count with a clear line; steep ground at the envelope edge whose uphill side faces away from the nearest dwelling as a candidate backstop. | `farmsearch/stages/stage6_viewshed.py`, `farmsearch/terrain.py` |
| 7 | **Future encroachment**: adjoining parcels' residential zoning acres (split zoning counted proportionally), planned / existing sewer service, Priority Funding Area, growth areas, approved-but-unbuilt units within 2 mi, adjoining permanently eased acres. | `farmsearch/stages/stage7_encroachment.py` |
| 8 | **Transmission / industrial exposure**: MPRP tier 0–3 against every studied route (preferred centerline, 150 ft ROW, dissolved 550 ft alternative-route corridor), line of sight to the route, existing HV lines and substations, data-center development (Frederick CDI overlay), Doubs substation. | `farmsearch/stages/stage8_transmission.py` |
| 9 | **Valuation**: arms-length agricultural sales (SDAT transfer records) → land $/acre bands by eased / un-eased status, county band first, pooled fallback; est_market_value, comp_basis, price-ceiling flags. Assessments are never a price proxy. | `farmsearch/stages/stage9_valuation.py` |
| 10 | **Commute** (reported, never a filter): peak minutes to BWI, Langley and Northern Virginia (traffic-aware with a Google Routes key; otherwise OSRM free-flow × a documented peak factor), route redundancy from the road graph (single egress vs redundant), corridor durability from approved units, AADT growth and programmed CTP projects. | `farmsearch/stages/stage10_commute.py` |
| — | **Deliverables**: `shortlist.csv` (configurable weights; MPRP tier 1 excluded per the spec), `owner_list.csv` (collapsed by owner + mailing address), `farmsearch dossiers` (two PDF pages per shortlist parcel). | `farmsearch/deliverables.py` |

## Install

```bash
pip install -e ".[dev]"
python -m pytest            # validates Stages 1–10 and the deliverables against the synthetic fixture
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
   check the spec asks for. The 2026-09-03 run over the three counties (about
   95 minutes with every layer and DEM window cached: 32 for Stages 1–4 and 63
   for Stages 5–10, of which Stage 6 alone is 54 because it fetches one DEM
   window per parcel) is recorded in `docs/HANDOFF.md`.
   Each stage leaves a checkpoint under `outputs/`; an interrupted run
   continues with `--stages 4 --resume` (or `2-4`, `3-4`). The default is
   `--stages 1-10`; Stages 5–10 can be run alone from the Stage 4 checkpoint
   with `--stages 5-10 --resume`.
8. **Deliverables.**
   ```bash
   farmsearch shortlist --config config/pipeline.yaml     # re-rank with the weights in config -> shortlist.csv, owner_list.csv
   farmsearch dossiers  --config config/pipeline.yaml --top 20 [--png-dir outputs/maps]   # -> outputs/dossiers.pdf
   ```

### Data sources used (all verified live)

| Need | Source | Layer / filter |
|---|---|---|
| Parcels | iMAP `PlanningCadastre/MD_ParcelBoundaries/MapServer/0` | per county, `JURSCODE` |
| Zoning, Frederick | Frederick County GIS `PlanningAndPermitting/Zoning/MapServer/1` | `TYPE` (coded domain) |
| Zoning, Carroll | Carroll County AGOL `Zoning/FeatureServer/0` | `Zoning` |
| Zoning, Washington | Washington County AGOL `Washington_County_Zoning/FeatureServer/21` | `Zone` / `Zone_Full` |
| MALPF easements | iMAP `Environment/MD_ProtectedLands/MapServer/4` + Frederick `AgPreservation/8` | favorable |
| County PDR/TDR/IPP/Critical Farms | `MD_ProtectedLands/MapServer/9` (easements only, no CREP) + Frederick `AgPreservation/5`, `/3`, `/2` | favorable |
| Rural Legacy | `MD_ProtectedLands/MapServer/1` + Frederick `AgPreservation/10` | favorable |
| MET easements | `MD_ProtectedLands/MapServer/2` + Frederick `AgPreservation/9` | varies |
| Forest conservation easements | `MD_ProtectedLands/MapServer/3` (no exclusions) + Frederick `ForestResource/2` minus releases `/3`, Frederick banking `/0`, Carroll `Forest_Conservation_Easement/0` (recorded), Washington `Forest_Conservation_Easements_View/0` (deduped) | hostile |
| CREP enrolled farms (mapped) | `MD_ProtectedLands/MapServer/9`, `OthrPrgNm == 'CREP'` | varies: whole farms, flag only |
| DNR, federal, local protected lands; Frederick other easements | `MD_ProtectedLands/MapServer/0`, `/8`, `/5`; Frederick `OtherEasementsOrRestrictions/0` | varies |
| Wetlands | USFWS `wetlandsmapservice/.../Wetlands/MapServer/0` (live NWI) | physical, Riverine excluded |
| Floodplain | iMAP `Hydrology/MD_Floodplain/MapServer/1` | `SFHA_TF == 'T'`, zones A, AE, AH, AO |
| Streams (riparian inference) | USGS NHD HR `nhd/MapServer/6` (perennial + intermittent) + iMAP SHORE lines | 100 ft each side |
| State road ROW | MDOT SHA `MDOT_SHA_Right-Of-Way_(Polygons)/FeatureServer/32` minus full-access-control corridors | polygons per SHA grid |
| Tax-map road ROW | `ACCTID = 'ROW'` rows of the parcel layer | polygons |
| County roads | Frederick `Basemap/Centerlines/0` (OWNERSHIP/JURISDICTION/ICADCLASS), Carroll `Roads_CarrollCounty/0` (`ROADCLASS`), Washington `Road_Centerlines_3_view/2` (`Road_Code`) | public only, no limited access, ramps, driveways |
| DEM | iMAP LiDAR `Statewide/MD_statewide_dem_m/ImageServer` | per-parcel `exportImage`; also the Stage 6 / Stage 8 line of sight |
| County boundaries | iMAP `Boundaries/MD_PoliticalBoundaries/MapServer/1` | study area |
| Municipal zoning (fill) | Frederick `PlanningAndPermitting/Zoning/MapServer/0` (11 towns), Washington `Sharpsburg_Town_Zoning_view/0` | consulted where the county layer says MUN / TOWN |
| Building footprints | iMAP `PlanningCadastre/MD_BuildingFootprints/MapServer/0` | locate dwellings / churches on SDAT-flagged parcels (Stage 5) |
| Schools | iMAP `Education/MD_EducationFacilities/MapServer/5`, `/6` + SDAT school exemption classes | 300 yd zones |
| Sewer service categories | Frederick `WaterSewerServiceAreas/MapServer/2`, `/3` (`SP_Type`); Carroll `SewerServiceAreas_CarrollCounty/0` (`Service`); Washington `vw_Sewer Service Area_Public/0` (`Priority_Designation`) | planned vs existing (Stage 7) |
| PFA, growth areas | iMAP `MD_PriorityFundingAreas/0`; Frederick `CommunityGrowthArea/2`, Carroll `GrowthAreas_CarrollCounty/0`, Washington `Growth_Areas_2025_View/0` | Stage 7 |
| Residential pipeline | Frederick `ResidentialDevelopmentPipeline/MapServer/0`, `/1` (`D_AMinusC_AvailablePipeline`) | approved-unbuilt units; Carroll / Washington publish no unit counts |
| MPRP routes | PSEG `pseg_mprp_proposed_route_20241115_public_copy/FeatureServer/1` (centerline), `/2` (150 ft ROW), `public_comment_basedata/FeatureServer/121` (550 ft alternative-route corridor); Frederick `PlanningAndPermitting/MPRP/MapServer/0` | Stage 8, every studied route |
| HV lines, substations | HIFLD `Electric_Power_Transmission_Lines/FeatureServer/0` (≥ 100 kV); PSEG `public_comment_basedata/FeatureServer/113` (HIFLD substations) | Stage 8 |
| Data centers | Frederick `PlanningAndPermitting/CDI/MapServer/0` (Critical Digital Infrastructure overlay) + Doubs substation point | Stage 8 |
| Property sales | iMAP `PlanningCadastre/MD_PropertySales/MapServer/0` (`CONVEY1`, `CONSIDR1`, `TRADATE`, `SALIMPVL`) | Stage 9 comps, 5 mi beyond the study area |
| AADT, CTP | iMAP `MD_AnnualAverageDailyTraffic/MapServer/1` (2010–2018 + current); MDOT `..._CTP_FY2025_FY2030/FeatureServer/0` (FY26–31 project points) | Stage 10 corridor durability |
| Routing | OSRM demo `router.project-osrm.org` (table service) or Google Routes with `GOOGLE_MAPS_API_KEY` | Stage 10 |

SHA plat boundaries, the HPMS access-control layer, statewide centerlines,
MDP generalized zoning, the MPRP route, the residential pipeline and other
later-stage layers are listed under `reference_layers:` in the config.

### Study area

`config/study_area.geojson` is built from the iMAP detailed county
boundaries (all of Frederick, the Mount Airy corner of Carroll, and the
Sharpsburg / Keedysville / Boonsboro / Rohrersville area of Washington;
2,269 km²). An expanded northern-Carroll run (3,270 km²) is a one-line change:

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
| `encumbrances.gpkg`, `row_public.gpkg` | encumbrance geometry per parcel × type and the public ROW used, for the dossier maps |
| `envelope.gpkg`, `occupied_structures.gpkg` | dischargeable envelope per parcel; the dwellings / churches that shaped it (Stage 5) |
| `valuation_comps.csv` | the arms-length agricultural comps behind Stage 9 |
| `shortlist.csv`, `shortlist_excluded.csv`, `owner_list.csv` | ranked shortlist with score components, hard-rule exclusions with reasons, one row per owner + mailing address |
| `dossiers.pdf` | `farmsearch dossiers`: map + record pages per shortlist parcel |
| `checkpoint_stage{1..9}.pkl` | resume points (`--resume`) |
| `parcels_scored.gpkg` / `.csv` / `.geojson` | the per-parcel record |
| `summary.json` / `summary.md` | counts per stage and county, missing layers, and the list of things the pipeline cannot determine |

### Per-parcel record

The spec's full record. Stages 5–10 add `dischargeable_envelope_acres`,
`dischargeable_envelope_longest_dim_yards`, `dwellings_with_line_of_sight`,
`candidate_backstop_slopes`, `mprp_tier`, `adjacent_residential_zoning_acres`,
`adjacent_planned_sewer`, `approved_unbuilt_units_within_2mi`,
`adjacent_permanently_eased_acres`, `est_market_value`, `est_per_acre`,
`comp_basis`, `commute_bwi_peak_min`, `commute_langley_peak_min`,
`commute_nova_peak_min`, `route_redundancy`, `corridor_durability_score`, each
with its supporting detail (envelope blocks, nearest dwelling, MPRP distance
and variant, sewer / PFA / growth-area status, comp bands, free-flow minutes,
egress path count, corridor AADT trend). Four columns say how much the answer
is worth: `same_owner_structures_within_safety_zone` (houses on the seller's
other parcels, which constrain the buyer unless the whole holding is bought),
`dwellings_line_of_sight_unevaluated` (no DEM under that profile),
`adjacent_boundary_covered_pct` (how much of the parcel's boundary is a known
parcel or a road) and `approved_unbuilt_units_radius_ft` (the radius actually
used). A per-county layer that did not load leaves its column empty rather
than reporting a confident zero. The Stages 1–4 portion:

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
`zoning_layer_missing`, `extends_beyond_layer_context_verify_constraints_there`.

### Flags raised by Stages 5–10

`dischargeable_envelope_below_minimum`,
`dischargeable_envelope_too_short_for_range_bay`,
`target_shooting_verify_county_discharge_ordinance_and_zoning`,
`safety_zones_from_parcel_points_no_footprints`,
`safety_zone_located_by_parcel_point`,
`envelope_assumes_same_owner_dwellings_acquired`,
`viewshed_dem_nodata_some_dwellings_unevaluated`,
`all_nearby_dwellings_terrain_shielded`, `natural_backstop_candidate`,
`adjacent_residential_zoning`, `adjacent_planned_sewer_service`,
`adjacent_zoning_unmapped_residential_share_may_be_understated`,
`approved_unbuilt_units_not_published_for_this_county`,
`adjoining_parcels_incomplete_check_neighbouring_jurisdiction`,
`mprp_tier1_intersects_studied_route_exclude`,
`mprp_tier2_within_exclusion_buffer`, `mprp_routes_beyond_reach`,
`near_existing_hv_transmission_corridor`, `near_substation`,
`near_data_center_development`, `estimated_value_above_price_ceiling`,
`valuation_segment_borrowed`, `single_egress_no_incident_tolerance`,
`no_state_road_reached_in_graph`, `commute_unavailable`.

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
  variant, plus the acres that a permit would reconnect. Frontage over such a
  constraint is still an entry point: a driveway may cross a mapped floodplain.
- **Unknown is not zero.** A layer that covers only some counties leaves its
  column empty in the others; a line of sight the DEM cannot answer is counted
  as unevaluated, not as terrain shielding; a route layer read with nothing in
  range means tier 0, while no route data at all means no tier.
- **The reading extent follows the parcels.** After Stage 1 the layer context
  is grown to cover every parcel that will be scored, so a farm straddling the
  study line meets its own constraints and roads over its whole area.
- **Checkpoints cannot go stale.** Re-running an early stage deletes the later
  checkpoints, a resume never starts below Stage 4 for Stages 5–10, and a range
  that starts later than Stage 1 requires `--resume`.

## Layout

```
config/            pipeline.yaml, study areas, parcel schema map, zoning mappings
farmsearch/        package
  io/              ArcGIS REST client (ESRI JSON paging), loaders, schema verification,
                   per-county parcel pull, study-area builder
  geometry/        encumbrance position, frontage probing, reserve strips, connectivity
  stages/          stage1..stage10
  slope.py         DEM (local file or iMAP ImageServer) → steep-area polygons
  terrain.py       DEM windows, line of sight, slope/aspect (Stages 6 and 8)
  deliverables.py  shortlist ranking, owner list, PDF dossiers
  pipeline.py      orchestration, checkpoints, outputs
  fixtures/        synthetic validation dataset
tests/             pytest suite (runs against the fixture)
docs/              the spec
```
