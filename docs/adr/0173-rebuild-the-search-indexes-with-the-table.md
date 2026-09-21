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
- `ats`, `country`, and the four employment-type flags: **bitmap**.
- `posted_at` and `first_seen`: **B-tree**.

An indexed vector query uses 80 IVF probes and exact-vector refinement over twice the requested
result count. Those knobs apply only when `JobSearch` observes a vector index; an old or local
unindexed table keeps exhaustive search unchanged.

The raw `employment_type` remains the display value. Four booleans materialize the existing
filter verdicts (`is_full_time`, `is_part_time`, `is_contract`, `is_internship`), including the
accepted `intern`-but-not-`international` rule. `index sync` adds them to an old table by evaluating
the same raw clauses in LanceDB; new and refreshed rows compute the same flags in Python. Until all
four exist, Search keeps the old LIKE clauses. Migration can therefore be partial without changing
which Jobs match.

The rebuild drops the in-memory Arrow table before training indexes. Without that release, peak RSS
rose from the old compactor's 7.14 GB to 9.63 GB. Releasing it first held the indexed rebuild to
7.33 GB, 2.6% above the old path rather than 35%.

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
| browse | 48.05 ms | 47.96 ms | -0.2% |
| browse, ATS filter | 17.17 ms | 13.62 ms | -20.7% |
| browse, employer-date order | 41.71 ms | 28.37 ms | -32.0% |
| semantic, no filter | 107.02 ms | 19.01 ms | -82.2% |
| semantic, ATS | 89.48 ms | 14.39 ms | -83.9% |
| semantic, full-time | 164.08 ms | 14.69 ms | -91.0% |
| semantic, salary bracket | 157.86 ms | 60.39 ms | -61.7% |
| semantic, India | 98.09 ms | 10.42 ms | -89.4% |
| semantic, combined filters | 84.41 ms | 10.26 ms | -87.8% |
| facets, no filters | 251.21 ms | 260.52 ms | +3.7% |
| facets, combined filters | 244.59 ms | 40.22 ms | -83.6% |
| facets, India | 231.25 ms | 72.01 ms | -68.9% |

IVF-SQ at 80 probes + 2× refinement reproduced **every top-20 id** across 16 real query vectors
and four filter selectivities (1,280 expected result positions). Building the full retained set
took **4.71 s** and added **401,266,643 bytes (13.65%)**. A full indexed compaction took 10.25 s
against the old path's 6.53 s.

Raw samples, scripts, live observations, and every rejected arm are under
`experiment/search-index-performance/`.

## Rejected experiments

- **IVF-PQ:** only 0.56–0.63 recall@20 by default; 16× refinement still left narrow-filter
  queries as low as 0.20.
- **IVF-Flat:** added 1.57 GB (53%) and still had 0.90 worst-query recall at the useful settings.
- **HNSW-SQ:** default unfiltered recall was 0.87; `ef=240` reached only 0.96 mean / 0.90 minimum.
- **HNSW-Flat:** added 1.63 GB (56%) before query tuning, failing the storage gate.
- **`remote` bitmap:** regressed remote semantic search and the combined page.
- **`min_years` B-tree:** the real `(min_years <= N OR min_years IS NULL)` path doubled from
  roughly 138 to 292 ms. A coalesced materialized integer with a B-tree was equally bad; without
  the index it changed latency by less than 1%.
- **Salary currency + range indexes:** the multi-currency overlap expression became slower.
- **FM indexes on raw title/location:** the current lowercased LIKE expressions could not use
  them. A lowercased title column made rare `kubernetes` queries fast, but `engineer` regressed
  from 192 ms to 9.54 s; location similarly regressed. Arbitrary user substrings cannot take that
  selectivity cliff.
- **Stored USD salary columns and a stored posting date:** LanceDB 0.36 cannot express the needed
  guarded CASE transforms as an in-place migration; their existing paths were no longer top costs
  after ANN indexing, so a pipeline-wide derivation was not justified.

## Consequences

- The first cleanup after deployment materializes the flags, rebuilds all nine indexes, uploads
  the larger table, and restarts the Space. Ordinary pipeline appends remain incremental.
- Exact exhaustive search remains the fallback on a table without the vector index.
- Adding a new employment-type filter now requires one rule whose Python verdict, legacy raw SQL,
  served boolean, migration expression, and bitmap index all derive from the same definition.
- Current-state storage rises by 13.65%; cleanup upload cost and HF history pressure rise with it.
  The existing orphan-blob reclamation remains responsible for preventing old rebuilt blobs from
  accumulating against quota.
