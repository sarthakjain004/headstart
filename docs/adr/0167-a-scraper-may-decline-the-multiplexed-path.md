# ADR-0167: A scraper may decline the multiplexed path, on a measurement

**Status:** accepted · **Date:** 2026-09-17 · **Amends:**
[ADR-0016](0016-async-fan-out-default.md) (reverses its "Per-ATS default flags" rejected
alternative, for the one case it did not anticipate) · **Builds on:**
[ADR-0015](0015-async-fan-out.md)

## Context

ADR-0016 made the multiplexed async fan-out the default and explicitly rejected per-ATS flags:

> **Per-ATS default flags.** Overkill: one global default plus Trakstar's explicit width already
> covers the only host that needs special handling.

That held for nineteen scrapers and two years of widths. It assumed the only per-ATS variable is
**how wide** to fan out — which `detail_streams` and `detail_workers` already express — and that
the *transport* is never the variable.

`apple:jobs.apple.com` is the counter-example, and it is not a corner case: with
`oracle:ejwl` parked ([`c5984f38`](../pipeline/2026-09-17_five-run-log-review.md)) it is the only
remaining scrape floor Board, 98% of its shard, and that shard was 33.9 min of a 56.2 min wall.

Measured live 2026-09-17, interleaved A/B on fresh ids, three rounds, zero non-200s either way
(`experiment/apple-detail-transport/`):

| transport | req/s |
| --- | --- |
| async, one `AsyncSession`, 16 / 32 / 64 streams | 4.39 / 4.23 / 4.21 |
| sync threads, 32 connections | 9.45 / 9.03 / 11.68 |

Widening the streams does nothing, and the server is not the one refusing — its own SETTINGS frame
advertises `MAX_CONCURRENT_STREAMS = 128`. **This origin meters per connection.** One shared
`AsyncSession` is one connection, so the multiplexed path is structurally the slow one here and no
width setting can reach the fast one.

## Decision

`BaseScraper.async_fanout: bool = True`. A scraper sets it False to decline the multiplexed path;
`async_fanout_enabled()` reads it and stays the single place the policy lives.

Three properties, each load-bearing:

- **Declarative, not an override.** It sits beside `detail_streams` and `detail_workers`, which
  are already per-ATS transport facts. A scraper that reimplemented `async_fanout_enabled()` would
  be a *Refused Bequest* — replacing an inherited policy hook with a constant, and quietly dropping
  the operator's env switch with it.
- **`HEADSTART_ASYNC_FANOUT=0` still wins**, for every scraper at once. Incident response must be
  able to take the whole shard off the async path without reading each scraper.
- **There is no on-switch.** The attribute is set False only on a measurement, so a flag that could
  force such a scraper back onto async would make the slow path reachable by accident, and would
  invite exactly the "tune it in CI" habit ADR-0016's global default exists to prevent.

## Consequences

`fan_out_async` wraps itself in `fanout_stats.batch`; `fan_out` does not, because it is a
`@staticmethod` with no scraper to name. A Board moving to the sync path would therefore stop
emitting its `concurrency {ats} details @N` line — the very line this decision was read from, and
the one a future width decision would need. Apple restores it at its own call site
(`_timed_details`), with its own lock: `fanout_stats.batch`'s callback accumulates into an
unsynchronised dict, which is safe under the async gather because that calls it from one
event-loop thread, and is not safe under a thread pool. **The next scraper to take this path must
do the same, or instrument `fan_out` once in the base and delete both copies.**

The counterfactual is no longer reachable by env var for such a scraper — measuring "what would
async have done here" now needs a code change. That is deliberate, and the measurement above is
recorded so it does not have to be re-run to be believed.

## Alternatives considered

- **`detail_streams = 32`** — the natural-looking knob, and measured to do nothing: it widens
  streams on the one connection, which is precisely what does not bind.
- **A multi-session async path** — several `AsyncSession`s round-robined. It would make the async
  path competitive here and would be the better answer if a second connection-metered origin
  appears. Not built for one Board: today it is speculative generality, and this attribute is the
  cheap thing that can be deleted when that path exists.
- **Leaving Apple on async and parallelising `_listing()` instead** — the listing is ~115s of the
  Board's 1,999s (~6%). It does not reach the problem.
