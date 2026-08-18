# Ten-run log review — 2026-08-18

Successor to `2026-08-13_five-run-log-review.md`. Runs reviewed (all `nightly-pipeline`,
chronological, 2026-08-17 15:46Z → 2026-08-18 09:55Z):

| Run | Head | Started (UTC) | Shards | Jobs scraped | Fatal board errors | Retries | Slowest shard |
|---|---|---|---|---|---|---|---|
| 32043354923 | `ab23879` | 08-17 15:46 | 12/15 | 805,009 | 245 | 206,365 | **60.0 min (budget)** |
| 32052278180 | `ab23879` | 08-17 17:52 | 15 | 998,596 | 270 | 213,243 | 42.1 min |
| 32062384178 | `ab23879` | 08-17 19:48 | 15 | 983,901 | 289 | 208,300 | 42.6 min |
| 32072887176 | `ab23879` | 08-17 21:47 | 15 | 975,754 | 252 | 196,968 | 39.2 min |
| 32081654941 | `ab23879` | 08-17 23:43 | 15 | 971,745 | 244 | 189,923 | 41.8 min |
| 32092211524 | `ab23879` | 08-18 02:32 | 15 | 1,002,373 | 332 | 246,757 | 37.1 min |
| 32097777827 | `ab23879` | 08-18 04:04 | 15 | 958,010 | 387 | 182,828 | 39.5 min |
| 32104563590 | `f7415c8` | 08-18 05:52 | 15 | 1,289,283 | 330 | 417,499 | 50.0 min |
| 32114156695 | `f7415c8` | 08-18 08:00 | 15 | 1,261,562 | 425 | 412,688 | **60.0 min (budget)** |
| 32124170195 | `66e6c11` | 08-18 09:55 | 15 | 1,196,090 | 420 | 343,266 | **60.0 min (budget)** |

Nine of ten reported success. The one failure was GitHub infrastructure, not our code (§10).
3,194 fatal board errors and 2,617,837 retries total.

**The dominant fact of this window is #161 (`f7415c8`, 08-18 ~05:00Z).** Restoring Workday and
Personio priority scoring lifted the corpus from ~198k to ~242k Jobs and scraped volume from ~975k
to ~1,250k jobs per run. It did what it intended. It also consumed all remaining scrape headroom
and roughly doubled rate-limit pressure. Most findings below are either that consequence or were
unmasked by it, so the pre/post split is given per finding rather than assumed.

---

## 1. Scrape shards now hit the 60-minute budget ceiling — headroom is gone

Slowest-shard wall clock, by commit:

| | Pre-#161 (7 runs) | Post-#161 (3 runs) |
|---|---|---|
| Slowest shard | 2,224–2,555 s (37–43 min) | 2,998 / 3,599 / 3,599 s |
| Mean shard | 993–1,136 s | 1,543–1,833 s |

`3599 s` is the 60-minute budget: the shard was killed, not finished. Two of the last three runs
ended that way, and the run before this window did too.

So far the loss is trivial — `time budget reached ... 1340/1341 boards done, 1 deferred` — because
the kill lands as the shard finishes anyway. That is exactly what makes this the top finding: the
margin is now under one board, the failure is **silent by design** (a banked partial fragment is a
supported outcome, and the run stays green), and the next increment of volume or ATS slowness comes
straight out of coverage with no red signal anywhere.

**Ranked first** because it is a cliff rather than a cost, and we are standing on it.

## 2. Workday 429s dominate everything and are still escalating

Retries across all ten runs, by class:

| Class | Retries | Share |
|---|---|---|
| `429-ratelimit` | 1,988,686 | 76.0% |
| `405-wall` | 253,338 | 9.7% |
| `403-wall` | 235,378 | 9.0% |
| `5xx` | 133,479 | 5.1% |
| `network` | 6,956 | 0.3% |

Per run that is ~262,000 retries against 20,000 boards — about 13 per board. Post-#161 the 429
class alone runs 273k–345k per run against 126k–190k before.

