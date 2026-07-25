# ADR-0026: Parallelize the nightly scrape across GitHub Actions runners (ADR-0025 Phase 2)

- Status: Accepted
- Date: 2026-07-25
- Implements **Phase 2** of [ADR-0025](0025-parallelize-nightly-pipeline.md) (which sharded the
  *embed* stage and deferred *scrape* to "only if scrape is still the binding constraint"). Builds on
  ADR-0025's shape, its shared LPT bin-packer, and the `--assignment` shard pattern; on
  [ADR-0022](0022-tech-priority-board-ordering.md) (the board-priority EWMA the planner costs on); and
  on [ADR-0014](0014-search-index-ingestion-and-freshness.md) (board-scoped eviction — why the join
  must union all shards before sync). Changes none of their semantics.

## Context

ADR-0025 Phase 1 sharded embedding; scrape stayed one time-boxed `ingest.scrape` job under a
140-minute budget. Scrape is the *other* stage that blows its budget — on join/Workday-heavy slices,
whose scrapers fetch each posting's detail in a per-board pool. With embed parallel, scrape becomes
the next wall-clock floor, and it is embarrassingly parallel: board scrapes are independent per-board
network I/O.

The complication scrape has that embed doesn't is **external hosts**. Embed is pure local CPU; scrape
hits ATS endpoints, and a naive fan-out could multiply the load a single ATS host sees. `scrape_all`
runs `min(cpu*4, 64)` board-threads with each detail scraper adding its own bounded pool, and no
*global* per-host limit — only `http.py`'s backoff. On the 4-vCPU `ubuntu-latest` GitHub VM that
formula resolves to **16**, not 64 — the 64 is the cap a large local machine hits, and the number to
reason about for CI is 16 per shard. So the design has to answer "does 15× the runners mean 15× the
hammering?" before it answers "how do we balance the boards?"

## Decision

Adopt the same **plan → fan-out → join** shape as ADR-0025, one stage upstream:

1. **`scrape-plan`** (1 job) — select this run's slice exactly as the monolith does (`pick_boards`:
   priority-first + a random exploration tail, capped at `--max-boards`), then split the *selected*
   boards across shards: shard **count** sized by board count (~`--target-boards` per shard, clamped
   to `max-parallel`), and **which board goes where** by an LPT bin-pack (the shared
   `headstart.ingest.binpack`) over a per-Board cost estimate. Emits `shard-{k}.jsonl` board lists + a
   `plan.json` matrix.
2. **`scrape`** (≤15 jobs, matrix) — `ingest.scrape --assignment shard-{k}.jsonl --outdir
   data/jobs/shard-{k}` scrapes only its boards into a shard-scoped fragment. `fail-fast: false`.
3. **`join`** (1 job) — download all fragments, **union them per ATS** into `data/jobs/` (eviction
   scope must be the full scraped-Board set — ADR-0014), then run the existing tech-filter →
   priority-update → `plan_embed`. From here the embed fan-out and merge (ADR-0025) are unchanged.

**Cost-balanced packing with per-IP safety** (the politeness decision). Each shard is one runner with
its **own IP**. Keeping per-shard workers at the monolith default (the planner does not touch
`HEADSTART_WORKERS`) makes every ATS host see a shard as one ordinary monolith from a distinct IP —
**per-IP load, which is what ATSes rate-limit on, is unchanged**. Only a *globally* rate-limited or
bot-protected host (Workday/DataDome) sees the aggregate S× load; those are the minority, already
carry `http.py` backoff, and can be given affinity later if a real block appears. So we LPT-balance
for wall-clock and accept the per-IP-safe aggregate, rather than constraining the packing for hosts
that mostly don't need it.

**The cost model** is `EWMA tech-job count × per-ATS weight`: a detail-fetching ATS (a per-job request
each — `join`/`keka`/`ripplehire`/`rippling`/`smartrecruiters`/`trakstar`/`workday`/`zoho`) costs far
more per job than a list-only one, and those are exactly the boards that blow the budget. Coarse by
design — LPT tolerates cost noise, and the shard *count* (the thing that must be right) is sized by
board count, not this estimate.

