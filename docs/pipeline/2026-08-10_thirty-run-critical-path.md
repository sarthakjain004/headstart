# Thirty pipeline runs: critical-path analysis (2026-08-08 → 2026-08-10)

**Source:** the 30 most recent `nightly-pipeline` runs as of 2026-08-10 ~16:30 UTC (run ids in the
table). Per-job timings from `gh run view <id> --json jobs`; error/throughput lines from targeted
greps of the join/merge job logs, plus full shard-log sampling on three runs (31398898491,
31386438292, 31380219143). Collected by three parallel agents, method per the `analyse-fanout-run`
skill: per stage, hold **max** (critical-path minutes) apart from **Σ** (total work minutes);
separate queue time from active time. NB `scripts/run_stats.py`, which that skill references, does
not exist in the repo — stats were computed from the jobs JSON directly.

**Headline:** all 29 completed runs succeeded (zero failed jobs, zero HF retry events).
Active wall ≈ **scrape-max + a ~13–20 min fixed tail** (join ~6.3 + embed 3–15 + merge ~5–7).
Queue time ~0 in 28/29 (one 8.5 min concurrency-group wait). The runs are bimodal: in **22 of 29,
one scrape shard ran to the 60-minute budget** and banked a partial — those runs land at 76–94 min;
the 7 uncapped runs land at 47–65 min. One straggler shard costs ~25–30 min of wall on
three-quarters of all runs.

## Per-run table

Minutes. Scrape shown `max/Σ`; **bold** max = a shard pinned at the 60 m budget. Totals include
queue (only 31386438292 had any: 8.5 m).

| run | created (UTC) | total | scrape max/Σ | join | embed max | merge |
|---|---|---|---|---|---|---|
| 31407817300 | 08-10 16:13 | in flight at capture | 28.6+ (1 shard running) | – | – | – |
| 31398898491 | 08-10 14:34 | 85.8 | **60.3**/262 | 6.5 | 11.5 | 6.6 |
| 31386438292 | 08-10 12:06 | 94.0 | **60.4**/247 | 6.5 | 12.0 | 6.1 |
| 31380219143 | 08-10 10:41 | 93.1 | **60.4**/295 | 5.9 | 14.8 | 11.2 |
| 31372217180 | 08-10 08:55 | 64.6 | 37.6/231 | 6.5 | 12.5 | 7.1 |
| 31363406822 | 08-10 06:49 | 47.5 | 26.8/197 | 6.5 | 7.9 | 5.5 |
| 31357366652 | 08-10 05:02 | 82.6 | **60.4**/325 | 6.2 | 7.8 | 7.6 |
| 31352419654 | 08-10 03:22 | 80.5 | **60.3**/244 | 6.3 | 7.4 | 5.8 |
| 31343113879 | 08-09 23:56 | 77.8 | **60.3**/307 | 6.4 | 3.3 | 7.3 |
| 31338099881 | 08-09 21:56 | 77.5 | **60.2**/297 | 5.7 | 4.3 | 6.7 |
| 31332875347 | 08-09 19:54 | 63.7 | 36.4/227 | 6.6 | 3.1 | 17.0 |
| 31327686425 | 08-09 17:55 | 76.1 | **60.4**/295 | 6.3 | 3.2 | 5.6 |
| 31322410780 | 08-09 15:55 | 75.9 | **60.2**/269 | 6.4 | 3.4 | 5.4 |
| 31317463076 | 08-09 14:03 | 52.1 | 36.4/226 | 5.8 | 2.9 | 6.3 |
| 31311882046 | 08-09 11:52 | 77.9 | **60.3**/302 | 6.4 | 4.0 | 6.6 |
| 31307377445 | 08-09 10:02 | 77.7 | **60.4**/260 | 6.4 | 2.8 | 7.4 |
| 31302708052 | 08-09 08:06 | 52.6 | 37.0/201 | 6.3 | 3.4 | 5.4 |
| 31298119319 | 08-09 06:05 | 79.8 | **60.3**/336 | 6.5 | 8.0 | 4.6 |
| 31295169514 | 08-09 04:43 | 78.3 | **60.4**/326 | 6.6 | 3.7 | 7.0 |
| 31291942426 | 08-09 03:12 | 82.8 | **60.4**/345 | 6.5 | 2.7 | 12.8 |
| 31284924590 | 08-08 23:51 | 86.8 | **60.3**/312 | 6.5 | 9.2 | 10.2 |
| 31280410695 | 08-08 21:53 | 84.4 | **60.4**/277 | 6.3 | 2.9 | 14.2 |
| 31275437737 | 08-08 19:50 | 54.6 | 38.3/245 | 6.2 | 3.3 | 5.9 |
| 31270593905 | 08-08 17:54 | 81.7 | **60.2**/247 | 6.3 | 7.2 | 7.2 |
| 31265619727 | 08-08 15:54 | 79.5 | **60.3**/256 | 6.3 | 5.8 | 6.5 |
| 31261057489 | 08-08 14:04 | 79.5 | **60.4**/292 | 6.4 | 5.3 | 6.7 |
| 31255932655 | 08-08 11:51 | 77.8 | **60.3**/267 | 6.3 | 5.2 | 5.5 |
| 31251851368 | 08-08 10:00 | 77.3 | **60.4**/326 | 5.6 | 4.5 | 6.2 |
| 31247612495 | 08-08 08:04 | 50.9 | 32.5/219 | 6.2 | 6.6 | 5.0 |
| 31243064941 | 08-08 06:03 | 75.6 | **60.4**/238 | 5.8 | 3.9 | 4.8 |

