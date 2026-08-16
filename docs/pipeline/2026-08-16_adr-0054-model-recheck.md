# ADR-0054 re-check: the makespan model survives darwinbox's browser cost

**Date:** 2026-08-16 · **Question** (from the session handoff): does the learned fan-out speedup
model (ADR-0054) still predict shard makespan now that darwinbox boards carry real-browser cost
(#141), and ahead of eightfold carrying re-sweep cost (#144)?

## Verdict: yes — within ±22%, mean +7%

Yardstick: the planner's own prediction (`predicted makespan` in each run's scrape-plan log)
against the actual slowest scrape shard (jobs API durations), over the six most recent completed
runs — all post-#141, so darwinbox browser cost is in every one:

| run | predicted | actual max shard | actual ÷ predicted |
|---|---|---|---|
| 31899073708 | 35.0 min | 31.8 min | 0.91× |
| 31904653165 | 29.6 min | 35.1 min | 1.19× |
| 31910176922 | 31.9 min | 39.1 min | 1.22× |
| 31915386128 | 34.9 min | 38.5 min | 1.10× |
| 31922347451 | 36.3 min | 40.5 min | 1.12× |
| 31925814898 | 38.1 min | 32.6 min | 0.85× |

Before ADR-0054, `actual ÷ predicted` ran 0.06–0.43× (the planner predicted serial time for a
parallel shard). The learned speedup currently sits at 7.61× over 15 shards
(`data/state/shard_speedup.csv`, updated 2026-08-16).

Two observations worth keeping:

1. **These runs are floor-bound, which makes the prediction robust.** In every run the predicted
   makespan *equals* the single-board floor — the slowest individual board (~30–38 min) owns the
   shard wall-clock, not the packed sum (Σ ≈ 1,400–1,500 min spreads to ~95 min/shard mean but
   the max shard is set by its worst board). The floor term is a direct measurement from the cost
   ledger, not a model output, so accuracy degrades only if a board's own cost drifts between
   runs. The mild under-prediction (4/6 runs actual > predicted, up to +22%) is that drift plus
   runner variance.
2. **Darwinbox's browser escalation did not disturb the model** — consistent with the #141
   observation that median board cost barely moved (the old cost was curl's failing retry cycle).
   The same is expected of eightfold's re-sweeps (#144): extra list pages only, and only on
   boards whose crawl came up short; the cost ledger's measured seconds absorb it automatically
   within a few runs. Worth one glance at eightfold board costs after ~a day of post-#144 runs.

## Confounds

Six runs, one day, same code era, same ~20k slice size — a consistency check, not a controlled
experiment. The per-run mix varies (which boards land in which shard), but the yardstick is the
planner's own per-run prediction, which cancels the mix by construction.
