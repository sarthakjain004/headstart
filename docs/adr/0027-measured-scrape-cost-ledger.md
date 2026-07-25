# ADR-0027: Bin-pack the scrape fan-out on measured per-board seconds

- Status: Accepted
- Date: 2026-07-25
- Replaces the cost model of [ADR-0026](0026-parallelize-nightly-scrape.md) (which kept the
  plan → fan-out → join *shape* — only the per-Board cost estimate changes). Reuses the ledger
  pattern of [ADR-0022](0022-tech-priority-board-ordering.md) and the shared LPT packer of
  [ADR-0025](0025-parallelize-nightly-pipeline.md). Changes no eviction or partial-harvest
  semantics (ADR-0014).

## Context

ADR-0026 costed each Board as `EWMA tech-job count × a per-ATS detail weight` and called it "coarse
by design — LPT tolerates cost noise". The first production run disproved that. In run
`30131376268` the planner emitted 14 shards it costed at **`~23275` each, 571-572 Boards each** —
identical to five significant figures — and they ran **60 s to 1,222 s**. Total scrape work was
3,294 shard-seconds, so a balanced split would have had a ~235 s makespan against the 1,221 s
observed: **5.2× worse than optimal, ~16 min of an 85-min critical path**.

That is not noise a 4/3-approximation absorbs. LPT tolerates a *noisy* cost signal; it cannot do
anything with an *uninformative* one, and this estimate carried no signal at all.

The units were simply wrong. Scrape cost is driven by **total postings** and **per-posting detail
latency**; the model measured **tech** postings, and the tech keep rate itself swings from 6.9%
(Workday) to 52.7% (Eightfold). Worse, volume alone does not explain the spread either — shard 0
pulled 37,449 Jobs in 304 s while shard 10 pulled 41,262 in 1,221 s. Same volume, 4× the time. Only
elapsed time captures that. And `_EXPLORE_BASELINE = 5.0` flattened every unscored Board to one
constant, which at `--max-boards 20000` is 6,000 Boards — 30% of the slice — costed identically.

## Decision

**Measure the seconds and pack on them.**

1. **Time every Board.** `scrape_all`'s worker wraps `fetch()` in `try/finally` and records elapsed
   into a dict keyed by `{ats}:{slug}`. The `finally` is load-bearing: a Board that hangs 30 s and
   then raises cost 30 s, and the packer must know that.
2. **Stream it to disk per Board.** `JobWriter.record_cost()` appends to `board_cost.csv` in the
   shard's own fragment dir and flushes — the same contract as the `.done` journal, so a shard
   killed by its time budget still hands over every timing it did take.
3. **Blend in the join.** `scripts/rank/update_board_cost.py` reads every shard's rows and
   EWMA-blends them into `data/state/board_cost.csv` (`board,seconds,jobs,updated_at`).
4. **Pack on it next run.** `plan_scrape` costs each Board at its measured EWMA, sizes the shard
   count by `--target-seconds` of work rather than Board count, and prints a **predicted makespan**.

**Estimate cascade for a Board with no measurement:** its ATS's median → the global median →
`FALLBACK_SECONDS`. Never one flat constant — an unmeasured Workday Board and an unmeasured
Personio Board have wildly different expected cost, and conflating them is the specific mistake
being fixed.

**EWMA weight 0.5**, against ADR-0022's 0.7, because wall time carries runner and network noise a
tech-job count does not. This is the first number to tune if shards still straggle.

## How we implement it

- **No new plumbing.** Every hop already exists: the shard's fragment is uploaded by the existing
  step, `join` already downloads all fragments, `merge` already uploads `data/state/`, and
  `scrape-plan` already downloads it. Only the blend step is new.
- **The filename is undotted on purpose.** `actions/upload-artifact` skips hidden files by default,
  which is why the `.done` journal never reaches the join — `board_cost.csv` must not repeat that.
- **`on_board` is untouched.** It is *not* an unused hook (`scripts/scrape/run_scrapers.py` passes a
  3-arg callback), so widening its signature would break a caller. Timing lives in the writer.
- **Cold start degrades to ADR-0026.** With no ledger, `plan_scrape` falls back to the old heuristic
  and sizes by Board count. One run populates ~20,000 rows, so it self-replaces immediately.
- **Nothing is pruned from the ledger.** Unlike a tech-yield score, a Board being expensive is never
  a reason to forget it, and the row is tiny.
- **The blend step is `continue-on-error`.** A missing ledger costs one run of balance; it must not
  sink a run that already scraped and embedded successfully.

## Why this shape

Measure rather than proxy, because the proxy was tested and failed. Per-Board granularity because
that is the unit LPT packs. A ledger on the existing HF state round-trip because ADR-0022 already
proved that path, and reusing it means no new state substrate. And the payoff compounds: with real
seconds the planner predicts a makespan, which turns `pipeline.yml`'s `timeout 60m` scrape budget
from a guess into arithmetic — the same move ADR-0025 made for embed.

## Alternatives considered

1. **Cost on `last_total_jobs`** (one extra column on the priority ledger). Much cheaper and
   strictly better than the tech count. Rejected as insufficient: shard 0 pulled 37,449 Jobs in
   304 s while shard 10 pulled 41,262 in 1,221 s — volume does not explain a 4× time difference,
   detail-fetch latency does, and only elapsed time captures it.
2. **Extend `board_priority.csv` with cost columns.** One file, one round-trip. Rejected: ADR-0022's
   ledger answers "which Boards are worth scraping first", a *product* question; cost answers "how
   long will this take", an *operational* one. They decay differently and are consumed by different
   stages, so conflating them makes both harder to reason about.
3. **Return timings in `RunResult`.** The obvious API. Rejected outright: a shard killed by
   `timeout 60m` never returns from `scrape_all`, so every timing — including those for Boards that
   finished — would be lost. Cost data must stream to disk like the Jobs do.
4. **Widen the `on_board` callback to carry elapsed.** Rejected: it has a live caller, and a hook
   signature is a worse home for durable state than the writer that already owns the fragment.
5. **Per-ATS affinity instead of better costs.** Solves straggling by construction but wrecks
   balance, for the reasons ADR-0026 §1 already records.

## Consequences

- **Balance should approach the 235 s optimum** rather than the 1,221 s observed, recovering ~16 min
  of critical path — but this is the *expectation*, not a measurement. ADR-0026's model also looked
  sound on paper. Validate against a real run before trusting it, and re-read the per-shard
  `~N min` lines the planner now prints against actual job durations.
- **One run of cold start.** The first run after this ships still packs on the old heuristic.
- **A tunable that can misbehave.** A Board whose posting count grows sharply is under-costed until
  the EWMA catches up; one whose ATS goes down is over-costed by the recorded timeout. The 0.5
  weight is the knob.
- **New state file** on the HF round-trip. ~20k-50k rows, well under 1 MB — trivial next to the
  810 MB vector store.
- **The planner gains a predicted makespan**, so the scrape time budget can be re-derived rather
  than guessed. ADR-0026's "watch the error rates" follow-up remains unaddressed — this ADR gives
  the timing instrument, not the per-ATS error one.
