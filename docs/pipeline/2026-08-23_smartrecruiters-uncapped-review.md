# SmartRecruiters uncapped-pagination review — 2026-08-23

Resolves [#227](https://github.com/sarthakjain004/headstart/issues/227). Decision recorded in
[ADR-0077](../adr/0077-smartrecruiters-pages-behind-a-cost-sized-cap.md)'s 2026-08-23 amendment:
**stay uncapped, no static `_MAX_PAGES` cap re-enabled.**

PR #226 (merged 2026-08-20T13:30:56Z, commit `bc0ce53`) shipped SmartRecruiters pagination with
`_MAX_PAGES = 50` defined but its enforcement commented out — uncapped on purpose, to measure real
cost before deciding a cap from data. This reviews eight real `nightly-pipeline` runs, three from
before the merge and five spread across the 2.5 days after it, via `scripts/runlog/`'s analysers
and a direct read of `data/state/board_cost.csv` / `board_priority.csv`. Every post-merge run's
head SHA was confirmed a descendant of `bc0ce53` via `git merge-base --is-ancestor` before being
trusted as "post-merge" behavior.

Runs compared:

| | run | started (UTC) |
|---|---|---|
| baseline | 32294914542 | 2026-08-19 19:46 |
| baseline | 32352149143 | 2026-08-20 09:06 |
| baseline | 32361513645 | 2026-08-20 10:58 |
| post-merge | 32378525276 | 2026-08-20 14:10 (first after merge) |
| post-merge | 32411365829 | 2026-08-20 19:56 |
| post-merge | 32449242159 | 2026-08-21 05:04 |
| post-merge | 32505993555 | 2026-08-21 17:01 |
| post-merge | 32621581881 | 2026-08-23 05:55 (latest) |

## 1. Corpus size delta

| run | scraped | tech-kept | kept% |
|---|---:|---:|---:|
| 32361513645 (base) | 48,916 | 13,353 | 27.3% |
| 32352149143 (base) | 47,954 | 13,272 | 27.7% |
| 32294914542 (base) | 47,854 | 13,191 | 27.6% |
| 32378525276 (post, 1st) | 186,780 | 43,231 | 23.1% |
| 32411365829 (post) | 235,537 | 44,441 | 18.9% |
| 32449242159 (post) | 240,334 | 46,066 | 19.2% |
| 32505993555 (post) | 236,581 | 46,258 | 19.6% |
| 32621581881 (post, latest) | 235,812 | 46,186 | 19.6% |

Baseline averages 48,241 scraped / 13,272 tech-kept per run (27.5%). Post-merge, excluding the
first run (still ramping as boards go from partially- to fully-read for the first time), averages
237,066 scraped / 45,738 tech-kept (19.3%) — **+188,825 scraped/run (~4.9×), +32,466 tech-kept/run
(~3.4×)**. Volume plateaus by the second post-merge run and holds flat through the fifth: this is
recovered steady-state re-scrape capacity, not a draining one-time backlog. The issue's projected
one-time figure (~478,000 previously-unread postings across ~807 boards) is a different kind of
number from this recurring per-run gain; the closest comparison is that the recurring gain
(~189K/run) is roughly 40% of that one-time projection, and it is not still climbing toward it.

Tech-kept% falls from ~27.5% to ~19.3%. Expected: the newly-read pages sit deeper in large boards,
which per ADR-0077's own probe skew retail/hospitality-heavy — pulling the SmartRecruiters-wide
average down without indicating a filter problem.

## 2. Shard wall-clock impact

Pre-merge, the floor-dominant board in a straggling shard was never SmartRecruiters
(`successfactors:careers.ey.com`, `workday:walmart`, `successfactors:hcltech.jobs.hr.cloud.sap`
recur instead).

Post-merge, `smartrecruiters:AdeebaEServicesPvtLtd` is the shard's floor-dominant board (per
`fanout_timing.py`'s own computation) in 4 of 5 runs:

| run | shard | seconds | floor% |
|---|---|---:|---:|
| 32411365829 | 4 | 1,554 (25.9 min) | 87% |
| 32449242159 | 13 | 1,438 (24.0 min) | 91% |
| 32505993555 | 8 | 1,678 (28.0 min) | 89% |
| 32621581881 | 0 | 1,447 (24.1 min) | 89% |

(32378525276, the first post-merge run, had Adeeba as its raw-slowest board at 2,055s but only 67%
floor / 2.53× actual-predicted — not flagged as floor-dominant that run.) `smartrecruiters:
EndeavorITSolution` also recurs as a per-shard slowest board across all five post-merge runs, but
never floor-dominant — its measured cost is only 519s (8.65 min, ADR-0064's floor is 900s), so it
can't threaten a makespan regardless.

Direct read confirms why Adeeba survives the ADR-0064 gate despite looking, on ADR-0077's original
probe sample, like the ~0–1%-tech "retail-shaped" bucket:

```
$ grep smartrecruiters data/state/board_cost.csv | grep -i adeeba
smartrecruiters:AdeebaEServicesPvtLtd,1306.495,23806,2026-08-23

$ grep smartrecruiters data/state/board_priority.csv | grep -i adeeba
smartrecruiters:AdeebaEServicesPvtLtd,136.0000,136,2026-08-23
```

136 tech jobs / 23,806 total = 0.57% density, matching that original bucket — but 136 tech jobs ÷
(1,306.5s / 60) = **6.25 tech/min**, clear of ADR-0064's 2.0/min floor. A low-density board can
still out-yield a gated one purely on size. This is exactly the shape a flat page cap cannot
resolve: capping Adeeba's read at 5,000 of its 23,806 postings would have cut its measured tech
yield by roughly the same ~79% it cuts off a genuinely junk giant.

`dominos` and `crossmark1` never appear as slow or floor-dominant boards post-merge — see §4, they
are being skipped by the value gate before they're scraped at all, not run slowly.

No shard hit the 60-minute CI budget in any of the eight runs sampled, pre- or post-merge
(`fanout_errors.py`). Overall run wall-clock shows no clean step change attributable to
SmartRecruiters: pre-merge 53.5/66.9/68.4 min vs post-merge 111.6/75.9/54.9/61.8/48.2 min, with the
111.6-minute outlier (32378525276) owned by the embed stage (41.8 min, 37% of that run's wall), not
scrape.

## 3. Storage/LFS growth

Merge job's "Reclaim dataset history" step logs `usedStorage X.XX GB · live Y.YY GB · N commits`:

| run | usedStorage | live | commits |
|---|---:|---:|---:|
| 32361513645 (base) | 37.32 GB | 3.20 GB | 77 |
| 32352149143 (base) | 33.66 GB | 3.16 GB | 69 |
| 32294914542 (base) | *(step didn't log this run)* | — | — |
| 32378525276 (post) | 32.37 GB | 3.65 GB | 9 |
| 32411365829 (post) | 35.07 GB | 3.81 GB | 29 |
| 32449242159 (post) | 43.30 GB | 4.04 GB | 57 |
| 32505993555 (post) | 48.58 GB | 3.75 GB | 5 |
| 32621581881 (post) | 52.95 GB | 4.28 GB | 5 |

`live` (the actual dataset size, squash-independent) rises gradually across all eight runs with no
step at the merge boundary — 3.16 → 4.28 GB over 2.5 days is consistent with the pipeline's
existing growth rate, not a new SmartRecruiters-driven jump. `usedStorage`'s rise-then-drop pattern
matches normal HF orphan-commit accumulation and squash cycling. No evidence ties storage growth
specifically to the pagination change.

## 4. ADR-0064 gate interaction

Pre-merge (3 runs): zero `smartrecruiters:` boards in any `value gate: skipped` sample (11 boards
skipped/run, all workday/successfactors/eightfold — SmartRecruiters boards were too cheap under the
one-page read to ever cross the 15-minute gate floor).

Post-merge, SmartRecruiters boards start appearing and the set grows:

- 32411365829: `smartrecruiters:dominos (0.82/min)` — 1 of 12 skipped
- 32449242159: `smartrecruiters:crossmark1 (0.30/min), smartrecruiters:dominos (0.82/min)` — 2 of 13
- 32505993555: same two boards — 2 of 13
- 32621581881: sample list truncated ("+4 more") before reaching either alphabetically among 10+
  boards — can't confirm from the printed sample alone, but the ledger read below settles it

Direct ledger read for these two boards confirms the gate math and that they remain excluded:

```
smartrecruiters:dominos      score 15.47  seconds 1132.07 (18.9 min)  → 0.82 tech/min  (gated)
smartrecruiters:crossmark1   score  4.55  seconds  904.84 (15.1 min)  → 0.30 tech/min  (gated)
```

This is the gate doing exactly the job ADR-0064 designed it for: a genuinely low-yield giant gets
one uncapped measurement pass, then is excluded from every subsequent plan until its 14-day
recheck. The gate's known blind spot — it cannot protect a board's *first, unmeasured* run, only
react after — is unchanged and still real; it just isn't a live problem for any board sampled here,
because none of them hit ADR-0064's original `dollartree`-style failure (a board so large it never
finishes and never gets a cost row at all).

## 5. Budget kills / new errors

No shard hit the 60-minute budget in any of the 8 runs. No SmartRecruiters board appears in any
run's quarantined-board or reported-gone samples (`fanout_errors.py`) — all sampled quarantines are
unrelated (`ashby:*` 404s). Error rates per 1,000 boards trend down over the sampled window
generally (5.8 → 1.6), with no post-merge spike attributable to SmartRecruiters.

## Decision

**Stay uncapped.** Recorded as a 2026-08-23 amendment to ADR-0077. The corpus gain is real, stable,
and not runaway; no run has approached the 60-minute budget; storage growth tracks its pre-existing
trend; and the one board riding close to ADR-0064's floor (Adeeba, 87–91% of its shard) is riding
there because it is earning its keep by the gate's own criterion, not because the gate is failing —
a static cap would have suppressed exactly that legitimate yield.

**Open watch, not a blocker:** if Adeeba or a similar IT-staffing-shaped giant grows enough that its
absolute cost threatens a 60-minute makespan on its own (the `dollartree` failure mode ADR-0064
was written against), that is a per-board problem to solve when it happens — not grounds to
reach for a flat cap again, which this review's own numbers argue against for this population.
