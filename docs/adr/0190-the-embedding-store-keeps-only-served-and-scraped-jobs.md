# ADR-0190: The embedding store keeps only served and just-scraped Jobs

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0025](0025-parallelize-nightly-pipeline.md) (the append-only merge) · **Relates to:**
[ADR-0050](0050-persist-descriptions-across-runs.md) (the one removal the store already had),
[ADR-0168](0168-delete-the-orphaned-blobs-dont-ask-for-them-to-be-collected.md) (the per-run
blob churn this shrinks)

## Context

`embed_merge` appends every run's vectors to `data/embeddings/jobs` and removes a row only when an
upgrade replaces it (ADR-0050). `index sync` evicts closed Jobs from the served table and
`index prune` evicts off-Board and duplicate ones, but neither touched the store, so it only ever
grew (issue #600). On 2026-09-24 the dataset held 1,020,676 vectors (`embeddings.f32` 3.14 GB,
`meta.jsonl` 0.69 GB) against 520,566 served rows: about 49% of the store belonged to Jobs no
search could return. Every merge job downloads the store whole and re-uploads it whole, the join
downloads `meta.jsonl`, and `update_meta`'s sweeps and the gap ledger scan every row.

The issue also names LanceDB's download. The table's own dead weight — soft-deleted rows and
superseded index files — is already reclaimed by `cleanup-index`, which rebuilds it daily with
`index compact` and uploads with `--delete "*"`, so it is out of scope here.

No ADR decided to retain these vectors; it followed from ADR-0025's append-only merge. Its one
benefit is that `sync` re-adds a returning Job from its retained vector for free. Five runs that
day (35971969417 … 35998606646) added 699, 6,625, 3,439, 1,936 and 1,731 listings while merging
558, 6,478, 3,353, 1,893 and 1,581 new vectors, so at most 141, 147, 86, 43 and 150 adds a run
reused a retained vector.

## Decision

A new merge-job stage, `embed_prune`, runs after `index prune --apply` and before the store is
uploaded. It keeps a store row only if its id is in the served table or in this run's tech
corpus, and drops the rest, rewriting meta and vectors in lockstep with `embed_merge.evict_ids`.

- **The corpus is kept too.** A Job scraped this run that `prune` just removed would otherwise
  lose its vector, be re-planned by the next `embed_plan`, re-added by `sync` and pruned again:
  one re-embed a run, indefinitely.
- **An untrustworthy table prunes nothing.** A missing or empty table aborts, and so does a row
  count that disagrees with the base record the last writer left (`check_base`), so a
  rolled-back table cannot shrink the store.
- **Never fatal in the pipeline.** The step is `continue-on-error`. It writes through tmp files,
  which the store's upload excludes, so a failure before the swap leaves the store as it was, and
  a larger store must not block publication.
- **Skipped without the corpus.** A merge whose corpus did not arrive prunes nothing that run,
  because the ids it must keep are missing.

## Alternatives considered

- **Keep a dead vector for N days.** This keeps returning Jobs free, but needs a new per-id
  eviction-date state file, maintained and published, to save about 100 re-embeds a run.
- **Prune once a day in `cleanup-index`.** This saves nothing: the merge already rewrites the whole
  store every run. It would also make `cleanup-index` download 3.8 GB and act as a second writer
  of the store through `state_guard`.
- **Fold it into `index prune`.** No new module, but it would couple the index command to the
  vector store, and `prune` also runs in `cleanup-index`, which has no store.

## Consequences

- The first run drops about 500k rows. From then on the store tracks the served table, plus
  whatever the corpus holds that the table does not.
- A Job evicted and later seen again is re-embedded: about 40–150 Docs a run at the rates above,
  a few CPU-minutes spread across the embed shards.
- The store is no longer an archive of every vector ever embedded. It still backs every served
  row, which is all the pipeline reads it for. The manual `cluster-roles` refit also fits its
  centroids from the whole store, and from now on sees only served and just-scraped Jobs.
