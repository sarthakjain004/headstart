# ADR-0021: Re-embedding changed content — targeted eviction now, content-hash later

- Status: Accepted
- Date: 2026-07-04
- Builds on [ADR-0014](0014-search-index-ingestion-and-freshness.md) (scrape-diff eviction) and
  [ADR-0019](0019-tech-corpus-search-index.md) (the id-keyed store + `--resume` semantics)

## Context

The embedding store is id-keyed: `embed_run.py --resume` skips every id already present, so a
vector is never recomputed when its Job's *text* changes. ADR-0014's eviction only covers ids that
*disappear* from a scraped Board — a Job that persists with different text keeps its stale vector
indefinitely. Two things change text under a stable id: (a) scraper fixes that alter what gets
captured (this session: lever lists, recruitee requirements, smartrecruiters qualifications, zoho
and ripplehire detail passes — ~30k affected vectors), and (b) organic drift, a company editing a
posted JD in place.

## Decision

**Now: per-incident targeted eviction.** `scripts/embed/evict_store.py --ats <list>` drops the
affected ATSes' rows from the store (lockstep rewrite of `embeddings.f32` + `meta.jsonl`, manifest
count last) and from the LanceDB `jobs` table; the next `embed_run --resume && index sync`
re-embeds and re-adds them fresh. Running it is part of shipping any scraper change that alters
captured text.

**Later: content-hash change detection**, once the nightly pipeline has run long enough to measure
organic churn. Design sketch, recorded so it isn't re-derived: hash the embedded text into each
meta row; `--resume` re-embeds on hash mismatch (append-only store, last-row-wins per id, periodic
store compaction drops superseded rows); `index sync` gains an update branch (changed vector =
delete + re-add). The trigger to build it is knowing the nightly churn volume — the free-tier CPU
embed budget is sized for *new* ids, and organic-edit churn lands on top of that.

## Rejected alternatives

- **Content-hash now** — right end-state, but sizing it blind risks the nightly budget: every
  edited JD re-embeds every night, and we have no measurement of how many that is. Two weeks of
  pipeline runs answer it for free.
- **Periodic full re-embed** — catches everything eventually, but wastes hours recomputing an
  unchanged majority and leaves staleness between rebuilds; strictly dominated by the hash design.
- **Treating it as already-solved by ADR-0014 eviction** — that eviction is id-diff only; a
  changed-in-place Job is invisible to it.

## Consequences

Scraper content fixes have a defined ship step (evict → re-embed → sync) instead of silently
stale vectors. Organic JD edits remain un-re-embedded until the hash design lands — accepted and
bounded: postings are mostly short-lived, so drift is limited by posting lifetime. When the hash
design is built, `evict_store.py` stays useful for bulk invalidation (model upgrades, prefix
changes).
