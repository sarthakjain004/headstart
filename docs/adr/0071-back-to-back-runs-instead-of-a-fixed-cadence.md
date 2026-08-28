# ADR-0071: Back-to-back runs instead of a fixed cadence

**Status:** accepted · **Date:** 2026-08-20 · **Amended by:**
[ADR-0093](0093-chain-the-successor-the-cron-is-only-a-seed.md) (on the *mechanism* that delivers
the cadence — GitHub stopped delivering this repo's cron reliably on 2026-08-26, so each run now
dispatches its own successor; the cadence target and the storage arithmetic below stand) ·
**Relates to:**
[ADR-0020](0020-free-tier-deployment.md) (the free-tier deployment this cadence serves),
[ADR-0025](0025-parallelize-nightly-pipeline.md) /
[ADR-0026](0026-parallelize-nightly-scrape.md) (the fan-out whose duration now sets the cadence),
[ADR-0028](0028-ingest-package.md) (which recorded the run as "2-hourly" — superseded on cadence
only, not on package layout)

## Context

The pipeline ran on `cron: 30 1-23/2 * * *` — every two hours at :30. That interval was chosen as
a cadence in its own right, and it left the pipeline idle for a large fraction of every cycle.

Over the 11 **successful** scheduled runs to 2026-08-19 (cancelled runs excluded — an early draft
of this ADR averaged a 0-minute cancelled run into the duration and understated the mean by 6 min):

| | value |
| --- | --- |
| run duration | mean **74.1 min** (min 55.3, max 87.3; only 1 of 11 under 60) |
| start-to-start | mean **120.8 min** (min 90.0, max 171.7) |
| idle between runs | mean **46.7 min** |
| effective throughput | **11.9 runs/day** |
| back-to-back ceiling | **19.4 runs/day** |

So 39% of every cycle was spent waiting on the clock rather than on work. Because the index only
evicts a Job when its Board is re-scraped and the posting is gone (ADR-0053), and because the
scrape slice is a 20,000-Board sample of ~59,790 live Boards (85,631 **Scrapable Boards** as of
2026-08-28 — the ratio has widened, which strengthens this argument rather than weakening it; the
term is defined in CONTEXT.md §Counting Boards), throughput *is* freshness: more runs
per day means each Board is revisited sooner and dead postings leave the index faster.

The mechanism to close the gap was already present and unused. `concurrency: {group:
nightly-pipeline, cancel-in-progress: false}` serializes runs on the dataset state; a run that
fires while another is in flight does not race and does not die — it parks as *pending* and starts
the moment the current one finishes. The 2h cron was simply too sparse to keep a successor queued.

## Decision

**Set the cron to hourly at :30 and let the concurrency group convert it into back-to-back
execution.**

```yaml
- cron: "30 * * * *"   # was: 30 1-23/2 * * *
```

The interval is deliberately shorter than the mean run. At 60 min against a 74.1 min run, a
successor is already pending when the current run ends, so the effective cadence becomes **run
duration**, not the cron. The hourly figure is a queueing device, not a target rate: runs cannot
overlap, so a slow run defers its successor rather than stacking on it. **~19 runs/day is the
expectation, not 24.** `30` is kept as the minute so the compulsory 21:30 nightly retains its slot.

### The dataset squash moves into the merge job

This is the half of the decision that took two review rounds to get right, and it is the part
worth reading.

Storage, not compute, is this workflow's binding constraint: each run rewrites ~1.86 GB of LFS
blobs and HF retains every revision. The reclaim — `super_squash_history` — therefore has to
outpace the writes. It ran as its own daily workflow, sharing `group: nightly-pipeline` so it could
never squash mid-upload.

**That sharing is exactly what the new cadence breaks.** GitHub permits only **one pending run per
group** and cancels the older when a newer arrives. The squash does not start when it fires:
measured across five runs it is created ~04:2x and sits **pending 37-55 minutes**, waiting out the
pipeline run in flight, starting 05:02-05:24. Under the old 2h cron the next pipeline fire was ≥90
min away, so it always won the queue. Under an hourly cron a fire lands inside that window and
cancels it — doubling the storage burn while removing the only thing that reclaims it.

Reserving a hole in the pipeline cron was implemented and then **rejected on measurement**. A hole
at 04:30-06:30 does protect the squash, but `cleanup-index` — the other workflow in the group — is
created 06:42-07:36, *always after such a hole closes*, with job starts as late as 08:17. Covering
GitHub's 42-96 min schedule lateness for both would need a hole running to ~08:30, costing a
~6-hour gap. That is the throughput this ADR exists to win, so the hole approach is a dead end
rather than a tuning problem.

**So the reclaim moved inside the `merge` job**, which already holds the concurrency group and
therefore races nothing by construction. It runs after the upload and is `continue-on-error`:
reclaiming space must never be able to lose a run's data. It fires on a **threshold**
(`used_storage` over 40 GB) rather than a schedule, because at ~1.86 GB/run the quota is the thing
that actually matters and any run-count or day-count proxy drifts as run size or cadence does.

