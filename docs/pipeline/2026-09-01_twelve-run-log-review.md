# Twelve-run pipeline log review — 2026-09-01

Window: runs `33427383367` → `33481327341`, 2026-08-31 18:51 → 2026-09-01 08:18 UTC.
All twelve on **one SHA (`1198e131`)**, so no code confound: every difference below is slice
mix, host behaviour, or infrastructure. Note `dcbe73a` (the Workday-400 retry fix, #335) is
main's head but landed *after* this window — **none of these runs contain it**, so they are the
control for measuring it later, not evidence about it.

Method: `analyse-fanout-run` skill + `scripts/runlog/`'s eight analysers, all reads through
`run_logs.clean` (the step-0 echo filter). Three `schedule` runs in the window were cancelled by
concurrency — expected under ADR-0093, the `chain` job is the real cadence, not the cron.

## 1. Wall clock and who owns it

| run | wall | plan | scrape max | scrape Σ | join | embed max | merge | crit path | queue |
|---|---|---|---|---|---|---|---|---|---|
| 33427383367 | 63.2 | 0.7 | 19.9 | 243 | 14.9 | 8.4 | 18.9 | 62.7 | 0.4 |
| 33433171861 | 60.0 | 1.0 | 28.2 | 282 | 12.7 | 8.9 | 8.8 | 59.6 | 0.4 |
| 33438617010 | 62.4 | 0.9 | 26.7 | 274 | 12.8 | 9.1 | 12.5 | 62.0 | 0.4 |
| 33443930252 | 55.2 | 0.6 | 20.4 | 242 | 13.8 | 10.6 | 9.5 | 54.9 | 0.4 |
| 33448251066 | 63.5 | 0.9 | 28.7 | 244 | 13.4 | 8.0 | 12.1 | 63.2 | 0.3 |
| 33452761443 | 65.3 | 0.6 | 33.0 | 283 | 13.7 | 7.2 | 10.3 | 64.8 | 0.4 |
| **33457127140** | **94.0** | 1.1 | **62.1** | **484** | 12.1 | 6.4 | 11.9 | 93.6 | 0.4 |
| **33463142063** | **101.8** | 0.8 | **67.9** | 355 | 12.7 | 10.1 | 9.9 | 101.5 | 0.4 |
| 33469311349 | 59.8 | 1.0 | 25.8 | 254 | 12.8 | 7.6 | 12.3 | 59.5 | 0.3 |
| 33473046160 | 59.6 | 1.0 | 23.9 | 253 | 14.3 | 7.6 | 12.5 | 59.2 | 0.4 |
| 33476972770 | 58.8 | 0.8 | 24.4 | 238 | 13.9 | 6.5 | 12.7 | 58.4 | 0.4 |
| 33481327341 | 61.6 | 0.6 | 23.8 | 262 | 13.2 | 10.9 | 12.7 | 61.1 | 0.4 |

Minutes. `crit path` = Σ of stage maxima; `queue` = wall − crit.

**`scrape` owns the critical path in 12 of 12 runs** (34–67% of wall). Queue/runner setup is
0.2–0.4 min throughout — infrastructure is not a lever here, and no scheduling change touches
this. `join` is second at a very stable 12.1–14.9 min, and it is 100% on the critical path.

One correction to carry forward: `fanout_embed.py`'s docstring calls embed "the stage this
pipeline's wall-clock is usually owned by". That is no longer true and has not been true across
this whole window — embed is now 6.4–10.9 min for 190–330 docs on 1–2 shards. The docstring is
stale, not wrong-at-the-time.

## 2. The two slow runs are two different failures

They look alike (94 and 102 min) and are not.

**`33457127140` is sum-bound.** Its scrape Σ is 484 board-minutes against a window median of
~254 — nearly double the work. Three shards (0, 6, 10) all landed at 3715–3728s with floor
ratios of only 23%, 9% and 13%: no single board dominates any of them. `scrape_plan` predicted
41–54 min for those shards in advance and they came in at a/p 1.11–1.46. Three shards hit the
60-min budget and deferred boards (`workday:dollartree/dollartreeus`, `successfactors:jobs.lidl`,
`successfactors:karriere.rewe-group.com`). The slice was simply too big; the packer was solving
the right problem.

**`33463142063` is one shard missing its estimate.** Σ is 355, only modestly high. Shard 3 ran
4075s (67.9 min) against a 31.5 min prediction — **a/p 1.90**, the worst in the window — with a
floor of just 9%, so no single board explains it either. Its slowest board was
`workday:https://citi.wd5.myworkdayjobs.com/2`. The same run's shard 9 *is* floor-bound (93% =
`successfactors:viacomcbs.careers` at 2736s / 45.6 min), but shard 9 is not the critical path;
shard 3 is. `scrape_plan` reported a near-equal pack (3.6 min spread), so this is a cost-model
miss on one shard, not a packing failure.

