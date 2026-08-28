# Five-run log review — 2026-08-13

Runs reviewed (all `nightly-pipeline`, all green, chronological):

| Run | Started (UTC) | Wall | Slowest shard | Worst board | Retries | actual/predicted |
|---|---|---|---|---|---|---|
| 31617329226 | 2026-08-12 16:23 | 69 min | 34.5 min | 2025 s | 191,648 | 0.06–0.31× |
| 31626605994 | 2026-08-12 18:13 | 63 min | 35.9 min | 2086 s | 232,530 | 0.07–0.33× |
| 31636112467 | 2026-08-12 20:08 | 69 min | 43.7 min | 2585 s | 201,108 | 0.06–0.40× |
| 31645669556 | 2026-08-12 22:08 | 59 min | 30.6 min | 1792 s | 185,745 | 0.07–0.27× |
| 31653231046 | 2026-08-13 00:05 | 74 min | 49.5 min | 2896 s | 157,318 | 0.08–0.44× |

Every run reported success. None of what follows was surfaced by a red run — which is the
point of reading the logs.

---

## 1. The served index flaps: whole Eightfold boards leave and come back every run

Counting board prefixes in the `[index] evict` / `[index] add` lines gives a clean ping-pong:

| Run | Top evictions | Top additions |
|---|---|---|
| 31617329226 | `nvidia.eightfold.ai` ×850, `jobs.nvidia.com` ×557, `ngc` ×523 | `citi` ×245, `qualcomm` ×199 |
| 31626605994 | `qualcomm` ×635, `careers.qualcomm.com` ×398, `jobs.nvidia.com` ×314 | **`nvidia.eightfold.ai` ×838**, `ngc` ×401 |
| 31636112467 | `nvidia.eightfold.ai` ×782, `lever:jobgether` ×125 | **`qualcomm` ×642**, `careers.micron.com` ×585, `ngc` ×508 |
| 31645669556 | `ngc` ×515, `careers.qualcomm.com` ×356, `infineon` ×277 | **`nvidia.eightfold.ai` ×803**, `jobs.nvidia.com` ×656 |
| 31653231046 | `jobs.nvidia.com` ×842, `qualcomm` ×487, `micron` ×413 | `infineon` ×276, `ngc` ×191 |

Evicted in run N, re-added in run N+1, evicted again in N+2. Net table size oscillates between
280,595 and 283,185 rows and never converges. Per-run churn runs `add 1,519 / evict 3,268`,
`add 4,073 / evict 1,762`, `add 1,192 / evict 3,550`.

**User-visible effect:** NVIDIA, Qualcomm and Micron jobs are absent from search for roughly two
hours at a stretch, then return, then vanish again. A user who searches at the wrong moment sees
none of them.

**The mechanism, confirmed in code.** `index.py:158-170`'s `_scraped_boards` defines the eviction
scope as `{board_of(job["id"]) for job in iter_jobs(data/jobs)}` — **a board is "scraped" iff it
emitted at least one job line this run** — and `index_plan.py:58` then deletes every indexed id
not in the fresh set whose board is in that scope. No board-outcome signal reaches sync at all:
`harvest.scrape_all` records failures in `RunResult.errors`, but `pipeline.yml:265-272` ships only
`data/jobs` + `data/state` to the merge job, never the shard reports.

So the guard is all-or-nothing at the *line* level. A board returning **zero** rows is protected;
a board returning **one page of a paginated board** is fully in scope, and its other 849 indexed
rows are evicted as delisted.

**A correction worth carrying: the Workday 429s are not the driver.** "Errored → evicted" is
false — a raising scraper writes no lines, so `_scraped_boards` never sees the board, and Workday
failures raise. The culprit is a scraper that swallows its own failure and returns partial output:
`scrapers/eightfold.py:130-139` breaks out of pagination on `if r.status_code != 200: break` and
returns what it has, even though the API hands back `data.count` and the truncation is therefore
exactly detectable. A second silent path: on the sitemap fallback a failed per-job detail fetch
yields `None` and `parse` drops it (`eightfold.py:242-265`), so ids vanish with no error recorded
anywhere. That is why the flapping is entirely Eightfold-shaped.

PR #102 ("stop sync and prune churning the same rows every run") addressed a different churn and
did not touch this one.

## 2. `ats:tenant:id` is ambiguous, and prune misparses it — a permanent re-prune loop

Workday job ids are not opaque tokens. Real ids observed in the served table:

