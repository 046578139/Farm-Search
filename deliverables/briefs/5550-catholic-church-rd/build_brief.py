"""Build the 5550 Catholic Church Rd brief from facts.json + research.json (curated) + the map PNG."""
import json, html, sys
from pathlib import Path
SP = Path(__file__).parent
F = json.load(open(SP / "facts.json"))
R = json.load(open(SP / "research.json"))
MAP = open(SP / "map_b64.txt").read().strip()
e = html.escape
def money(x):
    return f"${x:,.0f}" if isinstance(x, (int, float)) and x == x else "—"

STATUS = {  # stamp classes for the ledger
    "cashed": ("st-cashed", "Cashed in 1998"), "no": ("st-no", "Not eligible"), "yes": ("st-yes", "Still available"),
    "partly": ("st-partly", "Partly"), "tax": ("st-tax", "Tax only"), "unknown": ("st-unknown", "Unverified"), "none": ("st-no", "Pays nothing"),
}
def stamp(s):
    c, t = STATUS.get(s, STATUS["unknown"]); return f'<span class="stamp {c}">{e(t)}</span>'

def src_links(urls):
    out = []
    for u in urls or []:
        host = u.split("/")[2] if "//" in u else u
        out.append(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(host)}</a>')
    return " · ".join(out)

# ---------- sections ----------
lc = F["land_cover"]["lulc_2010"]; tot = sum(lc.values())
bar = "".join(f'<span class="seg {k}" style="width:{v/tot*100:.1f}%" title="{k} {v} ac"></span>' for k, v in
              (("crop", lc["cropland_21"]), ("pasture", lc["pasture_22"]), ("forest", lc["forest_41"]), ("res", lc["low_density_residential_11"])))
facts_rows = [
    ("Listing", f'<a href="{e(F["listing"]["url_redfin"])}" target="_blank" rel="noopener">Bright MLS {e(F["listing"]["mls"])}</a> · active since {F["listing"]["listed"]} at {money(F["listing"]["price"])} · {e(F["listing"]["agent"])} · open house {e(F["listing"]["open_house"])}'),
    ("SDAT account", f'<a href="{e(F["sdat"]["url"])}" target="_blank" rel="noopener">District {F["sdat"]["district"]}, account {F["sdat"]["account"]}</a> · tax map {F["sdat"]["map"]} parcel {F["sdat"]["parcel"]}'),
    ("Acreage", f'{F["acres"]["sdat"]} ac on the assessment, {F["acres"]["geometry"]} ac by the parcel polygon'),
    ("Zoning / land use", e(F["zoning"]) + " · " + e(F["land_use"])),
    ("Assessment (2026)", f'land {money(F["assessment"]["land"])} · improvements {money(F["assessment"]["improvements"])} · total {money(F["assessment"]["total"])}'),
    ("House", e(F["structures"]["house"])),
    ("Barns", e(F["structures"]["barns"])),
    ("Last recorded transfer", f'{F["transfer"]["date"]} for {money(F["transfer"]["consideration"])} · deed {e(F["transfer"]["deed"])}'),
    ("Owner of record", e(F["owner"])),
    ("Water, sewer, watershed", e(F["cama"]["water_sewer"]) + " · " + e(F["cama"]["watershed"])),
    ("Streams", e(F["streams"])),
    ("Historic", e(F["historic"])),
    ("Road frontage", f'{F["frontage"]["facing_ft"]:,.0f} ft on Catholic Church Rd (county); {F["frontage"]["open_ft"]:,.0f} ft open, {F["frontage"]["encumbered_ft"]:,.0f} ft in floodplain or steep, {F["frontage"]["foreign_ft"]:,.0f} ft behind a neighbour\'s lot'),
    ("Water and slopes", f'{F["physical"]["floodplain_ae"]} ac FEMA AE floodplain · {F["physical"]["riparian_presumed"]} ac presumed stream buffer · {F["physical"]["wetland_nwi"]} ac NWI wetland · {F["physical"]["steep_slope"]} ac over the slope limit'),
    ("Growth pressure", f'no residential zoning adjoining, no planned sewer, outside the Priority Funding Area, 0 approved-unbuilt units within 2 mi; {F["encroachment"]["adjacent_eased_pct"]}% of the boundary ({F["encroachment"]["adjacent_eased_acres"]:.0f} ac) is permanently eased'),
    ("Power lines", f'MPRP tier {F["mprp"]["tier"]} (clear) · nearest studied MPRP route {F["mprp"]["nearest_route_ft"]/5280:.1f} mi · nearest HV line {F["mprp"]["hv_line_ft"]/5280:.1f} mi'),
    ("Commute (peak, modelled)", f'BWI {F["commute"]["bwi_peak"]} min · Langley {F["commute"]["langley_peak"]} min · N. Virginia {F["commute"]["nova_peak"]} min — free-flow × peak factor, not a traffic measurement'),
]
facts_html = "".join(f"<tr><th>{e(k)}</th><td>{v}</td></tr>" for k, v in facts_rows)

