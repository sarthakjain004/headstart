# Five-run log review — runs 35175065218 … 35188643520 (2026-09-17)

All five runs are `success`, all on the **same SHA `02d6afb`**, spanning 02:36–07:09 UTC. One SHA
across the whole window means there is no code confound: every delta below is volume, host
behaviour, or an accreting stock, never a code change.

| run | wall | scrape max | join | embed max | merge | owner |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 35175065218 | 56.5m | 31.9m | 11.4m | 5.2m | 6.8m | scrape (56%) |
| 35178585354 | 49.3m | 27.4m | 9.4m | 4.1m | 7.4m | scrape (56%) |
| 35181676606 | 49.5m | 26.6m | 10.0m | 4.5m | 7.4m | scrape (54%) |
| 35184790053 | 57.9m | 33.5m | 11.4m | 3.8m | 8.2m | scrape (58%) |
| 35188643520 | 59.1m | 33.4m | 11.5m | 3.8m | 9.4m | scrape (57%) |

`scrape` owns the critical path in every run. Everything else is noise by comparison: `embed` never
exceeds 5.2m and `merge` never 9.4m.

## 1. The scrape critical path is one Board, every run

In all five runs the slowest shard is **96–98% a single Board**. This is a floor, not a packing
failure — `wall = max(Σ work ÷ concurrency, slowest single item)` — and `scrape_plan` *predicts it
in advance* every run (single-board floor 20.2 / 25.3 / 24.2 / 25.0 / 27.0 min against a 12.3–13.3
min even share). The planner is right; the question is whether these Boards deserve the slot.

Two Boards account for it:

- **`apple:jobs.apple.com`** — 1085–2007s (18–33 min). Critical-path owner in 3 of 5 runs. Its
  cost-ledger median is *rising* across the window: 1520 → 1278 → 1275 → 1622 → 1793s. It yields
  ~4,378 tech jobs from ~6,180 scraped.
- **`oracle:ejwl.fa.us2.oraclecloud.com`** — 1446–1668s (24–28 min), a top-two shard in every run.

Arithmetic: dropping the scrape max from ~33.4m to the ~18m third-place shard would take the wall
from 59.1m to roughly 44m, a **~25% cut**. That is a projection from the floor table, not a
measurement of a change.

Note `docs/oracle/2026-09-16_are-the-ejwl-and-jpmc-pods-real.md` already measured and **refuted**
the "ejwl/jpmc are fake demo tenants" theory (0.0% / 3.0% test-marked titles over 300 samples each).
Do not re-open that argument.

> **Already actioned — `ejwl` only.** `main` commit `c5984f38` (2026-09-17) parks
> `oracle:ejwl.fa.us2.oraclecloud.com` via `config.py`, on its own measurement: run-owning in 8 of 8
> sampled runs, and **not fetch-volume-bound** — a direct connection clears its workload in ~9 min,
> so the cost is specific to the WARP-routed path. That is a sharper diagnosis than the one below
> and supersedes it for `ejwl`. **`apple:jobs.apple.com` is untouched and is now the sole remaining
> floor Board**, so the wall-clock projection above should be re-measured against a post-`c5984f38`
> run before it is spent.

## 2. Oracle dev/test pods: `TotalJobsCount` is fiction, and it poisons the eviction scope

**Measured live, 6 pods, 2026-09-17.** These pods state a total far larger than they will serve,
and return sparse non-contiguous pages:

| pod | stated `TotalJobsCount` | actually walked | share |
| --- | ---: | ---: | ---: |
| `eofd-dev10.fa.us6` | 2,960 | 35 | 1.2% |
| `eofe-dev3.fa.us2` | 806 | 49 | 6.1% |
| `edca-test.fa.us2` | 373 | 43 | 11.5% |
| `ejwl.fa.us2` (control, real) | 13,713 | 2,400 in 12 dense pages of 200 | contiguous |

`eofd-dev10` returns 1 requisition at offset 0, 10 at offset 200, 11 at offset 1400, then nothing.
The real `ejwl` pod paginates densely at 200/page — the two shapes split cleanly.

The consequence is not the wasted fetch, it is downstream: the pipeline compares what it read
against that fabricated total, concludes the scrape was non-authoritative, and drops the Board into
the **ADR-0053 eviction scope exclusion — which has no drain**. 25 Boards are excluded on all five
runs, the overwhelming majority of them Oracle `-dev`/`-test` pods. They can never re-enter scope,
because the total they are judged against will never be reachable.

A second, genuinely structural cap sits alongside it: Oracle serves **no offset past 10,000**, so
`egud` (10,000 of 12,011), `eluq-dev19` (10,000 of 14,635), `ejwl` (9,926 of 13,694) and
`jpmc-test` (10,000 of 10,150) are incomplete *by construction* and permanently excluded.

## 3. ADR-0053 scope exclusion is a large, undrained stock

2,876 → 4,454 eviction-candidate rows across 42–52 Boards per run. The composition is stable and
led by one Board that is not Oracle at all:

