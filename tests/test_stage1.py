import pytest

from farmsearch.config import ConfigError
from farmsearch.io.schema import SchemaError, resolve_schema, verify_parcels_schema
from farmsearch.stages.stage1_base_filter import run_stage1
from farmsearch.io.loaders import load_study_area


def test_counts_are_sane(result):
    s = result["summary"]["stage1"]
    # N lies outside the study area and is never loaded (bbox read); A's two rows dissolve to one account
    assert s["parcels_in_study_area"] == 18                 # incl. the church and school lots (Stage 5 fixtures)
    assert s["stage1_pass"] == 11
    assert s["per_county"]["Frederick"]["in_study_area"] == 17
    assert s["per_county"]["Carroll"]["stage1_pass"] == 1


def test_multi_row_account_dissolves(stage1):
    assert stage1.index.is_unique
    # the two rows carried per-polygon SDAT acreage (50.6 each): the row sum matches the
    # dissolved geometry, so it is used and flagged
    assert stage1.loc["FRED-A", "gross_acres"] == pytest.approx(101.2, abs=0.2)
    assert stage1.loc["FRED-A", "acreage_basis"] == "sdat"
    assert "sdat_acreage_summed_across_rows" in stage1.loc["FRED-A", "manual_flags"]
    assert "sdat_acreage_disagrees_with_geometry" not in stage1.loc["FRED-A", "manual_flags"]
    assert stage1.loc["FRED-B", "acreage_basis"] == "sdat"


def test_acreage_filter(stage1):
    assert not stage1.loc["FRED-F", "meets_acreage"]
    assert stage1.loc["FRED-F", "stage1_pass_reason"] == "below_acreage_min"
    assert not stage1.loc["FRED-S", "stage1_pass"]          # 2.4-acre strip, retained as a neighbor
    assert stage1.loc["FRED-A", "meets_acreage"]


def test_zoning_per_county_mapping(stage1):
    assert stage1.loc["FRED-G", "is_agricultural"] is False or stage1.loc["FRED-G", "is_agricultural"] == False  # noqa: E712
    assert stage1.loc["FRED-G", "stage1_pass_reason"] == "not_agricultural_zoning"
    assert stage1.loc["CARR-O", "zoning"] == "AG" and stage1.loc["CARR-O", "is_agricultural"] == True  # noqa: E712
    # split zoning: majority wins, share reported
    assert stage1.loc["FRED-L", "zoning"] == "A"
    assert stage1.loc["FRED-L", "zoning_ag_pct"] == pytest.approx(60, abs=1)
    assert stage1.loc["FRED-L", "stage1_pass"]


def test_owner_attribution(stage1):
    assert stage1.loc["FRED-A", "owner_type"] == "llc"
    assert stage1.loc["CARR-O", "owner_type"] == "trust"
    assert stage1.loc["FRED-S", "owner_type"] == "corporation"
    assert stage1.loc["FRED-B", "owner_type"] == "individual"
    assert stage1.loc["FRED-S2", "owner_name"] == "ECHO EDWARD & ECHO MARY"
    assert "500 ECHO RD" in stage1.loc["FRED-E", "owner_mailing_address"]


def test_sdat_acreage_disagreement_flagged(stage1):
    assert stage1.loc["FRED-M", "acreage_disagrees"]
    assert "sdat_acreage_disagrees_with_geometry" in stage1.loc["FRED-M", "manual_flags"]
    assert stage1.loc["FRED-M", "acreage_basis"] == "sdat"
    assert stage1.loc["FRED-M", "gross_acres"] == 80.0


def test_schema_resolution_reports_real_fields():
    spec = {"required": {"account_id": ["ACCTID"], "owner_name": ["OWNNAME1", "OWNER"]}, "optional": {"acreage_sdat": ["ACRES"]}}
    res = resolve_schema(spec, ["acctid", "OWNER", "SHAPE"])
    assert res.ok and res.mapping == {"account_id": "acctid", "owner_name": "OWNER"}
    assert res.missing_optional == ["acreage_sdat"]
    res = resolve_schema(spec, ["FOO", "BAR"])
    assert not res.ok and "REQUIRED fields NOT FOUND" in res.report() and "FOO" in res.report()


def test_verify_schema_aborts_on_missing_required(cfg):
    with pytest.raises(SchemaError):
        verify_parcels_schema(cfg, actual_fields=["ACCTID", "geometry"])


def test_unmapped_zoning_code_aborts(cfg, fixture_dir):
    import copy
    import yaml
    raw = copy.deepcopy(cfg.raw)
    m = fixture_dir / "zoning" / "frederick_incomplete.yaml"
    m.write_text(yaml.safe_dump({"code_field": "ZONING", "codes": {"A": {"is_agricultural": True}}}))
    raw["zoning"][0]["mapping"] = str(m)
    from farmsearch.config import Config
    c2 = Config.from_dict(raw, cfg.base_dir)
    with pytest.raises(ConfigError, match="R1"):
        run_stage1(c2, load_study_area(c2))
    raw["on_unmapped_zoning"] = "flag"
    c3 = Config.from_dict(raw, cfg.base_dir)
    p = run_stage1(c3, load_study_area(c3)).parcels.set_index("account_id")
    assert p.loc["FRED-G", "zoning_unmapped"] and p.loc["FRED-G", "stage1_pass"]   # retained, flagged
    assert "zoning_unmapped" in p.loc["FRED-G", "manual_flags"]
