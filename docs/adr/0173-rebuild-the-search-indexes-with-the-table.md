# ADR-0173: Rebuild the Search indexes with the table

**Status:** accepted · **Date:** 2026-09-21 · **Amends:**
[ADR-0138](0138-a-materialized-country-column-serves-the-india-filter.md) (its deliberate
no-index scope and the `compact()` index-loss blocker) · **Relates to:**
[ADR-0091](0091-compaction-outranks-the-pipeline.md) (the isolated whole-table rebuild),
[ADR-0116](0116-a-quiet-palette-and-a-scanning-layout.md) (the Search surface being accelerated)

## Context

The production table had **508,991 Jobs**, occupied **2,939,074,149 bytes**, and carried **zero
indexes**. Authenticated low-concurrency measurements of the deployed Space put warm semantic
search at a 1,153 ms median and the combined `/facets` request at 4,502 ms. The model and network
are part of those endpoint figures; the controlled comparisons below isolate LanceDB against a
fresh pull of the exact served table, using the Space's pinned LanceDB 0.36.0 / PyArrow 25.0.0.

`compact()` was the blocker, not index creation. It rebuilds the table into a new directory to
remove orphan fragments, so every index created earlier disappears. Recreating indexes outside
that operation would therefore buy speed only until the next cleanup.

## Decision

The fresh-directory rebuild creates the Search indexes after it writes the live rows:

- `vector`: cosine **IVF-SQ**.
- `ats`, `country`, `remote`, two presence flags, the posting-date shape flag, four
  employment-type flags, and four offered experience ceilings: **bitmap**.
- `posted_at` and `first_seen`: **B-tree**.

An indexed vector query uses 80 IVF probes and exact-vector refinement over twice the requested
result count. Those knobs apply only when `JobSearch` observes a vector index; an old or local
unindexed table keeps exhaustive search unchanged.

`JobSearch` also keeps bounded 60-second LRUs for facet payloads (128 filter sets) and browse pages
(64 filter/page/sort sets), plus 128 query vectors with no TTL because the loaded model is immutable.
Changing a filter or page for the same semantic text therefore does not repeat model inference.
Facets are query-independent, and the table cannot change during a
process's lifetime — every successful pipeline publication restarts the Space on the newly-opened
table — so changing only the semantic query must not repeat ~60 counts. The short TTL keeps 2-hour
recency windows moving even in a long-lived process; the bounds prevent arbitrary requests growing
memory without limit. Startup preloads the unfiltered browse and facet responses plus one semantic
pass from that fresh table, so the first visitor pays neither cache population nor lazy model work.

The existing query shape remains deliberate: filters run before ANN search, the production result
projection omits vector/description payloads, and ranked date/salary sorts keep their 2,000-result
window. Post-filtering was both slower and destructive (0.003–0.19 mean recall with some queries
returning zero rows). On the indexed table, id-only / production / all-column projections measured
16.39 / 19.29 / 22.03 ms; the production fields are the response contract. Ranked windows of
20 / 400 / 1,000 / 2,000 measured 18.98 / 35.04 / 50.07 / 64.20 ms; shrinking the last one would
make later pages unreachable rather than merely faster.

The browser no longer keeps result cards as skeletons until facets finish. `/search` and `/facets`
still start together; Jobs paint as soon as the Search response arrives, initially with the count
known from that page, then the total and pager reconcile when facets return. Empty and stale-request
paths keep the same request-generation guards.

The raw `employment_type` remains the display value. Four booleans materialize the existing
filter verdicts (`is_full_time`, `is_part_time`, `is_contract`, `is_internship`), including the
accepted `intern`-but-not-`international` rule. `index sync` adds them to an old table by evaluating
the same raw clauses in LanceDB; new and refreshed rows compute the same flags in Python. Until all
four exist, Search keeps the old LIKE clauses. Migration can therefore be partial without changing
which Jobs match.

