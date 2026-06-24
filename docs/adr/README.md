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
