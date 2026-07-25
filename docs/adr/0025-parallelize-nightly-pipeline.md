# ADR-0025: Parallelize the nightly pipeline across GitHub Actions runners — plan → fan-out → merge

- Status: Accepted — Phase 1 shipped; Phase 2 followed as [ADR-0026](0026-parallelize-nightly-scrape.md)
- Date: 2026-07-24 (measured outcome appended 2026-07-25)
- Builds on [ADR-0020](0020-free-tier-deployment.md) (the single-job nightly + the HF-state
  download→mutate→upload round-trip), [ADR-0022](0022-tech-priority-board-ordering.md) (the
  board-priority EWMA the planners bin-pack on), [ADR-0014](0014-search-index-ingestion-and-freshness.md)
  (board-scoped eviction — why the join must union all shards before sync), and
  [ADR-0004](0004-memory-safe-parallel-resumable-scrape.md) (the append-only, crash-safe store the
  merge concatenates). Changes none of their semantics; the flow this parallelizes is documented in
  [`docs/agents/deployment.md`](../agents/deployment.md), the per-bucket embed costs in
  [`docs/AI_Integration/embedding-throughput.md`](../AI_Integration/embedding-throughput.md).

## Context

The nightly ingest (`pipeline.yml`, ADR-0020) is **one linear `ubuntu-latest` job** — download state
→ scrape → tech-filter → priority-update → embed → sync → prune → compact → upload → restart — under
a 350-minute cap, with two internal time budgets (`timeout 140m` scrape, `timeout 100m` embed) that
bank partial progress and resume next run.

Two facts make this slow and often incomplete:

- **Embedding is the measured bottleneck and it is CPU-bound.** GitHub's free runners have no GPU, so
  `embed_jobs.py` runs `device="cpu"`, fp32, at rates that fall off a cliff by token length —
  measured ~0.8 / 1.7 / 4.4 / ~18 s/doc for the 512 / 1024 / 2048 / 4096-token buckets
  (`embedding-throughput.md`). The 100-minute budget regularly clears only part of a backlog-heavy
  slice (an examined run: 2,312 of 5,810 docs before the budget expired). Scrape hits its 140-minute
  budget too on join/Workday-heavy slices (per-job detail fetches).
- **We use 1 of 20 available concurrent runners.** GitHub Actions Free allows **20 concurrent jobs,
  account-wide** (shared across `ci.yml`, `bot.yml`, `deploy-space.yml`). The pipeline is a single
  job; the other 19 slots sit idle during the nightly.

The consequence is freshness drift: the rotating `--max-boards` slice covers the live set only
~weekly, and a backlog-heavy night leaves boards ~9–10 days stale (ADR-0022 context). We want to
**clear the nightly backlog in one run** by spreading scrape and embed across the idle runners.

The hard constraint is the **state substrate**. The run is a download→mutate→upload cycle over a
**single HF git-dataset** (`embeddings.f32` + `meta.jsonl` + `manifest.json` + `data/lancedb/` +
`board_priority.csv`) and a **single-writer LanceDB table**. Neither accepts concurrent independent
writers — so parallelism can live in the *compute*, but the *write* must serialize.

## Decision

Adopt a **plan → fan-out → join → fan-out → merge** shape: a single workflow whose one job becomes
five stages, two of them GitHub Actions **matrix** fan-outs capped at 15 concurrent runners.

1. **Scrape-plan** (1 job) — read the liveness ledger + `board_priority.csv`, **bin-pack boards** into
   *N* groups balanced by predicted job-count (the EWMA counts are the cost estimate), emit the
   assignment as matrix JSON.
2. **Scrape fan-out** (≤15 jobs, matrix) — each shard scrapes only its board group into a
   **shard-scoped** `data/jobs/shard-{k}/{ats}.jsonl` artifact (no shared file to clobber).
3. **Join + prep** (1 job) — download all scrape artifacts, concatenate per-ATS and **union the board
   set** into `data/jobs/`, run tech-filter and the priority-ledger update, then act as the
   **embed-planner**: download the prior `meta.jsonl`, diff the new ids, tokenize them, **bin-pack by
   measured per-bucket cost** into *M* groups, **predict the makespan**, size the fan-out, and emit the
   embed assignment.
