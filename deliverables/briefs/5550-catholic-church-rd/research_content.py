"""Curated content for the 5550 Catholic Church Rd brief. Every figure traces to a source in SOURCES.
Run: python research_content.py  -> writes research.json next to it."""
import json
from pathlib import Path

SP = Path(__file__).parent
F = json.load(open(SP / "facts.json"))
A = F["ask_vs_comps"]

def m(x):
    return f"${x:,.0f}"

# ----------------------------------------------------------------------------- top
subtitle = ("Listed on 16 September 2026 at $1,999,900 (Bright MLS MDFR2089452, open house Sunday 20 September 2–4 pm). "
            "This brief answers three questions from the public record, live-verified on 19–20 September 2026: what the "
            "farm is, what is already preserved and what that means for a buyer, and where the money is — including "
            "the idea that putting the woods into a conservation easement pays cash.")

verdict = (
    "<p><strong>The preservation cheque was cashed in January 1998.</strong> The whole farm (81.3 easement acres against 81.28 assessed) is under a perpetual "
    "Maryland Environmental Trust conservation easement, and it was not a donation: the State bought it with federal "
    "ISTEA transportation-enhancement money (Frederick County project ISTEA-10) as half of the 250-acre “Berman Farm” "
    "scenic easement protecting the South Mountain battlefield and Burkittsville viewshed. The development rights are gone, "
    "the seller's family was paid for them, and every program that pays for development rights (MALPF, Rural Legacy, the "
    "county IPP and Critical Farms, MARBIDCO Next Gen) has nothing left to buy here.</p>"
    "<p><strong>What a buyer inherits is a tax position, not a payment:</strong> the eased land is valued at the highest "
    "agricultural rate ($500/ac) whether or not you farm it, which is why the land carries a $130,000 assessment and "
    "$1,589 of the $14,194 tax bill. The 15-year 100% land-tax credit that follows a <em>donated</em> MET easement ended by 2014 at the latest — if it ever applied to a purchased one — and does "
    "not restart on sale. The one preservation-side idea with real money in it is a Forest Resource Ordinance mitigation "
    "bank on the forested stream buffer — worth tens of thousands, not hundreds, paid out as developers buy credits, and it "
    "needs MET's consent. Beyond that the woods are worth a small hunting lease (≈ $2,000 a year at central-Maryland rates), a one-time timber harvest under a MET-approved stewardship plan, and under $100 a year in tax relief. Two county tax credits the seller appears never to have claimed — on the eased land and on the barns — are worth a few hundred to a few thousand dollars a year.</p>"
    "<p><strong>Where the money actually is:</strong> the price, and the taxes at purchase. Eased farmland in this "
    f"neighbourhood trades at a tight {m(F['comps_met']['frederick_eased_incl_met']['median'])}/ac (seven sales, "
    f"interquartile {m(F['comps_met']['frederick_eased_incl_met']['p25'])}–{m(F['comps_met']['frederick_eased_incl_met']['p75'])}), "
    f"which with the house at its assessed value supports about {m(A['value_at_median'])} — roughly {m(A['gap_at_median'])} "
    "under the ask. A Declaration of Intent at settlement removes an agricultural transfer tax of roughly $42,000 at $1.8M; "
    "a purchase-money deed of trust avoids 1.4% recordation tax on the loan; and if every buyer on the deed is a first-time "
    "Maryland homebuyer the statute shifts the state transfer and recordation taxes to the seller.</p>")

tiles = [
    ("Asking", "$1,999,900", "listed 16 Sep 2026 · $465 per finished sq ft"),
    ("Eased-comps value", m(A["value_at_median"]), f"land at {m(A['eased_median_per_acre'])}/ac + house at assessment"),
    ("Already preserved", "98%", "MET / ISTEA-10 easement, settled 7 Jan 1998"),
    ("Preservation cash left", "≈ $0", "no program buys rights already sold"),
    ("Avoidable at closing", "≈ $42,000", "ag transfer tax, waived by a Declaration of Intent (at $1.8M)"),
    ("Annual tax today", "$14,194", "89% of it is the house, not the land"),
]

# ----------------------------------------------------------------------------- parcel
parcel_prose = (
    "<p>Tax Map 74 Parcel 56 sits on the west side of Catholic Church Road between Jefferson and Burkittsville, at the "
    "foot of South Mountain. The assessment carries 81.28 acres; the parcel polygon measures 80.55. Frederick County zones "
    "all of it A (Agricultural); it is well and septic with no planned water or sewer service, outside the Priority Funding "
    "Area, and adjoins no residential zoning. Eleven neighbouring parcels total 502 acres, and 176 of those acres — a third of "
    "the boundary — are themselves under permanent easement, so the setting is about as protected from development as "
    "Frederick County gets.</p>"
    "<p>The stream is the parcel's defining feature. Manor Run, Burkitts Run and Broad Run converge here; the eastern branch "
    "crosses the property with an 8.2-acre FEMA AE floodplain and a presumed 7-acre riparian buffer along it. That splits the "
    "usable ground into a <strong>32-acre south block</strong> holding the 2005 house, the road frontage and most of the "
    "cropland, and a <strong>19-acre north block</strong> beyond the water holding the two barns, reachable from the house "
    "only across the floodplain (the pipeline flags the crossing as permit-dependent). A further 18 acres are steeper than the "
    "screen's slope limit. That split is why the acquisition screen dropped the parcel: its largest contiguous reachable "
    "block is 32 acres against a 40-acre rule.</p>"
    "<p>Land cover from the state's 2010 land-use mapping is roughly 47 acres cropland, 16 acres pasture, 15 acres forest and "
    "a 3-acre house site; the 2018 canopy raster agrees (13.6 acres at ≥ 50% canopy). DNR maps 6.9 acres of it as forested "
    "stream buffer. The listing gives no tillable / pasture / woods breakdown and never mentions the easement.</p>"
    "<p>Structures: a 4,298 sq ft two-storey house with basement (grade 7 of 9, 979 sq ft attached garage, 2,558 sq ft "
    "unfinished lower level per the listing) and two outbuildings of 2,904 and 1,962 sq ft footprints in the north block, "
    "which the listing describes as barns and stables. The county assigns a second street address (5650 Catholic Church Rd) "
    "to the same tax account, which usually means a second addressed building — ask what it is. Note also that the 1981 "
    "farm-lot plat the parcel descends from marks its 84-acre remainder “not an approved building lot — no residential "
    "building allowed”; the house was built anyway in 2005, so that status was resolved later, and the permit file should "
    "confirm how.</p>")

