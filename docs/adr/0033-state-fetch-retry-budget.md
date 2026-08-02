# ADR-0033: Size the state-fetch retry budget to the outage it actually faces

- Status: Accepted
- Date: 2026-08-02
- Amends the retry budget of [ADR-0030](0030-fail-closed-on-unfetched-state.md)'s `state_fetch`
  (the fail-closed semantics are unchanged). Sized against the merge job's timeout arithmetic
  ([ADR-0025](0025-parallelize-nightly-pipeline.md)) and the compaction cadence that PR #76 set.

## Context

ADR-0030's `state_fetch` retries 3× with 30 s/60 s waits — a budget copied from `up()`, chosen to
mirror the upload path rather than measured against anything. Five days of production falsified it:
**6 of 40 runs (~15%) failed**, all identically — the merge job's fetch of
`data/embeddings/jobs/* data/lancedb/*` aborting after 3/3 attempts hit `429 Too Many Requests`
from HF's `xet-read-token` endpoint, mid-download at 20–74% of ~1,320–1,340 files.

The failures cluster (five across 2026-07-30 17:01Z → 07-31 04:53Z with successes interleaved, one
on 08-02 14:45Z): HF-side congestion windows lasting minutes to hours, sampled by a 2-hourly cron.
A ~90-second retry window cannot ride out a multi-minute window — the original incident already
showed the same shape on the upload side ("outlasted the 30+60+90 s retry budget").

The guard itself worked every time: zero writes from any failed run, the served index at worst a
few hours stale. The cost is repeated work — a failed merge discards the run's scrape+embed output,
and the next run re-embeds the same jobs because `meta.jsonl` never advanced. Six wasted embed
cycles in five days is a tuning problem, not a design problem.

Merge is the only casualty because it is the only job fetching ~1,300 files (the LanceDB fragment
pile that accumulates between every-2-day compactions); per-file xet token requests are what trip
the rate limit. `scrape-plan` and `join` fetch 2–4 files and sailed through the same windows, as
did every cleanup-index run.

## Decision

**Five attempts, exponential waits capped at 5 minutes: 30 s → 60 s → 120 s → 240 s** (7.5 minutes
of waiting; with up to five partial downloads, a worst case around 15–18 minutes). The schedule
lives in a tiny pure helper so the arithmetic is tested rather than asserted in a comment.

**No jitter.** The workflow-level concurrency group serializes runs, so there is nothing to
de-synchronize from on our side, and a deterministic schedule stays testable. The cron's own drift
is jitter enough against HF-side window edges.

**Merge's job timeout rises 38 → 48 minutes** to keep the hang-detector arithmetic honest: the old
ceiling assumed ~90 s of fetch backoff; the new worst case adds ~14 minutes.

**Fail-closed semantics unchanged.** Exhausting five attempts still aborts the run with the same
message; nothing downstream ever sees a partial state.

## Alternatives considered

- **`HF_HUB_DISABLE_XET=1` on the fetch** — classic CDN downloads, a different rate-limit surface
  entirely. Plausible, but it swaps a measured failure mode for an unmeasured one across the whole
  download path. Held as the next lever if 429s persist after this change.
- **Compact more often to shrink the file count** — directly reverses PR #76's quota fix
  (per-run full-table blobs filled the 100 GB quota in ~45 runs). The quota math still wins.
- **Retry the whole merge job via workflow-level retries** — re-runs the fragment downloads and
  all setup for what is a fetch-scoped problem, and GitHub's re-run semantics on `if: always()`
  jobs are easy to get wrong. The loop belongs where the failure is.
- **Leave it** — at ~15% of runs, each failure wastes a full embed cycle; the fix is a constant
  and a helper.

## Consequences

A genuine long outage now holds a runner for up to ~18 extra minutes before failing — acceptable
for a 4-cron/day pipeline where the alternative is losing the run outright. If HF throttling
worsens to where even this budget flaps, the next lever is the xet toggle, not more waiting.
