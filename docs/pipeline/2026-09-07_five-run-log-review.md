# Five-run log review — 2026-09-07

Runs reviewed (all `nightly-pipeline`, all green, chronological). A sixth run in the window,
`34085443006`, was **cancelled with zero jobs** — a delayed `schedule` event arriving while the
chained run was in flight, cancelled by the concurrency group's one-pending-run rule. That is
`pipeline.yml`'s documented behaviour (ADR-0093), not a failure, and it is excluded below.

| Run | Started (UTC) | Wall | scrape max | Board errors | Retries | actual/predicted |
|---|---|---|---|---|---|---|
| 34074802564 | 02:00 | 57 min | 28.9 min | 26 | 265,536 | 0.73–1.34× |
| 34077959184 | 02:56 | 59 min | 29.9 min | 23 | 249,962 | 0.81–1.26× |
| 34081285305 | 03:55 | 56 min | 29.3 min | 25 | 238,396 | 0.69–1.34× |
| 34084670953 | 04:51 | 58 min | 30.1 min | 19 | 259,303 | 0.82–1.22× |
| 34088295600 | 05:49 | 57 min | 27.8 min | 40 | 257,281 | 0.81–1.26× |

**Not one `##[error]` line in any of the five runs, and every job concluded `success` or
`skipped`.** Everything below came out of `##[warning]` lines and info-level logs. That is the
point of reading them.

Health that needs no further comment: `embed` ran 0 failures on every shard; `index prune` evicted
0 rows every run; `state_fetch` never retried; no shard hit its time budget; no board was
*discarded* for reading too little; darwinbox never escalated to a browser; 0/15 shards lost their
spare egress by the `fanout_retries.py` ratio test; the `actual/predicted` yardstick sat at a median
of 0.99–1.02× — the cost model is honest.

---

## 1. The critical path is 102 SuccessFactors boards that return zero jobs

**The single biggest finding, on both wall-clock and data-quality axes.**

`fanout_timing.py` names each shard's floor board. Across the five runs' 75 shards, **69 of the 75
floor boards are SuccessFactors** (5 zwayam, 1 eightfold), and `scrape_plan` predicted the shape in
advance every run:

```
predicted: 20000 boards / 15 shards, makespan ~28.0 min ... single-board floor 28.0 min
```

The predicted makespan *equals* the single-board floor. The scrape stage — 49–52% of the run's wall
clock — is set entirely by its largest board, and no packer can touch it.

Then, from run `34088295600`:

```
05:51:38 [successfactors] careers.te.com: sitemap-urlset via sitemap urlset -> 2127 job pages to fetch
06:18:32 [successfactors] successfactors:careers.te.com: 2127/2127 detail fields missing
06:18:32 [scrape_run] slow board successfactors:careers.te.com: 0 jobs in 1614s
```

**The board that owns the pipeline's critical path fetches 2,127 pages, 27 minutes, and produces
nothing.** It is not alone.

### Scale

Counting `slow board {key}: {n} jobs in {s}s` (emitted at `_SLOW_BOARD_S = 120s`):

| Run | slow-board seconds | of which **zero-yield** | boards |
|---|---|---|---|
| 34074802564 | 94,118 | 43,142 (46%) | 121 |
| 34077959184 | 91,560 | 43,711 (48%) | 125 |
| 34081285305 | 86,795 | 41,708 (48%) | 121 |
| 34084670953 | 90,015 | 43,995 (49%) | 128 |
| 34088295600 | 93,227 | 45,104 (48%) | 127 |

**Roughly half of all straggler time is spent on boards that return zero jobs.**

**102 boards returned zero jobs in all five consecutive runs. Every one is SuccessFactors.** They
cost **631 board-minutes per run** against the cost ledger's own Σ of 8,646 board-minutes — **7.3%
of all scrape work, producing nothing**, every run, for at least five runs running.

Worst offenders (mean seconds per run, zero jobs in 5/5):

| Board | s/run | pages listed |
|---|---|---|
| `successfactors:careers.te.com` | 1,665 | 2,127 |
| `successfactors:jobs.l3harris.com` | 1,559 | 1,997 |
| `successfactors:southasiacareers.deloitte.com` | 1,453 | 1,705 |
| `successfactors:careers-inc.nttdata.com` | 1,077 | 1,399 |
| `successfactors:jobs.scotiabank.com` | 958 | 1,232 |
| `successfactors:corningjobs.corning.com` | 944 | 1,212 |