- `workday:dmainc/DMA:REQ: 228` — the id is `REQ: 228`, containing a colon and a space
- `workday:otis/REC_Ext_Gateway:OT221: GD - NEW YORK, NY One Penn Plaza, New York, NY, 10119 USA` — the id carries a full postal address
- `workday:campaignmonitor/marigold:https://campaignmonitor.wd5.myworkdayjobs.com/marigold/job/Remote-United-States/Manager-Security-Engineer_R2454` — the id is an entire URL

The composite key is `ats:tenant:id`, the Workday tenant is itself `co/site`, and both halves can
contain `:` and `/`. Splitting that key on `:` therefore attributes these rows to the wrong Board,
so prune classifies them **off-Board** and evicts them — after which sync re-adds them, because
they are in fact on a live Board.

The logs prove the loop rather than merely suggesting it: the same four `dmainc/DMA` rows and the
same three `otis/REC_Ext_Gateway` rows are pruned in run 31617329226, again in 31645669556, and
again in 31653231046. This is the same failure class as §1, just small enough to be invisible.

## 3. Six Workday tenants are scraped twice, differing only in case

Across the five runs, 1,655 distinct Workday tenants appear, of which six are pure case variants:

```
abb/External_Career_Page        abb/external_career_page
aptiv/APTIV_CAREERS             aptiv/aptiv_careers
micron/External                 micron/external
nvidia/NVIDIAExternalCareerSite nvidia/nvidiaexternalcareersite
qualifacts/Qualifacts_External_Careers  qualifacts/qualifacts_external_careers
vantive/Vantive                 vantive/vantive
```

`data/validate/liveness/workday.csv` — the committed, authoritative ledger — holds only the
lowercase form of each, yet its `url` column preserves the original capitals
(`abb/external_career_page` → `https://abb.wd3.myworkdayjobs.com/External_Career_Page`). So the
tenant key was lowercased somewhere while the URL was not, and the scraper then emits job ids
keyed on the capitalised site name Workday itself returns. Board keys and job keys disagree in
case, and the pair is scraped as two boards.

This is not free. `board_priority.csv` ranks both `workday:nvidia/NVIDIAExternalCareerSite`
(1,922 tech jobs) and `workday:nvidia/nvidiaexternalcareersite` (1,880) in its top ten, and both
`workday:micron/external` (1,851) and `workday:micron/External` (1,770). Roughly 11,000 duplicate
job fetches per run land on the second-most-expensive ATS. The `evict duplicate` step catches only
3–4 rows a run, so the duplicates are largely *served*, not deduped.

The likely origin is the known gotcha that `merge_harvest_into_tenants.py` lowercases every slug.

## 4. The cost model is wrong by 3–14×, and it is costing coverage

Every run emits:

```
[scrape_plan] 20000 boards across 15 shards; predicted makespan ~111.7 min
##[warning] predicted makespan ~111.7 min exceeds the 60 min shard budget — shards
            matching their prediction will bank partials
```

and then finishes the scrape in 30–50 minutes. `actual/predicted` is reported as 0.06–0.44× in
every single run — the planner knows it is wrong and says so, five runs running.

Two consequences. The warning is pure noise: it fires every run and has never once described what
happened. More importantly, the planner sizes the slice to 20,000 boards because it believes that
is already ~110 minutes of work. It is actually ~35. The keep-set is **60,545 live Boards**, so at
20,000 per run a board is revisited only every third run. A calibrated model could take a much
larger slice inside the same budget — this is the cheapest available coverage win, and it also
shrinks §1, since fewer boards would sit out any given run.

The predicted spread is also `min 109.8 / mean 109.8 / max 109.8 (1.00×)` — binpacking is
distributing predicted work perfectly evenly, which is exactly why the *actual* spread is so
uneven. The estimates it balances are not the times observed.

## 5. One board owns the critical path

Worst single board per run: 2025 s, 2086 s, 2585 s, 1792 s, 2896 s — against slowest-shard times
of 34.5, 35.9, 43.7, 30.6 and 49.5 minutes. In run 31653231046 one board is 2,896 s of a 2,970 s
shard: **97.5% of that shard's wall clock is a single board.** Binpacking cannot help; only a
per-board wall-clock cap can. This confirms the standing open item, now across five more runs.

## 6. Retries are enormous and unattributed

