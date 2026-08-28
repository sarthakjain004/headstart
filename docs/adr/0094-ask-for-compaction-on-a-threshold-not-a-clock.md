# ADR-0094: Ask for compaction on a threshold, not a clock

**Status:** accepted · **Date:** 2026-08-28 · **Amends:**
[ADR-0091](0091-compaction-outranks-the-pipeline.md) (on how a compaction is *triggered*; its
priority over the pipeline is unchanged) · **Relates to:**
[ADR-0093](0093-chain-the-successor-the-cron-is-only-a-seed.md) (the same delivery failure, the
same remedy)

## Context

ADR-0091 moved compaction into its own workflow on a daily `0 3 * * *` cron and gave it priority
over the pipeline, because its starvation is what took every HF upload down on 2026-08-27:
`data/lancedb/_deletions/` passed HF's **10,000-file per-directory limit** and pushes were
rejected outright.

ADR-0093 then established that GitHub stopped delivering this repo's scheduled events reliably on
2026-08-26, and fixed the *pipeline* by having each run dispatch its own successor. That left
compaction as the only thing still depending on the delivery that had failed — and on 2026-08-28
it duly did not fire: at 07:37 UTC there was no 03:00 run, and the last compaction was a manual
dispatch 21 hours earlier. `bot.yml`, the untouched control, was 391 minutes late against a
`*/15` cron at the same moment.

Compaction is the one job here that cannot merely be *late*. A late scrape costs freshness; a
compaction that never runs costs every subsequent upload.

Measured 2026-08-28 against the live dataset: `_deletions/` held **309 files** across the six runs
since the previous compaction emptied the directory. That is real runway against the 10,000 limit,
not an emergency, which is what made a threshold viable rather than forcing a manual dispatch.

**One measurement cannot establish the growth law, and this one fits two.** Linear at ~51
files/run gives exactly 309 at n=6. So does quadratic: ADR-0091 measured growth as
`ceil(deleted / chunk) x fragments` with the fragment count climbing ~10 per run and ~10,000 files
accumulating across ~36 runs, which fits `~7.7n²` and predicts 278 at n=6. Everything below is
sized against the quadratic — it is both the pessimistic model and the one ADR-0091 derived from
the mechanism rather than from a curve. Re-measure at a second point before trusting either.

## Decision

**`merge` counts `_deletions/` after its uploads and, above 3,000 of 10,000, asks for a
compaction.** The count sets a job output; a separate `request-compaction` job does the dispatch.
Two jobs rather than one step for the rule `cleanup-index.yml`'s permissions block already states:
the job holding `HF_TOKEN` must not also be able to dispatch workflows.

It counts **per directory**, not in total, because that is how HF enforces the limit — two
half-full `_deletions/` directories are fine, and summing them would compact for no reason. Today
there is exactly one (`data/lancedb/jobs.lance/_deletions/`), which is why the distinction is cheap
to make now and would be invisible to find later.

This is the same shape as the reclaim step immediately above it, which squashes on `usedStorage`
rather than on a run count — and for the reason `pipeline.yml` states there: *"a run-count or
day-count proxy drifts whenever run size or cadence does"* (ADR-0071 §Consequences words it as
"drifts as run size or cadence does"). The directory limit is the thing that matters, so measure
the directory.

**Why 3,000: it reproduces the daily cadence, not a long runway.** Under `~7.7n²`, one day of runs
at the ~20/day back-to-back rate lands at ~3,086 files — so tripping at 3,000 asks for a compaction
about as often as the 03:00 cron intended, which is the behaviour ADR-0091 designed around.

It is **not** chosen for headroom, and an earlier draft of this ADR claimed ~7 days of it. That was
a linear extrapolation and it was wrong. Under the quadratic, the margin from 3,000 to 10,000 is
**~16 runs (~0.8 days)**, and lowering the threshold barely helps: 2,000 buys ~20 runs, 500 buys
~28. Compacting *often* is the protection; a bigger buffer is not purchasable at any threshold.
~16 runs is still ~4x the ~4 runs that elapse while a compaction waits out its worst-case 4.5 h
window. Erring early is nearly free — compaction is idempotent (ADR-0071) and took **31 min** on
run 33063013497 (2026-08-27 10:26 → 10:57).

**A backstop, not a replacement.** The 03:00 cron stays. When it fires, nothing here triggers;
when it does not, the threshold catches it. Two independent triggers for the one job whose
starvation is unrecoverable.

**Deliberately unconfirmed, unlike ADR-0093's hand-offs.** `chain` and `handback` verify their
dispatch landed, because they are the only thing that restarts a stopped cadence and a dropped 202
there is terminal. This request is re-issued by *every* subsequent run for as long as the threshold
stays crossed, so it retries itself and needs no confirmation loop. The asymmetry is the point, not
an oversight.

It is also idempotent against itself: if a compaction is already in flight the request is skipped,
because `cleanup-index` keeps a single pending slot and a duplicate would displace a waiting one —
the exact starvation ADR-0091 removed.

## Consequences

Compaction now happens on need rather than on a clock, and the frequency falls out of the write
rate instead of being asserted. At ~1,000 files/day it fires roughly every three days when the
cron is dead, and never when the cron is alive.

It fails closed. If the counting step dies its output is never written, `request-compaction` reads
empty and dispatches nothing — and the threshold is still crossed on the next run. The step carries
`continue-on-error`, so a housekeeping check can never fail a run whose data is already published.

**It also recovers the over-limit state, which an earlier draft of this ADR said it could not.**
The counting step is `if: always()`, so it runs after a failed upload; `request-compaction` is
`!cancelled()`, so it fires from a red `merge`. A run that hit the limit still asks for the
compaction that clears it. Review caught the claim — the code was right and the prose was wrong.

**One interaction worth knowing.** A failed `merge` still counts and still dispatches, so a
compaction can occur while ADR-0093's three-consecutive-failure breaker has deliberately stopped
the chain — and `handback` then restarts it. That is bounded (the compaction empties the directory,
so it does not recur) and arguably right, since a rebuilt index is a reason to try again. But it
does mean the breaker's stop is not permanent while compactions are firing.
