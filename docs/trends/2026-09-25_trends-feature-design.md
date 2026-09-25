# Trends, one owner per fact: a design for simplifying the Trends feature

**Date:** 2026-09-25 · **Status:** accepted as
[ADR-0230](../adr/0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md),
with the owner's decisions below · **Starting point:** `origin/main` at 629a265e plus PR #682
(ADR-0224). PR #679 (ADR-0227) was open while this was written and has since merged (68a5f87d).
**Line numbers:** on 629a265e · **Measured on:** the production `data/state/` pulled from HF on
2026-09-25 (newest tick 05:15 UTC)

**The owner's decisions (2026-09-25), on §10:**
- the recommended design (§8) is adopted;
- Hot's rows become Company directory entries;
- Hot is ranked at Space boot;
- PR #679 lands whole (done);
- **`new` becomes an inflow,** the Opened Jobs of the trailing seven days. This departs from §10's
  recommendation to keep it;
- the archive stays in `data/state/`.

## Summary

**What is messy.** Trends stores the same counts twice and re-derives the same rules in up to five
places, across three tiers and two languages:

- Replaying the Board-delta ledger reproduces the aggregate trends ledger exactly, at all 314
  ticks since 2026-09-13. The Space still loads the aggregate as 6.6M Python dicts, which held
  4.75 GB and took 195 s on a laptop.
- The Board-count snapshot, the id-level assignment snapshot and a sum of deltas all give the same
  per-Board counts.
- What counts as a change "not hiring" is decided twice: once in `hot_boards` (Python) and once in
  the UI (JavaScript). The two are kept in step by comments, and ADR-0185 measured them
  disagreeing. PR #679 would add a third copy.
- Methodology is recorded both as a series version and as an epoch row, under a key still named
  `centroid_version`.
- "The live version", "found Board" and "which Boards are one company" each have two or three
  definitions.

§3.4 lists fourteen such facts, with file:line references.

**What I recommend.** One Board-delta ledger, and one history module in `headstart` proper that
the pipeline and the Space both call. Write each tick's facts once, then decide what counts as
hiring when reading:

- Each tick is one file carrying its own methodology. A re-base is just another delta.
- Netting moves out of the browser into Python, and Hot is computed by the same code as the trend
  it opens.
- PR #679's opened and closed counts are the per-Job facts that get stored.
- This retires four HF files, three modules, roughly 800 lines of the Space and roughly 400 of
  the UI, and gives every duplicated fact one owner (§8.3).

**How I got there.** Four designs were drawn independently under different constraints: a minimal
interface, maximal flexibility, the common caller first, and ports and adapters. All four agreed
on six points (§6). I rejected the two that store rules or per-Job stints. The rules changed in at
least eight of ADR-0185's thirteen rounds, and storing Job stints costs growth nobody asked for.

**The first step.** Step 0 lets PR #679 merge as it stands, because its data cannot be backfilled.
Then an ADR and CONTEXT.md terms. Then the first code change: every tick writes one delta file
stamped with its methodology, even when nothing moved. It changes no reader, and one pipeline run
verifies it (§9).

**Decisions that are yours** (§10):

- whether Hot's rows become companies;
- where Hot is ranked;
- how to take PR #679;
- whether `new` keeps its meaning;
- where the archive lives.

## 1. What "Trends" covers here

Trends is everything that turns the served index into counts over time, and every surface that
reads those counts:

- **the Trends tab**: one line per **Role family** (drill to seniority bands or watched roles),
  `stock` and `new`, share, count or change, an `ats` **Trend filter**, custom windows, comparable
  coverage, and company picks from the **Company directory**;
- **Hiring now** (internally `hot`): three **Lenses** over the trailing seven days;
- **the company picker** (`/companies/suggest`);
- **the category hand-off to Search**: a Trends category at a picked company opens as its exact Job
  ids.

