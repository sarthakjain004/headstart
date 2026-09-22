# ADR-0176: Resume derivation sweeps across pipeline runs

**Status:** accepted · **Date:** 2026-09-22 · **Amends:** [ADR-0061](0061-refreshable-metadata.md)

Run [35742628620](https://github.com/sarthakjain004/headstart/actions/runs/35742628620/job/106812007906)
lost its runner during the v13→v14 sweep, after its last progress report at 550,000 of 932,573
rows. All 1,219 new vectors had merged locally, but no dataset publication had started. The
shutdown log does not establish OOM or an application crash: it occurred about 29m42s into a
step with a 35-minute timeout. The confirmed application limitation was that no incomplete
sweep could make durable progress. The user chose resuming across pipeline runs over moving
sweeps into a separate job.

Each processed metadata row now carries an internal `_derivations_version`. A sweep dispatches
work for a soft 600-second budget, then finishes the ordinary fact and re-derivation-queue
refresh over the remaining rows. It writes a complete, order-preserving metadata file atomically.
The normal publication uploads those checkpoints; the global watermark advances only when
every row has been processed for that version. This may expose a mixture of old and corrected
derived values for several runs, while continuing publication of new Jobs and fresh facts.
No served column or extraction rule changes.

Row stamps survive embedding upgrades that remove/reorder rows, unlike an offset into the
metadata file. A new version invalidates older stamps. A missing description store cannot
stamp rows or advance the global watermark; individual rows without held descriptions retain
ADR-0061's existing conservative treatment and later description arrival uses the queue.

Only one 1,000-row batch per worker may be submitted but not yet consumed by the writer.
Completed results count against this bound; source order is restored before writing. Python
3.12's eager `Executor.map` previously consumed the whole store and retained results behind
slow 50,000-row batches. The temporary rewrite lives outside the uploaded embedding-store
directory so a hard-killed process cannot accidentally publish its partial temporary file.

The budget is soft: input loading, in-flight batches, the remaining fact/queue pass and uploads
take additional time. An arbitrary runner shutdown before publication still loses the current
run's progress; the next run resumes from the last *published* checkpoint. This change does not
claim to prevent hosted-runner termination or establish its cause.
