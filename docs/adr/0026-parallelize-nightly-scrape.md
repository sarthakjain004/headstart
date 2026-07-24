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

ADR-0025 Phase 1 sharded embedding; scrape stayed one time-boxed `nightly_harvest` job under a
140-minute budget. Scrape is the *other* stage that blows its budget — on join/Workday-heavy slices,
whose scrapers fetch each posting's detail in a per-board pool. With embed parallel, scrape becomes
the next wall-clock floor, and it is embarrassingly parallel: board scrapes are independent per-board
network I/O.

The complication scrape has that embed doesn't is **external hosts**. Embed is pure local CPU; scrape
hits ATS endpoints, and a naive fan-out could multiply the load a single ATS host sees. `scrape_all`
runs `min(cpu*4, 64)` board-threads with each detail scraper adding its own bounded pool, and no
*global* per-host limit — only `http.py`'s backoff. So the design has to answer "does 15× the runners
mean 15× the hammering?" before it answers "how do we balance the boards?"

## Decision

Adopt the same **plan → fan-out → join** shape as ADR-0025, one stage upstream:

1. **`scrape-plan`** (1 job) — select this run's slice exactly as the monolith does (`pick_boards`:
   priority-first + a random exploration tail, capped at `--max-boards`), then split the *selected*
   boards across shards: shard **count** sized by board count (~`--target-boards` per shard, clamped
   to `max-parallel`), and **which board goes where** by an LPT bin-pack (the shared
   `headstart.binpack`) over a per-Board cost estimate. Emits `shard-{k}.jsonl` board lists + a
   `plan.json` matrix.
2. **`scrape`** (≤15 jobs, matrix) — `nightly_harvest --assignment shard-{k}.jsonl --outdir
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

## How we implement it

- **`nightly_harvest.py` gains `--assignment <boards> --outdir <dir>`** — the exact mirror of
  `embed_jobs --assignment`: scrape a planner-built board list into a fragment, no slice selection.
  The default `pick_boards` path is untouched (local/single-job runs).
- **`scripts/pipeline/plan_scrape.py`** — selects the slice, costs each board, sizes + LPT-packs.
- **`scripts/pipeline/join_shards.py`** — streaming per-ATS concatenation of the fragments. Boards are
  shard-disjoint, so the union is a concat; an intra-board resume duplicate is deduped downstream by
  id (`corpus.iter_jobs`), exactly as with the monolith's single file.
- **`headstart.binpack`** — the LPT packer and shard sizing, extracted from `plan_embed` so both
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
  in one run — the whole live set covered per night instead of a rotating ~weekly slice — bounded by
  the serial join + the embed/merge tail, not a 15× wall-clock cut.
- **A real politeness risk remains for globally-limited hosts.** Watch the sharded scrape's error rates
  on Workday/DataDome-class ATSes; if a shard gets blocked, give that ATS affinity (alternative 1) or a
  worker cap. The per-board 8-worker pool and `http.py` backoff still apply per shard.
- **Built ahead of the measurement gate.** ADR-0025 gated Phase 2 on scrape being the binding
  constraint *after* Phase 1 runs in production. This was built before that confirmation (a deliberate
  call); the monolith `nightly_harvest` path is retained, so reverting to it is a workflow change only.
  Validate against a real run before relying on it.
- **Complexity.** The pipeline is now five stages / two fan-outs; the added scrape planner, join, and
  `--assignment` mode are the cost, weighed against the freshness benefit — the same trade ADR-0025
  records, now paid twice.
- `docs/agents/deployment.md`'s flow narrative should be updated to the five-stage shape once this and
  the ADR-0025 docs (PR #52) are on `main`.
