# WARP install fix — validation against five post-fix runs

**Date:** 2026-08-20 · **Verdict:** the fix landed and is mechanically confirmed; degradation is
reduced ~3x but **not** eliminated, and the residual has a different cause.

Method: `analyse-fanout-run`, steps 0-5, using `scripts/runlog/`. Every log read through the
`[36;1m` echo filter. The decisive signals are the **install duration** (mechanical — it moves only
if the package set changed) and the **429/network retry ratio** (a ratio no logging edit can fake),
per the skill's step 5, which warns that a bare degradation count is contention-dependent.

## The arms

| run | sha | arm |
| --- | --- | --- |
| 32261793515 | d45ce12 | PRE |
| 32272854468 | 8f40109 | PRE |
| 32283672442 | e4ccecf | POST |
| 32294914542 / 32305680181 / 32314496616 / 32325077157 | 22332f1 | POST |

`8405ff4` (the fix) is an ancestor of `e4ccecf` and `22332f1`, and not of `d45ce12`/`8f40109` —
checked with `git merge-base --is-ancestor`, not inferred from dates.

## 1. The install collapsed — this is the fix, measured

`apt-get install cloudflare-warp` seconds, per shard, 15 shards per run:

| run | arm | median | min | max |
| --- | --- | --- | --- | --- |
| d45ce12 | PRE | **23** | 11 | 126 |
| 8f40109 | PRE | **28** | 22 | 88 |
| e4ccecf | POST | **5** | 4 | 29 |
| 22332f1 (a) | POST | 17 | 5 | 141 |
| 22332f1 (b) | POST | 16 | 4 | 141 |
| 22332f1 (c) | POST | **4** | 3 | 13 |
| 22332f1 (d) | POST | **4** | 3 | 12 |

Three of five post-fix runs sit at a 4-5s median against 23-28s before — a **~6x reduction**, which
is the shape stubbing 229 MB across 294 packages (`cloudflare-warp` Depends on
`libwebkit2gtk-4.1-0`) predicts. The handoff's expectation was "median ~10, no tail"; the median
beat that.

This is the claim to trust. It is mechanical: nothing but a smaller package set moves an install
median 6x, and it cannot be produced by luck in contention.

## 2. Degradation fell ~3x but is not zero

Shards losing WARP, by the retry-ratio detector (`fanout_retries.py`), which agreed with the
`degrading to direct` log line in **7 of 7 runs** — no mismatches in either direction:

| arm | degraded shard-runs | rate |
| --- | --- | --- |
| PRE | 4 / 30 | 13.3% |
| POST | 3 / 75 | 4.0% |

Per run, POST reads 0, 1, 2, 0, 0. So the fix did not drive this to zero, and a single post-fix run
reading 0/15 would have proved nothing — the pre-fix control `8f40109` also read 0/15, which is why
step 5 exists.

## 3. The residual has a different cause, and the data names it

The two post-fix runs that still degraded are **exactly** the two with a slow-install tail:

| run | degraded shards | install median | install max |
| --- | --- | --- | --- |
| 32294914542 | 1 | 17s | **141s** |
| 32305680181 | 2 | 16s | **141s** |
| 32283672442 | 0 | 5s | 29s |
| 32314496616 | 0 | 4s | 13s |
| 32325077157 | 0 | 4s | 12s |

Perfect correlation across five runs. Package size is fixed — the median proves it. What remains is
that the install *occasionally* still takes 100-141s on some shards, and those shards are the ones
that fall back to direct. That points at apt/mirror/network contention on the runner, not at the
dependency tree, so **it is a different fix from the one that shipped**. n=5 runs; treat the
correlation as a strong lead, not a proven mechanism.

The cost when it happens is large: a degraded shard spends 14,600-23,800 rate-limit retries against
~2,000 on a healthy one — 16,523 and 47,563 excess retries in those two runs respectively.

## 4. Workday recovered from the collapse, but not to the old band

`workday.jsonl` lines, per run: 494,891 (the collapse) → 514,326 → 541,801 → **593,798 → 517,275 →
549,810 → 556,695 → 549,216**.

Post-fix mean ≈ 553k, stable, well clear of the 494,891 collapse — but still short of the
632,578-672,050 band the handoff recorded across four earlier runs. **So the WARP fix was not the
cause of the workday shortfall.** That confirms the handoff's own hypothesis: the pagination
fan-out is an independent cause, which is what #194 and #195 exist for.

Corpus totals are steady across all seven runs (1.26-1.35 M scraped, 20.2-20.8% tech), so the
workday figure is not an artefact of a smaller slice.

## 5. Wall clock and the owner — unchanged in character

| run | wall | owner | share | worst floor |
| --- | --- | --- | --- | --- |
| 32283672442 | 79.2m | scrape 41.4m | 52% | walmart **97%** |
| 32294914542 | 68.4m | scrape 28.8m | 42% | hcltech 93% |
| 32305680181 | 73.6m | scrape 34.6m | 47% | careers.ey.com 92% |
| 32314496616 | 53.7m | scrape 26.6m | 49% | careers.ey.com 93% |
| 32325077157 | 56.4m | scrape 27.8m | 49% | hcltech 93% |

Scrape owns every run at 42-52%. **Every single run has a shard that is 92-97% one board** — the
floor-bound signature is now confirmed across seven consecutive runs, with only three distinct
boards responsible (`careers.ey.com`, `hcltech.jobs.hr.cloud.sap`, `walmart`). No packer improvement
touches this; it needs a per-board timeout or splitting those three boards.

Cost-model health is fine: `actual/predicted` medians 0.79-0.97, so the model is not the problem.

One anomaly worth noting: run 32305680181 carried **5.2 min of queue/setup** against 0.1-0.2 min
everywhere else. Single occurrence, no explanation in the logs — watch rather than act.

## What to do next

1. **The shipped fix is done.** Do not spend more on package size; the median proves it landed.
2. **The residual is install-time contention**, not dependencies. If it is worth chasing, the lead
   is why an install occasionally takes 141s — retry/backoff around `apt-get`, or a mirror choice.
3. **Workday's shortfall is #194/#195**, now positively confirmed as independent of egress.
4. **Three boards own the critical path.** Splitting or time-boxing `careers.ey.com`,
   `hcltech.jobs.hr.cloud.sap` and `walmart` is the single highest-value scrape change available,
   and under the back-to-back cadence (ADR-0071) it converts directly into extra runs per day.
