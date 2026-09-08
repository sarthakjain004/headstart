# Five-run log review — 2026-09-08

Runs `34189305102`, `34192745002`, `34197315179`, `34202399791`, `34203005531`, with
`34185601765` as a sixth control point. Window 04:03Z → 09:18Z.

**Every run in this window is on the same head SHA, `d841c86`.** That is the single most useful
property of the window: the "code shipped between the runs" confound is not merely controlled for,
it is absent. Every difference below is input mix, runner draw, or infrastructure. `ee97ebc` is an
ancestor of `d841c86`, so the ADR-0083 grace period is live throughout and no eviction here can be
read as a single missed scrape.

One of the five did no work. `34202399791` ran 1.4 minutes and stood down; it is analysed in
§6 rather than excluded, because *why* it stood down is one of the healthier things in this review.

## 1. Where the wall clock goes

| run | wall | plan | **scrape max** | join | embed max | merge | Σ maxima | queueing |
|---|---|---|---|---|---|---|---|---|
| 34185601765 (ctrl) | 62.3 | 0.9 | **25.1** | 14.4 | 11.4 | 10.0 | 61.8 | +0.3 |
| 34189305102 | 53.8 | 0.9 | **19.0** | 14.3 | 10.7 | 8.6 | 53.5 | +0.3 |
| 34192745002 | 62.2 | 1.1 | **30.0** | 14.1 | 8.7 | 7.8 | 61.7 | +0.3 |
| 34197315179 | 61.3 | 1.6 | **25.1** | 14.8 | 11.7 | 7.7 | 60.9 | +0.2 |
| 34202399791 | 1.2 | 1.2 | — | — | — | — | 1.2 | — |
| 34203005531 | 68.3 | 1.5 | **27.1** | 12.8 | 16.3 | 10.4 | 68.1 | +0.2 |

Σ of stage maxima reconciles to the wall within 0.3 min every run. **There is no queueing or
runner-setup waste at the run level** — every minute is a stage doing work, and no scheduling or
infrastructure change buys anything. Shares: scrape 35–48%, join 19–27%, embed 14–24%, merge
11–17%.

Σ job-minutes is 277–306 per run (mean ≈ 287 over the four full runs), 23 jobs each.

## 2. Scrape is floor-bound on one board, in all five runs

`successfactors:careers.hcltech.com` is the floor board of the straggler shard in **5 of 5**
executed runs, at **92–95%** of that shard's wall.

| run | scrape max | straggler shard | floor s | floor % | 2nd shard |
|---|---|---|---|---|---|
| ctrl | 25.1 | 2 | 1392 | 92% | 18.0 |
| 34189305102 | 19.0 | 11 | 1064 | 93% | 16.5 |
| 34192745002 | 30.0 | 9 | 1704 | 95% | 19.1 |
| 34197315179 | 25.1 | 10 | 1423 | 95% | 20.5 |
| 34203005531 | 27.1 | 7 | 1521 | 94% | 20.3 |

**The planner knew in advance, every time.** `scrape_plan`'s predicted makespan *equals* its
reported single-board floor to the decimal in all five runs (22.8 / 23.0 / 20.4 / 24.4 / 24.0 min),
because `predict_minutes` returns `max(floor, serial/ratio)` and the floor wins. The straggler is
the plan being right, not the packer being wrong — the serial spread is 1.02x mean in every run.

The board's job count is flat (10,420–10,459, 0.4% spread) while its cost swings 1.6x
(1,064–1,704 s). The mechanism is `jobs ÷ detail throughput`, and throughput is pinned by
`_DETAIL_WORKERS = 6` in `successfactors.py:53`. **SuccessFactors ran at width 6 in 75 of 75
fan-out observations** — the adaptive prober never tried a second width, so whether SAP tolerates
more is *unmeasured*, and the constant's comment cites no probe.

Removing or splitting this one board is worth a mean **6.4 min of wall (10.3%)**, ranging 2.5–10.9
min. That is a projection: it assumes the other 14 shards are unaffected, which the ≤0.12 min
redistribution arithmetic supports but no run has demonstrated. Diminishing returns are steep —
the 2nd straggler is worth 1.6 min and the 3rd 0.7 min, because after hcltech the top shards
become Σ-bound (floor 38–58%). **Scrape's hard floor under perfect packing is ~19–21 min** at
20,000 boards / 15 shards.