In run `34088295600` the 127 zero-yield boards between them listed **56,120 postings and ingested
none of them**. The served table holds 335,545 rows; at SuccessFactors' measured 13.2% tech keep
rate that is on the order of **7,400 tech jobs missing from the product**, every run.

The liveness ledger says these boards are alive and populated
(`careers.te.com,...,live,1379`, `jobs.l3harris.com,...,live,1294`), and the sitemap hands the
scraper 2,127 URLs. This is not an empty board.

### It is a fetch problem, not a parse problem — verified

The obvious theory was that these tenants render a CSB variant the field extraction cannot read.
**Measured, that theory is wrong.** Fetching a live `careers.te.com` job page and a live
`jobs.l3harris.com` one from a laptop and running the repo's own `successfactors._titled_fields`
over them:

```
te.com   : http=200 size=93,038 time=2.6s -> dict(title='Werkstudent im Bereich Operations Finance (m/w/d)',
                                                  description=<present>, location=None, posted_at=None)
l3harris : http=200 size=50,097 time=1.9s -> dict(title='Deputy Customer Success Manager, Program Digital Cockpit',
                                                  description=<present>, location='Melbourne, FL, US, 32919',
                                                  posted_at='2026-08-12')
```

**The parser builds a usable Job from both pages.** (Neither page carries JSON-LD or `<meta
itemprop>` microdata — only `joblayouttoken` spans, German ones on te.com — but the parser does not
need them, which is exactly why reading the markup instead of running the code would have produced
the wrong answer.)

> **RESOLVED — and the hypothesis this section originally reached for was wrong.**
> **See [`docs/successfactors/2026-09-07_user-agent-denylist.md`](../successfactors/2026-09-07_user-agent-denylist.md).**
>
> What this section argued next, on the evidence above, was that the pages CI receives cannot be
> the pages the open internet serves — and therefore that SAP's CSB host walls the Actions runner
> IP range, `successfactors` being one of the ATSes with **no spare-egress fallback**
> (`egress_fallback_on` is set only on `eightfold`, `workday` and `workable`). It followed that
> settling it needed a `workflow_dispatch` probe from inside Actions.
>
> That reasoning had a hole: the plain `curl` above and *the scraper* do not send the same request.
> The real cause is a SuccessFactors edge policy denylisting the **exact literal**
> `headstart/0.1 (job-board reader)` — `(job-board)`, `(reader)`, `curl/8.7.1` and
> `python-requests/2.32.3` are all served on the same URL. The vantage was never the variable, and
> the bug reproduced on a laptop in about thirty seconds once a harness ran the real code path.
>
> Both dead theories are kept above rather than edited away, because the failure mode they share is the
> point: each was plausible, each was reasoned from real evidence, and each cost more than the
> measurement that killed it would have.

### Timing: this is recent

The priority ledger's `updated_at` is the last date a board appeared in a scrape snapshot:

| Board | cost ledger | priority ledger |
|---|---|---|
| `careers.te.com` | `1631s, jobs=0, 2026-09-07` | `score=171.7, last_tech_jobs=1, 2026-09-04` |
| `jobs.l3harris.com` | `1547s, jobs=0, 2026-09-07` | `score=218.1, last_tech_jobs=1, 2026-09-04` |
| `corningjobs.corning.com` | `940s, jobs=0, 2026-09-07` | `score=396.8, last_tech_jobs=397, 2026-09-03` |
| `jobs.scotiabank.com` | `960s, jobs=0, 2026-09-07` | `score=200.3, last_tech_jobs=200, 2026-09-03` |

These boards last yielded on **2026-09-03/04**. No commit touched `successfactors.py` on 09-04 or
09-05 (`57c1183` is 09-06, `5082406` is 09-07), so nothing on our side changed when they went
silent — consistent with the denylist entry having been added at the origin around then.

---

## 2. The ADR-0064 value gate is structurally blind to exactly this failure

The gate exists to stop a giant low-yield board owning a shard's makespan. It should have caught
`careers.te.com` on its first zero-yield run. It has not caught it in five.

`scrape_plan._gated_boards` computes:

```python
tech_per_min = scores.get(key, 0.0) / (row.seconds / 60)
if tech_per_min < _GATE_MIN_TECH_PER_MIN:   # 2.0
```

