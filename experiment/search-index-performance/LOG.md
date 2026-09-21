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
| semantic | 107.02 ms | 19.01 ms | -82.2% |
| semantic + ATS | 89.48 ms | 14.39 ms | -83.9% |
| semantic + full-time | 164.08 ms | 14.69 ms | -91.0% |
| semantic + India | 98.09 ms | 10.42 ms | -89.4% |
| semantic + combined | 84.41 ms | 10.26 ms | -87.8% |
| facets + combined | 244.59 ms | 40.22 ms | -83.6% |
| facets + India | 231.25 ms | 72.01 ms | -68.9% |

Retained: cosine IVF-SQ (`nprobes=80`, `refine_factor=2`), bitmap indexes on ATS/country and the
four materialized employment-type flags, B-trees on employer date and first-seen date. Build:
4.71 s; +401,266,643 bytes (+13.65%). The vector setting returned every exact top-20 id across
16 real query vectors × four filter selectivities.

### Lifecycle checks

- Appending 5,000 unindexed Jobs: 1.00 mean/min recall@20; 16.81 → 17.14 ms.
- Generic `table.optimize()`: 12.75 ms, but 3.34 → 6.99 GB because old versions remained — reject.
- Old full compaction: 6.53 s, 7.14 GB max RSS.
- Indexed full compaction after releasing the Arrow owner: 10.25 s, 7.33 GB max RSS; all nine
  indexes present after the directory swap.

### Discarded

- IVF-PQ: inadequate recall even with 16x refinement.
- IVF-Flat / HNSW-Flat: +1.57 / +1.63 GB, beyond the storage gate.
- HNSW-SQ: inadequate unfiltered recall.
- `remote`, `min_years`, salary group: scalar-index regressions.
- Coalesced experience column: <1% without an index, ~2x slower with one.
- Lowercased title + FM: 98 → 28 ms for rare `kubernetes`, but 192 ms → 9.54 s for `engineer`.
- Lowercased location + FM: ~101 → 328 ms.

See `artifacts/` for raw samples and ADR-0173 for the durable decision.
