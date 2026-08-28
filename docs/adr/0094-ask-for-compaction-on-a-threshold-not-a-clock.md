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
since the previous compaction emptied the directory — about **50 files per run**, so ~1,000/day at
this cadence. That is real runway, not an emergency, and it is what made a threshold viable rather
than forcing an immediate manual dispatch.

## Decision

**`merge` counts `_deletions/` after its uploads and, above 3,000 of 10,000, asks for a
compaction.** The count sets a job output; a separate `request-compaction` job does the dispatch.
Two jobs rather than one step for the rule `cleanup-index.yml`'s permissions block already states:
the job holding `HF_TOKEN` must not also be able to dispatch workflows.

This is the same shape as the reclaim step immediately above it, which squashes on `usedStorage`
rather than on a run count — and for the reason ADR-0071 gave there: *"a run-count or day-count
proxy drifts whenever run size or cadence does."* The directory limit is the thing that matters,
so measure the directory.

**3,000, not 8,000.** It leaves ~7 days of headroom for a compaction that may itself wait up to
4.5 h to acquire its window, and the growth is not linear: ADR-0091 measured it at
`ceil(deleted / chunk) x fragments`, with the fragment count climbing ~10 per run, so each run
costs more than the last and the runway shortens as the directory fills. Erring early is nearly free — compaction is
idempotent and ~30 min.

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

**What this does not fix:** if `_deletions/` is already over the limit, uploads fail before `merge`
reaches the counting step, so the threshold never fires and recovery is still a manual dispatch.
The 3,000 threshold exists to keep that state unreachable, not to escape it.