Three other repeated expressions are materialized without replacing their source fields:
`description_stored` mirrors `description IS NOT NULL`, `salary_known` mirrors
`min_salary_annual IS NOT NULL`, and `posted_at_comparable` mirrors the exact legacy
`posted_at LIKE '____-__-__%'` guard. The first removes the one 423 ms count every cold facet
request used to execute even when no description keyword was active; that coverage count now runs
only when its note can actually be shown. The four experience ceilings offered by facets are also
stored as booleans, while arbitrary user-entered ceilings keep the legacy expression. Every flag
keeps its raw fallback until the whole migration has landed.

The rebuild drops the in-memory Arrow table before training indexes. Without that release, peak RSS
rose from the old compactor's 7.14 GB to 9.63 GB. Releasing it first held the indexed rebuild to
7.30 GB, 2.2% above the old path rather than 35%.

There is deliberately no per-pipeline `table.optimize()`. Indexed LanceDB searches include newly
appended, unindexed fragments. A 5,000-row append kept recall@20 at 1.00 and moved median ANN
latency only 16.81 → 17.14 ms. `optimize()` improved that to 12.75 ms but retained old versions and
doubled the directory from 3.34 to 6.99 GB. The next cleanup rebuild folds the new rows into every
index without that accumulation.

## Measurement

The final comparison is seven ABBA cycles per workload, interleaving baseline and candidate calls.
Every Search-result and facet-payload fingerprint matched.

| production-table workload | no index | retained candidate | change |
| --- | ---: | ---: | ---: |
| browse | 47.76 ms | 48.29 ms | +1.1% |
| browse, ATS filter | 17.09 ms | 13.72 ms | -19.7% |
| browse, employer-date order | 42.45 ms | 39.35 ms | -7.3% |
| semantic, no filter | 102.94 ms | 18.36 ms | -82.2% |
| semantic, ATS | 85.38 ms | 13.60 ms | -84.1% |
| semantic, full-time | 156.93 ms | 15.36 ms | -90.2% |
| semantic, salary bracket | 142.18 ms | 33.41 ms | -76.5% |
| semantic, India | 93.98 ms | 10.69 ms | -88.6% |
| semantic, combined filters | 83.26 ms | 9.11 ms | -89.1% |
| facets, no filters (uncached) | 75.76 ms | 8.34 ms | -89.0% |
| facets, combined filters (uncached) | 123.33 ms | 19.26 ms | -84.4% |
| facets, India (uncached) | 105.00 ms | 9.70 ms | -90.8% |

IVF-SQ at 80 probes + 2× refinement reproduced **every top-20 id** across 16 real query vectors
and four filter selectivities (1,280 expected result positions). Building the full retained set
took **6.31 s** and added **402,650,424 bytes (13.70%)**. A full indexed compaction took 10.78 s
against the old path's 6.53 s. Uncached retained facet requests measured 8.34–134.19 ms by filter
shape; the same filter set with different semantic queries then measured a 0.0042 ms warm median
from the bounded cache.

Each employment-type flag was also checked over the full table, not only on returned pages:
313,836 full-time, 5,347 part-time, 20,464 contract and 2,186 internship Job ids produced identical
order-independent set fingerprints under the legacy clause and the new flag, with zero row-level
mismatches.

The other materialized verdicts passed the same full-set check with zero mismatches: 497,800 Jobs
with stored descriptions, 140,968 with known salaries, 487,469 with comparable posting dates, and
all four experience ceilings (107,131 / 181,641 / 398,081 / 499,477 Jobs). Removing the unused
description coverage scan and adding these bitmaps reduced a cold no-filter facet payload from
423.65 to **28.53 ms**. The date flag cut 30/90-day semantic pages from 28.92/38.82 to
13.06/14.15 ms; experience flags cut the four offered ceilings by 40–46%. A `remote` bitmap was
retested after ANN changed the plan and reduced its semantic page to 12.67 ms, so the earlier
exact-scan rejection no longer applied.

### Browser end to end

Playwright measured the click in the page (not automation actionability time), Resource Timing for
both requests, and MutationObserver stamps for first cards and fully reconciled counts.