Cost-model health is good: median actual/predicted is 0.92–1.02 across all five runs, inside the
healthy band. The worst outliers (1.39–1.46) sit on Σ-bound shards, not the floor-bound ones.

## 3. Embed is the cheaper lever, and it is one integer

Shard count moved 2↔3 across the window. It is not driven by doc count but by `ceil(Σ token-bucket
cost / 1200 s)`; the Σ swing 26.5 → 56.0 min is doc-length **mix**, chiefly the ≤2048 bucket going
98 → 351 docs. Two runs sat 3% over the 40-min cliff.

All 13 embed shards ran on `cpu` with the same model — **no mixed-device confound in this window**,
so cross-shard rates are comparable. Zero failures: 5,634 docs planned, 5,634 embedded, 0 failed.

Two findings the even packing exposes. `embed_plan`'s per-shard predictions differ by ≤1 s within a
run, yet actual walls spread up to 1.79x. Dividing each shard's s/doc by the 13-shard median per
bucket gives a near-constant per-shard scalar across all four buckets — the signature of machine
speed, not doc mix. **The runner lottery is 2.6x end to end** on nominally identical
`ubuntu-latest`. Once divided out, the cost model's bias is remarkably stable at **0.60 (range
0.51–0.62)**, i.e. `_S_PER_DOC` over-charges every bucket — 1.8x at ≤512, 1.9x at ≤2048, **3.7x at
≤4096**. Every one of 13 shards came in under prediction, so nothing risks a budget kill; the
error is one-sided.

`_TARGET_SECONDS = 20 min` against a `max-parallel: 15` matrix that never used more than 3 lanes is
the lever. Raising shard count against a 2.4 min/shard setup floor (of which 70–82 s is
`pip install -e ".[embed]"`) projects **5.5–10.6 min of wall, 9–16%**, at a cost of roughly +37
billed runner-minutes per run. That is a projection on n=4 runs, and the trade is a budget call,
not a technical one.

**The contrast with scrape is the point.** Both stages offer ~6–10 min. Scrape's requires surgery
on a specific board and an unmeasured question to SAP; embed's requires changing one integer.

## 4. Reliability: steady state with two incidents

Board-error rate per 1,000 attempted (every run attempted exactly 20,000, so these are directly
comparable): 1.1 / 1.8 / 2.1 / 2.0 / **4.8**. The headline climb is two single-ATS incidents, not
broad degradation — strip them and the floor is flat at 0.95–1.8.

- **teamtailor, 60 × HTTP 403, run E only (n=1).** All on one shard in a 10-minute window; 59 of 60
  came *after* the shard's spare-egress tunnel came up, continuing through IP rotations. Two
  readings fit (WARP exit ranges blocked, or the direct IP was already walled) and the logs cannot
  separate them. A 403 is a status code, not a mechanism. Cost was −1.2% of teamtailor rows.
- **trakstar timeouts, 0/0/0/12/17.** The only failure mode that grew — but both are *bursts*
  (run C's 12 inside 2m48s, run E's 17 inside 36s), every one failing at 92–96 s = three retries of
  a 30 s timeout with zero bytes received, across non-overlapping board sets. That shape is shared
  trakstar infrastructure, so this is **n=2 incidents, not 29 events**, and cannot support a trend.

Flat across the window: **0 of 75 shard-runs lost WARP** (confirmed three independent ways,
including that `stub_gui_deps` never hit its 60 s cap — the only thing that actually removes it);
0 budget kills; 0 deferred boards; quarantine inflow +2 in five runs; darwinbox browser escalation
**0**; zoho/freshteam widget ceilings stable on the same boards.

Workday's ADR-0103 in-pass cookie clear is doing real work — 42–70 boards per run hit a 400 and
121–283 detail fetches were recovered. Consistent with the known fact that a Workday 400 is a stale
`PLAY_SESSION`, recoverable by clearing but never by retrying.

The gone-matcher is healthy. Tallying per-board failure lines reconstructs the join's `examined`
count **exactly in all five runs**, so it is the complete error set: every 404 that occurred was
caught (including lever's, which arrives as a different exception class but carries the literal
`HTTP Error 404` in its message), and no 404-ish class sits unmatched.