comps_rows = "".join(
    f'<tr><td>{e(c["addr"])}<br><span class="mono muted small">{e(c["acct"])}{(" · " + e(c["note"])) if c.get("note") else ""}</span></td>'
    f'<td class="mono">{e(str(c["date"]))}</td>'
    f'<td class="mono num">{(money(c["list"]) + " → ") if c.get("list") else ""}<strong>{money(c["price"])}</strong>' + (f'<br><span class="muted small">{e(str(c["dom"]))}{" days" if isinstance(c["dom"], (int, float)) else ""}</span>' if c.get("dom") else "") + '</td>'
    f'<td class="mono num">{c["acres"]:.1f}</td><td>{e(c["house"])}</td><td class="mono num">{money(c["land_per_acre"]) if c.get("land_per_acre") else "—"}</td><td>{e(c["eased"])}</td><td class="mono num">{c["miles"]}</td></tr>'
    for c in F["comps"])

ledger_rows = "".join(
    f'<tr><td class="prog"><strong>{e(p["program"])}</strong><div class="muted small">{e(p["runs"])}</div></td>'
    f'<td>{stamp(p["status"])}</td><td>{e(p["pays"])}</td><td>{p["why"]}</td></tr>' for p in R["ledger"])

def lever_block(l):
    items = "".join(
        f'<li><div class="li-head"><strong>{e(i["name"])}</strong>{"".join(" " + stamp(s) for s in i.get("stamps", []))}</div>'
        f'<div>{i["text"]}</div>' + (f'<div class="dollars mono">{e(i["dollars"])}</div>' if i.get("dollars") else "")
        + (f'<div class="src small">{src_links(i.get("sources"))}</div>' if i.get("sources") else "") + "</li>" for i in l["items"])
    return f'<section class="lever" id="{e(l["id"])}"><h3>{e(l["title"])}</h3><p class="lead-in">{l["lead"]}</p><ul class="items">{items}</ul></section>'
levers_html = "".join(lever_block(l) for l in R["levers"])

asks_html = "".join(f"<li>{a}</li>" for a in R["asks"])
sources_html = "".join(f'<li><a href="{e(u)}" target="_blank" rel="noopener">{e(t)}</a></li>' for t, u in R["sources"])
budget_rows = "".join(f'<tr><td>{r[0]}</td><td class="mono num">{r[1]}</td><td>{r[2]}</td></tr>' for r in R["budget"])

