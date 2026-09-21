# What the pipeline still runs that no longer earns its keep — 2026-09-21

**Question:** which stages, ledgers and artifacts does every run still pay for, whose output nothing
reads or whose effect has gone to zero?

**Evidence:** the 13 successful `nightly-pipeline` runs of 2026-09-21 05:57–16:18 UTC (all on
`96c0c70`/`b49e4ac`/`38c2a7d`), read from their own Actions logs; a live `repo_info` of
`imPoseidon/headstart-index`; and a grep of `origin/main` at `d92238b3`. Nothing was dispatched and
no code was changed. Cadence for the per-day figures: **26 successful runs/day**, 37 total.

Findings are ranked by what they cost now, not by how easy they are to remove. Two of them
(§1, §2) are already prescribed cleanups that nobody performed.

---

## 1. `data/state/role_trends.csv` — 174.89 MB, dead since 2026-09-09, still downloaded twice a run

ADR-0120 moved the trends ledger from CSV to Parquet on 2026-09-09 and prescribed the cleanup in as
many words (`docs/adr/0120-...:87`): a one-time `delete_file` **after** the first Parquet lands. The
Parquet landed. The delete never happened.

Verified live today:

| file | size | read by |
| --- | ---: | --- |
| `data/state/role_trends.csv` | **174.89 MB** | nothing in steady state — see the caveat below |
| `data/state/role_trends.parquet` | 7.29 MB | `role_trends.py`'s `_LEDGER`; `deploy/hf-space/app.py:98`/`:258` serves `/trends` from it |

**"No reader" needs one qualification, and it is the one that matters.** `role_trends.py:354-364`
*does* read the CSV — `legacy = ledger.with_suffix(".csv")` → `_legacy_rows()` — but only on the
`else` branch taken when the **Parquet is absent**. That is the one-time migration fold-in, and it
doubles as the recovery net: it is why a short-but-present Parquet is the dangerous state, and why
the delete must assert a row floor rather than mere presence (see the guard below).

It is **82% of `data/state/`** (212.8 MB across 235 files), and both `scrape-plan` and `join` fetch
that directory with a `data/state/*` wildcard, so it is pulled twice per run:

```
scrape-plan  [state_fetch] fetched 233 file(s), 213 MB in 15s: data/state/*
join         [state_fetch] fetched 234 file(s), 807 MB in 33s: data/embeddings/jobs/meta.jsonl data/state/*
```

**Cost:** ~350 MB of HF egress per run. Per day that is **~11.7 GB**, and the derivation matters
because it is not `× 26`: the two fetches sit at different depths, so they are counted separately —
`scrape-plan` runs on every run that passes the gate (~37/day, failures included, since its fetch
is the first thing after the gate) and `join` on the ~30/day that get that far. 174.89 MB × (37 +
30) ≈ 11.7 GB. On successful runs alone it is 9.1 GB.

Plus ~20 s of the run's serial wall-clock (12.5 s in `scrape-plan` at its measured 14.1 MB/s, ~7 s
in `join` at 24.2 MB/s), and 2.4% of the dataset's 7.3 GB live footprint.

**Why it survived twelve days:** the tool existed. `scripts/state/retire_legacy_trends_csv.py` sat
untracked in a working tree, written and never run — and `docs/agents/deployment.md` carried a
second, *stronger* hand-run recipe for the same operation, still headed "the delete itself is NOT
yet done". Two spellings of one step, each of which could be mistaken for the other's owner, and
neither with anything watching whether it had happened.

### Done — 2026-09-21T17:0x UTC

Deleted with that script's `--apply`. `data/state/` is now **37.9 MB across 235 files**, down from
212.8 MB — an 82% reduction, confirmed by a fresh `repo_info`.

Two checks ran before the delete, because it is irreversible (`merge` super-squashes the dataset
every run, so there is no prior revision to restore from):

- **The Parquet really is a superset, not a fresh start.** Its row-group statistics give a `ts`
  range of `2026-08-11T12:57:28+00:00 → 2026-09-21T16:55:09+00:00` — the earlier bound is *exactly*
  the CSV's first row, read back over a 350-byte HTTP range request. **2,400,903** of its 5,405,929
  rows predate the 2026-09-09 migration, and the CSV's literal first row
  (`stock,ai-ml,entry,all`) is present with its identical value of `819`. ADR-0120's "no row lost"
  holds; this was verified rather than assumed.