## 5. Corpus health: three things with no drain

The tech gate is stable — corpus keep% 19.92–20.10%, ±0.09pp, and per-ATS rates are flat. Two
standing anomalies: **teamtailor at 3.8–4.2%** (lowest by 3x, stable, plausibly a genuinely
non-tech Nordic board population) and ripplehire's raw volume collapsing 16,158 → ~6,030 at the
control→A boundary and staying there for four runs *with tech rows unchanged* — nothing tech was
lost, but 2.7x of its scrape volume vanished. That is a scrape-side open question.

**(a) ADR-0053 scope exclusion.** 2,857–3,009 rows across 85–96 Boards, flat *within* this window.
The total is the wrong number to trend, because it swings with which boards land in the slice. The
persistence view is the answer: 198 unique Boards excluded at least once, **40 excluded in all five
runs**, and the top two — `careers.hcltech.com` and `careers.wipro.com` — are **73% of the row
cost** and both ratchet monotonically. The cause is a detail-fetch failure rate (856/11,314 pages
unreadable on hcltech), so the fix is scraper-side, not policy-side. `prune` cannot reach these
rows while the Boards stay Live.

**(b) The ADR-0083 unconfirmed queue.** All four numbers reconcile exactly:
`evict = carried_in − reappeared − unconfirmed_again` holds in 5/5, and `carried in` always equals
the previous run's `unconfirmed now` — including across the stood-down run, whose state file was
correctly left untouched. Measured against the honest denominator (ids that actually got a second
look, `reappeared + evict`), the grace period **rescued 149 rows in five runs, 9.6%**. Real work,
not a no-op — but it does not "return most of what it withheld"; 90.4% of second looks confirm the
absence, which is the two-scrape mechanism working as designed. The signal to watch is
`unconfirmed again`: **222 → 266 → 307 → 340 → 402, monotone, +81%**, now over half the queue.
Three causes are indistinguishable from this line and the benign one (Board simply not in the
slice) is likely dominant, so this needs a 24-run window before it means anything.

**(c) Non-tech creep, and this one is corroborated across two days.** Within this window non-tech
went 22.17% → 22.42% of served rows. The arithmetic underneath: the table grew +4,591 rows while
non-tech grew +1,936, so **42% of the marginal net rows were non-tech against a 22.4% stock rate**.
Cross-checking against the 2026-09-07 review (20.6%, 69,282 rows) gives an independent day:
+10,419 non-tech against +27,936 net rows = **37% marginal**. Two independent windows agreeing that
the margin runs ~2x the stock is much stronger than either alone. oracle and icims dominate adds in
exactly this window and are the first place to check filter precision.

Index health is otherwise clean. `prune` evicted 0 rows in 5/5 (no off-Board, no duplicates).
`flap_audit` is **GREEN at 2%** re-add, and unlike the 2026-08-24 review no Board hides behind the
aggregate — the worst is 6 of 21 flapped rows. One Board deserves a separate look for a *different*
pathology the flap metric cannot see: `zoho:agileengine.zohorecruit.com` runs 82 adds against 76
evicts across five runs with only 3 flapped ids, i.e. high two-sided churn that mostly is not the
same ids returning.

`update_meta` ran **no sweep** in any run, so all derivation movement is the ADR-0062 re-derive
queue or inputs moving — and with the SHA fixed, none of it is code. Experience is **net −134**
(225 lost vs 91 gained), driven by run E's 143 lost out of 388 re-derived. Code unchanged means
that is a description-side signal: re-fetched descriptions coming back thinner. Salary is balanced
(126 vs 128).

The description store is **not shrinking**: unsettled 41,711 → 42,838 (+2.7%) despite learning
7,858 descriptions across the window, and the count that carries no spike —
`no description and no stored answer either way` — climbs **every single run, 4,049 → 4,411
(+8.9%)**. Per ADR-0089 that count is exact but its cause is not inferable, since only eightfold
sets `detail_fetched`.

## 6. Orchestration: the stand-down is healthy, the cron is not

`34202399791` stood down deliberately. The decisive line, and the only meaningful one in the job:

> `##[warning]cleanup-index is active (1) - standing this run down; the hand-off starts a successor.`