**This model did not survive first contact — see "Measured outcome", and it is replaced by
[ADR-0027](0027-measured-scrape-cost-ledger.md), which packs on measured per-Board seconds.** The
distinction that matters is between *noisy* and *uninformative*: LPT tolerates the first, not the
second, and this estimate turned out to be the second. The plan → fan-out → join shape below is
unchanged; only the cost estimate was wrong.

## How we implement it

- **`ingest.scrape` gains `--assignment <boards> --outdir <dir>`** — the exact mirror of
  `embed_jobs --assignment`: scrape a planner-built board list into a fragment, no slice selection.
  The default `pick_boards` path is untouched (local/single-job runs).
- **`src/headstart/ingest/plan_scrape.py`** — selects the slice, costs each board, sizes + LPT-packs.
- **`src/headstart/ingest/join_shards.py`** — streaming per-ATS concatenation of the fragments. Boards are
  shard-disjoint, so the union is a concat; an intra-board resume duplicate is deduped downstream by
  id (`corpus.iter_jobs`), exactly as with the monolith's single file.
- **`headstart.ingest.binpack`** — the LPT packer and shard sizing, extracted from `plan_embed` so both
  planners share one implementation.
- **`pipeline.yml`** becomes five stages; `scrape` and `embed` are matrix fan-outs at
  `max-parallel: 15`, `fail-fast: false`; the run-level `concurrency` group and the inert-until-token
  gate are preserved.

## Why this shape

Parallelize where the work is genuinely parallel (per-board I/O), serialize only where storage forces
it (the single merge, ADR-0025). Union-before-sync because eviction is board-scoped. Cost-balanced LPT
because scrape cost is heavy-tailed on the detail-fetchers. Per-IP safety because the fan-out's real
risk is host politeness, and one-monolith-per-IP is the cheapest way to keep it unchanged for the hosts
that matter.

## Alternatives considered

1. **Host-affinity packing** — every board of an ATS host in one shard, so total per-host load equals
   the monolith. Safest for globally-limited hosts, but the dominant hosts (greenhouse/lever, thousands
   of boards) can't fit one shard — they either straggle or must be split anyway, wrecking balance.
   Rejected as the default; kept in reserve for specific sensitive ATSes.
2. **Cap per-shard workers so the aggregate ≈ the monolith's 64.** Preserves total per-host load, but
   throttles every shard for the sake of a few sensitive hosts and forfeits most of the speedup.
   Rejected — per-IP safety already protects the common case without the throttle.
3. **Shard by ATS** (each shard owns whole ATSes). No two shards hit the same host, but ATS board
   counts are wildly uneven (greenhouse/lever dominate), so balance is hopeless. Rejected.
4. **Let each scrape shard write to HF directly** — same rebuttal as ADR-0025 §3: storage is
   single-writer and eviction/prune are global; the join/merge must serialize regardless.

## Consequences

- **Freshness, not raw throughput.** As with embed (ADR-0025), the win is clearing the scrape backlog
  in one run, bounded by the serial join + the embed/merge tail, not a 15× wall-clock cut. **The
  mechanism is in place but the benefit is not yet taken:** `pipeline.yml` still passes
  `--max-boards 8000` against 51,314 live Boards on enabled ATSes, so the slice is still rotating.
  That is ~15.6% of Boards but only **~11.3% of postings** — the ledger totals 3,443,810 live
  postings and the run scraped 390,167, so the priority-first slice under-samples postings relative
  to Boards. Scale scrape cost on the ledger's posting counts, never on Board count: at 8.4 ms per
  posting a full pass is ~484 shard-minutes, ~32 min across 15 balanced lanes. Still cheap — scrape
  is not what blocks raising the cap. Embed is: the store spans only 16,608 of the 51,314 live
  Boards, a backlog of roughly **100,000-210,000 Docs** (derivation, per-ATS coverage, and error
  bars in [`embedding-throughput.md`](../AI_Integration/embedding-throughput.md)) — 2-4 runs at a
  200-minute embed budget, not one. The under-scraped ATSes are specific and worth naming:
  eightfold (6% of its projected tech in the store), successfactors (7%), smartrecruiters (16%).
