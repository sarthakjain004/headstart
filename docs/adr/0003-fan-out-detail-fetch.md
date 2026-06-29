# ADR-0003: Concurrent detail fetch via `BaseScraper.fan_out`

- Status: Accepted
- Date: 2026-06-23

## Context

Five scrapers (workday, smartrecruiters, rippling, trakstar, join) each hand-rolled the *same*
bounded `ThreadPoolExecutor` block for the per-posting detail pass — fan one detail GET out per Job,
isolate per-item failures so a single 404/timeout never drops the rest, stash the result back on the
raw item. The concurrency-plus-isolation invariant lived in no module: copied five times, and
exercised by zero tests (the scraper fixtures are post-`fetch_raw` snapshots, so the fan-out path
never ran under test).

## Options considered

- **A — leave it duplicated.**
- **B — a free function in a new `headstart/concurrency.py`.**
- **C — a method on `BaseScraper`.**

## Decision

**C — `BaseScraper.fan_out`, a `@staticmethod`.** Only scrapers fan out detail fetches, and that
stays true as more scrapers are added, so the primitive belongs with the scrapers, not in a general
utility module. A staticmethod (matching the existing `slug_from` precedent on the base) keeps it
ergonomic as `self.fan_out(...)` inside each scraper while taking no instance state — so it is still
tested directly as `BaseScraper.fan_out([...], fn)` with no instance to construct. Module option (B)
was rejected: it implies non-scraper callers that don't exist, and the cohesion win is small when
scrapers are the only users.

## Consequences

Only the fan-out/isolation was lifted. Each scraper keeps its own per-item fetcher (URL build + the
GET-or-default guard) and the `_description`/`_detail` side-channel on the raw item, so every `parse`
is untouched. Folding in the per-item GET guard and removing the side-channel are separate, later
cleanups.
