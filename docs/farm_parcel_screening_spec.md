# Farmland Acquisition Screening Pipeline — Spec

## Objective

Build a repeatable pipeline that screens agricultural parcels in Frederick, Carroll,
and southeastern Washington County, Maryland, and produces a per-parcel dossier
scoring each on usability, encumbrance, access, and future-risk exposure.

**Purpose:** identify a small set of genuinely viable acquisition candidates before
any outreach occurs, so that no landowner is contacted about a property that would
fail diligence anyway.

This is a **screening tool, not a title report.** Roughly 80% of the relevant
constraints are mapped and scriptable. The remainder live in deed language in county
land records and must be verified by an attorney. The pipeline's job is to reduce
thousands of parcels to dozens worth paying a professional to examine.

---

## Study area

**Include:** Frederick County, Carroll County, and the southeastern portion of
Washington County (the Sharpsburg / Keedysville / Boonsboro / Rohrersville area).

**Exclude:** Howard County and Montgomery County entirely.

Study area boundary is a user-supplied polygon; clip everything to it. Note that
the initial polygon captures only the Mount Airy corner of Carroll County and
excludes Westminster, New Windsor, Union Bridge, Uniontown, and Taneytown — which
is where the majority of Carroll's preserved agricultural inventory sits. Make the
boundary a swappable input so an expanded northern-Carroll run is a one-line change.

---

## Configuration block

Expose these at the top of the pipeline so they can be tuned without touching logic.

```yaml
acreage_min: 40
acreage_max: null            # TBD — set a practical ceiling
price_ceiling: null          # TBD — total, or per-acre band
slope_max_pct: 15            # above this, treat as non-usable for the envelope
safety_buffer_yards: 150     # statutory hunting minimum; raise for comfort
school_buffer_yards: 300     # statutory
min_dischargeable_acres: 10
min_envelope_length_yards: 200   # longest usable dimension for a shooting bay
mprp_exclusion_buffer_ft: 2000
commute_destinations:
  - BWI
  - Langley, VA
  - Northern Virginia (TBD specific)
```

**Commute is a reported column, never a filter.** Do not exclude any parcel on
drive time. Land characteristics are permanent; jobs are not.

---

## Data sources

### Parcels and ownership (foundation layer)

- **Maryland Parcel Boundaries** — statewide polygons attributed with State
  Department of Assessments and Taxation (SDAT) data. ~2.29M records.
  - REST: `https://geodata.md.gov/imap/rest/services/PlanningCadastre/MD_ParcelBoundaries/MapServer/0`
  - Bulk: MdProperty View / FINDER Quantum at
    `https://planning.maryland.gov/Pages/OurProducts/DownloadFiles.aspx`
  - **Use the bulk download**, not the REST API. MaxRecordCount is 1000 and you
    need three full counties.
  - **Read the dataset's custom license before building on it.**

- **Field schema:** inspect the layer's field list directly. Do not assume names.
  Key fields include an account ID (`ACCTID`), owner name and mailing address
  fields, acreage, land use code, and assessment values. Verify each against the
  live schema before writing queries.

### Zoning

- Carroll County and Frederick County GIS zoning layers (county-published).
- Washington County zoning layer.
- **Do not hardcode district codes.** Pull the actual domain values from each
  county's layer and map them to an `is_agricultural` boolean per county.

### Encumbrances — the critical stack

These are **different legal instruments with opposite implications.** Tag each
separately. Never collapse into a single "conservation" flag.