4. **Embed fan-out** (≤15 jobs, matrix) — each shard embeds only its assigned docs (texts passed via
   artifact) into a shard fragment (`embeddings.f32` + `meta.jsonl`). Shards are **stateless**: they
   need neither the existing store nor the LanceDB — the planner already did the dedup and bucketing.
5. **Merge + sync** (1 job) — download the prior store + all shard fragments, **concatenate** them into
   the store, then run the existing `index sync` → `index prune` → `index compact` → upload → restart,
   unchanged. This is the **single writer**.

Cross-cutting rules:

- **`max-parallel: 15`**, decoupled from shard *count* — leaves 5 of the 20-job pool for `ci.yml` /
  `bot.yml` / `deploy-space.yml`, and lets the planner emit *more, smaller* shards than 15 (they run
  15-at-a-time) for tighter load-balancing and shorter stragglers.
- **Dynamic shard count**, scaled to the workload (`strategy.matrix` sized from the planner's JSON via
  `fromJson`): 1 shard for a 200-doc day-run, up to the cap for a backlog-heavy nightly. Spinning 15
  VMs with ~4-minute setup each to embed ~13 docs apiece is pure waste.
- **Cost-aware bin-packing (LPT)**, never hash / round-robin — per-item cost is heavy-tailed on both
  axes (embed 20×, scrape similar), and makespan is the *slowest* shard.
- **Single merge/write job** — the only holder of the write-scoped `HF_TOKEN`; `if: always()` +
  matrix `fail-fast: false` so a straggler or OOM shard banks-and-continues instead of aborting the run.
- **Keep the workflow-level `concurrency: group: nightly-pipeline`** — it serializes whole *runs* so two
  never race on the dataset; orthogonal to intra-run sharding and still required.

**Phased rollout: embedding first.** Phase 1 shards only the embed stage (embed-planner + an
`embed_jobs.py` assignment mode + the embed matrix + the merge job); scraping stays
monolithic-but-time-boxed. Phase 2 shards scraping **only if** it is still the binding constraint once
embed is parallel.

## How we implement it

- **Matrix sized by a prior job:** the plan job sets an output (`shards: '[0,1,2,...]'`); the fan-out
  job declares `strategy: { max-parallel: 15, fail-fast: false, matrix: { shard: ${{ fromJson(needs.plan.outputs.shards) }} } }`.
- **State passing:** `actions/upload-artifact` per shard, `actions/download-artifact` in the join/merge
  jobs. The heavy ~460 MB store + LanceDB download and the upload are confined to the **merge job**;
  the planner pulls only `meta.jsonl` to diff. One artifact is *not* small and this ADR originally
  missed it: the join→merge `corpus-state` carries the whole scrape snapshot (`data/jobs`), because
  `index sync` derives the corpus-id set and the eviction Board set from it. It grows linearly with
  `--max-boards` (46 s up / 14 s down at an 8,000-Board slice). Only the *id* and *Board-key* sets are
  actually consumed — the indexed rows come from the store's `meta.jsonl` — so this artifact is a
  standing simplification target, not a fixed cost.
- **`embed_jobs.py` gains an assignment mode** — a `--assignment <file>` (id list + texts) that embeds
  exactly the given docs instead of self-selecting via `--resume` + corpus glob. The planner owns the
  dedup and the token-bucket balancing, so each shard is deterministic and its runtime predictable.
- **Two small planner scripts** (`src/headstart/ingest/plan_scrape.py`, `plan_embed.py`) that read the
  ledgers / `meta.jsonl`, LPT-bin-pack, print the predicted per-shard makespan, and emit the matrix JSON.
- **The merge is a concatenation** — the store is append-only row-major with the id carried in each
  meta row (ADR-0004), so combining shard fragments is `cat`, not reconciliation. `index sync` /
  `index prune` / `index compact` and the upload/restart run exactly as today.

