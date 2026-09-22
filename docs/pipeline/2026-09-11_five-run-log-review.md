# Five-run pipeline log review — 2026-09-11

Runs reviewed (the five most recent `nightly-pipeline` runs as of 2026-09-11 ~17:00 UTC; a sixth,
`34625075001`, was still `in_progress` at review time and is excluded per the task's own rule),
chronological:

| Run | Started (UTC) | Event | Conclusion | Wall | Head | add / evict | net | Table after | Board errors |
|---|---|---|---|---|---|---|---|---|---|
| 34605571005 | 13:39:35 | dispatch | success | 49.9m | `55b3292` | 465 / 301 | +164 | 414,561 | 42 |
| 34610441904 | 14:29:36 | dispatch | **failure** | 46.9m | `349ec71` | 432 / 308 | +124 | 414,673 | 23 (14 of 15 shards) |
| 34610866559 | 14:33:50 | schedule | **cancelled** | — 0 jobs ever started — | `349ec71` | — | — | — | — |
| 34615147683 | 15:16:30 | dispatch | success | 51.3m | `349ec71` | 510 / 437 | +73 | 414,746 | 25 |
| 34620199838 | 16:07:52 | dispatch | success | 50.9m | `349ec71` | 553 / 407 | +146 | 414,892 | 21 |

The row chain (414,561 → 414,673 → 414,746 → 414,892) is exactly continuous across the failed run
and both cancellations-worth of gap — every `net` reconciles to the next run's closing count with
no discrepancy anywhere in the window.

## 0. A correction to this review's own starting assumptions

The task brief for this review, and the current session's own memory, stated that `main` sits at
`fd155ef5` and that none of the prior review's fixes had merged. **That is stale.** `git fetch
origin main` puts `origin/main` at `42dfb964`, **51 commits ahead** of `fd155ef5`, and all five
runs in this window ran on commits inside that range (`55b3292` and `349ec71`, both confirmed
ancestors of `origin/main` via `git merge-base --is-ancestor`). The local `main` ref used to derive
"nothing has merged" was simply never fetched this session.

This matters for the whole shape of this review, because **all four of the 2026-09-10 review's
ranked findings were already addressed by separate, already-merged PRs before this window started**:

| 09-10 finding | Fix commit | Merged relative to this window |
|---|---|---|
| #1 LanceDB lost-update (compare-and-swap missing) | `80a1874f` "Make the LanceDB write check its own base, not a stale poll" (#412) | before all 5 runs |
| #2 50-annotation budget breach (`mark_walled`) | `d836e07e` "Stop spending 30 of 50 annotations on the spare-egress wall" (#425) | before all 5 runs |
| #3 teamtailor:waymaneducation ballooning | `349ec718` "Park Wayman Learning Trust: 56,527 postings a run, zero tech" (#427, ADR-0136) | **inside this window**, between run 1 and run 2 |
| #4 Oracle non-production pods | `f4b6d462` "Correct the Oracle non-production finding, which did not hold" (#422) | before all 5 runs — **the finding itself was withdrawn**, not fixed |

None of these fixes belongs to the unmerged `fix/observability-silent-drift` branch this session
started on (`dfea5a7c`..`129ca666` — still 10 commits ahead of `origin/main`, confirmed not an
ancestor of it). They landed through a separate, already-merged line of PRs (#412/#422/#425/#427,
among ~40 others including the 09-09 review's embed-fanout and role_trends-Parquet fixes). So this
review is not asking "did #1-4 recur on unfixed code" — it is asking "do the fixes hold, one day
later, against fresh runs," and, for #3, this window happens to catch the fix landing live.

## 1. The failed run: an isolated GitHub artifact-service timeout, fully absorbed

`34610441904` is marked `failure` for one reason: `scrape (4)`'s **`actions/upload-artifact`
step**, not its `scrape_run` work. The shard's own scrape finished cleanly —

```
14:43:31 [scrape_run] done: 103079 jobs from 1337 boards in 774s (3 board errors)
         | board seconds {'p50': 0.4, 'p90': 19.0, 'p99': 151.6, 'max': 632.7}
         | predicted 13.6 min, actual/predicted 0.95x
```

— a normal, on-plan shard (a/p 0.95x, in line with the other 14 shards' 0.79-1.45x spread that run).
Two minutes later, uploading the fragment:

```
14:45:44 ##[error]Failed to CreateArtifact: Unable to make request: ETIMEDOUT
```

This is a GitHub-runner-side connectivity failure to the Actions artifact service, not a pipeline
bug: the other 14 shards' uploads succeeded in the same few minutes (confirmed by grepping their
own "Finished uploading artifact content to blob storage!" lines in the same run), so this was
isolated to one runner's connection, not a platform-wide outage. This is the **first** occurrence
of this exact failure in the reviewed window, and only the **second** `failure`-concluded
`nightly-pipeline` run in the last 100 (2% base rate; the other 98 are 79 success / 18 cancelled /
1 in-progress) — genuinely rare, not a recurring pattern.

**What happened to the 103,079 jobs / 1,337 boards.** `join`'s `if: always()` + per-step
`continue-on-error: true` (`pipeline.yml` lines 481, 518-555) let the job proceed with whatever
fragments exist. `scrape_join` globbed 14 of 15 fragment dirs and logged it plainly:

```
14:52:47 [scrape_join] 14 shard(s), 21 ATS file(s)
```

`scrape_join.py` has **no expected-shard-count check** (unlike `embed_merge`'s `--expect-shards`,
per the skill's own step-7 note) — it silently accepts whatever fragments arrived, with no warning
that a 15th was expected. Downstream, `run-level: 23 board errors across 14 shards (0.1% of 18663
attempted)` — the "18663 attempted" excludes shard 4's ~1,337 boards entirely, confirming its full
board set (successes *and* its own 3 board errors) never reached the corpus this run.

**Why this is not data loss.** Per ADR-0014/ADR-0053, eviction scope is the set of Boards *present*
in `data/jobs/` this run. A board that shard 4 would have scraped is simply absent from that set —
the same state as a board that was never in the run's 20,000-board slice at all. It gets neither a
false eviction (`index sync` never touches its existing rows) nor an ADR-0053 scope-exclusion flag
(that mechanism is for a board that *was* scraped and came back short, not one wholly missing). The
downstream numbers confirm this cleanly: `index sync`'s `plan: add 432 ... evict 308 -> net +124`
and the ADR-0083 grace-period line (`858 id(s) unconfirmed; of 785 carried in ... 83 reappeared`)
both look like an ordinary run, with no spike attributable to the missing shard. The cost is a
**~1-cycle delay**: an estimated ~21,000 tech jobs (103,079 raw × the run's ~20.5% kept rate — a
projection, not a per-board count) that would have refreshed this run instead wait for their boards'
next slice draw, which the priority ledger makes likely soon since their "last successfully joined"
state never advanced. This is a materially different, and better-contained, failure mode than the
09-10 review's finding #1 (a silent overwrite of *already-published* good data) — nothing here was
written and then reverted; a slice of new data was simply delayed.

**The chain survived it.** `chain`'s own breaker (`pipeline.yml` ~line 976) only stops the cadence
on **three consecutive** failure/success-excluding-merge runs; this window has exactly one. `chain`
ran (`!cancelled()` is true for a `failure` conclusion) and dispatched `34615147683` normally — the
next run in this table started 3 seconds into `chain`'s own execution window. The pipeline
self-healed with no operator action, exactly as ADR-0093 intends.

## 2. The cancelled run: a routine concurrency-group supersession, not an incident

`34610866559` (`event=schedule`, created 14:33:50) shows `{"total_count":0,"jobs":[]}` — no job
of any kind ever started. `pipeline.yml`'s concurrency block (`group: nightly-pipeline,
cancel-in-progress: false`) allows one in-progress and one pending run per group; the scheduled
cron fired 4 minutes into `34610441904` (workflow_dispatch, started 14:29:36, still running) and
was cancelled outright as the superseded pending arrival, never starting a job. This is the
identical shape the 2026-09-09 review documented for `34313429608` and is a routine, expected
interaction between the cron backstop and the chain's own dispatches — **not caused by the
concurrent failure in §1** (the two are independent: this would have happened even had
`34610441904` succeeded). Cancellations of this shape are common: 18 of the last 100
`nightly-pipeline` runs are `cancelled`, essentially all of them this concurrency pattern.

## 3. Confirming the 09-10 findings' fixes, one day out

**#1 LanceDB lost-update — no recurrence, but this window did not test the race.** The row chain
above is exactly continuous. However, `cleanup-index` ran only **once** on 2026-09-11
(07:48:13-08:32:43 UTC), well before this window (13:39-16:24). The race in the 09-10 finding
requires a *concurrent* compaction; none ran alongside any of these four runs, so this window is
not an empirical stress-test of `#412`'s compare-and-swap — it simply confirms no regression while
the fix sits deployed. The fix itself (`80a1874f`) is present in all four runs' SHAs.

**#2 annotation budget — fixed, and now far under the cap.** Cleaned annotation counts this window:

| Run | warning | notice | total / 50 |
|---|---|---|---|
| 34605571005 | 19 | 1 | 20 |
| 34610441904 | 20 | 1 | 21 |
| 34615147683 | 18 | 1 | 19 |
| 34620199838 | 20 | 1 | 21 |

Down from 48-51 in the 09-10 window. Grepping every `##[warning]` line in each run found **zero**
`spare_egress` wall annotations — `#425` demoted `mark_walled` to INFO exactly as the prior review
recommended, and the per-shard `[scrape_run] workday: walled; spare egress rescued ...` INFO summary
(confirmed present, e.g. `34610441904`'s shard 4: `workday: walled; spare egress rescued
34,673/34,679 walled request(s) (100%)`) still carries the same fact. The remaining ~20 annotations
are the ordinary per-shard board-error and value-gate warnings the 09-09/09-10 reviews already
characterized as legitimate.

**#3 teamtailor:waymaneducation — caught landing live, inside this window.** Run 1
(`55b3292`, before `#427`) still shows the board:

```
14:02:51 [scrape_run] slow board teamtailor:waymaneducation-1710232669: 56527 jobs in 665s
```

— unchanged from the 09-10 review's own 56,527-job measurement. Runs 2-4 (`349ec71`, after `#427`
merged) contain **zero** mentions of this board anywhere in their scrape or join logs, and
`teamtailor`'s total scraped volume drops from **78,501** (run 1) to **25,827 / 26,755 / 23,126**
(runs 2-4) — back to the range the 09-10 review measured *before* the board's blowup. `config.py`
confirms the mechanism: `"teamtailor:waymaneducation-1710232669"` is now a literal entry in the
excluded-boards list (`src/headstart/config.py:289`), so `scrape_plan` never selects it again,
rather than the value gate being retuned — ADR-0136 explicitly kept the gate as a single (time)
dimension after measuring that neither a tech-yield nor a raw-volume threshold has a real gap to
sit in. This is a clean before/after natural experiment inside one review window, not an inference
across runs.

**#4 Oracle non-production pods — the original finding was withdrawn, not fixed; don't restate
it.** `#422`'s own commit message: *"The review's 0/50 overlap was a sampling artifact... Full
enumeration gives 16.1% shared. Reading all 223 candidates found 156 serving real content, and the
rule would have reversed `config.py`'s documented keep on `eczy-test`."* The corrected 09-10 review
(read from `origin/main`, since the locally-cached copy predates the correction) keeps only the
posting-*age* observation as a live, unresolved candidate — not board identity, and not a
recommendation to filter at discovery. This window's Oracle floor-table entries
(`oracle:jpmc-dev5`, `oracle:ejwl-dev7`, `oracle:jpmc-dev8`, `oracle:hcbt.fa.em2` all appear as
slow boards in run 2) and scope-exclusion entries (`oracle:ejwl.fa.us2.oraclecloud.com`,
`oracle:jpmc-test.fa.oraclecloud.com`, `oracle:eluq.fa.us2.oraclecloud.com`, all excluded 4/4 runs)
are real Oracle boards serving real, if often stale, content — not evidence of the withdrawn
"phantom duplicate" framing. `oracle:jpmc-test.fa.oraclecloud.com` remains the largest
description-store backlog carrier at a flat 2,691 unsettled descriptions in every run of this
window, unchanged from the 09-10 review — a real, boring, unrelated-to-#4 backlog fact.

## 4. Critical path and the recurring floor board

Wall clock: 49.9 / 46.9 / 51.3 / 50.9 min (run 2's is lower despite the failure, since the failed
step was a `continue-on-error` upload, not on the critical path). `scrape` owns 39-45% of wall in
all four, consistent with both prior reviews.

`successfactors:careers.hcltech.com` is the scrape floor again, 94-96% of its shard's wall in three
of the four runs (`fanout_timing.py`'s own NB: *"shard X is 96% one board... A better packer cannot
help it"*). It is also the largest ADR-0053 scope-exclusion carrier every run (1,703 / 1,705 / 1,706
/ 1,706 rows kept out of scope — essentially flat this window, not the slow monotonic climb the
09-09/09-10 reviews measured; consistent with `#393`'s ≥99%-tolerance fix not applying here, since
this board reads only ~88% of its listing). **One new wrinkle in run 2**: the slowest shard overall
was *not* the hcltech shard but shard 7 (`oracle:jpmc-dev5.fa.oraclecloud.com`, 1,238s, only 46%
floor-bound) — the first time in the reviewed windows an Oracle dev-pod board, not hcltech, was a
run's single slowest shard. One occurrence; not enough to generalize from.

`embed_plan` continued fanning out across 4-5 shards in every run (388/370/421/475 new Docs), the
`#390` fix from the 09-09 window holding steady — actual/predicted medians 0.80-0.91, all `cpu`.

## 5. What is NOT wrong

- **The row-count chain has no gap or overwrite anywhere in this window**, including across the
  failed run — see §3's caveat that this isn't a stress-test of the fix, but it is a clean chain.
- **Board errors are a flat floor**: 42 / 23 / 25 / 21 against ~18,663-20,000 boards attempted —
  0.1-0.2%, matching both prior reviews' pooled rate. No shard hit its time budget in any run.
- **The 21,122-line `board_key()` log flood is gone** — confirmed absent from every join log in
  this window (`#391`, merged before this window, per the 09-09 review's own recommendation).
- **`role_trends.csv` is no longer re-uploaded whole** — the merge logs show no 172 MB upload;
  `#394`'s Parquet fix is live.
- **No budget kills, no deferred-ATS-in-slice cases, and quarantine stayed a stable 693-694 boards**
  (0 cleared, as both prior reviews found — still by design, not re-verified live this round).
- **Retry volume and egress behavior are unremarkable**: 114k-132k retries/run, 0/15 shards ever
  look DIRECT by retry ratio, spare-egress rescues 100% of walled requests on workday and eightfold
  in every run — the same standing condition both prior reviews recorded, not a new degradation.
- **HF storage's `live` figure grows smoothly** (5.97 → 5.99 → 6.01 → 6.03 GB across the window,
  ~20 MB/run) with no discontinuity from the failed run.

## 6. A new, unranked observation: storage squashes every run now, not every four

`usedStorage` this window: 75.45 → 75.47 → 75.51 → 75.56 GB, against the same `SQUASH_ABOVE_GB: 40`
threshold the 09-10 review measured at 35.50-41.10 GB. Every run in this window is comfortably
above the 40 GB trigger, so `super_squash_history` fires on all four (`##[notice]squashed`),
whereas the 09-10 window squashed once and coasted for "roughly four runs of headroom." The step's
own comment names the likely cause: `usedStorage falls as HF collects the orphans` — an async,
HF-side reclaim independent of the squash call itself, which `super_squash_history` cannot force.
This reads as backend GC lag, not a broken squash: the code's own safety check (`live2 < live` ⇒
`::error::`) never fired in any of the four runs, so the live file set was never at risk. Flagged
as worth a glance next review, not as a finding — one day of data cannot distinguish "temporarily
behind" from "the reclaim stopped working."

