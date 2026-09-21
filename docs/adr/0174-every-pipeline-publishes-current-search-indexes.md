# ADR-0174: Every pipeline publishes current Search indexes

**Status:** accepted · **Date:** 2026-09-21 · **Amends:**
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) (its deliberate decision to leave
new pipeline rows outside the existing indexes until cleanup) · **Relates to:**
[ADR-0091](0091-compaction-outranks-the-pipeline.md) (cleanup still owns full-table compaction)

## Context

ADR-0173 put index creation in `index compact`, because compaction replaces the LanceDB directory
and would otherwise drop indexes created earlier. That made cleanup capable of preserving the
indexes, but it also made cleanup the only way to create the first set. The code deployed while
the live dataset still had zero index files, so Search correctly fell back to exhaustive scans
until a cleanup could win its publication window.

That is the wrong delivery contract. Every successful pipeline publication must be immediately
searchable through the measured indexes, including Jobs added by that run. Cleanup may improve
physical layout, but it cannot be a prerequisite for ordinary Search performance.

LanceDB searches appended rows even when they are outside an existing index, which preserves
recall but adds an exhaustive scan of those fragments. `index_stats()` exposes that state as
`num_unindexed_rows`. The pipeline therefore needs to refresh existing indexes as well as create
missing ones; merely calling the old create-if-missing helper would leave every new Job outside
them.

## Decision

Add `python -m headstart.ingest.index refresh-indexes` to every pipeline run, after `sync` and
`prune` and immediately before LanceDB publication. The embedding store is banked first, so a
failed refresh keeps the run's vectors for the next pipeline while preventing an unindexed table
publication. The command replaces the 17 Search indexes over the final row set the run will publish:

- cosine IVF-SQ on `vector`;
- 14 bitmap indexes on the categorical and materialized filter columns;
- B-trees on `posted_at` and `first_seen`.

The command fails the pipeline rather than publishing a partial state when:

- `sync` did not leave every required materialized column in the schema;
- any required index is absent after creation; or
- any index still reports a non-zero `num_unindexed_rows`.

It writes the index base receipt after the refresh, so the next writer opens a row count attributed
to the operation that last moved the table version. The existing state guard still verifies that
the remote LanceDB prefix did not change underneath the run before upload.

`cleanup-index` remains responsible for full-table compaction and remote deletion. Its rebuild
also recreates the indexes, so compaction cannot drop the per-pipeline contract.

## Measurement

Replacing the complete index set on the 508,991-row production copy with LanceDB 0.36.0 took
**5.52 seconds**, used **2.46 GB peak RSS**, and added **401,637,376 bytes** to the local directory
(3,342,430,208 → 3,744,067,584 bytes) before cleanup reclaims the replaced index files.

A five-cycle interleaved comparison of the indexed table before and after refresh produced the
same result/facet fingerprint for every workload. Median changes stayed between **-1.6% and
+0.9%**. All 17 `index_stats()` records reported 508,991 indexed rows and zero unindexed rows.

A focused test appends a fresh Job to an indexed table, proves every index reports one unindexed
row, runs `refresh-indexes`, then proves all return to zero. A second test starts from a zero-index
table and proves the command creates all 17 without compacting table data.

## Rejected alternatives

- **Create only missing indexes:** fixes the first deployment but leaves every later run's new
  Jobs outside the existing indexes.
- **Run `table.optimize()` every pipeline:** it does update indexes, but the production-table
  experiment retained old table versions and grew 3.34 → 6.99 GB. It rewrites much more than this
  contract requires.
- **Use indexed-only search:** LanceDB's `fast_search` would avoid the fragment scan by omitting
  fresh unindexed Jobs, directly violating the freshness requirement.

## Consequences

- Every published pipeline table has current indexes; cleanup is no longer a performance gate.
- Each pipeline creates about 402 MB of replacement index files. Daily cleanup and the existing
  orphan-blob reclamation remain responsible for bounding that churn.
- A failed index refresh prevents the LanceDB publication while the embedding store remains
  banked, matching the pipeline's existing fail-closed publication semantics.
- Search continues to query unindexed fragments by default as a correctness fallback, but a
  successful pipeline now publishes zero such rows.
