# apple:jobs.apple.com — the detail pass is connection-bound, not stream-bound

**Date:** 2026-09-17. **Why:** with `oracle:ejwl` parked (`c5984f38`), `apple:jobs.apple.com` is the
only remaining scrape floor Board. In run `35203211704` it was **98% of its shard**, and that shard
was 33.9 min of a 56.2 min wall.

## Reproducing this

`measure_transport.py` beside this file runs all four measurements
(`--settings`, `--streams`, `--threads`, `--ab`, or `--all`). **`artifacts/` is empty on purpose:**
re-running `--all` costs ~1,500 live requests against one company's careers origin, and that was
declined rather than spent twice. Every number below came from the runs described, through that
script's code paths; the script is tracked so they can be reproduced on demand rather than taken
on trust.

## Where the time goes — it is not the listing

From the shard's own log:

    [scrape_run] slow board apple:jobs.apple.com: 6187 jobs in 1999s
    [scrape_run] concurrency apple details @16: 6,187 req over 1,884 batch-s,
                 3.28 req/s, streams 16.0/16, mean 4.9s

**The detail pass is 1,884s of 1,999s — 94%.** The serial `_listing()` walk (~310 pages at
`_PAGE_SIZE = 20`, no larger-limit parameter) is the remaining ~115s, so parallelising it — the
obvious-looking fix — is worth about 6% and was not pursued.

## Widening the streams does nothing; adding connections does

Live, 2026-09-17, interleaved A/B on fresh ids, zero non-200s at every width.

Async (one shared `AsyncSession` = one HTTP/2 connection), varying stream width:

| streams | req/s |
| ---: | ---: |
| 16 | 4.39 |
| 32 | 4.23 |
| 64 | 4.21 |

Flat. And the server is **not** the one refusing — its own SETTINGS frame advertises
`MAX_CONCURRENT_STREAMS = 128`. So the limit is per *connection*.

Threads (one connection per worker), same endpoint:

| workers | req/s | mean latency |
| ---: | ---: | ---: |
| 16 | 3.72 | 2.83s |
| 32 | **10.34** | 2.52s |
| 64 | 15.69 | 3.21s |

Interleaved three rounds on fresh ids, to control for network drift:

| round | async, 32 streams | threads, 32 conns |
| ---: | ---: | ---: |
| 1 | 4.70 | 9.45 |
| 2 | 4.36 | 9.03 |
| 3 | 4.25 | 11.68 |

**~2.3x, reproducible.** 32 is taken rather than 64 because the gain flattens and this is one
company's careers origin, not a multi-tenant ATS with a population of Boards to spread over.

## End to end, through the real `fetch_raw`

400 deduped postings, control vs fix, interleaved:

| round | mode | wall | distinct | details | missing | employment_type |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | async, 1 conn | 85.6s | 400 | 400 | 0 | 317 |
| 1 | threads, 32 conns | 57.9s | 400 | 400 | 0 | 316 |
| 2 | async, 1 conn | 85.5s | 400 | 400 | 0 | 312 |
| 2 | threads, 32 conns | **41.1s** | 400 | 400 | 0 | 313 |

No detail loss, and `employment_type` coverage is unchanged (the ±1 is apple's own listing churn
between calls, not the transport).

### A false alarm worth recording

A first pass of this A/B reported `396 details, 4 missing` on the threaded path and 0 on async,
which read as a concurrency regression. It was not: the harness sliced `rows[:400]` and so skipped
the real `_listing()`'s dedup, while apple's evergreen `PIPE` postings get re-sorted mid-walk and
repeat across pages. `details` is keyed by id, so duplicates collapsed. Re-probing all 400 ids
directly at width 32 returned **400/400 ok**. The table above uses the real dedup semantics.

## What was NOT done, and why

- **`detail_streams = 32`** — the natural-looking knob, and measured to have no effect: it widens
  streams on the one connection, which is exactly what does not bind.
- **Parallelising `_listing()`** — ~6% of the Board's cost.
- **ADR-0048's detail skip**, which would take the pass to ~0 in steady state, is blocked:
  `employment_type` comes only from the detail payload and is **not** derivable from the listing —
  measured, `(standardWeeklyHours=40, type=PIPE)` maps to both `None` and `Standard`. Over 440
  sampled details the field is `Standard` 94.1%, `None` 3.6%, `Fixed` 1.8%, `Intern` 0.5%. Making
  the skip safe means storing `employment_type` beside the description in the ADR-0050 store —
  a change to that store's record shape, and its own ADR.

## Projection

Two ratios were measured and they differ, so take the conservative one. The **2.3x** above is the
detail-pass throughput ladder; the end-to-end `fetch_raw` A/B in the same session gave **1.48x and
2.08x**, because the listing is serial in both arms and dilutes the gain. Production is slower than
this bench either way — its async arm ran 3.28 req/s where the bench ran 4.39 — so CI is not the
bench.

At **2.08x**, apple's detail pass 1,884s -> ~906s and the Board lands ~1,021s instead of 1,999s
(at 2.3x, ~935s). Either way, in run `35203211704` shard 11 falls to ~17.6 min, below shard 6's
1,268s = 21.1 min, so the scrape max moves to shard 6: **33.9 min -> ~21 min**, and the wall from
56.2 min to roughly 43.

This is arithmetic on one run's shard table, not a measurement.

**Two things move the baseline underneath it.** `#509` (ADR-0166) landed the tech gate on the
detail pass while this was being measured, so apple now fetches details for its tech subset only —
~70.8% of 6,187, or ~4,379 — which cuts the pass *before* this change multiplies it. And apple's
own listing churns. Confirm on the first run after this merges, reading the
`concurrency apple details @32` line (preserved deliberately — see the Consequences section of
ADR-0167) beside the `slow board apple:jobs.apple.com` total.
