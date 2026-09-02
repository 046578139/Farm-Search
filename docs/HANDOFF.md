# Handoff — continue the Farm-Search build in a new session

This file exists so a fresh Claude Code session (in an environment with
network access) can pick up exactly where the previous one stopped without
the old conversation.

## State of the branch `claude/stages-1-4-build-pi9m53`

- Stages 1–4 of `docs/farm_parcel_screening_spec.md` are implemented,
  validated and pushed. `README.md` documents layout, outputs and design
  decisions; read it first.
- Validation is a synthetic fixture (`farmsearch/fixtures/synthetic.py`)
  with known answers for every Stage 1–4 failure mode, plus 30 pytest tests:
  ```bash
  pip install -e ".[dev]" && python -m pytest
  farmsearch make-fixture --out data/fixture
  farmsearch run --config data/fixture/pipeline.yaml --stages 1-4
  ```
- **No real data has been touched.** The previous session ran in a cloud
  environment whose network policy (level "Trusted") refused every Maryland
  and Esri host with a 403 at the egress proxy: planning.maryland.gov,
  data.imap.maryland.gov, geodata.md.gov, mdgeodata.md.gov, hub.arcgis.com,
  opendata.arcgis.com, services.arcgis.com. The new session's environment
  must be set to **Full** network access (or Custom with those hosts and
  `*.arcgis.com` allowlisted, keeping the default package-manager list).

## Things that are deliberately unverified and must be checked against live data

1. `config/schema/parcels.yaml` — candidate field names for the MD parcel
   layer are placeholders. Run `farmsearch verify-schema` and edit until
   every required field resolves. Stage 1 refuses to run otherwise.
2. `counties:` in `config/pipeline.yaml` — jurisdiction codes `FRED/CARR/WASH`
   are placeholders; the pipeline aborts on any code in the data not listed.
3. Every `url: null  # VERIFY` in `config/pipeline.yaml` — county zoning
   layers, MALPF / county ag / Rural Legacy / MET / FCA easements, NWI, NFHL,
   NHD flowlines, SHA ROW polygons, SHA plat boundaries, county road
   centerlines. Find the real endpoints, then `farmsearch fetch`.
4. Zoning codes — never typed from memory. `farmsearch zoning-domains
   --county <name> --code-field <field> --write`, then fill in
   `is_agricultural` in `config/zoning/<county>.yaml`.
5. `config/study_area.geojson` is an approximate envelope; replace with
   county-boundary geometry (initial run: all Frederick, Mount Airy corner
   of Carroll only, SE Washington). `study_area_expanded_carroll.geojson`
   is the northern-Carroll variant; swapping is one line in the config.
6. `slope.dem_path` — point at the statewide LiDAR DEM (VRT/COG; `/vsicurl/`
   works). Set `dem_vertical_unit_to_m: 0.3048` if it is in feet.

## Suggested first steps in the new session

1. Confirm network access: `curl -sS -o /dev/null -w "%{http_code}\n"
   https://geodata.md.gov/imap/rest/services/PlanningCadastre/MD_ParcelBoundaries/MapServer/0?f=pjson`
   should return 200.
2. Pull the live parcel schema from that endpoint and reconcile
   `config/schema/parcels.yaml` with it (field names, jurisdiction codes).
3. Obtain the parcel bulk download (MdProperty View / FINDER Quantum, or the
   parcel layer download on data.imap.maryland.gov — check the license) into
   `data/raw/parcels/`, then `farmsearch verify-schema`.
4. Locate and set the county zoning and other layer URLs; `farmsearch fetch`.
5. Run `farmsearch run --stages 1-4` and sanity-check `outputs/summary.md`
   per county (counts of parcels ≥ 40 ac and ag-zoned should be in the
   hundreds to low thousands per county, not tens or tens of thousands).
6. Only then continue the spec's build order: MPRP + future encroachment
   (Stages 7–8), dischargeable envelope + viewshed (5–6), valuation (9),
   commute (10), dossiers.

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
