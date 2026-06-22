# data/

Pipeline data, organized by the stage that produces it (mirrors `scripts/`).

| folder | stage | what's in it |
|---|---|---|
| `discover/` | discover | raw tenant discovery — the Common Crawl miner's output (`india_ats_tenants.csv`), its index cache (`cc_index_cache.txt`) and resume checkpoint (`cc_miner_checkpoint.txt`). |
| `wayback-ats/` | discover | tenant lists harvested from the Wayback CDX API (one CSV per ATS); `active/` is the liveness-filtered subset. See its README. |
| `ats-companies/` | discover (source pool) | the global "company board" universe (greenhouse, lever, ashby, workday), copied from jobhive. Input to the merge step. See its README. |
| `ats-tenants-merged/` | merge | Common Crawl ∪ Wayback, deduped, one CSV per ATS; `active/` is the live subset. Built by `scripts/merge/merge_tenants.py`. See its README. |
| `resolve/` | resolve | company → `ats:slug` resolution — `fingerprint_results.csv`, `verify_results.csv`, `investigated.csv`, `unfound_companies.csv`, `recovered_unfound.csv`, and the final `coverage.csv`. |
| `jobs/` | scrape | the scraped job feeds. `wellfound.csv` is the main active jobs output. |
| `scratch/` | — | regenerable run logs and superseded intermediates. Git-ignored; safe to delete. |

The live product reads the curated subset in `config/companies.toml`, not these files directly — these are the discovery/resolution pool used to grow that subset.
