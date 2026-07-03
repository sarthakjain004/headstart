# data/

Pipeline data, organized by the stage that produces it (mirrors `scripts/`). Most of it is large and
regenerable, so it's git-ignored; only a few small, hard-won artifacts are committed (marked below).

| folder | stage | what's in it | in git? |
|---|---|---|---|
| `discover/` | discover | raw tenant discovery — the Common Crawl miner's harvest per ATS (`cc_ats_tenants.csv`) plus its index/resume caches. | ignored |
| `ats-tenants-merged/` | merge | the merged candidate pool: Common Crawl ∪ Wayback ∪ harvests, deduped, one `{ats}.csv` per ATS (`ats,tenant,url,source`). See its README. | `*.csv` ignored; **`active/` committed** |
| `resolve/` | resolve | company → `ats:slug` resolution artifacts (`fingerprint_results.csv`, `verify_results.csv`, `coverage.csv`, …). | ignored |
| `validate/` | validate | the **liveness ledger** (ADR-0012): `liveness/{ats}.csv` = `ats,tenant,url,status,jobs,checked_at`, the source of truth for Live/Dead/Unknown. The Active list is just its `status==live` rows; written by `scripts/validate/check_liveness.py`. | **committed** |
| `jobs/` | scrape → filter | scraped jobs. `{ats}.jsonl` is the full per-ATS scrape (the source of truth); **`tech/{ats}.jsonl`** is the software/tech subset (ADR-0017) that the feed, embedding, and UI actually read. `wellfound.csv` is the one-off Wellfound corpus (the frozen eval benchmark); `logs/` holds run logs. | ignored |
| `enrich/` | enrich | years-of-experience extraction output, `wellfound_experience.jsonl` (ADR-0009). | ignored |
| `embeddings/` | embed | the vector store: `{source}/embeddings.f32` + `meta.jsonl` + `manifest.json` (ADR-0005). | ignored |
| `lancedb/` | embed | the local LanceDB table for query-time vector search (ADR-0008). | ignored |
| `eval/` | eval | the retrieval-eval harness data — `qrels.jsonl`, `pool.jsonl`, `judge_labels.jsonl`, `human_labels.jsonl` (ADR-0011). See its README. | committed (except `pool.jsonl`) |
| `scratch/` | — | regenerable run logs, recon captures, and superseded intermediates. Safe to delete. | ignored |

## Flow

`discover` finds tenants → `merge` unions them into the pool → `validate` probes each board and
records a verdict in the liveness ledger → `scrape` reads the live boards into `jobs/{ats}.jsonl` →
`filter` keeps the tech subset in `jobs/tech/` → `embed` turns that into vectors in `embeddings/`
and `lancedb/` for semantic search.

Only `data/validate/liveness/` and `data/ats-tenants-merged/active/` (and the eval labels) are
committed — everything else regenerates from the scripts. The served product reads the liveness
ledger's live view and the **tech subset**, not the full scrape.
