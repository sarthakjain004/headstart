# ADR-0101: Remove the collapse guard; the grace period is the line

**Status:** accepted · **Date:** 2026-09-01 · **Supersedes:**
[ADR-0046](0046-index-collapse-guard.md) and [ADR-0055](0055-bound-the-collapse-guards-hold.md)

## Context

`plan_sync` carried two withholding mechanisms that looked like a redundant pair and were not.

- **ADR-0046's collapse guard**, bounded by ADR-0055: a per-**Board** cap. A Board that would
  lose more than `COLLAPSE_RATIO` = 25% of its indexed rows in one run drained only a quarter of
  them and withheld the rest, Boards under `COLLAPSE_FLOOR` = 20 rows exempt.
- **ADR-0083's grace period**: per-**Job**. An id absent from one scrape is Unconfirmed; it is
  evicted only if the next scrape *of that Board* misses it too.

The reason they are not redundant is the order they run in. The grace period is applied first,
and the guard measured what survived it:

```python
eligible = absent & previously if grace_on else absent
if len(ids) >= COLLAPSE_FLOOR and len(eligible) > COLLAPSE_RATIO * len(ids):
```

So every id the guard withheld had **already been absent from two consecutive scrapes of its own
Board**. The guard was not a second opinion on a transient miss — the grace period had settled
that — it was a rate limit on how fast a *persistently* short Board could shed rows. ADR-0083's
own Context says as much from the other side: it lists the collapse guard among the mechanisms
that do *not* cover the shape it was written for, because the false evictions it fixed were 0.6%
and 6.7% losses, far under a threshold designed not to fire on them.

What the guard was actually doing in production was measured over the 13 runs
`33438617010`..`33498025335` (2026-09-01). It fired on 5 of the 13, withholding 5, 9, 178, 77 and
9 rows. Every Board it caught — `recruitee:eworgmbh`, `teamtailor:eworgmbh`,
`workday:lowes/LWS_External_CS` — shows **zero flapped ids** in `scripts/eval/flap_audit.py` over
the same window: nothing it withheld ever came back. Those were genuine closures being spread
over several runs by ADR-0055's drain (79 → 34, 80 → 35, 19 → 8), not truncations being caught.
Across the window it prevented no observed false eviction and delayed correct ones.

That is not proof it never would. It is a cheap insurance policy whose payout was not observed in
13 runs, and removing it is a deliberate acceptance of the tail risk below.

## Decision

**Remove the collapse guard entirely.** `COLLAPSE_RATIO`, `COLLAPSE_FLOOR`, the ADR-0055 drain,
`SyncPlan.held`, and the `collapse guard:` log lines and run-summary entry all go. The grace
period and ADR-0053's scope exclusion are the two remaining mechanisms.

`plan_sync` loses its `first_seen` parameter, which existed only to order the drain oldest-first,
and `index.py` loses `_ids_and_stamps` with it — the sync path reads `_all_ids` again, one column
instead of two.

## Alternatives considered

**Keep the guard as it is.** The honest case for it: what the measurement in Context shows is an
insurance policy that has not had to pay out in 13 runs, not one that cannot. Its cost is small
and bounded — 5 runs in 13, at most 178 rows, drained within a few runs by ADR-0055 — and the
event it covers is low-probability and high-impact. Rejected because the cover is thinner than it
looks: it protects only against a truncation that is silent *and* repeats, and in that case it
delays the mass eviction by a handful of runs rather than preventing it, while ADR-0053 already
takes every self-describing truncation out of scope first.

**Narrow it: keep a cap, raise the trigger.** Fire only on a catastrophic loss — a much higher
ratio, or a large absolute row count as well as a ratio — so it stops slow-walking ordinary
shrinkage like `eworgmbh` while still bounding a full-Board collapse. This is the option with the
best risk-per-line, and it was put forward as the recommendation. Rejected by the person making
the call, deliberately and with the tradeoff stated: a threshold nobody can calibrate against a
real incident is a number chosen to feel safe, and the measurement offers no incident to
calibrate against.

**Make the eviction scope per-Job rather than per-Board.** Exclude only the ids a scrape could not
confirm and leave the rest of the Board in scope. Architecturally the right shape, and it would
subsume both ADR-0053 and this decision. Not attempted here: it rewrites the mechanism this ADR
is removing a piece of, and doing both in one change would leave neither measurable.

## Consequences

**A Board short the same way on two consecutive scrapes now evicts every missing row at once.**
This is the whole cost, and it is real. The residual exposure is a scrape that (a) comes back
short, (b) cannot detect that it came back short — so ADR-0053 never fires — and (c) does so
twice running. Greenhouse's silent short lists are the known example of (a)+(b), and a shard
killed before it writes its board report is another; run `33463142063` had exactly such a
budget-killed shard, and it is one of the runs where the guard withheld rows (178).

What makes that acceptable rather than reckless: the grace period already forces a truncation to
*repeat* before anything is deleted, which every measured false-eviction incident in
`docs/pipeline/2026-08-23_false-board-eviction-root-cause.md` failed to do — each was a single
isolated miss. A row wrongly evicted is also recoverable: the next scrape re-adds it, and
ADR-0050's description store means it is re-embedded from stored text rather than refetched.

**ADR-0014's outcome is restored for a Board that goes all-non-tech.** ADR-0046 knowingly
regressed this: such a Board is in scope with zero fresh ids, indistinguishable above the floor
from a scrape truncated to nothing, so its stale rows were held. They now fall out in one run,
which is what ADR-0014 specified.

**`still_waiting` in the grace-period log line has three causes, and the collapse guard was never
one of the two that remain.** An id whose Board was scraped *and was in scope* is now evicted
rather than capped, so the guard drops out. What replaces it was always there and the old wording
hid it: `index sync` subtracts an Unauthoritative Board from the scope before calling
`plan_sync` (ADR-0053), so that Board's carried-in ids take the same carry-forward branch as a
Board that sat out the slice entirely. At 63–126 Boards per run
(`docs/pipeline/2026-09-01_twelve-run-log-review.md`) it is not a rounding error, and unlike the
slice cause it has no drain. The log line and `grace_period_counts`'s docstring are corrected to
name that set; reading the old wording against new logs would attribute accretion to a mechanism
that no longer exists.

There is a third, pre-existing cause the old wording also hid: a Board scraped that emits *zero*
jobs of any kind writes no ids, so `_scraped_boards` never sees it either. It is rarer than the
other two and belongs to ADR-0023's prune rather than to sync, but it is a Board that *was* read —
so the count must not be described as "Boards we did not get to". Both corrections came out of the
review on this change rather than the analysis before it; the first draft claimed one cause and
the second claimed two.

**Watch the eviction volume for two or three runs.** The signal that this was wrong is a Board
shedding a large block of rows and re-adding them within the window — exactly what
`flap_audit.py` measures. Its acceptance bar is unchanged: already-known adds under 10%.
