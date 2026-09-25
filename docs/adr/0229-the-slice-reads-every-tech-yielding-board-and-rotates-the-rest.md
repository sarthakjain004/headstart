# ADR-0229: The Slice reads every tech-yielding Board and rotates the rest oldest-first

**Status:** accepted · **Date:** 2026-09-25 · **Amends:**
[ADR-0022](0022-tech-priority-board-ordering.md) (the head/tail split, and a tail that is now a
rotation rather than a draw) · **Relates to:**
[ADR-0027](0027-measured-scrape-cost-ledger.md) (the cost ledger whose `updated_at` now orders the
tail), [ADR-0062](0062-drain-the-description-gap.md) (the gap
quota, unchanged), [ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md) (the value gate, whose
floor this keeps), [ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (eviction counts
scrapes of a Board)

## Context

A run scraped a 20,000-Board Slice out of 153,695 Scrapable Boards: a head of the 6,000
top-scored Boards and a 14,000-Board Tail drawn at random from everything else, reshuffled every
run with no memory of when a Board was last read. Measured on 2026-09-25 against the HF cost and
priority ledgers, after the value gate's 33 Boards:

- 43,327 Scrapable Boards were Scored Boards (holding a priority score), but only 6,000 had a
  seat. The other ~37,000 had
  the same ~9.5% chance per run as the 110,335 Boards with no score.
- Sampling with replacement has a long tail. Reading 99% of the non-head Boards once took ~46
  runs (~42 h). The oldest last look was 11 days, and 25 tech-hiring Boards had gone 7+ days.
- The 20,000 was a default, not a ceiling. It cost 1,804 serial scrape-minutes, ~9 min a shard at
  the ledger's 13.8x fan-out speedup. The scrape stage took 18 min only because one Board
  (`jibe:uhs`, 976 s) set a shard's wall clock. Scraping every Scrapable Board would cost ~46 min
  a shard, inside the 60 min shard budget.

## Decision

The Slice is 80,000 Boards (`--max-boards` and `pipeline.yml`'s default), split 70/30:

- **Head:** up to 56,000 Scored Boards, score-descending, which on 2026-09-25 is all 43,327
  Scrapable ones (`EXPLORE_FRAC` 0.7 becomes `TAIL_FRAC` 0.3). If they outgrow the cap, the
  lowest-scored overflow joins the Tail and waits its turn by its last look like any unscored
  Board, and `scrape_plan` warns each run that happens. ADR-0022 began at 70/30, and a
  2026-07-27 flip to 30/70 drained a never-scraped backlog that no longer exists.
- **Tail:** the rest (~36,700), minus ADR-0062's gap quota, taken **oldest look first**.
  `pick_boards` takes `last_looked`, the cost ledger's `updated_at`. A Board with no row goes
  first, and one run's Boards tie at random. Each unscored Board is read every ~3 runs.
- **The stamp is a UTC timestamp to the second.** `updated_at` already meant "the last run that
  looked at this Board, even if the look failed", which is what a rotation needs: a failing Board
  is not re-picked every run. A date could not tell apart the ~26 runs in a day. Rows written
  before this ADR hold a bare date, which sorts as the start of its day. The value gate's
  fortnight re-check reads only the date part.
- **The value gate's 10-minute floor stays** until real shard times are measured.
- **The time limits follow the workflow header's rules.** The scrape work budget goes from 60
  to 75 min, ~3x a balanced ~25 min shard, with its step and job timeouts moving to 81 and 94.
  The planner's `_BUDGET_MIN` mirrors it. The join's hang detector goes from 40 to 75 min, ~3.4x
  the ~22 min its cost model predicts for ~3.2M scraped lines, up from ~1.9M.

Without `last_looked` (`scrape_run`'s monolith path), the tail stays a random draw.

## Consequences

Predicted from the same ledgers: ~5,100 serial scrape-minutes, ~25 min a shard, and a run that
barely lengthens. Most of a run was join, embed and merge, and new Docs grow with new postings,
not with Boards read. Every tech-yielding Board is read every run, so a closed posting on one is
evicted after two runs (ADR-0083), not after two random draws. Boards that have never yielded
tech are read every ~3 runs instead of at random.

Two paths look at a Board without stamping it, so the rotation re-picks it every run. That
is right for a Board a shard's time budget never reached. It is wasteful but rare for a Board
whose Detail pass stalled, since ADR-0209 keeps that Board's old cost row (45 stall lines across
all 15 shards on run `36133540276`). Separately, the head now seats every Board with a positive
score, and a score only decays when the Board returns jobs. So 1,181 Boards whose last complete
scrape found none hold stale scores and a head seat every run. They cost 17.6 serial minutes,
0.3% of the Slice. Both are left for a follow-up rather than widened into this change.

The unmeasured risk is the origins. Per run, Workday sees ~6,500 Boards instead of ~2,100, Zoho
~3,800 instead of ~900, and SmartRecruiters ~5,600 instead of ~1,500. Eightfold barely moves
(78 → 95), which matters because its per-origin budget is the one that broke at ~79 (ADR-0063).
The first run is the trial. Compare its shard `actual/predicted`, 429 and wall counts,
Board-error rate, join time and merge's `sync` and `update_meta` steps against run
`36133540276`: 0.8% Board errors, 17 budgets spent, join 9.8 min, `sync` 1.2 min and
`update_meta` 0.9 min. Both merge steps scale with the corpus the join hands them. Roll back by
reverting this change.

## Rejected alternatives

- **Every Scrapable Board every run.** ~78 min runs, so the top Boards are read less often than
  now, for 5-10x the per-ATS volume, spent mostly on Boards that have never yielded tech.
- **Separate scrape and index loops.** Nothing in GitHub's or HF's limits blocks it. But the
  account's 20-job pool (ADR-0025) caps a continuous scrape at ~10-12 shards, and the index side
  (~22 min) becomes the latency floor. A posting would be searchable in ~60 min against ~70 here,
  for a rewrite of state ownership, batch merging and eviction scope. Worth revisiting once the
  index side is much faster.
- **A 45k or 65k Slice.** Either is a smaller step. 45k leaves 2% of the Scored Boards' score to
  the Tail. 65k reads the unscored Boards every ~5 runs instead of ~3.
