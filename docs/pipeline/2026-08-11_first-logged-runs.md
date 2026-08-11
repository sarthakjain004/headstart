# The first eight logged runs: what the new logging showed (2026-08-10 → 2026-08-11)

**Source:** the 8 completed `nightly-pipeline` runs on `c5452db`, i.e. every run after the #96/#98
logging landed (`28edf22`, 2026-08-10 17:13 UTC) and before `5109069`. All 158 job logs pulled
whole from `/repos/{owner}/{repo}/actions/jobs/{job_id}/logs` (~38 KB per shard, 9.7 MB total —
the per-job endpoint, not the whole-run zip) and aggregated offline. Numbers here are counted from
those logs, not sampled.

This is the follow-up the [30-run critical-path analysis](2026-08-10_thirty-run-critical-path.md)
asked for: *"a capped shard now names the boards it was chewing. Nobody has read a capped run's log
yet."* Somebody has now. The straggler question is answered, and the answer is not the one that
doc hypothesised.

**Headline:** the scrape straggler is **one indivisible board**, not a bad pack — a single board is
87–98% of its shard's entire wall-clock. Separately, two of the eight runs failed on the same
cause (HF 429 on the metadata API, never once recovered), and the id-level logging incidentally
exposed two things nobody was looking for: the index is **flat**, and **prune is not stable**.

## Per-run table

Minutes. Scrape shown `max/Σ`. `!` marks a failed job. No shard reached the 60-minute budget in
any run — a change from 22-of-29 in the previous window.

| run | total | scrape max/Σ | scrape median | join | embed max | merge |
|---|---|---|---|---|---|---|
| 31417782207 | 65.4 | 39/267 | 11 | 6.7 | 13.2 | 6.0 |
| 31427389154 | 72.1 | 47/287 | 16 | 6.5 | 11.7 | 5.9 |
| 31436749828 | 70.9 | 42/310 | 14 | 6.6 | 11.7 | 10.2 |
| 31444340623 | 68.7 | 41/249 | 11 | 6.1 | 8.7 | 7.9 |
| 31454850705 | 50.6 | 36/228 | 10 | 9.3! | skipped | 4.3! |
| 31459558608 | 64.0 | 38/246 | 11 | 6.4 | 10.0 | 8.5 |
| 31464238955 | 56.3 | 36/237 | 10 | 6.4 | 7.1 | 6.4 |
| 31473400252 | 58.7 | 32/203 | 10 | 6.9 | 9.6 | 9.4! |

The shape from the previous analysis holds: wall ≈ scrape-max + a fixed ~13–20 min tail, queue ~0,
and it is floor-bound (Σ/15 ≈ 14–21 min against a max of 32–47).

## The straggler is a single board, and packing cannot fix it

Correlating each shard's wall against the slowest `slow board` line in its own log:

| run | shard | shard wall | slowest board on it | that board | % of wall |
|---|---|---|---|---|---|
| 31427389154 | 0 | 47m | `successfactors:jobs.lidl` | 46m | 97% |
| 31436749828 | 0 | 42m | `successfactors:jobs.lidl` | 40m | 97% |
| 31444340623 | 0 | 41m | `successfactors:jobs.lidl` | 40m | 98% |
| 31444340623 | 1 | 39m | `successfactors:karriere.rewe-group.com` | 38m | 97% |
| 31417782207 | 0 | 39m | `successfactors:jobs.lidl` | 38m | 97% |
| 31417782207 | 7 | 37m | `workday:oreillyauto` | 32m | 87% |
| 31436749828 | 2 | 35m | `successfactors:cbscorporation.jobs` | 33m | 94% |
| 31436749828 | 5 | 33m | `workday:dollartree` | 29m | 88% |

Across the 16 slowest shards, one board is **87–98%** of the shard. The shard is one board plus
noise.

**This refutes the 30-run doc's hypothesis.** That doc guessed the monster board exceeds the
remaining budget, never completes, never records a `board_cost` row, and so the planner never
learns it is a monster. The logs say otherwise: `jobs.lidl` **completes in all 8 runs** and
`update_ledgers` times all 20,000 boards every run (61,509 ledger rows). The planner can see the
cost perfectly well.

The real constraint is that **a Board is atomic**. `binpack` distributes boards across shards; it
cannot distribute *one* board. So the makespan floor is the single largest board's duration, and
no improvement in packing quality can go below it. At 20,000 boards over 15 shards the pack is
already near-perfect (Σ/15 ≈ 14–21 min); the 47-minute shard is not a packing failure.

