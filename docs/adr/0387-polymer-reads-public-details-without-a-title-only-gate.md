# Polymer reads public details without a title-only gate

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** ADR-0017, ADR-0158, ADR-0197, ADR-0201

## Context

Polymer documents an unauthenticated public hiring API, distinct from its private Customer
API. Its listing omits the description and detail-specific department. The 20-largest-Board
sample read 202 postings and details, including 34 tech matches, with no losses. A title-only
gate could discard jobs promoted by the department, so it has no measured recall guarantee.

## Decision

Key a Board by its lowercase public organization label. Fetch all listing pages from
`/v1/hire/organizations/{slug}/jobs`, then each public detail through the shared detail-pass
seam, with no tech gate or held-description skip. A failed detail preserves the listed Job.
`meta.count` counts postings and `meta.total` pages. Asking beyond the end returned empty
items but a non-terminal next-page flag, so the walk uses count/page bounds, stops on no new
ids, and marks an incomplete or failed remainder truncated.

Use `published_at`, the native employment phrase and company. Retain every remote country
restriction and restriction city. Hybrid remains unknown for the remote-only filter. Keep
listing salary when the detail omits it; expand K, normalize periods and encode via
`salary.to_field`. Register the existing structured decoder to avoid rounding fractional
hourly amounts before annualization. Serve the verified vendor-host posting route even
when the feed's URL names a vanity host.

A named empty feed is live-empty; only the two measured 422 missing-organization/careers-page
bodies are dead. Other failures are unknown. The initial census settled 410 labels; recovered archives bring it to 419: 225 live,
194 dead, no unknowns. Exclude the documentation's Aperture Labs demo after reading its
corporate-ipsum description. A 120-request ramp through 2/4/8/16 concurrency returned only HTTP 200. Mixed-board throughput flattened after eight, so detail width is eight with eight starts/s. No real multi-page Board was established.

Enable it: 384,986 listing bytes plus ~6.6 KB per detail projects ~2.9 MB for all 380 sampled
postings / 91 tech jobs ≈ 32 KB/tech, well below ADR-0158's ~2 MB bar.

## Alternatives considered

Listing-only ingestion loses every description. A pre-detail title/category gate is unsafe
while detail department differs. Trusting `next_page` past the advertised total loops forever.
Private Customer API credentials are unnecessary for public jobs.

## Consequences

The scraper spends one detail request per Job; its measured cost is small enough to preserve
recall. Shared salary behavior is unchanged for existing ATSes. See the
[measurement](../polymer/2026-10-03_public-api-measurement.md); new served rows can be validated
only after the scheduled pipeline includes these Boards.
