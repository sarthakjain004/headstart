# ADR-0116: The value gate reads the measurement that kept up, not the one that froze

**Status:** accepted · **Date:** 2026-09-07 · **Amends:**
[ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md) · **Relates to:**
[ADR-0027](0027-measured-scrape-cost-ledger.md) (the cost ledger, the half that stays current),
[ADR-0022](0022-tech-priority-board-ordering.md) (the priority ledger, the half that freezes),
[ADR-0115](0115-one-user-agent-identifies-and-hosts-constrain-its-shape.md) (the incident that
exposed this)

## Context

ADR-0064's gate drops a Board whose measured hour buys too little tech to be worth a shard's
makespan:

```python
tech_per_min = scores.get(key, 0.0) / (row.seconds / 60)
if tech_per_min < _GATE_MIN_TECH_PER_MIN:   # 2.0
```

The numerator comes from `board_priority.csv`, the denominator from `board_cost.csv`. **They are
kept on different clocks, and only one of them keeps up.**

`update_ledgers.priority` builds its snapshot from the rows in the scraped jobs file:

```python
snapshot_boards = {board_of(j["id"]) for j in iter_jobs(args.jobs)}
```

A Board that is scraped and returns **nothing** contributes no row, so it is absent from that set,
so `update_priority` takes its documented branch — *"boards absent from the snapshot carry their
row unchanged — a partial harvest must not decay what it didn't look at"*. That rule is right: a
Board nobody scraped must not be punished for it. But at this layer a Board that was scraped and
found empty is **indistinguishable** from one that was never scraped, so its score is carried
forever while its cost row is rewritten every run.

The ratio therefore climbs without limit on precisely the Boards this gate exists to catch. **The
collapse the gate is for is what blinds it to one.**

Measured 2026-09-07 across five consecutive runs. `successfactors:careers.te.com`:

| ledger | row |
|---|---|
| cost | `1631s, jobs=0, 2026-09-07` — rewritten every run |
| priority | `score=171.7, last_tech_jobs=1, 2026-09-04` — three days cold |

`171.7 / 27.18 = 6.32` tech/min, comfortably clear of the 2.0 threshold, while the Board returned
**0 jobs in every one of those five runs** and set the entire scrape stage's makespan doing it. It
was one of 102 such Boards, and the gate held every one of them (ADR-0115).

## Decision

**A measured zero vetoes the ratio.** When the Board's own cost row says its last completed scrape
returned no jobs, its tech-per-minute is zero regardless of what score the priority ledger still
remembers.

It was meant to be a one-expression change and it is not, because the field it reads had to be
made trustworthy first. Three things shape it:

- **The units need no reinterpretation.** `board_cost.jobs` is not tech jobs — it is the count of
  *fresh* postings the scrape returned, deduped against the ids already seen in that shard — so it
  cannot be substituted into a tech-per-minute threshold. As a **veto** it needs no conversion:
  tech jobs are a subset of all jobs, so a scrape that found no jobs found no tech ones either.
  Zero is zero in every one of those units.
- **A zero has to *mean* zero first, and it did not.** The first draft of this ADR asserted that a
  `jobs` of 0 always came from a completed scrape, on the strength of `board_cost.update`'s
  unfinished branch. **That was false, and two independent reviews caught it.** `harvest` sets
  `n_fresh = 0` *before* the try and records it whatever happens, so a 0 meant four different
  things: a genuinely empty Board, a scrape that **raised**, a first-ever budget kill, and a
  healthy scrape whose every id was already seen this shard. Board errors run 19-40 a run, so a
  veto on that field would have sidelined any giant that failed once for a fortnight — the exact
  hazard `harvest.run_one`'s own comment already names: *"the value gate would drop it forever, on
  a Board that failed instantly."*

  So the ledger had to be made honest before the gate could read it. `ShardCost` now carries
  `errored` beside `unfinished`; neither an errored nor an unfinished run may overwrite a count a
  complete run learned; and where no complete run ever has, `BoardCost.jobs` is **None** rather
  than 0 — the same empty-CSV-field idea `liveness.py` already uses for an unknown count. A 0
  reaching the gate is now a finding, and None falls through to the ratio.