For `careers.te.com` that is `171.7074 / 27.18 = 6.32/min` — comfortably above the threshold — while
the board's actual current yield is **zero**.

**The numerator and the denominator are on different clocks, and the failure itself is what stops
them converging.** `board_priority.update`'s docstring states the rule:

> boards absent from the snapshot carry their row unchanged — a partial harvest must not decay what
> it didn't look at.

A board that is scraped and yields nothing contributes no snapshot row, so it is indistinguishable
at that layer from a board that was never scraped — and its score is carried, undecayed, forever.
The ledger dates above are the proof: the cost row is rewritten every run (`2026-09-07`) while the
priority row has not moved for three or four days.

So **the very collapse the gate is meant to catch is what makes the gate unable to see it.** A board
whose yield goes to zero keeps its last good score indefinitely and can never be gated.

Across the whole 88,225-row cost ledger, 83 boards cost over 15 min; 15 of those have `jobs=0`;
the gate correctly gates 9 of them and **keeps 6** (127 board-minutes per full pass) — and those 6
are the ones that set the makespan.

**Fix shape** (small, and it fits the gate's existing philosophy of judging a Board on its own
measurement): the gate already reads `BoardCost.jobs`. Judge on that, or on
`min(score, k · last_measured_jobs)`, so a measured zero cannot be outvoted by a remembered
non-zero. Alternatively, let `update_ledgers priority` distinguish "scraped, yielded nothing" from
"not scraped" and decay the former. The second is the more honest fix; the first is one line.

---

## 3. Every error in the window, by class

Board errors were 19–26 per run (0.1% of 20,000 attempted) in four runs and 40 (0.2%) in the fifth.

### 3a. Trakstar: a ~5-minute host outage cost 20 boards and 64% of its volume

Run `34088295600` alone: **20 `Timeout` board errors, all trakstar**, against zero in the other four
runs. Every failure is `curl (28) Operation timed out after 30,002 ms with 0 bytes received`, and
they land in one tight window across **12 different shards**:

```
05:58:46 happyfox     06:00:49 easebuzz     06:01:35 twonice      06:02:07 topclosers
05:58:54 loginext     06:00:50 managementapps 06:01:46 apsbank    06:02:09 tenthpin
05:59:13 m800         06:01:11 rivian       06:01:49 sensedia
06:00:01 srijantech   06:01:19 whatfix101   06:01:55 starfish
06:00:02 exotel       06:01:22 gulfstreamsp 06:01:57 webengage
06:00:24 leamseducation 06:01:24 rowepsc
```

Each failure represents ~95 s of retrying, so the outage window is roughly 05:57–06:02:30.
Simultaneity across shards rules out per-shard egress. `trakstar:futurerecruit` logged
`25 cards, capped, and the RSS feed is unreachable` at 06:02:26 in the same window.

Trakstar's scraped volume: 5,414 → 5,688 → 5,410 → 5,467 → **1,943** (−64%), with `kept%` jumping
21% → 41.4% — the mix shift a partial scrape produces.

**Live re-check now: `happyfox`, `exotel`, `loginext`, `futurerecruit` all return 200 in 2.2–9.6 s.**
Transient, host-side, since recovered. ADR-0053 correctly excluded the failed boards from eviction
scope, so nothing was wrongly delisted.

The cost that *is* ours: 20 boards × ~95 s ≈ **1,900 board-seconds spent retrying into a host that
was down**, because each board retries independently. A per-host circuit breaker — after N
consecutive timeouts on `*.hire.trakstar.com`, stop trying for the rest of the run — would have cost
~300 s instead. `loginext`'s feed takes 9.6 s on a good day against a 30 s timeout, so the margin
here is thin by design.

### 3b. SuccessFactors TLS: four permanently broken boards that can never be struck off

Every run, 2–4 `CertificateVerifyError`s, always SuccessFactors, always the same kind of board:

```
successfactors:karriere-solingen.de     certificate has expired
successfactors:careers.shapoorji.com    certificate has expired
successfactors:jobs.kkg.ch              no alternative certificate subject name matches 'jobs.kkg.ch'
successfactors:jobs.newway.eu           unable to get local issuer certificate
```