The design starts from the state PR #682 leaves. The classifier head reads JobBERT-v2(title) plus
the row's served `vector`, so a family is decided per row. The title cache holds 25 title logits
per normalised title. `role_trends` decides families through
`role_family_classifier.served_vector_batches` and `decide_rows`, and a re-embed can move a Job
between families. A new head is a new series version, and a re-base. Re-bases are now routine:
series 2001 landed on 2026-09-24 at 21:19, and head v2 (series 3002) was still warming its title
cache the next day when head v3 (series 3003, #682) replaced it.

## 2. Words this document uses

Domain terms are CONTEXT.md's: **Job**, **Board**, **board_key**, **Tenant**, **Company**,
**Company directory**, **Role family**, **Role watchlist**, **Lens**, **Operator**, **Eviction**,
**Trend filter**. Trends also leans on seven words CONTEXT.md does not define yet. They are
defined here, and step 1 of the migration adds them to CONTEXT.md:

- **Tick**: one pipeline run's measurement, stamped with the run's `ts` (`HEADSTART_RUN_TS`,
  shared by `index prune` and `role_trends`). 929 ticks from 2026-08-11 to 2026-09-25.
- **stock** / **new**: every served row, and the rows whose `first_seen` is inside the trailing
  seven days. `new` is a rolling level, not an inflow.
- **Series version**: `3000 + classifier head version` (older eras: 2 = centroid fit, 2001 = title
  rules). It changes only on a re-base.
- **Span**: a run of consecutive ticks counted at one series version (`headstart.version_spans`,
  ADR-0221).
- **Counting change**: a tick where a methodology stamp moved (ADR-0164, ADR-0188): the family
  list, the classifier head, the tech filter, the derivations or the dedup rules.
- **Board delta**: one tick's change in a Board's count for one `(metric, family, band, ats)`
  group (ADR-0143). A **found Board** is one whose first delta lands its whole backlog at once.
- **Netting**: taking the steps that are not hiring (counting changes, found Boards, dedup
  removals) out of a line's change (ADR-0185, round 13).

Architecture terms are the codebase-design skill's: **module**, **interface**, **implementation**,
**depth**, **seam**, **adapter**, **leverage**, **locality**.

## 3. Today's map

### 3.1 Data flow

```text
config/role_family_classifier/ (head)   config/role_families.json   config/role_watchlist.json
LanceDB `jobs` (id, title, vector, min_years, employment_type, ats, first_seen)
   │   merge job: stamp ts → index sync → index prune (→ dedup_evictions.csv) → embed_prune
   ▼
role_trends ─┬─ reads/writes role_title_families.parquet   title → 25 title logits (per head)
             ├─ appends     role_trends.parquet             index-wide group counts, every tick
             ├─ appends     role_trend_board_deltas/{ts}    per-Board group changes, every tick
             ├─ overwrites  role_trend_board_counts.parquet per-Board group counts now
             ├─ overwrites  role_assignments.parquet        Job id → family now (tech rows)
             ├─ appends     role_reassignments.csv          family → family moves per tick
             └─ appends     trends_epochs.csv               methodology stamps, on change only
hot_boards        ← board counts, board deltas, epochs, Board names (LanceDB) → hot_boards.json
company_directory ← board deltas, Board names (LanceDB), its own previous file → company_directory.json
   │   upload data/state to HF (additive: nothing is ever deleted there)
   ▼
Space boot (deploy/hf-space/app.py): downloads role_trends.parquet, role_trend_board_deltas/*,
   trends_epochs.csv, hot_boards.json, company_directory.json, role_assignments.parquet,
   dedup_evictions.csv; config is copied into the image
   /trends · /hot · /companies/suggest · /search?board=&family= (hand-off)
   ▼
UI (src/headstart/ui/static/app.js): stepNotes → stepJumps → netOfSteps (netting), movers,
   sentences, chart, table, picker, Hot tab
```

### 3.2 Every stored artifact

Sizes and row counts are from the 2026-09-25 HF copy.

| artifact (under HF `data/state/` unless noted) | format and key | writer | readers | lifetime |
| --- | --- | --- | --- | --- |
| `role_trends.parquet`, the aggregate trends ledger | `(ts, version, metric, family, band, ats, count)`; 6,625,021 rows, 929 ticks, 9.0 MB; ~13.7k rows a tick; non-tech written undecomposed as `(stock, non-tech, all, all)` | `role_trends.append_ledger` (role_trends.py:347), read and rewritten whole each run | Space `_load_trends` (app.py:179): the index chart, the list of ticks (app.py:1431), which ticks measured `new` (1756), the live version (589), Hot's window-base fallback (443) | append-only since 2026-08-11 |
| `role_trend_board_deltas/{ts}.parquet`, the Board-delta ledger (ADR-0143) | one file a tick, `(ts, board, metric, family, band, ats, delta)`; series version in schema metadata under `centroid_version`; 314 files, 1,110,438 rows, 6.4 MB; median 1,484 rows a tick, ~954 KB for a re-base tick | `role_trends._append_board_deltas` (450) | Space `_load_board_deltas` (227): company picks and comparable coverage (`_replay_rows` 1416), openings (590), arrivals (592), `new` holds (593); `hot_boards` (190, 304, 315); `company_directory.ledger_boards` (95) | append-only since 2026-09-13 |
| `role_trend_board_counts.parquet`, the Board-count snapshot | `(board, metric, family, band, ats, count)`; 294,047 rows over 39,551 Boards; metadata `centroid_version`, `as_of` | `role_trends._save_board_counts` (489) | `role_trends` (the next tick's delta, with `_recover_board_counts` 427), `hot_boards.read_levels` (126) | overwritten each run |
| `role_assignments.parquet` | `(id, family)` for 414,937 tech rows; metadata `centroid_version` | `role_assignments.save` from `role_trends.main` (653-690) | `role_trends` (the diff); Space `search.load_family_ids` (app.py:581) for the Search hand-off; `scripts/ui/serve.py` | overwritten each run, discarded across a re-base |
| `role_reassignments.csv` | `(ts, version, family_from, family_to, count)`; 1,170 rows | `role_assignments.append_ledger` (117) | **none in code** | append-only |
| `trends_epochs.csv` | one row per change of `(centroid_version, family_map_fingerprint, tech_filter_version, derivations_version, dedup_version, family_classifier_version)`; 10 rows | `trends_epochs.append_if_changed` (112), plus `upgrade_older_header` every run | Space `_load_epochs` (363) → `/trends` `epochs`; `hot_boards.counting_changes`/`dedup_changes` (142, 150); UI `stepNotes` (app.js:2110) | append-only, header migrated in place |
| `role_title_families.parquet`, the title cache | after #682: `(title, logits[25])`, metadata `head_version`, ~27 MB (the HF copy is still head v2's `(title, family, confidence)`, 196,608 titles) | `role_family_classifier.fill` / `save_cache` | `role_trends` only | discarded on a new head |
| `hot_boards.json` | three lenses of ≤100 `{board, ats, company, stock, new7, net, rate, operator}`, a window, exclusion counts; 63 KB | `hot_boards.main` | Space `/hot` | overwritten each run |
| `company_directory.json` | `{"companies": [{name, boards}]}`; 34,324 companies over 35,494 Boards; 2.3 MB | `company_directory.main` | Space `_load_directory` (460) → picker and `/trends?company=` | overwritten each run |
| `dedup_evictions.csv` | `(ts, board, count, rule)`; 47 rows | `index prune` (not a Trends stage) | Space `_load_evictions` (401) → `/trends` `evicted` | append-only since ADR-0210 |
| `config/role_families.json` (git) | 24 families + `unclassified-tech`, each `{name, label, definition}`; `retired[] {name, label, successor}` | people | `roles.load_families`, `roles.family_list_fingerprint`; Space labels and retired-name resolution (app.py:241-310, 562) | curated |
| `config/role_watchlist.json` (git) | `roles[] {name, label, parent, match[]}` | people | `roles.load_watchlist`; Space `_watch_meta` (316), patterns included for the hand-off | curated |
| `config/role_family_classifier/` (git) | `manifest.json`, `head.npz` | `scripts/embed/train_role_family_classifier.py` | `role_family_classifier.Head` | one per head |

Line numbers are on 629a265e. The code behind this: `role_trends.py` (715 lines, 753 after #682),
`role_family_classifier.py` (252, 300 after #682), `role_assignments.py` (117), `trends_epochs.py` (147),
`hot_boards.py` (488), `company_directory.py` (251), `roles.py` (132), `version_spans.py` (54),
about 1,050 lines of the Space's `app.py`, and about 3,180 of the UI's 4,691-line `app.js`
(Trends 1516-4493, Hot 4494-4691), tested by 2,334 lines of `tests/js/app_trends.test.js`.

### 3.3 Facts derived on read, and where

- **In the Space, at boot**: the stitched history (`_stitch_versions`, 213); the live version
  (589); each Board's openings (`_board_openings`, 476); each Board's arrival (`_board_arrivals`,
  503); when each Board's `new` may count (`_new_holds`, 524); the ledger's start (595); the
  picker's candidates with openings (591); the family ids for the hand-off, widened by retired
  names (`_with_predecessors`, 562); Hot's window base when `hot_boards` wrote none (443).
- **In the Space, per `/trends` request**: a replay of the delta ledger for picks or a comparable
  cohort (`_replay_rows`, `_replay_span`); totals and `non_tech`; the retired-family renames
  (1695-1714); which ticks measured `new` (1756); per-pick `counted_since`, `new_counted_from`,
  `discovered`, `evicted`, `pick_series`, `company_totals`, `uncounted`; epoch labels
  (`_EPOCH_LABELS`, 353).
- **In the UI, per answer**: which runs are counting changes for which lines (`stepNotes`,
  app.js:2110), the netted levels (`netOfSteps`, 2725), each line's move, the movers, "too new",
  "sorted in by a counting change".
- **In `hot_boards`**: current levels, the windowed net change without counting-change runs,
  the newly-discovered exclusion, one row per company, Operator labels, the window base.
- **In `company_directory`**: the Board set at the live version, the company grouping and names.

### 3.4 Every fact held more than once

This is the core finding. Each row is one fact, and each place listed holds, derives or labels it
independently of the others.

| # | fact | held, derived or labelled in | can they disagree? |
| --- | --- | --- | --- |
| D1 | group counts at every tick | `role_trends.parquet` **and** `role_trend_board_deltas/` (summed over Boards); the Space serves the index chart from the first and every company or comparable chart from the second (app.py:1642-1652) | **Measured equal**: replaying the deltas reproduces the aggregate at all 314 ticks since 2026-09-13, 0 mismatches. Only the aggregate's 615 earlier ticks exist nowhere else. |
| D2 | a Board's counts now | `role_trend_board_counts.parquet` (read by `hot_boards`) **and** the sum of its deltas at the live version (Space `_board_openings`, app.py:476) **and** `role_assignments.parquet` summed | **Measured equal** today (0 differing keys; 0 per-family differences). They are written in sequence, so a run killed between writes leaves them out of step: `_recover_board_counts` (role_trends.py:427) exists for that. |
| D3 | which methodology counted a tick | `version` on every aggregate row; `centroid_version` metadata (misnamed: it holds the series version) on each delta file, the snapshot and the assignments; `head_version` on the title cache; the epoch ledger's `centroid_version` and `family_classifier_version` columns | **Yes, by design.** At tick 2026-09-24T21:19:12 the ledgers say `2001` while the epoch row says `centroid_version=2`, `family_classifier_version=3b5cc5d9183c`. A re-base is recorded twice: as a span start and as an epoch row. |
| D4 | the live series version | Space: the span that began last in the aggregate (app.py:589); `hot_boards`: spans over the deltas (hot_boards.py:252); `company_directory`: the newest delta file's metadata (company_directory.py:110) | **Yes.** Three rules; they part when a stray tick of an older version is written last, the case `version_spans` handles and `company_directory` does not. |
| D5 | which ticks are counting changes, and which Boards a dedup change can touch | `hot_boards._STOCK_MOVING`, `_DEDUP_SIBLING_ATSES`, `_DEDUP_MIRROR_ATS` (84-95) and "the run after" (257); UI `LINE_MOVING`, `DEDUP_ATSES`, `MIRROR_ATS` (app.js:2099-2109) and the settling run (2163); the Space's `_EPOCH_LABELS` (353) as a third list of the columns | **Yes, observed.** Both files say "change one, change the other". |
| D6 | how much of a change was hiring (netting) | UI `stepNotes` / `stepJumps` / `netOfSteps` (app.js:2110-2853: by size, per line, backwards from the latest value) **and** `hot_boards.read_stock_change` (190: drops whole runs) | **Yes, observed.** ADR-0185 round 12 measured Hot against the trend its row opens: Google −27 against −42, Amazon +58 against +17. Hot was patched to match; round 13 then changed the UI's rule. |
| D7 | when a Board was found | Space `_board_arrivals` (503), the same loop again inside `_replay_rows` (1447-1452), `_new_holds` (524); `hot_boards` uses a different rule, "90% of stock arrived in the window" (`NEWLY_FOUND_SHARE`, 112, 367) | **Yes**: two definitions. |
| D8 | Hot's window base | `hot_boards.window_base` (315) **and** the Space's fallback `_with_window_base` (443) | Only when `hot_boards` wrote none. |
| D9 | "tech stock" (metric `stock`, not `non-tech`, not `watch:`) | Space `_is_tech_stock` (491) and the `/trends` totals loop (1684-1687); `hot_boards.read_levels` (136) and `read_stock_change` (293-297); `company_directory.ledger_boards` (121-123) | Five copies of one predicate. |
| D10 | which Boards are one company | the directory (Tenant ∪ curated alias); `hot_boards._collapse_same_company` (by display name, 327); `hot_boards.dedup_touches` (ATS plus lower-cased Tenant, 173); the UI's `touched` (board_key prefixes per pick, app.js:2116) | **Yes.** ADR-0185 round 12: "A pair joined only by a curated alias is still unseen by Hot." |
| D11 | the `new` window, and two reserved names | `NEW_WINDOW_DAYS = 7` in role_trends.py:103, app.py:174, app.js:2098, a sentence at app.js:3415, and `hot_boards.WINDOW_DAYS` (117) "to match"; `NON_TECH` in roles.py:26 and app.py:173; `WATCH_PREFIX` in roles.py:73, app.py:313 and hot_boards.py:119 | Hand-mirrored. The Space may import `headstart.roles` but copies it. |
| D12 | dedup removals | the `dedup_version` epoch (a marker), `dedup_evictions.csv` (exact, per Board, not per family) and the negative stock deltas themselves | A category line cannot net them: the exact counts carry no family (app.js:2178-2181). |
| D13 | non-tech counts | the aggregate writes one undecomposed row per tick; the deltas decompose it by Board and ATS | Two grains of one number. |
| D14 | the family of a Job, per tick | decided each run and counted into D1 and D2; the ids are kept only for the newest tick (`role_assignments.parquet`); moves only as an index-wide `(from, to)` count (`role_reassignments.csv`) | A move between families is visible index-wide, never per Board, so a company's category lines read it as hiring in one family and closure in another. |

Smaller duplicates:

- `count_groups` (role_trends.py:146) is dead in production. Only one test keeps it alive, beside
  the `count_board_groups` (210) the run calls.
- Every Board-delta row carries an `ats` column that equals its board_key's prefix: it differs on
  0 of 1,110,438 delta rows and 0 of 294,047 snapshot rows.
- The Space takes its list of ticks, and "which ticks measured `new`", from the aggregate ledger,
  even on the delta-replay path (app.py:1431, 1756). An unchanged tick writes no delta file
  (role_trends.py:465), so neither ledger can be dropped alone today.

### 3.5 In flight: PR #679 (ADR-0227, opened and closed Jobs)

PR #679 is open, not merged, and adds about 2,000 lines. It records, per Board and group and
tick, how many Jobs were **Opened**, **Closed** or **Recounted** (a found Board's backlog, a prune
removal, a move between families, bands or Boards). It works by diffing each tick's served ids
against a snapshot that gains each Job's Board, band and ATS, with `index sync` queuing its
evictions in `eviction_queue.tsv`. The four counts are stored as extra metric rows in the same
Board-delta files, so it adds no new files.

This matters to the design in two ways:

- **It builds the per-Job facts that no later change can backfill.** That is the most valuable
  idea in Design C and Design B below. They are facts, not rules: whether a step counts as hiring
  is still decided on read.
- **It adds a third copy of the counting-change rule.** The Space gains `_DEDUP_ATSES`,
  `_MIRROR_ATS`, `_LINE_MOVING` and `_left_out_runs`, beside app.js's and `hot_boards`' copies.
  `hot_boards` gains `read_window_sum`. A test pins the Space's copy to Hot's; app.js's copy stays
  pinned by comment only. ADR-0227 itself defers planning the Space's growing in-memory delta rows
  to a later ADR.

## 4. Pain points, with evidence

1. **One fact, two ledgers, and the redundant one is the expensive one.** D1 is measured equal,
   yet both are written every tick and both are read by the Space. The aggregate grows ~13.7k
   rows a tick against the deltas' ~1.5k. Loaded the way `app.py:_load_trends` loads it, as 6.6M
   Python dicts, it took **195 s and held 4.75 GB steady (5.3 GB peak)** on a laptop; the 1.1M
   delta rows took 2 s. Most of that is the representation, not the data: the same file as an
   Arrow table is 0.66 GB of RSS and loads in 0.2 s. As dicts it grows about 200 MB of Space
   memory a day (~21 ticks a day × 13.7k rows × ~717 bytes). The repo does not record the Space's
   hardware tier, so the date this reaches the limit is not known, but it is weeks, not years.
2. **The rules that decide "hiring" are written twice, in two languages, in two tiers.** D5 and D6.
   Hot is Python in the pipeline; the trend's netting is JavaScript in the browser. The only guard
   is a "change one, change the other" comment in each file. ADR-0185's twelfth round found them
   disagreeing on the biggest employers, and its thirteenth changed one side again. A Hot row's
   figure equals the trend it opens only when a person keeps two implementations in step. PR #679
   would add a third copy of the rule, in the Space (§3.5).
3. **The seam between the Space and the UI is in the wrong place.** The UI does the product's
   statistics: which runs are counting changes, how to net them, what a line's move is. The Space
   ships the ingredients (`epochs[].fields`, `discovered`, `evicted`, `pick_series`,
   `counted_since`, `new_counted_from`, `uncounted`, `ledger_start`, `company_totals`) for the
   browser to assemble. That interface is wide and shallow, the logic cannot be reused by Hot or
   tested in Python, and `/trends` is one ~430-line function (app.py:1529-1962). Nothing outside
   the Space can run it: the local renderer serves "no trends" (`scripts/ui/serve.py:5`).
4. **Methodology is recorded twice and named for an era that ended.** D3 and D4. A re-base is
   both a series version and an epoch; `centroid_version` holds a series version in four places
   and a different number in the fifth. Handling a re-base is spread over six modules:
   `role_trends` (`series_version`), `version_spans`, the Space (stitching, retired-name renames,
   arrivals "over every version"), `hot_boards` (drop each span's first tick),
   `company_directory` (live version only) and `role_assignments` (discard the snapshot). With
   ADR-0224, re-bases are now routine: a new head, a new family list or a retrain each cost one.
   36% of all delta rows already sit on the ten epoch ticks.
5. **Moves between families are invisible where they matter.** D14. Under ADR-0224 a re-embed can
   move a Job between families, so these moves become more common. The reassignment ledger that
   ADR-0057 built to tell a move from a closure has no reader, and it holds no Board, so no company
   line can use it.
6. **Migrations never finish.** Three legacy CSV shapes are still parsed (role_trends.py:84-98,
   314-344) although the CSV left HF on 2026-09-21; the epoch header is upgraded every run
   (trends_epochs.py:63); the Space renames `family_rules_fingerprint` (app.py:376). Because the
   merge upload only adds, every retired file lingers on HF until someone deletes it by hand. The
   old CSV cost ~11.7 GB a day of needless egress for 12 days (`scripts/state/retire_legacy_trends_csv.py`).
7. **The decisions have no single statement.** ADR-0185 is 894 lines across thirteen critic
   rounds, recorded as a changelog. The current rule set lives in JavaScript comments, and
   CONTEXT.md's Trends section defines only **Trend filter**.

## 5. The problem the designs must solve

Following the codebase-design skill's "Design It Twice", this is the problem space the four
candidate designs were given.

**Constraints every design must meet.**

- Keep every product behaviour in §1: the index chart, comparable coverage, company picks summed
  or split, markers, Hiring now, the hand-off.
- A Hot row's figure must equal the trend it opens **by construction**, not by a mirrored rule.
- Company history starts 2026-09-13, when Board deltas began; nothing can recover per-Board counts
  before that.
- Re-bases are routine and must cost one marked step, not a history (ADR-0221).
- The Space never imports `headstart.ingest`. It restarts after every run and loads its state at
  boot.
- The merge upload never deletes, so every retired HF file needs a one-off delete. HF storage is the
  binding cost, and `data/state/*` rides the wire three times a run.
- Degrade, never die: a Trends failure must not sink a run that scraped and embedded.

**Dependencies, by category.**

- **In-process**: counting, banding, versioning, the counting-change rules, netting, company
  grouping. Deepenable freely and testable directly through an interface.
- **Local-substitutable**: the LanceDB served table and the Parquet state files. A local
  directory stands in for both in tests.
- **Remote but owned**: the HF dataset between the pipeline (writer) and the Space (reader), and
  the Space's JSON to the browser.
- **True external**: the JobBERT model, pinned by revision, needed only by the classifier.

**An illustrative sketch.** This is not a proposal, only the constraint made concrete: one module
that every reader of stored Trends facts goes through, so a rule has one home.

```python
history = trends_history.load(state_dir)        # the one reader of stored Trends facts
history.ticks()                                  # every tick, with its series version and stamps
history.counts(boards=None, since=..., until=...)  # group counts per tick, from one store
history.not_hiring_steps(boards=...)             # the rule set Hot and the chart share
```

## 6. Four candidate designs

Four agents each designed the whole thing independently, from the same brief (§3, §4, §5), under
one constraint each. Figures marked *(verified)* I re-measured on the HF copy. The rest are the
designer's own measurements or estimates.

### Design A: one module, three entry points

*Constraint: minimise the interface.*

- **Shape.** One module in `headstart` proper, `src/headstart/trends_history.py`, with three entry
  points:
  - `record_tick(state_dir, ts, board_counts, methodology)`;
  - `load_history(state_dir, config_dir)`;
  - `TrendsHistory.lines(TrendRequest) -> TrendLines`.

  `TrendLines` is the `/trends` wire payload, and each series carries `points`, `netted` and
  `steps`.
- **Stored.**
  - The Board deltas, as today, with a baseline at each re-base.
  - A tick table, `role_trend_ticks.parquet`, with one row per tick: its methodology, whether it
    is a baseline, and whether `new` was measured.
  - The history before 2026-09-13, folded into the delta directory as index-wide deltas under a
    reserved pseudo-Board: 331,354 rows, 0.61 MB *(verified)*.
- **Derived on read.** Everything else: index and company lines, spans, markers, netting,
  arrivals, openings, and the directory's Board set.
- **Hot.** `hot_ranking.rank(history, companies)` runs at Space boot and makes two `lines` calls,
  so a row's figure and its trend's figure are one computation.
- **Deletes.**
  - HF files: the aggregate, the snapshot, `trends_epochs.csv`, `hot_boards.json` and
    `role_reassignments.csv`.
  - Modules: `hot_boards.py`, `trends_epochs.py` and `version_spans.py`.
  - About 850 of the Space's roughly 1,050 Trends lines, and about 450 lines of app.js.
- **Weak spots, which it names itself.**
  - Its interface is small in entry points but wide in shape. `lines` returns the whole wire
    payload, so it changes whenever the UI needs a field.
  - Getting the directory's Board set out of `lines` is awkward.
  - The tick table is a second file per tick that can disagree with the delta directory. A rule
    settles it: a tick with no row never existed.

### Design B: store each Job's stints, decide everything on read

*Constraint: maximise flexibility.*

- **Shape.** Store each Job's history instead of counts:
  - `job_stints/current.parquet`: every served Job's counted attributes (~11 MB, estimated).
  - `job_stints/ended/{day}.parquet`: the stints that ended that day.
  - `served_exits/{day}.parquet`: why each Job left (evicted, off-Board, dedup, alias).
  - `trend_ticks.csv` and two frozen archives.

  `TrendHistory.lines(scope, by, metric, rule: HiringRule)` then works out any grouping, any
  `new` window and any hiring rule, applied to past ticks as well as new ones.
- **What it gains.**
  - A reassignment is told apart from a closure for each Job.
  - Dedup removals are counted per family.
  - Watchlist and band edits apply to history.
  - A `remote` dimension becomes possible.
  - Trends no longer pauses while a new head warms up.
- **What it costs.**
  - It deletes the most, including the Board-delta ledger and `role_assignments.parquet`.
  - Its stored history grows about 0.7 MB a day, and every Space boot downloads it all. The
    designer estimates about 130 MB per boot at six months, or about 4 GB a day of egress.
  - It needs a roll-up within about 90 days, after which rules no longer apply to older history.
  - It still cannot re-score closed Jobs under a new head, because `embed_prune` drops their
    vectors.
  - Exit volumes and stint sizes are estimates.

### Design C: decide each change's cause once, when it is written

*Constraint: make the most common caller trivial.*

- **Shape.**
  - `role_trends` diffs an extended snapshot, `id → (board, family, band, watch, new_until)`.
  - It stores a `cause` on every Board-delta row: `hiring`, `found_board`, `counting_change` or
    `dedup_removal`.
  - A new `headstart.trend_lines` reads the rows, and every figure is a sum: a line's hiring
    change is the sum of its `hiring` deltas.
  - Total equals the sum of its companies, a company equals the sum of its categories, and Hot
    equals its trend, all by construction.
- **Also.**
  - It drops the redundant `ats` column.
  - It redefines `new` to count only arrivals caused by hiring, which is a visible methodology
    change.
  - A pipeline stage, `hot_companies`, ranks directory companies.
- **What it costs.**
  - A rule is baked into stored history, so changing one means rewriting the ledger on HF.
  - Causes for past ticks can only be approximated, because no ids were kept.

### Design D: a storage port and one deep history module

*Constraint: ports and adapters.*

- **Shape.** Two modules in `headstart` proper:
  - **`trend_record`** is the storage port. It holds a `TrendRecord` value, a Parquet adapter
    `load(root)`, an in-memory adapter `from_rows` for tests, and `append_tick`.
  - **`trend_history.TrendHistory(record, taxonomy)`** is the deep module:
    - `answer(TrendQuestion) -> TrendAnswer`, for `/trends`;
    - `company_moves`, for Hot;
    - `openings`;
    - `boards_counted`;
    - `delta_to(levels)`, for the writer.
- **Stored.**
  - Board deltas only, with each tick's methodology in its own file's metadata. This replaces
    both `centroid_version` and `trends_epochs.csv`.
  - An empty file for a tick where nothing changed.
  - A re-base is written as a delta against the replay, not as a baseline. The 2001 re-base
    shrinks from 291,384 rows (954 KB) to 167,168 rows (593 KB) *(verified)*.
  - The aggregate before 2026-09-13 is frozen outside `data/state/`: 3,230,972 rows, 4.31 MB
    *(verified)*.
- **Seams.**
  - A real port at the storage seam, with two adapters.
  - A real port at the Space/UI seam: golden answers in `tests/fixtures/trend_answers/*.json`.
    Pytest asserts them against `answer()`, and the node tests load them in place of hand-written
    payloads.
  - No port at the pipeline/Space code seam; putting each module in the right place is enough.
- **Hot.** `hot_ranking.rank` runs at Space boot, and `company_directory` writes each company's
  **Operator**.
- **Weak spot, which it names itself.** `trend_record` is mostly file layout. Its in-memory
  adapter is justified only by the shared fixtures.

### Where all four agree

Four independent designs, under four different constraints, reach the same six conclusions:

1. **The Board-delta ledger is the only stored count.** The aggregate ledger and the Board-count
   snapshot are derived from it (D1, D2).
2. **A tick's methodology is stored once, with the tick.** Spans, markers and "the live version"
   are derived from it. `trends_epochs.csv` and the misnamed `centroid_version` go (D3, D4).
3. **Netting leaves the browser** for one Python module in `headstart` proper that the Space and
   Hot both call. The UI draws `points`, `net` and `steps` (D5, D6).
4. **Hot is computed by the same code as the trend it opens,** so the two are equal by
   construction. Its rows become Company directory entries instead of single Boards (D6, D10).
5. **Constants and labels have one home each,** and the UI receives them in the payload (D11).
6. **A re-base is one more delta, not a new history** (C and D; A as a later option; B has no
   versions at all). That lets `version_spans` go.

They split on three questions:

- where "not hiring" is decided: on read (A, B, D) or when written (C);
- whether per-Job facts are stored (B and C, and now PR #679);
- whether storage gets a port (D) or only a directory path (A).

## 7. Comparison

| | A: minimal interface | B: Job stints | C: cause at write | D: ports and adapters |
| --- | --- | --- | --- | --- |
| Stored Trends files | Board deltas, a tick table, an archive | stints, exits, a tick log, two archives | Board deltas with `cause`, a wider id snapshot, prune's removed ids | Board deltas with methodology inside, an archive |
| Where "not hiring" is decided | on read, Python | on read, Python, per Job | at write, Python, per Job | on read, Python |
| Per-Job move vs closure | no (PR #679 adds it) | yes | yes | no (PR #679 adds it) |
| Cost of changing a hiring rule | a code change and a Space restart | a code change and a Space restart | a rewrite of stored history on HF | a code change and a Space restart |
| Hot | at Space boot | a pipeline stage | a pipeline stage | at Space boot |
| HF files retired | 5 | 8 | 6 | 4 |
| Storage growth | as today, minus ~10 MB | +~0.7 MB a day plus per-boot egress; needs a roll-up | as today, plus a wider snapshot | as today, minus ~10 MB |
| First step | Space read side over today's files | shadow-write stints | write `cause` | Space read side over today's files |

**Depth.**

- A and D are the deepest per unit of interface. Each puts replay, versioning, counting changes,
  arrivals, cohorts and netting behind four or five calls, where today it is spread over six
  modules and two languages.
- A's single `lines` looks smallest, but it returns the whole wire payload. Its interface is
  really as wide as the payload, and every UI change goes through it.
- D's `answer` has the same width. D is honest about it and keeps Hot's `company_moves` and
  `openings` as separate narrow calls.
- B is the deepest of all behind its interface, but the stored representation it needs is the
  widest and the most expensive.
- C makes its readers very shallow: every figure is a sum. It gets there by pushing the depth into
  stored data.

**Locality.**

- In A, B and D, a change to what counts as hiring touches one Python table and one function. The
  Space restarts after every run, so history re-reads under the new rule at once.
- In C the same change is local in code but not in data. At least eight of ADR-0185's thirteen
  rounds changed how a step is taken out or which runs are left out: rounds 3, 4, 6, 7, 8, 9, 12
  and 13, all within two days. The rule reversed twice:
  - round 6 took steps out by openings;
  - round 9 went back to ratio where both sides were substantial;
  - round 13 returned to openings.

  Under C, each of those rounds would have rewritten stored history on HF, where storage is the
  binding cost and the upload never deletes.

**Seam placement.**

- All four put the history module in `headstart` proper, so the Space can import it without
  importing `ingest`. That is the right placement, and today's code lacks it.
- D adds a storage port. By D's own account it fails the deletion test: without `trend_record`,
  its layout knowledge would sit in `trend_history` and nowhere else. A's seam, a `state_dir` path
  that tests fill in `tmp_path` through `record_tick` itself, costs nothing.
- D's second port, golden answers at the Space/UI seam, is a real one. It is the only way the
  node tests stop hand-writing payloads that drift from what the Space sends.

## 8. Recommendation: one Board-delta ledger, one history module, facts written once and rules decided on read

**Build D's structure behind A's surface, keep D's golden answers, and treat PR #679's per-Job
turnover as the stored facts.** Reject B, and take only C's principle, not its mechanism. My
reasons, in order of weight:

1. **The rules are still moving.** At least eight of ADR-0185's thirteen rounds changed what
   counts as hiring, all in two days, and the rule reversed twice. A design that stores the rule
   (C), or lets it live in two tiers (today), pays for every future round: in HF rewrites or in
   drift between two implementations. Decided on read in one Python module, a new rule is one code
   change, and Hot and the trend move together.
2. **Facts that cannot be backfilled should still be written as they happen.** This is C's and B's
   real insight. PR #679 already does it well: it stores Opened, Closed and Recounted as facts in
   the same Board-delta files, and leaves the rules to the reader. The recommended design adopts
   those rows rather than adding a `cause` column or stints.
3. **B's flexibility was not asked for.** Its cost is permanent: growth, per-boot egress and a
   roll-up policy. CLAUDE.md asks for "no flexibility that wasn't requested".
4. **A storage port with one real adapter is indirection.** A `state_dir` path is the seam. The
   port worth having is the golden answers between the Space and the UI.

### 8.1 The interface

```python
# src/headstart/trend_history.py: in headstart proper, so role_trends, company_directory and
# the Space all import it. It imports roles, board_identity, numpy and pyarrow, never ingest.

NEW_WINDOW_DAYS = 7                      # the only copy; the payload carries it to the UI


@dataclass(frozen=True)
class Methodology:                       # how one tick was counted; stored inside that tick's file
    family_classifier_version: int
    family_list_fingerprint: str
    tech_filter_version: int
    derivations_version: int
    dedup_version: int


def record_tick(state_dir: Path, ts: str, levels: pa.Table, turnover: pa.Table,
                methodology: Methodology) -> int:
    """Write this tick as one file: its changes against the replayed history, its turnover
    (ADR-0227), and its methodology. It always writes, even when nothing moved. A new classifier
    head is a delta like any other tick, never a baseline. Raises ValueError when `ts` is not
    newer than the newest tick; OSError propagates."""


class TrendHistory:
    @classmethod
    def load(cls, state_dir: Path, config_dir: Path) -> "TrendHistory":
        """Never raises: missing or unreadable history is an empty one, and the tab stays dark."""

    def answer(self, question: TrendQuestion) -> TrendAnswer:
        """/trends. Each series carries points, net (count and share) and steps
        [{i, kind, size, fields}]. Raises ValueError on a bad question (the Space answers 400)."""

    def company_moves(self, companies: Mapping[str, Sequence[str]]) -> CompanyMoves:
        """Hot's figures per directory company over the trailing NEW_WINDOW_DAYS, through the same
        code as answer(), and the window's base tick for the link."""

    def openings(self) -> dict[str, int]:
        """Every Board the history has counted, with its tech openings now (0 once closed)."""
```

**Invariants.** Each is a test at this interface, and the tests replace today's scattered ones:

1. After `record_tick(..., levels, ...)`, `load` replays exactly `levels` at `ts`. Today's
   measured equalities D1 and D2 become this test.
2. Total equals the sum of its picks' lines, and a company equals the sum of its category lines,
   in openings (ADR-0185, round 13).
3. Every netted line ends on its measured latest value.
4. For every Hot row, the whole-company `answer(since=window.base)` moves by exactly the row's
   `net`.
5. For every key and tick, `Δstock = opened − closed + recounted_in − recounted_out` (ADR-0227).

**Behind the interface.** The one module hides the following, each of which is written in two to
five places today:

- the file layout and its atomic writes;
- the replay and the archive;
- markers derived from consecutive `Methodology` values;
- one table, `_COUNTING_CHANGES`, saying which lines each methodology field moves:
  - the classifier, the family list and the tech filter move every line;
  - the tech filter also echoes a week later under `new`;
  - derivations move only level lines;
  - dedup moves only the companies it can touch (Tenant siblings on Workday and Taleo Enterprise,
    and Eightfold);
- the settling run after a change;
- found Boards and the week's hold on `new`;
- comparable cohorts;
- the tech-stock predicate;
- retired-family renames, through `roles`;
- exact dedup removals from `dedup_evictions.csv`;
- turnover windowing;
- netting by openings, with the ratio exception (ADR-0185).

**Where each piece lives.**

| module | tier | owns |
| --- | --- | --- |
| `headstart/trend_history.py` (new) | shared | everything in the list above |
| `headstart/hot_ranking.py` (new) | shared, run at Space boot | the three Lenses, `MIN_STOCK`, `TOP_N`, the exclusion counts |
| `headstart/roles.py` | shared | the family list and labels, `successor_of` for retired names, the watchlist, bands and band labels (moved from app.py's `_BAND_LABELS`), `NON_TECH`, `WATCH_PREFIX` |
| `ingest/role_trends.py` | pipeline | decide families (ADR-0224), count the tick's levels, compute turnover (`job_turnover`, ADR-0227), call `record_tick`, save `role_assignments` |
| `ingest/company_directory.py` | pipeline | Boards from `TrendHistory.openings()`, names as today, and each company's Operator |
| `deploy/hf-space/app.py` | Space | parse a request into a `TrendQuestion`, call `answer`, attach company labels; serve `/hot` from `hot_ranking.rank`, computed once at boot |
| `src/headstart/ui/static/app.js` | UI | draw `points`, `net` and `steps`; write the sentences from `steps` |

**Names, checked against their neighbours.**

- `trend_history` differs from `role_trends` (the stage that counts one tick) in both words. No
  other `*_history` module exists. `trends_epochs` and `version_spans` are deleted in step 6, so
  the `trend`/`trends` near-homograph lasts only while the migration is in flight.
- `record_tick` uses **tick**, which step 1 defines.
- `Methodology`, not "stamp": the payload's `stamps` already means tick times.
- `TrendQuestion`, not "query": Search owns that word.
- `hot_ranking`, not `hot_companies`: `hot_companies` is already the `/hot` route function in
  `app.py` and `scripts/ui/serve.py`, and an import of that name would shadow it.
- `company_moves` is a method, and no module is called that.

### 8.2 What is stored afterwards

| path (under HF `data/state/`) | holds | writer | readers | lifetime |
| --- | --- | --- | --- | --- |
| `role_trend_board_deltas/{ts}.parquet` | one tick: `(board, metric, family, band, delta)` for `stock`, `new` and (with #679) `opened`, `closed`, `recounted_in`, `recounted_out`, `unscoped`; its `Methodology` in the file's metadata; no `ats` column (it is the board_key's prefix) | `trend_history.record_tick`, called by `role_trends` | `TrendHistory.load` (the Space, `company_directory`, `role_trends`) | append-only, one file per tick, empty allowed |
| `role_trend_index_deltas_before_2026-09-13.parquet` | the 615 ticks before per-Board counting, as index-wide group deltas with their methodology: 331,354 rows, 0.61 MB | a one-off script | `TrendHistory.load` | frozen |
| `role_assignments.parquet` | each tech Job's family (with #679 also its Board, band and ATS) | `role_trends` | `role_trends` (turnover and reassignment diffs), the Space's Search hand-off, `scripts/ui/serve.py` | overwritten each run |
| `role_reassignments.csv` | unchanged: the only record of which family a Job moved from and to | `role_trends` | people | append-only |
| `dedup_evictions.csv` | unchanged | `index prune` | `TrendHistory.load` | append-only |
| `eviction_queue.tsv` (with #679) | unchanged | `index sync` | `role_trends` | drained each tick |
| `company_directory.json` | `{name, boards, operator}` | `company_directory` | the Space | overwritten each run |
| `role_title_families.parquet` | unchanged: the classifier's own cache; `head_version` is its validity key, not a Trends fact | `role_family_classifier` | `role_trends` | one per head |

**Retired from HF:** `role_trends.parquet` (9.0 MB), `role_trend_board_counts.parquet` (1.0 MB),
`trends_epochs.csv` and `hot_boards.json`. The archive adds 0.61 MB, so `data/state/` shrinks by
about 9.4 MB. That directory rides the wire three times a run (fetched by `scrape_plan` and `join`,
uploaded by `merge`) about 21 times a day, so the saving is roughly 0.6 GB a day. That figure is an
estimate.

**The Space at boot.** It holds the delta ledger as Arrow columns: +370 MB RSS for today's 1.1M
rows, measured without dictionary encoding. Today it also holds the aggregate as 6.6M dicts:
4.75 GB steady, 195 s to load.

### 8.3 One owner for each duplicated fact

| # | fact | owner after the change |
| --- | --- | --- |
| D1 | group counts at every tick | stored once in the Board-delta ledger; derived by `TrendHistory` |
| D2 | a Board's counts now | `TrendHistory.openings()`, from the replay; `role_assignments.parquet` holds membership and is never counted from |
| D3 | how a tick was counted | the tick file's `Methodology`, written by `record_tick` |
| D4 | the live series version | gone: history is continuous |
| D5 | counting changes and the Boards they touch | `_COUNTING_CHANGES` in `trend_history` |
| D6 | netting | `TrendHistory`; `answer` and `company_moves` share the code |
| D7 | found Boards | `trend_history`: a Board's first tick with tech stock |
| D8 | Hot's window base | `company_moves`; Hot's links carry it |
| D9 | the tech-stock predicate | `trend_history` |
| D10 | which Boards are one company | the Company directory; Hot ranks its entries |
| D11 | constants and reserved names | `roles` for `NON_TECH`, `WATCH_PREFIX` and band labels; `trend_history` for `NEW_WINDOW_DAYS`, sent in the payload |
| D12 | dedup removals | `dedup_evictions.csv`, owned by `index prune`, for exact sizes; the marker is derived from `Methodology.dedup_version` |
| D13 | non-tech counts | the Board deltas; the index-wide number is derived |
| D14 | a Job's family over time | `role_assignments.parquet` now; with #679, Recounted turnover per key over time |

### 8.4 What it lets you delete

- **HF files:** the four retired above.
- **Modules:** `ingest/hot_boards.py`, `ingest/trends_epochs.py`, `version_spans.py`.
- **In `role_trends.py`:**
  - `count_groups`, `append_ledger` and `series_version`;
  - `_legacy_rows`, `_PRE_ATS_COLUMNS`, `_PRE_METRIC_COLUMNS`, `_to_table` and `_ledger_schema`;
  - `_load_board_counts`, `_recover_board_counts`, `_save_board_counts` and
    `_append_board_deltas`;
  - the epochs block.
- **Elsewhere in the pipeline:** `company_directory.ledger_boards`, and `hot_boards.read_window_sum`
  if #679 lands.
- **In `app.py`, roughly 800 of its ~1,050 Trends lines:**
  - the loaders `_load_trends`, `_load_board_deltas`, `_load_epochs` and `_load_hot`;
  - `_version_spans` and `_stitch_versions`;
  - the retired-name helpers `_family_successors`, `_predecessors`, `_resolve_family` and
    `_with_predecessors`, and `_family_weights`;
  - the counting helpers `_board_openings`, `_is_tech_stock`, `_board_arrivals`, `_new_holds`,
    `_replay_rows`, `_replay_span` and `_picks_evicted`;
  - `_with_window_base`, `_EPOCH_LABELS`, `_LIVE_VERSION` and the mirrored constants;
  - with #679: `_left_out_runs`, `_index_turnover`, `_DEDUP_ATSES`, `_MIRROR_ATS` and
    `_LINE_MOVING`.
- **In `app.js`, roughly 400 lines:**
  - the constants `NEW_WINDOW_DAYS`, `LINE_MOVING`, `DEDUP_ATSES`, `MIRROR_ATS` and `RATIO_FLOOR`;
  - the netting in `stepNotes` (its withholding), `stepJumps`, `netOfSteps`, `summedPicks`,
    `isWholeLine`, `noteKind`, `notesOf` and `stepsFor`;
  - `hotNote`'s reconciliation, and the `hot=`, `hot_board=` and `hot_at=` link parameters.
- **Tests:**
  - `test_version_spans.py` and `test_trends_epochs.py` go, and `test_hot_boards.py` shrinks into
    `test_hot_ranking.py`.
  - 65 of the 175 tests in `app_trends.test.js` touch netting inputs or functions *(counted)*.
    Their measured cases (Amazon's Sep 17 filter change, Google, Micron, Wipro, NVIDIA's removals,
    Squircle) become Python tests at `answer()`.

**Kept deliberately:** `role_reassignments.csv`. It has no code reader, but it is the only record
of which family a Job moved from and to. It is not a duplicate of anything the tab serves.

## 9. Migration path

Each step is one PR that can ship and be reverted on its own. Each runs the `code-review` skill
(CLAUDE.md), and together they keep every product behaviour in §1 working throughout.

**Step 0: don't hold up PR #679, and stop adding rule copies.**

- #679's turnover history starts only when it merges, and nothing can backfill it. Merge it as it
  stands.
- From then until step 4 lands, add no Trends rule outside the future `trend_history`.
- *Verify:* as #679's own PR.

**Step 1: decide, and name things (docs only).**

- An ADR recording the chosen design. Use the next free number, and check open branches first:
  ADR numbers have collided across branches before, and 0222 and 0227 are both in flight.
- CONTEXT.md entries for **tick**, **methodology**, **counting change**, **Board delta**,
  **found Board** and **netting**.
- *Verify:* the owner's review.

**Step 2: every tick writes one file that says how it was counted.**

- `role_trends` always writes the tick's delta file, empty when nothing moved.
- It stamps a `methodology` key in the file's metadata beside `centroid_version`.
- It deletes the dead `count_groups` and its test.
- No reader changes.
- *Verify:*
  - unit tests that an unchanged tick writes a file and that the metadata round-trips;
  - after one pipeline run, the newest delta file on HF carries `methodology`;
  - from then on, the delta directory holds exactly one file per aggregate-ledger tick.

**Step 3: `trend_history`'s read side, with the Space moved onto it and the payload unchanged.**

- Build `TrendHistory.load`, `answer()` and `openings()` over today's files. The old layout's
  quirks stay inside `load`: the baseline ticks, `centroid_version`, `trends_epochs.csv`, and the
  aggregate read as columns for the ticks before 2026-09-13.
- Point `/trends` and `/companies/suggest` at it, drop the aggregate from the Space's download
  patterns, and drop the 6.6M dicts.
- Leave app.js untouched.
- *Verify:*
  1. the existing Space Trends tests pass unchanged;
  2. a new test that the replay reproduces the aggregate at every tick (the 314-of-314
     measurement, on a fixture), plus the same check once on the real state;
  3. a script that diffs the old and new `/trends` JSON on the real HF state, with zero
     differences, over a fixed set of requests:
     - stock and new;
     - a family drill, and the roles split;
     - an `ats` filter;
     - comparable coverage with a base;
     - one, two and five picks, and `split=company`;
  4. the Space's boot time and RSS, before and after.

**Step 4: netting moves into `answer()`.**

- The payload gains `net` (count and share) and `steps` per series. app.js draws them.
- Delete app.js's netting functions and constants, and #679's Space copies.
- Port the measured cases from the 65 JS netting tests to pytest at `answer()`.
- Add golden answers under `tests/fixtures/trend_answers/`, read by both pytest and node.
- *Verify:*
  - before deleting the JS, compute both on the real state for the 50 companies with the most
    openings and every company behind a Hot row. The Python net must equal the JS netted figure,
    with every difference explained;
  - invariants 2 and 3 pass as tests.

**Step 5: Hot from the same history.** This needs the owner's decision on company rows (§10).

- `hot_ranking.rank(history, directory)` runs at Space boot, and `company_directory` writes the
  Operator.
- Delete the `hot_boards` stage and module, `hot_boards.json`'s download pattern,
  `_with_window_base`, and `hotNote`'s reconciliation along with its link parameters.
- *Verify:*
  - invariant 4 passes as a test;
  - diff the first new list against the last `hot_boards.json` and explain every row that moved.
    Expect movement mostly on the rows of multi-Board companies: today 5 of the 100 Expansion
    rows, 17 of Volume and 9 of Rate *(counted)*.

**Step 6: the writer switches, and the old files retire.**

- `role_trends` writes through `record_tick`, so a new head writes a delta, not a baseline. It
  stops writing the aggregate, the snapshot and `trends_epochs.csv`.
- A one-off script:
  - rewrites the existing baseline ticks into deltas, moves the 10 epoch rows into tick metadata,
    drops the `ats` column, and writes the archive;
  - dry-runs by default, runs under `state_guard`, and runs with the pipeline's chain paused.
- Delete the old-layout readers, `version_spans`, `trends_epochs`, and the legacy CSV and header
  shims.
- Once the Space from this step is live, retire the four HF files with a script shaped like
  `retire_legacy_trends_csv.py`, then run `reclaim_storage`.
- *Verify, before anything is deleted:* the replay of the rewritten files equals
  `role_trend_board_counts.parquet` (0 differing keys) and equals the aggregate at every tick.
- *Verify, after:*
  - the four files are gone from the HF file list;
  - the Space boots, and `/trends`, `/hot` and `/companies/suggest` answer;
  - the next pipeline run is green and writes exactly one tick file.

## 10. Decisions for the owner

These are real choices, so they are put to you rather than made silently:

1. **Adopt the recommended design** (§8), or another of the four.
2. **Hot's rows become Company directory entries, not single Boards.** All four designs lead
   here, because it is what makes a row equal its trend.
   - Today, 5 of 100 Expansion rows, 17 of Volume and 9 of Rate sit on multi-Board companies.
   - A cross-ATS employer with no curated alias would appear twice.
   - Follow and Hide would act on all of the company's Boards.
3. **Where Hot is ranked: at Space boot (recommended) or in a pipeline stage that writes a file.**
   - At boot it can never be stale against the deltas the Space loaded, and it removes a stage
     and a file.
   - In a stage, the ranking cost stays off the Space.
4. **PR #679: merge it as it stands and absorb its read side in step 4 (recommended), or split
   it** and land only its write side now. Splitting avoids the third rule copy but reworks a
   finished PR. The turnover history it starts cannot wait.
5. **`new` keeps its meaning (recommended).** Design C would redefine it to count only arrivals
   caused by hiring. That is a visible methodology change nobody has asked for.
6. **The archive stays in `data/state/` (recommended, 0.61 MB), or moves outside it** so that
   `scrape_plan` and `join` never fetch it.

## 11. Smaller findings along the way

- `_append_board_deltas` means to name files with a `Z` suffix, but it replaces `:` before it
  replaces `+00:00`, so every file ends in `+00-00.parquet` (role_trends.py:468). This is harmless,
  but no reader should rely on either spelling.
- A tick where nothing changed writes no delta file (role_trends.py:465). That is why the Space
  takes its tick list from the aggregate ledger (step 2 fixes it).
- Hot and the directory name one Board differently: "NTT Data" against "NTT DATA", 1 of the 300
  Hot rows.
- `role_reassignments.csv` has no code reader. ADR-0057 built it to tell a reassignment from a
  closure, and nothing charts it.
