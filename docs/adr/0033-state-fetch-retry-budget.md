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

## Amendment (2026-08-11): wait what the Hub says, and stop paying for the listing twice

**Status:** accepted. The exponential ladder above is demoted to a *fallback*; it is no longer the
primary schedule.

The ladder was sized against a measured outage but never against HF's published contract, and the
runs of 2026-08-10/11 showed it failing on its own terms: **10 rate-limit retries across two runs,
zero recoveries.** Runs 31454850705 (`join`) and 31473400252 (`merge`) each burned all five attempts
and aborted. See `docs/pipeline/2026-08-11_first-logged-runs.md`.

Two facts from [HF's rate-limit documentation](https://huggingface.co/docs/hub/rate-limits) explain
it, and neither was known when this ADR was written:

1. **Quotas are fixed 5-minute windows**, metered in three buckets, and a free account gets **1,000
   Hub API requests per window** (Resolvers, the `/resolve/` file downloads, get 5,000 — far more
   headroom). `/api/datasets/{id}/tree/...` is an API-bucket call.
2. **Every 429 carries the reset time**: `RateLimit: "api";r=<remaining>;t=<seconds to reset>`.

So the ladder was guessing at a number the server was already sending, and guessing *low*: attempts
land at 0/30/90/210/450 s, four of them inside about three and a half minutes. Each retry spends
another API request in the very window it is waiting on, holding the bucket saturated. The measured
signature is exactly that — `join` exhausted its ladder at 04:01:27 and `merge`, in the same run,
fetched 1,521 files successfully at 04:05:40, one window boundary later.

**Amended decision, two parts.**

**Wait what the Hub advises.** `reset_after` reads `t=` from the `RateLimit` header (falling back to
`Retry-After`), and the loop sleeps that instead of `wait_before(attempt)`, capped at `_BACKOFF_CAP`
so a bogus header cannot park a job past its timeout. Failures that advise nothing — timeouts, DNS,
a listing that did not land — still use the exponential ladder, which is why it stays.

**Stop listing twice.** `list_repo_files` walked the `/tree/` endpoint, which costs one API request
*per directory*; the state repo's file count grows ~90 per run between compactions (1,048 → 1,601
across these 8 runs, ADR-0036), so that cost rises every run. `remote_files` now reads the whole
listing from `repo_info(expand=["siblings"])` — a single `/api/datasets/{id}` request, which is HF's
own recommendation for this pattern. It **fails closed** when the Hub answers without `siblings`:
`DatasetInfo.siblings` is `None` there, and an empty listing would make `absent_locally` report
nothing missing, i.e. exactly the empty-state-reads-as-a-first-run bug ADR-0030 exists to prevent.

**Timeout arithmetic, re-checked.** Worst-case waiting rises from 7.5 min (30+60+120+240) to 20 min
(4 × the 300 s cap). Against measured stage times — `merge` 5.9–10.2 min including a 143–406 s fetch,
`join` ~6.5 min — the worst cases become ~30 min and ~27 min against job timeouts of **48** and **40**
minutes. Both keep their margin, so no timeout changes; recorded here because the arithmetic is what
this ADR exists to keep honest.

**Not addressed:** the 1,000-per-window ceiling is the *free* tier. If API-bucket pressure keeps
growing with file count, the levers are compacting more often (ADR-0036) or a paid plan (PRO: 2,500).