- **It rides ADR-0064's existing floor.** Only Boards already over `_GATE_FLOOR_S` (15 min) are
  considered. A Board that returns nothing in 30 s is not a makespan problem, and dropping it would
  make this a value gate rather than the makespan gate ADR-0064 argues for — it would also retire
  every Board that happens to be between hiring rounds.

## Consequences

Measured against the live ledgers on 2026-09-07 (88,225 rows): **+6 Boards gated, 128.2
board-minutes** per full pass reclaimed, **nothing previously gated released**. The *delta* is the
claim; the absolute count moves with the ledger between runs (72 → 78 on one pull of it, 70 → 76 on
another an hour earlier), so quoting an absolute here would be stale by the time it is read.
The six are exactly the Boards that set the makespan — `careers.te.com`, `jobs.l3harris.com`,
`southasiacareers.deloitte.com`, `careers-inc.nttdata.com`, `jobs.scotiabank.com`,
`corningjobs.corning.com`.

**Residual case, named rather than solved.** A Board whose every posting id was already seen
earlier in the same shard records a real 0 from a healthy scrape, because `harvest` counts *fresh*
ids. That is a true duplicate contributing nothing new, so gating it is defensible — but which of
the pair gets gated depends on scrape order. ADR-0111 resolves duplicate Boards upstream and the
14-day recheck bounds the cost, so this is documented rather than special-cased.

**The interaction with ADR-0115 is the part to understand before reading those six as a win.** They
returned zero because of the User-Agent denylist, which is now fixed. Once any run scrapes them
successfully their `jobs` becomes non-zero and this veto stops applying — the gate is reading a
symptom that has already been cured. If instead this change lands while their rows still read
`jobs=0`, the gate stops them being scraped, so their rows never refresh, and ADR-0064's
`_GATE_RECHECK_DAYS = 14` becomes the only way out: fourteen days of a Board that works. That is
the one-way-door risk ADR-0064 already names, arriving through a new door.

**So the ordering is load-bearing, not incidental:** this must land *after* a run has re-measured
those Boards with the fixed agent. Verified before merge rather than assumed.

The deeper defect is left standing, deliberately, because fixing it is a bigger change than this
one and wants its own evidence: `update_ledgers.priority` still cannot tell "scraped, found
nothing" from "not scraped". Until it can, every consumer of `board_priority.csv` is reading scores
that silently freeze on collapse — this ADR fixes the one consumer where that was measured to cost
something. The honest fix is for the priority stage to take the *attempted* Board set (the cost
ledger already knows it) and decay a Board that was scraped and came back empty, while still
carrying one that was never reached.

## Alternatives considered

- **Substitute `row.jobs` for the score outright.** Rejected: `jobs` is total postings and the
  threshold is in tech jobs per minute, so the gate's calibration — measured against a real
  distribution in ADR-0064 — would silently change meaning.
- **`min(score, k · row.jobs)`.** A softer veto that also caps an inflated score on a Board whose
  yield merely *fell*. Rejected as tuning without evidence: no measurement says what `k` should be,
  and the zero case is the one that was actually observed to cost 631 board-minutes a run.
- **Trust `board_cost.jobs` as it was.** Rejected on review: the field meant four things, and a
  guard resting on a value with four meanings is the failure mode CLAUDE.md cites from #77.
- **Fix `update_ledgers.priority` instead.** The right fix, and still open (above). Rejected *for
  now* because it changes what every reader of the priority ledger sees, which wants its own
  measurement pass rather than riding along with an incident fix.
- **Shorten `_GATE_RECHECK_DAYS` for Boards gated on the veto.** Rejected: a second recheck
  constant is speculative tuning, and getting the merge order right removes the need for it.