Boards lost outright to Workday 429 (the scraper exhausted `_ATTEMPTS = 3` and raised):

```
pre-#161 :  124  126  143  121  118  184  151
post-#161:  191  282  296          <- monotonic across all three runs
```

1,736 of the window's 3,194 fatal board errors are Workday 429s — 54% of all board-level failure,
one ATS, one status. Each is a whole board contributing nothing that run.

**Not yet measured, and it matters:** whether 429 backoff is what consumes the shard budget in §1.
The mechanism is plausible — `_note_retry` sleeps `Retry-After` (capped 30 s) or `1.5*(attempt+1)`
— but per-shard effective concurrency is `workers + workers*8` (`harvest._default_workers`), so the
aggregate stall could divide down to minutes or up to most of the budget. Do not act on the
inference; instrument the sleep total per shard and read it. Per CLAUDE.md, measure it.

## 3. Eightfold: the relative per-ATS cap let #161's volume growth blow the origin budget

**Diagnosed 2026-08-18 (this document's own §3 originally mis-attributed it — corrected here).**

Fatal eightfold errors by run: `0 0 0 0 0 0 0 | 30 27 18`. The onset is exactly at `f7415c8`, but
**#161 never touched `eightfold.py` or `http.py`** — it changed `board_priority.py` and six lines of
`scrape_run.py`. The scraper is unchanged. What changed is how many eightfold Boards land in a run:

| head | distinct EF Boards/run | per-shard spread | fatals | wall rate |
|---|---|---|---|---|
| `ab23879` (4 sampled) | 54–61 | `[5,5,5,5,5,5,4,4,4,4,3,2,2,1,1]` | **0** | 0% |
| `f7415c8`+ (3 runs) | 73–79 | `[6,6,6,6,6,6,6,5,5,5,5,5,4,1,1]` | 18–30 | 25–38% |

Pre-#161 one or two shards reach the cap of 6; post-#161 seven to nine do.

**The failure is per-shard, and it scales with that shard's own eightfold load** — which is what
identifies the mechanism rather than merely correlating with it:

| EF Boards on a shard | shards observed | fatals per shard |
|---|---|---|
| 6 | 23 | 2.09 |
| 5 | 15 | 1.73 |
| 4 | 2 | 0.50 |
| 1 | 5 | **0.00** |

Every shard holding a single eightfold Board finished clean. That is ADR-0047's premise confirmed:
parallel Actions shards get distinct egress IPs, so each shard spends its **own** per-origin budget,
and roughly four Boards is what one budget affords in the startup window.

**Root cause.** `binpack.lpt_pack_capped` caps one ATS at `ceil(n/m)` per shard — a *relative* cap.
It equalises distribution but places **no absolute ceiling on per-shard load**, so when #161 grew
`n` from ~55 to ~79 the cap passed the growth straight through to every origin budget. The guard
that ADR-0047 added to protect the budget silently scaled past it.

**Two falsified hypotheses, recorded so they are not retried.** (a) *Tenant is blocked*: all 18
hosts that failed in run `32124170195` answer 200 from a low-volume local client — 8 of them do
serve `403` on `/api/pcsx/search`, but that is the designed API-disabled case and their sitemap
fallback answers 200, so nothing about the tenant explains a fatal. (b) *Sustained per-origin
meter*: every eightfold fatal lands 2–9 minutes into a shard with none after 10 minutes — but so
does every eightfold **success** (45–49 of ~50 per run in the first 10 minutes), because the
cost-descending pack schedules these large Boards first. Timing therefore reflects scheduling, not
accumulation.

**The status flips 403↔405 for the same host across runs** (kering 403/403/405, libertymutual
405/405/403, starbucks 403/405/405), which is why neither status should be read as a tenant
property — both are the same edge wall.

Separately and still open, 16–24 Boards per run are excluded from the eviction scope as
non-authoritative, and the newest runs name the reason per Board:

```
scope-excluded Board: eightfold:caci.eightfold.ai — HTTP 405 on page 209 — got 1590 of 1709 postings
scope-excluded Board: eightfold:jobs.nvidia.com  — HTTP 405 on page 392 — got 2507 of 2629 postings
scope-excluded Board: eightfold:citi.eightfold.ai — still short after 3 sweeps — got 3352 of 3377
```

Two distinct causes there — the 405 wall interrupting deep pagination, and sweeps that converge
short — wanting different fixes. This is #150 / #145 / #157, now with per-Board evidence.

**No fast local loop exists for this bug.** It only reproduces at CI request volume from CI egress
IPs; a local client at polite volume cannot turn it red. The working signal is the differential one
used above — eightfold fatals and per-shard load, run over run — which means the verification cycle
is a 2-hourly pipeline run, not a test.

## 4. The cost model flipped sign, and the budget warning went silent with it

The 2026-08-13 review found the planner predicting ~111.7 min against a 30–50 min actual
(`actual/predicted` 0.06–0.44×) and warning about the budget every single run — pure noise.

It now predicts **44.0 min** against actuals reaching 60. Per-shard `actual/predicted` maxima
across this window: 4.06, 2.33, 1.38, 2.50, 1.59, 1.83, 1.25, 2.50, 2.84, 1.76 — the model now
**under**-predicts, by up to 4×.

The consequence is worse than the old noise. `predicted makespan ~44.0 min` no longer exceeds the
60-minute budget, so the warning that used to fire every run has stopped firing — precisely in the
window where shards actually started hitting the ceiling. A guard rail that was useless is now
actively misleading, and §1 is invisible to it.

## 5. Dead boards are re-scraped every run; the quarantine ledger is too young to have fired

404/410 fatals by ATS across the window: greenhouse 395, ashby 180, teamtailor 57, trakstar 36,
personio 33, recruitee 16, workday 2, rippling 1.

Several specific Boards 404 in **all ten** runs: `greenhouse:fireworksai`, `greenhouse:matx`,
`greenhouse:dmcengineering2024`, `trakstar:roroinc`, `freshteam:recro-team`,
`smartrecruiters:AveryDennisonSB`, `successfactors:careers.ltimindtree.com`.

`update_ledgers failures` exists to close exactly this loop, but it only shipped with #161, so it
has run three times:

```
32104563590: 75 gone | 75 ledger rows  | 0 cleared | 0 at/over 5 strikes
32114156695: 80 gone | 134 ledger rows | 0 cleared | 0 at/over 5 strikes
32124170195: 74 gone | 178 ledger rows | 0 cleared | 0 at/over 5 strikes
```

`QUARANTINE_AT = 5` consecutive gone-runs, so `0 at/over 5 strikes` is a young ledger behaving
correctly, **not** a defect — worth stating plainly because the line reads like a failure. A Board
404ing every run since 32104563590 should first quarantine on the fifth ledger run; the in-flight
`32133497258` is the fourth. **That is a falsifiable prediction — check it on the next run.**

Two things to watch rather than fix: `0 cleared by a successful scrape` in three runs, and rows
growing ~50/run with nothing leaving. If neither changes by run 5, the accumulation is worth a
second look.

## 6. Personio — much improved by #161, not yet zero

`ParseError` 239 and `HTTPError 429` 103 across the window. But the split is the story: 29–50
combined failures per run pre-#161, then **10, 14, 15**. The ledger repair in #161 did most of the
work the 2026-08-13 review asked for ("throws 1–4 ParseErrors in every shard of every run").
Residual is steady but small.

## 7. 403/405-wall retries are 18.7% of all retry spend and probably unwinnable

488,716 retries across the window (~49k/run) are spent on 403 and 405 walls. Both are ordinarily
permanent for the request as formed, so three attempts mostly buys three times the wall.

Deliberately **not** proposing a change: `http._retry_reason` keeps 405 separate from 403 precisely
because Eightfold's edge returns it, and it is plausible some fraction succeeds on retry. The
success rate per class is not currently counted. Count it first, then decide — the same discipline
CLAUDE.md's live-API rule demands, since this is a claim about what real hosts return.

## 8. SuccessFactors TLS and timeouts — steady, low

`CertificateVerifyError` 59, `Timeout` 43, `DNSError` 3 across ten runs (roughly 6–24 per run,
concentrated in a handful of tenants). Unchanged in character from the previous review. Low
priority, but it is the only ATS whose failures are transport-level rather than status-level.

## 9. The embedding store is 1.7× the table it serves, and still unpruned

`store: 483,668 embedded Jobs` against `table now holds 285,678 rows`, growing monotonically
478,508 → 483,668 across the window (~516/run). Nothing prunes it; §8 of the previous review
stands.

Partly offset: `state_fetch` now pulls **35 files in 207 s**, against 1,094–1,624 files and up to
667 s before — compaction clearly landed and this is no longer a top cost. Still present: five
`unauthenticated requests to the HF Hub` warnings per run, so the download remains rate-limited.

Jobs without vectors climbed 11,751 → 14,435 (non-English by design, per ADR — worth reconfirming
the split still reconciles exactly as it did in the previous review's §9).

## 10. One run in ten lost 20% of its slice to GitHub infrastructure

`32043354923` is the window's only red run, and it is not ours:

```
##[error]Failed to download archive 'https://codeload.github.com/actions/download-artifact/...'
         after 3 attempts.
##[error]Response status code does not indicate success: 429 (Too Many Requests).
##[error]Response status code does not indicate success: 502 (Bad Gateway).
```

Three shards never started; the run scraped 15,998 of 20,000 boards. Nothing to fix in our code,
but it is the second 429 story in this document, and it means one run in ten silently ran at 80%
coverage.

## 11. Workday case-duplicate Boards are still scraped twice

Unfixed since the 2026-08-13 review (§3). The priority ledger's top ten in the newest run still
carries both halves of the pair:

```
1935.1  workday:nvidia/NVIDIAExternalCareerSite   (1936 tech jobs)
1880.2  workday:nvidia/nvidiaexternalcareersite   (1880 tech jobs)
```

This is no longer merely wasteful. The duplicated fetches land on the ATS that is rate-limiting us
hardest (§2) and consume the budget that has just run out (§1), so the cheapest available relief
for the top two findings is to stop scraping these Boards twice.

---

## What improved since 2026-08-13

Recorded so the next review does not re-raise them:

- **Retries are attributed.** §6 of the previous review ("enormous and unattributed") is fixed —
  every shard now reports `403-wall / 405-wall / 429-ratelimit / 5xx / network`. That attribution
  is what made §2 and §7 above measurable at all.
- **Index flapping is largely gone.** Table rows moved 285,164 → 285,678 across ten runs with
  per-run churn of add 380–3,469 / evict 310–1,068, against the previous window's non-converging
  add 1,519 / evict 3,268 oscillation between 280,595 and 283,185.
- **Truncation is diagnosed per Board**, with the reason and the shortfall, rather than a bare count.
- **`state_fetch` is no longer a top cost** (35 files / 207 s).
- **Personio** failures fell ~70% (§6).
- **Darwinbox** posted 10 fatals across ten runs, against "8–16 per shard every run" previously.

## Suggested order of work

1. Instrument per-shard retry-sleep totals — it is the single measurement that decides whether §1
   is fixed by rate-limit policy (§2) or by slice size (§4).
2. Deduplicate the Workday case-variant Boards (§11) — cheapest relief for §1 and §2, and already
   diagnosed.
3. Recalibrate the cost model and restore a budget warning that fires on the real ceiling (§4).
4. Confirm the quarantine prediction in §5 on the next run before touching the ledger.
5. Eightfold 403/405 (§3) — needs a live-API probe, not a code read.
