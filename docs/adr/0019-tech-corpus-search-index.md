# ADR-0019: Tech-corpus search index (thin slice) — embed `data/jobs/tech`, seniority-estimated experience is filterable

- Status: Accepted
- Date: 2026-07-03
- Implements [ADR-0014](0014-search-index-ingestion-and-freshness.md) (reconciled with [ADR-0017](0017-tech-role-filter.md)); the experience call extends [ADR-0018](0018-experience-seniority-fallback.md)

## Context

ADR-0014 decided the search index should serve the product's own corpus, but the decision never
got wired: `embed_wellfound.py` still embeds the one-off Wellfound CSV, `build_index.py` still
`create_table(mode="overwrite")` on a `wellfound` table, and `TABLE = "wellfound"` is baked into the
shared `search.py`. Only the *scaffolding* landed — `corpus.iter_jobs(source)` (a source-agnostic
reader) and `index_plan.plan_sync`/`apply_sync` (the pure, board-scoped add/evict diff and its
LanceDB executor), both unit-tested but unused.

Two things changed the target since ADR-0014:

1. **ADR-0017 moved the source of truth.** The tech-role filter made `data/jobs/tech/{ats}.jsonl`
   the authoritative downstream corpus ("everything downstream — feed, embedding, index, UI — reads
   that"). This supersedes ADR-0014's literal "embed `data/jobs/{ats}.jsonl`": embedding the full
   scrape would index non-tech Jobs the product never shows.
2. **ADR-0018 changed what `min_years` is.** Experience is now extracted at ~79% coverage, but ~15k
   of the ~40k hits are a *floor estimated from a seniority label/title* (`source="seniority"`),
   not a stated number — the bulk of the 45%→79% gain.

The tech corpus's structured fields are also dirty: `employment_type` appears in ~10 spellings
(`Full-time`/`Full time`/`Employee`/`Permanent`/`Homeoffice`/…), and `salary` is raw strings like
`INR 3 - 5 (Annual) (Annual)` (doubled suffix, lakhs-vs-absolute mixed, multi-currency). Normalizing
either is real, self-contained work.

## Decision

Ship a **thin slice**: serve the real tech corpus semantically with only the filters that are cheap
and already built, reusing the ADR-0014 scaffolding rather than rewriting it.

1. **Embed source = `data/jobs/tech/`** (reconciles ADR-0014's intent with ADR-0017). Generalise
   `embed_wellfound.py` → `embed_run.py --source`, reading via `corpus.iter_jobs`, keeping the
   `langdetect` English gate (Project Scope) and the crash-safe streaming `EmbeddingStore`. The
   vector cache moves to `data/embeddings/jobs/`, keyed by id so re-runs embed only new ids.
2. **A production `jobs` table; Wellfound becomes the frozen eval benchmark.** `search.py` gains
   `PROD_TABLE = "jobs"` + `EVAL_TABLE = "wellfound"`; `serve.py` serves `jobs`, the eval scripts and
   `search_wellfound.py` stay on `wellfound`. This makes ADR-0014's "Wellfound = benchmark" split
   concrete.
3. **Thin-slice metadata.** Filters live for v1: `remote` (already a clean bool) and `min_years`.
   `employment_type` and `salary` are stored **raw for display only** — no filter on them yet; their
   normalization/parsing is deferred.
4. **`min_years`/`max_years` computed inline into `meta.jsonl`** (via `experience.extract(field,
   description, title)`), with the `source` tag carried alongside. No separate `data/enrich/tech`
   artifact and no join — Wellfound only needed that because extraction post-dated its embeddings.
5. **The experience filter trusts all `min_years`, seniority estimates included.** `build_filter`'s
   `min_years <= N OR min_years IS NULL` is unchanged; the seniority-derived floors flow in by
   construction. This keeps ADR-0018's coverage for the filter, and the exclusion a floor produces
   (a no-number "Senior" Job placed above a "≤2 yrs" search) is usually semantically right. The
   `source` tag is stored so this is revisitable if inflated titles cause false-negatives.
6. **Index maintained via `index_plan` from day one.** `index sync` replaces the overwrite: read
   the corpus, embed uncached ids, then `plan_sync`/`apply_sync` treating every Board present in the
   snapshot as "scraped" — so the first run is all-add and the identical path does true incremental
   eviction once a scrape cadence exists.

## Rejected alternatives

- **Embed the full `data/jobs/{ats}.jsonl`** (ADR-0014's literal text) — superseded by ADR-0017;
  indexes non-tech Jobs the product doesn't surface and inflates embedding cost.
- **Full filter parity now** (normalize `employment_type`, parse `salary`) — the data is genuinely
  dirty and salary is ambiguous (lakhs vs absolute, doubled suffixes, multi-currency); its own step,
  and it shouldn't block serving the real corpus.
- **Only stated numbers gate the experience filter** (seniority display-only) — safer against
  false-negatives, but discards most of ADR-0018's gain for the filter use-case; rejected per the
  fork, with the `source` tag kept as the escape hatch.
- **A separate `data/enrich/tech` experience artifact + join** (mirroring Wellfound) — needless
  indirection for a greenfield path; extraction is cheap and recomputed independently by
  `experience_coverage.py`.
- **Overwrite-rebuild** (`create_table(overwrite)`) — the already-built `index_plan` makes
  incremental free and keeps the durable table ADR-0014 wants for the ANN index; overwrite throws
  that away.

## Consequences

The AI layer finally serves the product's real ~49.7k-Job tech corpus, and Wellfound is explicitly
the frozen benchmark (the eval's nDCG now measures a corpus distinct from production — documented,
not accidental). Filters live: `remote` and experience (including seniority-estimated floors);
`employment_type` and `salary` are display-only until normalized. The seniority-in-filter call
narrows the old "unknown is kept" invariant — only a Job with **neither** a stated number **nor** a
seniority signal is now *unknown/kept*; a seniority-estimated floor can place a Job above the "at
most N years" filter (`CONTEXT.md`'s **Required experience** entry sharpened to match).

Deferred: `employment_type` normalization, `salary` parsing, content-hash re-embedding, and the
eviction *cadence/scheduler* (ADR-0014 already gates that on a scrape cadence that doesn't exist).
Implementation lands on its own branch; tests must `importorskip` lancedb/torch (the `quality` CI job
doesn't install `[embed]`).
