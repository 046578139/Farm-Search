"""Command-line interface.

  farmsearch run             --config config/pipeline.yaml [--stages 1-10] [--out outputs] [--resume]
  farmsearch verify-schema   --config config/pipeline.yaml
  farmsearch zoning-domains  --config config/pipeline.yaml --county Frederick [--code-field ZONING] [--write]
  farmsearch fetch           --config config/pipeline.yaml [--only name ...]
  farmsearch fetch-parcels   --config config/pipeline.yaml [--county FRED Carroll ...] [--force]
  farmsearch build-study-area --config config/pipeline.yaml [--variant initial] [--out path]
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
    bad = sorted(x for x in out if x < 1 or x > 10)
    if bad:
        raise SystemExit(f"Stages are numbered 1-10 (requested {bad})")
    return sorted(out)


def cmd_run(args) -> int:
    from .pipeline import run_pipeline
    cfg = Config.load(args.config)
    res = run_pipeline(cfg, stages=_stages(args.stages), out_dir=args.out, resume=bool(getattr(args, "resume", False)))
    from .pipeline import render_summary
    print(render_summary(res["summary"]))
    return 0


def cmd_shortlist(args) -> int:
    """Re-rank an existing run's parcels_scored.gpkg (weights from the config) and write shortlist / owner list."""
    import geopandas as gpd
    from .deliverables import owner_list, rank_shortlist
    cfg = Config.load(args.config)
    out = Path(args.out or cfg.run.output_dir)
    scored = gpd.read_file(out / "parcels_scored.gpkg")
    short, excl = rank_shortlist(scored, cfg)
    short.to_csv(out / "shortlist.csv", index=False)
    excl.to_csv(out / "shortlist_excluded.csv", index=False)
    owners = owner_list(scored, short)
    owners.to_csv(out / "owner_list.csv", index=False)
    print(f"shortlist: {len(short)} parcels -> {out / 'shortlist.csv'}; excluded {len(excl)}; owners {len(owners)} -> {out / 'owner_list.csv'}")
    print(short[["rank", "shortlist_score", "account_id", "county", "gross_acres", "largest_contiguous_reachable_acres"]].head(15).to_string(index=False))
    return 0


def cmd_dossiers(args) -> int:
    """Render PDF dossiers for the shortlist (or the top N of it)."""
    import pandas as pd
    from .deliverables import render_dossiers
    cfg = Config.load(args.config)
    out = Path(args.out or cfg.run.output_dir)
    sl_path = out / "shortlist.csv"
    if not sl_path.exists():
        print(f"no shortlist at {sl_path}; run `farmsearch shortlist` first", file=sys.stderr)
        return 2
    short = pd.read_csv(sl_path, dtype={"account_id": str})
    if args.top:
        short = short.head(int(args.top))
    pdf = render_dossiers(cfg, out, short, pdf_path=(Path(args.pdf) if args.pdf else None))
    print(f"dossiers: {len(short)} parcels -> {pdf}")
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
    from .io.loaders import fetch_layer_to_cache, study_bbox_4326
    cfg = Config.load(args.config)
    sources = cfg.all_layer_sources()
    only = set(args.only or [])
    rc = 0
    for s in sources:
        if only and s.name not in only:
            continue
        if not s.url:
            if s.path and s.path.exists():
                print(f"skip {s.name}: local layer at {s.path}")
            else:
                print(f"skip {s.name}: no url configured and nothing at {s.path} "
                      f"(set url:, or produce it locally — parcel_row_polygons comes from `farmsearch fetch-parcels`)")
            continue
        if s.path and s.path.exists() and not args.force:
            print(f"skip {s.name}: cached at {s.path}")
            continue
        try:
            # Every layer is pulled with a margin beyond the study polygon so
            # parcels on the edge keep their roads, neighbours and constraints.
            margin = max(cfg.context_buffer_ft, s.fetch_margin_ft or 0.0)
            out = fetch_layer_to_cache(s, study_bbox_4326(cfg, margin))
            print(f"fetched {s.name} (margin {margin:.0f} ft) -> {out}")
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"FAILED {s.name}: {e}", file=sys.stderr)
            rc = 1
    print("Parcels: run `farmsearch fetch-parcels` (paginated per county from parcels.url).")
    return rc


