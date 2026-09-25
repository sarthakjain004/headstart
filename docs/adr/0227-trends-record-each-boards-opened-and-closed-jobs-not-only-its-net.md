# ADR-0227: Trends record each Board's opened and closed jobs, not only its net

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
| `recounted_in` / `recounted_out` | moved for a reason that is not hiring: a found Board's backlog, a `prune` removal (duplicate or off-Board, in the pipeline or in `cleanup-index`), a served row the classifier moved into or out of tech, or a change of family, band or Board key |

Per key and tick, `Δstock = opened − closed + recounted_in − recounted_out` exactly, and a test
pins it. The module is named for **turnover**, because "flow" is already ADR-0051's word for `new`.

- The snapshot gains `board`, `band` and `ats` columns and an `as_of` stamp. Turnover reads it at
  any series version (`load_placements`), because a new classifier head changes families, not
  which ids were served. A snapshot written before this ADR has no placements, so turnover starts
  on the tick after the first one that writes them.
- The tick's delta file and the snapshot move together. The file is written first, and any
  failure of the snapshot write deletes it again. The file without its snapshot would book this tick's turnover
  again next tick; the snapshot without the file would leave next tick's stock change covering
  two ticks while its turnover covers one. Only a process killed between the two writes can break
  the identity for a tick.
- **Closed is positive evidence.** `index sync` appends every id it evicts, stamped with the
  run, to `data/state/eviction_queue.tsv`. Only evictions count: a re-embedded Job that got no
  vector this run is not re-added, but it was never absent from its Board. `role_trends` books a
  departed id as Closed only if it is queued. Every other departure is Recounted. That covers a
  prune in the pipeline, and one in `cleanup-index`, which runs between pipeline runs and fetches
  no `data/state`, so it could hand nothing over.
- **The queue is published with the table.** `index_publish` puts it in the same HF commit as
  the LanceDB table and the grace set. The snapshot `role_trends` diffs rides the later
  `data/state` upload. If that upload fails after the table's commit lands, the next tick diffs
  the older snapshot against a table missing this run's evictions. So `role_trends` never clears
  the queue: it drops only the entries stamped at or before the snapshot it diffed, which the
  tick that wrote that snapshot already booked. A queue published without this run's entries
  would have read them as Recounted. A test pins that this run's entries stay.
- One user decision was that `prune` would hand over the ids it removes. That was built first,
  then replaced by the eviction queue, which is exact on the same keys and also covers
  `cleanup-index`.

**Where it is stored.** The four metrics are rows in the per-tick delta file that already exists,
beside `stock` and `new`, with `delta` holding the tick's count. This adds no files, where a new
directory would have doubled a count that grows by about 27 files a day. Every reader that summed `delta` across metrics now
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

**Trends** (Space `/trends`). On `stock`, every line of every view carries
`turnover: {opened, closed, recounted}` per charted run: the company, category and level lines
under a pick, and the index's with no pick. The roles drill gets none, because its watched roles
re-count their family's jobs.

- **Where each view's rows come from.** A pick sums its own Boards' turnover rows. The index sums
  `_INDEX_TURNOVER`: every Board's rows summed once, at load, per tick, metric, family, band, ATS
  and whether duplicate removal can move the Board's company. It is built from the same rows. It is not written into the role_trends ledger:
  summing rows already loaded costs no pipeline change and no storage, and it cannot disagree
  with the per-company figures. Under comparable coverage the index sums the cohort's own Boards.
- **Where counts land.** Each tick's counts land on the first charted run at or after it. The
  first charted run is None, and so is every run before turnover began (`turnover_since`).
- **Summed lines.** A line summing several picks gets each pick's own turnover (`pick_turnover`,
  beside `pick_series`). `closures_unseen` counts, per pick (`""` for the index), the Boards with a
  marker inside the window.