## Why this shape

Parallelize where the work is genuinely parallel (scrape is per-board I/O; embed is per-doc CPU) and
**serialize only where the storage forces it**. LPT bin-packing because random assignment straggles on
heavy-tailed costs — makespan is the worst shard, not the mean. Dynamic width because a fixed 15-way
fan-out wastes setup on the small day-runs. A single merge because the HF git-branch and the LanceDB
table are single-writer and `prune`/`compact` are inherently global — and because board-disjoint
sharding makes that merge cheap (a concatenation, not a conflict resolution).

## Alternatives considered

1. **Status quo — one time-boxed, resumable job.** Simplest, degrades gracefully (banks partial,
   resumes). Rejected as the *target* because it can't clear a backlog-heavy nightly in one run — the
   very drift we're trying to remove. It remains the correct fallback and the Phase-1 baseline for scrape.
2. **Hash / round-robin sharding (`hash(id) % N`).** Its only virtue is needing no planner. Rejected:
   per-doc cost spans 20×, so random assignment reliably hands one shard the heavy 4096-token docs and
   it straggles a minute-plus while others idle. We need a planner for dedup regardless, so LPT is
   nearly free on top.
3. **Let each VM write to HF directly, skipping the merge job** (proposed on the grounds that
   whole-board shards are data-disjoint). Board-disjointness is real and valuable — it removes *data*
   races and makes the merge a concatenation — but it does **not** remove *storage* races: (a) the HF
   dataset is one git branch, so concurrent commits hit `412` ref conflicts (a retry storm that
   serializes anyway) or, if each uploads the monolithic `embeddings.f32`, silently lost-update each
   other — the exact 2026-07-05 clobber the runbook records; (b) the LanceDB `jobs` table is
   single-writer and `prune`/`compact` are global (cross-board dedup, dead-board sweep, whole-table
   rewrite) — un-shardable in principle; (c) 15 independent committers give a torn, non-atomic served
   state where the Space could restart mid-update. Even the shard-scoped-file variant (each VM writes a
   unique `shard-{k}.f32`) still needs one downstream single-writer job to sync/prune/compact/upload.
   So the consolidation job survives — for serialization + atomicity, not conflict resolution.
4. **Federated serving — the Space queries N shard-tables and merges at query time.** Rejected: pushes
   the complexity onto the latency- and memory-constrained free-tier Space; the served index wants to be
   one compacted table.
5. **A GPU runner** (embedding is 10–40× faster on GPU). Rejected: GitHub-hosted GPU / larger runners
   are paid, off the free tier.
6. **Reduce embed cost — truncate harder (cap < 4096) or a smaller model.** Rejected: directly degrades
   ranking; the 8192→4096 cap is already a conscious concession (ADR-0005), and the long docs are where
   the semantic signal lives.
7. **Build both fan-outs at once.** Rejected in favour of phased. Embed is the *measured* bottleneck and
   the *cleaner* half (append-only store, trivial merge, stateless shards); scrape carries the messier
   eviction-union semantics and hits its budget less often, so it is lower-ROI and higher-risk to do first.

## Consequences

- **Realistic speedup is 3–6× end-to-end, not 15×.** Amdahl: the serial tail — download + join + sync +
  prune + compact + upload + restart, ~20–40 min — cannot parallelize, and each shard carries fixed
  setup + artifact I/O. The real win is the nightly backlog **clearing in one run** and freshness moving
  from ~9–10 days toward ~1–2, not raw wall-clock. (Measured: the serial tail came in far *under* this
  estimate — see "Measured outcome" — but so did the speedup, and for a different reason.)
- **A large complexity step:** one linear job becomes ~32 jobs across two fan-out/fan-in stages, two
  planners, artifact passing, a cost model, dynamic sizing, and cross-shard failure handling. This is the
  main cost weighed against the freshness benefit; it is only worth it while board-staleness is a real
  pain. If ~2–3-day freshness is acceptable, the status quo is preferable.
- **Billing:** 15× the setup + egress in *billed minutes* — free on the public repo's unlimited public
  minutes, but a real cost if the repo ever goes private.
