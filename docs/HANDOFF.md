# Handoff — continue the Farm-Search build in a new session

This file exists so a fresh Claude Code session can pick up exactly where the
previous one stopped without the old conversation. Read `README.md` first.

## State of the branch `claude/geodata-md-gov-access-zvtlkc`

- **All ten stages** of `docs/farm_parcel_screening_spec.md` plus the
  deliverables (shortlist, owner list, PDF dossiers) are implemented and
  validated against the synthetic fixture (96 pytest tests, `python -m pytest`).
  Stages 1–4 have been run end to end on the real three-county data several
  times (results below); the first full Stage 1–10 run is recorded in the
  "Stages 5–10 on the real data" section.
- Session 3 (2026-09-03) fixed three Stage 4 faults found by a visual spot
  check (edge parcels losing their roads to the study-area clip, flag-lot
  lanes severed by a mapped flood zone, a too-strict landlocked threshold),
  filled the municipal zoning holes, and built Stages 5–10 on live-verified
  sources; every design decision is in the sections below.
- **Every data source for Stages 1–4 has been located and verified live**
  (layer JSON read, field list checked, feature count over the study area
  confirmed) and is wired into `config/pipeline.yaml`. The verification
  notes per layer are below.
- The pipeline can now pull all of its inputs itself:
  ```bash
  pip install -e ".[dev]" && python -m pytest
  farmsearch build-study-area --config config/pipeline.yaml
  farmsearch fetch-parcels    --config config/pipeline.yaml
  farmsearch fetch            --config config/pipeline.yaml
  farmsearch verify-schema    --config config/pipeline.yaml
  farmsearch run              --config config/pipeline.yaml --stages 1-4
  farmsearch run              --config config/pipeline.yaml --stages 4 --resume   # after an interrupted run
  ```
  The run writes `checkpoint_stage{1,2,3}.pkl` under `outputs/`; `--resume`
  continues from the state saved after the stage before the first one
  requested. (This session's container restarted twice under a 25-minute
  run; the checkpoints are the answer.)
- Network: the previous session's environment blocked every Maryland host.
  This session ran with **Full** network access. `geodata.md.gov` itself was
  returning HTTP 503 "Site Maintenance" throughout; **`mdgeodata.md.gov`
  serves the identical iMAP service tree** (same paths) and is used
  everywhere. `planning.maryland.gov` download pages (MdProperty View /
  FINDER Quantum) returned 404. All county GIS servers and MDOT SHA services
  were reachable.

## What was learned from the live data (and what changed because of it)

1. **Parcel schema.** The iMAP Parcel Boundaries layer has 117 fields.
   `config/schema/parcels.yaml` was reconciled: `OWNERZIP` (not `OWNZIP`),
   plus `POLYACRES`, `LANDAREA`/`LUOM`, `PTYPE`, `POLYID`, `DR1LIBER`/
   `DR1FOLIO`, `OOI`, `FCMACODE`, `AGFNDAREA`, `SDATWEBADR`, dates, etc.
   Jurisdiction codes really are `FRED` / `CARR` / `WASH` (statewide
   124,459 / 76,249 / 62,327 polygons). `ACRES` agrees with the polygon
   area to ~2% median; `POLYACRES` to ~0.1%.
2. **Owner names are not public.** Not in the parcel layer, not in the
   Parcel Points layer, and the SDAT extract on opendata.maryland.gov is the
   "Hidden Property Owner Names" version. Only the owner mailing address is
   published. `owner_name` is now **optional**: `owner_type` = `unknown`,
   `owner_key` and the Stage 4 same-owner test use the normalized mailing
   address, `deed_ref` (liber/folio) is carried as a second same-owner
   signal, and every parcel gets `owner_name_unavailable_lookup_sdat` plus
   `sdat_url`. A licensed MdProperty View extract with `OWNNAME1` restores
   the original behaviour with no code change.
3. **Placeholder account IDs.** Besides `ROW` (~18k Frederick, ~6k Carroll,
   ~1k Washington road right-of-way slivers, split by `farmsearch
   fetch-parcels` into `data/raw/parcels_row/` and reused as a ROW layer),
   the layer carries `WATER`, `WATER_CANAL`, `WATER_IS`, `RAILROAD`, `RR`,
   `UNK`, `UNKNOWN`, `NO ID`, `GCE`/`LCE` (condominium common elements),
   `COMMON`, `OS`, `SWM`, `PARK`, `PRIVATE ROW`, `ROW_ALLEY` and nulls.
   Dissolved by ID these became a 2,642-acre "WATER" parcel in Washington
   and an 1,801-acre "UNK" parcel in Carroll that passed Stage 1. A real
   SDAT account ID is 8+ alphanumerics with a digit (`1101000098`,
   `1102502WH`); `parcels.account_id_regex` (see `farmsearch/accounts.py`)
   now excludes everything else, and the fixture sets it to null.
3b. **Owner type without names.** SDAT's exemption class (`DESCEXCL`, e.g.
   `STA Parks`, `JUR Schools`, `MUN Public Works Properties`, `NPF Other`,
   `PVT Churches, Synagogues, & Parsonages`) identifies government and
   nonprofit/church holders. `owner_type` is filled from it when no name is
   available (`owner_type_basis` = `exemption_class`); private taxable land
   stays `unknown`.