# ----------------------------------------------------------------------------- preserved
preserved_prose = (
    "<p><strong>What is on the land.</strong> One easement matters: MET easement 0424BER98.FRED, 81.30 easement acres, "
    "held solely by the Maryland Environmental Trust (no co-holder in the DNR layer), covering 78.8 of the parcel's 80.55 "
    "polygon acres — the remaining 1.7 acres are boundary slivers, and Frederick County's 2022 land-preservation plan lists "
    "the property as “Berman, Melvin · 81.30 · 81.30 · MET · ISTEA”, i.e. the whole parcel counted as preserved. Two "
    "neighbouring MET easements and two Rural Legacy easements overlap the boundary by hundredths of an acre and do not "
    "burden this parcel. There is no MALPF, IPP, Critical Farms, Rural Legacy, CREP, Forest Conservation Act or forest-bank "
    "easement on it, no Forest Conservation and Management Agreement, and no county historic overlay (the Maryland Historical "
    "Trust's Crampton's Gap battlefield polygon covers the area; the 1899 “Haley Farm” historic house is on the neighbouring "
    "Shisler land, not this parcel).</p>"
    "<p><strong>How it got there.</strong> The county records the easement as project ISTEA-10, settled 7 January 1998 — the "
    "same day as ISTEA-11 on the 160-acre farm next door at 6100 Burkittsville Road. Scenic America's case study describes the "
    "deal: the 250-acre Berman Farm easement, MET as holder, the State Highway Administration as conduit for federal "
    "Intermodal Surface Transportation Efficiency Act enhancement funds, and DNR providing the match. Frederick County's plan "
    "says these ISTEA funds were used “to purchase development easements” around South Mountain and Burkittsville, and lists "
    "MET's own donated-easement programme separately. So the landowner was <em>paid</em>. The price is not published; it "
    "would be in the 1998 deed of easement, which the seller must hand you before contract under Real Property §10-705.</p>"
    "<p><strong>What it means for you.</strong> MET's FAQ: “the land can be bought and sold; however, the easement remains "
    "on the land and binds all future owners.” Extinguishment needs a court and “unexpected change” making conservation "
    "impossible; MET's 2025 policy is to amend only to strengthen protection and not to subordinate to a newer overlay "
    "easement without its Board's approval. MET's model easement (the 1998 deed will differ in detail) prohibits subdivision and leases of part of the "
    "land beyond 20 years, caps dwellings at a negotiated number inside a mapped building area, keeps agricultural structures "
    "inside that area unless under 500 sq ft, requires a forest stewardship plan before cutting in mapped forest areas, allows "
    "commercial agriculture and small-scale agritourism, and counts hunting (with deer stands and blinds) as permitted "
    "passive recreation. Target shooting is not addressed by the model; Frederick County has no county-wide discharge "
    "ordinance that this research could find, and a formal range in the A district is a special-exception use with a 500 ft "
    "setback. Read the recorded deed for the dwelling cap, the building envelope, forestry, and any clause on firearms — "
    "those four clauses decide what you can do here.</p>")

# ----------------------------------------------------------------------------- ledger
ledger_intro = ("Every Maryland and Frederick County programme that pays landowners for preservation, with its rule for land "
                "already under a perpetual easement. “Cashed in 1998” means the value that programme pays for was sold to the "
                "State in the ISTEA deal. Figures are the current (FY2026–27) ones where a cycle exists.")

ledger = [
    {"program": "MET easement (the one already here)", "runs": "Maryland Environmental Trust · DNR", "status": "cashed",
     "pays": "Purchase price in 1998 (ISTEA funds). A donated MET easement pays a federal deduction and a state credit of up to $5,000/yr ($80,000 total) — to the donor, in the year of the gift",
     "why": "Perpetual, binds every future owner. The 1998 grantor was paid; a buyer receives no deduction or credit for an easement that already exists. The state credit statute (TG § 10-723) was enacted in 2001 and is reduced by any payment received, so even the grantor likely never had it. Only the § 8-209.1 land valuation (below) survives."},
    {"program": "§ 9-107 conservation property tax credit", "runs": "SDAT · state law", "status": "no",
     "pays": "100% of property tax on the unimproved eased land for 15 tax years after a donated easement",
     "why": "Requires a donated easement and runs from the donation, not the owner: for a January 1998 easement the window closed 30 June 2013 (2014 at the latest) and cannot restart. It never covered the house or its acre anyway (≈ $490/yr at today's land value)."},
    {"program": "§ 8-209.1 conservation-property valuation", "runs": "SDAT · state law", "status": "yes",
     "pays": "Land valued at the highest agricultural-use rate ($500/ac) with no farming requirement, indefinitely",
     "why": "Already in the bill: 80.28 ac × $500 = $40,140 of land value, ≈ $490/yr in tax. Passes to a buyer automatically; the statute covers easements “sold or donated”. Worth ≈ $9,000/yr against an eased-market valuation of the land — but you never see it as cash."},
    {"program": "Frederick County agricultural-preservation land credit (Code § 1-8-62)", "runs": "Frederick County Treasury · apply by 1 October", "status": "partly",
     "pays": "100% of the county tax on land under an easement permanently conveyed to the county or a Maryland state agency “to preserve the agricultural use”; residence + 1 ac excluded; lasts while the land qualifies",
     "why": "Verified in the current county code (Ord. 02-24-320) although the Treasury's tax-credit page omits it, and the county's 2022 land-preservation plan says it credits “any land preservation program … 100% of the agriculturally assessed land.” MET is a state agency; the open question is whether a scenic/conservation easement counts as one “to preserve the agricultural use.” Worth ≈ $446/yr. The current bill ($14,193.53) equals 1.222% of the assessment exactly, so the seller is not receiving it."},
    {"program": "Frederick County agricultural building credit (Code § 1-8-63)", "runs": "Frederick County · apply by 1 October", "status": "yes",
     "pays": "100% of the county tax on farm buildings that sit on ag-use-assessed land and are used for an SDAT-recognised agricultural activity (not the house)",
     "why": "Verified in the county code and in TP § 9-312(h). The listing shows barns, stables, a beef barn and a dairy barn; whatever share of the $1,031,500 improvement assessment is those buildings, used for cattle, hay or boarding, comes off the county tax at $1.110 per $100 — hundreds to a few thousand dollars a year. Ask SDAT for the house/outbuilding split. Nothing in the current bill suggests it is being claimed."},
    {"program": "MALPF easement purchase", "runs": "Maryland Agricultural Land Preservation Foundation · county applications due 1 April", "status": "none",
     "pays": "Fair market value minus agricultural value, from two state appraisals; capped at 75% of FMV; one lump sum at settlement",
     "why": "The formula pays for development value, and MALPF's application instructions say it “will not pay for acreage already encumbered.” On land whose rights went to MET in 1998 the easement value is nil, and MET would have to consent to an overlay."},
    {"program": "Rural Legacy (Mid-Maryland Frederick RLA)", "runs": "DNR · Frederick County as sponsor", "status": "none",
     "pays": "Lump sum by the county's points formula ($5.45/point up to 1,100 points in the 2015 draft); recent Frederick purchases $5,664–$6,873/ac",
     "why": "Not barred in law — no statute or manual excludes eased land, and MET's policy allows overlays with Board approval — but 450 of the 1,100 points are for development rights extinguished, which this parcel no longer has, and the sponsor chooses whom to buy from. Possible in law, worth about nothing in practice; ask Shannon O'Neil (301-600-1411)."},
    {"program": "Frederick County Installment Purchase Program (IPP)", "runs": "County Land Preservation · applications to 1 September", "status": "no",
     "pays": "Base value ($1,000/ac) plus ranking points per acre, paid as tax-free interest over 10–20 years and a balloon; 46 applicants for 21 slots in FY2025",
     "why": "Code § 1-13-35(C)(4): applicants “must have further subdivision rights.” The no-rights exception (adjoining ≥ 50 eased acres — this parcel does) pays base value only, and the county has not said it would pay base value for rights MET already holds."},
    {"program": "Frederick County Critical Farms", "runs": "County · for contract purchasers of farms", "status": "no",
     "pays": "A 5-year option worth 75% of easement value (≈ 51% of land FMV) paid to the buyer at purchase, repaid when MALPF buys",
     "why": "Code § 1-13-34(B)(4): the farm “must be able to have additional residential lots created.” A 98%-eased parcel cannot. It is also a loan against a future MALPF sale, not a grant. Cycle closed as of March 2026."},
    {"program": "MARBIDCO Next Gen / Small Acreage Next Gen", "runs": "MARBIDCO · monthly applications", "status": "no",
     "pays": "Up to 51% of land value (cap $500,000) at settlement as an easement option; SANG 30–60% on 10–49 ac",
     "why": "Both exist to option development rights that the buyer later sells to MALPF or the county — “extinguishing the development rights forever” cannot happen twice. UMD Extension: the farm “cannot already be subject to a permanent conservation easement.” SANG also fails on acreage."},
    {"program": "Forest mitigation bank (county FRO § 1-21-29)", "runs": "Frederick County Development Review · Graham Hubbard 301-600-1436", "status": "partly",
     "pays": "Credits sold privately to developers; county's FY2026 overview: “going sales price roughly $20,000 per acre”; existing forest banks at 2.5 : 1",
     "why": "The closest thing to “money for the woods.” Only stream, floodplain and wetland buffer forest on Ag-zoned land qualifies (≈ 7 ac here, and state law since 2021 allows new banks of existing forest only in such priority-retention areas). It needs a new perpetual deed of easement to the county, a forester's plan, survey, title opinion, a 2–3 year improvement agreement with a letter of credit, a 5-year minimum banking agreement, and MET's written consent (MET's 2025 policy treats credit sales on eased land ≥ 5 ac as a permitted commercial activity after Stewardship Committee review). Credits sell as demand arrives — county-wide 28–73 ac of existing-forest credits a year across 33 banks, two of them on Burkittsville Run / Broad Run next door. Whether the $20,000 is per credit-acre (7 ac ÷ 2.5 = 2.8 credits ≈ $56,000) or per banked acre (≈ $140,000) is not stated, and whether county staff will bank forest already under an MET easement is undocumented."},
    {"program": "Forest Conservation and Management Agreement (FCMA)", "runs": "DNR Forest Service · 15-year minimum", "status": "tax",
     "pays": "Woodland assessed at $125/ac instead of $500/ac",
     "why": "On 15 ac: $7,500 → $1,875 of assessment, saving ≈ $69/yr before the $50 entry fee, $200 plan fee and $100 five-yearly inspections — net ≈ $50/yr, with back taxes if you break the 15-year plan. A stewardship plan alone ($187.50/ac) saves ≈ $57/yr with no lock-in. MET's forestry clause governs any plan."},
    {"program": "CREP riparian forest buffer (CP22)", "runs": "USDA FSA · MDA · Catoctin Soil Conservation District", "status": "partly",
     "pays": "Annual rent of the soil rental rate plus a 200% state incentive for 15 years (≈ $350/ac/yr at Frederick's $114 rent) + a $1,000/ac state bonus through 2031 + up to 100% cost-share and a 40–50% practice incentive, so planting is effectively free",
     "why": "Three catches. Only cropped or grazed streamside acres count (perhaps 7 ac of field edge; the wooded buffer already here does not). Federal rule 7 CFR 1410.6(e)(2) bars land whose deed “requires any resource-conserving measures” — DNR notes Rural Legacy easements with mandatory stream buffers are precluded — so it turns on whether the 1998 MET deed mandates a buffer. And timing: 12 months of ownership first, the FY2026 signup closed 1 May 2026, and CRP's authority was extended only to 30 September 2026. DNR's CREP <em>permanent</em> easement is expressly closed to already-eased land (FAQ Q16). ≈ $2,400/yr gross if it all lines up; not immediate money."},
    {"program": "Family Forest Carbon Program and other carbon payments", "runs": "American Forest Foundation / TNC", "status": "no",
     "pays": "$12.10 per forested acre per year for 20 years (Appalachians sheet, April 2026), 25% more if paid up front through the Premium Partnership",
     "why": "Works “alongside most conservation easements” but needs 30+ forested acres, the legal right to harvest, and no encumbrance restricting timber harvest; this parcel has ≈ 15 ac of woods, so it fails on size, and even at 30 ac it would pay ≈ $360/yr. Forest Carbon Works needs 500 ac; NCX no longer runs a payment program."},
    {"program": "Donating a new easement or an amendment", "runs": "MET or a land trust · IRC § 170(h), TG § 10-723", "status": "none",
     "pays": "Federal deduction (50% of AGI, 100% for a qualified farmer, 15-year carryover) and a Maryland credit of up to $5,000/yr ($80,000 total) for the value given up",
     "why": "A donation is worth the value of the rights donated. After 1998 there is nothing of value left to give on 98% of the parcel; the 1.7 boundary acres are slivers under MET's 25-acre minimum. MET amends only to strengthen protection, and the requester pays the appraisal and fees."},
    {"program": "Federal estate-tax exclusion for eased land (IRC § 2031(c)), battlefield and Forest Legacy grants", "runs": "IRS · NPS · USFS/DNR", "status": "no",
     "pays": "Up to $500,000 excluded from a taxable estate; grants to governments to buy battlefield or forest land",
     "why": "§ 2031(c) requires the easement to have been granted by the decedent's family and the land held by the family for three years — a 2026 purchaser fails both. Battlefield Land Acquisition Grants and Forest Legacy pay governments for unprotected land; this parcel was itself a battlefield-viewshed purchase in 1998."},
]

