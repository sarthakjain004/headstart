# Architecture Decision Records (ADRs)

Non-obvious design decisions for HeadStart — the option picked, the ones rejected, and why — so the
reasoning survives past the commit that made it.

One file per decision, numbered in the order made (`NNNN-short-title.md`). Record a new decision as
the next number; don't edit a past ADR's decision — if something changes, add a new ADR that
supersedes it and note the supersession in both.

| ADR | Decision | Date |
| --- | --- | --- |
| [0001](0001-per-ats-slug-derivation.md) | Per-ATS slug derivation via `slug_from` on the scraper | 2026-06-22 |
| [0002](0002-pooled-thread-local-http.md) | One pooled, thread-local curl_cffi HTTP session | 2026-06-22 |
| [0003](0003-fan-out-detail-fetch.md) | Concurrent detail fetch via `BaseScraper.fan_out` | 2026-06-23 |
| [0004](0004-memory-safe-parallel-resumable-scrape.md) | Memory-safe, parallel, resumable full-board scrape | 2026-06-24 |
| [0005](0005-embedding-model.md) | Embedding model: local `nomic-embed-text-v1.5` for English semantic search | 2026-06-25 |
| [0006](0006-what-we-embed.md) | What we embed: title + cleaned description; structured fields stay as filter metadata | 2026-06-28 |
| [0007](0007-search-metadata-canonical-typed.md) | Search metadata is a typed, canonical `Job`-shaped projection | 2026-06-28 |
| [0008](0008-local-lancedb-vector-store.md) | Local LanceDB for the query-time vector store (cloud later) | 2026-06-28 |
| [0009](0009-experience-extraction.md) | Years-of-experience extraction: a tiered deterministic cascade | 2026-06-29 |
| [0010](0010-feed-from-jsonl.md) | Dashboard feed built from the per-board `.jsonl`, not an in-memory copy | 2026-06-29 |
| [0011](0011-retrieval-eval-harness.md) | Retrieval-eval harness: a validated LLM judge and graded nDCG | 2026-07-01 |
| [0012](0012-liveness-ledger.md) | Liveness state as a TTL'd ledger keyed by `(ats, tenant)` | 2026-07-02 |
| [0013](0013-experience-plausibility-guards.md) | Experience plausibility guards: fix Tier 1, defer the Tier 2 anchor | 2026-07-03 |
| [0014](0014-search-index-ingestion-and-freshness.md) | Search-index ingestion: real corpus, scrape-diff eviction, incremental LanceDB | 2026-07-03 |
| [0015](0015-async-multiplexed-fan-out.md) | Async HTTP/2-multiplexed detail fan-out, opt-in per scraper | 2026-07-03 |
| [0016](0016-async-fan-out-default.md) | Async multiplexed fan-out on by default, width 100 | 2026-07-03 |
| [0017](0017-tech-role-filter.md) | Post-hoc recall-biased tech-role filter as the authoritative tech gate | 2026-07-03 |
| [0018](0018-experience-seniority-fallback.md) | Experience: widened description patterns + a data-calibrated seniority fallback | 2026-07-03 |
| [0019](0019-tech-corpus-search-index.md) | Tech-corpus search index (thin slice): embed `data/jobs/tech`, seniority-estimated experience filterable | 2026-07-03 |
| [0020](0020-free-tier-deployment.md) | Free-tier deployment: GitHub Actions ingest, private HF dataset state, HF Space serving | 2026-07-04 |
| [0021](0021-re-embed-on-content-change.md) | Re-embedding changed content: targeted eviction now, content-hash later | 2026-07-04 |
| [0022](0022-tech-priority-board-ordering.md) | Tech-priority board ordering: EWMA ledger, priority-first scrape + embed slices | 2026-07-06 |
| [0023](0023-prune-stale-and-duplicate-index-rows.md) | Prune stale/duplicate index rows: dead-Board sweep + case-variant dedup | 2026-07-17 |
| [0024](0024-india-location-gazetteer-filter.md) | India location filter: query-time gazetteer expansion (vetted alias substrings) | 2026-07-20 |
| [0025](0025-parallelize-nightly-pipeline.md) | Parallelize the nightly pipeline across GitHub Actions runners: plan → fan-out → merge | 2026-07-24 |
