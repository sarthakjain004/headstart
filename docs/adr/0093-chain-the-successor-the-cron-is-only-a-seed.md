# ADR-0093: Chain the successor; the cron is only a seed

**Status:** accepted · **Date:** 2026-08-28 · **Amends:**
[ADR-0071](0071-back-to-back-runs-instead-of-a-fixed-cadence.md) (on the *mechanism* that delivers
the cadence, not on the cadence itself) · **Relates to:**
[ADR-0091](0091-compaction-outranks-the-pipeline.md) (the gate whose stand-down this must survive),
[ADR-0020](0020-free-tier-deployment.md) (the free-tier deployment being paced)

## Context

ADR-0071 made the cadence back-to-back by pairing an hourly cron with the `nightly-pipeline`
concurrency group: a cron firing mid-run parks a run as *pending*, and it starts the instant the
current one ends. That worked exactly as designed and was measured doing so — over 56 intervals to
2026-08-26, **median true idle was −13 minutes**, i.e. a successor was usually already waiting
before its predecessor finished, at 18–23 runs/day against the ADR's ~19 target.

It stopped working on **2026-08-26, ~14:00 UTC**, and the failure is upstream of this repo.
Scheduled runs were no longer being *created*. Measured over the following 40 hours:

| | |
| --- | --- |
| gaps between scheduled pipeline runs | 55 min → **145** → **261** → **653** → 638 → 467 |
| `bot.yml` (`*/15`, untouched, unrelated) | degraded in lockstep, to a **679-min** gap |
| queue latency (`created_at` → `run_started_at`), every run | **0.0 min** — nothing was pending |
| `workflow_dispatch` runs in the same window | 4 of 4 started **instantly** |
| GitHub Status, Actions component | operational; no incident |
| only workflow commit near the onset (#322) | landed 08-27 13:43Z — **23 h after** it began |

Four things fall out of that table. The pipeline was not being *stood down* — a stand-down still
publishes a green no-op run, so it cannot reduce the run count. It was not concurrency queuing —
queue latency was zero and nothing was pending, meaning the group was *idle*, not contended. It
was not a change to `pipeline.yml` — `bot.yml` shares no code with it and degraded identically,
and the only nearby workflow commit postdates the onset by a day. And the runner capacity was
plainly there, because dispatch kept working while cron did not.

The likeliest cause is GitHub deprioritising scheduled events for a repo consuming ~22 jobs/run at
20–23 runs/day (≈500 jobs/day) — the hourly cron landed 2026-08-20 and the throttle appeared on
day four of sustained peak. **This is inference, not measurement.** GitHub exposes no signal for
it, and `billing/actions` needs a token scope this project does not hold. What *is* measured is
that `schedule` delivery became unreliable and `workflow_dispatch` did not.

## Decision

**Each run dispatches its own successor.** A `chain` job, `needs: [merge]`, ends every run with
`gh workflow run pipeline.yml -f chained=true`. The concurrency group then does what it always
did: the successor parks as pending and starts the moment this run ends.

The cron stays, demoted. It is now the **seed** — only a `schedule` run may start a chain — and
the **recovery path** if a chain ever breaks. It no longer paces anything.

Three guards, because a self-sustaining loop that fires on failure is a runaway by construction:

- **`!cancelled()`, not `always()`.** A run that succeeded, *failed*, or was stood down by the
  ADR-0091 gate must still hand off; a stand-down completes in ~2 min and would otherwise end the
  chain for as long as a compaction ran. A **cancelled** run must not: cancellation is either a
  human stopping the line or the 6 h ceiling, and dispatching past either is precisely the
  runaway.
- **A 20-minute floor.** Before dispatching, the job sleeps until this run is 20 min old. A
  healthy ~85-min run is long past that, so the floor costs nothing in the normal case. It bites
  only when a run ends early — the exact shape that would spin. Without it, one 45-min compaction
  window would produce ~30 dispatches.
- **A three-consecutive-failure breaker.** If the last three completed runs all failed, the job
  logs `::error::` and hands off to nobody. Three in a row is not a flake — 2026-08-02 lost 21 of
  25 runs to a single cause — and a chain would spend ~22 jobs per cycle rediscovering it. A
  stand-down cannot trip this: it publishes nothing but still concludes `success`.

An internal `chained` input marks chain-initiated runs, so an ad-hoc manual dispatch (a one-off
`max_boards=100` probe) cannot accidentally seed an endless chain of default-input runs.

## Alternatives rejected

**`workflow_run`.** The obvious built-in, and the wrong one: GitHub caps it at three chained
levels, so it cannot sustain an indefinite self-chain.

**Slow the cron and accept it.** Halving the cadence would cut consumption and might let the
throttle decay — but if GitHub is dropping scheduled events, a slower cron is dropped too. It
treats the symptom while leaving delivery in someone else's hands.

**Wait for it to recover.** Freshness *is* throughput here: the index only evicts a Job when its
Board is re-scraped and the posting is gone (ADR-0053), so at 3 runs/day the served table drifts.

## Consequences

Cadence no longer depends on GitHub delivering cron. It does now depend on the `chain` job itself
running, which the cron backstops.

**The volume that plausibly caused this is unchanged, and may not be survivable.** Restoring
~20 runs/day restores ~500 jobs/day. If that is what GitHub reacted to, this recovers the cadence
without removing the cause, and the same throttle could return against a mechanism the throttle
does not govern — or invite a firmer one. The signal to watch is whether cron delivery to
`bot.yml` recovers now that pipeline scheduling no longer depends on it; if it does not, the next
move is ADR-0071's other lever, cadence itself. ADR-0071's storage arithmetic is untouched and
still binding: ~1.86 GB of LFS per run, ~39 runs of headroom, so the in-merge squash still has to
outpace the writes.
