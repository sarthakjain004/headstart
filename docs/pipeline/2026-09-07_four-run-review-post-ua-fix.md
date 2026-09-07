# Four-run pipeline review — 2026-09-07, post UA-fix verification

Runs analysed: the four `nightly-pipeline` runs immediately after
`docs/successfactors/2026-09-07_user-agent-denylist.md`'s fix merged (`d39dfa6`, #362), i.e. the
runs `docs/pipeline/2026-09-07_five-run-log-review.md` could not yet see.

| Run | headSha | Started (UTC) | Result | Wall | scrape max |
|---|---|---|---|---|---|
| 34103055382 | `d39dfa6` (the fix itself) | 08:54 | success | 89 min | 24.3 min |
| 34111206926 | `87ee4d9` | 10:23 | success | 79 min | 24.0 min |
| 34112476117 | `f419503` | 10:38 | **cancelled, 0 jobs** | — | — |
| 34118022576 | `f419503` (#370) | 11:42 | success | 70 min | 21.9 min |

`34112476117` is a `schedule`-triggered run that arrived while `34111206926` was still in flight and
was superseded by the newer `workflow_dispatch` run the `chain` job dispatched moments later — the
documented ADR-0093 back-to-back behaviour, confirmed again (a second instance, `34085443006`, shows
the identical shape earlier the same day). Not a failure; excluded below.

Wall time itself is dominated by `merge`'s "Download the prior store + LanceDB" step, not by
`scrape` — see §2.

---

## 1. CONFIRMED: the SuccessFactors User-Agent fix works in production, on real nightly-pipeline runs

`docs/successfactors/2026-09-07_user-agent-denylist.md` left one thing open: every measurement in it
was from a laptop or an isolated probe workflow, never from `nightly-pipeline` itself. These three
runs are literally the first ones to scrape with the fixed `USER_AGENT` end to end, and the shard logs
settle it.

`careers.te.com` — the board that owned 90%+ of the scrape critical path in every one of the prior
five-run review's runs, at 0 jobs each time:

| Run | Result |
|---|---|
| 34103055382 (08:54) | `2/2116 detail fields missing` — 2,114 of 2,116 pages parsed |
| 34118022576 (11:42) | `1/2127 detail fields missing` — 2,126 of 2,127 pages parsed |

`jobs.scotiabank.com` (previously 958 s / 0 jobs, every run):

```
08:58:33 [scrape_run] slow board successfactors:jobs.scotiabank.com: 1617 jobs in 133s
```

1,617 of 1,617 listed pages became real jobs, in 133 s — close to the writeup's own laptop-sample
projection (~8 min, later revised down after an Actions-vantage probe measured 0.3–0.4 s/page).

`jobs.l3harris.com`, `corningjobs.corning.com`, `careers-inc.nttdata.com`, and
`southasiacareers.deloitte.com` all list their full page sets (1,996 / 1,219 / 1,416 / 1,733 pages)
and **none of them appear in any shard's `slow board` list any more** — the pre-fix runs had all four
in the "worst offenders" table at 944–1,665 s each; post-fix they finish under the 120 s slow-board
threshold entirely. (Their individual `detail fields missing` completion lines did not surface in this
sweep's grep window — likely a fast enough finish that it landed outside the captured range — so this
is inferred from absence-from-stragglers rather than a captured completion line; `careers.te.com` and
`jobs.scotiabank.com` are the two boards with a directly captured, unambiguous number.)

**Knock-on effect on the actual critical path.** `scrape_plan`'s predicted single-board floor — which
tracked `careers.te.com`'s stale, pre-fix cost-ledger entry — fell run over run as the ledger
re-measured the now-fast board:

| Run | predicted floor | actual scrape wall |
|---|---|---|
| 34103055382 | 27.2 min (stale pre-fix cost data) | 24.3 min |
| 34111206926 | 20.9 min | 24.0 min |
| 34118022576 | 22.0 min | 21.9 min |

Actual scrape wall time dropped ~2.4 min from the first post-fix run to the third, and the
predicted-vs-actual gap tightened to near-exact by the third run as the cost ledger caught up. This is
the mechanism the writeup predicted (§"Does this make the makespan floor worse?") playing out in real
runs, not a laptop projection.

**Recommendation:** close out the "Pending: the Actions vantage" section of the UA-denylist writeup
and the "verification pending" flag on item 1 of the five-run review's ranked scope — both can now
read CONFIRMED with these three runs as evidence.

---

## 2. STILL OPEN: the ADR-0064 gate blind spot (five-run review item 2)

Checked directly: `src/headstart/ingest/scrape_plan.py` has no commits today and its `_gated_boards`
logic is unchanged — it still divides a *carried* (possibly stale) priority score by the ledger's
`seconds`, so a board whose yield collapses to zero can still clear the 2.0 tech-jobs/min gate forever,
because a zero-yield scrape emits no priority-ledger row to decay the stale score. The SuccessFactors
UA fix happened to remove the specific instance this was written about, but the structural gap — a
board that is scraped and returns nothing looks, to this gate, identical to a board that was never
scraped at all — is untouched and will catch the next board that goes silently jobless for a different
reason. Worth doing regardless of item 1's resolution.

---

## 3. STILL OPEN, still the dominant cost: `merge`'s HF download

The 2026-08-25 review flagged `state_fetch`'s "Download the prior store + LanceDB" step as an
HF-side, size-dependent slowdown that "has not self-corrected." Three more data points, from three
more runs:

| Run | files | bytes | step time | effective rate | % of merge job wall |
|---|---|---|---|---|---|
| 34103055382 | 11 | 4,295 MB | **1,962 s** (32m43s) | 2.2 MB/s | 84% |
| 34111206926 | 35 | 4,339 MB | **1,241 s** (20m41s) | 3.5 MB/s | 76% |
| 34118022576 | 63 | 4,373 MB | **815 s** (13m35s) | 5.4 MB/s | 68% |

Same shape as before: dozens of small files land in seconds (10–60 files/s), then the download stalls
hard on the last 1–3 files — 26 of the 33 minutes in run 1, 15 of 21 in run 2, 10 of 14 in run 3 — all
inside the single largest file(s) in the batch (`embeddings.f32`, `jobs.lance` fragments). This is
`state_fetch.py`'s `snapshot_download()` call, the exact mechanism `docs/adr/0085-...md` already
diagnosed as unsafe for multi-GB pulls and built `scripts/fetch/pull_lancedb.py` to replace — but
`state_fetch.py` (used by `join` and `merge` in CI) still calls `snapshot_download` directly, not the
ranged-GET resumable path. This single step is 68–84% of the merge job's wall time in all three runs,
and merge is the second-largest pipeline stage after scrape. The three options the 08-25 review
weighed (canary `hf_transfer`, prune the 43%-unused embedding-store cache, stop `np.fromfile`-ing the
whole 1.7 GB vector blob for ~500 rows) are all still on the table and still unstarted.

The file count climbing run to run (11 → 35 → 63) while total bytes barely move (4,295 → 4,339 → 4,373
MB) is LanceDB fragment accumulation between `cleanup-index` compactions, exactly as the 08-25 review
described — not a new phenomenon, just a fresh instance of it.

---

## 4. A new transient error spike, self-resolved — same class as the already-withdrawn circuit breaker

Board errors across the three runs: **96 (0.5%) → 24 (0.1%) → 20 (0.1%)**. The 96 in the first run is
well outside the 19–40/run baseline the five-run review established, and the excess is entirely
`Timeout`: **66 timeouts — 49 trakstar, 17 successfactors** — versus zero timeouts in either of the
other two runs. This is a second instance of the same shape already investigated and written up in
`docs/pipeline/2026-09-07_circuit-breaker-cannot-catch-a-concurrent-fanout.md` (that one was trakstar
alone, 20 boards); this one is broader (two ATSes at once) but the conclusion already reached there
applies without new work — boards run concurrently inside a shard, so a completion-triggered circuit
breaker cannot prevent requests already on the wire during a five-minute outage, and ADR-0053 already
kept the affected boards out of eviction scope, so nothing was wrongly delisted. Recorded as a second
data point rather than a new problem; no action needed unless this pattern starts recurring more than
twice.

---

## 5. Everything else: healthy, and matches the existing baseline

- Served table: 335,543 → 335,385 → 335,355 rows — flat, same pattern as the 08-25 and 09-07 reviews.
- Tech-filter keep rate: 20.9%, 21.0%, 20.9% — unchanged.
- `index prune`: 0 evictions in all three runs (liveness ledger SHA unchanged).
- `embed`: 634/410/369 new Docs across 1–2 shards — light, consistent with the 09-07 five-run review's
  observation that embedding is no longer the pipeline's dominant stage.
- `update_meta`: all three runs report `derivations v7 stored, v7 in code — no sweep` — see §6.
- Scrape shard imbalance persists (fastest shard ~10–11 min, slowest ~22–24 min, ~2–2.2x spread) even
  post-fix — this is the already-documented "one board sets the makespan" structural pattern
  (08-25 review item 1), just with a different board now that SuccessFactors's worst offenders are
  fixed. Not investigated further here; same shape, not a new finding.

---

## 6. Forward-looking: the next chain-triggered run will carry a derivations sweep

All three runs here predate `929703c` ("Let a confident remote JD supersede the field", #373, merged
2026-09-07T13:04:35Z), which bumped `DERIVATIONS_VERSION` 7 → 8. The run in flight at review time
(`34124216871`, headSha `630e853`) also predates it. `update_meta`'s `no sweep` fast path — 36–38 s
processing 636k+ rows by only touching the small re-derive queue (224, 196, then 2,207 ids across
these three runs) — will not apply on the first run whose `merge` job runs with v8 in code: every row
with a held description (493,629 per `remote.py`'s own measurement) becomes eligible for re-derivation
in that pass. This is ordinary, working-as-designed sweep behaviour (the same mechanism experience/
salary version bumps already exercise), not a bug — but it is worth watching `update_meta`'s step time
on that run against this review's 36–38 s baseline, since it hasn't been observed yet at this corpus
size with a `remote`-shaped sweep specifically.

---

## Ranked scope for improvement

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | SuccessFactors UA denylist | **CONFIRMED FIXED** — close out the "pending" flag | §1 |
| 2 | ADR-0064 gate reads a carried score, not measured yield | still open | §2 |
| 3 | `state_fetch` uses raw `snapshot_download`, not the ADR-0085 ranged-GET path | still open, 68–84% of merge wall every run | §3 |
| 4 | Scrape shard imbalance (single-board makespan floor) | still open, structural, already documented | §5 |
| 5 | Watch `update_meta` step time on the first v8-sweep run | new watch item, not yet observed | §6 |