Given the window's known Workday 400-throttle gap, shard 3's slowest board being Workday is
suggestive — but it is **one shard in one run and its floor is 9%**, so the time is spread across
many boards, not that one. This is a hypothesis for #335's post-merge measurement to test, not a
finding.

## 3. Straggler shape across all 180 shard-runs

- **59 of 180 shard-runs (33%) are floor-bound** — one board is ≥70% of the shard's wall.
- Recurring floor owners: `successfactors:careers.hcltech.com` (11 runs, median 907s),
  `zwayam:adani.openings.co` (10, median 793s), `zwayam:careers.eaplworld.com` (7, median 889s),
  `workday:.../hitachi` (4). Worst single floors: `eightfold:starbucks.eightfold.ai` 3054s
  (50.9 min) and `successfactors:viacomcbs.careers` 2736s (45.6 min).
- **`actual/predicted` across all 180: median 0.98, mean 1.04, p90 1.41, max 2.35.** The ADR-0054
  cost model is well-calibrated in the middle and has a fat right tail. 47% of shards run longer
  than their own wall estimate.

The floor is a real ceiling: for the ~33% of shards that are one board wearing a shard as a
costume, no packer improvement helps. The lever there is a per-board timeout or splitting the
board, per ADR-0077.

## 4. Errors, egress, quarantine

Board-error rates are **0.1–0.4% of ~20,000 boards attempted**, every run — healthy and flat.
Class mix is stable: `HTTPError` (workday-dominated), `ConnectionError` (workday), a persistent
3–6 `CertificateVerifyError` on successfactors, and occasional `Timeout` spikes on successfactors
(12 and 17 in two runs). Budget kills occurred only in the two slow runs (3 shards and 1 shard);
the other ten runs had none.

## 5. Corpus and index health

Scrape volume is stable at ~1.55M lines/run; the ADR-0017 tech gate keeps **19.7–19.8%** (~307K),
per-ATS keep% flat against its own history. No ATS collapsed.

**The ADR-0083 grace period is load-bearing, and its arithmetic closes exactly.** For all twelve
runs, `evict = carried_in − reappeared − still_unconfirmed`:

| run | unconf | carried | reapp | still | implied | actual evict | add | net rows | table rows | live GB |
|---|---|---|---|---|---|---|---|---|---|---|
| 33427383367 | 1285 | 794 | 26 | 302 | 466 | 466 ✓ | 453 | −14 | 332457 | 4.18 |
| 33433171861 | 827 | 1285 | 71 | 339 | 875 | 875 ✓ | 527 | −348 | 332109 | 4.22 |
| 33438617010 | 1205 | 827 | 18 | 354 | 455 | 455 ✓ | 483 | +28 | 332137 | 4.25 |
| 33443930252 | 871 | 1205 | 37 | 347 | 821 | 821 ✓ | 348 | −474 | 331663 | 4.29 |
| 33448251066 | 730 | 871 | 50 | 378 | 443 | 443 ✓ | 237 | −208 | 331455 | 4.33 |
| 33452761443 | 791 | 730 | 46 | 327 | 357 | 357 ✓ | 212 | −145 | 331310 | 4.36 |
| 33457127140 | 819 | 791 | 42 | 368 | 381 | 381 ✓ | 438 | +55 | 331365 | 4.40 |
| 33463142063 | 809 | 819 | 14 | 497 | 308 | 308 ✓ | 406 | +98 | 331463 | 4.44 |
| 33469311349 | 869 | 809 | 69 | 362 | 378 | 378 ✓ | 383 | +5 | 331468 | 4.48 |
| 33473046160 | 569 | 869 | 44 | 316 | 509 | 509 ✓ | 244 | −265 | 331203 | 4.51 |
| 33476972770 | 536 | 569 | 15 | 292 | 262 | 262 ✓ | 278 | +16 | 331219 | 4.54 |
| 33481327341 | 648 | 536 | 11 | 253 | 272 | 272 ✓ | 399 | +127 | 331346 | 4.57 |