- `successfactors:careers.hcltech.com` — **2,044–2,047 rows on every one of the five runs**
  (578 of 3,014 job pages unreadable). A permanent ~2k-row block of rows that can never be evicted.
- `oracle:ejwl` — 538–541 rows, every run. `oracle:eluq` — 150–153, every run.
- `amazon:www.amazon.jobs` — 228 → 1,526 → 984 rows, and truncating **variably**: 21,773 / 17,260 /
  18,902 of ~22,717 postings (76–96%). The variability is the concern, not the level.

This is the accretion CLAUDE.md and ADR-0053 both warn has no bound and no drain. It is worth
watching across runs the way the collapse guard's 267 → 953 was watched.

## 4. zwayam fails half to three-quarters of its Boards, every run, forever

| run | attempted | successful | failed | fail rate |
| --- | ---: | ---: | ---: | ---: |
| 35175065218 | 127 | 48 | 79 | 62% |
| 35178585354 | 126 | 49 | 77 | 61% |
| 35181676606 | 138 | 55 | 83 | 60% |
| 35184790053 | 141 | 75 | 66 | 47% |
| 35188643520 | 148 | 43 | 105 | 71% |

For scale, workday fails 13 of 2,281 (0.6%) and greenhouse 6 of 2,072 (0.3%) in the same run.
zwayam alone is **55–73% of every run's board errors** (79, 77, 83, 66, 105 HTTPErrors).

**Verified live 2026-09-17** by running the registered scraper against 14 randomly sampled
ledger rows with `jobs > 0`: **12 of 14 returned HTTP 403**, 2 succeeded. That rate is consistent
with CI's. A 14-host sample is evidence, not proof, and the mechanism is not established — per this
repo's own standing lesson, a 403 does not imply its cause. What *is* established is the
operational consequence.

**Do NOT make these read as gone.** An earlier draft of this review recommended exactly that, and
it is wrong — measured 2026-09-17, after the fact:

- 10 of 10 ledger-live Boards that a fast sweep had called `unknown` came back **live** when
  re-probed serially at 3s spacing.
- 60 requests at 16-wide against 60 distinct Boards returned **200 on all 60** — so this is not a
  concurrency wall.
- A full local sweep of all 3,239 ledger rows at 8 workers produced 303 dead / 120 live /
  **2,816 unknown**, and the order effect is decisive: every settled verdict came from the first
  quartile, while quartiles 2, 3 and 4 are **809/809 `unknown` each**. That is a hard cutoff — a
  cumulative per-IP request quota — not a property of the Boards. The sweep is invalid as a
  liveness measurement and its numbers must not be written to the ledger.

So the Boards are live and the 403 is a self-inflicted quota wall that clears on cooldown. Treating
it as a gone-strike would quarantine ~100 **live** Boards per run.

**The fix is rotation, not pacing** — the wall meters cumulative requests per IP, not concurrency
(100 requests per block at 16-wide: 200s through 400, 23 of 100 refused at 500, 100 of 100 at 600,
while 60 requests at that same width never tripped it). Slowing down cannot buy quota back; a
different address can, and does — against 25 just-refused slugs, WARP cleared 25/25 where the
direct route cleared 12/25. Today **rotation does not fire for a zwayam 403** at all: `_EGRESS_ON` is empty on
purpose (a single response may route over the spare egress but must never *mark* the group walled —
the decision lives in `_ban_or_rotate`), while `_ban_or_rotate` is only reached from `_on_429` or a
*detected* challenge, and a bare Akamai 403 matches neither `cf-mitigated: challenge` nor any of the
three `_CHALLENGE_BODIES` markers. The zwayam liveness ledger was last checked **2026-08-27** —
three weeks stale — and cannot be refreshed until that gap is closed.

## 5. Retry volume is the largest untouched cost

Roughly **80,000–88,000 retries per run** against 20,000 Boards (~4.3 per Board):

| run | network | 429 | 5xx | 403-wall | total |
| --- | ---: | ---: | ---: | ---: | ---: |
| 35175065218 | 51,170 | 8,479 | 12,859 | 14,036 | 86,544 |
| 35184790053 | 49,364 | 8,191 | 15,464 | 14,943 | 87,962 |
| 35188643520 | 42,455 | 7,289 | 15,698 | 14,172 | 79,614 |

Egress itself is healthy — 0 of 15 shards look direct by retry ratio in every run, and none logged
`degrading to direct`, so WARP is up. This is a volume observation, not an egress failure.

## 6. The description store has a permanently unfillable core

`82,380 / 82,363 / 82,351 / 82,384 / 82,380` embedded ids carry **no description** — flat across the
whole window — while `update_meta` reports `0 backfilled has_description` on every run. The gap
ledger explains why it cannot move: of the store's 842,640 rows, **131,206 are "gone from a Board
this run scraped in full"** and 13,592 are "not on a Scrapable Board" — both unreachable by
construction. The live backlog (30,396 unsettled across 7,419 Boards) is draining only slowly:
7,426 left, 6,111 joined, net −1,315 in the last run.

