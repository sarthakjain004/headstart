# Eight-run pipeline log review — 2026-08-25

Runs analysed (the eight most recent finished `pipeline.yml` runs as of 2026-08-25 05:00 UTC):

| run | created (UTC) | result | wall (job span) | wall (incl. pending) | pending |
| --- | --- | --- | --- | --- | --- |
| 32807917733 | 08-25 04:10 | success | 56.5 | 79 | 22.5 |
| 32805179451 | 08-25 03:26 | success | 66.4 | 67 | 0.6 |
| 32799926182 | 08-25 02:03 | success | 58.3 | 58 | ~0 |
| 32790937521 | 08-24 23:47 | success | 57.8 | 77 | 19.2 |
| 32786872871 | 08-24 22:54 | success | 59.7 | 73 | 13.3 |
| 32781949927 | 08-24 21:54 | success | 68.8 | 73 | 4.2 |
| 32777057873 | 08-24 21:00 | **cancelled** | — | 54 | 54 (no job ever started) |
| 32770979693 | 08-24 19:55 | success | 64.0 | 122 | 58.0 |

All eight sit on the **same SHA, `99369d7`** — there is no code confound anywhere in this window, which
makes every cross-run comparison below a like-for-like one. `99369d7` post-dates `ee97ebc` (ADR-0083
grace period) and `960d991` (PR #280's new log lines), verified with `git merge-base --is-ancestor`, so
every diagnostic line this review leans on genuinely exists in all eight logs.

Mean wall across the seven successful runs is 61.6 min (job span). `scrape` owns the critical path in
7 of 7, at 23.4–27.0 min, or 35–43% of wall.

## 1. One board sets the scrape makespan in 7 of 7 runs

`smartrecruiters:AdeebaEServicesPvtLtd` is the slowest single board of the slowest shard in every run
in the window, at 88–93% of that shard's wall. Evidence is in each `scrape` matrix job's log
(`slow board` lines), cross-read against `scrape_plan`'s prediction in the `scrape-plan` job.

| run | scrape max | Adeeba floor | floor% | next-slowest shard | saving if removed |
| --- | --- | --- | --- | --- | --- |
| 32807917733 | 1442 s | 1293 s | 90% | 1163 s | 4.7 min |
| 32805179451 | 1412 s | 1284 s | 91% | 1181 s | 3.9 min |
| 32799926182 | 1463 s | 1327 s | 91% | 1133 s | 5.5 min |
| 32790937521 | 1485 s | 1305 s | 90% | 1442 s (Adeeba was #2) | 0 |
| 32786872871 | 1403 s | 1304 s | 93% | 1213 s | 3.2 min |
| 32781949927 | 1607 s | 1456 s | 91% | 1278 s | 5.5 min |
| 32770979693 | 1620 s | 1472 s | 91% | 1202 s | 7.0 min |

Total 29.6 min over seven runs, i.e. **~4.2 min per run, ~6.8% of mean wall**. That figure is a
projection from re-ranking the observed shard walls, not a measurement of a run without Adeeba; and
32790937521 shows it can be zero when another shard happens to run longer anyway.

The packer is not at fault and cannot help. `scrape_plan` reports a predicted spread of 1.01x mean in
all seven runs — near-perfectly balanced — and its own `predicted makespan` equals its own reported
`single-board floor` to the decimal in all seven (21.8/21.8, 22.3/22.3, 22.4/22.4, 23.1/23.1,
24.4/24.4, 24.6/24.6, 24.6/24.6). The planner already knows the floor is the answer; it says so before
a shard runs.

**Why the ADR-0064 value gate does not catch it.** Adeeba yields 136 tech jobs
(`data/state/board_priority.csv`, pulled fresh from HF: `smartrecruiters:AdeebaEServicesPvtLtd,136.0000,136,2026-08-25`)
from 23,806 scraped postings — 0.57% tech. But the gate's rule is a *rate*: skip a board over 15 min
that returns under 2 tech jobs/min. Adeeba returns 136 jobs in 21.5 min = **6.3 tech jobs/min**, which
clears the threshold by 3.2x. The gate has no notion of critical-path cost, so the one board that
sets the whole stage's makespan passes it comfortably while boards like
`eightfold:starbucks.eightfold.ai` (0.16/min) are correctly skipped. The same 14 boards are skipped
every run, unchanged across the window.

Worth noting the gate is working exactly as designed — this is a gap in what the design measures, not
a defect in its implementation.

## 2. The merge job's HF download took a real ~2.5x step change — cause is external to this repo

`merge` is the second-largest stage (8.7–22.3 min). Its variance is entirely one step — "Download the
prior store + LanceDB" — at 159 s / 389 s / 993 s across three sampled runs, while every compute step
inside the same job is rock-steady: `embed_merge` 13–14 s, `update_meta` 31–34 s, `index sync`
57–58 s, `prune` 5 s, `role_trends` 14 s, upload 59–63 s.

Measured over 58 runs' step durations from the Actions API, bucketed by daily `cleanup-index`
compaction boundary:

| cycle | n | mean | median | sd | min–max |
| --- | --- | --- | --- | --- | --- |
| A (08-22→08-23) | 18 | 187 s | 168 s | 79 s | 85–379 |
| B (08-23→08-24) | 22 | 175 s | 168 s | 89 s | 66–456 |
| C (08-24→08-25) | 18 | **437 s** | 419 s | 179 s | 159–993 |

Welch t for C vs B is 5.49 — far outside within-cycle noise. Five hypotheses were tested and four
were killed by measurement.

**Not fragment accumulation.** `index compact` moved to `cleanup-index` (daily 06:00 UTC), so LanceDB
fragments do pile up — file count resets to 11 after each compaction and grows ~160/run, confirmed.
But within cycle C the Pearson correlation between file count and download seconds is +0.20 (n=12).
The decisive pair is the two 11-file runs: 105 s in cycle B against 412 s in cycle C — identical file
count, 3.9x the time.

**Not code.** Cycle C's first five runs (07:37–11:55) ran on `6c098e62`, the same SHA that dominated
cycle B (17 of 22 runs), and were already slow at 412/748/276/362/427 s. Same code, both sides of the
boundary, 2.5x apart.

**Not bandwidth, runner, or region.** The merge job's *upload* step pushes ~1.86 GB over the same
runner on the same network in the same job, and it did not move at all: 51 s / 50 s / 52 s across
cycles A/B/C. A network or runner-placement explanation would have to slow both directions.

**Not a per-run runner-quality lottery.** Within cycle C, merge's download time and the join job's
download time are uncorrelated (Pearson r = −0.17, n=18) — the two jobs in the same run are slow
independently of each other.

**Not per-request latency.** `scrape-plan`'s light fetch (`data/state/*`, 18 files, 62 MB) is
essentially unchanged: 2.6 s → 3.0 s → 3.0 s median across A/B/C.

What survives is a size-dependent degradation of the HF *download* path only. Both large fetches
moved together, and the small one did not:

| job | fetches | bytes | cycle B mean | cycle C mean | factor |
| --- | --- | --- | --- | --- | --- |
| scrape-plan | `data/state/*` | 62 MB | 3.0 s | 3.0 s (median) | 1.0x |
| join | meta.jsonl + state + descriptions | ~790 MB | 23 s | 81 s | 3.5x |
| merge | embeddings + lancedb | 3,765 MB | 175 s | 437 s | 2.5x |
| merge (upload) | embeddings + lancedb + state + descriptions | ~1,860 MB | 50 s | 52 s | 1.0x |

Effective download throughput fell from ~21.5 MB/s to ~8.6 MB/s while upload held at ~40 MB/s. The
cause is HF-side and not identifiable from these logs; nothing in this repo changed across the
boundary. It has **not** self-corrected — the newest finished run (32811479033, 08-25 05:05) still
took 297 s against cycle B's 175 s mean.

**What merge actually fetches**, which is where any mitigation has to come from:

| file | bytes | who needs it |
| --- | --- | --- |
| `data/lancedb/*` | 1,715 MB (3,067 files) | `index sync`, `prune`, `role_trends` |
| `data/embeddings/jobs/embeddings.f32` | 1,702 MB | `embed_merge` appends; `index sync` reads ~500 rows |
| `data/embeddings/jobs/meta.jsonl` | 348 MB | `embed_merge`, `update_meta` (rewrites every row) |

`index.py:181` loads the whole 1.70 GB vector blob with `np.fromfile` to use `vectors[index]` for the
few hundred rows being added that run. That is the largest structural inefficiency on this path.

## 3. The embedding store is an unbounded cache — 43% of it serves nothing

`embed_merge` reports the store at 553,971 vectors (dim 768) while `index sync` reports the served
table at 317,632 rows. Every served row needs a vector, so at least **236,339 vectors — 42.7% of the
store — belong to Jobs the index does not serve**. At 768 float32 that is ~726 MB of the ~1.70 GB
`embeddings.f32` that is re-uploaded every run.

This is a cache with no eviction policy rather than a leak, and the caching is deliberate:
`embed_merge`'s own comment notes `plan_sync` cannot re-add a Job with no vector, so retaining the
vector of an evicted Job makes a later re-add free. The runs bear that out — `plan: add N (L new +
R re-embedded)` shows re-adds happening without re-embedding.

The concern is that nothing bounds it. `embed_merge --evict-ids` only ever drops the *upgrade* list
(a replace, ADR-0050); no path removes the vector of a Job evicted from the index. Since storage is
this workflow's documented binding constraint, an unbounded cache holding 43% of the largest artifact
deserves an explicit retention rule rather than an implicit "forever".

Storage itself is currently healthy and should not be read as an emergency: the in-merge squash
(ADR-0071) fires every run, `live` sits at 3.98→4.18 GB and grows only ~33 MB/run, and `usedStorage`
oscillates 41.5–43.8 GB against the 100 GB quota rather than climbing.

## 4. `role_trends`' top-5 log line prints non-unique labels

Every run's line reads like:

```
top: software-engineering/senior 7044, software-engineering/senior 6637,
     software-engineering/mid 5016, web-development/senior 4052, software-engineering/mid 3838
```

The same label appears twice with different counts, which reads as a double-count. It is not one —
`role_trends.py:311-317` keys counts on the 4-tuple `("stock", family, band, ats)` and then formats
only `f"{family}/{band} {c}"`, discarding `ats`. So those are two different ATSes' rows for the same
family and band, rendered identically.

Harmless to the ledger (the CSV carries `ats` correctly, ADR-0075) but actively misleading in the log,
and it lands in exactly the place this repo has already been burned on trends double-counting.

**Fixed** — the label now carries `ats`, e.g. `software-engineering/senior/workday 7044`. ATS goes
last, joined with the `/` already on the line, rather than `ats:`-first: ADR-0023 spells `{ats}:{slug}`
for *Board* identity, and a trends group borrowing that shape would read like a board key it is not.
Regression test:
`tests/test_role_trends.py::test_top_line_distinguishes_two_atses_sharing_a_family_and_band`,
verified to fail against the pre-fix formatter.

## 5. Grace period, withholding, and churn

The ADR-0083 grace period is doing real work but returning less than it withholds. Per run,
`unconfirmed` / `carried in` / `reappeared` / `unconfirmed again`:

| run | unconfirmed | carried in | reappeared | reappear rate |
| --- | --- | --- | --- | --- |
| 32807917733 | 1207 | 900 | 86 | 9.6% |
| 32805179451 | 900 | 1202 | 137 | 11.4% |
| 32799926182 | 1202 | 1324 | 592 | 44.7% |
| 32790937521 | 1324 | 1372 | 718 | 52.3% |
| 32786872871 | 1372 | 770 | 93 | 12.1% |
| 32781949927 | 770 | 1633 | 87 | 5.3% |
| 32770979693 | 1633 | 1827 | 175 | 9.6% |

Read this against the unit ADR-0083 actually uses: the unit is *scrapes of that Board*, not runs, and
only 20,000 of 66,401 live Boards are in any run's slice, so a carried-in id whose Board was not
re-read simply keeps its state. A ~10% reappear rate against a ~30% re-read rate is therefore not
obviously unhealthy — but the two 45–52% runs against five 5–12% runs is a spread worth watching, and
the stock is not trending down.

ADR-0053 scope exclusion is holding 671–1,097 eviction-candidate rows across 61–106 Boards, with no
drain by design. `successfactors:careers.hcltech.com` appears in the top-5 of all seven runs at a
near-constant 126–136 rows — the signature of a Board short on every run, which never re-enters scope.
That is the accretion pattern PR #280's row-count line was added to make visible; it is visible, and
it is not shrinking.

`index prune` evicted **0 rows in all seven runs** (0 off-Board + 0 duplicate) with a keep-set constant
at 66,401 live Boards. Both are expected rather than suspicious: the liveness ledger is committed to
git and the SHA did not move across the window.

## 6. Things that are healthy

Egress is clean: `fanout_retries.py` reports 0/15 shards degraded by retry ratio and 0/15 logging
`degrading to direct`, in all seven runs. Board errors are 35–41 per 20,000 boards attempted (0.2%),
stable, dominated by `personio` (8–14/run) and `workday` HTTPErrors. No shard hit its time budget in
any run. `embed` ran 155–463 Docs on 1 shard (2 in the oldest run), all on CPU, 0 failures, with
actual/predicted 0.46–0.99 — consistently faster than planned. `update_meta` reported `no sweep` in
all seven (derivations v7 stored, v7 in code), so none of the merge timings above are confounded by a
derivations sweep. Quarantine stock is 391→395 across the window, an inflow of ~4 boards over seven
runs, with `0 cleared by a successful scrape` every run.

The served table is flat: 317,933 → 317,632 rows across the window, a net −301 over seven runs, on
net-row deltas of +39, −78, +48, −2, +473, −781, −428.

## 7. A dropped cycle, and where the rest of the wall clock goes

Run 32777057873 was cancelled after 54 minutes with **zero jobs ever started** — it sat pending in the
ADR-0071 concurrency group and was superseded. That is the documented back-to-back design working, but
it is a silently dropped scheduled cycle. Rate is 1 in 40 recent runs, so it is not systemic.

Pending time is the largest single gap between the two wall-clock figures and no code change touches
it: 0–58 min per run, again the concurrency group serialising a cron that fires faster than a run
completes. `run_stats.py`'s "queueing/setup" figure is measuring this, not runner setup — within the
job span, queue/setup is only 0.1–0.2 min.

## What is worth acting on

Ranked by measured cost against effort:

0. **Done in this pass (log-only, no behaviour change).** `state_fetch` now reports bytes and MB/s
   beside the seconds, so the §2 diagnosis becomes a grep rather than a 58-run Actions-API sweep.
   Applying it to run 32807917733's merge fetch *would* render
   `fetched 2702 file(s), 3765 MB in 389s (9.7 MB/s): …` — a projection, not an observed line: this
   code has not yet run in CI. Its inputs are that run's own `fetched 2702 file(s) … 389s` log line
   and today's 3,765 MB total; the file count is that run's, not today's 3,067-file LanceDB above,
   because compaction moves it every day. Note the comparison it enables is across *runs and jobs*,
   not within one line — `pipeline.yml` calls `state_fetch` twice in the join job alone, so each
   invocation reports only its own patterns. `role_trends`' top-5 label now carries
   `ats` (§4). Both have regression tests proven to fail against the pre-fix code; full suite 1540
   passed / 1 skipped / 1 xfailed, ruff clean.

1. **Split or cap `smartrecruiters:AdeebaEServicesPvtLtd`** — ~4.2 min/run of critical path for 136 of
   299,073 tech jobs. Either shard it across shards, give it a per-board timeout, or extend ADR-0064's
   gate with a makespan term alongside its rate term.
2. **Give the embedding store a retention rule** — 726 MB re-uploaded every run for Jobs nothing serves,
   against a documented storage-bound budget.
3. **Re-measure the merge download** — the step change is confirmed external and has **not**
   self-corrected (newest finished run 297 s against cycle B's 175 s mean). Deferred, with the
   options weighed and rejected-for-now on 2026-08-25: enable `hf_transfer` (one env var, 2–3x
   expected, but it removes huggingface_hub's internal retry and opens more parallel connections
   against a repo that lost 21 of 25 runs to 429s on 2026-08-02 — wants a canary, not a flip);
   prune the store per §3 (−23% of this download, justified independently); or stop `np.fromfile`-ing
   the whole 1.70 GB vector blob for ~500 rows (`index.py:181`), which is the real structural fix and
   needs a format change, a migration, and an ADR.
