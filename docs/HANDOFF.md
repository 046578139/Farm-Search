# Handoff — continue the Farm-Search build in a new session

This file exists so a fresh Claude Code session can pick up exactly where the
previous one stopped without the old conversation. Read `README.md` first.

## State of the branch `claude/geodata-md-gov-access-zvtlkc`

- Stages 1–4 of `docs/farm_parcel_screening_spec.md` are implemented and
  validated against the synthetic fixture (41 pytest tests, `python -m pytest`).
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
  ```
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
     (`fetch-parcels --no-bbox` is the default in the docs; the county
     `where` is cheap). All three counties: 6 minutes, exact counts.
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
   clipped to the SE box. `config/study_area.geojson` (2,109 km²) and
   `study_area_expanded_carroll.geojson` (3,102 km²) are committed.

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

## Stage 1 on the real data (2026-09-02)

`farmsearch run --stages 1` on the initial study area, 71 s:

| county | parcels in study area | ≥ 40 ac | ag-zoned | zoning unknown | Stage 1 pass | median ac | acres passing |
|---|---:|---:|---:|---:|---:|---:|---:|
| Frederick (whole) | 105,822 | 2,078 | 1,880 | 0 | 1,880 | 88 | 205,525 |
| Carroll (Mount Airy corner) | 11,143 | 218 | 203 | 2 | 205 | 77 | 21,552 |
| Washington (SE) | 6,906 | 309 | 308 | 0 | 308 | 102 | 39,722 |
| total | 123,871 | 2,605 | 2,391 | 2 | 2,393 | | 266,800 |

Sanity: hundreds to low thousands per county, as the spec expects. 1,879
distinct owner keys (mailing address) among the 2,393. The two "zoning
unknown" parcels are inside the Town of Mount Airy (municipal zoning; no
county polygon) and are retained with `zoning_unknown_retained`.
`owner_name_available_pct` is 0 (see item 2 above).

Performance note: intersecting every parcel with the detailed county
boundary took >15 minutes; `attribute_study_area` now intersects only
parcels that straddle the boundary (interior parcels are found with the
spatial index), which is the difference between 15 minutes and 71 seconds.

## Caveats to keep in mind

- **Limited-access highways.** The SHA ROW polygons and county centerline
  layers give frontage on interstates; frontage there is not access. County
  centerline filters drop INTERSTATE/RAMP/A1, but the SHA polygon layer has
  no attribute to filter on. The HPMS "Roadway Access Control" layer
  (`reference_layers.roadway_access_control`) is the fix: subtract
  controlled-access ROW in Stage 4.
- **Overlapping FCA sources.** The state compilation and the three county
  layers overlap. Stage 2 reports each as its own row (`source_layer`) and
  unions by implication before summing acres; Stage 3 unions all hostile
  geometry before subtracting, so usable area is not double-counted.
- **CREP.** MDP maps CREP enrollments for Frederick (`OthrPrgNm='CREP'`);
  Carroll/Washington enrollments are not mapped, so the presumed 100 ft
  stream buffer still applies everywhere and is still flagged.
- **Statewide centerline `ID_PREFIX` codes** (CO, MU, MD, US, IS, RP, PV,
  GV, OP, LL, SP, SR, XO) are undocumented; the county layers were used
  instead because they carry explicit ownership.
- **Mount Airy** parcels inside the town limits have no county zoning polygon
  (municipal zoning); they come through Stage 1 as `zoning_unknown_retained`.

## Suggested next steps

1. Re-run `farmsearch run --stages 1-4` after any config change and compare
   `outputs/summary.md` with the counts recorded above.
2. Wire the HPMS access-control layer into Stage 4 (frontage on
   controlled-access ROW ≠ access).
3. Continue the spec's build order: MPRP + future encroachment (Stages 7–8;
   Frederick publishes `PlanningAndPermitting/MPRP` and
   `ResidentialDevelopmentPipeline`), dischargeable envelope + viewshed
   (5–6; `MD_BuildingFootprints`, the DEM ImageServer), valuation (9;
   `MD_PropertySales`), commute (10), dossiers.
4. If a licensed MdProperty View extract with owner names is obtained, put
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