12/12 exact. **No id was evicted on a first absence in this window** — every eviction came through
the two-scrape grace period, as ADR-0083 intends.

The reappear rate is **1.7–8.5% (median ~4.6%)**. Read carefully: `still_unconfirmed` means the
Board sat out this run's slice or the collapse guard capped it first, *not* that the id was missed
again. So the honest reading is that the grace period rescues a median ~40 live Jobs per run from
a wrong eviction. Modest, real, and cheap.

`index prune` evicted **0 rows (0 off-Board + 0 duplicate) in all twelve runs**. Worth knowing —
either the table is genuinely clean of off-Board and duplicate rows, or prune's keep-set is not
reaching anything. Not diagnosed here.

Table size drifts down slightly: 332,457 → 331,346 rows (−1,111, −0.33%) across the window, with
net-row swinging ±474 run to run. The vector store grows monotonically, 603,062 → 605,606.

**ADR-0053 scope exclusion: 63–126 Boards and 1,851–2,874 eviction-candidate rows withheld per
run** — flat across the window, not accreting *within* it. But **28 Boards were excluded in all
twelve runs**: 25 successfactors, 2 workday, 1 eightfold. Several carry structurally permanent
reasons that will never drain —
`workday:bridgestone/external` and `workday:kohls/kohlscareers` both "capped at 2000 with no facet
left to split" (12/12 runs), and `successfactors:jobs.crh.com` "the RSS feed hit the 30 MB read
cap" (12/12). This is exactly the unbounded-accretion failure mode ADR-0053's Consequences section
predicted. Per `scope_exclusion_persistence.py`'s own caution, 28 is a **lower bound** — a Board
excluded on every scrape it got but scraped in only some runs never earns the ALL-RUNS flag.

**Storage `live` climbs monotonically and linearly: 4.18 → 4.57 GB, +0.0355 GB/run**, while the
table row count is flat. That is not data growth (2,544 new vectors ≈ 8 MB); it is LanceDB version
accretion, which `index compact` drains out-of-band. That loop is visible in this window: the
13th run (`33486314169`, 52s) stood down with *"cleanup-index is active (1) — standing this run
down; the hand-off starts a successor"*, `cleanup-index` `33486350245` ran 08:18:19–08:27:33, and
the successor pipeline started 08:27:25. Working as designed. At ~24 runs/day the accrual is
~0.85 GB/day between compactions.

## 6. Inside the `join` job — where its 13 minutes go

Median over 6 sampled runs (33433171861, 33443930252, 33457127140, 33463142063, 33473046160,
33481327341). Total median 778s = 13.0 min.

| step | median s | % of job |
|---|---|---|
| upload `corpus-state` | **178** | **23%** |
| `filter_tech` | 130 | 17% |
| `embed_plan` | 89 | 11% |
| `pip install -e ".[embed]"` | 73 | 9% |
| `update_descriptions` | 57 | 7% |
| `state_fetch` (2 calls) | 54 (20–117) | 7% |
| `update_ledgers gap` | 53 | 7% |
| `update_ledgers priority` | 37 | 5% |
| `update_ledgers failures` | 31 | 4% |
| `setup-python` | 27 | 4% |
| `scrape_join` | 26 | 3% |
| `download-artifact` (fragments) | 17 | 2% |
| `update_ledgers cost` | 2 | 0% |
| checkout / cache / upload assignments | 5 | 1% |

**Only 425s (7.1 min) of the 13.0 min is pipeline work.** The other 353s (5.9 min) is environment
setup and artifact I/O.