These are genuinely, durably broken — an expired cert does not heal on retry. And
`update_ledgers failures` reports them under **`error(s) did not read as gone`** every single run, so
they never accrue a gone-strike and never quarantine. They will be retried forever, cheaply (~6 s
each) but pointlessly. This is the shape CLAUDE.md's skill notes call out: *a class sitting in the
not-gone bucket run after run is the signal.*

`careers.shapoorji.com` is a real Indian employer, so this is lost coverage, not just noise.

### 3c. Workday mid-crawl page losses — much improved, worth recording

6–12 boards per run lose 1–5 pages mid-crawl, split between `ConnectionError` and `HTTP 500`. Every
one ends `— Board unauthoritative this run`; **none** ended `— too little of T listed read to keep`,
so no board was discarded.

For context against the earlier investigation that measured **105–170 ConnectionError pages and
28–52 boards per run**: this window runs **6–12 boards and roughly 8–20 pages**. Whatever landed
between then and now worked. Recording the number so the next reader has a baseline.

Separately, ADR-0100's break-off fired five times across five runs
(`breaking off the detail pass after 30 consecutive details lost to settled 5xx`) — working as
designed.

### 3d. Silent ceilings: 8 boards permanently truncated, every run

| Ceiling | Boards hitting it in **all five** runs | Also seen |
|---|---|---|
| zoho ~750-record widget | `tmievan`, `overturerede`, `biztekpeople`, `agileengine`, `2coms` | 3 more, once each |
| freshteam 1000-job widget | `simera-talent`, `abnhire` | `kalam`, once |

Neither calls `mark_truncated` — the widget exposes no true total to compare against — so everything
past the ceiling is invisible in both directions: not scraped, and not flagged as unscraped.

### 3e. Eightfold

15–17 boards per run fall back to the sitemap (`the PCSX API did not answer`) — steady, not a trend.
Sweep convergence needed a second or third pass on only 1–2 boards per run, so `_MAX_SWEEPS = 3` is
not being stressed.

---

## 4. Where the remaining wall clock is, and the free throughput nobody has taken

`scrape` owns 27.8–30.1 min of a 56–59 min wall in every run; `join` 11.6–12.8; `merge` 7.8–11.8;
`embed` 3.6–6.7. Queueing and setup is **0.3 min** — infrastructure is not the problem.

The pipeline measures its own concurrency headroom every run and says the same thing every time.
Over all 75 shard-runs:

| Surface | n | throughput median | latency median | verdicts |
|---|---|---|---|---|
| **workday pages** | 75 | **2.18×** | **1.00×** | **74× room to widen**, 1× narrowing is free |
| workday details | 75 | 1.24× | 1.80× | 52× widen, 12× narrow, 11× mixed |
| eightfold details | 73 | 1.22× | 1.70× | 42× widen, **27× narrowing is free**, 4× mixed |

**Workday's listing-page concurrency buys 2.18× throughput for 2.1× width at no latency cost, on 74
of 75 shard-runs across five runs.** That is near-linear scaling and it is free. Widening it is the
clearest unclaimed win in the pipeline, and Workday is 37% of all scrape volume (523k of 1.41M
lines). Eightfold's detail concurrency is the mirror image — over-wide on a third of shards, where
narrowing costs nothing and returns capacity.

Eightfold is also the most expensive ATS per board by a wide margin: **40.4 s median** against
workday 9.2 s and successfactors 11.1 s.

---

## 5. Corpus and index health

**Volume is flat and healthy.** 1.41–1.42 M lines scraped per run, 299,060–299,346 through the tech
gate (21.0–21.2% keep), served table 335,425 → 335,545 rows across the five runs.

**Embedding has saturated.** 57 / 66 / 91 / 117 / 212 new Docs per run against 275,660–275,974
already embedded. `embed` is no longer the pipeline's dominant stage — worth noting, since that
contradicts the older mental model.

**ADR-0083's grace period is barely rescuing anything.** Of 1,701 ids carried in across the five
runs, **33 reappeared — 1.9%** (17, 0, 9, 4, 3). Meanwhile a stable ~172–181 per run are
`unconfirmed again`. The mechanism is doing what it says, but as a *rescue* it is close to a no-op
at this hit rate; it mostly delays an eviction by one run. Worth measuring over a longer window
before changing anything — five runs is an anecdote.

