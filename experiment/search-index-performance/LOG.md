# Search-index performance

## Goal

Measure the production Search and filter path on the current served LanceDB table, then retain
only indexing, materialization, or query-plan changes that improve comparable latency without
changing filter results or materially weakening semantic-search recall.

## Acceptance gates

- Scalar/filter changes must preserve the exact matching Job ids.
- Vector changes must reach at least 0.98 recall@20 against exhaustive cosine search over the
  fixed query set.
- A retained change must improve a representative warm median or p95 by at least 20%, exceed the
  interleaved control's drift, and introduce no repeatable regression above 10% on another common
  Search path.
- Index build time, on-disk growth, compaction survival, incremental-update behavior, and the
  private dataset's storage budget are measured constraints, not afterthoughts.
- Live endpoint measurements are low-concurrency observations only. Candidate comparisons run on
  a fresh copy of the production table, never by mutating production.

## Workloads

1. Authenticated production `/search` and `/facets`: browse, semantic query, one filter, and
   combined filters; cold wake-up is separated from warm steady state.
2. Local table-only search over the freshly pulled production table: exhaustive vector search,
   filtered vector search, browse/filter/sort, and facet counts.
3. Recall: indexed results compared with exhaustive cosine results for the same fixed query
   vectors and filters.

## Candidate hypotheses

1. Scalar indexes on equality/range columns used by real `build_filter()` clauses.
2. A vector ANN index, with index family and query knobs selected by measured latency/recall.
3. Index-preserving compaction plus index refresh after incremental writes.
4. Materialized columns only for filter expressions that cannot use an index and dominate a
   measured plan.
5. Full-text/NGRAM indexes for keyword/location/company substring filters, only if they preserve
   today's substring semantics.
6. Query-shape changes such as prefilter/postfilter, projection, sort-window, and facet-count
   reuse, tested independently of storage indexes.

## Run-owned resource ledger

- Worktree: `/Users/sarthakjain/.codex/worktrees/trust-tab-20260921/HeadStart`
- Branch: `codex/search-index-performance`
- Production snapshot destination: `data/lancedb/` in this worktree (gitignored; remove after the
  PR evidence is complete).
- Copy-on-write experiment root: `/tmp/headstart-search-index-perf-20260921/` (marker
  `.codex-owned`; contains the untouched `baseline/` clone and candidate clones).
- Production-compatible benchmark environment:
  `/tmp/headstart-search-index-perf-20260921/venv/` (Python 3.12, LanceDB 0.36.0,
  PyArrow 25.0.0, matching the Space pins).
- Benchmark artifacts: `experiment/search-index-performance/artifacts/` (retain as PR evidence).
- No production table, dataset, or Space mutation is authorized by this experiment.

## Results

### Production observations

Authenticated, sequential, nine rotated rounds against the deployed Space:

| request | median | p95 |
| --- | ---: | ---: |
| browse | 628.7 ms | 1,075.1 ms |
| semantic | 1,153.0 ms | 1,233.9 ms |
| semantic + combined filters | 829.2 ms | 966.3 ms |
| facets + combined filters | 4,501.9 ms | 5,122.5 ms |

The first `/coverage` call paid a 4.4 s lazy count; later calls were ~220–300 ms. The cookie was
read on stdin and is absent from every artifact.

### Production-table baseline

- Pulled from `imPoseidon/headstart-index` on 2026-09-21.
- 508,991 rows, 2,939,074,149 bytes, 17 fragments, zero indexes.
- Candidate measurements used Python 3.12, LanceDB 0.36.0 and PyArrow 25.0.0, matching the Space.
- Final comparison used seven ABBA cycles per workload; all result and facet fingerprints matched.

| workload | baseline | retained | change |
| --- | ---: | ---: | ---: |
| semantic | 102.94 ms | 18.36 ms | -82.2% |
| semantic + ATS | 85.38 ms | 13.60 ms | -84.1% |
| semantic + full-time | 156.93 ms | 15.36 ms | -90.2% |
| semantic + India | 93.98 ms | 10.69 ms | -88.6% |
| semantic + combined | 83.26 ms | 9.11 ms | -89.1% |
| facets, no filters | 75.76 ms | 8.34 ms | -89.0% |
| facets + combined | 123.33 ms | 19.26 ms | -84.4% |
| facets + India | 105.00 ms | 9.70 ms | -90.8% |

