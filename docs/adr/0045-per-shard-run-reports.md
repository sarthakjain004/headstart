# ADR-0045: Per-shard run reports, and a run-level summary

**Status:** accepted · **Date:** 2026-08-12 · **Amends:** ADR-0039

## Context

ADR-0039 put every stage behind one logging seam and weighed a **structured JSONL event
stream per shard**, rejecting it with an explicit trigger: *"new artifact plumbing for a need
`board_cost.csv` + the new WARNING lines mostly cover. **Revisit if log-grepping becomes the
bottleneck again.**"*

It became the bottleneck. Diagnosing the wall clock of twelve runs (2026-08-12) meant pulling
~165 individual job logs through the Actions API and parsing prose lines out of them. Three
things that decided the analysis could not be recovered from the logs at all:

1. **A budget-killed shard reported nothing.** `scrape_run` printed its error summary and
   `done:` line only after `scrape_all` returned, and the workflow runs it under `timeout
   60m`. The two runs most worth diagnosing (86 and 90 min) ended mid-harvest with no board
   count, no error summary, and no record of what was never attempted. A sweep keyed on the
   `done:` line silently omitted exactly those two shards.
2. **Prediction was never compared to outcome.** `scrape_plan` logs a predicted makespan and
   each shard logs its own elapsed time, in different jobs. Nothing reconciled them, so a
   2.7x cost-model over-prediction (108.8 min predicted vs 39.6 actual) was invisible across
   every run — as was the fact that the planner targets a makespan 1.8x the shard's own
   budget, which is *why* shards get killed.
3. **No run-level view existed.** `GITHUB_STEP_SUMMARY` was unused, so "what did this run
   do?" required opening ~20 job logs across five stages.

`board_cost.csv` does not cover (1) — the cost ledger never records a board the budget kills,
which is precisely the population in question.

## Decision

**One summary record per shard, written beside the fragment the shard already uploads**, plus
**a run-level step summary**. Both live in `headstart/ingest/observability.py`.

- **`write_shard(outdir, **fields)`** drops `_shard_report.json` into the fragment dir. It
  rides the existing `upload-artifact` step — no new artifact plumbing, which was ADR-0039's
  stated objection. `read_shards()` collects them in `scrape_join`, the first place in the
  pipeline that can see the whole fan-out.
- **Deliberately not an event stream.** ADR-0039 rejected per-shard *JSONL events*; this is
  one flat record of end-state per shard (counts, board-time percentiles, retry totals by
  reason, predicted vs actual, the full error map, and whether the budget killed it). Bounded
  by shard count, not by boards scraped.
- **`summary(title, lines)`** appends markdown to `$GITHUB_STEP_SUMMARY`, which GitHub renders
  on the run page. No-ops off CI.
- **Telemetry never fails a stage.** Both swallow `OSError` at WARNING. `read_shards` skips an
  unreadable report rather than raising: the join's real job is unioning the run's job data,
  and losing a whole run's scrape to a corrupt telemetry file would be a bad trade.
- **`scrape_all` abandons its queue on the way out** (`shutdown(cancel_futures=True)`). Every
  board is submitted up front, so the plain `with ThreadPoolExecutor(...)` drained the entire
  remaining assignment on any exception — meaning a SIGTERM at 60 min would run past the
  66-min step timeout and be killed before reporting anything. Turning SIGTERM into
  `SystemExit` only works because of this.

## Options considered

- **Keep grepping logs.** Zero code, and it is what ADR-0039 chose. Rejected on the trigger it
  named: the last analysis cost ~165 log fetches and still could not answer (1) at all.
- **A full JSONL event stream per shard** (ADR-0039's original option). Best forensics, but
  the volume scales with boards rather than shards, and every question that actually blocked
  the analysis is answerable from end-state. Still available later; this does not preclude it.
- **Write metrics to the HF dataset instead.** Cross-run trends for free, but it puts a
  15-way concurrent writer on the one artifact the pipeline treats as single-writer, and the
  join already has to read the fragments anyway.
- **Emit only step summaries, no shard file.** Simplest, but a step summary cannot be read
  back by a later stage, so the join could not aggregate and cross-run analysis would still
  mean scraping the Actions UI.

## Consequences

- `scrape_join` now depends on shard reports being present, and must stay tolerant of their
  absence: an older shard, a local run, or a shard that died before writing all produce none,
  and the log says so rather than failing.
- `plan.json` carries `per_shard_minutes`, so a shard can state actual/predicted. Cold-start
  runs (no cost ledger) omit it rather than writing fake minutes.
- The retry counters in `http.py` are process-global. That is correct for one-stage-per-process
  and would need revisiting if a single process ever ran two stages.
- A budget-killed shard now exits 0 having banked its fragment, and says what it deferred. The
  workflow's `|| echo` remains as the belt to that braces.