The gate at `pipeline.yml:148-187` set `run=false`, which every downstream job's `if:` reads — hence
six skipped jobs and six 404ing logs. `scrape_plan` never executed at all, so the value gate, the
slice, and `state_fetch` are all ruled out as explanations. `cleanup-index` run `34201303112` was
mid-`cleanup` at that moment, and standing down is correct because it re-uploads the description
store with `--delete "*"`. **Cost: 1.2 job-minutes against a 277–306 minute run.** Reproduced n=2
across two days with a byte-identical warning.

The +5:26 handoff gap that follows is also correct: a stood-down run's `chain` job is skipped by
design (otherwise the successor would start seconds later and compaction's "wait for zero in-flight"
poll would never see its zero), and `cleanup-index`'s own `handback` job dispatched the successor
3 seconds into its run. The cancelled run `34189090867` lost nothing — `GET .../jobs` returns an
empty list; it never got a runner, and the workflow's own comment anticipates exactly this
displacement. `request-compaction` is skipped in every run including the successful ones, correctly:
`_deletions` sits at 576 against a 3,000 threshold.

**The cron backstop is not credible on this evidence, and that is the finding of this section.**
Six scheduled events delivered against 24 expected slots in 24 hours (25%), 6–32 min late when
delivered, and **not one seeded a productive run** — every scheduled event in the last 60 runs was
cancelled. When the chain genuinely broke on 2026-09-07 (three consecutive failures tripped the
breaker, which logs *"Fix the run; the cron reseeds it"*), no scheduled event was delivered for
**4 h 07 m** across four consecutive missed slots. Recovery took two manual dispatches from the repo
owner and ~55 min of idle — two, because the first defaulted `chained=false` and correctly ended
with itself. A stopped chain looks like nothing at all: no red run beyond the one that tripped the
breaker, no alert, just an absence.

Storage is the binding constraint, not Actions minutes. The repo is public and GitHub bills
`total_ms: 0`. The `live` dataset figure ratchets +0.02–0.03 GB/run and dropped −0.33 GB at run E
— explained, not mysterious: compaction ran at 07:49Z, so E uploaded 5 LanceDB LFS files instead of
50–62. At ~18 full runs/day the LFS rewrite is the cost that scales with cadence, which is the
entire reason compaction must be able to win a window, which is the entire reason the stand-down
exists. Cadence and storage are one problem.

## 7. Defects found, all verified against the code

1. **`scrape_plan.py:492` — the floor warning can never fire.** `even` is serial per-shard minutes
   (~171), `floor` is wall-clock minutes (~23), and `if floor > even` asks `23 > 171`. The honest
   test is `floor > even / ratio` (23 vs ~13, i.e. 1.77x). The warning under-fires by the whole
   speedup factor, in all five runs, on the one condition that is true every time. The comment four
   lines above warns against mixing exactly these two units.
2. **`fanout_merge.py:146` — `_ATS` is missing four shipped providers.** The set has 21 entries
   against the registry's 25; `icims`, `zwayam`, `jazzhr`, `jobvite` are absent, and since
   `_ID_BOUNDARY` is built by joining it, those ids create no boundary and are swallowed into the
   preceding id. The loss is then **whole batches, not stray ids**: the parse count disagrees with
   the batch's own `[start-end]` header and the existing guard `continue`s over all 100 of them,
   silently. Measured per run: **658 / 553 / 580 / 312 add ids discarded — 48% of the control
   run's 1,358** — hiding the entire iCIMS ramp-in (1,503 adds) from every reader. (An earlier
   pass quoted 38%; that counted only the 516 ids the boundary itself failed to produce and so
   understated the loss by the remainder of each dropped batch.) Fix: derive it from
   `registry.SCRAPERS`, anchor the boundary to whitespace, and make the drop loud.
3. **`fanout_errors.py:77` — stale `FAILURES` pattern.** Expects `N board(s) reported gone`;
   `update_ledgers.py:191` emits `N of M board error(s) read as gone`. `search` returns `None`, the
   `if totals:` skips, quarantine totals vanish with no error. `run_logs.warn_if_unparsed` exists
   precisely for this and is wired into `fanout_corpus` and `fanout_ledgers` — but not into the one
   analyser whose pattern actually drifted. Third recurrence of this failure mode in the package.