## 7. Served-index composition

`non-tech: 25.7%` of served rows in all five runs (123,318 → 123,718 of ~481,000). Stable as a
share, rising in absolute terms. ADR-0017's gate is deliberately recall-biased so some creep is
by design, but a quarter of the served index is a product-visible number worth a decision.

## What is healthy — verified, not assumed

- **Flapping is essentially nil.** Recomputing the add/evict id sets across all five runs with a
  corrected ATS boundary: **3 of 1,227 evicted ids were re-added (0.24%)**, against the 10% RED
  threshold. One each on zoho, greenhouse, workday. The ADR-0083 grace period is working, and its
  arithmetic reconciles exactly every run (`carried_in − reappeared − unconfirmed_again = evicted`).
- **The SuccessFactors 99.6% tech keep-rate is not a broken filter.** It is the ADR-0048 slug gate
  in `successfactors.py`, which runs `is_tech(_title_from_slug(url))` *before* the detail pass, so
  only tech-looking postings ever reach `filter_tech`. Measured against 396 live postings across
  pirelli/nexans/webasto: **100% recall, zero false negatives, zero wasted fetches** (54 true tech,
  342 correctly dropped). Three tenants is evidence, not proof, but the gate is sound on this sample.
- **Quarantine drains.** It is not a 760-Board graveyard: the full line reads
  `skipped 655 of 655 confirmed-gone board(s); 105 re-admitted on parole, of 760 quarantined`
  (591+168=759, 655+105=760). Parole is working.
- `embed` — even fragments, **0 failed docs** in all five runs, a/p 0.60–1.03, all shards on cpu.
- `index prune` evicts 0 rows every run; **no shard hit its time budget** in any run; storage `live`
  is stable at 7.03 → 7.08 GB.

## Tooling defects — and three that were not real

**Read this before acting on any analyser finding here.** Three of the four "defects" in the first
draft of this review were **artifacts of the checkout I read the runs from**, not defects. The
review was run from `add-phenom-scraper`, which is **26 commits behind `origin/main`**, and the
runs executed `02d6afb`, an ancestor of `main`. Checked against `main`:

| reported as broken | actually on `main` |
| --- | --- |
| `fanout_ledgers.GAP_HEADER` missing the third unreachable class | **already fixed** |
| `fanout_ledgers.GAP_TOP` swallowing the `(+N)` delta as the board name | **already fixed** |
| `fanout_plan` dropping the `re-admitted on parole` clause | **already fixed**, and better — it cites ADR-0162 and labels pre-ADR-0162 runs |
| `fanout_merge`'s `_ATS` boundary | **genuinely broken — fixed here** |

The one real defect, and it is worse than it first looked: `_ATS = tuple(sorted(SCRAPERS))` reads
the registry **this process imports**, which is the editable install in the *primary checkout* —
so a `git worktree` does **not** isolate it. `scripts/` is checked out per tree; the `headstart`
package is not. Verified: an analyser run from a clean `origin/main` worktree still imported
`/Users/…/HeadStart/src/headstart/scrapers/registry.py` and still printed
`!! churn by ATS: 300 add id(s) NOT counted`. Fixed on branch `fix/runlog-emitter-drift`
(`e6be82da`) by harvesting the ATS vocabulary from the log itself — any `prefix:` that opens a
token in an id batch and recurs at least three times — with the registry kept as the trusted core
and three regression tests, one of which fails without the fix.

Also still true: the README's documented `curl …/search?q=…` example now returns
`{"error":"sign in first"}` — the served API is behind a sign-in wall.

**The standing lesson, which this review had to learn twice:** analyse a run from a worktree at the
run's own SHA, and for anything importing `headstart`, force `sys.path` to that worktree's `src/`.
If an analyser's own "unparsed / NOT counted" warning fires, suspect the checkout before the
pipeline.

## Ranked by what buys the most

1. **`apple:jobs.apple.com`** (§1) — the sole remaining floor Board now that `ejwl` is parked.
   Worth re-measuring on a post-`c5984f38` run first, and worth checking whether `ejwl`'s finding
   generalises: if the cost is the WARP-routed path rather than the fetch volume, apple may be the
   same shape rather than a Board to drop.
2. ~~Make a zwayam 403 rotate the egress~~ — **done** on `fix/zwayam-403-rotates-egress`, both
   halves: the liveness checker's ladder and the scraper's `egress_fallback_on`. The re-run sweep
   went from 2,816 `unknown` to 3. What remains is a decision, not work: **applying that sweep to
   the ledger** would revive 98 Boards and retire 1, and `recheck_boards.py` deliberately does not
   write it — measuring and delisting are separate calls.
3. **Stop trusting Oracle `TotalJobsCount` on sparse pods** (§2) — unblocks the ADR-0053 accretion
   at its root.
4. **Give ADR-0053 a drain**, or at least alert on `careers.hcltech.com`'s permanent ~2k rows (§3).
5. ~~Fix the three analyser defects~~ — one was real and is fixed; three were never broken.