- **A real politeness risk remains for globally-limited hosts.** Watch the sharded scrape's error rates
  on Workday/DataDome-class ATSes; if a shard gets blocked, give that ATS affinity (alternative 1) or a
  worker cap. The per-board 8-worker pool and `http.py` backoff still apply per shard. **No mechanism
  was built to do this watching:** `scrape_all` returns per-board `errors`, but `ingest.scrape` only
  prints a count and nothing aggregates them per ATS across shards, so "watch the error rates" is not
  actionable as written.
- **Built ahead of the measurement gate.** ADR-0025 gated Phase 2 on scrape being the binding
  constraint *after* Phase 1 runs in production. This was built before that confirmation (a deliberate
  call); the monolith `ingest.scrape` path is retained, so reverting to it is a workflow change only.
  Now validated against run `30131376268` — see below. The gate's answer, in hindsight, was **no**:
  scrape was 20 min of an 85-min run against embed's 55.
- **Complexity.** The pipeline is now five stages / two fan-outs; the added scrape planner, join, and
  `--assignment` mode are the cost, weighed against the freshness benefit — the same trade ADR-0025
  records, now paid twice.
- `docs/agents/deployment.md`'s flow narrative should be updated to the five-stage shape once this and
  the ADR-0025 docs (PR #52) are on `main`.

## Measured outcome

Run `30131376268` (2026-07-24 22:35 UTC), first full five-stage run. Scrape took **20 min** of the
85-min total across 14 shards.

**The cost model carries no signal.** The planner emitted 14 shards it costed at `~23275` each,
571–572 Boards each — identical to five significant figures. Actual shard wall times ran from
**60 s to 1,222 s, a 20× spread**. Shard 10 finished 400 of its 572 Boards in 32 s; the remaining
172 took ~1,190 s. That is not cost noise LPT can absorb — a perfectly balanced plan by the model's
own arithmetic produced a 10× straggler, and that straggler is ~16 min of the run's critical path.

The cause is a units mismatch the Decision above glossed over: the estimate is built from each
Board's EWMA **tech**-Job count, but scrape cost is driven by **total** postings (the tech keep rate
was 31.8% that run, and it varies per Board) and by per-posting detail latency. A Board with 3,000
postings and 40 tech Jobs is costed at 40, or 240 on a detail ATS. Unscored exploration Boards fare
worse still — they take a flat `_EXPLORE_BASELINE = 5.0` regardless of true size, and 1,839 of the
8,000 selected Boards were unscored.

The fix is to cost on a measurement rather than a proxy: time each Board inside `scrape_all` and
persist per-Board seconds to a `data/state/` ledger that rides the existing HF state round-trip,
making the estimate an observation — the same move [ADR-0022](0022-tech-priority-board-ordering.md)
made for tech yield. (An earlier revision of this section proposed hanging that off the
`on_board(key, n_new_jobs, error)` callback and described it as unused. It is not unused —
`scripts/scrape/run_scrapers.py` passes one — so [ADR-0027](0027-measured-scrape-cost-ledger.md)
put the timing in `JobWriter` instead and left the callback alone.)
Carrying `last_total_jobs` in the priority ledger is a cheaper interim and strictly better than the
tech count, but it is **not** sufficient on its own: shard 0 pulled 37,449 Jobs in 304 s while shard
10 pulled 41,262 in 1,221 s. Similar volume, 4× the time — the difference is per-posting detail
latency, which only a timing measurement captures.

The headroom is large: total scrape work was 3,294 shard-seconds, so a perfectly balanced 14-way
split would have a ~235 s makespan against the 1,221 s observed.

**Per-IP safety held so far.** All 14 shards ran to completion — none hit the 140-min budget — at a
steady 6–14 board errors each, 122 of 8,000 Boards (~1.5%), with no shard showing the error
concentration a block would produce. That is consistent with the one-monolith-per-IP argument but
does not yet prove it: this run drove 16 board-threads per GitHub VM (4 vCPU × 4), so the aggregate
was ~224 in-flight board threads, not the ~900 the Context's `min(cpu*4, 64)` phrasing implies. A
per-ATS error breakdown across shards is still the missing instrument.