## Ranked scope of improvement

| # | change | est. effect | confidence |
|---|---|---|---|
| 1 | Give `scrape_join` an expected-shard-count check, mirroring `embed_merge`'s `--expect-shards` (§1) | turns a silent "14 of 15" into a visible warning; costs nothing when all 15 arrive | high — the gap is confirmed in code, and this window is a real instance of it firing silently |
| 2 | Watch whether `usedStorage` recovers headroom over the next few days, or whether the async HF-side reclaim has genuinely slowed (§6) | early warning before the 40 GB trigger becomes "squash every run" permanently | low — one day of data, explicitly not enough to call a regression |
| 3 | Re-run this review's §3 the next time a `cleanup-index` run genuinely overlaps a pipeline run, to get the first real post-`#412` stress-test of the LanceDB compare-and-swap | closes the gap this window left open | medium — needs the right timing, not something to force |
| 4 | If GitHub's artifact-upload ETIMEDOUT recurs, consider a retry on `upload-artifact` before treating shard loss as routine (§1) | currently a 1-in-100-run event fully absorbed by design; only worth acting on if the rate rises | low — one occurrence, base rate is 2% and this was a clean absorb |

Nothing rises to the urgency of the 09-10 review's #1 or #2, because both are already fixed and
holding. The genuinely new material in this window — the failed run and the cancelled run — both
turned out to be the pipeline's error-handling working as designed, not defects to fix.