def _study_bbox_4326(cfg: Config) -> tuple[float, float, float, float]:
    from .io.loaders import study_bbox_4326
    return study_bbox_4326(cfg, cfg.context_buffer_ft)


def cmd_fetch_parcels(args) -> int:
    import json
    import requests
    from .io.arcgis import ArcGISError
    from .io.parcels_rest import fetch_parcels, resolve_county_codes
    cfg = Config.load(args.config)
    if not cfg.parcels.url:
        print("parcels.url is not set in the config", file=sys.stderr)
        return 2
    try:
        codes = resolve_county_codes(cfg, args.county)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    try:
        summary = fetch_parcels(cfg, county_codes=codes, force=args.force, page_size=args.page_size)
    except (ArcGISError, requests.RequestException) as e:
        print(f"fetch-parcels failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    empty = [c for c, v in summary["counties"].items() if v.get("features") == 0]
    if empty:
        print(f"no features returned for {empty}", file=sys.stderr)
        return 1
    return 0


def cmd_build_study_area(args) -> int:
    import requests
    from .io.arcgis import ArcGISError
    from .io.study_area import build_study_area
    cfg = Config.load(args.config)
    try:
        out = build_study_area(cfg, args.variant, out=Path(args.out) if args.out else None)
    except (ArcGISError, requests.RequestException) as e:
        print(f"build-study-area failed: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    return 0


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
    r.add_argument("--stages", default="1-10", help="e.g. 1, 1-4, 7-8, 1-10")
    r.add_argument("--out", default=None, help="output directory (default run.output_dir)")
    r.add_argument("--resume", action="store_true",
                   help="continue from the checkpoint written after the stage before the first requested one, "
                        "e.g. `--stages 4 --resume` after a run that died in Stage 4")
    r.set_defaults(func=cmd_run)

    sl = sub.add_parser("shortlist", help="rank an existing run into shortlist.csv and owner_list.csv (weights in config)")
    sl.add_argument("--config", default="config/pipeline.yaml")
    sl.add_argument("--out", default=None)
    sl.set_defaults(func=cmd_shortlist)

    ds = sub.add_parser("dossiers", help="render per-parcel PDF dossiers for the shortlist")
    ds.add_argument("--config", default="config/pipeline.yaml")
    ds.add_argument("--out", default=None)
    ds.add_argument("--top", type=int, default=None, help="only the top N of the shortlist")
    ds.add_argument("--pdf", default=None, help="output PDF path (default outputs/dossiers.pdf)")
    ds.set_defaults(func=cmd_dossiers)

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

    fp = sub.add_parser("fetch-parcels", help="pull the parcel layer per county from parcels.url into data/raw/parcels")
    fp.add_argument("--config", default="config/pipeline.yaml")
    fp.add_argument("--county", nargs="*", help="jurisdiction codes or county names (default: every key under counties:)")
    fp.add_argument("--force", action="store_true", help="re-download counties that are already cached")
    fp.add_argument("--page-size", type=int, default=None)
    fp.set_defaults(func=cmd_fetch_parcels)

    bs = sub.add_parser("build-study-area", help="assemble the study polygon from county boundaries (study_area_build)")
    bs.add_argument("--config", default="config/pipeline.yaml")
    bs.add_argument("--variant", default="initial")
    bs.add_argument("--out", default=None, help="output GeoJSON (default: the config's study_area path for 'initial', "
                                                 "study_area_<variant>.geojson next to it otherwise)")
    bs.set_defaults(func=cmd_build_study_area)

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