page = f"""<title>Catholic Church Road Farm</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--ground:#f3f5f1;--card:#ffffff;--ink:#1b241d;--muted:#5b665e;--line:#d8ddd6;--green:#2f6b3a;--green-soft:#e2eee4;--blue:#3f6b8c;--blue-soft:#e3ecf3;--ochre:#a8741c;--ochre-soft:#f4ecd9;--red:#a63d32;--red-soft:#f5e3e0;--grey-soft:#e9ece8;--serif:"IBM Plex Serif",Georgia,"Times New Roman",serif;--sans:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif;--mono:"IBM Plex Mono",Menlo,Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--ground:#141a16;--card:#1c231e;--ink:#e6ebe4;--muted:#9aa79c;--line:#2c352e;--green:#7cc98b;--green-soft:#22352a;--blue:#8fb6d3;--blue-soft:#1f2d3a;--ochre:#dcb04f;--ochre-soft:#3a2f16;--red:#e5867a;--red-soft:#3d2320;--grey-soft:#262e29}}}}
:root[data-theme="dark"]{{--ground:#141a16;--card:#1c231e;--ink:#e6ebe4;--muted:#9aa79c;--line:#2c352e;--green:#7cc98b;--green-soft:#22352a;--blue:#8fb6d3;--blue-soft:#1f2d3a;--ochre:#dcb04f;--ochre-soft:#3a2f16;--red:#e5867a;--red-soft:#3d2320;--grey-soft:#262e29}}
body{{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.55;padding-inline:16px;padding-block:0 64px}}
a{{color:var(--blue)}}
.top{{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--ground);border-bottom:1px solid var(--line);margin-inline:-16px;padding:10px 16px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}}
.top .name{{font-family:var(--serif);font-weight:600;font-size:1.05rem}}
.top .id{{font-family:var(--mono);color:var(--muted);font-size:.8rem}}
.wrap{{max-width:1040px;margin:0 auto}}
.prose{{max-width:72ch}}
h1{{font-family:var(--serif);font-weight:600;font-size:clamp(1.7rem,3.6vw,2.4rem);line-height:1.15;margin:36px 0 8px;text-wrap:balance}}
h2{{font-family:var(--serif);font-weight:600;font-size:1.45rem;margin:48px 0 12px;text-wrap:balance}}
h3{{font-family:var(--serif);font-weight:600;font-size:1.15rem;margin:28px 0 8px}}
.eyebrow{{font-size:.72rem;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:40px 0 -6px}}
.sub{{color:var(--muted);font-size:1.02rem;max-width:72ch}}
.verdict{{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--green);padding:18px 22px;margin:22px 0;max-width:80ch}}
.verdict p{{margin:.4em 0}}
.tiles{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}}
@media (max-width:760px){{.tiles{{grid-template-columns:repeat(2,1fr)}}}}
@media (max-width:440px){{.tiles{{grid-template-columns:1fr}}}}
.tile{{background:var(--card);border:1px solid var(--line);padding:12px 14px}}
.tile .k{{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
.tile .v{{font-family:var(--mono);font-size:1.35rem;font-weight:500;margin-top:2px;font-variant-numeric:tabular-nums}}
.tile .n{{font-size:.85rem;color:var(--muted)}}
table{{border-collapse:collapse;width:100%;font-size:.93rem}}
th,td{{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}}
th{{font-weight:600;white-space:nowrap;color:var(--muted);font-size:.85rem}}
.facts th{{width:200px}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.mono{{font-family:var(--mono);font-size:.88rem}}
.muted{{color:var(--muted)}} .small{{font-size:.82rem}}
.scroll{{overflow-x:auto;margin:10px 0}}
figure{{margin:18px 0}} figure img{{max-width:100%;border:1px solid var(--line);display:block}} figcaption{{font-size:.85rem;color:var(--muted);margin-top:6px;max-width:80ch}}
.bar{{display:flex;height:22px;border:1px solid var(--line);margin:10px 0 6px;max-width:100%}}
.seg{{display:block;height:100%}} .seg.crop{{background:#d9b95c}} .seg.pasture{{background:#a9c46a}} .seg.forest{{background:#3f7a4a}} .seg.res{{background:#9b9b9b}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}} .legend i{{display:inline-block;width:11px;height:11px;margin-right:5px;vertical-align:-1px}}
.stamp{{display:inline-block;font-family:var(--mono);font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;padding:2px 7px;border:1px solid;white-space:nowrap;margin:0 2px 2px 0}}
.st-cashed{{color:var(--ochre);border-color:var(--ochre);background:var(--ochre-soft)}}
.st-no{{color:var(--red);border-color:var(--red);background:var(--red-soft)}}
.st-yes{{color:var(--green);border-color:var(--green);background:var(--green-soft)}}
.st-partly{{color:var(--blue);border-color:var(--blue);background:var(--blue-soft)}}
.st-tax{{color:var(--blue);border-color:var(--blue);background:var(--blue-soft)}}
.st-unknown{{color:var(--muted);border-color:var(--muted);background:var(--grey-soft)}}
.ledger td.prog{{min-width:200px}} .ledger td:nth-child(3){{min-width:220px}} .ledger td:nth-child(4){{min-width:320px}}
.lever{{background:var(--card);border:1px solid var(--line);padding:6px 22px 16px;margin:18px 0}}
.lever h3{{margin-top:14px}} .lead-in{{max-width:72ch;margin:0 0 10px}}
.items{{list-style:none;padding:0;margin:0;display:grid;gap:14px}}
.items li{{border-top:1px solid var(--line);padding-top:10px;max-width:80ch}}
.li-head{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px}}
.dollars{{margin-top:6px;padding:6px 10px;background:var(--ground);border-left:3px solid var(--green);font-size:.86rem;white-space:pre-line}}
.src{{margin-top:6px}}
.asks li{{margin:6px 0;max-width:80ch}}
.budget td:first-child{{min-width:220px}}
.foot{{margin-top:48px;padding-top:14px;border-top:1px solid var(--line);font-size:.85rem;color:var(--muted);max-width:80ch}}
.sources{{font-size:.85rem;columns:2;column-gap:28px}} .sources li{{break-inside:avoid;margin:3px 0}}
@media (max-width:640px){{.sources{{columns:1}} .facts th{{width:auto}}}}
</style>
<div class="top"><span class="name">Catholic Church Road Farm</span><span class="id">5550 Catholic Church Rd · Jefferson MD 21755 · SDAT 22-432257</span></div>
<div class="wrap">
<h1>What this farm is, what is already preserved, and how to buy it for the least money</h1>
<p class="sub">{R["subtitle"]}</p>

<div class="verdict">{R["verdict"]}</div>
<div class="tiles">{"".join(f'<div class="tile"><div class="k">{e(t[0])}</div><div class="v">{e(t[1])}</div><div class="n">{e(t[2])}</div></div>' for t in R["tiles"])}</div>

<div class="eyebrow">1 · The parcel</div>
<h2>81 acres in two blocks, split by a stream</h2>
<div class="prose">{R["parcel_prose"]}</div>
<figure><img src="data:image/png;base64,{MAP}" alt="Map of the parcel with the MET easement hatch, floodplain, stream, usable area, envelope and frontage classes"><figcaption>Parcel polygon (MDP 2024), MET and Rural Legacy easement layers (DNR), FEMA AE floodplain, NWI wetlands, county road centerlines, building footprints, and the screening pipeline's usable area, dischargeable envelope and frontage classes, on the Chesapeake Conservancy 1 m land cover.</figcaption></figure>
<div class="bar">{bar}</div>
<div class="legend"><span><i style="background:#d9b95c"></i>cropland {lc["cropland_21"]} ac</span><span><i style="background:#a9c46a"></i>pasture {lc["pasture_22"]} ac</span><span><i style="background:#3f7a4a"></i>forest {lc["forest_41"]} ac</span><span><i style="background:#9b9b9b"></i>house site {lc["low_density_residential_11"]} ac</span><span class="muted">MDP land use 2010; 2018 canopy raster agrees: {F["land_cover"]["canopy_2018_30m"]["acres_ge_50pct"]} ac at ≥ 50% canopy</span></div>
<div class="scroll"><table class="facts">{facts_html}</table></div>

<div class="eyebrow">2 · Already preserved</div>
<h2>A perpetual MET easement covers 98% of it</h2>
<div class="prose">{R["preserved_prose"]}</div>

<div class="eyebrow">3 · Money on the table</div>
<h2>Every program, and whether it can still pay you</h2>
<p class="sub">{R["ledger_intro"]}</p>
<div class="scroll"><table class="ledger"><thead><tr><th>Program</th><th>Status here</th><th>What it pays</th><th>Why</th></tr></thead><tbody>{ledger_rows}</tbody></table></div>

<div class="eyebrow">4 · Making it affordable</div>
<h2>The levers, in the order they matter</h2>
<p class="sub">{R["levers_intro"]}</p>
{levers_html}

<div class="eyebrow">5 · Price check</div>
<h2>What eased farms near it actually sold for</h2>
<div class="prose">{R["comps_prose"]}</div>
<div class="scroll"><table><thead><tr><th>Sale</th><th>Closed</th><th>List → sold</th><th>Acres</th><th>House and land</th><th>Land $/ac</th><th>Easement</th><th>mi</th></tr></thead><tbody>{comps_rows}</tbody></table></div>

<div class="eyebrow">6 · A worked budget</div>
<h2>Where the money goes on a {R["budget_price"]} purchase</h2>
<div class="prose">{R["budget_intro"]}</div>
<div class="scroll"><table class="budget"><thead><tr><th>Line</th><th>Amount</th><th>Basis</th></tr></thead><tbody>{budget_rows}</tbody></table></div>

<div class="eyebrow">7 · Before you sign</div>
<h2>Ask for these, in this order</h2>
<ol class="asks">{asks_html}</ol>

<div class="eyebrow">Sources</div>
<ul class="sources">{sources_html}</ul>
<div class="foot">{R["foot"]}</div>
</div>
"""
out = SP / "catholic_church_road_farm.html"
out.write_text(page)
print("wrote", out, f"{out.stat().st_size/1024:.0f} KB")