## Method and limitations

Logs were read through `scripts/runlog/`'s analysers (`fanout_plan`, `fanout_timing`,
`fanout_retries`, `fanout_errors`, `fanout_corpus`, `fanout_ledgers`, `fanout_embed`,
`fanout_merge`, `scope_exclusion_persistence`) over per-job logs cached in
`experiment/runlog/artifacts/`, all through `run_logs.clean()`'s echo filter, plus direct
`gh api .../logs` pulls (also echo-filtered) for the failed shard and the join job's fragment
count, and the `analyse-fanout-run` skill's own guidance for step ordering.

- **The single biggest limitation this review corrected mid-stream, not before starting**: the
  session's initial belief about `main`'s HEAD was wrong because the local ref was unfetched. §0
  documents the correction and the evidence for it; every other section assumes `origin/main`
  (`42dfb964`) as ground truth, not the stale local `fd155ef5`.
- **Board-count and net-row claims all trace to `fanout_merge.py`'s printed lines**, cross-checked
  against the raw `##[warning]` scope-exclusion text for two runs (§3, §4) to confirm the tool's
  parse matches the source.
- **The ~21,000-tech-job estimate for shard 4's lost data (§1) is a projection** — 103,079 raw
  jobs × the run's own 20.5% aggregate kept rate — not a per-board or per-ATS measured figure,
  since shard 4's own per-ATS breakdown never reached any downstream log.
- **End-to-end serving was not verified.** The Space's `/search` endpoint sits behind the sign-in
  wall from this session (matching the 09-10 review's own note), so every claim about "reaches the
  served index" is from merge-log row counts, not a live query.
- **The teamtailor before/after (§3) is one run on each side of the fix**, not a multi-run
  average — but since the fix is a literal exclude-list entry rather than a probabilistic effect,
  a single clean before/after is sufficient evidence here, unlike a throughput or timing claim.
- **The storage observation in §6 is explicitly one day of data** and is reported as "worth
  watching," not as a finding — see the confidence rating in the ranked table.