| Layer | Source | Implication |
|---|---|---|
| MALPF agricultural easements | MD Dept. of Agriculture / MD Protected Lands | **Favorable.** Restricts subdivision and non-farm construction. Does NOT restrict farming, vehicle access, horses, or ATVs. Land is cheaper because development rights are stripped. |
| County ag preservation easements | Carroll and Frederick county programs | Same as above. |
| Rural Legacy easements | DNR | Same as above. |
| Maryland Environmental Trust easements | MET | Donated; terms vary widely. Read individually. |
| **Forest Conservation Act easements** | County-administered (FCA) | **Hostile.** Regulatory exaction from prior subdivision approval. Typically bars clearing, structures, and vehicle crossing. County-level coverage quality varies — expect gaps. |
| CREP riparian buffers | Inferred from geometry | Restrictive strips along waterways. USDA FSA enrollment data is federally protected and not available parcel-by-parcel; infer from buffer shape, confirm with seller. |
| MD Protected Lands | `https://geodata.md.gov/imap/rest/services/Environment/MD_ProtectedLands/MapServer/0` | DNR-owned land plus conservation easements statewide. |

### Physical constraints

- **National Wetlands Inventory** (USFWS)
- **NHD** streams and waterbodies
- **FEMA NFHL** flood zones
- **SSURGO** soils — prime farmland and Class 1/2 designations
- **Maryland statewide LiDAR** — derive slope and use for viewshed analysis

### Access

- **MDOT SHA Right-of-Way (Polygons)** — `data.imap.maryland.gov`, statewide
  SHA right-of-way boundaries
- **MDOT SHA Right of Way** (line feature layer)
- **MDOT SHA Plat Boundary Polygon** — derived from 60,000+ georeferenced ROW
  plat images; this is where reserve strips and access-control takings are recorded
- County road centerlines and right-of-way for Carroll, Frederick, Washington

### Structures and noise receptors

- Statewide address points and/or building footprints (**not** parcel counts, and
  **not** the SDAT improvement flag — you need location, not existence)
- MSDE school points
- Places of worship (the hunting statute names churches specifically)

### Future-risk layers

- **MPRP proposed route and all studied alternatives** — PSEG Case No. 9773
  filings with the Maryland PSC; Carroll and Frederick counties both publish
  project pages with mapping
- Existing high-voltage transmission lines and substations (HIFLD)
- County water and sewer plan service categories
- Priority Funding Areas
- Municipal boundaries and growth areas
- Approved-but-unbuilt subdivision pipeline (both counties publish this)
- MDOT SHA AADT traffic counts (multi-year history)
- Maryland Consolidated Transportation Program (programmed road widenings)

---

## Processing stages

### Stage 1 — Base filter

Clip parcels to study area. Filter to `acreage >= acreage_min` and agricultural
zoning per each county's own code mapping. Retain all parcels; do not drop yet.

### Stage 2 — Encumbrance accounting

For each parcel, compute intersecting area **by encumbrance type**, reported
separately with acreage and centroid position relative to the parcel.

A 12-acre forest easement in a back corner is irrelevant. Twelve acres bisecting
the property is fatal. Position matters as much as area — report both.

### Stage 3 — Usable area

```
usable = parcel
         - forest_conservation_easements
         - riparian_buffers
         - wetlands
         - floodplain
         - slope > slope_max_pct
```

Do **not** subtract agricultural preservation easements. Those are farmable and
drivable; they restrict subdivision, not use.

### Stage 4 — Access connectivity (the core algorithm)

This single routine catches three separate failure modes.

1. **Identify entry nodes.** Intersect parcel boundary with road right-of-way.
2. **Landlock test.** Zero contact with any public right-of-way → flag
   `landlocked_apparent`.
3. **Frontage blockage test.** Compute what fraction of the road-facing boundary
   is covered by an encumbrance polygon or by a **separately-owned parcel**.
   - **Reserve strip detection:** look for adjacent parcels with extreme
     perimeter-to-area ratio sitting between the subject parcel and the road
     (e.g. 50ft × 1,300ft). Compare the strip's owner name against the subject
     parcel's. A mismatch is a deliberate access-control strip. Report the
     strip's account ID.
   - If frontage coverage approaches 100% → flag `frontage_blocked_by_foreign_parcel`
     with the offending account ID attached.
4. **Internal connectivity.** Seed at the entry node. Compute connected components
   of the usable-area polygon. Report:
   - total usable acres
   - **largest contiguous reachable block** ← the number that actually matters
   - count of unreachable islands and their acreage

