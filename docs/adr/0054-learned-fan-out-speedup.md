# ADR-0054: Predict the scrape makespan from a learned fan-out speedup

- Status: Accepted
- Date: 2026-08-14
- Amends: [ADR-0027](0027-measured-scrape-cost-ledger.md) — the per-Board seconds it measures are
  correct; what the planner *did with them* was not.

## Context

`scrape_plan` packs Boards onto shards by their measured scrape seconds (ADR-0027) and then
predicted the run's makespan as the packed **sum**:

```python
makespan = max(loads) / 60          # loads = per-shard SUM of board seconds
```

That is a shard's **serial** time. But `harvest.scrape_all` runs a shard's Boards through a
`ThreadPoolExecutor` (`cores*4`, capped at 64), so the wall clock is the sum divided by whatever
concurrency the run achieves. The planner predicted serial against a parallel shard, and nothing
ever divided.

Measured over the twelve pipeline runs reviewed on 2026-08-14, the error is consistent and large:

| Run | Predicted (Σ) | Slowest single Board | Actual slowest shard |
|---|---|---|---|
| 31738892152 | 120.8 min | 29.9 min | **42.6 min** |
| 31756176995 | 119.9 min | 35.3 min | **41.6 min** |

`actual/predicted` ran 0.06–0.43x across all twelve. The consequence was not a bad schedule — the
pack itself is fine — but a **dead warning**: every run emitted

> `predicted makespan ~120.8 min exceeds the 60 min shard budget — shards matching their
> prediction will bank partials`

and no shard has ever banked a partial. A warning that fires unconditionally cannot signal the run
where it is true, which is precisely the run that matters.

Note the planner already computed a far better predictor and discarded it: the single-Board floor
(29.9 / 35.3 min) is within ~30% of actual, where the sum is 3x out.

## Decision

Predict `max(slowest Board on the shard, serial / speedup)`, where **speedup is measured, not
assumed**, and persisted as `data/state/shard_speedup.csv` (`headstart.ingest.shard_speedup`).

The divisor cannot be the nominal worker count. Nominal is 16 on a hosted runner; the observed
speedup is ~2.8x, because per-host politeness, ATS rate limits and one straggler Board eat most of
the theoretical parallelism — and how much they eat shifts with the slice's ATS mix. So it is an
EWMA over `serial_minutes / wall_clock_minutes`, blended each run by `scrape_join` from the shard
reports it already aggregates. Same shape as the sibling ledgers (`board_cost`, `board_priority`):
an EWMA, an ISO stamp, a missing file degrading to the old behaviour, riding the same HF state
round-trip. One row, because this measures the fan-out, not any Board.

Two properties are load-bearing, and both were review findings against the first draft:

**The ratio is measured against the packed serial sum, never against the shard's own prediction.**
The obvious source — the shard report's `predicted_minutes` — is the number this model *produces*.
Feeding `predicted/actual` back makes the estimate chase its own tail and settle at
`sqrt(true_speedup)`: 1.69x against a true 2.86x, permanently wrong and wrong in the direction that
looks converged. The planner therefore ships `per_shard_serial_minutes` **beside**
`per_shard_minutes`, and the join measures against the former.

**A shard killed by the budget is never blended.** Its wall clock measures the budget, not the
work, so its ratio is inflated — 120 min packed, killed at 60 having done half, reports 2.0x
against a real ~1.0x. Blending it teaches a speedup the fan-out never achieved and silences the
budget warning in the one situation the warning exists for. `ratios_from_reports` drops them on
`killed_by_budget`.

The floor is per-shard, not global: `max(costs)` over the whole slice would over-predict every
shard that does not hold the slowest Board.

## Consequences

- The budget warning becomes meaningful. On the 2026-08-14 numbers the prediction moves from
  120.8 min to ~42 min against a 42.6 min actual, so it stops firing — and will fire when a plan
  genuinely packs past 60 minutes.
- Cold start is a no-op: `DEFAULT = 1.0` reproduces the old serial prediction exactly, so the
  first run after this lands is no worse than the behaviour it replaces and never *under*-predicts
  on no evidence. It self-replaces after one run.
- `plan.json` gains `per_shard_serial_minutes`; the shard report gains `serial_minutes`. Both are
  read with the existing "absence is not an error" discipline, so an older plan degrades quietly.
- The speedup is a *fleet* average. A shard whose mix is unusually serial (one huge Board, or an
  ATS being rate-limited hard) is still under-predicted; the per-shard floor is what protects that
  case, not the ratio.
- **Not addressed:** the straggler itself. One 42-minute Board sets the makespan no matter how well
  the pack balances, and `successfactors:jobs.lidl` (~24,000 jobs, 35-40 min) does exactly this
  every run. Predicting it accurately is not the same as fixing it; in-run rebalancing or splitting
  a Board across shards is the lever there, and neither is in scope here.
