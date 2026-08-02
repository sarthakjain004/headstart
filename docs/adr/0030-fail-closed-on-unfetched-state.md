# ADR-0030: Fail closed when the prior state was not fetched

- Status: Accepted
- Date: 2026-07-28
- Amended by [ADR-0033](0033-state-fetch-retry-budget.md): the 3×/30 s–60 s retry budget described
  below was re-sized after production measurement (the fail-closed semantics are unchanged).
- Guards the download → mutate → upload cycle of [ADR-0020](0020-free-tier-deployment.md) across
  every stage of the sharded run ([ADR-0025](0025-parallelize-nightly-pipeline.md),
  [ADR-0026](0026-parallelize-nightly-scrape.md)). Changes no eviction, prune, or partial-harvest
  semantics ([ADR-0014](0014-search-index-ingestion-and-freshness.md),
  [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md)).

## Context

Every stage of the pipeline opens by pulling its slice of state from the HF dataset, mutates it, and
uploads the result. The pull was a bare `snapshot_download(local_dir='.')` in four places.

That call does not raise when the Hub is unreachable. It warns — `Returning existing local_dir … as
remote repo cannot be accessed` — and returns the local path. Every state dir is gitignored, so on a
CI runner "the existing local_dir" is **empty**, and an empty state is indistinguishable from a
legitimate first run.

Run `30304173982` (2026-07-27) hit a transient `429 Too Many Requests` on that call. The step
printed `store + lancedb downloaded` and exited 0 in **1 second**, against 20 s and 14 s in the runs
either side. From there every downstream step behaved exactly as designed:

- `embed_merge._reconcile_store()` returns `0` for an absent `meta.jsonl` — correct for a first run —
  then wrote a fresh `manifest.json` with `count: 11558`. That **disarmed the one guard that
  existed**, `index._load_store()`'s "no committed store" abort, which had been the only thing
  standing between a missing store and a publish.
- `index sync` created the table and bootstrapped it: `index: 0 rows`, `plan: add 11558, evict 0`.
- `index prune`'s `_MIN_KEEP_BOARDS` abort did not fire, because it validates the **ledger**, not the
  table. `keep-set: 59814 canonical live Boards` was perfectly healthy — a good ledger applied to a
  95%-empty index, the opposite direction from the one it defends.

The served table held 244,173 rows; this run was about to replace it with 11,555. What stopped it
was **the same rate limit**: `up()` exhausted its three attempts on the first upload and `bash -e`
aborted the step. Nothing landed (zero dataset commits between 20:05Z and 23:40Z). Had the 429
cleared one attempt sooner, recovery would have cost ~260 CPU-hours of re-embedding, recoverable
from HF revision history only until the next Sunday `super_squash_history` discarded it — and every
later run would have appended ~15k rows onto the stump and exited **green** while serving a
95%-empty index.

The bug was not in any one step. No step ever asked whether the state it was building on had
actually arrived.

## Decision

**Never publish state derived from a prior state you failed to load.**

**Ask the Hub what exists, then assert it landed.** `headstart.ingest.state_fetch` replaces all
four inline downloads. It calls `list_repo_files` first — which *raises* on a 429 instead of falling
back — then downloads, then checks every matched file is on disk, retrying 3× with 30 s/60 s backoff
to mirror `up()`. The download path previously had no retry at all, which is why a transient failure
became state loss instead of a wait.

Requiring **exactly what the Hub reports** is what makes this need no bootstrap opt-out: a genuine
first run matches nothing, requires nothing, and proceeds. A flag would have been one more thing to
set wrong.

All four sites are wired, not just the one that broke — the same silent fetch in `join` would make
`embed_plan` re-embed the whole corpus, in `scrape-plan` would lose the board-priority EWMA history,
and in `cleanup-index` still precedes an upload with `--delete "*"` (which today fails closed only by
accident, because `index prune` calls `open_table` on a directory that isn't there).

One layer, not two. Every downstream guard considered below is blind to this failure for the same
reason, and it is worth stating plainly: **the fetch is the only place that holds the missing fact.**
Once an empty state is on disk it is, by construction, indistinguishable from a first run — that is
the whole defect. Only the step that asked the Hub knows whether an answer came back.

## Alternatives considered

- **A fixed `_MIN_STORE_VECTORS` floor in `_load_store()`**, the literal analogue of
  `_MIN_KEEP_BOARDS`. It cannot distinguish a failed fetch from a genuine bootstrap, and
  `pipeline-smoke.yml` syncs a **10-vector** store and asserts 10 rows — any useful floor hard-fails
  smoke, and the locally-documented small syncs with it.
- **A relative store-vs-table ratio in `sync`** — abort when the store holds far fewer vectors than
  the table holds rows. This was written, then removed: it cannot fire on this incident. One
  `state_fetch` call delivers `data/embeddings/jobs/*` **and** `data/lancedb/*`, so the 429 emptied
  both, and the run reached sync with `index: 0 rows`. Any store-vs-table comparison is blind
  precisely when both come from the same failed fetch, which is every real case. The table looked
  like a witness; it was another victim.
- **A guard inside `embed_merge`.** `test_merge_first_run_no_prior_store` asserts that merging onto an
  absent store is legitimate, and merge cannot tell first-run from failed-fetch without new external
  state. The fetch already knows; asking there is free.
- **Letting the fetch fail loudly and relying on `bash -e` alone.** It already did exit 0 — that is
  the entire defect.

## Consequences

Four steps that previously degraded silently now fail closed. This is a deliberate departure from the
`continue-on-error` stance elsewhere in `pipeline.yml`, which lets a missing scrape artifact "degrade
to a safe no-op" (a run that adds and evicts nothing is harmless). The distinction: those steps
degrade to doing *nothing*, whereas an unfetched state degrades to publishing a *wrong* everything.

The cost is a louder failure mode. A 3×-retried 429 in `scrape-plan` now kills the whole run where
before it cost only that run's priority blend — a lost run, not lost data. Given the run is scheduled
every 2-4 hours and the alternative is a silent 95% index deletion, that is the right trade.

`state_fetch` is also the third guard on this same invariant, after `_MIN_KEEP_BOARDS` and
`_load_store`'s missing-manifest abort — a sign the "don't publish garbage" rule keeps being
rediscovered locally rather than stated once. Worth watching, not worth centralising yet.

One hole is knowingly left open. When `list_repo_files` succeeds but matches nothing, the fetch
requires nothing and the run bootstraps — which is correct for a genuine first run, and wrong if
`HF_DATASET` ever points at an emptied or mistyped repo. Closing it needs a witness that survives a
failed fetch, and the obvious candidate (the committed liveness ledger) is present on a fresh fork
too, so it would reject exactly the bootstrap this allows. Left as a known limit rather than a guard
that fires on the wrong runs.