Three ways out, all design decisions rather than tuning — none taken yet:

1. **Intra-board sharding** — paginate a known-huge board across shards. Fixes the floor properly;
   costs a planner that plans page-ranges, not boards, and a `board_cost` keyed finer than a board.
2. **Per-board timeout** — cap any single board. Cheap, but manufactures a partial board on every
   fire, which is exactly the eviction hole already open (see below), so it cannot land alone.
3. **Cadence split** — scrape giant boards on a slower schedule than the rest. Cheapest; costs
   staleness on the biggest employers, and needs a rule for what counts as giant.

### The cost is concentrated in SuccessFactors

963 slow-board events (≥120 s) across the 8 runs:

| ATS | events | total board-time |
|---|---|---|
| successfactors | 830 | 4243 m |
| workday | 113 | 544 m |
| ripplehire | 8 | 57 m |
| eightfold | 12 | 28 m |

Worst boards by total time across the 8 runs — note the ATS medians in `update_ledgers`
(successfactors 5.2 s, workday 4.8 s) hide this tail completely:

| board | runs | total | avg | max jobs |
|---|---|---|---|---|
| `successfactors:jobs.lidl` | 8 | 17737 s | 2217 s | 24677 |
| `successfactors:karriere.rewe-group.com` | 8 | 15289 s | 1911 s | 16393 |
| `successfactors:cbscorporation.jobs` | 6 | 8598 s | 1433 s | **28** |
| `successfactors:jobs.compassgroupcareers.com` | 8 | 8161 s | 1020 s | 15819 |
| `successfactors:viacomcbs.careers` | 6 | 8155 s | 1359 s | **217** |
| `successfactors:careers.ey.com` | 8 | 8132 s | 1016 s | 7283 |
| `workday:dollartree` | 3 | 5316 s | 1772 s | 23893 |

`cbscorporation.jobs` and `viacomcbs.careers` are a **different** problem from lidl: 1,400 s for 28
and 217 jobs respectively. That is per-request latency, not volume — those two burn ~28 minutes of
board-time per run to return almost nothing.

## Both failed runs: HF 429 on the metadata API, zero recoveries

`31454850705` (join) and `31473400252` (merge) both aborted in `state_fetch`:

```
429 Too Many Requests for url:
https://huggingface.co/api/datasets/imPoseidon/headstart-index/tree/main?recursive=true&expand=false
```

This is `list_repo_files`, the **metadata** call — not the blob downloads that `HF_HUB_DISABLE_XET`
was set to protect. The merge failure in `31454850705` is a cascade of its own join failure, and it
surfaces as an unhelpful `ValueError: unsupported corpus source: .../data/jobs/tech` rather than
"upstream produced nothing".

The retry ladder is the finding. There were exactly 10 such 429s in the whole 158-job corpus, all
of them inside those two runs, and **not one attempt ever recovered** — every 429 event burned all
five attempts and aborted. ADR-0033 sized the ladder (30→60→120→240 s, 7.5 min) against a measured
outage; against *this* failure it buys nothing but a 7.5-minute delay before the abort. Either the
window is much longer than 7.5 min, or retrying the same rate-limited endpoint cannot clear it.
Note the request rate scales with file count, which grows between compactions (ADR-0036), so this
should be expected to worsen.

**Logging defect found while reading these:** `HfHubHTTPError`'s string is multi-line and puts the
CF ID first, so the GitHub annotation — which takes only the first line — renders
`state fetch attempt 1 failed (HfHubHTTPError: (Amz CF ID: …)` and the status code lands on line
three, invisible. The `reason` built at `state_fetch.py:96` should be collapsed to one line.

## Two things nobody was looking for

### The index is flat

Rows in the `jobs` table, run over run: 276,337 → 277,086 → 276,358 → 276,710 → 274,927 → 277,015
→ 275,808. Each run adds 1,773–4,117 and evicts 1,250–3,380, for **net ~zero growth**. Every run
spends ~70 min of wall and rewrites ~1.86 GB of HF LFS blobs; at ~54 runs per 100 GB quota, the
storage budget is being spent almost entirely on churn rather than on growth.

That is not automatically wrong — a job board is a stock, not a cumulative log, and a flat index
can mean postings arrive and expire at the same rate. But it has never been distinguished from the
failure mode where the same rows are removed and re-added forever, and the prune data below shows
at least some of the latter is real.

### Prune is not stable

