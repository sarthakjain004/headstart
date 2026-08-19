# ADR-0068: Back-to-back runs instead of a fixed cadence

**Status:** accepted · **Date:** 2026-08-19 · **Relates to:**
[ADR-0020](0020-free-tier-deployment.md) (the free-tier deployment this cadence serves),
[ADR-0025](0025-parallelize-nightly-pipeline.md) / [ADR-0026](0026-parallelize-nightly-scrape.md) (the fan-out whose
duration now sets the cadence), [ADR-0028](0028-ingest-package.md) (which records the run as
"2-hourly" — superseded on cadence only, not on package layout)

## Context

The pipeline ran on `cron: 30 1-23/2 * * *` — every two hours at :30. That interval was chosen as
a cadence in its own right, and it left the pipeline idle for a large fraction of every cycle.

Measured over the twelve scheduled runs to 2026-08-19:

| | value |
| --- | --- |
| run duration | mean **68 min** (min 0, max 87) |
| start-to-start | mean **120 min** (min 90, max 172) |
| idle between runs | mean **52 min** |
| effective throughput | **12.0 runs/day** |
| back-to-back ceiling | **21.2 runs/day** |

So 43% of every cycle was spent waiting on the clock rather than on work. Because the index only
evicts a Job when its Board is re-scraped and the posting is gone (ADR-0053), and because the
scrape slice is a 20,000-Board sample of ~59,790 live Boards, throughput is directly freshness:
more runs per day means each Board is revisited sooner and dead postings leave the index faster.

The mechanism to close the gap was already present and unused. `concurrency: {group:
nightly-pipeline, cancel-in-progress: false}` serializes runs on the dataset state; a run that
fires while another is in flight does not race and does not die — it parks as *pending* and starts
the moment the current one finishes. Nothing had to be built. The 2h cron was simply too sparse to
keep a successor queued.

## Decision

**Set the cron to hourly at :30 and let the concurrency group convert it into back-to-back
execution.**

```yaml
- cron: "30 * * * *"   # was: 30 1-23/2 * * *
```

The interval is deliberately shorter than the mean run. At 60 min against a 68 min run, a
successor is already pending when the current run ends, so the effective cadence becomes **run
duration**, not the cron. The hourly figure is a queueing device, not a target rate: runs cannot
overlap, so a slow run defers its successor rather than stacking on it, and the ceiling stays at
duration. 21 runs/day is the expectation, not 24.

`30` is kept as the minute so the compulsory 21:30 nightly retains its slot.

## Alternatives considered

- **Self-dispatch from the final job** — the `merge` job calls `gh workflow run pipeline.yml`,
  with the 2h cron kept as a floor. This is exact back-to-back regardless of run duration, and
  produces no cancelled runs. Rejected on cost of ownership: `GITHUB_TOKEN` deliberately cannot
  trigger a workflow recursively, so it needs a PAT secret to create, store and rotate — real
  ongoing burden for the ~27 idle minutes it buys over the hourly cron on a fast run. Revisit if
  run durations fall well below 60 min, where the cron approach degrades and this does not.
- **Half-hourly cron.** Closes the gap even after a fast run, but at 48 fires against ~21 runs it
  cancels roughly 27 pending runs a day. Those appear in the run history and would swamp
  `gh run list` and the `scripts/runlog/` tooling's `--latest`. The observability cost is not
  worth the marginal minutes.
- **Leaving the cadence alone.** Rejected: the idle time is pure loss, and the freshness it costs
  is the product's core claim.

## Consequences

**The daily squash is now load-bearing, and this is the failure mode to watch.** Storage, not
compute, is this workflow's binding constraint: each run rewrites ~1.86 GB of LFS blobs and HF
retains every revision, so the 100 GB quota buys ~54 runs. At 12 runs/day that was ~4.5 days of
headroom; at ~21 it is **~2.5 days**. That is survivable only because `squash-dataset-history`
already runs **daily** at 04:00 UTC (moved from weekly on 2026-08-14 for the same reason), holding
one day's burn at ~39 GB against the quota. Verified before this change: `used_storage` was
26.6 GB of 100 GB, and the last five daily squashes all succeeded. **If that squash is disabled,
fails repeatedly, or moves to a longer interval, this cadence must come down with it** — a push
against a full quota fails mid-run and leaves the dataset half-written.

**Cancelled runs become a normal sight, at low volume.** When a run exceeds 120 minutes, two crons
fire during it and the older pending run is replaced. Historically that is the 124-min tail, so
expect a small number per week rather than per day. A cancelled run is the concurrency group
working, not a failure.

**Cost tracking should use runs/day, not the cron.** Anything reasoning about spend or storage
from "every 2h" is now wrong. The cron no longer describes the cadence; run duration does, and it
moves with scrape volume and egress health.

**Per-run cost pressure now translates directly into throughput.** Because the cadence is duration
bound, anything that shortens a run — the floor-bound stragglers of #194/#195, a faster embed —
buys extra runs per day rather than more idle time. That is a change in kind: under the 2h cron,
speeding a run up bought nothing.
