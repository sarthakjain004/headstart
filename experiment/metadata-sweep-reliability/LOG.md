# Metadata sweep recovery, 2026-09-22

Request: fix the failed v14 derivation sweep identified in the pipeline audit. The user chose
resuming incomplete sweeps across pipeline runs over a separate sweep job. ADR-0176 records
the publication and row-alignment contract.

## Evidence

- Run 35742628620, SHA 90a6fc64, merge job 106812007906: 1,219 vectors merged, v13→v14
  sweep began at 15:30:10 UTC, last milestone 550,000 rows at 15:53:19, runner shutdown at
  15:59:50. Dataset publication never started. The runner shutdown cause is unknown; there
  is no OOM evidence in the log and neither the 35-minute step nor 98-minute job timeout
  had expired. The earlier audit overstated this as an unconditional P0.
- The dataset watermark downloaded during investigation remained v13. Fresh Uber and Workday
  description bases are captured under ignored `artifacts/data/descriptions/` using the HF CLI.
- Deterministic reproduction: `pytest -q tests/test_update_meta.py -k does_not_read_entire`
  failed before the fix at batch 3 with 2 workers and 0 batches written. `Executor.map`
  consumed the store eagerly; its queued inputs and blocked results could retain the full store.
- Local profile, first 1,000 descriptions in the freshly downloaded Workday base, synthetic
  metadata with an Engineer title: 2.939 seconds under cProfile; salary 1.515s, remote 0.861s,
  experience 0.555s. The 126-description Uber base took 0.328s. These are diagnosis samples,
  not estimates of whole-corpus throughput or production hardware.

## Implementation and verification

The pipeline uses a 600-second soft sweep budget; dispatched batches have 1,000 rows and at
most one batch per worker may be ahead of the writer. Completed row versions are published
in metadata while the global watermark remains old until completion. The metadata rewrite
remains atomic and order-preserving. Its temporary file is outside the directory uploaded as
the embedding store. Facts and queued descriptions still refresh after the sweep budget.

`verify_resume.py` replays the first 1,000 captured Workday descriptions across 5,000 synthetic
metadata rows with two real worker processes. A 0.1-second dispatch budget completed 2,000
rows in 3.226 seconds and retained watermark v13; a second pass completed all 5,000 in 6.175
seconds and advanced to v14. In-flight work explains the soft-budget overshoot. The harness
compares every final field and row order with uninterrupted `refresh_row` results.

Targeted metadata, embed-merge and log-contract tests: 399 passed. Regression coverage includes
out-of-order completion, bounded queued work, later-worker failure, preserving metadata/queue/
watermark on failure, resumption after row reordering and addition, missing descriptions, and
invalidating checkpoints on the next version bump.

Production verification requires a pipeline built with this patch to publish a checkpoint or
complete the sweep, followed by a successor reading that metadata. Local tests cannot establish
that the hosted-runner shutdown itself is fixed. No live dataset writes or pipeline dispatches
were performed during implementation.
