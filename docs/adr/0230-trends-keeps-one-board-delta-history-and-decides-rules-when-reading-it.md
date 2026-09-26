# ADR-0230: Trends keeps one Board-delta history, and decides its rules when reading it

**Status:** accepted; migration in progress · **Date:** 2026-09-25 · **Amends:**
[ADR-0040](0040-role-trend-ledger.md) (the trends ledger stops being a second stored copy),
[ADR-0051](0051-trends-as-share-flow-and-watched-roles.md) (`new` becomes an inflow),
[ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (methodology travels with
each tick), [ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) and
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (Hot ranks companies, from
the same history, and netting leaves the browser) · **Builds on:**
[ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md) (the Board-delta ledger) and
[ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md) (Opened,
Closed and Recounted)

## Context

The owner asked for Trends to be simplified until no fact has two sources of truth. The design
review (`docs/trends/2026-09-25_trends-feature-design.md`) mapped every stored artifact, reader and
rule on the production state of 2026-09-25. It found fourteen facts held more than once. The ones
that decide this ADR:

- **Counts are stored twice.**
  - Replaying the ADR-0143 Board-delta ledger reproduces the aggregate trends ledger
    (`role_trends.parquet`) at all 314 ticks since 2026-09-13, with zero mismatches.
  - The Space still loads that aggregate as 6.6M Python dicts. On a laptop that took 4.75 GB and
    195 s.
- **Per-Board counts are held three times:** the Board-count snapshot, the id-level assignment
  snapshot, and a sum of deltas.
- **What is "not hiring" is decided in two languages.**
  - `hot_boards` (Python) and the Trends tab's netting (JavaScript) are kept in step by comments,
    and ADR-0185 measured them disagreeing (Google −27 against −42).
  - ADR-0227 added a third copy in the Space.
- **Methodology is recorded twice:** as a series version, under a key still named
  `centroid_version`, and as a row in `trends_epochs.csv` written only when something changed.
- **Other duplicated definitions and constants.**
  - "Live version", "found Board" and "which Boards are one company" each have two or three
    definitions.
  - The seven-day window is written in five places.

Four designs were drawn independently under different constraints (the codebase-design skill's
"design it twice"). All four agreed on six points:
- one stored count ledger;
- methodology on every tick;
- netting in Python;
- Hot computed by the same code as the trend it opens;
- one home per constant;
- a re-base stored as an ordinary delta.

The review rejected two alternatives:
- **Storing each change's cause at write time,** because the netting rule changed in at least
  eight of ADR-0185's thirteen rounds and reversed twice.
- **Storing each Job's full history,** because it grows the Space's download every day for
  flexibility nobody asked for.

## Decision

1. **The Board-delta ledger is Trends' one stored count history.** Every tick writes one delta
   file, even when nothing moved, and the file carries the tick's methodology:
   - the classifier head;
   - the family list;
   - the tech filter;
   - the derivations;
   - the dedup rules.

   A re-base is stored as an ordinary delta. The aggregate ledger, the Board-count snapshot and
   `trends_epochs.csv` become derived and then retire. `role_assignments.parquet` stays, as the
   id-level state that turnover (ADR-0227) needs.
2. **One module owns reading and answering: `headstart/trends/trend_history.py`,** in `headstart` proper,
   so the pipeline and the Space share it. Its interface has four calls:
   - `record_tick` writes a tick;
   - `TrendHistory.load` reads the history;
   - `answer` produces a Trends series with its net line and its steps;
   - `openings` and `company_moves` serve Hot and the company views.

   Rules (netting, lenses, windows) are decided in `answer`, when the history is read, never
   stored.
3. **Netting moves out of the browser.** The UI draws the net line and steps `answer` returns.
   Its netting functions, and the Space's third copy, are deleted.
4. **Hot ranks companies (Company directory entries), not single Boards,** and is ranked from the
   same history at Space boot. The `hot_boards` stage and `hot_boards.json` retire. The owner
   chose company rows (2026-09-25). On 2026-09-25 this changes 5 of 100 Expansion rows, 17 of
   Volume and 9 of Rate. Follow and Hide act on all of a company's Boards.
5. **`new` becomes an inflow:** the **Opened** Jobs (ADR-0227) summed over the trailing seven
   days.
   - **What changes.** Until now `new` was a level: served Jobs first seen in the last seven days
     and still open. That undercounts Jobs that open and close within the week. The owner chose
     inflow (2026-09-25).
   - **Why no netting is needed.** Opened already excludes found Boards' backlogs, duplicates and
     reclassified rows, which ADR-0227 books as Recounted.
   - **Series boundary.** The inflow starts where ADR-0227's facts start and cannot be
     backfilled. The switch is a counting change, and the old level series ends there. Step 4
     puts it at the first tick whose whole trailing week has Opened facts, a week after they
     begin, because a partial week would read as a ramp; the answer names that tick
     (`new_inflow_from`).
   - **One rule for two surfaces.** The Volume lens already counts Opened over the same window,
     so `new` and Volume become one rule with one owner.
6. **The aggregate's history from before 2026-09-13 stays in `data/state/`** as a 0.61 MB
   archive. It predates the delta ledger, so no replay reproduces it.
7. **Each constant has one home:**
   - the seven-day window;
   - `NON_TECH`;
   - `WATCH_PREFIX`;
   - the series-version base.

   Each lives in the module that owns its fact, and the Space imports it rather than copying it.

The migration is the design review's §9, one shippable PR per step, each through the
`code-review` skill:
- step 0: ADR-0227 lands whole (done, #679);
- step 1: this ADR and its CONTEXT.md terms;
- step 2: every tick writes one file, stamped with its methodology;
- step 3: `trend_history`'s read side, with the Space moved onto it and its payload unchanged;
- step 4: netting moves into `answer`, and `new` becomes the inflow;
- step 5: Hot is ranked from the same history, by company;
- step 6: the writer switches and the old files retire.

## Consequences

- **Every Trends fact gets one owner.** A rule changes in one place, and Hot, the chart and the
  company views cannot disagree about it.
- **The Space stops loading the aggregate.** The step-3 PR measures its boot time and memory
  before and after.
- **Deletions, when the migration completes:**
  - four HF state files (after `reclaim_storage`);
  - the `hot_boards` stage;
  - the `trends_epochs` and `version_spans` modules;
  - roughly 800 lines of the Space and 400 of the UI.
- **`new` changes meaning once.** Its chart is a new series from the step-4 tick, and the older
  level stays readable up to its boundary.
- **Rules are decided on read.** That makes a netting change a code change with golden answers
  (`tests/fixtures/trend_answers/`), not a data migration.
- **Until step 4, no new Trends rule is written outside `trend_history`.**

## Step 6, as built (2026-09-25)

The writer switch and the rewrite of the stored history are separate acts: the code merges first,
and the owner runs the one-off rewrite (`scripts/state/migrate_trends_to_one_delta_history.py`)
later, with the pipeline's chain paused. Four calls follow from that, or were made on the way:

- **The code reads both layouts until the rewrite runs.** `headstart.trends.history_migration`
  rewrites the older layout in memory exactly as the script rewrites it on disk, and the script
  calls the same functions, so the reader and the rewrite cannot disagree. The step-6 writer's
  first ticks land beside the older files and count against that same history. Measured on the
  2026-09-25 state: the Space's 23 fixed Trends requests answer identically on the older layout and
  on its rewrite. Once the rewrite and the retirement have run, the module, the script, the
  Space's two download patterns for the retired files, the older-layout test fixtures and
  `scripts/eval/check_trend_history_replays_aggregate.py` (which reads the aggregate) go.
  **Done 2026-09-26:**
  - the rewrite and the retirement ran live (commit `fa4a8cf3`), and the first run after them
    wrote one tick file;
  - the module, both scripts, the check and the two download patterns are deleted;
  - the older-layout fixtures survive only as the test helper `tests/trends_stored_layout.py`,
    which stores a test's compact old-style state in today's layout.
- **The rewrite reproduces what it replaces.** Dry-run on the 2026-09-25 HF state (newest tick
  05:15:02): 314 tick files, 1,110,438 rows to 986,222, one re-base rewritten (2026-09-24
  21:19:12); an archive of 615 ticks and 331,354 rows under one Methodology; the replay against
  the Board-count snapshot differs on 0 of 294,047 keys, against the aggregate on 0 of 929
  ticks, and its 9 counting changes are the epoch ledger's 9 boundaries, tick for tick.
- **The archive is named for what it precedes.** `role_trend_index_deltas_before_board_deltas.parquet`,
  not `..._before_2026-09-13`: two of its 615 ticks fall on 2026-09-13, before 12:00:39, when
  per-Board counting began. It lists its ticks in its metadata, since a tick where nothing moved
  index-wide has no rows.
- **`centroid_version` is not carried.** It read 2 on every epoch row, and the head that followed
  moves `family_classifier_version` on the same tick, so dropping it loses no boundary; the script
  refuses if it ever would. The payload's `version` field goes with the series versions: no page
  read it.
- **`role_assignments.parquet` is stamped with the classifier head's version** under
  `family_classifier_version`, not a series version under `centroid_version`. The first snapshot
  after the switch reads as not comparable once, which skips one tick of reassignment counts.
