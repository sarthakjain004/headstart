# ADR-0036: Fetch HF state over the plain path, not Xet

- Status: Accepted
- Date: 2026-08-05
- Keeps [ADR-0030](0030-fail-closed-on-unfetched-state.md)'s fail-closed semantics and
  [ADR-0033](0033-state-fetch-retry-budget.md)'s retry budget unchanged — both behaved exactly
  as designed throughout the outage. What changes is the transport underneath them.
- Explains why [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md)'s every-2-days
  compaction is load-bearing for availability, not just for storage quota.

## Context

Between 2026-08-02T14:45Z and 2026-08-05T13:00Z, **21 of 25 scheduled pipeline runs failed**,
every one of them identically: the `merge` job's `state_fetch` took HTTP 429 from
`huggingface.co/api/datasets/imPoseidon/headstart-index/xet-read-token`, spent its full
five-attempt budget (30/60/120/240s), and aborted rather than publish from an empty store.
Before that window, 19 consecutive runs had succeeded.

The trigger was **file count**, which scales the number of token requests a fetch makes. The
served table appends on every run — `sync` and `prune` write new data, manifest, transaction and
deletion files, and the 2-hourly upload deliberately omits `--delete` so a run ships ~10% of the
table instead of all of it (ADR-0023). Only `cleanup-index` compacts. The count climbed 1,339
(Aug 2) → 1,524 (Aug 3) → **2,563** (Aug 5), and 429s began landing consistently around 1,000
files into a fetch.

That made it self-sealing. `cleanup-index` — the only thing that reduces the count — must first
download `data/lancedb/*`, so from Aug 3 it failed on the same 429. **The repair mechanism was
blocked by the condition it repairs**, and each 2-hourly run added more files to the pile.

## Decision

**Set `HF_HUB_DISABLE_XET=1` on the pipeline and cleanup workflows**, so state transfers use the
plain resolve/CDN path.

The reason is narrower than "Xet is rate-limited", and the narrower version is the one worth
keeping. A verification run on 2026-08-05 fetched all 2,570 files in 95 seconds — and took **22
HTTP 429s while doing it**. Both transports are rate-limited by this repo. The difference is
recovery: `huggingface_hub` retries a 429 on the plain path internally and continues, whereas on
the Xet path the same status surfaced as a `RuntimeError` that killed the entire
`snapshot_download`, leaving `state_fetch`'s budget to re-attempt a whole doomed fetch five times
over. **The fix is not avoiding rate limits — it is using the path that survives them.**

That run compacted **2,573 files down to 14**, preserving 270,827 rows (`prune` evicted 0 —
nothing was stale, the table was merely fragmented), and unblocked the pipeline.

## Alternatives considered

- **Widening the `state_fetch` retry budget.** The 429 persisted across the full 7.5 minutes and
  across days; ADR-0033 sized that budget against a measured transient outage, and this is not
  one. More retries would only lengthen each failure.
- **Lowering `snapshot_download` concurrency** to slow the request rate. Plausible, and untested
  because it was not needed — but it treats the symptom, and it slows every fetch forever to
  avoid an error the other transport already handles.
- **Uploading with `--delete` on every run** so files never accumulate. This is precisely the
  full-table replace ADR-0023 removed: a fresh ~755 MB blob per run, retained forever, filling
  the 100 GB quota in ~45 runs.
- **Compacting more often.** Complementary rather than alternative, and still needed — see below.

## Consequences

The plain path is less efficient than Xet's chunked dedup for large blobs; at this repo's size
and cadence that cost is not measurable against a fetch that completes at all.

**The underlying fragility is unchanged.** File count still grows monotonically between
compactions, and a fetch's request count still grows with it. This decision buys a transport that
degrades gracefully instead of catastrophically; it does not stop the growth. If the count climbs
back into the thousands, expect slower fetches and eventually the same wall on a path with no
better fallback. The open question ADR-0023's cadence now owes an answer to is whether every 2
days is frequent enough at 12 runs/day — measure the climb from the post-compaction baseline of
14 files before changing it.

Worth noting for future outages: the fail-closed guard (ADR-0030) is what made this diagnosable.
Every failure was loud, identical and at a known step, and the served index kept answering from
its last good state for three days rather than being replaced by an empty one.