- **Which runs are left out.** The page sums a line's turnover over exactly the runs its hiring
  move counts. Wherever `netOfSteps` takes a step's whole jump out (a counting change and its
  settling run; a found Board on a category line), it leaves out the turnover of every run inside
  that jump. A step of known size keeps the run's ordinary hiring, and so keeps its turnover.
  The index's lines keep a counting change's jump, marked. The Space leaves its turnover out
  Board by Board instead, by the rule a company's own line follows (`_left_out_runs`): a
  line-moving counting change leaves out its run and the one after everywhere, and a
  duplicate-removal change only on the Boards of a company it can move: two or more sites of
  one Tenant on Workday or Taleo Enterprise, compared case-blind, or any Eightfold Board. That
  Tenant rule is the one app.js, the Space and Hot's `dedup_touches` all apply, and a test pins
  that the Space and Hot agree on it. The runs arrive as gaps, and `turnover_left_out` names
  them. A test pins that the index's displayed opened and closed equal the sum over every
  company's whole line.
- **Where the index and the companies can differ, bounded.** The equality holds for whole lines.
  A company's category line can also drop a run the index keeps. That happens where a Board of
  that company was found and its size is unknown there, or where the page dropped a partial
  read. The found backlog itself is already Recounted, so what differs is that run's ordinary
  turnover in that one category. On a levels view, an extraction change leaves out its run and
  the run after on every level line, the index's and each company's. A company's levels
  sentence reads its total, which keeps them, while the index's sentence sums its level lines
  and says those runs are left out. An extraction change re-sorts levels and never opens or
  closes a job, so only that ordinary hiring differs.
- **Duplicate handovers.** When duplicate removal changes, a requisition can move to a sibling
  site: the old row closes and the sibling's copy arrives with a fresh `first_seen`. On the run
  of a duplicate-removal change and the run after it, the Boards it can move are left out of the
  turnover wherever they are read (Hot's `dedup_touches`, the page's pick rule, the index's
  touched rows), as they are left out of the net. A handover outside those runs, when a survivor
  closes on one site while a sibling still lists it, still reads as one closed and one opened.
  Matching it exactly needs each requisition's history across runs, which nothing keeps.
- **What the page shows.** The company sentence reads "… (+17 openings) — about 1,500 opened,
  1,500 closed, closures not counted on 1 board". The index sentence gives its hiring net, opened
  less closed, with recounted jobs out of it: "All tech roles: about +10 net from hiring — about
  50 opened, 40 closed". It adds "runs where HeadStart changed how it counts left out" only when
  such a run is inside the window. The table gains Opened and Closed columns in every view. The
  legend shows none, with a pick or without one (the owner's decision).

**Hot.** Every row carries the window's `opened` and `closed`. These are summed over the same runs
as its net change, with the same counting-change exclusions (`read_window_sum`). Each Board's
arrival, its first tick in the ledger, is left out of its net and its opened: Sphinixusa,
counted from Sep 23, ranked second at "+250 net" off the backlog it landed with. A Board counted
for under three days is not ranked, as its trend calls it too new (app.js `MIN_SPAN_DAYS`). The
Volume lens ranks by `opened` instead of `new7`. Rate still divides `new7`, the jobs first seen
this week and still open, so its row leads with that count. `window.turnover_from` says when
turnover began inside the window.

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
- A Board that had no served row at the previous tick counts as found, so its first tech
  posting is Recounted rather than Opened. That is the rule the Space's `discovered` and Hot's
  newly-found exclusion already use. Doing better would need a record of which Boards were ever
  scraped, and nothing keeps one.
- A refit tick's stock change is a whole baseline, while its turnover is the real moves. Readers
  drop baseline ticks, so the identity holds everywhere it is read.
- "Closures not counted on N boards" counts markers on every run in the window, including runs a
  line leaves out for a counting change. The claim stays true, but it can name a Board whose only
  uncounted run is outside the figures beside it.
- The Space holds every delta row in memory (`_TREND_DELTAS`, 1.11M rows over 12 days on
  2026-09-25). Turnover adds at most one row per job that moved, plus about 138 markers per tick.
  `_INDEX_TURNOVER` adds a few hundred summed rows per tick. Planning that growth is deferred to
  its own ADR.
- CONTEXT.md gains **Opened**, **Closed** and **Recounted**, and its **Lens** entry now glosses
  Volume as Opened.