- **A reusable admission-control signal:** the embed-planner prints a predicted makespan and can cap a
  run to the top-priority docs that fit, banking the rest to the next run's `--resume` — turning "will it
  fit the budget?" from a gamble into arithmetic. Observability the monolith never had. **Half-delivered:**
  `plan_embed --limit` implements the cap, but `pipeline.yml` never passes it, so only the printed
  makespan is live in production; the cap itself is unexercised.
- **Invariants preserved:** single-writer merge, board-scoped eviction (the join unions before sync), the
  crash-safe/resumable store (ADR-0004), and run-level serialization (the `concurrency` group).
- **`embed_jobs.py` gains an assignment mode** — additive; the existing `--resume` path stays for local
  and single-job runs.
- **Lifecycle:** Phase 1 shipped and this ADR is **Accepted**. Phase 2 (scrape sharding) was taken up
  as [ADR-0026](0026-parallelize-nightly-scrape.md) — deliberately *ahead* of the measurement gate this
  ADR set, which that ADR records as a known deviation.

## Measured outcome

Run `30131376268` (2026-07-24 22:35 UTC), the first full five-stage run: **85 min end-to-end** —
scrape-plan <1 min, scrape 20 min (14 jobs), join 4 min, embed 55 min (15 jobs), merge 2 min.
Corpus that run: 390,167 scraped lines, 124,248 tech (31.8% keep), 9,708 new Docs against a prior
store of 254,061 ids.

- **The premise holds — embed is the bottleneck.** 55 of 85 min, 65% of wall-clock, with the fan-out
  already saturated at all 15 lanes. Every further wall-clock gain has to come from per-Doc cost or
  more lanes, not from better balancing.
- **The embed cost model is accurate**, and this is the load-bearing result: measured s/Doc vs
  `_S_PER_DOC` was 0.88 / 1.98 / 4.36 / 19.4 against 0.8 / 1.7 / 4.4 / 18.0 for the
  512 / 1024 / 2048 / 4096 Buckets; predicted makespan 42.7 min vs 44.5 min actual. LPT held the
  shards to 25–55 min. The bin-packing machinery this ADR argued for does what it claimed.
- **End-to-end speedup was 1.4×, not 3–6×** — 121 min monolith (run `30111428538`) → 85 min sharded.
  Not like-for-like (the monolith was time-boxed and banked partials, so it did less work), but the
  3–6× range remains unvalidated and should not be quoted as measured.
- **The serial tail is far smaller than the 20–40 min estimated** — join 4 min + merge 2 min. The HF
  round-trip in particular is cheap: 11 s to download the store + LanceDB, 19 s to upload.
- **Runs are slice-limited, not corpus-limited.** `--max-boards 8000` against 51,314 live Boards on
  enabled ATSes supplies only ~640 lane-min of embed work, which 15 lanes absorb in ~43 min, so the
  350-min budget in the Context goes unused. That is a cap we chose, not a shortage of work: the
  store spans only **16,608 of 51,314 live Boards** (32%), and full coverage carries a backlog on
  the order of **100,000-210,000 Docs** — 2-4 runs at a 200-minute embed budget. See
  [`embedding-throughput.md`](../AI_Integration/embedding-throughput.md) for the derivation, the
  per-ATS coverage table, and the three easily-confused corpus numbers (projected live tech ~514k,
  store 263,769, served index ~200k). Raising `--max-boards` is what fills the budget; the binding
  technical cap is then `timeout 100m` inside the embed step, and `plan_embed --limit` is the
  control that should govern it.
- **Two defects this shape introduced.** (1) The `actions/cache` entry for the embedding model is
  created by the **join** job, which loads only the tokenizer, so all 15 embed shards get a cache
  *hit* on an entry with no model weights, re-download them, and — being a hit — never re-save it.
  The cache is permanently ineffective. (2) Fixed per-shard overhead is small but paid 32 times:
  `pip install` runs 83–89 s per job, ~4 min of the 85 on the critical path.