A 100-acre parcel with 85 unencumbered acres but a 50-acre largest reachable
block is a different property than the listing describes. Every commercial tool
reports the first two numbers. Report the third.

### Stage 5 — Dischargeable envelope

Two separate legal regimes; do not merge them.

- **Hunting** is governed by Maryland Natural Resources §10-410: 150-yard safety
  zone from any dwelling, residence, church, or occupied building; 300 yards from
  a school during school hours or school activities. The statute exempts the
  owner/occupant, so the subject parcel's own dwelling does not constrain the owner.
  Every neighbor's does.
- **Target/practical shooting is not hunting** and §10-410 does not govern it.
  That falls to county discharge ordinances, nuisance law, and zoning (accessory
  recreational use vs. a "shooting range" requiring special exception). **Flag for
  manual verification with each county's zoning office** — this is not scriptable
  and the answer differs by county.

Compute: subtract `safety_buffer_yards` around every **off-parcel** dwelling,
church, and occupied structure, and `school_buffer_yards` around schools. Report
the remainder as the dischargeable envelope, with:
- envelope acreage
- **longest usable dimension** (a 30-acre ribbon is useless; 8 acres square works)

### Stage 6 — Viewshed (noise and safety)

Terrain shielding is real and computable. Using the LiDAR DEM, run a viewshed
from candidate firing points within the envelope against every neighboring
dwelling. Report count of dwellings with direct line-of-sight.

A house 500 yards away over a ridge is quieter than one 900 yards away across
open ground. A berm addresses safety; distance and terrain address noise.

Also report: slopes within the envelope oriented away from neighbors, as candidate
natural backstops.

### Stage 7 — Future encroachment

Present buildout is the wrong question. Score:

- Zoning of all adjacent parcels (residential zoning on adjoining open land is
  the threat, not existing houses)
- County water/sewer plan service category for adjacent parcels — **planned sewer
  service on adjoining agricultural land is the strongest available predictor of
  future subdivision**
- Priority Funding Area status
- Approved-but-unbuilt units within a defined radius
- Whether adjacent parcels carry permanent agricultural easements (neighbors who
  will still be neighbors in twenty years)

### Stage 8 — Transmission and industrial exposure

The MPRP is a 500kV line, ~67–70 miles, cutting a 150-foot swath from northern
Baltimore County through central Carroll County to the Doubs substation in
Adamstown, southern Frederick County. It affects 350+ parcels and ~1,200 acres,
and the route crosses roughly 514 acres of already-protected land.

**An agricultural easement does not protect against a utility with condemnation
authority.** Screen this independently of encumbrance status.

Three tiers:
1. Parcel intersects **any studied route** (not just the current preferred
   alignment — if the PSC rejects or reroutes, alternates return) → exclude
2. Within `mprp_exclusion_buffer_ft` or with line-of-sight to any studied route
   → flag (reuse the Stage 6 viewshed machinery)
3. General corridor, or near existing high-voltage corridors and substations →
   flag. New transmission follows existing corridors.

Also flag proximity to the Adamstown / Doubs area and known large-scale data
center development. Data center noise (chillers, cooling fans, generator testing)
is measurable and substantiated, not merely perceptual — which means it does not
fade the way perception-only concerns do.

**Status note:** PSEG filed its CPCN application 2024-12-31 (PSC Case No. 9773).
Public hearings were scheduled for the week of 2026-09-21 through 09-28, with a
decision targeted March 2027. As of April 2026 the schedule was in flux; the
Power Plant Research Program called it untenable due to incomplete field surveys.
**Re-verify current status at runtime — this is actively moving.**

### Stage 9 — Valuation

SDAT assessed values **understate farmland badly** because agricultural use
assessment values land well below market. Do not use assessment as a price proxy.

Instead: pull recent arms-length agricultural sales across all three counties,
build a per-acre band **segmented by eased vs. un-eased status**, and apply the
ceiling against estimated market value. MALPF easement payments have historically
run roughly $2,000–$6,000/acre depending on location, which gives a rough sense
of the development-rights spread.

