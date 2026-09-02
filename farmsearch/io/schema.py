"""Runtime schema verification.

The spec is explicit: inspect the layer's field list directly, do not assume
names. This module resolves the canonical field names the pipeline uses to the
actual field names in the data, and refuses to proceed if a required field
cannot be found — printing the real field list so the mapping can be fixed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import yaml

from ..config import Config, ZoningSpec
from .loaders import read_layer_fields


class SchemaError(ValueError):
    pass


@dataclass
class SchemaResolution:
    mapping: dict[str, str] = field(default_factory=dict)        # canonical -> actual
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    actual_fields: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_required

    def report(self) -> str:
        lines = ["Parcel schema resolution:"]
        for k, v in self.mapping.items():
            lines.append(f"  {k:<28} <- {v}")
        if self.missing_optional:
            lines.append("  optional fields not found (features degrade gracefully):")
            for k in self.missing_optional:
                lines.append(f"    - {k}")
        if self.missing_required:
            lines.append("  REQUIRED fields NOT FOUND:")
            for k in self.missing_required:
                lines.append(f"    - {k}")
            lines.append("  Actual fields in the data:")
            for f in self.actual_fields:
                lines.append(f"    {f}")
            lines.append("  Edit config/schema/parcels.yaml so each required canonical field lists a real field name.")
        return "\n".join(lines)


def load_schema_spec(path: Path) -> dict:
    spec = yaml.safe_load(Path(path).read_text()) or {}
    for k in ("required", "optional"):
        spec.setdefault(k, {})
        for canon, cands in list(spec[k].items()):
            if isinstance(cands, str):
                spec[k][canon] = [cands]
    return spec


def resolve_schema(spec: dict, actual_fields: Iterable[str]) -> SchemaResolution:
    actual = list(actual_fields)
    upper = {f.upper(): f for f in actual}
    res = SchemaResolution(actual_fields=actual)
    for group, missing_list in (("required", res.missing_required), ("optional", res.missing_optional)):
        for canon, cands in spec.get(group, {}).items():
            hit = None
            for c in cands:
                if c in actual:
                    hit = c
                    break
                if c.upper() in upper:
                    hit = upper[c.upper()]
                    break
            if hit is None:
                missing_list.append(canon)
            else:
                res.mapping[canon] = hit
    return res


def verify_parcels_schema(cfg: Config, actual_fields: Optional[Iterable[str]] = None) -> SchemaResolution:
    """Resolve the parcel field map against the real data. Raises SchemaError
    when a required field is missing."""
    spec = load_schema_spec(cfg.parcels.schema_path)
    fields = list(actual_fields) if actual_fields is not None else read_layer_fields(cfg.parcels.source)
    res = resolve_schema(spec, fields)
    if not res.ok:
        raise SchemaError(res.report())
    return res


def apply_schema(gdf: gpd.GeoDataFrame, res: SchemaResolution) -> gpd.GeoDataFrame:
    """Rename resolved source fields to canonical names (originals are dropped
    to avoid duplicate columns; unmapped source columns are kept)."""
    rename = {actual: canon for canon, actual in res.mapping.items() if actual != canon}
    out = gdf.rename(columns=rename)
    # Guarantee optional canonical columns exist so downstream code is uniform
    for canon in res.missing_optional:
        if canon not in out.columns:
            out[canon] = None
    return out


# ----------------------------------------------------------------------------
def zoning_domain_template(spec: ZoningSpec, gdf: Optional[gpd.GeoDataFrame] = None,
                           code_field: Optional[str] = None,
                           domain: Optional[dict[str, str]] = None) -> str:
    """Build the YAML text of a zoning mapping with every observed code and
    is_agricultural: null. Pulls codes from a coded-value domain when the
    service publishes one, otherwise from the distinct values in the layer."""
    code_field = code_field or spec.code_field
    if code_field is None:
        raise SchemaError(f"zoning {spec.county}: code_field is not set; fields available: "
                          f"{list(gdf.columns) if gdf is not None else 'unknown'}")
    codes: dict[str, dict] = {}
    if domain:
        for code, desc in domain.items():
            codes[str(code)] = {"description": desc, "is_agricultural": None}
    if gdf is not None:
        if code_field not in gdf.columns:
            raise SchemaError(f"zoning {spec.county}: field {code_field!r} not in layer; fields: {list(gdf.columns)}")
        for v in sorted({str(x) for x in gdf[code_field].dropna().unique()}):
            codes.setdefault(v, {"description": None, "is_agricultural": None})
    # Preserve any answers already given in the existing mapping file
    for code, v in spec.codes.items():
        if code in codes and v.get("is_agricultural") is not None:
            codes[code]["is_agricultural"] = v["is_agricultural"]
            codes[code]["description"] = codes[code]["description"] or v.get("description")
    doc = {"code_field": code_field, "codes": codes}
    header = (f"# {spec.county} County zoning codes -> is_agricultural.\n"
              f"# Generated from the live layer; fill in every null. Stage 1 aborts on unmapped codes.\n")
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