scrape-plan is 0.2–0.4 min in every run (omitted). The critical path is fully additive — e.g.
31398898491: 0.3 + 60.3 + 6.5 + 11.5 + 6.6 = 85.2 vs 85.7 active (matrix-scheduling gaps ~1 min).

## The scrape straggler is floor-bound, not sum-bound

By `wall = max(Σ work ÷ concurrency, slowest single item)`: Σ scrape is 197–345 min, so
Σ÷15 ≈ 13–23 min — yet the actual max is 60 (capped). The **median** shard runs 9–21 min; the
straggler runs to the cap at 4–6× the median. No packing improvement beats a floor; the fix is
per-item.

The capped shard is a **different index every run** (4, 5, 6, 7, 9, 10, 12 … across the window),
which fits one slow board-set rotating through the plan, not a fixed hot shard or a packer bug.
**Hypothesis (labelled as such, not yet measured):** a board whose own scrape exceeds the remaining
budget never completes, so it never records a `board_cost` row, so the planner never learns it is a
monster — a permanent black hole that eats whichever shard it lands in. The per-board logging that
merged 2026-08-10 (ADR-0039, PR #96) should name the culprit boards on the next capped runs: the
last boards a capped shard was chewing appear as its final per-board lines.

## Errors and throughput (sampled)

- **Board errors: ~400–580 per run** (414 / 490 / 582 across the three sampled runs; 17–62 per
  shard, ~2–3 % of the ~20 k-board slice). All absorbed as per-board errors; none failed a job.
  Until ADR-0039 these were invisible (count only); the type × ATS WARNING summary now names them.
- **Tech filter steady state:** ~0.79–1.01 M raw lines joined → ~183–200 k tech (~20 %).
- **Embed is vestigial at steady state:** 47–688 new Docs per run → the 15-lane matrix
  self-collapses to 1–2 shards of 3–15 min, 0 failed Docs anywhere in the window. The 180 m embed
  budget and `_S_PER_DOC` calibration are sized for a backlog world that no longer exists
  (harmless — it degrades gracefully).
- **Index churn:** add 1.6–4.6 k / evict 0.9–4.2 k per run; prune 222–796; served table steady at
  274–278 k rows; vector store grew 429.6 k → 434.5 k monotonically.
- **Merge variance is HF download, not merge logic:** state_fetch pulls ~1,285 files; typically
  2–4.6 min, but 7.5–13.8 min in four runs (the 10.2–17.0 merge outliers in the table). Actual
  merge+sync+prune is ~30 s everywhere. File count grows between compactions (ADR-0036).
- **Non-English re-gating leak:** ~12.3–13.0 k non-English rows are language-gated from scratch in
  every run's embed_plan (they never enter `meta.jsonl`, so the diff can't skip them). Minor,
  bounded cost inside join's ~6.3 min.

## Ranked scopes of improvement

1. **Per-board timeout in the scrape shard** — the only lever that matters: ~25–30 min × 22/29
   runs. A cap of ~10–15 min (≈40× the median board) converts the monster board into a recorded,
   visible failure, and its cost row finally lands so the planner learns. **Design constraint:** a
   timed-out board must contribute no partial job set to the fragment (or be excluded from the
   eviction scope) — otherwise it recreates the partial-board eviction hole already open against
   ADR-0014 (a partial board's unfetched jobs would evict as though closed). The two designs
   should land together.
2. **Triage the recurring board errors** using the new per-run type × ATS summaries; demote
   persistent offenders in liveness/priority so they stop burning scrape seconds every 2 h.
3. **Merge's HF-download stalls** — tighten the cleanup-index compaction cadence or narrow the
   state_fetch patterns; worth ~5–9 min on occasional runs.
4. **Embed right-sizing / non-English id set** — cosmetic at current volume; a persisted
   non-English id set would shave repeated langdetect work from the join.

Queue time and runner capacity need nothing: 28/29 runs started within seconds, and the 2 h cron
slot comfortably fits even the 94-min worst case.