# ----------------------------------------------------------------------------- levers
levers_intro = ("Ordered by dollars. The first lever is worth more than all the others together; the next three are decided in "
                "the contract, not after closing. Beginning-farmer status matters only for the two cheapest FSA loans.")

levers = [
    {"id": "price", "title": "1 · Pay the eased price, not the estate price", "lead":
     f"The listing never mentions the easement and prices the land at {m(A['implied_land_per_acre'])}/ac after the house's assessed value "
     f"— 24% above what eased farmland here has fetched in seven sales since mid-2025. The gap is about {m(A['gap_at_median'])}.",
     "items": [
        {"name": "The comps say ≈ $1.81M", "stamps": ["yes"], "text":
         f"Frederick County agricultural sales since 2023 with a perpetual easement (MET, MALPF, Rural Legacy or county) over half the parcel: "
         f"seven arms-length sales, land at a median {m(F['comps_met']['frederick_eased_incl_met']['median'])}/ac, interquartile "
         f"{m(F['comps_met']['frederick_eased_incl_met']['p25'])}–{m(F['comps_met']['frederick_eased_incl_met']['p75'])}, against "
         f"{m(F['comps_met']['frederick_uneased']['median'])}/ac for 36 un-eased sales. The two nearest are the neighbours: 5533 Gapland Rd "
         "(adjoining, MET-eased, 94 ac, restored 4,750 sq ft farmhouse, cidery and orchard) closed at $1,650,000 in August 2025 after 104 days "
         "at $1,679,000; 6208 Picnic Woods Rd (MET + Rural Legacy, 87 ac) closed at $1,150,000 in March 2026 after listing at $1,250,000. "
         "The other half of this very easement, the 160-acre manor at 6100 Burkittsville Rd, asked $3.49M in 2019, cut to $2.5M, expired, and "
         "sold at $1.85M in 2022 — 53% of the first ask.",
         "dollars": f"80.28 ac × {m(A['eased_median_per_acre'])} = {m(80.28 * A['eased_median_per_acre'])} land + {m(A['improvements_assessed'])} improvements at assessment = {m(A['value_at_median'])}\n"
                    f"at the eased 75th percentile ({m(A['eased_p75_per_acre'])}/ac): {m(A['value_at_p75'])}\nask {m(A['ask'])} → implied land {m(A['implied_land'])} = {m(A['implied_land_per_acre'])}/ac",
         "sources": ["https://www.redfin.com/MD/Jefferson/5533-Gapland-Rd-21755/home/195572976", "https://www.compass.com/homedetails/6208-Picnic-Woods-Rd-Jefferson-MD-21755/1VFHQ8_pid/", "https://mdgeodata.md.gov/imap/rest/services/PlanningCadastre/MD_PropertySales/MapServer/0"]},
        {"name": "Make the appraisal do the arguing", "stamps": ["yes"], "text":
         "Any lender's appraisal (and FSA's, under USPAP) values the property as encumbered. Hand the appraiser the recorded 1998 easement and the three "
         "eased sales above. If the appraisal lands near $1.8M the financing contingency does the negotiating for you; if you overpay, the FSA "
         "down-payment loan and the lender's loan-to-value are both computed on the lower of price and appraisal, so the excess comes out of your cash.",
         "dollars": "every $100,000 of price above appraisal = $100,000 more cash at closing (0% financed)", "sources": ["https://www.law.cornell.edu/cfr/text/7/761.7"]},
        {"name": "Price the house honestly", "stamps": ["partly"], "text":
         "The assessed improvement value ($1,031,500 for a grade-7 2005 house) is a cost-based figure and probably fair; the market for a $1M house on an "
         "81-acre eased farm ninety minutes from Washington is thin (the ciderworks farm needed 104 days, the manor needed three years). Every $100,000 "
         "of house price also becomes ≈ $1,222/yr of tax once SDAT reassesses toward your deed price (the homestead cap only slows it).",
         "dollars": "$1,222 per year of property tax per $100,000 of assessment", "sources": ["https://frederickcountymd.gov/DocumentCenter/View/373830/TAX-RATES-2026-2027"]},
     ]},
    {"id": "closing", "title": "2 · Taxes at closing you can legally avoid", "lead":
     "Four rules, all in the contract and the deed package. On a $1,800,000 purchase they are worth about $55,000 to $75,000 depending on who is on the deed.",
     "items": [
        {"name": "Sign a Declaration of Intent — no agricultural transfer tax", "stamps": ["yes"], "text":
         "Maryland taxes the deed on land that leaves the agricultural-use assessment: 5% on transfers of 20 acres or more, plus a 25% surcharge (6.25% effective), on the "
         "consideration attributable to the ag-assessed land — the price less the assessed improvements and the market value of the one-acre homesite. It is not charged at all "
         "if the purchaser signs SDAT form RP-18, a Declaration of Intent to keep the land in agricultural use for five full consecutive taxable years. On this parcel the "
         "promise costs nothing: the MET easement already forbids anything else. Breaking it triggers the tax on fair market value plus 10%. Beginning-farmer status is irrelevant.",
         "dollars": "at $1,800,000: net land ≈ $1,800,000 − $1,031,500 − ≈$100,000 homesite ≈ $670,000 × 6.25% ≈ $42,000 avoided\nat $1,999,900: ≈ $870,000 × 6.25% ≈ $54,000 avoided\nask SDAT Frederick (301-815-5350) for the binding Agricultural Transfer Tax Statement before contract",
         "sources": ["https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=13-303&enactments=false", "https://dat.maryland.gov/realproperty/Documents/agtransf.pdf", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=13-305&enactments=false"]},
        {"name": "Record a purchase-money deed of trust — no recordation tax on the loan", "stamps": ["yes"], "text":
         "Frederick County's recordation tax is $7 per $500 (1.4%) on deeds and on secured debt. A deed of trust that recites purchase money, is executed within 30 days of the deed and "
         "recorded within 30 days after it is exempt (TP § 12-108(i)); a later refinance is exempt up to the unpaid balance. Any lender, including FSA and Farm Credit, qualifies. Record "
         "any second lien (FSA participation, seller note) in the same package.",
         "dollars": "$1,530,000 loan (85% of $1.8M) × 1.4% = $21,420 avoided\nper $100,000 borrowed: $1,400",
         "sources": ["https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=12-108&enactments=false", "https://frederickcountymd.gov/7861/Record-a-Deed"]},
        {"name": "First-time Maryland homebuyer: the seller pays the transfer taxes", "stamps": ["partly"], "text":
         "If every grantee on the deed has never owned a Maryland principal residence and will live in the house, the state transfer tax drops from 0.5% to 0.25% and by statute the "
         "seller pays all of it (TP § 13-203) and, unless the contract says otherwise, all of the recordation tax on the deed (RP § 14-104). Buying through an LLC or adding a "
         "non-qualifying co-owner forfeits it. The seller will price it in, so raise it in the offer, not at the table. Whether the clerk applies the residential rule to an 81-acre "
         "farm deed with a dwelling is unconfirmed — the statute has no acreage cap.",
         "dollars": "at $1,800,000, buyer's customary half of state transfer ($4,500) + recordation ($12,600) = $17,100 → $0",
         "sources": ["https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=13-203&enactments=false", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=grp&section=14-104&enactments=false"]},
        {"name": "What cannot be avoided", "stamps": ["tax"], "text":
         "Frederick County has no county transfer tax (“at this time, Frederick County does not impose a County Transfer Tax”). Clerk's fees are $60–$115 per instrument plus a $20 lien "
         "certificate. The MET easement adds only a free 30-day post-sale notice to MET (RP § 10-705); no MET approval or right of first refusal.",
         "dollars": "≈ $250 of fixed fees", "sources": ["https://www.mdcourts.gov/clerks/frederick/recordingfees"]},
     ]},
    {"id": "financing", "title": "3 · The cheapest money, in order", "lead":
     "FSA's two subsidised farm-ownership loans are the cheapest dollars available anywhere, but each is capped well below the price and each requires you to be the owner-operator "
     "of a family farm with three years of farm-business experience and a lender's turndown. The bulk of the loan will be Farm Credit. Rates below are September 2026 and reset monthly.",
     "items": [
        {"name": "FSA Down Payment Farm Ownership loan — 2.000%, beginning farmers", "stamps": ["yes"], "text":
         "You put down 5%; FSA lends 45% of the least of price, appraisal or $667,000 (so at most $300,150) at 2.000% for 20 years; a commercial lender carries the rest on a 30-year "
         "amortisation with no balloon in the first 20 years, and FSA guarantees that lender up to 95% with no fee. Beginning farmer means under 10 years operating a farm and owning "
         "no more than 30% of the county's average farm size — 30% of Frederick County's 138-acre average is 41.4 acres, a test on what you already own, not on the 81 acres you are buying.",
         "dollars": "$300,150 at 2.000% ≈ $6,000 first-year interest, ≈ $18,350/yr level payment over 20 years (≈ $26,170 at 6%: about $7,800/yr less, ≈ $12,000 less interest in year one)\ncash needed at $1.8M: $90,000 (5%) + closing",
         "sources": ["https://www.fsa.usda.gov/news-events/news/09-01-2026/usda-announces-september-2026-lending-rates-agricultural-producers", "https://www.law.cornell.edu/cfr/text/7/764.203", "https://www.law.cornell.edu/cfr/text/7/761.2"]},
        {"name": "FSA Joint Financing — 4.000%, up to $600,000, any farmer", "stamps": ["yes"], "text":
         "FSA funds up to half of the amount financed (capped at $600,000) at the direct rate minus two points, 40-year term, alongside a lender for the other half or more. Same "
         "owner-operator, three-year experience and credit-elsewhere tests; no beginning-farmer requirement. Direct funds are appropriated annually and can queue, so file early in "
         "the federal fiscal year (October).",
         "dollars": "$600,000 at 4.000% = $24,000/yr interest, ≈ $30,300/yr level over 40 years\nsaves $2,000/yr per $100,000 against the regular 6.000% direct rate",
         "sources": ["https://www.law.cornell.edu/cfr/text/7/764.154", "https://www.fsa.usda.gov/resources/loans/farm-ownership-loans"]},
        {"name": "Horizon Farm Credit for the balance — 85% LTV, patronage", "stamps": ["yes"], "text":
         "The cooperative lender for Frederick County (888-339-3334): no acreage or loan cap, up to 85% of price or appraisal with no mortgage insurance for first-time rural property "
         "owners (their stated term — confirm it covers you), farm income counted, and a patronage refund on profits — 100 basis points declared for 2024 and paid in 2025, not guaranteed. Ask them to layer FSA Joint or Down "
         "Payment underneath and to request the FSA 95% guarantee. MARBIDCO's MRBIFF participation piece (4.25% plus up to 0.75% servicing, up to $700,000, 15-year balloon; its direct loan adds a 1% origination fee) is a "
         "further tranche if FSA money is capped or queued. Their note rate is not published — get a written quote.",
         "dollars": "at $1.8M: loan $1,530,000, cash $270,000 (or $90,000 with the FSA down-payment structure)\npatronage at 100 bp ≈ $1,000/yr per $100,000 borrowed (2024 level; consumer-purpose loans excluded, not guaranteed)",
         "sources": ["https://www.horizonfc.com/loans/home-loans", "https://www.horizonfc.com/patronage", "https://marbidco.org/mbriff/"]},
        {"name": "Out of reach: MARBIDCO Next Gen, Maryland Mortgage Program, USDA housing loans", "stamps": ["no"], "text":
         "Next Gen and SANG option development rights this parcel no longer has. The Maryland Mortgage Program caps acquisition at $1,306,974 (non-targeted) and the loan at $832,750. "
         "USDA Rural Development's guaranteed housing loan excludes farm-related, income-producing property outright.",
         "dollars": "$0", "sources": ["https://www.marbidco.org/next-generation-farmland-acquisition-program", "https://mmp.maryland.gov/Lenders/Documents/income-and-purchase-limits.pdf"]},
     ]},
    {"id": "holding", "title": "4 · Holding cost: the house is the tax bill", "lead":
     "FY2027 rate $1.110 county + $0.112 state = $1,222 per $100,000 of assessment. On today's $1,161,500 assessment that is $14,194 (the listing's figure) — $12,605 on the house, $1,589 on the land.",
     "items": [
        {"name": "Keep the land at $500/ac", "stamps": ["yes"], "text":
         "Two statutes hold the land there: agricultural-use assessment (TP § 8-209) for land actually farmed — an 81-acre parcel with ~63 acres cropped or grazed faces no income test — "
         "and conservation-property valuation (TP § 8-209.1) for land under an MET easement whether farmed or not. File the SDAT Agricultural Use application with the Declaration of Intent "
         "at settlement so nothing lapses in the transfer; keep a crop or pasture lease or Schedule F on file.",
         "dollars": "80.28 ac × $500 = $40,140 → ≈ $490/yr; at eased market value (≈ $780,000) it would be ≈ $9,500/yr",
         "sources": ["https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=8-209.1&enactments=false", "https://dat.maryland.gov/realproperty/Pages/The-Agricultural-Use-Assessment.aspx"]},
        {"name": "Homestead credit: file it the week the deed records", "stamps": ["yes"], "text":
         "Frederick County caps annual taxable-assessment growth on an owner-occupied principal residence at 5% (state 10%) — the dwelling and its curtilage, not the farmland. Nothing in the "
         "first tax year after a transfer; from the second year it compounds. Move in before 1 July of the first full tax year and file at onestop.md.gov (SDAT ID 11 + 432257).",
         "dollars": "if SDAT reassesses the house toward a $1.8M deed (+50%, phased 16.7%/yr): ≈ $1,600 saved in the first capped year, growing each year",
         "sources": ["https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=9-105&enactments=false", "https://dat.maryland.gov/realproperty/pages/maryland-homestead-tax-credit.aspx"]},
        {"name": "Claim the two county credits the seller is not using", "stamps": ["yes"], "text":
         "Frederick County Code § 1-8-62 gives 100% of the county tax on land under a permanent easement conveyed to the county or a state agency to preserve agricultural use (residence + 1 ac excluded), and § 1-8-63 gives 100% of the county tax on farm buildings on ag-use-assessed land used for a recognised agricultural activity. Both are in the current code although the Treasury's credits page omits them; both are applied for by 1 October. The current bill is exactly 1.222% of the assessment, so neither is being claimed today. The land credit is worth ≈ $446/yr if the county accepts an MET easement as agricultural; the building credit depends on how much of the $1,031,500 improvement assessment is the barns.",
         "dollars": "land credit ≈ $446/yr · building credit = 1.11% × the barns' assessed value (e.g. $150,000 of barns ≈ $1,665/yr)",
         "sources": ["https://frederickcountymd.gov/DocumentCenter/View/334378", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=9-312&enactments=false"]},
        {"name": "Small change", "stamps": ["tax"], "text":
         "Pay the bill in July for 1% off (≈ $142). An FCMA on the woods nets ≈ $50/yr for a 15-year commitment. Add the $88 system benefit charge and the $60 Bay Restoration Fee for septic.",
         "dollars": "≈ $150–$600/yr all told; the all-in bill on today's assessment is $14,194 + $88 + $60 = $14,342", "sources": ["https://frederickcountymd.gov/3188/Tax-Rates", "https://dnr.maryland.gov/forests/pages/programapps/fcmp.aspx"]},
     ]},
    {"id": "income", "title": "5 · What the land can earn", "lead":
     "Realistic, low-effort income is about $9,000–$12,000 a year — roughly half a percent of the price. It trims the carrying cost; it does not finance the purchase. Every figure below was checked against the source and the MET model easement; the 1998 deed may be narrower.",
     "items": [
        {"name": "Cash-rent the fields — ≈ $6,700/yr", "stamps": ["yes"], "text":
         "USDA NASS county cash rents (2025 release, via UMD Extension): Frederick County non-irrigated cropland $114/ac, pasture $83.50/ac. A written 1–3 year cash lease to a neighbouring farmer is passive income, needs no farmer status, and keeps the agricultural-use assessment. Keep any lease of part of the land under 20 years — MET's model easement treats a longer partial lease as a subdivision. Ask the seller who farms it now and at what rent.",
         "dollars": "47 ac × $114 = $5,358 + 16 ac × $83.50 = $1,336 → ≈ $6,700/yr\nhaying it yourself grosses ≈ $18,000 (2.2 t/ac × $175/t) but only nets more with your own equipment; a one-third crop share ≈ $6,000",
         "sources": ["https://extension.umd.edu/sites/extension.umd.edu/files/2025-10/MD_Cash_Rental_Rates_Write_Up_accessible.pdf", "https://www.nass.usda.gov/Quick_Stats/Ag_Overview/stateOverview.php?state=Maryland&year=2025"]},
        {"name": "Hunting lease on the woods and stream corridor — ≈ $2,000/yr", "stamps": ["partly"], "text":
         "Frederick County led Maryland's deer harvest in 2024–25, and the neighbouring 6208 Picnic Woods listing sold on “excellent hunting … large whitetail bucks.” The only current price data are a lease marketplace's: central Maryland $25–$35/ac/yr, 50–100 ac tracts $1,250–$3,500 (not an official statistic). MET's model counts hunting as passive recreation and allows stands and blinds, but limits commercial passive recreation run by a resident to a “de minimis” scale — a small lease fits, an outfitting business does not. On liability, a hunter you admit on a limited-entry basis, with or without charge, assumes responsibility for their own safety under NR § 5-1109(a)(2); still use a written lease with indemnity and the lessees' liability insurance.",
         "dollars": "≈ 15 ac of woods plus stream buffer and field edges: plan on ≈ $2,000/yr; archery-only or two or three guns ≈ $1,200–$1,500",
         "sources": ["https://huntlease.co/blog/maryland-hunting-leases-2025-complete-guide-to-prices-regions-laws/", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gnr&section=5-1109&enactments=false", "https://agrisk.umd.edu/post/hunting-leases-checklist-for-those-considering-a-hunting-lease"]},
        {"name": "CREP forest buffer on the cropped stream edge — contingent", "stamps": ["partly"], "text":
         "Maryland's CREP pays three times the soil rental rate for 15 years on a riparian forest buffer (CP22), a $1,000/ac state signing bonus through 2031, and 87.5% cost-share plus a 40% practice incentive, so planting costs nothing net. Only cropped or grazed land with cropping history qualifies — the wooded buffer already here does not — so the candidate is the field edge along the stream, perhaps 7 acres, and you must have owned the land 12 months first. The catch is timing: the FY2026 continuous signup closed on 1 May 2026, only 1.9 million acres remain under the national cap, and CRP's authority was extended only to 30 September 2026, so a 2027 offer depends on Congress. Do not underwrite it.",
         "dollars": "7 ac × (3 × $114 + $10 maintenance) ≈ $2,460/yr gross, ≈ $1,670/yr after the rent those acres earn today; one-time ≈ $7,700 ($1,000/ac state bonus + $100/ac federal incentive)",
         "sources": ["https://www.fsa.usda.gov/resources/conservation/conservation-reserve-program", "https://mda.maryland.gov/resource_conservation/pages/crep.aspx", "https://dnr.maryland.gov/wildlife/documents/crep_qa_factsheet.pdf", "https://www.fsa.usda.gov/news-events/news/02-10-2026/usda-open-continuous-general-conservation-reserve-program-enrollment"]},
        {"name": "Cost-share if you run livestock: MACS 87.5%, EQIP 75–90%, CSP $4,000/yr floor", "stamps": ["yes"], "text":
         "Maryland's MACS programme pays 87.5% of stream-exclusion fencing (NRCS 382), a livestock stream crossing (578), a watering facility (614), spring development and a well, through the Catoctin Soil Conservation District (301-695-2803 x3), for a herd of 15 animal units (pro-rated below) with a soil-conservation and nutrient-management plan. A funded crossing is also the natural way to reach the north-block barns. NRCS EQIP reimburses 75% of practice costs — 90% with half paid in advance for a beginning farmer (under 10 consecutive years) — and CSP pays a $4,000/yr floor over five years to the operator; if you cash-rent, the tenant is the applicant. Fencing is expressly allowed by the MET model; run-in sheds under 500 sq ft may sit outside the building envelope but not in the 100 ft stream buffer.",
         "dollars": "illustration: 3,000 ft of fence + a crossing + trough and pipeline ≈ $26,000 → your share ≈ $3,250\nCSP floor $4,000 × 5 years = $20,000 if you are the operator and rank",
         "sources": ["https://mda.maryland.gov/resource_conservation/Documents/1_Eligible_Practices_and_Cost-Share_Rates_NRCS_Numerical_Order.pdf", "https://news.maryland.gov/mda/press-release/2026/01/29/department-launches-outreach-campaign-to-promote-stream-protection-herd-health/", "https://www.law.cornell.edu/cfr/text/7/1466.23", "https://www.nrcs.usda.gov/programs-initiatives/csp-conservation-stewardship-program"]},
        {"name": "Barns, boarding, a farm stand — allowed in principle, no verified rates", "stamps": ["partly"], "text":
         "MET's model easement defines agriculture to include boarding and training horses, allows commercial activity inside allowed structures, on-farm sale of products mostly grown on the property, and equestrian services; it prohibits industrial uses, kennels and golf. MET warns that older deeds “prohibit all but a few commercial activities” beyond selling what the farm grows or what fits inside an existing structure without changing its appearance, and weddings or festivals need written approval 60 days ahead (20 ac, 150 guests, three days). No Frederick County boarding or barn-rental rates were verifiable; field board in the existing barns is the low-labour version, and bay storage runs about $1,200/yr per $100/month bay.",
         "dollars": "not underwritten — get MET's written read of the 1998 deed first",
         "sources": ["https://dnr.maryland.gov/met/Documents/PDFs/MET_ModelEasement.pdf", "https://dnr.maryland.gov/met/documents/conservation_easement_policies.pdf"]},
        {"name": "If you farm it yourself: MDA cover-crop payments and a timber sale", "stamps": ["partly"], "text":
         "Maryland's Cover Crop Program pays the operator $35/ac base and up to $65/ac with add-ons on cropland planted to cover crops (10-acre minimum, nutrient-management plan required); sign-up is each June–July at the soil conservation district, so 2027 is the first cycle available, and if a tenant farms the fields it is the tenant's money unless the lease says otherwise. The ≈ 15 ac of woods can be harvested once under a forest stewardship plan that MET approves (its model easement permits cutting in mapped forest areas under an approved plan); no stand data exists, so the value is unknown until a forester walks it.",
         "dollars": "cover crop on 47 ac: $1,645–$3,055/yr to whoever farms it\ntimber: one-time, unpriced — get a DNR or licensed forester's cruise",
         "sources": ["https://mda.maryland.gov/resource_conservation/Pages/cover_crop.aspx", "https://dnr.maryland.gov/met/Documents/PDFs/MET_ModelEasement.pdf"]},
        {"name": "Solar lease: $0", "stamps": ["no"], "text":
         "The one high-dollar land income this farm cannot capture. MET's model allows solar only on allowed buildings (or another structure with approval) to serve the property's own load, and MET has denied commercial generation on eased land as industrial use. A barn-roof array for the house is fine; a lease to a developer is not.",
         "dollars": "$0", "sources": ["https://dnr.maryland.gov/met/Documents/PDFs/MET_ModelEasement.pdf", "https://www.matrixsolar.com/blog/agriculture-preservation-solar-maryland"]},
     ]},
]

# ----------------------------------------------------------------------------- comps + budget
comps_prose = (
    "<p>Arms-length agricultural sales within the acquisition screen's study area and a five-mile margin, three years back, multi-account transfers collapsed, improvement-dominated "
    "sales dropped; land price is sale price less SDAT's assessed improvements. The screen originally counted only MALPF, Rural Legacy and county easements as “eased”, which "
    "left 5533 Gapland Rd in the un-eased band; counting MET easements moves it and lifts the Frederick eased band to seven sales. The four below are the ones a buyer should carry "
    "into the negotiation; the first three are MET-eased farms within two miles.</p>")

budget_price = "$1,800,000"
budget_intro = (
    "<p>A purchase at the comps-supported price, financed the cheapest way a qualifying beginning farmer could, with every avoidable tax avoided. Change the price and the "
    "percentage lines scale; the FSA caps do not. Income lines are what the land earns with almost no labour; they offset about half a percent of the price a year. This is arithmetic from public rates, not a lender's quote or tax advice.</p>")
budget = [
    ("Purchase price", "$1,800,000", "eased comps at the median $9,719/ac plus the house at its assessed $1,031,500 (ask $1,999,900)"),
    ("Cash at closing — down payment", "$90,000", "5% under the FSA Down Payment structure; $270,000 (15%) with Farm Credit alone at 85% LTV"),
    ("FSA Down Payment loan", "$300,150", "45% of the $667,000 cap · 2.000% · 20 years · ≈ $18,350/yr"),
    ("Farm Credit first lien (FSA-guaranteed 95%)", "$1,409,850", "balance · 30-year amortisation · rate by quote · patronage ≈ $14,000/yr at 100 bp"),
    ("Alternative: FSA Joint Financing", "$600,000 at 4.000%", "if not a beginning farmer: ≈ $30,300/yr over 40 years, Farm Credit $930,000 + 15% cash"),
    ("State transfer tax (0.5%)", "$4,500 → $0", "buyer's half; $0 if every buyer is a first-time Maryland homebuyer (0.25%, seller pays)"),
    ("Recordation tax on the deed (1.4%)", "$12,600 → $0", "buyer's half by custom; seller pays all for a first-time buyer unless the contract says otherwise"),
    ("Recordation tax on the loan", "$0", "purchase-money deed of trust exempt (saves $23,940 on $1.71M of liens)"),
    ("Agricultural transfer tax + surcharge", "$0", "Declaration of Intent (RP-18) signed at settlement; ≈ $42,000 without it"),
    ("County transfer tax", "$0", "Frederick County levies none"),
    ("Clerk and lien-certificate fees", "≈ $250", "two instruments plus the county lien certificate"),
    ("Property tax, year 1", "$14,194", "on the current assessment; rises ≈ $1,222 per $100,000 as SDAT moves toward the deed price, house capped at 5%/yr from year 2"),
    ("Land tax inside that", "≈ $490", "80.28 ac × $500 × 1.222% — the MET easement's lasting benefit"),
    ("Post-sale notice to MET", "$0", "within 30 days of settlement (RP § 10-705)"),
    ("Income: cash rent on 63 ac", "≈ $6,700/yr", "47 ac × $114 + 16 ac × $83.50, Frederick County 2025 NASS rates"),
    ("Income: hunting lease", "≈ $2,000/yr", "central-Maryland marketplace rate of $25–$35/ac on the huntable ground; de minimis scale under MET"),
    ("Income: CREP buffer", "≈ $1,700/yr net + ≈ $7,700 once", "only if CRP is reauthorised and a signup opens after 12 months of ownership — not underwritten"),
]

asks = [
    "<strong>The recorded 1998 Deed of Conservation Easement</strong> (MET 0424BER98 / county ISTEA-10) with exhibits and every amendment, which the seller must deliver before contract under RP § 10-705, plus MET's easement file (410-697-9515): the consideration clause, the Board of Public Works date, the reserved-dwelling clause and building envelope, the forest-area map and stewardship-plan requirement, the stream-buffer clause, commercial-use and ecosystem-services language, the latest monitoring report and any open violation. This one document decides CREP, forest banking, timber, boarding and every future structure.",
    "<strong>SDAT's Agricultural Transfer Tax Statement</strong> for District 22 Account 432257 and the seller's written TP § 13-308 notice; confirmation that 80.28 acres carry the agricultural-use assessment; and form RP-18 (Declaration of Intent) plus the Agricultural Use application in the settlement package.",
    "<strong>The last three county tax bills</strong> — to see that neither the § 1-8-62 land credit nor the § 1-8-63 building credit is applied, and whether the account carries the “Agricultural Transfer Tax” recapture flag — and SDAT's split of the $1,031,500 improvement assessment between the house and the barns.",
    "<strong>Title and authority to convey.</strong> The owner of record is “Berman Melvin J”, the last recorded transfer is 1981, and the easement was signed in 1998; confirm who holds title today (an estate or trust would change the closing) and that the MET easement is disclosed in writing — the MLS listing does not mention it.",
    "<strong>The permit file for the 2005 house</strong>, given the 1981 plat's “no residential building allowed” note on the remainder, what the second county address on this account (5650 Catholic Church Rd) is, well yield, and the septic system's rated bedrooms.",
    "<strong>The current crop lease</strong> behind the listing's “Crops Reserved” — tenant, rent, term, whether it survives closing, and who holds the nutrient-management plan and any cover-crop enrolment.",
    "<strong>A written Horizon Farm Credit quote</strong> and an FSA eligibility read from the Frederick USDA Service Center on the three-year farm-experience test, before choosing between the Down Payment and Joint structures; note the NRCS FY2027 EQIP/CSP deadline of 13 November 2026 if you intend to farm.",
    "<strong>Frederick County Land Preservation</strong> (Shannon O'Neil, 301-600-1411) on whether the IPP's no-subdivision-rights exception would pay base value on MET land, <strong>Development Review</strong> (Graham Hubbard, 301-600-1436) on banking the stream-buffer forest and whether the $20,000 figure is per credit or per banked acre, and <strong>Treasury</strong> (301-600-1111) on whether an MET easement qualifies under § 1-8-62.",
]

sources = [
    ("Bright MLS listing MDFR2089452 via Redfin", "https://www.redfin.com/MD/Jefferson/5550-Catholic-Church-Rd-21755/home/205569228"),
    ("Coldwell Banker listing page", "https://www.coldwellbankerhomes.com/md/jefferson/5550-catholic-church-rd/pid_73759651/"),
    ("SDAT real property record (District 22, Account 432257)", F["sdat"]["url"]),
    ("MD iMAP property data (SDAT extract, May 2026)", "https://mdgeodata.md.gov/imap/rest/services/PlanningCadastre/MD_PropertyData/MapServer/0"),
    ("MD iMAP Protected Lands — MET easements layer", "https://mdgeodata.md.gov/imap/rest/services/Environment/MD_ProtectedLands/FeatureServer/2"),
    ("Frederick County GIS — Agricultural Preservation (ISTEA-10)", "https://fcgis.frederickcountymd.gov/server_pub/rest/services/PlanningAndPermitting/AgriculturalPreservationAll/MapServer/0"),
    ("Frederick County 2022 Land Preservation, Parks and Recreation Plan", "https://dnr.maryland.gov/land/Documents/Stewardship/2022-LPPRP-Frederick-County-Final.pdf"),
    ("Scenic America — Berman Farm scenic easement case study", "https://www.scenic.org/take-action/resources/case-studies/case-studies-scenic-easements/case-study-scenic-easement-in-frederick-county-md/"),
    ("1981 farm lot plat, Plat Book 24 p. 144", "https://fcgis.frederickcountymd.gov/dpss/otherplats/0024-0144.TIF"),
    ("MET FAQ", "https://dnr.maryland.gov/met/Pages/faq.aspx"),
    ("MET model easement", "https://dnr.maryland.gov/met/Documents/PDFs/MET_ModelEasement.pdf"),
    ("MET Conservation Easement Policies (May 2025)", "https://dnr.maryland.gov/met/documents/conservation_easement_policies.pdf"),
    ("MET tax benefits", "https://dnr.maryland.gov/met/pages/tax_benefits.aspx"),
    ("Tax-Property § 9-107 conservation property tax credit", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=9-107&enactments=false"),
    ("Tax-Property § 8-209.1 conservation property valuation", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=8-209.1&enactments=false"),
    ("Tax-Property § 13-303 agricultural land transfer tax", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=13-303&enactments=false"),
    ("SDAT — Agricultural Transfer Tax (rev. 2019)", "https://dat.maryland.gov/realproperty/Documents/agtransf.pdf"),
    ("Tax-Property § 12-108 recordation tax exemptions", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=12-108&enactments=false"),
    ("Tax-Property § 13-203 state transfer tax", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=13-203&enactments=false"),
    ("Real Property § 14-104 allocation of transfer taxes", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=grp&section=14-104&enactments=false"),
    ("Real Property § 10-705 conservation easement notice on sale", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=grp&section=10-705&enactments=false"),
    ("Frederick County — Record a Deed (recordation tax)", "https://frederickcountymd.gov/7861/Record-a-Deed"),
    ("Frederick Circuit Court Clerk — recording fees (no county transfer tax)", "https://www.mdcourts.gov/clerks/frederick/recordingfees"),
    ("Frederick County tax rates FY2026–2027", "https://frederickcountymd.gov/DocumentCenter/View/373830/TAX-RATES-2026-2027"),
    ("Tax-Property § 9-105 homestead credit", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=9-105&enactments=false"),
    ("Tax-Property § 9-312(g) Frederick County agricultural preservation credit", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtp&section=9-312&enactments=false"),
    ("Frederick County tax credits page", "https://frederickcountymd.gov/8277/Tax-Credits"),
    ("USDA FSA September 2026 lending rates", "https://www.fsa.usda.gov/news-events/news/09-01-2026/usda-announces-september-2026-lending-rates-agricultural-producers"),
    ("FSA beginning farmers and ranchers loan fact sheet", "https://www.fsa.usda.gov/sites/default/files/2026-02/FSA-Loan%20Beginning%20Farmers%20and%20Ranchers.pdf"),
    ("7 CFR 764.154 joint financing · 764.203 down payment · 761.2 beginning farmer", "https://www.law.cornell.edu/cfr/text/7/764.154"),
    ("MARBIDCO Next Generation Farmland Acquisition Program", "https://www.marbidco.org/next-generation-farmland-acquisition-program"),
    ("UMD Extension on Next Gen / SANG eligibility", "https://extension.umd.edu/news-events/news/demystifying-agricultural-conservation-two-programs-are-helping-beginning-farmers-purchase-and"),
    ("Horizon Farm Credit home loans and patronage", "https://www.horizonfc.com/loans/home-loans"),
    ("Maryland Mortgage Program 2026 limits", "https://mmp.maryland.gov/Lenders/Documents/income-and-purchase-limits.pdf"),
    ("MALPF application instructions (existing encumbrances)", "https://mda.maryland.gov/malpf/Documents/MALPF%20Easement%20Application%20FY%2021%20w-instructions%202.pdf"),
    ("MALPF fact sheets 1 and 3", "https://mda.maryland.gov/malpf/Documents/fact01Eligibility.pdf"),
    ("Frederick County MALPF FY2027 cycle notice", "https://frederickcountymd.gov/CivicAlerts.aspx?AID=5740&ARC=8851"),
    ("Frederick County Code § 1-13-35 IPP (AFT copy)", "https://s30428.pcdn.co/wp-content/uploads/sites/2/2019/09/Frederick_County-_MD_Local_PACE_Installment_Plan_Ordinance_0.pdf"),
    ("Frederick County Code § 1-13-34 Critical Farms (archived)", "https://web.archive.org/web/20230923093117id_/https://codelibrary.amlegal.com/codes/frederickcounty/latest/frederickco_md/0-0-0-3726"),
    ("Frederick County Rural Legacy EVS (2015 draft)", "https://farmlandinfo.org/wp-content/uploads/sites/2/2016/06/Frederick_md_2015_rural_legacy_evs.pdf"),
    ("DNR Rural Legacy grants manual", "https://dnr.maryland.gov/land/Documents/RuralLegacy/rlp_grants_manual_april_2009.pdf"),
    ("Frederick County forest banking overview (FY2026)", "https://frederickcountymd.gov/DocumentCenter/View/333724/Banking-Overview?bidId="),
    ("Frederick County FRO § 1-21-29 ordinance text", "https://frederickcountymd.gov/DocumentCenter/View/333725"),
    ("Frederick County available forest banking sites (1 Sep 2026)", "https://frederickcountymd.gov/DocumentCenter/View/350019"),
    ("Natural Resources § 5-1601 (qualified conservation, mitigation banking)", "https://mgaleg.maryland.gov/2026RS/Statute_Web/gnr/5-1601.pdf"),
    ("DNR Forest Conservation and Management Program", "https://dnr.maryland.gov/forests/pages/programapps/fcmp.aspx"),
    ("Frederick County zoning — use table and § 1-19-8.355 shooting range (archived)", "https://web.archive.org/web/20250322100625id_/https://codelibrary.amlegal.com/codes/frederickcounty/latest/frederickco_md/0-0-0-41341"),
    ("Natural Resources § 10-410 hunting safety zone", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gnr&section=10-410&enactments=false"),
    ("MD iMAP land use / land cover 2010", "https://mdgeodata.md.gov/imap/rest/services/PlanningCadastre/MD_LandUseLandCover/MapServer/1"),
    ("MD iMAP forests — forested buffers", "https://mdgeodata.md.gov/imap/rest/services/Biota/MD_Forests/MapServer/1"),
    ("MD iMAP 2018 canopy cover", "https://mdgeodata.md.gov/imap/rest/services/Biota/MD_CanopyCover/ImageServer"),
    ("UMD Extension — Maryland cash rental rates (NASS 2025)", "https://extension.umd.edu/sites/extension.umd.edu/files/2025-10/MD_Cash_Rental_Rates_Write_Up_accessible.pdf"),
    ("USDA FSA — Conservation Reserve Program status (signups, authority)", "https://www.fsa.usda.gov/resources/conservation/conservation-reserve-program"),
    ("MDA — Maryland CREP", "https://mda.maryland.gov/resource_conservation/pages/crep.aspx"),
    ("MDA — MACS cost-share rates", "https://mda.maryland.gov/resource_conservation/Documents/1_Eligible_Practices_and_Cost-Share_Rates_NRCS_Numerical_Order.pdf"),
    ("7 CFR 1466.23 EQIP payment rates", "https://www.law.cornell.edu/cfr/text/7/1466.23"),
    ("NRCS Conservation Stewardship Program", "https://www.nrcs.usda.gov/programs-initiatives/csp-conservation-stewardship-program"),
    ("Natural Resources § 5-1109 hunter assumption of liability", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gnr&section=5-1109&enactments=false"),
    ("HuntLease Maryland lease price guide (marketplace, not official)", "https://huntlease.co/blog/maryland-hunting-leases-2025-complete-guide-to-prices-regions-laws/"),
    ("Frederick County Code Chapter 1-8 (§§ 1-8-62, 1-8-63 tax credits)", "https://frederickcountymd.gov/DocumentCenter/View/334378"),
    ("MDA Cover Crop Program", "https://mda.maryland.gov/resource_conservation/Pages/cover_crop.aspx"),
    ("7 CFR 1410.6 CRP eligible land (deed-restricted land)", "https://www.law.cornell.edu/cfr/text/7/1410.6"),
    ("DNR CREP questions and answers (permanent easement not on eased land)", "https://dnr.maryland.gov/wildlife/documents/crep_qa_factsheet.pdf"),
    ("Natural Resources § 5-1610.1 (post-2020 mitigation banks)", "https://mgaleg.maryland.gov/2026RS/Statute_Web/gnr/5-1610.1.pdf"),
    ("Frederick County forest banking agreement (5-year minimum)", "https://www.frederickcountymd.gov/DocumentCenter/View/277103"),
    ("Family Forest Carbon Program — Growing Mature Forests, Appalachians (April 2026)", "https://assets.familyforestcarbon.org/assets/47849fdf-06f9-4111-8f29-7ed695712acb"),
    ("Tax-General § 10-723 conservation easement income tax credit", "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-723&enactments=false"),
    ("IRC § 2031(c) estate-tax exclusion for eased land", "https://www.law.cornell.edu/uscode/text/26/2031"),
    ("NRCS Maryland EQIP (FY2027 deadline 13 November 2026)", "https://www.nrcs.usda.gov/programs-initiatives/eqip-environmental-quality-incentives/maryland"),
    ("Sale: 5533 Gapland Rd (Redfin)", "https://www.redfin.com/MD/Jefferson/5533-Gapland-Rd-21755/home/195572976"),
    ("Sale: 6208 Picnic Woods Rd (Compass)", "https://www.compass.com/homedetails/6208-Picnic-Woods-Rd-Jefferson-MD-21755/1VFHQ8_pid/"),
    ("Sale: 6100 Burkittsville Rd (Compass)", "https://www.compass.com/listing/6100-burkittsville-road-jefferson-md-21755/"),
]

foot = ("Prepared 19 September 2026 from Maryland and Frederick County public records, the Bright MLS listing as syndicated, and the statutes and programme documents linked above; "
        "the SDAT web page itself and the county's code library blocked automated reads, so assessment fields come from the state's May 2026 SDAT extract and code text from archived copies. "
        "Three research passes (the property record, eight preservation programmes, four affordability levers — 42 agents) each had every finding challenged by independent refuters; the corrections that survived (the agricultural transfer tax tiers, CREP's 2026 status and deed-restriction rule, Rural Legacy being unbarred in law, the NRCS FY2027 deadline) are reflected, and a final completeness check surfaced the two county credits and the title question. This is research, not legal, tax or lending advice — the "
        "recorded 1998 deed of easement, SDAT's transfer-tax statement and a lender's written terms control.")

R = dict(subtitle=subtitle, verdict=verdict, tiles=tiles, parcel_prose=parcel_prose, preserved_prose=preserved_prose, ledger_intro=ledger_intro, ledger=ledger,
         levers_intro=levers_intro, levers=levers, comps_prose=comps_prose, budget_price=budget_price, budget_intro=budget_intro, budget=budget, asks=asks, sources=sources, foot=foot)
json.dump(R, open(SP / "research.json", "w"), indent=1)
print("research.json written:", len(ledger), "ledger rows,", sum(len(l["items"]) for l in levers), "lever items,", len(sources), "sources")
