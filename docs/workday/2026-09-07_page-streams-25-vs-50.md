# Widening Workday's listing fan-out buys nothing — measured, 2026-09-07

`_PAGE_STREAMS` stays at 25. This records the measurement so the proposal is not made a third time,
and corrects the statistic that prompted it.

## What was proposed, and why it was wrong

`docs/pipeline/2026-09-07_five-run-log-review.md` §4 ranked "widen Workday's listing-page
concurrency" third, on this:

> **workday pages** — 75 shard-runs, throughput median **2.18×**, latency median **1.00×**,
> **74× "room to widen"**

Near-linear scaling at no latency cost, on 74 of 75 shard-runs across five runs. It reads
overwhelming. It does not support the conclusion, for two independent reasons.

**1. The two widths compared are 12 and 25 — not 25 and 50.** `fanout_stats` records each batch
against *the width in force*, and a shard runs at exactly two: `spare_egress.stream_width` clamps a
**walled** group to `_WALLED_STREAM_WIDTH = 12` and leaves everything else at the resolved ceiling
of 25. So "2.1× the width" is `25 ÷ 12 = 2.08`. The finding is that **12 is narrow**, and 25 is
already what an unwalled Workday group runs at. Whether 50 beats 25 was never in the data, and
`_PAGE_STREAMS`'s own comment had asked for exactly that measurement — *"may need to diverge once
measured under real pagination load"*.

**2. The two populations are not comparable anyway.** Width 12 is what a group gets **after the
origin has walled it**; width 25 is what a healthy group gets. So the comparison is not "the same
fan-out at two widths" but "a walled fan-out against an unwalled one", and a walled group is slower
for reasons that have nothing to do with its width. "25 outperforms 12" may be no more than
"unwalled outperforms walled", which is trivially true.

The emitter's own `room to widen` verdict is computed from those two populations, so it inherits the
confound. It is a useful line — it is how `_WALLED_STREAM_WIDTH` came under suspicion at all — but
it is not a controlled A/B and must not be read as one.

## The measurement

`scripts/bench/probe_workday_detail.py` already runs the real `_post_async` path at an arbitrary
width and already probes the listing surface, so this needed running, not building.

`workday:ngc/Northrop_Grumman_External_Site` (3,791 postings), 60 listing pages per arm, widths
interleaved `25 → 50 → 25 → 50` so drift in host load cannot masquerade as a win:

| arm | pages | wall | missing | retries |
|---|---|---|---|---|
| width 25 | 60 | **5 s** | 0 | none |
| width 50 | 60 | **5 s** | 0 | none |
| width 25 | 60 | **5 s** | 0 | none |
| width 50 | 60 | **5 s** | 0 | none |

And the detail surface on the same board, 200 postings per arm: **11 s at width 25, 11 s at width
50**, 0 missing and 0 retries at both.

**Doubling the width changes nothing on either surface.** At 25 the width is already not the
binding constraint — which is exactly the third reading `fanout_stats`'s own docstring lists:
*"`streams` far below its width → the width is not what limits this fan-out; look elsewhere."*

Vantage caveat, and it cuts one way only: this is one laptop IP, while production is fifteen
runners, and Workday's limits are per-(source IP, instance). A laptop cannot prove 50 is *safe* at
production's concurrency. It can and does show 50 is not *faster*, which is enough to stop here —
there is no benefit to weigh the risk against.

## What this leaves

- **`_PAGE_STREAMS` = 25 unchanged.** No code change. Widening would add load on a third party's
  origin for a measured zero.
- **`_WALLED_STREAM_WIDTH = 12` is the constant the evidence actually questions**, and
  `stream_width`'s docstring already flags it: ADR-0081 removed its original justification *"without
  supplying a re-measured width to replace `12` — see ADR-0081's consequences before trusting this
  constant as tuned."* Still open, and still needing a controlled probe rather than the confounded
  population comparison above, because widening the fan-out against a host **that has just walled
  us** is a politeness decision before it is a throughput one. The clamp exists to back off; that it
  costs throughput is the point of it, not a defect.
- The review doc's §4 and its ranked item 3 now carry this correction inline.
- **The same confound invalidates the eightfold recommendation** (review item 7, "27 of 73 say
  narrowing is free"), read the other way round: a fan-out clamped to 12 *because the origin walled
  it* is slower per stream than a healthy one at 25, and calling that "eightfold is over-wide at 25"
  inverts cause and effect. Withdrawn with this one. Eightfold is still the most expensive ATS per
  Board by a wide margin — 40.4 s median against workday's 9.2 — so there is likely something real
  to find; the width is simply not shown to be it.
