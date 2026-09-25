# ADR-0222: Trends record each Board's opened and closed jobs, not only its net

**Status:** accepted · **Date:** 2026-09-25 · **Extends:**
[ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md) (the Board-delta
ledger), [ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (company
trends), [ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) (Hot) ·
**Relates to:** [ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (the id →
family snapshot), [ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (Unconfirmed),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (Unauthoritative Boards),
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) /
[ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) /
[ADR-0221](0221-a-refit-is-a-step-in-one-trends-history.md) (counting changes)

## Context

Trends and Hot showed only a company's **net** change in open tech jobs. A company that opened
five jobs and closed five read flat.

A local probe measured how often that matters. HF state was pulled on 2026-09-25, and the window
was Sep 18 → 25 05:15 UTC. Of 2,791 companies with at least 20 openings at the window start, 695
read "about flat" (|net| < 1%). Between 105 and 247 of those opened 5 or more jobs that week.
Amazon read net +17 while it opened 914–1,532. Median weekly turnover was 6.6–14.5%, and p90 was
20–36%. 64 companies read falling while opening 20 or more. Every figure is a range, and a lower
bound, because nothing recorded when a job id arrived or left. The probe could only bracket it,
from the `new` level (`first_seen` inside the week) and the replayed counting changes.

`role_trends` already held what a per-id answer needs, at the right moment. It runs after
`index sync` and `index prune`. It reads every served row's id, `first_seen`, Board, family, band
and ATS. It also keeps last tick's `id → family` for every tech row in
`data/state/role_assignments.parquet` (ADR-0057).

## Decision

**Where it is computed.** `role_trends` diffs last tick's served tech ids against this tick's.
The pure module `ingest/job_turnover` books each id that differs as exactly one of four metrics,
keyed like stock by `(board, family, band, ats)`:

| metric | an id that… |
|---|---|
| `opened` | is new to the snapshot, has `first_seen` after the previous tick, and sits on a Board the previous tick counted |
| `closed` | left because `sync` evicted it, which is its second consecutive absence |
| `recounted_in` / `recounted_out` | moved for a reason that is not hiring: a found Board's backlog, a `prune` removal (duplicate or off-Board), a served row the classifier moved into or out of tech, or a change of family, band or Board key |

Per key and tick, `Δstock = opened − closed + recounted_in − recounted_out` exactly, and a test
pins it. The module is named for **turnover**, because "flow" is already ADR-0051's word for `new`.

- The snapshot gains `board`, `band` and `ats` columns and an `as_of` stamp. Turnover reads it at
  any series version (`load_placements`), because a new classifier head changes families, not
  which ids were served. A snapshot written before this ADR has no placements, so turnover starts
  on the tick after the first one that writes them.
- The snapshot is now written *before* the tick's delta file. A snapshot left stale after flows
  were written would book them again on the next tick. This order can lose one tick's flows
  instead, which under-reports once.
- `index prune --pruned-ids data/run/pruned_ids.txt` hands every id prune removed to
  `role_trends`. `data/run/` is a hand-off inside one merge job, and no upload takes it.

**Where it is stored.** The four metrics are rows in the per-tick delta file that already exists,
beside `stock` and `new`, with `delta` holding the tick's count. This adds no files; the delta
directory already gains about 27 files a day. Every reader that summed `delta` across metrics now
filters to the level metrics: `_recover_board_counts`, and the Space's `_replay_span` and
`_family_weights`.

**When a job counts as closed.** At eviction. A closure is never reversed and lands on the tick
where stock falls, so the identity holds. The cost is lag: a closure lands one scrape of its Board
late.

**Unauthoritative Boards.** Each tick writes a `metric=unscoped` marker row for each Board whose
scrape was Unauthoritative (`data/state/unauthoritative_boards.json`, 138 Boards on 2026-09-25).
None of its closures could be counted that tick, and the sentence says "closures not counted on N
boards".

**Reposts** (the same role under a new id) count as they are. `index sync` logs how many new
listings share a Board and a normalised title with a posting missing from the same scrape:
`reposts: N of M new listing(s)…`. That measures the repost share before anything is built on it.

**Trends** (Space `/trends`). Under a pick and on `stock`, every line carries
`turnover: {opened, closed, recounted}` per charted run. That covers the company, category and
level lines; the roles drill gets none, because its watched roles re-count their family's jobs.
Each tick's counts land on the first charted run at or after it. The first charted run is None,
and so is every run before turnover began (`turnover_since`). `closures_unseen` counts, per pick,
the Boards with a marker inside the window. The page sums a line's turnover over the runs its
hiring move counts, which leaves out a counting change's run and the run after it, as the net
change does. The company sentence reads "… (+17 openings) — about 1,500 opened, 1,500 closed,
closures not counted on 1 board". The table gains Opened and Closed columns.

**Hot.** Every row carries the window's `opened` and `closed`. These are summed over the same runs
as its net change, with the same counting-change exclusions (`read_window_sum`). The Volume lens
ranks by `opened` instead of `new7`. Rate still divides `new7`, the jobs first seen this week and
still open. `window.flows_from` says when turnover began inside the window.

## Options not taken

- **Computing it elsewhere.** One option was for `sync` and `prune` to write each id's reason to
  a file that `role_trends` joins. That ties three modules together, and a skipped `role_trends`
  loses those reasons for good; the snapshot diff instead heals on the next tick. The other was a
  separate ledger kept by `sync`, which has no family or band, so category lines would get no
  turnover.
- **Storing it elsewhere.** A new per-tick directory would double a file count already heading for
  HF's 10,000-file directory limit. One Parquet file rewritten each run would be re-uploaded about
  27 times a day, and storage is the binding cost (ADR-0168).
- **Counting a closure at the first absence**, with a reversal on reappearance. Most Unconfirmed
  ids reappear, and a closure would no longer match any fall in stock.
- **A churn lens on Hot.** It is deferred until the repost share is measured, since an aggregator
  that reposts daily would lead it.

## Consequences

- History starts at the first pipeline run after this merges, and turnover at the run after that,
  the first with a placement snapshot to diff. HF history was squashed on 2026-09-25, so nothing
  can be backfilled.
- Turnover is a lower bound. A job opened and closed between two scrapes of its Board is in
  neither count, and neither is any change during a run where `role_trends` skipped.
- **A tech-filter change is the one recount turnover cannot see.** A job the new filter admits
  arrives with a fresh `first_seen`, exactly like a new posting. Only the rule that leaves out a
  counting change's run and the run after it keeps the change out. Its evictions land two scrapes
  later, so the leak the net change already has, turnover has too.
- A `prune` removal that is not handed over reads as Closed: one made by the `cleanup-index`
  workflow, or one made in a run where `role_trends` skipped.
- The Space holds every delta row in memory (`_TREND_DELTAS`, 1.11M rows over 12 days on
  2026-09-25). Turnover adds at most one row per job that moved, plus about 138 markers per tick.
  Planning that growth is deferred to its own ADR.
- CONTEXT.md gains **Opened**, **Closed** and **Recounted**, and its **Lens** entry now glosses
  Volume as Opened.
