# JobScore reads public HTML without polling feeds

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** ADR-0017, ADR-0158, ADR-0197, ADR-0201

## Context

JobScore's complete published feed asks for at most hourly polling. HeadStart's chained runs,
direct scrape commands and reusable assignments can run more often. Optional cost telemetry
cannot enforce a durable runtime limit. Its public HTML board and posting routes are permitted
by robots and expose the same public cards and complete posting text.

## Decision

Every scraper request reads public HTML; no production fetch path requests a feed. Parse the
Atom link only to identify the canonical Board label. Read the complete card list, then public
posting pages through the shared detail pass. A native id is the 22-character suffix in the
posting path (1,833/1,833 observed). Serve the exact provider-host route, browser-verified on
JobScore and the vanity-domain Pricefx board.

Published cards can be stale: 20 attempted Boards (16 seeded random hiring Boards, largest
Board and three metadata controls) yielded 19 readable boards, 281 advertised postings and
73 reachable Jobs; 208 links returned 404. A browser click on the provider's exact Pricefx
anchor also failed. Omit confirmed 404/410 postings; keep transient detail failures as listed
Jobs with missing detail. An unreadable listing fails instead of certifying zero. Liveness
counts advertised cards; reachable posting counts are reported separately.

The visible description works with or without JSON-LD. Read publication date and native
major-unit salary only where the page states them. Preserve full location, department and
employment type; Hybrid is unknown. No title-only gate or held-description skip: details can
supply department, date and salary. No publication date or experience category is invented.

Enable: 3,018,589 request-body bytes / five returned tech Jobs ≈ 0.60 MB per tech Job, below
half of ADR-0158's ~2 MB bar. Seven confirmed integration/test Boards are excluded. The
[measurement](../jobscore/2026-10-03_public-html-measurement.md) records field coverage and
bounded concurrency results.

## Alternatives considered

A feed-only scraper is cheaper but incompatible with unrestricted direct/chained fetch paths
without a new mandatory durable state mechanism. A planner-only timestamp gate fails when
telemetry persistence fails or assignments are reused. A new cross-run feed cache would add
unnecessary state while the permitted public surface works. Treating every advertised card
as a reachable job would serve confirmed dead links.

## Consequences

The scraper spends one posting-page request per advertised Job and reports only reachable or
transiently unresolved postings. The HTML sample proves a viable cost, not universal routing
or a vendor maximum. Served-row verification follows the scheduled pipeline.