Of the ids the `prune` step removed across the 6 runs that logged prune output, **45% were pruned
in more than one run**:

| pruned in | ids |
|---|---|
| 1 run | 891 |
| 2 runs | 455 |
| 3 runs | 251 |
| 4 runs | 38 |

`personio:ailylabs:*` is pruned in 4 of 6 runs. A prune is supposed to be terminal: the row leaves
the table. For the same id to be pruned again two runs later, something must have **re-added it in
between** — so `index sync` and `index prune` are fighting each other on a stable set of ids, every
cycle. Both prune reasons recur (`off-Board` is dominated by personio, `duplicate` by workday, e.g.
`workday:tapestry/tapestry_careers:JR2221` pruned in three separate runs).

Diagnosed. There are **two independent mechanisms**, one per prune reason.

**A — personio's `board_key()` disagreed with its own ids (fixed).** `PersonioScraper.parse` ids
each Job as `personio:{slug.split(".")[0]}:{jid}` — the bare tenant — while `board_key()` inherited
the default `{ats}:{slug}`, and personio's slug is the full host. So the keep-set held
`personio:ailylabs.jobs.personio.com` while the rows carried `personio:ailylabs`. They can never
match, so **every personio row was pruned as off-Board on every run** and re-added by the next
sync — for all **2,740 of 2,740** live personio Boards. `base.py`'s `board_key` docstring already
states the contract ("Override where the id's Board segment isn't the bare slug"); personio simply
didn't honour it. Fixed by overriding `board_key()`, with two regression tests in
`tests/test_index_plan.py`. Net effect: personio was scraped, tech-filtered and embedded but its
rows never survived a run, so **personio has effectively never been searchable**. (Embedding cost
was *not* re-paid — `embed_plan` diffs against the embedding store, not the index — but every run
paid the LanceDB add/delete write amplification.)

**B — the duplicate sweep can prefer a fossil (not fixed; a design call).** `plan_prune` keeps the
lexicographically-smallest casing of a `(board, native id)` group. If the *live scrape* emits a
different casing than the lex-min one, the lex-min row is a fossil that sync can never evict (its
Board is not in `scraped_boards`, so partial-harvest safety protects it), and the freshly-scraped
row is deleted as its duplicate — every run, forever. `_dedupe_boards` and `plan_prune` both claim
to pick "the same representative", and they do agree with each other; the false assumption is that
that representative is the one the scrape actually produces. Confirmed as a stable 3-cycle loop
against the pure planners. Note `test_dedup_keeps_lexmin_casing` encodes the current behaviour, so
this is a deliberate design choice to revisit, not a plain defect.

## Board errors: ~530/run, four distinct problems

Mean 530 errors/run (475–602), 2,311 distinct failing boards, 84 of which fail in all 8 runs.
Cross-tabbed, they are not one problem:

| ATS | 429 | 404 | 500 | 403 | net/parse |
|---|---|---|---|---|---|
| workday | **1526** | 0 | 0 | 15 | 4 |
| darwinbox | 0 | 3 | **832** | **372** | 0 |
| greenhouse | 0 | **532** | 0 | 0 | 0 |
| ashby | 0 | **279** | 0 | 0 | 0 |
| successfactors | 0 | 0 | 0 | 0 | **279** |
| personio | 61 | 41 | 0 | 0 | 145 |
| teamtailor | 0 | 53 | 0 | 31 | 0 |

- **Workday is self-inflicted:** 1,526 of its 1,566 errors are 429s (~191/run). We are rate-limiting
  ourselves with our own concurrency.
- **Greenhouse + Ashby are 811 pure 404s** (~101/run) — dead slugs in the liveness ledger,
  deterministic, re-attempted every run to fail identically. Cheapest cleanup in the list.
- **Darwinbox is server-side** (832×500, 372×403) — not ours to fix; candidate for backoff or
  delisting.
- **SuccessFactors' 279 are transport/parse** (curl 28/60/35, XML `not well-formed`), consistent
  with the latency profile of its slow boards.

## What this changes

- The ~25–30 min/run straggler has a name and a mechanism. It is **not** a planner-learning problem;
  it is board atomicity, and it needs one of the three design choices above.
- Reliability, not wall-clock, is now the bigger loss: 2 of 8 runs (25%) produced nothing, from a
  failure mode that retrying does not fix.
- `index sync` ⇄ `index prune` churn is a live defect, not just an efficiency question.
- ~292 requests/run (191 Workday 429 + 101 dead-slug 404s) are known-futile before they are made.
