# ADR-0104: A Keyword filter with a scope map, backed by a stored `description` column

**Status:** accepted · **Date:** 2026-09-02 · **Extends:** [ADR-0031](0031-search-filters-and-first-seen.md)'s Search-filter vocabulary, [ADR-0084](0084-facet-counts-are-filter-shaped-not-query-shaped.md)'s counting rule; **changes** `index._schema()` (README §"The served table" updated in lockstep)

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
KEYWORD_SCOPES = {"title": ("title",), "description": ("description",), "both": ("title", "description")}
```

`build_filter` compiles whatever the map says; `filter_kwargs` whitelists `kw_in` against its keys
(an unknown scope falls back to `title`, never interpolated); the rail's `<select>` mirrors it.
Adding a scope — company, location, department — is one entry. The scope is a **modifier** of the
keyword, not a filter of its own: without a keyword it is nulled, so it can never be named as the
Blocking filter (the salary currency's rule, ADR-0084 §3).

### 2. Substring, not whole-word — measured, then chosen

LanceDB 0.33 accepts `regexp_like(col, '(?i)\bjava\b')` and it behaves. It was rejected anyway:
DataFusion's regex engine is Rust's, which has **no lookarounds**, so `\b` can only be a
word/non-word transition — and `c++`, `.net`, `c#` end in non-word characters, so `\bc\+\+\b`
never matches "c++ developer". A keyword box that cannot find "c++" is a worse failure than "java"
also finding "JavaScript". Substring is also exactly how `location` and `company` already match
(`lower(col) LIKE '%term%'` via `_like()`), so the filter adds no second escaping path. Every
whitespace-separated term must appear (AND), in at least one scoped column (OR), capped at five
terms because each is one more `LIKE` on every count the facet strip issues.

### 3. `description` becomes a column of the served table

The only shape that keeps the description scope a first-class filter (§Context). Measured on the
local store before deciding: 329,314 descriptions, median 4,447 chars, 1,534 MB raw; Lance stores
the text at **0.47×**, so the column costs **≈ 690 MB across ~287k served rows** against today's
1,155 MB table. That is a real cost against the storage budget this pipeline names as its binding
constraint, so it is taken **in stages**, not at once:

- The column is added by the same idempotent `add_columns` migration `first_seen` and the salary
  columns use. Existing rows get null.
- Every row `sync` **adds** carries its text, read from the corpus rows `update_descriptions` filled
  in the join job — the corpus already travels to the merge job as an artifact, so there is no new
  450 MB download. A targeted pass over exactly the added ids keeps memory to the run's adds.
- A row `_refresh_metadata` rewrites *anyway* (its meta moved) has a null description filled at no
  extra write cost.
- **The full backfill is a flag**, `index sync --backfill-descriptions`, off by default. It marks a
  row stale when its description is null and the store's `has_description` bit says one exists,
  and fills it from the corpus. It rewrites every row it fills, so turning it on is the deliberate
  step that spends the ≈690 MB; the disclaimer below reports honestly-low coverage until it runs.

One trap the column introduces, fixed in the same change: `_refresh_metadata` compares every schema
column against the embedding store's `meta.jsonl` and rewrites rows that differ. Meta carries only a
`has_description` **bit**, never the text — so a compared `description` column would read as stale
on every row and be clobbered to null on every run. It is therefore **carried across** a rewrite
exactly as `first_seen` is, and never compared.

### 4. The disclaimer is a number, not a sentence

`/facets` returns `description_coverage`: the count of rows matching the current filters that carry
a description, counted from the same where-clause as the total and on the same thread pool (ADR-0084
§1). Whenever the scope includes description the rail reads *"Description search covers 12,304 of
40,807 jobs matching your filters — the rest have no stored description and can't be matched this
way."* It is `null` while the column does not exist, which the UI renders as "not available yet"
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
- `facets.counts` returns one more key; `has_description` and `kw_in` join the never-droppable set.
- `CONTEXT.md` gains **Keyword filter**, with the vocabulary to avoid.
- The next `index sync` adds the column and starts filling new rows. `--backfill-descriptions` is
  the switch for the rest, to be run once from `pipeline.yml` (or `cleanup-index`) when the storage
  cost is accepted; watch the dataset's *live* size line across the runs that follow.