The threshold is sized to hold the reclaim at **roughly daily**, which is what the schedule it
replaces did: live files settle at ~3.5 GB after a squash and each run adds ~1.86 GB, so
`(40 - 3.5) / 1.86 = ~19.6 runs`, and at the ~19.4 runs/day ceiling that is ~24 h. This is worth
stating because the first revision used 55 GB, which measures out at ~34 h — *less* often than the
daily schedule it replaced, and so a regression dressed as an improvement. A threshold is only
equivalent to a schedule if someone does that arithmetic.
`squash-dataset-history.yml` loses its schedule and stays as a manual escape hatch.

### `cleanup-index` keeps the shared group, and may occasionally be displaced

> **Superseded 2026-08-27 by [ADR-0091](0091-compaction-outranks-the-pipeline.md).** The exposure
> this section tolerated is exactly what fired: `cleanup-index` was displaced on three consecutive
> days, `_deletions/` passed HF's 10,000-file directory limit, and every pipeline upload was
> rejected for eight hours. The tell named below — "if compaction starts visibly slipping, this is
> the first thing to look at" — was correct, but nothing was watching for it. `cleanup-index` now
> has its own group and the pipeline stands down for it.

`cleanup-index` compacts the whole table — ~1.9 GB rewritten — which is precisely why it does not
live in the 2-hourly run. It cannot move into `merge` for the same reason, so it keeps the shared
group and, under the hourly cron, a pending `cleanup-index` can be displaced by the next pipeline
fire. This is **tolerated, not solved**: compaction is idempotent and simply runs the next day, and
its cost is deferred orphan-fragment reclaim rather than lost data. Measured exposure is real
(0 cancellations across 17 runs to date, but its pending window overlaps the new fire schedule),
so if compaction starts visibly slipping, this is the first thing to look at.

### On "treat the 2 hour as the minimum interval"

The request that prompted this carried an ambiguity worth recording, since the readings are
opposites. "Minimum interval" can mean *runs must be ≥2h apart* (a rate cap, which contradicts
back-to-back) or *runs must happen at least every 2h* (a frequency floor). It was read as the
latter — the 2h figure describes the status quo being replaced. No explicit floor is implemented,
and with the hole removed none is needed: the hourly cron is strictly more frequent than 2h in
every window. If the intent was the rate cap, this ADR is the wrong decision and should be
revisited rather than patched.

## Alternatives considered

- **Self-dispatch from the final job** — `merge` calls `gh workflow run pipeline.yml`. Exact
  back-to-back regardless of run duration. Rejected on cost of ownership: `GITHUB_TOKEN`
  deliberately cannot trigger a workflow recursively, so it needs a PAT to create, store and
  rotate. Worth revisiting if run durations fall well below 60 min, where the cron approach
  degrades and this does not.

  **Adopted 2026-08-28 by [ADR-0093](0093-chain-the-successor-the-cron-is-only-a-seed.md), and the
  reason for rejecting it was wrong.** `workflow_dispatch` is one of the two events the default
  `GITHUB_TOKEN` *may* still trigger, so no PAT is involved and there was no cost of ownership to
  reject it on. The revisit trigger guessed at here was also the wrong one: run durations never
  fell below 60 min. What forced the change was GitHub ceasing to deliver this repo's scheduled
  events at all.
- **Half-hourly cron.** Closes the gap even after a fast run, but at 48 fires against ~19 runs it
  cancels ~29 pending runs a day, swamping `gh run list` and `scripts/runlog/`'s `--latest`.
- **A hole in the cron reserving a maintenance window.** Implemented, measured, rejected — see
  above.
- **Leaving the cadence alone.** Rejected: the idle time is pure loss, and the freshness it costs
  is the product's core claim.

## Consequences

**Throughput rises ~63%**, from 11.9 to ~19.4 runs/day, and the ceiling is now run duration.

**Storage headroom roughly halves, and the merge-job reclaim is what holds it.** ~54 runs of quota
from empty, but only **~39 from the 26.6 GB already used** on 2026-08-19 — about **2 days** at
19.4 runs/day. The threshold reclaim checks every run, so it cannot silently lapse the way a
displaced scheduled workflow could. If that step starts failing, the quota is ~2 days from a
mid-run push failure that leaves the dataset half-written.

**Cancelled runs become a normal sight.** 24 fires against a ~19.4-run ceiling means roughly
**4-5 cancelled pending runs a day**. A cancelled run here is the concurrency group working, not a
failure — but `gh run list` and `scripts/runlog/`'s `--latest` will surface them, so filter on
conclusion before measuring anything.

**Cost tracking should use runs/day, not the cron.** Anything reasoning about spend or storage from
"every 2h" is now wrong. The cron no longer describes the cadence; run duration does, and it moves
with scrape volume and egress health.

**Per-run cost pressure now translates directly into throughput.** Because the cadence is
duration-bound, anything that shortens a run — the floor-bound stragglers of #194/#195, a faster
embed — buys extra runs per day rather than more idle time. Under the 2h cron, speeding a run up
bought nothing.