4. **~~`cleanup-index.yml:107` — queued-run blind spot~~ — WITHDRAWN, not a defect.** The
   mechanism is real and reproduces live: `cleanup-index.yml:107` polls `--status in_progress`,
   which cannot see a queued run, while `pipeline.yml:171` uses `!= "completed"` and its comment
   notes that this *"also matches `queued`"*. Measured on 2026-09-08, `in_progress` returned 1
   where `!= completed` returned 2. Run `34202399791`, created 08:02:49 and still queued, was
   invisible to the 08:03:00 poll, and compaction overlapped a pipeline run for 68 s.

   **But the overlap is inert, and the obvious fix is harmful.** `pipeline.yml:188-197` gates
   `pip install -e .`, `state_fetch` and `scrape_plan` on `steps.gate.outputs.run`, so a run that
   boots during compaction stands down *before touching any data* — the second guard is not a
   lucky catch, it is the designed exclusion, and the first one is only a cheap pre-filter.
   Changing the window to `!= completed` would then count the chain's own queued successor and
   any late `schedule` event: run `34212932129` sat `pending` for 22+ minutes before the chain
   cancelled it, which would have eaten half of cleanup's 45-minute wait and pushed it toward its
   six-attempt `exit 1` — starving compaction into precisely the `_deletions/` overflow ADR-0071
   exists to prevent. Cost of the current behaviour is one stood-down run, 1.2 job-minutes, which
   `pipeline.yml:164-165` already calls an acceptable price. **Leave it alone.**

5. **`fanout_plan.py` and `fanout_timing.py` hard-fail on a stood-down run**, exiting with
   `gh: HTTP 404` when they try to fetch a skipped job's log. `fanout_plan` also prints
   `value gate: no boards skipped this run` for such a run — a tool artefact reported as a
   measurement, which is the exact shape of error the analysers exist to prevent.

## 8. Ranked actions

1. **Fix defects #1, #2, #3 and #5 above.** #2 and #3 are corrupting the numbers any future
   review reads; #1 silences a true warning; #5 crashes the analysers and, worse, reports a tool
   artefact as a measurement first. #4 is withdrawn — see above; it is working as designed.
2. **`careers.hcltech.com`.** It is simultaneously the scrape critical path (92–95% floor, 5/5) and
   73% of ADR-0053's shielded rows, via the same root cause: detail-fetch throughput and failures on
   one host. One board, two of this review's three biggest problems. Splitting or capping its detail
   pass buys ~6.4 min of wall without asking SAP anything; a measured two-width probe would be
   needed before touching `_DETAIL_WORKERS`.
3. **Tech-filter precision on oracle and icims.** 37–42% of marginal net rows are non-tech against a
   22.4% stock rate, corroborated across two independent days.
4. **Decide on `_TARGET_SECONDS`.** 5.5–10.6 min of wall for ~+37 runner-minutes/run, currently free
   on a public repo. A budget call.
5. **Treat the cron as decorative and monitor the chain directly.** 25% delivery and 0 productive
   runs in 24h; the one real break needed a human. Absence of runs is currently silent.
6. **Re-measure `unconfirmed again` and ADR-0053 over ~24 runs**, not five.

## 9. What this window cannot answer

Five runs over five hours on one SHA is a strong design for isolating code from mix, and a weak one
for trends. Specifically: the trakstar and teamtailor incidents are n=1 and n=2; the embed runner
lottery is 13 samples; the ADR-0053 and unconfirmed-queue slopes are 4 intervals; and the daily
job-minute and storage projections rest on a single 24-hour window that contains an unrepresentative
4-failure streak. The two claims that *are* corroborated across independent windows — non-tech
marginal share, and the hcltech/wipro ratchet — are flagged as such above; treat the rest as
hypotheses with arithmetic attached.

Cross-day note on ADR-0053: yesterday's review concluded "stable, not accreting" at 2,321–2,433 rows
across 35–41 Boards. Today the same measure reads 2,857–3,009 rows across 85–96 Boards, with
hcltech 1,323 → 1,418 and wipro 670 → 726 over the day. Whether the Board-count jump reflects real
escalation or a differing slice is not resolvable from these logs, but it is the number that
yesterday's own advice said to watch across days rather than runs, and it moved.