**ADR-0053 scope exclusion is stable, not accreting, in this window.** 2,433 → 2,321 → 2,394 →
2,351 → 2,358 rows across 35–41 boards. Two boards dominate permanently and both creep upward:
`successfactors:careers.hcltech.com` 1,315 → 1,323 and `successfactors:careers.wipro.com` 663 → 670.
Slow, monotonic, and with no drain — the number to keep watching across weeks, not runs.

**Non-tech creep is steady at 20.6%** — 69,247 → 69,282 of the served rows (issue #186). Unchanged
across the window.

**The description store's unreachable stock is stable, not growing.** 126,252 → 128,940 → 128,732 →
127,971 → **128,591** — it oscillates around ~128k rather than climbing, which is worth stating
because the shape invites the opposite reading. That is 20% of the 635,488 stored ids that can never
be filled. `unsettled` likewise oscillates (44,641 → 41,950 → 42,156 → 42,915 → 42,285), and ~3,200
Jobs per run carry no description and no stored answer.

**A stale duplicate ledger key, already self-healing.** Run `34074802564` listed both
`workday:nvidia/NVIDIAExternalCareerSite` and `workday:nvidia/nvidiaexternalcareersite` in its
priority top-10, and the latter held 2,832 unsettled description ids. Checked against
`load_active_companies`: the three `live` ledger rows for NVIDIA collapse to **one** Board,
`workday:nvidia/NVIDIAExternalCareerSite`. So this is a stale casing row in the *ledgers*, not a
double scrape — and it had dropped out of both lists by the newest run. No action needed; recorded
so the next reader does not re-raise it.

**Eightfold sandbox boards are not being scraped.** `amdocs-sandbox`, `microsoft-tm2-dev-sandbox`,
`citigroup-qa-sandbox` and `nvidia-sandbox` sit high in the description-gap top-10, which looks like
vendor boards in the corpus. Checked: **every sandbox row in `eightfold.csv` is `dead`.** Their
backlog is stale and correctly classed unreachable. Also no action; also recorded so it is not
re-raised.

---

## 6. Observability: the warning stream is 98% noise

Across all five runs, ~23,700 `##[warning]` lines. **23,213 of them — 98% — are spare-egress
rotation chatter**, four lines per rotation:

```
spare egress: {board} walled the current IP — rotating
spare egress: rotating egress IP
spare egress: rotated to a fresh egress IP
spare egress: now egressing from {addr} via {pop}
```

at 1,007–1,411 rotations per run. Every one is an Actions annotation. The per-run summary lines
(`spare egress rotations: ...`, `rotation demand by board: ...`) already carry the same information
in aggregate, and `fanout_retries.py` deliberately measures egress health from the 429/network
*ratio* rather than these strings.

Demoting the four per-rotation lines to info would leave the roughly 400 warnings that actually
mean something — the mid-crawl losses, the ceilings, the board errors, the scope exclusions —
visible in the Actions UI instead of buried.

---

## Ranked scope for improvement

| # | Change | Evidence | Payoff |
|---|---|---|---|
| 1 | ~~Diagnose the SuccessFactors zero-yield class, then fix~~ **DONE** — one denylisted User-Agent literal, not an egress wall ([writeup](../successfactors/2026-09-07_user-agent-denylist.md)) | §1 | ~56,120 postings/run unblocked; te.com's detail pass measured **4.1x faster**, not slower |
| 2 | Make the ADR-0064 gate read measured `jobs`, not a carried score | §2 | Would have caught this in one run; ~127 board-min/run and the critical path |
| 3 | Widen `workday` listing-page concurrency | §4 — 74/75 shard-runs, 2.18× for free | Directly cuts the largest stage's Σ work |
| 4 | Per-host circuit breaker on repeated timeouts | §3a | ~1,600 board-s saved in one outage; bounds any future one |
| 5 | Let a durable `CertificateVerifyError` count as a gone-strike | §3b | Stops 4 boards retrying forever; surfaces real lost coverage |
| 6 | Demote spare-egress rotation lines to info | §6 | Makes 400 real warnings visible instead of 23,700 |
| 7 | Narrow `eightfold` detail concurrency | §4 — 27/73 say narrowing is free | Returns capacity on the most expensive ATS per board |
| 8 | Paginate past the zoho 750 / freshteam 1000 ceilings, or `mark_truncated` | §3d | 8 boards permanently and silently short |

Items 1 and 2 are the same incident seen from two sides: one is why the pipeline is losing 56,000
postings a run, the other is why nothing noticed.
