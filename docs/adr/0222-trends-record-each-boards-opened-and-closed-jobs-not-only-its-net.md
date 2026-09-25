# ADR-0222: Trends record each Board's opened and closed jobs, not only its net

**Status:** proposed (the forks below await the owner's choice; nothing is built) · **Date:**
2026-09-25 · **Extends:** [ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md)
(the Board-delta ledger), [ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md)
(company trends), [ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) (Hot) ·
**Relates to:** [ADR-0057](0057-record-family-assignments-and-report-reassignment.md) (the id → family
snapshot), [ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (Unconfirmed),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (Unauthoritative Boards),
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) /
[ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) /
[ADR-0221](0221-a-refit-is-a-step-in-one-trends-history.md) (counting changes),
[ADR-0210](0210-an-eightfold-posting-its-backing-board-serves-is-served-once.md) (dedup eviction ledger)

## Context

Trends and Hot show only a company's **net** change in open tech jobs. A company that opens five
and closes five reads flat.

A local probe measured how often that matters (`experiment/gross-job-flows/`, kept local; HF state
pulled 2026-09-25, window Sep 18 → 25 05:15 UTC). Of 2,791 companies with ≥ 20 openings at the
window start, 695 read "about flat" (|net| < 1%). Between 105 and 247 of those opened ≥ 5 jobs
that week. Amazon read net +17 while opening 914–1,532. Median weekly turnover was 6.6–14.5%, p90
20–36%. 64 companies read falling while opening ≥ 20. Every figure is a range, and a lower bound,
because nothing records when a job id arrived or left. The probe could only bracket it from the
`new` level (`first_seen` inside the week) and the replayed counting changes.

Two ledgers come close, and neither answers it:

- The Board-delta ledger (`data/state/role_trend_board_deltas/{ts}.parquet`, `role_trends`) holds
  level changes per `(board, metric, family, band, ats)`. A delta is opened minus closed.
- `new` (ADR-0051) is a level: jobs first seen in the last 7 days *and still open*. A job opened
  and closed inside the week is in neither. A found Board's backlog is in it.

`role_trends` already holds everything a per-id answer needs, at the right moment. It runs after
`index sync` and `index prune` and reads every served row's `id`, `first_seen`, Board
(`_board_keys`), family, band and ATS (`count_board_groups`). It also keeps last tick's
`id → family` for every tech row in `data/state/role_assignments.parquet` (ADR-0057). That is
415k rows and 4 MB, and the Space already downloads it for the category hand-off. Diffing the two
id sets gives, per tick, which tech ids arrived and which left.

## Proposal (recommended options marked; see Forks)

For each tick, `role_trends` diffs this tick's served tech ids against the previous snapshot. It
books every id that differs into exactly one of three flows, keyed like stock:
`(board, family, band, ats)`.

| flow | an id that… |
|---|---|
| `opened` | is new to the snapshot, with `first_seen` after the previous tick, on a Board the previous tick already counted |
| `closed` | left the snapshot because `sync` evicted it (its second consecutive absence, ADR-0083) |
| `recounted` (in / out) | moved for any reason that is not hiring. That covers a found Board's backlog, a duplicate removal, a Board leaving the keep-set, a served row the classifier moved into or out of tech, and an id whose family, band or Board key changed |

Per key and tick this holds exactly:
`Δstock = opened − closed + recounted_in − recounted_out`. A test pins it, and it is what lets
the Space print "net +17 — about 1,200 opened, 1,180 closed, 3 recounted" without the three
figures disagreeing.

Readers leave out the same ticks for flows as they do for net. That means a counting change's
tick and the tick after it (`hot_boards.counting_changes`, `dedup_changes`/`dedup_touches`,
app.js `stepNotes`), and each version's baseline tick. The pipeline records what it can know per
id. The readers keep applying the epoch rules they already own. A tech-filter change is the case
the pipeline *cannot* tell per id: a job the new filter admits arrives with a fresh `first_seen`,
exactly like a new posting. So only the epoch rule keeps it out, as it does for net today.

The logic lives in one pure module in `src/headstart/ingest/`. Its working name is
`job_turnover`, since "flow" is already ADR-0051's word for `new`. Its interface takes the
previous and current `id → placement` maps plus the reason sets, and returns the per-key flow
counts. Every precedence rule above sits behind that interface, and the tests cross it.

## Forks

**1. Where the flows are computed.**
- **A. `role_trends`, as an id-snapshot diff (recommended).** Only this stage holds the ids *and*
  their family and band. A diff is self-healing: a run where the stage skips (it is
  `continue-on-error`, and it returns early while a new head warms up) is covered by the next
  tick's diff against the last good snapshot. It needs `band` and an `as_of` stamp added to
  `role_assignments.parquet` (the Space reads only `id, family`, so this is safe). It also needs
  the snapshot read *without* its version guard, because ids stay comparable across a new
  classifier head even though families do not.
- B. `sync` and `prune` write each removed or added id with its reason to a run-local file, and
  `role_trends` joins that file to families. The reasons are exact, including the rare `sync`
  displacement of a non-public Workday copy (ADR-0187). But three modules are then coupled through
  a file, and a skipped `role_trends` loses that run's reasons for good.
- C. `sync` keeps its own flow ledger at add and evict time. It has no family or band, so category
  lines get no flows, and `prune`'s removals need a second writer.
- Sub-fork under A: how duplicate removals are told from closures. (i) `prune` also writes this
  run's dedup-removed ids (it already holds `{id: rule}`) to a file that is not published, which
  is exact per key. (ii) Take `dedup_evictions.csv`'s per-Board count, which is exact only for a
  whole company's line.

**2. Where the flows are stored.**
- **L1. As new `metric` values (`opened`, `closed`, `recounted_in`, `recounted_out`) in the
  per-tick delta file `role_trends` already writes (recommended).** It adds no files, and the
  Space's loader, version stamping and `ts` join come for free. Rows per tick are at most the ids
  that flowed. The current delta files hold a median of 1,479 rows (12 KB) per tick. Every reader
  that sums across metrics must then filter by metric. `_recover_board_counts`
  (role_trends.py:427) replays every row into the stock counts. The Space's `_replay_span`
  (app.py:1476) emits every metric it replays, and `_family_weights` (app.py:271) sums them
  whatever the metric. `_board_arrivals` and `hot_boards.read_stock_change` already filter to
  `stock`.
- L2. A new per-tick directory `data/state/board_job_turnover/`. It has cleaner semantics, but it
  doubles the file count. The delta directory already holds 314 files from Sep 13 12:00 to Sep 25
  05:15, about 27 a day, which reaches HF's 10,000-file per-directory limit (pipeline.yml:206) in
  about a year on its own.
- L3. One append-only Parquet file rewritten every run, as `role_trends.parquet` is (ADR-0120).
  It is one file, but the whole of it is re-uploaded about 27 times a day, and ADR-0168 names
  storage as the binding cost.

**3. When a job counts as closed.**
- **At eviction, the second consecutive absence (recommended).** It is never reversed, and it
  lands on the tick where stock falls, so the identity above holds. The cost is lag: closure lands
  one scrape of that Board late, and that is scrapes, not runs.
- At the first absence (Unconfirmed), with a negative correction when the id reappears. This is
  more timely, but most Unconfirmed ids do reappear, so the flows would carry reversals, and
  closed would no longer match any fall in stock.

**4. Whether Hot ranks by churn.**
- **H1. Show the week's opened and closed on every row, and replace Volume's `new7` with the
  week's `opened`: inflow that excludes found backlogs and still counts a job opened and closed
  inside the week (recommended now).**
- H2. Add a Turnover lens, ranked by (opened + closed) / 2 / stock. Recommended only after 7 days
  of flow history exist and the repost share is measured (fork 5): an aggregator that reposts
  daily would lead that lens.
- H3. Add a "hiring while flat" lens, ranked by high opened with |net| small. It answers the
  probe's question, but it is narrow.

**5. Reposts** (the same role under a new id) read as one opened and one closed.
- **Count them as they are for v1, and log a diagnostic (recommended).** The diagnostic counts
  opened ids whose `(board, normalised title)` matches an id closed within N days. That measures
  the repost share before anything is built on it.
- Pair and net them out. Closure lags by a scrape, so the pairing needs a window, and it would
  hide real re-openings.

**6. Unauthoritative Boards (ADR-0053)** never evict, so their `closed` is always zero while
`opened` still counts.
- **Write a marker row per scope-excluded Board per tick (`metric=unscoped`, about 138 Boards on
  2026-09-25, from `unauthoritative_boards.json`, which is already in `data/state` at this
  stage), so the sentence can say "closures not counted on 1 of Amazon's Boards" (recommended).**
- Say nothing, and let such a company read as opening more than it closes.

## Consequences (if the recommendations are taken)

- History starts on the day this ships. HF history was squashed on 2026-09-25, so no id snapshot
  exists to backfill from.
- Flows are lower bounds on activity, like every figure here. A job opened and closed between two
  scrapes of its Board is invisible, and so is any change during a run where `role_trends`
  skipped.
- The Space holds every delta row in memory (`_TREND_DELTAS`, 1.11M rows over 12 days). Flow rows
  add at most one row per flowed id. The pre-existing growth needs its own plan.
- CONTEXT.md's **Lens** entry glosses Volume as "roles opened in the rolling 7-day window". Under
  H1 that sentence becomes true of the new `opened`. Under any other choice it must be reworded to
  "first seen in the window and still open", so that "opened" names one thing.
