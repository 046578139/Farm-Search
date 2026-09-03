"""Shortlist, owner list and dossiers on the synthetic fixture."""
import pandas as pd
import pytest

from farmsearch.pipeline import run_pipeline


@pytest.fixture(scope="module")
def full(cfg, fixture_dir):
    out = fixture_dir / "outputs_full"
    res = run_pipeline(cfg, stages=tuple(range(1, 11)), out_dir=out, write=True)
    return res, out


def test_shortlist_ranks_and_excludes(full):
    res, out = full
    from farmsearch.deliverables import rank_shortlist
    from farmsearch.config import Config
    short, excl = rank_shortlist(res["scored"], Config.load(out.parent / "pipeline.yaml"))
    assert len(short) == 5 and list(short["rank"]) == [1, 2, 3, 4, 5]
    assert short["shortlist_score"].is_monotonic_decreasing
    # I intersects the MPRP preferred route -> excluded by the hard rule; C (usable unreachable) and D/E/H (landlocked, reach 0) below acreage
    assert "FRED-I" in set(excl["account_id"]) and "mprp_tier_1" in excl.set_index("account_id").loc["FRED-I", "exclusion_reason"]
    assert "FRED-C" in set(excl["account_id"])
    assert "FRED-A" in set(short["account_id"])                   # the clean corner lot makes the list
    assert (out / "shortlist.csv").exists() and (out / "shortlist_excluded.csv").exists()
    csv = pd.read_csv(out / "shortlist.csv")
    assert len(csv) == 5 and "score_largest_contiguous_reachable_acres" in csv.columns


def test_owner_list_collapses_by_mailbox(full):
    res, out = full
    from farmsearch.deliverables import owner_list
    ol = pd.read_csv(out / "owner_list.csv")
    scored = res["scored"]
    assert len(ol) < len(scored) or len(ol) == len(scored)
    # E and its strip S2 share ECHO EDWARD / 500 ECHO RD, but S2 is not scored; every scored parcel has a distinct owner here
    assert ol["parcel_count"].sum() == len(scored)
    assert ol.iloc[0]["on_shortlist"] == True  # noqa: E712  shortlisted owners come first
    assert set(ol.columns) >= {"owner_key", "owner_name", "owner_mailing_address", "parcel_count", "account_ids", "gross_acres_total", "best_shortlist_rank", "sdat_urls"}


def test_dossiers_render_a_pdf(full, cfg):
    res, out = full
    from farmsearch.deliverables import render_dossiers
    short = pd.read_csv(out / "shortlist.csv", dtype={"account_id": str}).head(2)
    pdf = render_dossiers(cfg, out, short)
    assert pdf.exists() and pdf.stat().st_size > 20000
    import re
    data = pdf.read_bytes()
    assert len(re.findall(rb"/Type\s*/Page\b(?!s)", data)) == 4         # two pages per parcel
