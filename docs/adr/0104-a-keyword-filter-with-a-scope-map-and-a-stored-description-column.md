# ADR-0104: A Keyword filter with a scope map, backed by a stored `description` column

**Status:** accepted · **Date:** 2026-09-02 · **Extends ADR-0031's Search-filter vocabulary and ADR-0084's counting rule; changes `index._schema()`, with README §"The served table" updated in lockstep**

## Context

The Search tab had a semantic **Query** and a rail of structured **Search filters**, and no way to
say "the word *kubernetes* must appear". A user who wants that has to hope the embedding agrees,
and it often does not — a vector search ranks by meaning, and "must contain this token" is not a
meaning. The request was a keyword filter that can look in the **title**, in the **description**,
or in both, built so that adding a further scope later is cheap, and — because not every Job has a
description — with a disclaimer whenever the description scope is in use.

Two facts about the served table decided the shape:

- `title` is a column; `description` was **not**. The table stored the vector the description was
  embedded into and nothing else of it (README: *"the description is embedded, not stored"*). The
  text lives in the ADR-0050 store (451 MB gzipped on HF) and, after `update_descriptions`, in the
  corpus rows that travel to the merge job.
- ADR-0084 makes every filter's *count* a property of the where-clause alone. A filter that cannot
  be expressed as a where-clause on the served table cannot have facet counts, cannot be named as
  the Blocking filter, and would make the "N matching your filters" header disagree with the list
  beneath it — the exact defect ADR-0084 calls worse than no count.

## Decision

### 1. One scope map is the extension point

```python
KEYWORD_SCOPES = {
    "title": KeywordScope(("title",), "Title"),
    "description": KeywordScope(("description",), "Description"),
    "both": KeywordScope(("title", "description"), "Title or description"),
}
```

`build_filter` compiles whatever the map says; `filter_kwargs` whitelists `kw_in` against its keys
(an unknown scope falls back to `title`, never interpolated); and `keyword_scope_options()` hands
the template and the JS `(value, label, needs_description)` per scope, so the rail's `<select>`,
which options are disabled before the column exists, and which scopes carry the coverage
disclaimer are all *rendered from* the map rather than restating it. Adding a scope — company,
location, department — is one map entry, its columns and its label, and nothing in the template
or the JS.
The scope is a **modifier** of the keyword, not a filter of its own: without a keyword it is
nulled, so it can never be named as the Blocking filter (the salary currency's rule, ADR-0084 §3),
and the browser omits it from a request when it is the default.

### 2. Substring, not whole-word — measured, then chosen

LanceDB 0.33.0 accepts `regexp_like(col, '(?i)\bjava\b')` and it behaves — measured 2026-09-02 on
an in-memory table: `(?i)` honoured, `\b` honoured, null column safe, two terms AND. It was rejected anyway:
DataFusion's regex engine is Rust's, which has **no lookarounds**, so `\b` can only be a
word/non-word transition — and `c++`, `.net`, `c#` end in non-word characters, so `\bc\+\+\b`
never matches "c++ developer". A keyword box that cannot find "c++" is a worse failure than "java"
also finding "JavaScript". Substring is also exactly how `location` and `company` already match
(`lower(col) LIKE '%term%'` via `_like()`), so the filter adds no second escaping path. Every
whitespace-separated term must appear (AND), in at least one scoped column (OR), capped at five
terms because each is one more `LIKE` on every count the facet strip issues.

### 3. `description` becomes a column of the served table

The only shape that keeps the description scope a first-class filter (§Context). Its cost was
measured before deciding, on 2026-09-02, against the **local** `data/descriptions/` snapshot —
stale by construction (whenever this machine last pulled it) but shape-representative:

- 329,314 records carry a description; median 4,447 chars, p90 7,690; **1,534 MB** of raw text.
- A 20,000-record sample written to a LanceDB 0.33.0 table occupied **48.1 MB on disk for
  101.5 MB of text — 0.47×**.
- Scaled by row count to the ~287k rows the served table held, that is **≈ 690 MB**. It is an
  estimate, and an upper one: not every served row has text to store.
- The served table itself measured **1,155 MB** the same day, read from the HF dataset's file
  metadata (`HfApi().repo_info(files_metadata=True)`), so the column is roughly +60%.

That is a real cost against the storage budget this pipeline names as its binding constraint, so
it is taken **in stages**, not at once:

- The column is added by the same idempotent `add_columns` migration `first_seen` and the salary
  columns use. Existing rows get null.
- Every row `sync` **adds** carries its text, read from the corpus rows `update_descriptions` filled
  in the join job — the corpus already travels to the merge job as an artifact, so there is no new
  450 MB download. A targeted pass over exactly the added ids keeps memory to the run's adds.
- A row `_refresh_metadata` rewrites *anyway* (its meta moved) has a null description filled at no
  extra write cost.
- **The full backfill is a flag**, `index sync --backfill-descriptions`, off by default. A row is a
  candidate when its description is null and the store's `has_description` bit says one exists —
  and it is rewritten **only if this run's corpus actually carries its text**. That qualifier
  matters: the merge job's corpus is the run's *slice*, not the whole table, so without it one
  flagged run would delete-and-re-add every null-description row (~25 KB of vector each) while
  filling only the slice's share. Candidates the corpus lacks are logged and left for a later
  run. So it rewrites every row it fills and only
  those; the disclaimer below reports honestly-low coverage until the whole table is covered.
- **The whole-table pass is a separate subcommand**, `index backfill-descriptions`. The flag above
  cannot do that job and never could: it reads its text from the *corpus*, which is the run's
  ~20,000-Board slice, so a row whose Board sits out every run is permanently unreachable from it.
  The subcommand reads the ADR-0050 store instead — every Job whose description we hold, whichever
  run last scraped it — so one pass covers the table. It is dry-run by default like `prune`, takes
  each row's vector from **the row itself** rather than the embedding store (so it needs no ~1.5 GB
  download), and reads the store one ATS at a time to bound memory. Like `compact` it is not a
  stage of the 6-hourly run: it is invoked from `cleanup-index`, whose `backfill_descriptions`
  input gates it and which already holds both inputs on disk.