### The single biggest item is an upload nothing downstream is waiting for

`corpus-state.zip` is **1,898,294,003 bytes (1.90 GB)** — measured from the join log's own
finalize line, run 33481327341. It carries `data/jobs`, `data/state`, `data/descriptions`, and
only the `merge` job reads it.

But `embed` declares `needs: [scrape-plan, join]`, and GitHub Actions `needs` waits on the whole
job. Measured in 6/6 sampled runs, `embed` starts **2–3 seconds after `join` ends**, and `join`
ends **176–190s after the corpus-state upload begins**:

| run | join end | corpus-state upload starts | embed starts |
|---|---|---|---|
| 33433171861 | 20:37:22 | 20:34:23 | 20:37:25 |
| 33443930252 | 22:32:45 | 22:29:49 | 22:32:48 |
| 33457127140 | 02:17:27 | 02:14:43 | 02:17:30 |
| 33463142063 | 03:57:45 | 03:54:49 | 03:57:47 |
| 33473046160 | 05:57:05 | 05:53:55 | 05:57:08 |
| 33481327341 | 07:54:07 | 07:50:58 | 07:54:10 |

`embed` only ever downloads `embed-assignments` — **266,260 bytes**, uploaded in ~1s one step
earlier. So every run spends ~3 minutes of critical path uploading 1.9 GB before letting a job
start that needs 266 KB of it. `merge` (the actual consumer) cannot start until `embed` finishes
anyway, so that upload has ~7 minutes of slack it is not using.

### What is actually in the 1.9 GB, and what merge needs from it

