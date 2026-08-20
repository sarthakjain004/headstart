# ADR-0077: Fan-out width narrows once the origin has walled, and only once it has

**Status:** accepted · **Date:** 2026-08-20 · **Amends:**
[ADR-0047](0047-pace-against-the-origin.md) (whose fixed per-scraper widths become ceilings rather
than the width in use) · **Relates to:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the wall registry this reads),
[ADR-0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) (why a working spare
egress does not earn the wide width back),
[ADR-0076](0076-a-lost-page-is-a-truncation-until-most-of-them-are.md) (what a lost page costs once
the width has already produced it)

## Context

`BaseScraper.fan_out_async` resolved its multiplexing width from a chain that is static end to end:
the explicit argument, then `HEADSTART_H2_STREAMS`, then the scraper's `detail_streams` /
`detail_workers`, then `_DEFAULT_H2_STREAMS`. Nothing in it knows anything about the shard it is
running on. A shard whose origin has already refused it fans out exactly as wide as one the origin
is still serving, which is wrong by construction rather than by tuning.

Run 32249345870 measured the cost. Four of fifteen shards failed their WARP install and had no
spare egress at all; those four absorbed **75,043 of the run's 94,110 rate-limit retries — 80% of
them on 27% of the shards**, at zero rotations each, while the other eleven took 790–2,342 apiece.
The install fix (#190) makes an undefended shard rarer, not impossible.

Two facts bound what an answer can look like:

1. **There is no cheap eager signal.** `spare_egress.walled_groups()` is already computed for other
   reasons and costs a set lookup, but it is empty until a wall has actually happened this run.
   `proxy_url()` *could* answer before the first wall, but only by dialling WARP — on every shard,
   including every shard that would never have needed it. That inverts today's "never dial unless
   walled" contract and puts a connect on the critical path of runs that meet no wall at all.
2. **ADR-0067 already measured that rotation is not a budget.** A working spare egress supplies one
   to three IPs fixed by the runner's colo. So "does this shard have a spare egress" is not the
   question that decides the width — a shard that can rotate reaches the wall more slowly, it does
   not get a larger allowance.

## Decision

**One function, `spare_egress.stream_width(group, ceiling)`, narrows a caller's resolved width to a
stopgap once `group` has walled this run. Before the first wall, and forever for a group that never
walls, it returns `ceiling` unchanged. It only ever narrows.**

- **Reactive within a run, deliberately.** The first wall is the signal. Requests before it are as
  wide as they were; requests after the origin has already said no are not. Nothing here dials, so
  a run that meets no wall pays nothing and behaves byte-for-byte as before.
- **Two states, not three.** It does not ask whether the walled group *has* a spare egress, per
  ADR-0067 above. That also keeps it a pure read of state, which is what makes it dial-free.
- **The stopgap is 12, and it is extrapolated.** ADR-0047's benchmark against a live origin puts
  width 12 at 47.9% loss for 17.6 minutes on the worst shard, against 49.9% for 9.7 minutes at 25
  and 24.9% for 30.6 minutes at 4 — 12 is where narrowing still buys something and the wall-clock
  still fits a 60-minute shard. That table is **Eightfold bare GETs**; the Workday pagination this
  now clamps is a POST with a JSON body against a different origin, and ADR-0047 says of its own
  row that "one production run should be read before moving it". Re-measure with
  `scripts/bench/probe_eightfold_throttle.py`'s method before treating 12 as settled.
- **`group` is whatever `_egress()` names**, so the clamp keys on exactly the group the requests
  themselves are metered under and cannot drift from `egress_fallback_on`. A scraper that never
  opted into the fallback passes None and is untouched.
- **Applied at two call sites, on purpose.** `fan_out_async` clamps its resolved concurrency;
  `workday._paginate_async` clamps its own semaphore. That duplication is not resolved here — see
  below.

### Rejected

- **Dial `proxy_url()` eagerly and size the width from the answer.** The only design that can be
  right on the *first* request of a run. It buys that by paying a WARP connect on every shard,
  including shards that meet no wall, and ADR-0067 says the answer would barely change the width
  anyway. Rejected on both counts.
- **Narrow every shard unconditionally.** Simple, needs no signal, and pays the wall-clock on the
  eleven shards of fifteen that did not need it. ADR-0047 chose 25 over 12 precisely because seven
  more minutes on the worst shard is real money against a 60-minute budget.
- **Collapse `_paginate_async` into `fan_out_async` and clamp once.** The right end state, and not
  reachable from here: ADR-0076 has `_paginate` decide a board's verdict from *which* pages failed,
  while `fan_out_async`'s contract swallows every exception into a default. Reconciling those two
  is its own change, and doing it under a width fix would hide it.

## Consequences

**An undefended shard degrades to slow rather than lossy — which is the trade, not a free win.**
Narrowing converts loss into wall-clock, and that lands on the shards already having the worst run.
Whether it fits the 60-minute budget is exactly what the next production run has to answer; the
`origin returned {status}` warning `mark_walled` already logs, once per group per shard, is what
says which shards narrowed.

**Re-measuring 12 needs a harness that cannot wall itself.** `scripts/bench/probe_eightfold_throttle.py`
passes no `egress_group`, so nothing in it reaches `mark_walled` and its width arms still measure
the width they set. That is luck, not design: the moment a benchmark routes through `_egress()`,
every arm above 12 silently collapses to 12 and the table it produces is a table of one number.

**Nothing narrows until something walls.** A shard that is over-wide but never quite refused keeps
its full width, and the first N requests of a walled shard are still made at the old one. That is
the price of not dialling, and it is a real gap rather than a rounding error: the narrowing arrives
after the damage that revealed it.

**The two fan-outs still exist separately.** `fan_out_async` and `workday._paginate_async` now share
a width policy but not a code path. The duplication remains a follow-up.