### Stage 10 — Commute (reported, not filtered)

Peak-departure routing (7:00 AM), not free-flow, from each parcel's road frontage
to each destination. Use a departure-time-aware engine (Google, HERE, or
self-hosted Valhalla on OSM).

Report as **separate columns per destination**, not a blended average. Variance
differs in kind between them — the Langley trip depends on the American Legion
Bridge, which has no redundant crossing.

Additionally compute:
- **Route redundancy:** does the parcel have two independent routes to different
  interchanges, or exactly one road out? Single-egress parcels have no tolerance
  for incidents. This is the Stage 4 connectivity question one scale up.
- **Corridor durability:** approved-but-unbuilt residential units along each
  commute corridor, against SHA AADT history and programmed widenings. Corridors
  with high approved-unit counts and no programmed capacity increase are the ones
  that degrade next. This is what happened to MD-27.

---

## Output

### Per-parcel record

```
account_id, owner_name, owner_mailing_address, owner_type (individual/LLC/trust)
gross_acres, zoning, county

encumbrances: [{type, acres, position, source_layer}]
usable_acres
largest_contiguous_reachable_acres        ← primary usability metric
unreachable_islands: [{acres}]

landlocked_apparent (bool)
frontage_blocked_by_foreign_parcel (bool)
blocking_parcel_account_id

dischargeable_envelope_acres
dischargeable_envelope_longest_dim_yards
dwellings_with_line_of_sight
candidate_backstop_slopes (bool)

mprp_tier (0-3)
adjacent_residential_zoning_acres
adjacent_planned_sewer (bool)
approved_unbuilt_units_within_2mi
adjacent_permanently_eased_acres

est_market_value, est_per_acre, comp_basis

commute_bwi_peak_min, commute_langley_peak_min, commute_nova_peak_min
route_redundancy (single_egress / redundant)
corridor_durability_score

manual_verification_flags: []
```

### Deliverables

1. **CSV/GeoJSON** of all parcels passing Stage 1, fully scored
2. **Ranked shortlist** with configurable weights
3. **Per-parcel PDF dossier** for the shortlist: parcel map with encumbrances
   colored by type, usable-area overlay, dischargeable envelope, access diagram
4. **Deduplicated owner list** — collapse by owner + mailing address before any
   outreach. One farmer frequently holds several contiguous parcels; six letters
   to one mailbox destroys credibility.

---

## What this pipeline cannot determine

State these explicitly in the output. They are the items that require a human.

- **Deeded access easements over neighboring land** are recorded in land records,
  not mapped. A parcel flagged `landlocked_apparent` may have perfectly good
  deeded access. **Flag, never auto-delete.**
- **Entrance permit feasibility.** A perc approval is a health department septic
  finding and says nothing about whether a driveway can be permitted. Separate
  agencies, separate permits. Listing agents conflate them constantly. For the
  shortlist, this is a ten-minute call to county public works (or SHA on
  state-numbered roads) and it is the one thing no script can settle.
- **Stream crossing permits.** Crossing a stream generally requires a Maryland
  Department of the Environment nontidal wetlands and waterways permit regardless
  of easement status. Agricultural crossings are routinely permitted — an
  apparently bisected parcel is not automatically unusable.
- **County discharge ordinances and range zoning.** Verify directly with Carroll,
  Frederick, and Washington County zoning offices.
- **Individual easement terms.** MET and donated easements vary widely. Read them.
- **Whether a Tuesday morning actually feels like the routing engine says.** For
  the final handful, drive the route at 7:15 AM twice.

---

## Build order

1. Parcel ingest + schema verification + base filter → confirm counts are sane
2. Encumbrance stack + usable area
3. Access connectivity (Stages 4) — highest value per line of code
4. MPRP + future encroachment
5. Dischargeable envelope + viewshed
6. Valuation
7. Commute columns
8. Dossier generation

Stages 1–4 alone will eliminate the large majority of parcels. Build and validate
those before investing in the rest.
