"""Command-line interface.

  farmsearch run             --config config/pipeline.yaml [--stages 1-4] [--out outputs]
  farmsearch verify-schema   --config config/pipeline.yaml
  farmsearch zoning-domains  --config config/pipeline.yaml --county Frederick [--code-field ZONING] [--write]
  farmsearch fetch           --config config/pipeline.yaml [--only name ...]
  farmsearch make-fixture    --out data/fixture
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config, ConfigError


def _stages(spec: str) -> list[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    bad = sorted(x for x in out if x < 1 or x > 4)
    if bad:
        raise SystemExit(f"only Stages 1-4 are implemented (requested {bad})")
    return sorted(out)


def cmd_run(args) -> int:
    from .pipeline import run_pipeline
    cfg = Config.load(args.config)
    res = run_pipeline(cfg, stages=_stages(args.stages), out_dir=args.out)
    from .pipeline import render_summary
    print(render_summary(res["summary"]))
    return 0


def cmd_verify_schema(args) -> int:
    from .io.schema import SchemaError, verify_parcels_schema
    cfg = Config.load(args.config)
    try:
        res = verify_parcels_schema(cfg)
    except SchemaError as e:
        print(e)
        return 2
    print(res.report())
    return 0


def cmd_zoning_domains(args) -> int:
    from .io.loaders import load_study_area, read_layer
    from .io.schema import zoning_domain_template
    cfg = Config.load(args.config)
    spec = cfg.zoning_for(args.county)
    if spec is None:
        print(f"no zoning entry for county {args.county!r} in config", file=sys.stderr)
        return 2
    if spec.mapping_path.exists():
        spec.load_mapping()
    domain = None
    gdf = None
    if spec.source.url and not args.local:
        from .io.arcgis import ArcGISLayer
        info = ArcGISLayer(spec.source.url).info()
        print(f"layer {info.name}: fields = {info.field_names()}")
        code_field = args.code_field or spec.code_field
        if code_field and code_field in info.domains():
            domain = info.domains()[code_field]
        elif code_field:
            vals = ArcGISLayer(spec.source.url).distinct_values(code_field)
            domain = {str(v): "" for v in vals}
    else:
        gdf = read_layer(spec.source, cfg.working_crs, load_study_area(cfg))
        print(f"layer fields = {list(gdf.columns)}")
    text = zoning_domain_template(spec, gdf=gdf, code_field=args.code_field, domain=domain)
    if args.write:
        spec.mapping_path.write_text(text)
        print(f"wrote {spec.mapping_path}")
    else:
        print(text)
    return 0


def cmd_fetch(args) -> int:
    import geopandas as gpd
    from .io.loaders import fetch_layer_to_cache
    cfg = Config.load(args.config)
    sa = gpd.read_file(str(cfg.study_area_path))
    if sa.crs is None:
        sa = sa.set_crs("EPSG:4326")
    bbox = tuple(float(x) for x in sa.to_crs("EPSG:4326").total_bounds)
    sources = []
    for z in cfg.zoning:
        sources.append(z.source)
    for c in cfg.constraints:
        if c.source is not None:
            sources.append(c.source)
        if c.derive_from_lines is not None:
            sources.append(c.derive_from_lines.source)
    for r in cfg.access.row_layers:
        sources.append(r.source)
    only = set(args.only or [])
    rc = 0
    for s in sources:
        if only and s.name not in only:
            continue
        if not s.url:
            print(f"skip {s.name}: no url configured (VERIFY and set it in the config)")
            continue
        if s.path and s.path.exists() and not args.force:
            print(f"skip {s.name}: cached at {s.path}")
            continue
        try:
            out = fetch_layer_to_cache(s, bbox)
            print(f"fetched {s.name} -> {out}")
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"FAILED {s.name}: {e}", file=sys.stderr)
            rc = 1
    print("Parcels are a manual bulk download (license acceptance required): see README.")
    return rc


def cmd_make_fixture(args) -> int:
    from .fixtures.synthetic import build_fixture
    cfg_path = build_fixture(Path(args.out))
    print(f"fixture written; run:  farmsearch run --config {cfg_path}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="farmsearch", description="Farmland acquisition screening pipeline (Stages 1-4)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the pipeline")
    r.add_argument("--config", default="config/pipeline.yaml")
    r.add_argument("--stages", default="1-4", help="e.g. 1, 1-2, 1-4")
    r.add_argument("--out", default=None, help="output directory (default run.output_dir)")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify-schema", help="resolve the parcel field map against the real data")
    v.add_argument("--config", default="config/pipeline.yaml")
    v.set_defaults(func=cmd_verify_schema)

    z = sub.add_parser("zoning-domains", help="pull zoning district codes from a county layer into a mapping template")
    z.add_argument("--config", default="config/pipeline.yaml")
    z.add_argument("--county", required=True)
    z.add_argument("--code-field", default=None)
    z.add_argument("--local", action="store_true", help="read the cached local file instead of the REST service")
    z.add_argument("--write", action="store_true", help="write the template over config/zoning/<county>.yaml")
    z.set_defaults(func=cmd_zoning_domains)

    f = sub.add_parser("fetch", help="download configured REST layers (clipped to the study-area bbox) into data/raw")
    f.add_argument("--config", default="config/pipeline.yaml")
    f.add_argument("--only", nargs="*")
    f.add_argument("--force", action="store_true")
    f.set_defaults(func=cmd_fetch)

    m = sub.add_parser("make-fixture", help="write the synthetic validation dataset")
    m.add_argument("--out", default="data/fixture")
    m.set_defaults(func=cmd_make_fixture)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    from .io.loaders import LayerNotAvailable
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except LayerNotAvailable as e:
        print(f"data not available: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
