# ADR-0177: An `unknown` re-probe keeps a `live` verdict

**Status:** accepted, provisional · **Date:** 2026-09-23 · **Amends:** [ADR-0012](0012-liveness-ledger.md)

## Context

`check_liveness` wrote `unknown` over any Board still inconclusive after its last pass, including
a Board whose prior verdict was `live` and was only due its routine TTL re-probe. `unknown` is not
`live`, so the Board left `load_active_companies` — the Scrapable set and `index prune`'s keep-set
alike — and the next run's prune evicted every one of its rows as off-Board, with no grace period.
For the served index, one blip behaved exactly like `dead`. It has happened at scale: the per-host
429 breaker turned all of workable UNKNOWN in `ed808f7a` (14,360 live → 16,638 unknown), and it
stayed that way for 55 days.

Only the offline prober, run by hand from the user's machine, writes this ledger; the pipeline
never does. So the fix belongs in the prober, and it takes effect when the next prober run is
committed.

## Decision

When a re-probe ends `unknown` and the row's prior verdict is `live`, the prior row is kept as-is
— status, job count and `checked_at` unchanged. Its `checked_at` is already past the live TTL, so
the next prober run probes it again; a Board that is really gone reaches `dead` through a
conclusive probe, never through an inconclusive one. A Board never seen live still records
`unknown`.

## Provisional: the alternative the user may prefer

The user may later want a Board whose re-probe ends `unknown` taken **out of scraping**: record
`unknown`, but keep its Boards in prune's keep-set so its rows survive while it is not scraped.
That protects the rows without spending scrape time on a Board that may be failing. The tradeoff
is that those rows are kept and never re-checked — nothing scrapes the Board, so none of its
Jobs can be confirmed or evicted, which is the no-drain shape of ADR-0053's scope exclusion. This
ADR chose keep-scraping because a scrape is itself the re-check.
