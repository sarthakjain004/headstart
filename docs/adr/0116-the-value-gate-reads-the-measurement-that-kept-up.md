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

Three things make this the small fix rather than a re-plumbing:

- **The units need no reinterpretation.** `board_cost.jobs` counts *all* postings, not tech ones,
  so it cannot be substituted into a tech-per-minute threshold. As a **veto** it needs no
  conversion: tech jobs are a subset of all jobs, so a completed scrape that found no jobs found no
  tech ones either. Zero is zero in both units.
- **A zero there always means a completed scrape.** `board_cost.update` deliberately keeps the
  *previous* count for a Board whose shard was killed mid-scrape — *"a 0 there would erase what the
  last full scrape saw"* — and only ever raises its seconds. So the veto cannot fire on a giant we
  merely stopped reading, which is the one way it could have evicted a healthy Board.
- **It rides ADR-0064's existing floor.** Only Boards already over `_GATE_FLOOR_S` (15 min) are
  considered. A Board that returns nothing in 30 s is not a makespan problem, and dropping it would
  make this a value gate rather than the makespan gate ADR-0064 argues for — it would also retire
  every Board that happens to be between hiring rounds.

## Consequences

Measured against the live ledgers on 2026-09-07 (88,225 rows): gated **72 → 78**, six Boards newly
gated, **128 board-minutes** per full pass reclaimed, and **nothing previously gated is released**.
The six are exactly the Boards that set the makespan — `careers.te.com`, `jobs.l3harris.com`,
`southasiacareers.deloitte.com`, `careers-inc.nttdata.com`, `jobs.scotiabank.com`,
`corningjobs.corning.com`.

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
- **Fix `update_ledgers.priority` instead.** The right fix, and still open (above). Rejected *for
  now* because it changes what every reader of the priority ledger sees, which wants its own
  measurement pass rather than riding along with an incident fix.
- **Shorten `_GATE_RECHECK_DAYS` for Boards gated on the veto.** Rejected: a second recheck
  constant is speculative tuning, and getting the merge order right removes the need for it.