Measured in CI logs: 1.55M scraped records vs 307K tech records — the full scrape is **5× the
tech subset**. Local byte ratio (a *stale* working-tree snapshot, quoted for proportion only, per
CLAUDE.md's freshest-data rule): `data/jobs` 1.6 GB of which `data/jobs/tech` is 295 MB, plus
`data/descriptions` 292 MB and `data/state` 19 MB.

Tracing every merge-side consumer:

- `embed_merge` — fragments + store. Does not read the corpus.
- `update_meta` — `_JOBS = data/jobs/tech`. Tech subset only.
- `index sync` — `--source` = `data/jobs/tech`; `--scraped` = `data/jobs` (full).
- `index prune` — live-Board keep-set.
- `role_trends` — reads `PROD_TABLE`.

So the **full `data/jobs/*.jsonl` rides 1.3 GB across the network for exactly one purpose**:
`index._scraped_boards()`, which reduces it to
`{resolve_board(job["id"], live) for job in iter_jobs(path)}` — a set of ~20,000 Board keys, the
ADR-0053 eviction scope.

**Hazard, and it is sharp.** `_scraped_boards` *silently falls back* to the tech corpus's Boards
when the scrape dir has no `.jsonl`. Removing `data/jobs/*.jsonl` from the artifact without first
supplying the Board set some other way would not error — it would quietly narrow the eviction
scope to tech-only Boards, stranding exactly the rows the docstring says the full scrape exists to
evict. The two changes must land together, or not at all.

`scrape_join` already writes `data/state/unauthoritative_boards.json` with the comment *"Under
data/state because that is what rides the corpus-state artifact to the job running [index sync]"*
— a direct precedent for emitting a `scraped_boards.json` beside it.

### Real dependency DAG inside join

Read from the modules, not the workflow's step order:

- `update_ledgers cost` reads only `data/scrape/fragments/*/board_cost.csv`.
- `update_ledgers failures` reads only `observability.read_shards(data/scrape/fragments)`.
- `update_ledgers priority` reads `data/jobs` + `data/jobs/tech` → needs `filter_tech`.
- `update_ledgers gap` reads `meta.jsonl`, `data/descriptions`, `unauthoritative_boards.json` →
  needs `update_descriptions`.
- `embed_plan` reads `data/jobs/tech`, `meta.jsonl`, **and `data/state/board_priority.csv`** →
  needs `priority`.

All four ledger subcommands write **different files** (`board_priority.csv`, `board_cost.csv`,
`board_failures.csv`, `board_description_gap.csv`) and none reads another's output. So:

```
download-artifact ─┬─ cost (2s) ──────────────────────────────┐
                   ├─ failures (31s) ─────────────────────────┤
                   └─ scrape_join(26) → filter_tech(130) → update_descriptions(57)
                                              │                    ├─ gap (53s) ──┤
                                              └─ priority(37) → embed_plan(89) ───┴→ upload
```

Serial today: 26+130+57+37+2+31+53+89 = **425s**. Critical path of the DAG above: 26+130+57+37+89
= **339s**. Saving ≈ **86s**.

Caveat: `cost`, `failures` and `gap` carry `continue-on-error: true` while `priority` does not.
Any collapsing of steps must preserve that asymmetry — a failed `priority` should still sink the
job.

## 7. Estimated join savings, ranked by payoff over risk

| change | saving | risk |
|---|---|---|
| Drop full `data/jobs/*.jsonl` from `corpus-state`, precompute the Board set in `scrape_join` | ~120s | **Medium** — silent-fallback hazard above; must land as one change |
| Run `cost`+`failures` early, `gap` parallel with `priority`→`embed_plan` | ~86s | Low — files are disjoint; needs an ADR, CLAUDE.md documents the current order |
| Run `state_fetch`'s two calls concurrently | 0–50s | Low — independent path sets, but the second is warn-only on failure |
| Parallelise `filter_tech` across ATS files | ~80s (floor: workday ≈ 37% of lines) | Medium — new concurrency in the authoritative tech gate |

Combined, roughly **4–5 min off a 13.0 min join**, i.e. ~7% of a typical 61-min wall. Modest next
to scrape's 20–68 min, but join is 100% critical path and the top item is pure I/O waste.


## 8. Full error sweep (added after the first pass)

91,082 warning/error lines across all 21 jobs × 12 runs; every job concluded `success`, so all of
it is non-fatal. ~85,000 lines are `spare_egress` rotation chatter (1,750 rotations in one run is
typical — activity, not health, per ADR-0067/0081). The signal underneath:

**Workday detail-fetch failures dominate everything else: 319,967 of 390,323 detail fetches
failed across the window — 82.0%.** By class: HTTP 400 × 289,917 (337 boards), HTTP 500 ×
24,601, HTTP 404 × 3,355, ConnectionError × 1,286, HTTP 429 × 698. Three different mechanisms,
three different fates:

- **HTTP 400 (90.6% of failures)** — ADR-0098's throttle-wearing-the-wrong-number. These runs
  predate `dcbe73a` (#335), so every one settled first-attempt, unretried. Worst boards:
  `ngc/Northrop_Grumman_External_Site` 13,346, `pwc/Global_Experienced_Careers` 12,481,
  `capitalone/Capital_One` 7,291. **This window is the natural control for measuring #335** —
  compare the same line's 400 share in the first post-merge runs.
- **HTTP 500 (24,601)** — already in `http.TRANSIENT`, so these are terminal after 3 attempts.
  Distinct from the 400 class; not addressed by #335; not diagnosed here.
- **HTTP 404 (3,355; 99.6% iHeartMedia)** — a distinct class the 400 work does not touch:
  whole sub-sites whose every CXS detail 404s while the listing works and the postings are live
  ("Posted Yesterday", public pages 200 with full JSON-LD). Diagnosed and fixed in **PR #337 /
  ADR-0099**: reproduced from a residential IP, all CXS variants falsified, page JSON-LD
  confirmed as the only source; a settled 404 now falls back to the public page. Live result:
  `iHM_Corporate_Site` 0/164 → 164/164 descriptions, plus exact-currency `startDate`,
  `remoteType` (TELECOMMUTE), and `timeType` from the same JSON-LD.

The detail-failure tail matters: 500 of these lines end "— not a truncation (the listing pass
reports its own)", i.e. detail losses never mark a Board unauthoritative — the affected Jobs
index title-only instead (the quiet cost ADR-0050 exists to cap).

Everything else in the sweep, briefly: per-run board errors held at 0.1–0.4% of ~20,000 attempted
(recruitee spiked once to 56 HTTPError in `33427383367`); successfactors carries a persistent
3–6 `CertificateVerifyError` and two Timeout bursts (12, 17); the silent ceilings fired
identically in all 12 runs — six zoho boards at exactly the ~750 widget ceiling,
`freshteam:abnhire` at the 1000-job cap — documented limits, not regressions.

## 9. Follow-ups from the first pass, resolved

**`index prune` evicting 0 is healthy, not broken.** Across ~80 cached runs, prune has only ever
evicted 1–7 rows at a time, always `duplicate`, never `off-Board`, most recently in
`32936269675` (2026-08-26) — the path demonstrably fires when there is something to sweep, and a
per-run sweep keeps the standing stock at zero. Twelve consecutive zeros is consistent with that
trickle rate.

**Shard 3's a/p 1.90 (run `33463142063`) is the floor metric's blind spot, not a cost-model
mystery.** Shard 3 was that run's one budget-killed shard, and its deferral names
`successfactors:cbscorporation.jobs` — a board later measured at 0.01 tech jobs/min over 15+
minutes. The floor ratio only counts *completed* boards' seconds, so a giant that eats the
shard's tail until the budget kill is invisible in it: floor said 9% while one unfinished board
owned the overrun. Same mechanism for `33457127140`'s three killed shards (dollartree,
jobs.lidl, karriere.rewe-group.com).

