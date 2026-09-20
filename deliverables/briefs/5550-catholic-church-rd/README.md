# 5550 Catholic Church Rd, Jefferson MD 21755 — parcel brief

SDAT District 22, Account 432257 (ACCTID 1122432257), Tax Map 74 Parcel 56. Prepared 2026-09-19 in answer to
"tell me everything you know about this property … what is already in preservation … is there money left on the
table … and how to make it as affordable as humanly possible."

Published brief: https://claude.ai/artifact/Hx3KTB8vZnhjgm8LHcRSkx (private; share from the page's menu).
`catholic_church_road_farm.html` is the same page with the map embedded.

## What it says, in one paragraph

Listed 2026-09-16 at $1,999,900 (Bright MLS MDFR2089452). 81.28 ac zoned A with a 2005 house assessed at $1,031,500.
98 % of the parcel is under a perpetual Maryland Environmental Trust easement settled 1998-01-07 — county project
ISTEA-10, a scenic easement *purchased* with federal transportation-enhancement funds (half of the 250-ac "Berman Farm"
easement), not a donation. Every programme that pays for development rights (MALPF, Rural Legacy, county IPP and
Critical Farms, MARBIDCO Next Gen) therefore has nothing left to buy; the 15-year § 9-107 credit expired in 2014;
what a buyer inherits is the § 8-209.1 valuation of the land at $500/ac. The only preservation-side money is a county
forest-mitigation bank on the ~7 ac of forested stream buffer (credits ≈ $20,000/ac at 2.5 : 1, sold as demand
arrives, MET consent needed). The affordability levers, in order: the price (eased Frederick farmland trades at a
7-sale median of $9,719/ac, supporting ≈ $1.81 M, ≈ $188 k under the ask), a Declaration of Intent at settlement
(≈ $42 k of agricultural transfer tax at $1.8 M), a purchase-money deed of trust (no recordation tax on the loan),
first-time-Maryland-homebuyer allocation of transfer taxes to the seller, FSA Down Payment (2.000 %) and Joint
Financing (4.000 %) loans under a Farm Credit first lien, two Frederick County tax credits the seller is not claiming
(Code §§ 1-8-62 eased land and 1-8-63 farm buildings), and ≈ $9–12 k/yr of low-effort land income.

## Files

| file | what |
|---|---|
| `catholic_church_road_farm.html` | the brief (self-contained, map embedded) |
| `5550_catholic_church_map.png` | parcel map: MET/Rural Legacy easements, floodplain, wetland, usable area, envelope, frontage classes on 1 m land cover |
| `facts.json` | every parcel figure used, from the pipeline outputs and live state/county GIS |
| `research.json` / `research_content.py` | the curated text, ledger, levers, budget and sources (edit the .py, re-run) |
| `build_brief.py` | renders the HTML from `facts.json` + `research.json` + the map |
| `comps_with_met.csv`, `bands_met.json` | the pipeline's valuation comps re-classified with MET easements counted as eased |

Rebuild: `python research_content.py && python build_brief.py` (needs `map_b64.txt` = base64 of the PNG next to the scripts).

## Where the facts came from

Pipeline outputs for the parcel (`outputs/parcels_scored.csv`, `encumbrances.csv`, `usable_area.gpkg`, `envelope.gpkg`,
`frontage.gpkg`), the raw constraint layers under `data/raw/constraints/`, MD iMAP (property data, protected lands,
2010 land use, forests, 2018 canopy, 1 m land cover), Frederick County GIS (agricultural preservation, address points,
plats), the Bright MLS listing as syndicated by Redfin and Coldwell Banker, Maryland statutes on mgaleg.maryland.gov,
and the programme documents linked in the brief. Three research workflows (property record, preservation programmes,
affordability levers) each ran an independent refutation pass; the two corrections that survived — the agricultural
transfer tax tiers (5 % at ≥ 20 ac + 25 % surcharge) and CREP's 2026 signup status — are reflected.

## A pipeline finding

Stage 9 classified this parcel as *un-eased* (valued at $16,010/ac) because MET easements are typed `varies` and only
`favorable` layers count toward the eased band. Counting MET moves 5533 Gapland Rd into the eased band as well.
A task card was filed to treat MET easements as eased for valuation.
