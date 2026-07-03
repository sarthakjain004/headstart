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
