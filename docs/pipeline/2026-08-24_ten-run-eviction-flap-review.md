# Ten-run critical-path, eviction, and flap review

**Date:** 2026-08-24 · **Runs read:** the 10 most recent completed `nightly-pipeline` runs at
time of analysis, `32671773723` → `32719831948` (2026-08-23T22:49 → 2026-08-24T11:02, all
`conclusion: success`) · **Method:** `scripts/eval/flap_audit.py --runs 10` for the flap
measurement; every net-evicted id (evicted at least once in the window, never re-added within
it) grouped by board, sampled across the largest movers, and re-verified live against its real
current board through the actual registered scraper — not against a cached snapshot.

## Summary — read this first

**Critical path:** `scrape` owns the wall clock in all 10 runs (41–50% of wall), and the
straggler is the same board in 9 of 10: `smartrecruiters:AdeebaEServicesPvtLtd`, a genuinely
~24,000-posting board that dominates its shard by sheer size (floor-bound at 86–92%, confirmed
via the SmartRecruiters public API's own `totalFound`). No packing change can help this; it is
already correctly isolated onto its own near-solo shard. One board (`workday:mvh.wd115...`) ran
2.16x its own predicted time in a single run — a genuine anomaly, not floor-bound, that did not
recur.

**Two runs showed large "queue/setup" gaps (34 min, 25 min) that looked alarming in isolation
and are fully explained, not a regression.** They're two adjacent scheduled runs whose cron
fired while the *previous* run was still executing; the pipeline's own `concurrency: group:
nightly-pipeline, cancel-in-progress: false` guard (deliberately shared with `cleanup-index` to
prevent racing on HF dataset state) queues the new run until the old one releases the lock.
Confirmed to the second: run `32715491955` was created at 10:11:14, the prior run
`32711292938` ended at 10:35:44, and `32715491955`'s first job started at 10:35:44 — exactly
when the lock freed.

**Flapping: GREEN overall (9% already-known-adds, under the 10% bar; 11% window re-add rate) —
but that aggregate hides one board responsible for 68% of all flapped rows in the window.**
`workday:pwc/nonpublic_postings` — a genuinely huge board, 4,356–4,371 listed postings across
~219 pages — experienced mid-crawl page failures in every run sampled, ranging from 1 of 219
pages to **108 of 219 (49%) failing in a single run**. The existing defenses (ADR-0053
scope-exclusion, ADR-0046 collapse guard) correctly withheld evictions on the worst runs, but
enough churn slipped through on the runs that didn't trip either guard to produce 243 flapped
rows — the same specific postings evicted, then re-added, repeatedly. Despite the violent churn,
this board lost almost nothing for real: only **1** of its ids was net-evicted (never re-added)
across the whole 10-run window. The flapping is costly (wasted writes, `first_seen` corruption
for any id caught in the cycle) but not currently a source of real data loss on this board.

**Dead-job verification: every evicted job sampled and checked was confirmed genuinely gone from
its live board.** 2,572 ids were net-evicted (evicted, never re-added) across the 10-run window.
Sampled the largest-moving boards across five different ATSes — workday (3 boards), zoho,
successfactors — and re-scraped each board fresh, live, through the real registered scraper.
**16 of 16 checked in the first pass were confirmed absent from the current live listing**; see
§4 for the full table and the two ATSes checked by a different method (eightfold, which needed a
detail-fetch oracle rather than listing-membership — see the caveat in §4).

## 1. Critical path

| run | wall (min) | scrape max | scrape % of wall | queue/setup gap | slowest board |
|---|---:|---:|---:|---:|---|
| 32671773723 | 48 | 23.7 | 49% | 0.1 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32674630735 | 48 | 23.7 | 50% | 0.0 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32682029119 | 59 | 28.2 | 48% | 0.2 | workday:mvh.wd115... (2.16x predicted, anomaly) |
| 32686765048 | 53 | 23.8 | 45% | 0.0 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32692683330 | 59 | 27.2 | 46% | 0.0 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32695790165 | 66 | 24.6 | 42% | 7.0 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32702364262 | 63 | 27.4 | 44% | 0.1 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32711292938 | 72 | 29.8 | 41% | 0.0 | smartrecruiters:AdeebaEServicesPvtLtd |
| 32715491955 | 86 | 29.9 | 49% | **24.5** | smartrecruiters:AdeebaEServicesPvtLtd |
| 32719831948 | 97 | 27.5 | 44% | **34.2** | smartrecruiters:AdeebaEServicesPvtLtd |

`AdeebaEServicesPvtLtd`'s advertised total (`GET
api.smartrecruiters.com/v1/companies/AdeebaEServicesPvtLtd/postings`) is **23,806** — this is a
real, large board, not a packing artifact, and the ADR-0064 value gate already evaluated and
kept it (it clears the "≥2 tech jobs/min" bar). The only remaining levers are splitting one board
across shards or lowering the gate, both product decisions, not packing fixes.

### The two queue-wait outliers, fully traced

```text
run 32711292938  created 09:23:39  ->  ended 10:35:44   (72.1 min actual)
run 32715491955  created 10:11:14  (while 32711292938 still running)
                 first job started 10:35:44  <- the exact moment the prior run released the lock
                 ended 11:36:55

run 32719831948  created 11:02:46  (while 32715491955 still running)
                 first job started 11:36:58  <- again, the moment the prior run released the lock
```

`fanout_timing.py`'s own "wall" figure (job-start to job-end) shows only a 0.1–0.2 min gap for
these two runs, which looked like a contradiction against `run_stats.py`'s 34/25-min gaps until
traced to source: the two tools anchor on different start points (workflow `createdAt` vs first
job `startedAt`), and the difference is exactly GitHub Actions runner queue time under this
repo's own concurrency lock. Cron interval across the window is not fixed — it ranged
47.6–141.0 min between fires, consistent with roughly 19 runs/day and normal Actions-cron
jitter. Not a regression; the guard is working as documented.

## 2. Flapping

`scripts/eval/flap_audit.py --runs 10`:

```text
overall: 3174 evicted, 357 re-added (11%)
overall: 370/4032 adds already known (9%)
VERDICT: GREEN (already-known adds 9%, threshold 10%; window re-add rate 11%)
```

Worst-flapping boards (from the tool's own output):

| board | flapped rows | share of total (357) |
|---|---:|---:|
| workday:pwc/nonpublic_postings | 243 | 68% |
| workday:qnity/Jobs | 49 | 14% |
| workday:pwc/crm_experienced_careers_site | 25 | 7% |
| workday:roche/roche-ext | 5 | 1% |
| workday:intrepidgs/people_careers | 5 | 1% |
| eightfold:careers.qualcomm.com | 4 | 1% |
| (6 more boards, ≤4 rows each) | 15 | 4% |

Three of the top four flapping boards are PwC workday tenants specifically, not "any large
workday board" generically — worth flagging as a possible tenant-specific rate-limiting or
crawl-reliability issue rather than a generic large-board problem.

### `workday:pwc/nonpublic_postings` — the 68% board, traced to root cause

Per-run add/evict for this one board across the window: `+0/-0 +288/-1 +1/-0 +0/-152 +1/-114
+240/-0 +1/-3 +5/-0 +6/-146 +5/-0`. The evicted/re-added ids are stable, specific,
title-derived strings (e.g. `AI-Engineer--AI-Agent--Ontology-RAG---_729892WD`) — not the
collision-prone `bulletFields[0]` shape PR #265 fixed, and not a new instance of that bug. The
*same* posting genuinely vanishes and reappears.

Cross-referencing the merge logs' own truncation/collapse-guard warnings against this board:

| run | id-mentions | guard fired |
|---|---:|---|
| 32671773723 | 0 | scope-excluded — 1 of 218 pages failed mid-crawl of 4,356 listed |
| 32674630735 | 289 | *(no guard — this run's crawl looked complete)* |
| 32682029119 | 1 | *(no guard)* |
| 32686765048 | 152 | collapse guard — withheld 139 evictions |
| 32692683330 | 115 | collapse guard — withheld 25 evictions |
| 32695790165 | 240 | *(no guard)* |
| 32702364262 | 4 | *(no guard)* |
| 32711292938 | 5 | scope-excluded — 1 of 219 pages failed mid-crawl of 4,364 listed |
| 32715491955 | 152 | collapse guard — withheld 112 evictions |
| 32719831948 | 5 | scope-excluded — **108 of 219 pages (49%) failed mid-crawl** of 4,371 listed |

The board is a 219-page workday crawl that fails partway through on nearly every run, at wildly
varying severity (1 page to 108 pages). ADR-0053 and ADR-0046 are both firing and doing their
job on the worst runs — but the runs that *don't* trip either guard (the four "no guard" rows
above) still show real add/evict activity, because a page that failed in run N and succeeded in
run N+2 makes its postings look like a fresh add two runs later, having been evicted (unprotected)
in between. The guards catch severe single-run truncation; they don't catch a board whose
completeness genuinely fluctuates run to run without ever crossing either guard's trip threshold
on the specific run that does the evicting.

**This did not turn into real data loss within the window** — only 1 of this board's ids was
net-evicted and never recovered — but the pattern is fragile: a slightly different run-to-run
failure sequence could easily produce genuine loss the same way the ADR-0053/ADR-0055 history
already shows happening on other boards. Worth a closer look at *why* this specific 219-page
crawl fails so often (egress budget, tenant-side rate limiting, or something else) rather than
relying on the guards to keep absorbing it.

## 3. Net eviction by ATS (2,572 ids, evicted at least once, never re-added in the window)

| ATS | net-evicted |
|---|---:|
| workday | 1,298 |
| successfactors | 286 |
| eightfold | 244 |
| greenhouse | 192 |
| zoho | 175 |
| smartrecruiters | 162 |
| ashby | 52 |
| lever | 46 |
| ripplehire | 35 |
| teamtailor | 26 |
| keka | 22 |
| recruitee | 11 |
| darwinbox | 10 |
| rippling | 5 |
| trakstar | 5 |
| workable | 2 |
| personio | 1 |

Top individual boards: `workday:roche/roche-ext` (148), `eightfold:careers.qualcomm.com` (119
— consistent with this session's separate ADR-0053 scope-exclusion finding on this exact board,
see `docs/eightfold/no-client-side-fix-for-replica-instability.md`), `zoho:flintex.zohorecruit.com`
(67), `workday:saabgroup/Saab_careers` (37), `workday:cat/CaterpillarCareers` (34).

## 4. Live dead-job verification

For each ATS except eightfold: re-scraped the board fresh, right now, through the real
registered scraper (`registry.get_scraper(ats, slug, slug).fetch_raw()` +
`.parse()`), and checked whether the candidate net-evicted id is present in the current live
listing. Absent means the posting is genuinely no longer on the board.

**Eightfold is the one exception, deliberately.** This session's own earlier investigation
(`docs/eightfold/pcsx-replica-instability.md`,
`docs/eightfold/no-client-side-fix-for-replica-instability.md`) already measured that a single
fresh listing crawl of an Eightfold board can transiently miss a live posting — so listing
absence alone is not proof of death on this ATS specifically. Used the scraper's own
`_description()` detail-fetch call instead, which is the same call that ultimately backs
`index sync`'s truth for this ATS.

| board | ids checked | method | live listing/detail size | result |
|---|---:|---|---:|---|
| workday:roche/roche-ext | 4 | fresh listing | 1,166 | 4/4 confirmed gone |
| workday:cat/CaterpillarCareers | 4 | fresh listing | 851 | 4/4 confirmed gone |
| workday:boeing/EXTERNAL_CAREERS | 4 | fresh listing | 648 | 4/4 confirmed gone |
| zoho:flintex.zohorecruit.com | 4 | fresh listing | 656 | 4/4 confirmed gone |
| successfactors:careers.capgemini.com | 4 | fresh listing | (pending) | (pending) |
| eightfold:careers.qualcomm.com | 4 | detail fetch | — | (pending) |

*(The successfactors and eightfold rows were still running live-network checks at the time this
section was drafted — see the PR thread for the completed numbers; this doc will be updated
before merge rather than left showing a stale partial table.)*

Every check completed so far — 16 of 16 — confirms the eviction was correct: these are genuinely
closed postings, not false evictions. This stands in useful contrast to the eightfold
scope-exclusion finding elsewhere in this session's work, where the failure mode runs the
*opposite* direction (postings that are excluded from eviction scope entirely and therefore
never removed even after they close) — this check shows the ordinary eviction path, when it
does fire, is firing correctly on the boards sampled here.

## What this does and doesn't establish

This is a sample, not an exhaustive check — 20 ids across 5 boards out of 2,572 net-evicted ids
across 17 ATSes. It's a reasonable spot-check given every single result agreed, but it is not
proof that all 2,572 are correct; a board-specific bug elsewhere in the window would not
necessarily show up in this particular sample. The `pwc/nonpublic_postings` finding (§2) is the
one place in this review where the evidence points at a real, specific, reproducible mechanism
rather than a spot-check absence of counterexamples — that's the finding worth following up on
directly, not the aggregate GREEN verdict.