4. **How these ArcGIS servers actually page (all learned the hard way).**
   - GeoJSON output (`f=geojson`) was 24 s/page on the parcel layer and
     unparseable on the county-boundary MapServer; ESRI JSON (`f=json`) is
     1–2 s/page. **All REST reads page ESRI JSON through GDAL's ESRIJSON
     driver** (`ArcGISLayer.iter_pages`, `fetch_layer_gdf`).
   - `resultOffset` paging under a spatial filter gets slower with every
     page (the server re-runs the query and skips); the parcel layer took
     ~20 s/page by page 40. A bbox filter alone made a keyset page 15 s vs
     1.2 s. The parcel pull therefore uses **keyset paging**
     (`WHERE ... AND OBJECTID > last ORDER BY OBJECTID`) with **no bbox**
     (the county `where` is cheap; `fetch-parcels` never sends a bbox, so
     the per-county cache is always the whole county). All three counties:
     6 minutes, exact counts.
   - `returnCountOnly` and `returnDistinctValues` **ignore the spatial
     filter** on the iMAP MapServers (NWI "77,548 in bbox" was the
     statewide count; the true bbox count is 6,830). `returnIdsOnly` does
     honour it. Layer downloads therefore use **ID mode**: fetch the
     matching object ids, then the features in chunks by `objectIds`
     (POST), and raise unless every id came back.
   - Pages come back short **without** `exceededTransferLimit`; the pager
     stops only on an empty page.
   - The FEMA floodplain MapServer (and FEMA's own NFHL service) return
     `error 500` for any page-style query with geometry over this area, and
     for some `objectIds` chunks of 50 too. The ID fetch splits a failing
     chunk in halves down to single features (then tries a generalized
     geometry, then records the id as unservable). All 2,821 floodplain
     polygons in the study area came through this way, none generalized.
   - When every retry fails the fetch **raises** and `farmsearch fetch`
     reports `FAILED <layer>`; the run then lists it under `missing_layers`.
5. **Zoning layers publish codes, not always names.** Frederick's `TYPE`
   has a coded-value domain (22 codes); Carroll's `Zoning` and Washington's
   `Zone` do not (`Zone_Full` carries the name). `config/zoning/*.yaml`
   were generated from the live layers with `farmsearch zoning-domains` and
   `is_agricultural` filled from the county ordinances (citations inline).
6. **LiDAR DEM.** The statewide DEM is an ArcGIS ImageServer
   (`Statewide/MD_statewide_dem_m`, 0.3048 m native, EPSG:6487, meters;
   `_ft` variant also exists). `exportImage` in EPSG:26985 at 5 m with
   `noData=-9999` returns a Float32 GeoTIFF in ~0.3–1 s. `slope.py` gained
   `ImageServerDEM` + `steep_polygons_from_imageserver` with an on-disk
   window cache; `slope.dem_url` in the config selects it.
7. **Study area.** `farmsearch build-study-area` assembles the polygon from
   the iMAP detailed county boundaries (`study_area_build:` in the config):
   all of Frederick, Carroll clipped to the Mount Airy box, Washington
   clipped to the SE box. The previous session's placeholder envelope ended
   at lon -77.69 and left **Boonsboro and Rohrersville outside**; the box now
   runs to -77.58 and a test checks all four named towns fall inside.
   `config/study_area.geojson` (2,269 km²) and
   `study_area_expanded_carroll.geojson` (3,270 km²) are committed
   (political boundaries; the earlier physical-boundary build clipped the
   Potomac and was 2,248 km²).
8. **Adversarial code review** (5 reviewers, 2 refuters per finding) confirmed
   20 defects in the first version of this work, all fixed and covered by
   tests: the parcel cache depended on the study-area bbox it was fetched
   with (parcels are now always whole counties, both files written
   atomically); `distinct_values` had been orphaned outside its class
   (`zoning-domains` would have crashed for Carroll/Washington); DEM nodata
   cells filled with the window mean created a false ring of "steep" cells
   (the ring is now masked); a failed slope window was reported as flat
   (now `slope_evaluated = False` + `slope_window_failed_not_evaluated`, and
   `slope_windows_failed` in the summary); the exportImage interpolation
   enum was misspelled; an empty cached layer crashed a `where` filter;
   nameless, addressless parcels collapsed into one owner key (now keyed by
   account, counted as `owner_key_unavailable`); `deed_ref` was reported but
   never used (identical deed liber/folio is now a same-owner match in the
   reserve-strip and frontage tests); and a handful of CLI / config edge
   cases (county-code validation, error exit codes, `[]` vs missing lists,
   `build-study-area` default output per variant).

## Independent source verification (27 agents, 2026-09-02)

Every configured endpoint was re-verified by an independent agent that
fetched the layer metadata, counted ids in the study bbox, sampled
features, ran distinct-value queries and searched for better sources; a
critic then checked the inventory against the spec. What changed because
of it:

- **Acreage basis is now geometry.** The live `ACRES` field stores square
  feet on ~335 Frederick rows with `LUOM='A'` (e.g. ACRES 65,995 for a
  1.51-acre lot); 38 rows with ACRES ≥ 20 have POLYACRES < 5. POLYACRES
  matches the polygon area to 0.1%. SDAT acreage is still reported and the
  >10% disagreement flag still fires.
- **Placeholder polygons are blockers, not parcels.** RAILROAD, WATER,
  WATER_CANAL, GCE/LCE, PRIVATE ROW, UNK etc. stay in the Stage 4 neighbour
  set as non-account rows (`is_account = False`,
  `stage1_pass_reason = non_parcel_polygon`) so a railroad or canal between
  a farm and the road reports as a foreign blocker; they are excluded from
  every count. They keep one row per polygon (492 in the study area): the
  multi-row account dissolve skips them, since 262 `UNK` polygons scattered
  over a county are not one account. Account-shaped ids with no SDAT record
  (`PTYPE` null, ~220 rows) are dropped (`parcels.require_non_null`).
- **Municipal zoning holes are known-unknowns.** Frederick `MUN` and
  Washington `TOWN` are placeholders, not districts (`is_agricultural:
  unknown`); parcels there are retained as `zoning_unknown_retained`
  instead of being filtered out as non-agricultural. Carroll's layer has no
  polygons inside town limits, which already produced the same result.
- **Fresher county easement layers added alongside the state
  compilation**: Frederick MALPF (/8), IPP (/5), Critical Farms (/3),
  County Held (/2), Rural Legacy (/10), MET (/9) and forest banking
  easements (ForestResource/0). Same `type` as the state entry; Stage 2
  unions by implication before summing.
- **Erase layers.** Frederick FRO easements are erased with the county's
  release polygons (ForestResource/3: 24% of released acreage was still
  drawn); the SHA right-of-way polygons are erased with a 200 ft buffer of
  the MDOT SHA full-access-control corridors (I-70, I-270, I-81, US 15,
  US 40, US 340) so a fenced freeway is not frontage (`erase:` on a
  constraint or ROW layer; a missing erase layer degrades with a warning).
- **Replacements**: USFWS live NWI (14.4k polygons in the study bbox vs
  6.8k in iMAP's undated 2016 copy; Riverine excluded, dotted field names
  need backticks in `where`); USGS NHD High Resolution flowlines for the
  riparian buffer (iMAP's SHA hydrography misses ~1,100 km of headwater
  streams; perennial + intermittent StreamRiver only) plus iMAP SHORE lines
  for the banks of the Potomac, Monocacy and ponds; Washington
  `Road_Centerlines_3_view` (the `_Public_View` is a frozen Oct-2022
  snapshot); political county boundaries for the study area (the physical
  boundary is clipped to hydrography and cut riverfront parcels).
- **Filters tightened**: floodplain `SFHA_TF == 'T'`; state FCA `Type !=
  'exclusion'`; MALPF / county PDR `Category == 'Easement'` (Next Generation
  "options" are not easements); Washington FCA deduped by geometry (295
  identical-geometry duplicates, a 93% overcount); Frederick roads filtered
  on OWNERSHIP, JURISDICTION and ICADCLASS (A10 limited access, A63 ramps,
  A66 crossovers, A71 trails, A73 alleys, A74 driveways); Carroll blank
  ROADCLASS kept (86% are municipal streets); Washington not-built and
  private roads excluded.
- **DEM**: the mosaic has artefact elevations down to -22 m in Frederick
  County (`dem_min_valid_m: 30` masks them); a 200 response with a JSON
  error body is now rejected by TIFF magic bytes; the Carroll corner is
  2014-15 LiDAR (`carroll1m`), Frederick/Washington are 2021.

Staleness ledger (print with every run): parcel geometry FRED 2024NOV /
CARR 2025JUL / WASH 2025MAR with SDAT attributes 2026MAY (county parcel
layers are edited through 2026-08/09 but carry no SDAT attributes, except
Washington `Property_view/42`, which also carries OwnName1/2 — the only
public owner-name source found); state FCA compilation 2019; state MET
rows for these counties end 2020; state MALPF lags the counties by 2–7% of
acreage; state county-PDR lags Frederick by ~14 months; Rural Legacy is
missing ~480 ac of 2025-26 settlements; SHA access-control 2017.

Not yet consumed (verified, listed under `reference_layers` or noted):
SSURGO prime-farmland classes (valuation), municipal zoning layers for the
holes (Frederick layer 0 for 11 towns, Mount Airy's own layer), Frederick
Creek ReLeaf riparian easements, MHT preservation easements, NHD waterbody
polygons, SHA plat sheets (rectangles of plat extents, unusable for
reserve-strip acreage — no public source for access-control takings
exists; Stage 4's geometry heuristic is the substitute).

## Verified sources (2026-09-02)

Counts are `returnIdsOnly` counts inside the study-area bbox (the only count
these servers apply the spatial filter to) and equal the cached feature counts.

| Layer | URL | Verified | Notes |
|---|---|---|---|
| Parcels | `mdgeodata.md.gov/imap/rest/services/PlanningCadastre/MD_ParcelBoundaries/MapServer/0` | 117 fields, pagination, 2,288,725 rows | see above |
| Zoning Frederick | `fcgis.frederickcountymd.gov/server_pub/rest/services/PlanningAndPermitting/Zoning/MapServer/1` | 81,252 polygons, `TYPE` domain (one polygon has a null TYPE and is ignored) | layer 0 = municipal zoning; `MUN` = inside a municipality |
| Zoning Carroll | `services.arcgis.com/Uf0DiYpD9NOFO5YH/.../Zoning/FeatureServer/0` | 238 in study bbox, `Zoning` (13 values) | `ComprehensiveRezoning` service also exists (pending map) |
| Zoning Washington | `services2.arcgis.com/uxxyl33jRTSmjre5/.../Washington_County_Zoning/FeatureServer/21` | 339 in study bbox, `Zone` (21 values) | sublayer 26 = overlays, 23 = urban growth areas |
| MALPF | `.../Environment/MD_ProtectedLands/MapServer/4` | 576 in study bbox (2,849 statewide) | `JURSCODE` filter server-side |
| County PDR/TDR/IPP | `.../MD_ProtectedLands/MapServer/9` | 471 in study bbox (+44 CREP); `OthrPrgNm` ∈ County Held, Critical Farms, IPP, Next Generation, TDR, CREP… | CREP split out |
| Rural Legacy | `.../MD_ProtectedLands/MapServer/1` | 281 in study bbox | `RLArea` |
| MET | `.../MD_ProtectedLands/MapServer/2` | 164 in study bbox | `Easement`, `COHOLD` |
| FCA easements (state) | `.../MD_ProtectedLands/MapServer/3` | 3,526 in study bbox (26,334 statewide); `Jurisdict` incl. Frederick/Carroll/Washington County | |
| FRO easements Frederick | `fcgis.../PlanningAndPermitting/ForestResource/MapServer/2` | 2,436 in study bbox | layer 0 banking, layer 3 releases |
| FCE Carroll | `services.arcgis.com/Uf0DiYpD9NOFO5YH/.../Forest_Conservation_Easement/FeatureServer/0` | 867 in study bbox | `STATUS == 'RECORDED'` |
| FCE Washington | `services2.arcgis.com/uxxyl33jRTSmjre5/.../Forest_Conservation_Easements_View/FeatureServer/0` | 1,312 in study bbox | `RELEASE_DATE.isnull()` |
| DNR lands | `.../MD_ProtectedLands/MapServer/0` | 32 in study bbox | varies |
| Other easements Frederick | `fcgis.../OtherEasementsOrRestrictions/MapServer/0` | 37 in study bbox | varies |
| NWI wetlands | `.../Hydrology/MD_Wetlands/MapServer/2` | 6,830 in study bbox (77,548 statewide) | DNR wetlands = layer 1 |
| FEMA floodplain | `.../Hydrology/MD_Floodplain/MapServer/1` | 2,820 in study bbox; `FLD_ZONE` A/AE/AH/AO/VE/X | X excluded; objectIds chunks only |
| Streams | `.../Hydrology/MD_Waterbodies/MapServer/2` (detailed) | 14,297 in study bbox | generalized = layer 0 |
| SHA ROW polygons | `services.arcgis.com/njFNhDsUCentVYJW/.../MDOT_SHA_Right-Of-Way_(Polygons)/FeatureServer/32` | 45 multipolygons in study bbox, one per SHA grid (`GRID_ID`), ~20k vertices each, EPSG:2893 | includes interstate ROW |
| SHA plats | `utility.arcgis.com/usrsvcs/servers/cb9897ee…/rest/services/OHD_PSD/RecordedPlats_Maryland_MDOTSHA/MapServer/0` | reachable; count not id-verified | reference only |
| Roads Frederick | `fcgis.../Basemap/Centerlines/MapServer/0` | 20,436 in study bbox; `OWNERSHIP` COUNTY/STATE/FEDERAL/municipalities/PRIVATE/PRIVATE-COUNTY/OTHER/UNKNOWN; `ROADTYPE` incl. INTERSTATE/RAMP | filtered |
| Roads Carroll | `services.arcgis.com/Uf0DiYpD9NOFO5YH/.../Roads_CarrollCounty/FeatureServer/0` | 5,469 in study bbox; `ROADCLASS` COUNTY/STATE/PRIVATE/blank | filtered |
| Roads Washington | `services2.arcgis.com/uxxyl33jRTSmjre5/.../Road_Centerlines_Public_View/FeatureServer/0` | 10,988 in study bbox; `Road_Code` County/State/Private/municipal/not-built | filtered |
| DEM | `mdgeodata.md.gov/lidar/rest/services/Statewide/MD_statewide_dem_m/ImageServer` | exportImage OK | per-county folders also exist |
| County boundaries | `.../Boundaries/MD_PhysicalBoundaries/MapServer/0` | 24 counties | |

## Stages 1–4 on the real data (2026-09-02)

`farmsearch run --stages 1-4` on the verified configuration: every parcel
of the three counties loaded, study area = Frederick County plus the
Carroll and Washington corners cut from the political boundaries. 29
minutes on the first run, 22 on the final one, with all layers and DEM
windows cached (Stage 1 ≈ 6 min, Stage 2 ≈ 7, Stage 3 ≈ 4, Stage 4 ≈ 17). `outputs/prev_run/` (local, not
versioned) holds the run immediately before the reserve-strip and
blocker fixes below; the pre-verification run (SDAT acreage, CREP as a
subtraction, older layers) passed 2,527 parcels against today's 2,616.

### Stage 1

| county | parcels in study area | ≥ 40 ac | ag-zoned | zoning unknown | Stage 1 pass | median ac | acres passing |
|---|---:|---:|---:|---:|---:|---:|---:|
| Frederick (whole) | 105,797 | 2,059 | 1,883 | 39 | 1,922 | 89 | 205,389 |
| Carroll (Mount Airy corner) | 11,153 | 223 | 208 | 2 | 210 | 78 | 20,531 |
| Washington (SE) | 12,069 | 450 | 439 | 8 | 447 | 94 | 48,746 |
| total | 129,019 | 2,732 | 2,530 | 49 | 2,579 | | 274,666 |

Reading zoning over the context band rather than the study polygon changed
three Carroll parcels that straddle the study line: covered only in part, each
read as majority `Conservation` (agricultural) and passed; covered in full,
each is majority `I-2` (industrial) and is correctly excluded. That is the
whole difference between the 2,579 parcels of the 2026-09-02 run and today's
2,576.

Sanity: hundreds to low thousands per county, as the spec expects. 2,041
distinct owner keys (mailing address) among the 2,616. Acreage is the
polygon geometry for every parcel (`acreage_basis: geometry`; the SDAT
`ACRES` field is in square feet for some rows). The 99 "zoning unknown"
parcels are inside municipalities (Frederick `MUN` 89, Washington `TOWN`
8, Mount Airy 2), whose zoning the county layers do not carry; they are
retained with `zoning_unknown_retained`. Owner names are not public
(`owner_name_available_pct` 0); by exemption class the passing parcels
are 195 government, 41 religious / non-profit, 2,380 untyped.

### Stages 2–4

| | total |
|---|---:|
| encumbrance rows (24 layers, 0 missing) | 8,790 |
| parcels with a hostile easement / a favorable easement | 560 / 1,078 |
| parcels bisected by a hostile constraint | 1,555 |
| usable acres of gross acres | 188,551 of 274,666 |
| parcels whose usable area falls below 40 ac | 774 |
| DEM windows failed | 0 |
| public ROW features | 39,950 |
| landlocked_apparent | 145 |
| frontage blocked by a foreign parcel (≥ 95 %) | 36 |
| parcels with unreachable islands | 1,737 |
| largest reachable block ≥ 40 ac / below | 1,291 / 1,288 |
| reserve strip detected | 127 |

| county | scored | landlocked | frontage blocked | reserve strip | usable < 40 ac | largest reachable ≥ 40 ac | with islands |
|---|---:|---:|---:|---:|---:|---:|---:|
| Frederick | 1,922 | 84 | 24 | 85 | 574 | 972 | 1,285 |
| Carroll | 210 | 15 | 3 | 19 | 70 | 92 | 164 |
| Washington | 447 | 46 | 9 | 23 | 130 | 227 | 288 |

Reserve strips after the tightening: 186 strip rows on 150 parcels (130 flagged with a foreign owner, 25 same-owner rows), 170 distinct strip accounts, all vacant by the SDAT improvement fields, median 51 ft wide by 701 ft long. The first run flagged 744 parcels. Elapsed 22 min.

Performance note: intersecting every parcel with the detailed county
boundary took >15 minutes; `attribute_study_area` now intersects only
parcels that straddle the boundary (interior parcels are found with the
spatial index), which is the difference between 15 minutes and about one.

## Stages 5–10: sources, decisions and assumptions (2026-09-03)

Verified live in this session (id-verified counts inside the study bbox):

| Need | Source | Notes |
|---|---|---|
| Statute | NR §10-410 at `mgaleg.maryland.gov` | 150 yd dwelling/church/occupied building, 300 yd school (school hours), archery 50 yd in Frederick/Carroll/Washington (elevated stand in Washington); owner/occupant permission exemption |
| Dwellings, churches, schools | the parcel fabric: `SQFTSTRC` ≥ 400 under residential/agricultural `DESCLU`; `DESCEXCL` matching church/synagogue/parsonage (929 parcels) or school/college (241) | located with iMAP building footprints (199,193 in bbox + 3,000 ft); parcel point when no footprint. HIFLD "places of worship" is an IRS geocode list (PO boxes) and was rejected |
| Municipal zoning | Frederick `Zoning/MapServer/0` (11 towns, 1,156 polygons, `Municipality`/`District`/`Code`); Washington `Sharpsburg_Town_Zoning_view/0` (`TC`, `TR`) | City of Frederick, Boonsboro and Keedysville publish no service (AGOL and city hosts searched); their parcels stay `zoning_unknown_retained` |
| Sewer categories | Frederick `WaterSewerServiceAreas/2` (county, per parcel: S-1 35,533; S-3/S-3 DEV 2,253; S-4/DEV 144; S-5/DEV 2,370; PS 536) and `/3` (city); Carroll `SewerServiceAreas_CarrollCounty/0` (`Service`: Existing/Priority/Future/Long Range/No Service); Washington `vw_Sewer Service Area_Public/0` (`Priority_Designation` 1/3/5/7) | planned = S-3..S-5 + PS; Priority/Future/Long Range; 3-Programmed / 5-Long Term |
| PFA / growth | iMAP `MD_PriorityFundingAreas/0` (573); Frederick `CommunityGrowthArea/2` (25), Carroll `GrowthAreas_CarrollCounty/0` (7), Washington `Growth_Areas_2025_View/0` (3) | |
| Residential pipeline | Frederick `ResidentialDevelopmentPipeline/0` (82 subdivisions; `A_ApprovedUnits`, `C_DevelopedOrPermittedUnits`, `D_AMinusC_AvailablePipeline`) and `/1` (Eaglehead) | Carroll `DevelopmentsInProcess` is commercial only; Washington `Subdivision_view` has names only: units are Frederick-only (gap) |
| MPRP | PSEG/Stantec `pseg_mprp_proposed_route_20241115_public_copy/1` (centerline, 140 km), `/2` (150 ft ROW); `public_comment_basedata/121` "Alternative Routes 550 ft Study Corridor – dissolved"; Frederick `MPRP/MapServer/0` (county copy) | all studied routes honoured; status note in config, re-verify |
| HV lines / substations | HIFLD hub `Electric_Power_Transmission_Lines/0` (74 in bbox, 115–500 kV, `SUB_1`/`SUB_2`); PSEG basedata `/113` HIFLD substations (59, `NAME`; DOUBS at −77.5148, 39.2964) | no standalone HIFLD substation service was reachable |
| Data centers | Frederick `PlanningAndPermitting/CDI/MapServer/0` Critical Digital Infrastructure overlay (2,612 ac, Ordinance 26-01-001, 2026-01) | + Doubs substation point of concern (3 mi) |
| Sales | iMAP `MD_PropertySales/0`, a rolling window of recent transfers (14,123 in bbox across all land uses; 861 agricultural within the 5-mile comp margin, `CONVEY1` 1: 120, 2: 49, 3: 75, 4: 617 with $0 consideration) | arms-length = 1–3 (assumption from the data: code 4 = $0 transfers); fetched 5 mi beyond the study area with `DESCLU='Agricultural'` and **no acreage filter**, because a code-3 transfer must be visible in all its accounts before it is collapsed into one comp; `min_comp_acres` is then applied to the collapsed transfer |
| Traffic | iMAP `MD_AnnualAverageDailyTraffic/1` (1,371 lines; `AADT_2010..2018`, `AADT`, K/D factors); MDOT CTP FY2026–31 `..._CTP_FY2025_FY2030/FeatureServer/0` (189 points; `MFCE_Name`, `TBU_Facility`) | capacity projects = Construction / Development programs |
| Routing | OSRM demo `router.project-osrm.org` (table service works, ~30 requests for 2,600 parcels); no Google/HERE key in the environment; `osmnx`/`pyrosm`/`pyvalhalla` are pip-installable and Geofabrik is reachable for self-hosting | peak minutes = free-flow × per-destination `peak_factor` (BWI 1.35, Langley/NoVA 1.7) until a traffic-aware key is supplied: a documented placeholder |

Decisions to keep in mind:

- **Context buffer.** Parcels, ROW and every layer are fetched and read out to
  `context_buffer_ft` (2,000 ft) beyond the study polygon; parcels in that band
  are context rows (`stage1_pass_reason = outside_study_area`, never scored or
  counted). Layers that need more reach carry `fetch_margin_ft`. After Stage 1
  the reading extent grows again to cover every parcel that will be scored
  (plus the frontage search radius), because a 300-acre farm straddling the
  study line is wider than the band; a parcel reaching past what was fetched
  keeps the flag `extends_beyond_layer_context_verify_constraints_there`.
  Zoning is read over the same extent, so a straddling parcel's majority code
  is computed from full coverage.
- **Passable vs usable.** `usable` still subtracts every hostile/physical
  constraint; reachability runs through `passable` = parcel minus what stops a
  vehicle (`blocks_travel`, default true; floodplain false). Lanes are never
  sliver-filtered.
- **Envelope.** The statute exempts the owner/occupant of the dwelling, and an
  acquisition buys the subject parcel only, so a house on the seller's *other*
  parcel constrains the buyer like any neighbour's: `exclude_same_owner` is
  **false**. Set it true only when the whole holding is bought at once; either
  way `same_owner_structures_within_safety_zone` reports the dependency and
  the flag `envelope_assumes_same_owner_dwellings_acquired` marks the parcels
  it changed. Every footprint ≥ 400 sq ft on a dwelling parcel counts as a
  candidate occupied building (barns included: pessimistic), and non-residential
  buildings people occupy (shops, clubhouses, exempt properties) get the same
  150 yd zone. A zone placed from the parcel's centre because no footprint
  matched is flagged (`safety_zone_located_by_parcel_point`). Target shooting is
  flagged on every parcel.
- **Backstops and viewshed.** A candidate backstop must face away from *every*
  dwelling within 1,000 yd, not merely the nearest, and is searched inside the
  parcel only. A line of sight that the DEM cannot answer counts as unevaluated
  (`dwellings_line_of_sight_unevaluated`), never as terrain shielding.
- **Stage 8 tiers.** 1 = inside a route corridor (150 ft, or the 550 ft
  alternative corridor polygon); 2 = within 2,000 ft or line of sight (40 m
  conductor height, bare-earth DEM); 3 = within 1 mile of a route, 1,000 ft of an
  HV line or 0.5 mile of a substation.
- **Stage 9 land price** = consideration − assessed improvement value at sale;
  a sale whose improvements exceed 80% of the price is a farmstead, not a land
  comp, and is dropped. Comps are read as far as the sales layer is fetched
  (5 miles), and a sale outside the parcel fabric takes its county from
  `JURSCODE`. A multi-account arms-length transfer (`CONVEY1 = 3`) is collapsed
  into one comp — acres and improvements summed — because the consideration
  covers the whole sale. Bands by county then pooled when a county has fewer
  than 5 comps; `est_market_value` adds the subject's own assessed improvements.
- **Stage 10** is reported only; shortlist weights for commute are 0. Route
  redundancy attaches every entrance to the real centerline geometry (edge split
  at the projection, so a curved segment does not push the entrance out of
  range) and counts road-disjoint paths to a state road within 5 miles as a
  max flow with unit edge capacities, so a loop through one road is not two
  routes. The `*_freeflow_min` columns stay empty under a traffic-aware engine.
  Durability = 100 − 45·min(1, units/1000) − 35·min(1, AADT growth/30%) + 20 if
  a CTP capacity project is nearby.
- **Shortlist** hard rules: MPRP tier 1 excluded (spec); the largest reachable
  block must clear `acreage_min` either strictly or with stream crossings
  permitted (the spec's flag-never-delete rule); government owners are not
  acquisition candidates. Everything else is a weight. Metrics are min-max
  scaled between the 5th and 95th percentiles, except booleans and tiers, which
  keep their full range so a rare flag keeps its penalty; MPRP tiers are mapped
  to severity (1 worst, then 2, then 3, then 0) before scaling.
- **Per-county data gaps are null, not zero.** Sewer, growth-area and
  approved-unbuilt-unit layers are per-county; a parcel in a county whose
  source did not load gets an empty column and a flag, never a confident
  "no planned sewer". Approved-unbuilt units are published by Frederick only.
- **Resume.** Stages 5–10 need the Stage 4 checkpoint; a resume never falls
  back to an earlier one, and re-running an early stage deletes the later
  checkpoints so a stale frame can never be reloaded. Stage 6 alone can resume
  from `envelope.gpkg` and `occupied_structures.gpkg`.

## Stages 5–10 on the real data

`farmsearch run --stages 5-10 --resume` from the Stage 4 checkpoint, 3 min; stages run [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]; layers missing: none.

**Stage 5.** 114,786 occupied structures and 237 school features shape the zones. Envelope: median 45 ac (p10 14, p90 119) of median usable 59 ac; 172 parcels below 10 ac, 124 with a longest dimension under 200 yd (median longest dimension 560 yd).

**Stage 6.** Terrain imageserver, 0 DEM windows failed. Dwellings within 1,000 yd: median 134; with line of sight: median 73 (p90 201); 14 parcels have every nearby dwelling terrain-shielded; 2,210 have a candidate natural backstop.

**Stage 7.** 1,235 parcels adjoin residential zoning (median 1 ac where present); 277 adjoin planned sewer service; 84 sit inside a Priority Funding Area; 1,535 adjoin permanently eased land; approved-unbuilt units within 2 mi: median 0, p90 612 (Frederick pipeline only).

**Stage 8.** MPRP tiers {'0': 1677, '3': 529, '2': 233, '1': 140} (routes: mprp_pseg_centerline (preferred), mprp_pseg_row150 (preferred), mprp_alternatives_550ft_corridor (alternative), mprp_frederick_county_copy (preferred)); near an existing HV corridor 590, near a substation 140, near data-center development 177.

**Stage 9.** 76 arms-length agricultural comps (3 yr, ≥ 20 ac). Bands: ALL/eased: n=10, median $10,272/ac; Carroll/eased: n=3, median $13,918/ac; Frederick/eased: n=5, median $10,201/ac; Washington/eased: n=3, median $10,343/ac; ALL/uneased: n=64, median $17,586/ac; Carroll/uneased: n=7, median $26,055/ac; Frederick/uneased: n=40, median $17,289/ac; Washington/uneased: n=14, median $16,607/ac. Parcels valued 2,579; median estimate $17,289/ac.

**Stage 10.** Engine: osrm_freeflow_x_peak_factor (https://router.project-osrm.org); routed 2,579; median peak minutes bwi 104 min, langley 137 min, nova 128 min; route redundancy {'redundant': 1147, 'single_egress': 1043, 'no_route': 389}; median corridor durability 89.2.

**Shortlist.** 40 parcels listed, 1,422 excluded by the hard rules, 2,023 distinct owners in `owner_list.csv`. Top five: 1122435531 (Frederick, 314 ac, reachable 309 ac, score 6.49); 1112290446 (Frederick, 203 ac, reachable 196 ac, score 6.41); 2220005947 (Washington, 250 ac, reachable 215 ac, score 6.14); 2220001178 (Washington, 247 ac, reachable 210 ac, score 6.07); 1122430718 (Frederick, 431 ac, reachable 330 ac, score 5.92).



## Caveats to keep in mind

- **Limited-access highways.** Frontage on an interstate is not access.
  County centerline filters drop INTERSTATE/RAMP/A1, and the SHA ROW
  polygon layer (which has no access attribute of its own) is erased with
  the HPMS "Roadway Access Control" centerlines where `ACCESS_CONTROL == '1'`
  (full control), buffered 200 ft (`row_layers.sha_row_polygons.erase`).
  Partial-control corridors (code 2) are kept: a driveway there needs an
  SHA entrance permit, which the `state` authority flag already implies.
- **Roads carried as assessment accounts.** Many road, alley and rail
  corridors have a real ACCTID (owner: the county, MDOT, CSX), so they
  survive the placeholder filter and sit next to farms as "neighbours".
  Stage 4 treats a neighbour whose polygon is mostly public ROW
  (`row_parcel_overlap`, 0.5) as the road itself: a probe that hits it is
  road contact, and it is never a reserve-strip candidate. Strip candidates
  longer than `strip_max_length_ft` (5,000 ft) are skipped for the same
  reason.
- **Reserve strips are vacant and in front.** The first full run flagged
  744 parcels with a "reserve strip"; the 1,187 strip accounts were 84 %
  residential, 73 % improved, median 1.3 ac: roadside house lots carved off
  the farm, plus narrow neighbours along a side line that a corner sample
  probed sideways through. Per the spec ("sitting between the subject
  parcel and the road") a candidate must now lie behind at least two
  frontage samples, and one with a dwelling or any assessed improvement
  (`strip_exclude_improved`) is a house lot, not a strip. Same-owner
  crossings count, so a strip held by the same family is still listed
  (unflagged). A genuine 1-ft spite strip is vacant and blocks the whole
  frontage, so nothing real is lost; a vacant building lot in front of a
  farm still shows up and needs a look.
- **Overlapping FCA sources.** The state compilation and the three county
  layers overlap. Stage 2 reports each as its own row (`source_layer`) and
  unions by implication before summing acres; Stage 3 unions all hostile
  geometry before subtracting, so usable area is not double-counted.
- **CREP.** MDP maps CREP enrollments for Frederick (`OthrPrgNm='CREP'`),
  but as whole enrolled farms, not buffer strips (34 of 44 polygons cover
  >90% of a parcel; median 98 ac, up to 305 ac). The layer is therefore a
  `varies` flag (`crep_enrollment_confirm_contract_term`), not a
  subtraction; the presumed 100 ft stream buffer is what Stage 3 subtracts,
  everywhere, and is still flagged. Carroll/Washington enrollments are not
  mapped at all.
- **Statewide centerline `ID_PREFIX` codes** (CO, MU, MD, US, IS, RP, PV,
  GV, OP, LL, SP, SR, XO) are undocumented; the county layers were used
  instead because they carry explicit ownership.
- **Mount Airy** parcels inside the town limits have no county zoning polygon
  (municipal zoning); they come through Stage 1 as `zoning_unknown_retained`.

## Suggested next steps

1. Re-run `farmsearch run --stages 1-4` after any config change and compare
   `outputs/summary.md` with the counts recorded above.
2. Open a sample of parcels in QGIS with `frontage.gpkg`, `entry_points.gpkg`
   and `reserve_strips.csv`: the frontage and strip logic has been tuned
   against the numbers only, never against a map.
3. Fill the municipal zoning holes (99 retained `zoning_unknown` parcels):
   Frederick's municipal layer (11 towns), Mount Airy's own layer, and the
   Washington towns are listed under "not yet consumed".
4. Continue the spec's build order: MPRP + future encroachment (Stages 7–8;
   Frederick publishes `PlanningAndPermitting/MPRP` and
   `ResidentialDevelopmentPipeline`), dischargeable envelope + viewshed
   (5–6; `MD_BuildingFootprints`, the DEM ImageServer), valuation (9;
   `MD_PropertySales`), commute (10), dossiers.
5. If a licensed MdProperty View extract with owner names is obtained, put
   it under `data/raw/parcels/` and list `OWNNAME1` under `owner_name` in
   `config/schema/parcels.yaml`; nothing else changes.

## Design decisions to preserve

- Different legal instruments are never collapsed: `implication`
  favorable / hostile / varies per layer; `subtract_from_usable` drives
  Stage 3; `crossable_with_permit` drives the Stage 4 crossings variant.
- Parcels are never geometrically cut by the study area.
- Every flag is a reason to look, never a reason to drop a row.
- Frontage is classified by outward probing (open / encumbered /
  foreign_parcel / same_owner_parcel / gap); side edges near a corner whose
  probe runs along the edge are not frontage.
- `landlocked_apparent` means zero direct ROW contact, reported even when
  the same owner's other parcel provides practical access.
- SDAT acreage that is per-polygon on multi-row accounts is reconciled
  against the dissolved geometry (repeat value vs row sum), and flagged.
- `where` in the config is pandas syntax applied on read; `rest_where` is
  SQL sent to the service. Never put SQL in `where`.
