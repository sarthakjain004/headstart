# ADR-0014: Search-index ingestion — real corpus, scrape-diff eviction, incremental LanceDB

- Status: Accepted
- Date: 2026-07-03
- Amended by: [ADR-0046](0046-index-collapse-guard.md) — a collapse guard withholds a Board's
  evictions when it would lose more than a quarter of its rows in one run, so scrape-diff eviction
  is no longer unconditional

## Context

The AI search layer (ADR-0005–0008) has never ingested the product's own output. `embed_wellfound.py`
reads a single hardcoded `data/jobs/wellfound.csv` — a one-off side-corpus — and `build_index.py`
loads LanceDB with `create_table(mode="overwrite")`. Two consequences, both flagged in the June-2026
audit (finding #3):

1. **Wrong corpus.** The 18 ATS scrapers write canonical `Job` records to `data/jobs/{ats}.jsonl`,
   but nothing embeds them. The whole retrieval story — including the ADR-0011 eval (nDCG@10 = 0.90) —
   is measured over the Wellfound side-corpus, not the pipeline's. ADR-0007 already calls the
   `to_meta` adapter temporary and names the canonical JSONL as the no-adapter source; this is that
   deferral coming due.
2. **No freshness.** The embedding store is append-only and the index is rebuilt-from-store by
   overwrite, so a Job can only ever *enter* the index and never leave. Closed postings accumulate —
   the worst failure mode for a freshness product (`docs/AI_Integration/architecture.md` promises an
   eviction path that isn't built).

Three facts constrain the fix:

- **Freshness signal.** Liveness (ADR-0012) is *board*-level (`status == dead` = a whole Board 404'd)
  and *upstream* — it already selects which Boards get scraped. The precise, *posting*-level
  "still open" signal is the fresh scrape's own id-set: `JobWriter` truncates `{ats}.jsonl` on a
  non-resume run, so after a full scrape of a Board the ids present are exactly its currently-open Jobs.
- **Not yet materialised.** No `{ats}.jsonl` is on disk (only one-off `wellfound.csv` / `zoho.csv`
  CSVs), and `scrape.yml` was removed, so no scrape cadence runs. The mechanism is buildable now but
  only fully exercised once a scrape runs on a cadence.
- **Eval is Wellfound-locked.** Every qrel/label id is `wellfound:…`; moving the served index off
  Wellfound orphans the ADR-0011 labels unless Wellfound's role is decided.

## Decision

Four decisions, taken together.

**1. Served corpus = the pipeline's `{ats}.jsonl`; Wellfound becomes a frozen eval benchmark.**
A source-agnostic `iter_jobs(source)` reader yields canonical `Job`-shaped dicts, so for JSONL sources
`to_meta` collapses to near-nothing (the scrapers already produce the canonical shape). The production
index is built from `{ats}.jsonl`; the Wellfound CSV is retained, behind its existing adapter, purely
as the *labelled eval benchmark* — a fixed test set is meant to be stable, not the live corpus.
`embed_wellfound.py` is generalised to `embed_run.py --source`.

**2. Eviction = scrape-diff scoped to the Boards actually scraped.**
After a scrape, for each Board in the run's `.done` set, delete index rows for that Board whose id is
absent from the fresh output and add the new ids. This is posting-level freshness; dead Boards fall
out for free (they yield nothing); scoping to scraped Boards means a partial harvest never evicts
Boards it didn't touch. Liveness stays upstream (it picks *what* to scrape), not the eviction key.

**3. The LanceDB table is the authoritative store; the flat store is demoted to a vector cache.**
Add/delete happen incrementally on the table (`table.add` new ids, `table.delete("id IN …")` gone ids,
`merge_insert` on id), replacing `create_table(overwrite)`. The `embeddings.f32` + `meta.jsonl` store
stays but as a per-id *vector cache* — embed only ids not already cached. This keeps the ADR-0004
crash-safe streaming (vectors-before-metadata, reconcile-on-resume) the audit called genuinely solid,
keeps the expensive vectors portable for the ADR-0008 cloud-store move (re-load the cache, don't
re-embed), and makes the ANN index (audit finding #8) a natural add on the now-durable table.

**4. Change detection is id-only for v1.**
Key purely on id: add new ids, delete gone ids, leave an existing id's vector untouched even if its
description was edited. Material edits to a live posting are rare and the failure is mild (slightly
stale text, not a dead link). Content-hash re-embedding is deferred; it slots in later as a third
"changed" bucket without touching the add/delete plumbing.

## Rejected alternatives

- **One unified index (Wellfound + pipeline together).** Simplest single table, but production would
  serve a stale one-off Wellfound scrape mixed with fresh Jobs, and the eval pool would be diluted by
  non-Wellfound neighbours — silently changing what nDCG = 0.90 means.
- **Re-scrape Wellfound into canonical `{ats}.jsonl`, drop the CSV.** Cleanest end-state, but voids
  the eval labels (ids change) and needs a scraper rewrite plus a full re-pool/re-label — most effort,
  and it discards the ADR-0011 labelling investment.
- **Liveness-only (board-level) eviction.** Cheap, but only removes whole dead Boards; a posting that
  closes on a still-live Board would never leave the index.
- **Both posting-diff and a liveness sweep.** More thorough as a backstop, but more moving parts; the
  scrape-diff already subsumes dead Boards, so the sweep is deferred until a gap is shown.
- **Snapshot rebuild (overwrite from a complete harvest).** Correct and dead-simple, but requires a
  full harvest of the whole Active list on every refresh — fine now, infeasible at millions-scale.
- **Prune the flat store, keep rebuild-from-store.** Simplest mental model, but an O(entire store)
  rewrite of the `.f32` blob on every refresh.
- **Vectors in LanceDB only (drop `.f32`).** One store, least machinery, but it rewrites the proven
  crash-safe `EmbeddingStore` and loses the portable vector artifact ADR-0008 will want for the cloud move.
- **Content-hash re-embed from the start.** Catches edited descriptions, but adds a stored hash + a
  compare on every ingest for a rare, low-harm case — cost without shown need.

## Consequences

The AI layer finally serves the product's real corpus, and closed postings leave the index — the
freshness story `architecture.md` promised. `to_meta` retires for canonical JSONL sources (ADR-0007's
temporary adapter), surviving only for the Wellfound benchmark CSV. `build_index.py`'s overwrite is
replaced by an incremental sync, and the durable table makes the ANN index (finding #8) buildable. The
eval now explicitly measures a *benchmark* corpus distinct from production — a gap that is now
documented rather than accidental.

Deferred / gated: the mechanism can be built now but is only exercised once a scrape produces
`{ats}.jsonl` on a cadence — which needs the removed `scrape.yml` re-enabled (audit finding #13); the
eviction loop has no scheduler until then. Content-change re-embedding, the liveness board-sweep
backstop, and ANN-index tuning at scale (ADR-0008's threshold) are all deferred.