One trap the column introduces, fixed in the same change: `_refresh_metadata` compares every schema
column against the embedding store's `meta.jsonl` and rewrites rows that differ. Meta carries only a
`has_description` **bit**, never the text — so a compared `description` column would read as stale
on every row and be clobbered to null on every run. It is therefore **carried across** a rewrite
exactly as `first_seen` is, and never compared.

### 4. The disclaimer is a number, not a sentence

`/facets` returns `description_coverage`: `{"covered", "total"}` — of the rows the *other* filters
match, how many carry a description, and how many there are — counted on the same thread pool as
every facet (ADR-0084 §1) and **with the keyword lifted**, the way a dimension's "Any" row lifts its
own dimension. Lifting it is what keeps the number meaningful while description searching is in
use: counted with the keyword intact, a description-scoped keyword makes the covered set and the
total the same clause — every row it matched has a description by construction — and the rail
would read "N of N" on a table where almost nothing has text, the one state the disclaimer exists
for. Whenever the scope includes description the rail reads *"Descriptions are stored for 12,304 of
the 40,807 jobs your other filters match — a keyword looked for here can only match inside those;
the rest have no stored description."* It is `null` while the column does not exist, which the UI renders as "not available yet"
with the scope options disabled — the `has_first_seen` dark-until-migrated rule — distinct from a
genuine zero. The number is honestly low on the day this ships and rises as rows land and when the
backfill runs; a static sentence would have said the same thing on both days.

## Alternatives rejected

- **Post-filter in the Space against the description store.** Breaks ADR-0084 (counts and the list
  would describe different queries), needs the 451 MB store in the Space image plus ~1.5 GB of text
  in memory or a disk-backed lookup, and cannot fill a page of `k` without over-fetching. A
  second-class filter by construction.
- **Make the keyword part of the Query.** CLAUDE.md's hybrid split is explicit: structured
  constraints are filters, the Query is only the role sentence. A token that *must* appear is a
  constraint.
- **Full-text (tantivy) index on the description column.** Better tokenisation than substring, but
  it needs the column first — this ADR — plus an index to build and keep current in `sync`. A
  refinement to layer on later, not a reason to skip the column.
- **Two boxes, one per column** — "*X* in the title and *Y* in the description" at once. The
  request reads as two *options* for one keyword, not two simultaneous constraints, so one box
  with a scope picker was built; a second box would be one more map-driven control if that
  reading turns out to be wanted.
- **Backfill immediately in this change.** Rewrites the whole table and adds ≈690 MB in one merge
  run, into a dataset whose history is compacted on a budget. That is the user's call, made
  reversible by shipping it as an off-by-default flag rather than a silent side effect.

## Consequences

- `build_filter` gains `kw`, `kw_in` and `has_description`. The last is the one runtime fact that is
  **defaulted** (`False`), unlike `has_first_seen`/`has_min_salary_annual`: forgetting it can only
  leave the description scope dark — the safe direction — where forgetting `has_first_seen` turns
  the alerts cutoff into no clause. Existing callers are untouched.
- `index._schema()` gains `description` after `title`; README §"The served table" documents it and
  `tests/test_readme_schema.py` pins the two together. The API projection does **not** serve it.
- `facets.counts` returns one more key, `description_coverage` (`{"covered", "total"}`, or `null`
  without the column); `has_description` and `kw_in` join the never-droppable set.
- Saved sets and Subscriptions do **not** carry the keyword: `ALLOWED_SEARCH_FILTERS` and
  `SET_SEARCH_FILTERS` (`alerts/store.py`) omit `kw`/`kw_in`, as they already omit the salary
  bracket, so saving or subscribing from a keyword search drops it. The request scoped the filter
  to the Search tab; carrying it into alerts is a separate decision.
- `CONTEXT.md` gains **Keyword filter**, with the vocabulary to avoid.
- The next `index sync` adds the column and starts filling new rows. The rest are covered by
  `index backfill-descriptions`, run once from `cleanup-index` with its `backfill_descriptions`
  input set — a decision, not a default, tracked in GitHub issue **#346**. Watch the dataset's
  *live* size line across the runs that follow. The step is placed before `compact` deliberately:
  the rewrite is delete-then-add and leaves a deletion file per touched fragment, and `compact` is
  what reclaims them.