- **Nothing resurrects it.** `join` fetches `data/state/*`, packs the whole directory into the
  `corpus-state` artifact, and `merge` re-uploads it *without* `--delete` — so any run that fetched
  the CSV before the delete would put it straight back. The delete was therefore timed against run
  `35628837050`, which was still in `scrape` with zero `join`/`merge` jobs started. `scrape-plan`
  had already fetched it at 16:57, but that job uploads only `data/scrape/assignments`, so its copy
  died with the runner.

**If this ever needs redoing**, the same timing constraint applies: delete while no run sits between
`join`'s `state_fetch` and `merge`'s upload.

## 2. `data/state/*` is a wildcard over 213 MB where both callers need ~16 MB

Independent of §1. What the two planners actually open:

- `scrape_plan` reads five ledgers — `board_priority.csv` (1.9 MB), `board_cost.csv` (5.7 MB),
  `shard_speedup.csv`, `board_failures.csv`, `board_description_gap.csv` — and ships
  `held_details.txt.gz` (8.0 MB) to the shards. **~16 MB of the 213 MB it downloads.**
- The `join` stage adds `unauthoritative_boards.json`, `scraped_boards.json` and
  `scrape_health.json`. Still under ~17 MB.

Everything else on the wire is never opened by either: `role_trends.parquet`,
`role_assignments.parquet`, `board_freshness.csv.gz`, `board_freshness_report.json`,
`role_centroids/`, `embedded_ids.txt.gz`, `unconfirmed_ids.txt`, and all **211**
`role_trend_board_deltas/*.parquet`.

Even after §1 removes the 175 MB, this is ~22 MB/run of dead transfer and 200-odd needless file
fetches. Naming the patterns explicitly — the way `merge` already names
`'data/embeddings/jobs/*' 'data/lancedb/*'` — closes both.

## 3. ADR-0057 reassignment tracking has measured exactly zero for two weeks

Every run, `role_trends` loads the previous tick's `role_assignments.parquet` (3.86 MB, ~390k rows),
diffs it, and writes it back. The result, on **10 of 10** runs sampled today:

```
[role_trends] assignments: 0 of 389,352–390,376 rows changed family (0.00%), 0 transition rows
```

`docs/pipeline/2026-09-09_five-run-log-review.md:306` found the same zero on four consecutive runs
twelve days ago and could not tell convergence from a broken path. It is convergence, and the
mechanism is now visible: a transition requires a row's **vector** to move to a different centroid,
which only happens when a Job is re-embedded (`load_previous` refuses to compare across centroid
versions, and the version has been frozen at 2 since the last dispatch of `cluster-roles.yml` on
2026-08-11). Re-embeds are now **1 per run** — `[embed_merge] upgrades: dropped 1 stale rows for 1
ids` — because ADR-0050's description backfill, the flow this was built to catch, has drained.
`role_reassignments.csv`'s last non-zero row is `2026-09-08T15:43:44Z`.

So the instrument is correct and its input has dried up. It is cheap (a few seconds), so this is a
judgement call rather than an obvious cut — but it should be an explicit one, and if a future
re-clustering is what would revive it, it belongs beside `cluster-roles.yml` rather than in every
run.

## 4. `board_freshness` writes 1.6 MB a run that nothing reads

`index.py:762` calls `board_freshness.update()`, which writes `board_freshness.csv.gz` (0.68 MB) and
`board_freshness_report.json` (0.94 MB) into `data/state/`. Grepping the whole repo outside
`tests/`, **neither file has a reader** — not the Space, not `alerts`, not any script. The only
consumer is the per-ATS log line printed in `merge`.

That is not automatically waste: it is the *only* thing counting ADR-0053's scope exclusions, which
have no drain and accrete dead rows invisibly. But an instrument whose readings nobody reads is one
step from not existing. Either surface it (the freshness numbers are a real product signal) or stop
paying for the two files.

## 5. The description-gap ledger is a treadmill, not a drain

`update_ledgers gap` costs **65 s of `join`'s ~585 s serial budget — 11%** — to rescan 909,304
stored rows, and `scrape_plan` reserves `round(explore_slots × 0.05)` ≈ **500** of ~9,950
exploration slots for the Boards it names.

Per run it does real work: `10,341 left the gap, 5,424 joined it, net -4,917`. But across the 13
runs of 2026-09-21 the backlog does not move:

