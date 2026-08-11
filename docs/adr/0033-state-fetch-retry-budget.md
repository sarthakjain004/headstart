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
the delay-seconds form of `Retry-After`), and `next_wait` sleeps that instead of `wait_before`.
Failures that advise nothing — timeouts, DNS, a listing that did not land — still use the
exponential ladder, which is why it stays. One header can carry several policies and does not say
which bucket was blown, so `reset_after` takes the **longest** reset: it is the only one guaranteed
to have cleared, and over-waiting is bounded by the budget below while under-waiting is the bug.

**List in one request.** `list_repo_files` goes through `list_repo_tree(recursive=True)`, which
pages at ~1,000 entries: two `/tree/` requests at our current 1,601 files, and another every ~1,000
the repo grows by (it grew ~90 per run across these 8 runs — 1,048 → 1,601 — between compactions,
ADR-0036). `remote_files` now reads the listing from `repo_info(expand=["siblings"])`, one
`/api/datasets/{id}` request whatever the file count.

Be honest about the size of this: it is 2 requests → 1, not the per-directory blow-out first
supposed, so it is **not** on its own an explanation for exhausting a 1,000-request window. It earns
its place because it is on the exact endpoint that 429'd, and because it stops growing. What is
actually consuming the API bucket is not yet measured — see "Not addressed".

It **fails closed** when the Hub answers without `siblings`: `DatasetInfo.siblings` is `None` there,
and an empty listing would make `absent_locally` report nothing missing, i.e. exactly the
empty-state-reads-as-a-first-run bug ADR-0030 exists to prevent. The trade is that `siblings` is
unpaginated where the tree walk kept paging, so a Hub-side truncation would be silent — not a live
risk at 1,601 files, and the reason that guard stays strict.

**Total waiting is unchanged, and that is deliberate.** Honouring `t` per attempt could sleep up to
4 × 300 s = 20 min, which **would have broken `scrape-plan`** — `state_fetch` runs there too, under a
10-minute job timeout, so it would have been killed mid-sleep and never printed the ABORT annotation
the rest of this amendment exists to make readable. So `_WAIT_BUDGET` caps the *sum* of all waits at
450 s, exactly the ladder's old worst case. The change reallocates the budget from a guess to the
Hub's own number; it does not spend more of it. Every job timeout therefore keeps the margin ADR-0033
already sized, and `next_wait` is a pure helper so that arithmetic is tested rather than asserted
here — as this ADR required of `wait_before`.

**Not addressed:** what actually exhausts the 1,000-request window is still unmeasured — this
amendment removes one request per fetch and stops guessing at the wait, but does not explain the
consumption. `snapshot_download` walks the tree itself, and the merge stage pulls 1,000–1,600 files
per run, so the next step if this recurs is to count requests per stage rather than infer them. The
remaining levers are compacting more often (ADR-0036) or leaving the free tier (PRO: 2,500/window).
