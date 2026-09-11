# Five-run log review — 2026-09-10

Runs reviewed (all `nightly-pipeline`, all head `2503be7`, **all green**, chronological):

| Run | Started (UTC) | Wall | add / evict | net | Table after | Board errors |
|---|---|---|---|---|---|---|
| 34450830376 | 07:36 | 53.1 min | 922 / 215 | +706 | 410,516 | 20 |
| 34455486313 | 08:29 | 0.3 min | — stood down — | — | — | — |
| 34455705954 | 08:32 | 61.6 min | 1,221 / 461 | +760 | 410,570 | 26 |
| 34461364139 | 09:34 | 55.5 min | 1,375 / 443 | +930 | 411,500 | 28 |
| 34466352099 | 10:29 | 49.4 min | 1,031 / 305 | +724 | 412,224 | 36 |
| 34470668397 | 11:19 | 50.0 min | 790 / 322 | +468 | 412,692 | 22 |

Every run reported success. Every finding below comes from reading the logs of runs that passed.

These runs are the first five to run **after** the logging overhaul (#399, `cf7e326c`) landed —
`git merge-base --is-ancestor cf7e326c 2503be73` confirms it. So §2 is a live gap in shipped work,
not a pre-existing one.

---

## 1. A completed run's index write was silently discarded — 706 rows

Run 34450830376 finished its sync at **410,516** rows. The next run *opened* the table at
**409,810** — its predecessor's **pre-sync** count. The entire `+706` was reverted, with no error,
no warning, and no failed job.

Every other boundary in the window chains correctly (410,570 → 411,500 → 412,224 → 412,692), so
this is one event, not drift.

**The mechanism is a read-modify-write lost update between two workflows.** Timestamps from both
runs' own logs:

```
08:22:32  cleanup-index 34452134890: "no pipeline in flight — compacting"
                                     (pipeline 34450830376 had been in flight since 07:36:48)
08:26:51  cleanup-index reads the table          409,810
08:27:05  cleanup-index rebuilds 'jobs'          409,810   ← snapshot taken
08:27:55  pipeline merge reads the table         409,810   ← same base, independently
08:28:30  pipeline merge writes                  410,516   (+706)
08:29:33-49  pipeline uploads → 410,516 lands on HF
08:30:27  cleanup-index uploads → 409,810 overwrites it.   −706
```

**Neither guard is a lock; both are one-shot polls of an eventually-consistent API.**

- `cleanup-index`'s `window` job polls `gh run list --status in_progress`. It behaved correctly for
  ~30 minutes (30 × `pipeline in flight (1); re-checking in 60s`) and then read **0** at 08:22:32
  while the pipeline demonstrably ran until 08:30:06.
- `pipeline.yml`'s gate (line 177) is checked **once, in `scrape-plan`** — roughly 50 minutes before
  the merge job performs the dangerous write. A pipeline that starts before a compaction begins
  passes the gate and then writes straight into the compaction's window.

The merge job's own comment asserts otherwise:

> `always()` so a partial fan-out still publishes what it got — but NOT when the gate stood down.
> This is the one job that would otherwise ignore the gate, and it is precisely the job that writes
> LanceDB, so a compaction it was meant to yield to would have found it there.

`needs.scrape-plan.outputs.run` is a **start-time snapshot**. The comment describes a live check
that does not exist. ADR-0091 moved compaction to its own concurrency group to stop it being
starved; that fixed starvation and removed mutual exclusion, leaving two polite pollers.

**Measured user-visible damage**, from diffing the affected runs' id batches:

- All **215** of run 1's evictions were resurrected; **43 were never re-evicted** through run 5 —
  closed postings still being served.
- **327** of run 1's 922 adds were not re-added by run 2, and **178 were never re-added** in the
  window — live jobs missing from search until their Board is next scraped.

**Nothing in the pipeline can detect this.** `index sync` logs `index: N rows in table 'jobs'` but
never compares `N` against the prior run's closing count, and `prune` reported
`evict 0 (0 off-Board + 0 duplicate)` on all five runs, so no counter moved.

**Fix shape.** A compare-and-swap, not a better poll: record the HF commit sha of the
`data/lancedb` slice at fetch time, and refuse the upload if it changed. Log the expected base
beside the observed one, so a rollback becomes a red run instead of a silent one.

---

## 2. The 50-annotation run budget is already breached

GitHub caps workflow annotations at 10/step, 50/job and **50/run**. Cleaned counts (echoed
workflow source excluded):

| Run | warning | notice | total / 50 |
|---|---|---|---|
| 34450830376 | 47 | 1 | 48 |
| 34455705954 | 49 | 2 | **51 — over** |
| 34461364139 | 49 | 1 | **50 — at cap** |
| 34466352099 | 48 | 1 | 49 |
| 34470668397 | 47 | 1 | 48 |

Per-job and per-step maxima are 5, so the **run** cap is the binding one. In 34455705954 the
dropped annotation was the `chain` job's successor notice.

**The dominant consumer is one call site**, `spare_egress.py:mark_walled`, at 29–30 per run:

| Run | workday | eightfold |
|---|---|---|
| 34450830376 | 15/15 shards | 15/15 |
| 34455705954 | 15/15 | 15/15 |
| 34461364139 | 15/15 | 15/15 |
| 34466352099 | 15/15 | 14/15 |
| 34470668397 | 15/15 | 15/15 |

`tests/test_log_levels.py` **does** cover `spare_egress.py` — it is in
`_PER_ITEM_BY_CONSTRUCTION`. `mark_walled` is a *deliberate, documented* exemption, and its
stated reason is where the gap is:

> the `_walled` set makes it fire once per *group* per process, and a group is an ATS that sets
> `egress_fallback_on` — eightfold, workday and workable are the only three, so **three per shard
> at the ceiling**, not one per Board.

Every clause is true. The bound is reasoned **per shard process**; the budget is enforced **per
run**, across 15 shards. The stated ceiling of three per shard is **45 of the 50 run-wide
annotations** from this one site. Today only two of the three ATSes wall, which is why it lands at
29–30 instead. If `workable` starts walling, the budget overflows hard.

This is not a hole in the test — it is a bound whose unit does not match the budget's unit.

**Fix shape.** Demote `mark_walled` to INFO: the per-shard
`[scrape_run] workday: walled; spare egress rescued …` summary already carries the fact. That
frees ~28 of 50. Longer term, the exemption reasons should be stated in run-wide units, since
that is the unit GitHub enforces.

---

## 3. One board is ~3% of all scrape volume and yields zero tech jobs

teamtailor's raw volume jumped 3.4x mid-window while its tech output did not move:

| Run | teamtailor raw | teamtailor tech | kept% |
|---|---|---|---|
| 34461364139 | 24,178 | 3,261 | 13.5% |
| 34466352099 | **81,382** | 3,231 | **4.0%** |
| 34470668397 | **83,376** | 3,216 | **3.9%** |

All five runs share head `2503be7`, so this **cannot** be a code change. It is one board:

```
slow board teamtailor:waymaneducation-1710232669: 56527 jobs in 597s   (94.7 jobs/s)
slow board teamtailor:waymaneducation-1710232669: 56527 jobs in 572s   (98.8 jobs/s)
```

56,527 rows per run — ~3% of the corpus-wide scrape total (1.91 M) — for a net tech change of
**−30 jobs**.

**Why the ADR-0064 value gate does not catch it.** The gate skips Boards costing over
`_GATE_FLOOR_S = 900.0` (15 min) for under 2 tech jobs/min. This board costs **9.5–9.9 min**, below
the floor, so its tech yield is never consulted. The gate's rationale is explicitly about makespan
— "below it a Board cannot threaten a 60 min makespan" — and it is right about that. But this
board's cost is not makespan; it is **volume**: 56,527 rows pushed through `filter_tech`, the join
artifact, and the description store every run, twice so far.

The gate has a time dimension and no volume dimension. The docstring's own survey found only 12
Boards over 15 min in a 68,715-row ledger, so the floor was calibrated for giants; a fast,
enormous, zero-yield board is a shape it was not built for.

---

## 4. Oracle non-production pods — a finding that did not survive follow-up

> **Corrected 2026-09-11. The original §4 claimed these boards serve jobs that appear on no
> production careers site, and recommended filtering ~32% of Oracle's volume at discovery. Both
> the central evidence and the conclusion were wrong. The section is rewritten here rather than
> deleted, because the *way* it was wrong is the useful part.**

### What is still true

Against `origin/main`'s ledger (2,025 live Oracle boards, 596,829 jobs), tenants whose pod label
carries a `-dev<N>` / `-test` / `-uat` / `-stage` marker: **343 boards (16.9%), 193,640 jobs
(32.4%)**, of which **288 have a live production sibling** in the same ledger. Sampled against a
same-day, same-method production control, they are markedly staler — median posting age **233 days
against 7**, and **41.4% over a year old against 5.7%**.

### The claim that was wrong, and why

The original section reported that `jpmc-dev3`, `jpmc-test`, `jpmc-dev1` and `jpmc-dev9` each
returned **0/50 id overlap and 0/50 title overlap** with production, and concluded: *"They are not
duplicates… they are distinct — and that is worse: those postings appear on no production careers
site."*

**That inference does not hold.** Drawing 50 items from a 7,402-posting board and 50 from a
7,691-posting board yields almost no shared titles *even when the two sets are identical*. The
measurement described the sample size, not the data. Re-run at 200 per board, only **13 of 208**
comparisons are decidable at all — the rest are boards larger than the sample — and among those
13 the result is **11 partial overlaps, 2 identical, zero disjoint**.

Enumerating the flagship case in full (every page of both boards, 38 pages, zero failures, zero
duplicate ids) gives the real number: `jpmc-dev9` shares **1,077 of its 6,693 distinct titles with
production — 16.1%**, not 0%. Its remaining titles are mostly real JPMorgan postings production has
since closed, plus a little test data.

### Why the recommended fix was also wrong

`config.py`'s `EXCLUDED_BOARDS` sets the bar this review failed to apply:

> Every entry was confirmed by **reading that Board's own postings**, never from the shape of its
> slug. That distinction is the whole point: a slug-pattern rule would also have dropped
> `greenhouse:stage`, which is KKR's real board of 128 jobs, and `recruitee:test1234`, which
> belongs to a real Austrian education agency.

Reading all 223 candidates that pass the strictest structural test (marker + live production
sibling + an **identical site configuration**, the Fusion clone signature):

| Verdict from the board's own postings | Boards | Jobs |
|---|---|---|
| Real-looking content — the slug is not confirmed | **156** | 128,498 |
| Serves nothing — already handled by ADR-0053 | 63 | 6,987 |
| Possibly fabricated | 4 | 2,117 |

`eluq-dev19`, `jpmc-test` and `jpmc-dev3` all read 0.0% test-marker titles. And at least two of the
four "fabricated" verdicts are the classifier misfiring the same way a slug rule does: `egmn-dev2`
was flagged on **`Sr. Production Testing Engineer`**, a real job, and `eiqg-test` serves zero rows
at all.

The rule would also have parked **`eczy-test.fa.us2.oraclecloud.com`**, which `config.py` names
explicitly as a deliberate keep with its own handling — silently reversing a prior reasoned
decision. `oracle:eubt`, the genuine load-test instance, was already excluded on content evidence
in #382, which is the standard working as intended.

### What is left

A real but differently-shaped problem: ~84% of `jpmc-dev9`'s postings are closed or stale reqs.
That is **posting age**, not board identity — it exists on production boards too (5.7% over a year
old), just far worse on these (41.4%). A staleness lever judges postings rather than boards, so it
carries none of the misclassification risk this section ran into, and it would apply corpus-wide
rather than to one ATS. Recorded as a candidate, not a recommendation: nothing here measures what
a user would lose to it.

### The transferable lesson

Two failures, one shape. The overlap number described the *method* (a 50-item sample) rather than
the data, and the exclusion rule described the *hostname* rather than the postings. Both produced
a confident, wrong answer that survived until someone asked what the denominator was. The repo had
already written the second lesson down in `config.py`; this review did not read it first.

## 5. What is **not** wrong

Worth recording, because three of these were the previous reviews' headline problems.

- **Flapping is essentially gone.** Across all 10 ordered run-pairs, only **10 distinct ids** were
  evicted and later re-added — 0.57% of 1,746 evictions, 8 of them on one board. No Board was
  evicted en masse and re-added. ADR-0083's grace period is doing real work: the identity
  `carried_in − reappeared − still_unconfirmed = evicted` holds exactly in all five runs, and it
  withheld 359 live rows over the window.
- **The cost model is healthy.** `scrape_run` actual/predicted medians: 0.94, 0.87, 1.05, 0.95,
  0.96 (pooled 0.73–1.31), with 19,965–19,988 of 20,000 boards measured. Do not chase the packer.
- **Board errors are a flat floor**, not a trend: 20 / 26 / 28 / 36 / 22 against 20,000 boards
  attempted every run — **0.132%** pooled. workday HTTPErrors are 8–9 every run; successfactors
  owns 100% of the TLS failures.
- **No budget kills and no deferrals** in any of the five runs.
- **Jobs without vectors are stable**: 20,847 → 20,908 (+0.3%). The non-English gate tracks it.
  No resume leak.
- **The priority ledger is not accumulating**: `carried` flat at 23,849–23,920 (±35).
- **The stood-down run 34455486313 is correct behaviour**, not an anomaly — it saw
  `cleanup-index is active (1)` and yielded, and the chain started its successor two minutes later.

---

## 6. Standing conditions — design costs, visible every run

These are constant across all five runs. None is a regression; each is an unbounded cost worth
knowing.

- **Spare egress is not a reserve.** workday spends it on 15/15 shards every run, eightfold on
  14–15/15. ADR-0063's second route is a permanent operating mode for two ATSes, with no headroom
  left for a third.
- **ADR-0053 scope exclusion has no drain.** 1,979 / 1,975 / 2,010 / 2,102 / 2,039 eviction-candidate
  rows held out of scope across 54–62 Boards. `successfactors:careers.hcltech.com` alone is
  **76–81%** of it and rises monotonically (1,592 → 1,601); with `oracle:ejwl.fa.us2` it is 90–95%.
  **23 Boards are excluded in all five runs** — a lower bound, since `k/N` for `k < 5` cannot be
  read as a drain rate (a Board absent from a run's exclusions is unresolvable between "drained"
  and "not in this run's slice").
- **Quarantine is a one-way door.** 673 of 690 failure-ledger rows are quarantined and **0 were
  ever cleared** in five runs. `board_failures.py:151` clears a streak on any output, but
  `scrape_plan.py:307` filters quarantined Boards out of the slice unconditionally — so the only
  reset requires a scrape that quarantine prevents. Re-entry needs a human running the offline
  liveness probe. Entry is conservative (5 consecutive 404/410s ≈ weeks); exit does not exist.
- **The vector store is append-only and 43.8% orphaned.** 734,391 vectors back 412,692 served rows;
  the gap grows 250–500 per run and never drains. It functions as a re-embed cache, so this is a
  deliberate cost — but nothing bounds it. (~988 MB of ~2.26 GB, computed, not logged.)
- **HF storage churn.** `usedStorage` 35.50 → 41.10 (squash) → 35.63 → 35.64 → 38.36 GB against a
  40 GB `SQUASH_ABOVE_GB` trigger, with `live` steady at 5.84–6.27 GB. Working as designed, with
  roughly four runs of headroom between squashes.

---

## 7. Critical path

`scrape` owns the critical path in 5/5 runs at 45–50%; `join` is the runner-up at 12–16 min.
Inter-job queueing is ~12 s — infrastructure is not the tax. In-job setup (pip, artifact transfer,
`state_fetch`) is 19–27% of wall.

**Two boards are the scrape floor, identically, every run** — a shard's wall cannot go below its
slowest single Board:

| Board | seconds per run | floor share |
|---|---|---|
| `successfactors:careers.hcltech.com` | 1,552 / 1,651 / 1,607 / 1,158 / 1,318 | 96–97% |
| `oracle:ejwl.fa.us2.oraclecloud.com` | 1,387 / 1,170 / 1,427 / 1,352 / 1,097 | 95–97% |
| `zwayam:careers.eaplworld.com` | 844–930 | 92–96% |

`scrape_plan` already warns pre-run every time ("one board at 22.9–26.5 min against a 14.8–15.8 min
even share"), so the run confirming it is not news. Sub-sharding the top two by page/offset range
would save a median **7.5 min ≈ 13% of wall**; after that the next-binding shards are work-bound at
50–70% floor share and `join`'s 12–16 min becomes the target. A per-board timeout is the wrong
fix — it would discard ~20,170 real jobs.

`zwayam:careers.eaplworld.com` is a different shape: only 1,774 jobs at **2.0 jobs/s**. Slow, not
big — a throughput bug rather than a size problem.

**Embed is not worth optimising.** All 34 embed shards across all five runs ran on `cpu`; spread
(1.19–1.55x) is runner CPU variance, not device or plan imbalance. Embed is 7–9% of wall.

**`join` + `merge` are the real ceiling**: both single-writer and unsharded, together 40–46% of the
critical path. With infinitely many scrape shards the wall still cannot fall below ~21–28 min.

---

## 8. Ranked scope of improvement

1. **Make the LanceDB write a compare-and-swap** (§1). Silent data loss on a green run is the worst
   failure mode present. Log the expected base row count and HF commit sha beside the observed.
2. **Demote `spare_egress.mark_walled` to INFO** (§2). One line; un-breaches the run budget and
   returns ~28 annotation slots.
3. **Give the value gate a volume dimension** (§3). One board is 3% of scrape volume at zero yield
   and sits below the time floor by design.
4. ~~**Filter Oracle non-production pods at discovery** (§4).~~ **Withdrawn 2026-09-11** — reading
   the candidates' own postings found 156 of 223 serving real content, and the rule would have
   reversed `config.py`'s documented keep on `eczy-test`. See the rewritten §4; what is left is
   a posting-age question, recorded there as a candidate rather than a recommendation.
5. **Guard `workday.py:602`.** Both tracebacks in the window are the same unguarded
   `response.json()` on a non-JSON 200 (`gilead.wd1`, `msd.wd5`). Caught and non-fatal, but it
   costs a 35-line stack and the board.
6. **Give `pipeline.yml`'s no-token stand-down branch the `standing this run down` wording.** It is
   a bare `echo`, so `run_logs.stood_down()` returns False and every analyser would report zeros as
   if the pipeline had run — the exact artefact-as-finding error the mechanism exists to prevent.
7. **Add consumers for `[index] corpus:` and `[index] index: N rows`.** The served row count, store
   size and the 20,847-job embed backlog are stated every run and parsed by nothing — which is why
   §1 went unnoticed.

---

## Method and limitations

Logs were read through `scripts/runlog/`'s analysers over the cached per-job logs in
`experiment/runlog/artifacts/`, with `run_logs.clean()` stripping GitHub's echoed workflow source.
That filter matters: a naive `grep '::error::'` over the raw archives reports ~40 errors per run
that are entirely workflow YAML text.

- **The zip archive of the newest run is incomplete** (556 KB / 34 files against ~2.8 MB / 52 for
  its peers). That is GitHub's archive-assembly lag, not a pipeline finding; the per-job API fetch
  has everything.
- **End-to-end serving was not verified.** `imposeidon-headstart-search.hf.space/search` returns
  401 behind the sign-in wall, so the index presence noted in §4 is established from the merge logs' per-Board
  row counts, not from a live query.
- **The non-production pod classifier was a hostname heuristic, and that was the defect.** Eyeballing
  25 labels for false positives tested whether they *looked* like dev pods — not whether the boards
  serve real jobs, which is the question that decided it and the one `config.py` already required
  be asked. See the rewritten §4.
- **The staleness sample is the first page of 100 per host**, which is not a random draw from a
  large board. The production control was sampled identically on the same day, so the comparison
  holds even where the absolute figures are biased.
- **The working checkout used for this review was 24 commits behind `origin/main`** (its
  `oracle.csv` was 3,458 rows short, `trakstar.csv` 2,830). All ledger figures here were recomputed
  against `origin/main`.