The authenticated deployed baseline dispatched both requests in under 1 ms. With an empty semantic
query, `/search` took 2.15–2.49 s under concurrent load, `/facets` took 5.54–6.05 s, and the UI
showed nothing until **5.55–6.05 s**. Repeated semantic combined filters similarly settled at a
6,214 ms median. The deployed JavaScript waits for facets. This is production observation, not a
candidate comparison: the PR is not deployed yet.

For a comparable before/after, current `origin/main` and this branch ran locally on the same host,
against copy-on-write clones of the same 508,991-row production table and the real encoder:

| browser interaction | current main | retained candidate | change |
| --- | ---: | ---: | ---: |
| empty query, no filters, fully settled | 268.5 ms | 32.8 ms | -87.8% |
| empty query, first combined filter set, fully settled | 257.0 ms | 27.6 ms | -89.3% |
| empty query, repeated combined filters, fully settled | 295.5 ms | 17.8 ms | -94.0% |
| semantic query, repeated combined filters, fully settled median | 320.1 ms | 25.9 ms | -91.9% |

On the final candidate's cold empty-query combined interaction, Search TTFB was 20.3 ms, facets
TTFB 25.7 ms, cards painted at 22.5 ms, and counts settled at 27.6 ms. Repeating it returned both
cached endpoints in 1.0–1.3 ms and settled at 17.8 ms including browser work. Semantic warm
repeats settled in 24.8–26.9 ms.

Raw samples, live observations, and every rejected arm are under
`experiment/search-index-performance/`; their reusable harnesses are
`scripts/bench/search_*.py`.

## Rejected experiments

- **IVF-PQ:** only 0.56–0.63 recall@20 by default; 16× refinement still left narrow-filter
  queries as low as 0.20.
- **IVF-Flat:** added 1.57 GB (53%) and still had 0.90 worst-query recall at the useful settings.
- **HNSW-SQ:** default unfiltered recall was 0.87; `ef=240` reached only 0.96 mean / 0.90 minimum.
- **HNSW-Flat:** added 1.63 GB (56%) before query tuning, failing the storage gate.
- **`min_years` B-tree:** the real `(min_years <= N OR min_years IS NULL)` path doubled from
  roughly 138 to 292 ms. A coalesced materialized integer with a B-tree was equally bad; without
  the index it changed latency by less than 1%.
- **Salary currency + range indexes:** the multi-currency overlap expression became slower.
- **FM indexes on raw title/location:** the current lowercased LIKE expressions could not use
  them. A lowercased title column made rare `kubernetes` queries fast, but `engineer` regressed
  from 192 ms to 9.54 s; location similarly regressed. Arbitrary user substrings cannot take that
  selectivity cliff.
- **Company/description FM indexes:** selective `google` improved 82.6 → 38.8 ms, but `tech`,
  `group`, and `a` regressed to 0.56 s, 0.28 s, and 18.34 s. A lowercased description plus FM
  doubled current-state storage to 5.83 GB and made `kubernetes` 2.47 → 5.11 s.
- **NGRAM FTS:** compact (29 MB) and fast for selective terms, but exposes a BM25-ranked top-k,
  not the deterministic boolean candidate set the explicit Keyword filter promises. Feeding only
  that top-k into semantic ranking would silently discard valid matches.
- **Stored USD salary columns:** LanceDB 0.36 cannot express the needed guarded CASE transforms as
  an in-place migration; the numeric bracket was no longer a top cost after ANN indexing, so a
  pipeline-wide FX derivation was not justified.

## Consequences

- The first cleanup after deployment materializes the flags, rebuilds all 17 indexes, uploads
  the larger table, and restarts the Space. Ordinary pipeline appends remain incremental.
- Exact exhaustive search remains the fallback on a table without the vector index.
- Every successful pipeline publication restarts the Space after opening the newly-uploaded table;
  no process cache survives across that boundary, so newly-arrived Jobs cannot be hidden by it.
- Adding a new employment-type filter now requires one rule whose Python verdict, legacy raw SQL,
  served boolean, migration expression, and bitmap index all derive from the same definition.
- Current-state storage rises by 13.70%; cleanup upload cost and HF history pressure rise with it.
  The existing orphan-blob reclamation remains responsible for preventing old rebuilt blobs from
  accumulating against quota.
