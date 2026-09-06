# ADR-0110: Record fan-out throughput against the width in force

**Status:** accepted · **Date:** 2026-09-05 · **Relates to:** ADR-0078 (the clamp that supplies
the second operating point), ADR-0081 (which removed `_WALLED_STREAM_WIDTH`'s justification without
replacing it), ADR-0063 (the spare egress the clamp belongs to), ADR-0047 (per-origin budgets)

## Context

`spare_egress.stream_width` clamps a walled group's fan-out to `_WALLED_STREAM_WIDTH = 12`. That
constant has never been measured. Its own docstring says so: ADR-0067 justified narrowing with a
"one to three IPs per colo" figure, ADR-0081 corrected that to a deep pool with a ~100% rescue rate
across 150 shard-runs, and the docstring records that this "removes that specific justification
without supplying a re-measured width to replace `12` — see ADR-0081's consequences before trusting
this constant as tuned."

Nothing in the pipeline could settle it. A shard reported how long its Boards took (`board seconds
{p50/p90/p99/max}`), its retry classes, and its egress traffic — but never *at what width* any of
that happened. So the two widths a shard already runs at were never comparable, and the question
"should the fan-out be wider or narrower" had no answer in any log.

Asked to add a metric for exactly that decision, three scopes were put up: one aggregate line per
shard, one row per ATS, or one row per fan-out call site keyed by the width in force. The third was
chosen.

## Decision

`headstart.fanout_stats` accumulates, per `(fan-out, width)`: batches, items, item-seconds
(`busy`), and batch-seconds (`wall`). The two sites that resolve a width through `stream_width`
record against it — `BaseScraper.fan_out_async` (detail passes) and `WorkdayScraper._paginate_async`
(listing pages) — and `scrape_run._report` prints one line per row plus a comparison.

The load-bearing property is that **this needs no experiment**. ADR-0078's clamp already runs some
Boards at the ceiling and some at 12 *within one shard*, so keying on the width in force turns a
side effect of the wall into a free two-point throughput curve. No flag, no extra traffic, no A/B.

Little's Law supplies the reading. `streams = busy / wall` is the mean occupancy actually achieved:
near the ceiling every stream is busy, far below it the width is not the binding constraint at all.
Then throughput across two widths of one fan-out says which side of the knee the shard is on.

Three deliberate limits. The unit is **one fan-out batch, not the shard** — many Boards fan out
concurrently, so summing batch walls overshoots the shard's wall by design; `req/s` is the
throughput of a fan-out at that width, which is what a width decides, and the comparison is
unaffected. The verdict line is **suppressed below 50 items**, because a few Boards with three
postings each would otherwise print a confident-looking recommendation. And an ATS's detail passes
**merge into one row** — eightfold calls `fan_out_async` from two places and both land in
`eightfold details`. That is accepted: both resolve their width from the same clamp, so the row is
still honest about `(ATS, width)`, and splitting it would mean threading a label through a shared
helper for a distinction no width decision turns on.

## Consequences

The clamp becomes measurable in production rather than argued from. Two probes taken while building
this already disagree with the constant's premise — against one Workday CXS host through the
tunnel, width 25 bought **0.98x** the throughput of width 12 at 2.2x the latency, and driving the
real paginate through the module agreed at 0.88x. Both say the wider fan-out is slower.

That is deliberately **not** acted on here. Two probes of one host on one day are a reason to
instrument every shard, not to move a constant — the same standard ADR-0063 sets for an egress
opt-in, and the one freshteam (#311) and personio (#312) each failed by generalising from an
aggregate. The constant moves when the shards themselves have said so across runs.

The cost is one context manager per batch and one `time.monotonic()` per item, inside the semaphore
so a wait for a slot counts as queueing rather than stream time. A shard whose Boards are all
single-request prints nothing.
