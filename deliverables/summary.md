# Farm-Search run summary

- acreage_min: 40.0  acreage_max: None  slope_max_pct: 15.0
- study area: /home/user/Farm-Search/config/study_area.geojson (intersects)
- stages run: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]   elapsed: 248.6s

## Stage 1 — base filter

loaded 133090 · in study area 129019 · meets acreage 2732 · pass 2576 · unique owners passing 2022

| county | in study area | ≥ acreage | ag-zoned | zoning unknown | pass | median ac | total ac |
|---|---:|---:|---:|---:|---:|---:|---:|
| Carroll | 11153 | 223 | 205 | 2 | 207 | 78 | 20271 |
| Frederick | 105797 | 2059 | 1883 | 39 | 1922 | 89 | 205389 |
| Washington | 12069 | 450 | 439 | 8 | 447 | 94 | 48746 |

## Stage 2 — encumbrances

layers: county_ag_preservation, county_ag_preservation_frederick_county_held, county_ag_preservation_frederick_critical_farms, county_ag_preservation_frederick_ipp, crep_enrolled, floodplain, forest_banking_frederick, forest_conservation, forest_conservation_carroll, forest_conservation_frederick, forest_conservation_washington, local_protected_lands, malpf, malpf_frederick, met, met_frederick, other_easements_frederick, protected_lands_dnr, protected_lands_federal, riparian_presumed, riparian_presumed_shore, rural_legacy, rural_legacy_frederick, wetlands
rows 8787 · hostile-eased parcels 559 · favorably-eased parcels 1078 · bisected 1554

## Stage 3 — usable area

slope source: imageserver (windows failed: 0) · usable 187791 of 274407 gross acres · parcels whose usable area falls below acreage_min: 776

## Stage 4 — access connectivity

landlocked_apparent 144 · frontage blocked by foreign parcel 36 · reserve strips 127 · parcels with islands 1727
largest reachable block ≥ acreage_min: 1293 · below: 1283

## Stage 5 — dischargeable envelope

occupied structures 141392 · school features 237 · envelope 148970 ac total · below minimum 184 · too short 130 (safety 150 yd, school 300 yd)

## Stage 6 — viewshed

terrain imageserver (windows failed 0) · parcels with a visible dwelling 2544 · all nearby dwellings shielded 15 · backstop candidates 439

## Stage 7 — future encroachment

adjacent residential zoning 599 · adjacent planned sewer 275 · inside PFA 81 · adjacent permanently eased 1529 · median approved-unbuilt units within 10560 ft: 0.0

## Stage 8 — transmission and industrial exposure

routes: mprp_pseg_centerline (preferred), mprp_pseg_row150 (preferred), mprp_alternatives_550ft_corridor (alternative), mprp_frederick_county_copy (preferred) · MPRP tiers {'0': 1677, '3': 527, '2': 237, '1': 135} · near existing HV 588 · near substation 138 · near data center 177
MPRP status (re-verify at run time): PSC Case No. 9773: PSEG filed the CPCN application 2024-12-31; hearings were scheduled for 2026-09-21 to 09-28 with a decision targeted March 2027; as of April 2026 the schedule was in flux (PPRP: incomplete field surveys). Re-verify at run time; alternates return if the preferred route is rejected.

## Stage 9 — valuation

102 arms-length agricultural comps · ALL/eased: n=15 $9,719/ac; ALL/uneased: n=68 $15,330/ac · parcels valued 2576 · median est $16,010/ac · above price ceiling 0

## Stage 10 — commute (reported, never a filter)

engine osrm_freeflow_x_peak_factor (https://router.project-osrm.org) · routed 2576 · median peak bwi 104 min, langley 137 min, nova 128 min · redundancy {'redundant': 2219, 'no_route': 210, 'single_egress': 147} · median corridor durability 89.2

## Shortlist

40 parcels listed (top 40), 1273 excluded by hard rules, 2022 distinct owners across all scored parcels (owner_list.csv)

## What this pipeline cannot determine

- Deeded access easements over neighboring land are recorded in land records, not mapped: a landlocked_apparent parcel may have good deeded access. Flag, never auto-delete.
- Entrance permit feasibility: a perc approval says nothing about whether a driveway can be permitted. Call county public works (or SHA on state-numbered roads).
- Stream crossing permits: crossing a stream generally requires an MDE nontidal wetlands and waterways permit regardless of easement status; agricultural crossings are routinely permitted.
- County discharge ordinances and range zoning: verify with Carroll, Frederick and Washington County zoning offices.
- Individual easement terms: MET and donated easements vary widely. Read them.
- CREP riparian buffers are inferred from stream geometry; FSA enrollment data is not available parcel-by-parcel. Confirm with seller.