157,318–232,530 retries per run, against 20,000 boards — a mean of roughly 8–11 retries per
board. The fan-out line reports the total and nothing else: no breakdown by ATS or status. Given
166 429s and 112 500s are *logged*, the retry counter is measuring something an order of magnitude
larger than the errors that survive to be reported. Either most retries succeed silently (in which
case they are the hidden cost driver behind §4 and §5) or the counter is measuring something other
than what its name suggests. It cannot currently be told apart, which is itself the finding.

## 7. `state_fetch` before `embed_merge` costs up to 11 minutes

| Run | Files | Seconds |
|---|---|---|
| 31617329226 | 1,094 | **667** |
| 31626605994 | 1,252 | 155 |
| 31636112467 | 1,375 | 186 |
| 31645669556 | 1,466 | 238 |
| 31653231046 | 1,624 | 230 |

The file count climbs monotonically (1,094 → 1,624 over five runs) because the store is never
compacted, and the run that hit 667 s spent 15% of its total wall clock downloading state. The
logs also carry `Warning: You are sending unauthenticated requests to the HF Hub. Please set a
HF_TOKEN to enable higher rate limits and faster downloads` — this download is running
unauthenticated and rate-limited.

## 8. The embedding store is 2.3× the corpus it serves

`embed_merge` reports 454,832 vectors; the corpus is 197,292 jobs and the served table 280,806
rows. At 768 × f32 that is ~1.4 GB, of which more than half is vectors for jobs that left the
corpus long ago. Nothing prunes it, and `state_fetch` pays for the size every run (§7).

## 9. Resolved: the 12,624 vector-less jobs are not a backlog

A standing question was whether the ~6% of jobs without vectors were non-English by design or an
embedding backlog. The numbers close exactly:

```
[embed_plan] new Docs: 703 (scanned 199336, already 186009, non-English 12624)
[index] corpus: 199336 Jobs ... 186712 have vectors — 12624 without
```

186,009 + 12,624 + 703 = 199,336. The vector-less set *is* the non-English set, to the job. There
is no backlog. The `[index]` line's wording — "non-English, or run embed_run --resume" — is what
made this ambiguous; it should state the split it already knows.

## 10. Smaller notes

- **Scrape volume is dominated by the two lowest-yield ATSes.** SuccessFactors contributes 333,511
  of ~1,002,067 scraped jobs at 10.7% tech; Workday 203,033 at 7.4%. Together they are 53% of
  scrape volume and 25% of tech output. Overall tech yield is a steady 20%.
- **22.3% of served rows are non-tech** — `role_trends` reports this every run as the ADR-0017
  filter's deliberate recall-biased creep. Worth confirming it is still the intended trade at that
  magnitude.
- **`ci` is not broken.** The failing run (31600499853) is on a Dependabot branch; `main` is green.
  The standing note that CI is failing should be closed.
- **`darwinbox` returned 96 jobs from 11+ boards** and posts 8–16 HTTPErrors per shard every run —
  the highest error count of any ATS. It is effectively not working.
- **`personio` throws 1–4 ParseErrors in every shard of every run**, and `successfactors` 1–2
  `CertificateVerifyError`s. Both are steady-state, not transient.

---

## Separately: the `alerts` workflow has failed eight consecutive runs

Not part of the pipeline, but it is what generated the failure emails.

Last success 2026-08-12 09:48 UTC; first failure 11:37 UTC; eight failures since. The error:

```
[alerts] search attempt 1 failed (HTTPError); retrying in 15s
[alerts] search attempt 2 failed (HTTPError); retrying in 30s
[alerts] search attempt 3 failed (HTTPError); retrying in 60s
[alerts] 437e7d1c48cb7900: FAILED SearchUnavailable: HTTPError: HTTP Error 401: UNAUTHORIZED
```

**Cause.** `deploy/hf-space/app.py` sets `_PUBLIC_PATHS = {"/", "/auth/google", "/me",
"/unsubscribe"}` and `_require_sign_in` 401s everything else. `/search` is not public.
`src/headstart/alerts/space_query.py` calls `/search` with a bare `urllib` request carrying no
credentials, so the digest generator has been locked out of its own search endpoint since the
sign-in wall shipped. The comment above `_PUBLIC_PATHS` reasons explicitly about not breaking the
unsubscribe link a mailed Digest carries — the digest *generator* was missed.

**Second defect, independent of the first.** `space_query.newly_seen` retries on any exception.
A 401 is permanent, so each affected subscriber burns the full 15 + 30 + 60 s budget before
failing. The retry ladder is documented as sized to a Space cold start; it should not apply to an
auth failure that cannot succeed.