```
05:57  7,384 boards / 30,856 jobs      11:13  7,349 / 31,294
06:42  7,329 / 30,602                  11:46  7,361 / 32,917
07:20  7,403 / 32,904                  14:07  7,342 / 29,502
08:00  7,363 / 29,318                  14:48  7,400 / 34,440
08:49  7,342 / 30,007                  15:33  7,339 / 29,523
09:34  7,372 / 31,813
10:11  7,341 / 32,236
10:43  7,327 / 29,588
```

Board count oscillates in a 76-board band around ~7,355 with no trend over 9.5 hours; the job count
oscillates 29.3k–34.4k, also trendless. The reservation is buying churn, not convergence — and
`scrape_plan`'s own log comment already concedes that coincidental hits (~1,380 of the 1,879 gap
Boards in a slice) dominate the ~500 reserved ones.

This one is a decision, not a deletion: the ledger is honest (it already excludes the 132,174
unsettled rows it knows are unreachable), but a mechanism that has not reduced its backlog in 13
consecutive runs should either be re-aimed or stood down. 65 s/run is the price of the question.

## 6. `role_trend_board_deltas/` grows one file per run, forever, and only 7 days are read

`role_trends._append_board_deltas` writes a new `{ts}.parquet` each tick and **prunes nothing**.
`hot_boards` — the only reader — uses a trailing `WINDOW_DAYS = 7` window.

Live: **211 files**, `2026-09-13T12-00-39` → `2026-09-21T16-55-09`, so ~26/day. HF enforces a
**10,000-file limit per directory** — the same limit that rejected every upload for three days when
`_deletions/` crossed it. At this rate that lands in roughly a year. Bytes are not the problem
(~3 MB total); file count is. A retention sweep keeping ~2× the window would fix it before it is
urgent.

## 7. `request-compaction` is dormant

The `merge` job spends an `HfApi().repo_info()` call per run deciding whether to dispatch a
compaction at `_deletions > 3000`. The daily `cleanup-index` cron keeps the directory far below it:
measured now, **123 files of 10,000** — and that is eight hours after today's 08:35 compaction, so a
full day lands near ~350.

It fails closed and costs one API call, so this is not urgent. But the dispatch arm has had no
occasion to fire since the daily compaction became reliable, and it should be understood as
insurance rather than as a working path.

## 8. `telegram-bot` polls every 15 minutes to do nothing

`bot.yml` is scheduled `*/15 * * * *` — a fresh runner, checkout and `pip install -e ".[alerts]"`
each time. All **20** of the last 20 runs report `[bot] 0 update(s), 0 repl(ies), 0 failed, 0
waiting`.

Two mitigations keep this from being a real cost, and both are worth knowing. GitHub is dropping
most of the schedule anyway: the last 20 runs span ~18 hours against the 72 the cron asks for. And
the bot is the *enrolment* path for a subscriber base that is real but tiny — `alerts.yml` on
2026-09-20T17:38 sent one digest of 30 matches to one Telegram subscriber, with three more enrolled
but no query set. Hourly rather than quarter-hourly would change nothing anyone would notice.

## 9. Dormant workflows, zero runtime cost, non-zero reading cost

All `workflow_dispatch`-only, so none of them "keeps running" — listed because they are clutter a
reader has to rule out:

| workflow | last run | note |
| --- | --- | --- |
| `probe-warp-install.yml` | 2026-09-02 | its own header says "Delete once the stub change has landed and one production run confirms it" — both happened |
| `squash-dataset-history.yml` | 2026-08-19 | superseded by `reclaim_storage` inside `merge` (ADR-0071), which reclaimed 3.39 GB on the run sampled here |
| `probe-workday-400.yml` | 2026-09-12 | self-described one-off diagnostic |
| `probe-successfactors-ua.yml` | 2026-09-12 | same |
| `embed-bench.yml`, `embed-threads.yml` | 2026-07-25 | `embed-threads` is pinned to an experimental branch that will not merge |
| `diff-role-assignments.yml` | 2026-08-16 | reads the ledger §3 shows is now static |
| `cluster-roles.yml` | 2026-08-11 | the deliberate re-base decision; keep |

---

## What this does not establish

- One day of runs, on three commits. The *shares* were stable across all 13; a longer window could
  still move the gap-backlog verdict in §5, which is the finding most sensitive to it.
- §4 and §6 are greps plus a live file listing, not proof that no out-of-repo consumer exists.
- §7's ~350 files/day projection is extrapolated from a single eight-hour observation.
- Nothing here was measured by removing it and re-running. §1 is the only item where the effect size
  is arithmetic rather than inference.
