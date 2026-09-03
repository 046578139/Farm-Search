# Deliverables — run of 2026-09-03

The pipeline writes everything to `outputs/`, which is not versioned (it holds
multi-gigabyte checkpoints and geopackages). These are the small, human-readable
results of the run described in `docs/HANDOFF.md`, kept in the repository so the
answers outlive the machine that produced them.

| file | what it is |
|---|---|
| `shortlist.csv` | the ranked 40, with every score component that produced the rank |
| `shortlist_excluded.csv` | the 1,273 parcels a hard rule removed, each with its reason |
| `owner_list.csv` | one row per owner mailbox: parcels held, total acres, best rank |
| `valuation_comps.csv` | the 102 arms-length agricultural sales behind the price bands |
| `summary.json`, `summary.md` | per-stage and per-county counts, missing layers, and the list of questions the pipeline cannot answer |

Not kept here, because of their size: `parcels_scored.csv` (8 MB, all 2,576
parcels with the full record), the matching `.gpkg` / `.geojson`, the per-stage
geopackages, and `dossiers.pdf`. Reproduce them with:

```bash
farmsearch run --config config/pipeline.yaml --stages 1-10
farmsearch dossiers --config config/pipeline.yaml --top 20 --png-dir outputs/maps
```