Retained: cosine IVF-SQ (`nprobes=80`, `refine_factor=2`), 14 bitmap indexes (ATS/country/remote,
presence/shape flags, four employment types, four offered experience ceilings), and B-trees on
employer date and first-seen date. Full build: 6.31 s; +402,650,424 bytes (+13.70%). The vector
setting returned every exact top-20 id across 16 real query vectors × four filter selectivities.

Removing unused description coverage plus the presence/date/experience bitmaps reduced cold
no-filter facets from 423.65 to 28.53 ms. The 60-second bounded cache serves repeated filter sets
at a 0.0042 ms in-process median and is invalidated by every pipeline-triggered Space restart.
A separate 128-entry vector LRU avoids repeating model inference when only filters or pages change;
startup performs one semantic pass to remove the measured first-request model penalty.

Full-set correctness (not only top-20): all four employment flags produced identical legacy/new
Job-id set fingerprints and zero row mismatches across 508,991 rows. The incremental positive
control appended a new vector equal to a real query vector; it ranked first under exhaustive and
indexed search both before and after index optimization.

The presence/date/experience flags also matched their complete legacy Job-id sets with zero row
mismatches. Counts: description 497,800; salary 140,968; comparable date 487,469; experience
ceilings 107,131 / 181,641 / 398,081 / 499,477. Cold no-filter facets fell from 423.65 ms before
these fixes to 28.53 ms after them. A remote bitmap, rejected under exhaustive search, was retested
with the retained ANN plan and improved the remote semantic page to 12.67 ms, so it is retained.

Independent query-shape results: prefiltering retained 1.00 recall and was faster; postfiltering
fell to 0.003–0.19 mean recall and sometimes returned no rows. Id-only / production / all-column
projection measured 16.39 / 19.29 / 22.03 ms. Ranked windows of 20 / 400 / 1,000 / 2,000 measured
18.98 / 35.04 / 50.07 / 64.20 ms; 2,000 remains the pagination contract.

Playwright end to end: deployed baseline empty-query requests dispatched in <1 ms but Search took
2.15–2.49 s, facets 5.54–6.05 s, and cards waited the full 5.55–6.05 s. Same-host/current-main
against the same table settled an unfiltered browse at 268.5 ms and repeated filtered browse at
295.5 ms. The final candidate settled those at 32.8 and 17.8 ms. Candidate cold filtered browse:
Search TTFB 20.3 ms, facets TTFB 25.7 ms, cards 22.5 ms, settled 27.6 ms. The UI now paints Search
results before a cold facet request finishes, then reconciles the total/pager.

### Lifecycle checks

- Appending 5,000 unindexed Jobs: 1.00 mean/min recall@20; 16.81 → 17.14 ms.
- Generic `table.optimize()`: 12.75 ms, but 3.34 → 6.99 GB because old versions remained — reject.
- Old full compaction: 6.53 s, 7.14 GB max RSS.
- Indexed full compaction after releasing the Arrow owner: 10.78 s, 7.30 GB max RSS; all 17
  indexes present after the directory swap.

### Discarded

- IVF-PQ: inadequate recall even with 16x refinement.
- IVF-Flat / HNSW-Flat: +1.57 / +1.63 GB, beyond the storage gate.
- HNSW-SQ: inadequate unfiltered recall.
- `min_years` B-tree and the numeric salary index group: scalar-index regressions. A remote bitmap
  was later retained after IVF-SQ changed the query plan and the combined benchmark showed a win.
- Coalesced experience column: <1% without an index, ~2x slower with one.
- Lowercased title + FM: 98 → 28 ms for rare `kubernetes`, but 192 ms → 9.54 s for `engineer`.
- Lowercased location + FM: ~101 → 328 ms.
- Company FM: `google` 82.6 → 38.8 ms, but `tech` 90.3 → 557 ms and `a` 192 ms → 18.34 s.
- Description FM: +2.89 GB and `kubernetes` 2.47 → 5.11 s.
- NGRAM FTS: fast BM25 top-k, but cannot preserve the Keyword filter's boolean-set contract.

See `artifacts/` for raw samples and ADR-0173 for the durable decision.