**And those four giants expose a gate dynamic worth watching:** all four sat in the ADR-0064
value-gate skip list in every earlier run of the window, vanished from the gate list precisely in
the two slow runs (whose gates skipped only 6 and 12 boards against 12–17 elsewhere), blew their
shards' budgets, and re-entered the gate afterwards with re-measured rates (rewe 0.39→0.54/min,
lidl 0.22→0.26/min). Why their cost rows transiently dropped below the gate's threshold is not
pinned here — candidates are the cost-blend decay and the ADR-0096 re-key migration that
`update_ledgers cost` logs — but the consequence is measured: **each gate lapse on a giant costs
a ~35–40-minute wall-clock overrun**, which is exactly the two slow runs of this window.

**The 28 permanently scope-excluded Boards** (§5) stand as reported; the two structural Workday
cases (`bridgestone/external`, `kohls/kohlscareers` — "capped at 2000 with no facet left to
split") and `jobs.crh.com` (30 MB RSS cap) can never drain under ADR-0053 as designed, and are
the strongest argument yet for the drain mechanism its Consequences section deferred.

## 10. Workday field audit (user-requested, folded into PR #337)

What the API exposes vs what `parse()` takes, measured live (4 tenants, 20 listing items, 8
details) against the 2026-08-25 full-corpus audit:

- **Listing carries exactly five keys** — `title`, `externalPath`, `locationsText`, `postedOn`
  (relative text), `bulletFields`. So `parse()`'s reads of `item.get("jobFamilyGroup")`,
  `item.get("timeType")`, `item.get("remoteType")` never fire from the listing; `department` has
  no source in either payload (n=20 items, n=8 details — the fixed CXS projection, not a tenant
  choice) and is a measured ceiling for Workday, matching the audit's "no salary and no
  experience field exist anywhere".
- **Detail fields read**: description, startDate, timeType, location, additionalLocations,
  country (landed in #308 after the audit flagged it), remoteType. **Present but unread**:
  `endDate`/`timeLeftToApply` (4/8 — closing dates; Job has no field, needs a schema change +
  README lockstep, deferred), `hiringOrganization.name` (8/8, audit says 93.52% — would put
  per-posting subsidiary names in `Job.company`, changing every served Workday row's company
  string; deferred with a recommendation to measure before adopting), `externalUrl`,
  `jobPostingId`/`jobReqId` (identity — ADR-0097 settled that against the detail).
- **Fixed in #337**: the 404-fallback's JSON-LD now supplies `datePosted`→`startDate` (same ISO
  currency), `jobLocationType`→`remoteType` ("TELECOMMUTE" maps through `_remote_from`'s
  existing patterns verbatim), and `employmentType`→`timeType` via a closed schema.org enum map
  onto Workday's own wording — verified live on iheartmedia. The filter vocabulary
  (`ETYPE_CLAUSES`) matches by substring, so both forms filter identically; the map is display
  consistency.
