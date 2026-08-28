# ADR-0091: Compaction outranks the pipeline

**Status:** accepted · **Date:** 2026-08-27 · **Amended by:**
[ADR-0094](0094-ask-for-compaction-on-a-threshold-not-a-clock.md) (on the *trigger* only — the
03:00 cron below is now a backstop rather than the sole path, because GitHub stopped delivering it;
compaction's priority over the pipeline, decided here, is unchanged) · **Amends:**
[ADR-0071](0071-back-to-back-runs-instead-of-a-fixed-cadence.md) (§"`cleanup-index` keeps the shared group,
and may occasionally be displaced", whose tolerated risk is what fired) · **Relates to:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (the prune whose deletes accumulate),
[ADR-0050](0050-persist-descriptions-across-runs.md) (the second shared writer)

## Context

On 2026-08-27 the pipeline stopped publishing. Every upload was rejected:

```
Your push was rejected because it contains too many files per directory.
Offending directories: /data/lancedb/jobs.lance/_deletions/
```

Nothing was wrong with the run. Hugging Face refuses a push where one directory holds more than
10,000 files, and `_deletions/` had passed it.

**Why the files accumulate, and why the growth is not linear.** Lance never edits a data file in
place, so a delete writes a tombstone instead: one file per *fragment* the delete touches. Deletes
are chunked, and each chunk's ids are scattered across the whole table, so a run writes
`ceil(deleted / chunk) x fragments` of them. The fragment count is the problem — the pipeline
appends ~10 fragments per run and uploads additively, so it climbs every run and each run costs
more than the last. Measured: 10 fragments and 30 deletion files immediately after a compaction,
against ~10,000 accumulated across ~36 runs. It reads as healthy right up to the cliff.

**Only `index compact` resets it**, and it had not run for three days. `cleanup-index` shared the
`nightly-pipeline` concurrency group so the two could never race on HF state. But GitHub keeps one
*pending* run per group and lets a newer arrival replace it, and the pipeline fires hourly while
taking 80-130 minutes — so there was nearly always a fresher pipeline run waiting, and the
once-a-day job was the one displaced. Last success 2026-08-24; cancelled the 25th and 26th; no run
at all on the 27th.

ADR-0071 accepted this risk explicitly and named the tell: *"if compaction starts visibly slipping,
this is the first thing to look at."* The reasoning was that compaction is idempotent and "simply
runs the next day". That holds for one displacement. It does not hold for a cadence that displaces
it every day, which is what an hourly cron against a once-daily job produces.

## Decision

**The pipeline yields to compaction, not the other way round.** The asymmetry is the whole point:
the pipeline runs 12-24 times a day and any single run is disposable, while compaction runs once
and has no second chance. The shared group encoded the opposite priority.

Three parts, and all three are needed.

**`cleanup-index` gets its own concurrency group**, so nothing can displace it.

**The pipeline stands down while a compaction is alive.** Its `scrape-plan` gate already publishes
a `run` output that every downstream job reads, so this needed no new job — only a second reason
to set it false, plus the same condition on `merge`, which was `if: always()` and would otherwise
have ignored it. `merge` is precisely the job that writes LanceDB.

**`cleanup-index` waits for any pipeline already in flight, and retries rather than giving up.**
The gate only stops *future* pipelines; one already running cannot be recalled. The wait is also
what *creates* the window it waits for: the pipeline is busy nearly all the time, so there is no
free gap to arrive into, and it is cleanup sitting there that makes every later fire stand down so
the running one drains into an empty queue. A single long wait was the first shape and was rejected
— it had to be justified by "no pipeline has ever run past 240 minutes", the same rare-case
tolerance that caused this outage. Instead the wait job tries 45 minutes and re-dispatches itself,
bounded at six attempts (~4.5 hours against a measured worst pipeline of 129 minutes).

**The delete chunk goes 512 -> 2048**, roughly halving deletion files on the eviction path
(measured 317-1,560 evictions/run: 1-4 calls at 512, exactly 1 at 2048). This divides a constant
and does not touch the fragment count that actually grows.

## What made the naive fix dangerous

Giving `cleanup-index` its own group *alone* — the one-line reading of "stop cancelling it" — would
have removed the only thing preventing data loss. `cleanup-index` uploads the description store
with `--delete "*"`, and `merge` writes `data/descriptions` too, so an overlapping run can delete
fragments a pipeline has just uploaded. Exclusion had to be re-established by the gate and the wait
before the group could be split.

## Consequences

- Compaction can no longer be starved by scheduling. It can still be *skipped* — six exhausted
  attempts exit red — but skipping is now loud and bounded rather than silent and open-ended.
- The pipeline loses a run or two a day to a compaction. At hourly cadence that is nothing.
- Both mirror-image failures are guarded. A stuck compaction cannot stand the pipeline down
  forever: the gate ignores cleanup runs older than 8 hours, which is beyond any legitimate
  compaction, and each stand-down emits `::warning::`.
- A dropped re-dispatch cannot silently end the chain: the wait job confirms its successor exists
  before exiting, and fails red if it does not.
- **Still missing: nothing alerts when compaction has not succeeded in N days.** This ran silently
  for three days and the first symptom was a rejected push, eight hours after the last publish.
  The guards above make each individual failure visible in its own run; none of them notices an
  *absence*. That belongs in `alerts.yml` and is not addressed here.
