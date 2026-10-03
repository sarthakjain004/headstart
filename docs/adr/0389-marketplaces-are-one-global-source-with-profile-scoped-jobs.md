# Marketplaces are one global source with profile-scoped jobs

**Status:** accepted · **Date:** 2026-10-04 · **Relates to:** ADR-0012, ADR-0017, ADR-0023, ADR-0031, ADR-0157, ADR-0201

## Context

HeadStart normally reads one employer-owned Board from an ATS. Instahyre instead publishes one
anonymous marketplace feed: its 2026-10-03 listing reported 12,919 current jobs, capped each page
at 35 rows, and supplied a numeric Instahyre employer profile next to every job. A public detail
endpoint adds the description, locations, `is_active`, internship signal, and numeric work
experience bounds.

The profile endpoint cannot define a Board: the five measured profiles stated 7, 54, 67, 2 and
405 jobs while returning only 7, 10, 10, 2 and 10 rows. The full global listing is therefore the
only complete membership surface. A five-profile / 114-current-row / ten-detail sample found no
profile-name collision, but eight details named a recruiting agency rather than the hiring
company. Instahyre exposes neither original ATS provenance nor a verified employer domain.

The cap-safe listing union returned all **12,919** rows and **9,223** title-gated tech Jobs
(71.39%). It took 1,032 listing/function-catalog responses and 42,633,835 bytes. Five live detail
responses averaged 3,281.8 bytes, projecting 42.4 MB for one detail per listed Job. The combined
~85.0 MB / 9,223-tech-Job estimate is **~9.2 KB per tech Job**, far below ADR-0158's 2 MB bar.

The user authorized public marketplace ingestion, Instahyre's public job URL as the application
destination, HeadStart first-seen as the recency fallback, and salary extraction from description
text rather than an invented native field.

## Decision

Add `marketplace` as a Scraper source kind. An ATS source reads one employer-owned Board;
a marketplace source reads its platform-wide public feed and owns the application route. The
first adapter is `instahyre`, whose one liveness row and Board key are `instahyre:global`.

The adapter fetches the first list page for `meta.total_count`, then reads the independently
addressable 35-row offsets at a bounded width of 32. The unfiltered route rejects offset 9,975
with `400` despite that total, so its accessible 9,975-row prefix is unioned with every triple of
the 107 public job-function IDs (the API permits at most three). The resulting listing union
matched all 12,919 rows the global feed reported. It keeps a shortfall as an unauthoritative read.
The public detail endpoint runs through the normal detail-loss accounting. Both listing and detail
traffic opt into spare-egress fallback on a 429: the direct route later returned `429` with
`Retry-After: 55` during a distinct-job crawl, so retrying its spent origin is not sufficient.

Each Job keeps `marketplace_employer_id` from the list row and the list-row company name. That
identifier is an **Instahyre employer profile**, never a verified company, legal entity, ATS Board
identity, or cross-source deduplication key. A changed canonical profile name is an identity
review signal, not an automatic merge. The Job URL is Instahyre's public page. `posted_at` stays
unknown; the existing first-seen recency fallback applies. Full-time salary stays unknown and the
description extractor remains the only salary recovery path. A stipend without currency and
period is not normalized as salary.

## Alternatives considered

One Board per Instahyre profile would produce intuitive Board deltas but silently omits all jobs
past the profile endpoint's ten-row cap. Treating the feed as a standard ATS Board misstates
ownership and hides the non-employer application flow. Refusing marketplaces entirely preserves
the old scope but discards a public, complete listing surface the owner explicitly approved.

## Consequences

Instahyre is an active source with one global liveness row. Its company names remain display data
and may overlap employer-owned ATS Boards; no automatic cross-source deduplication is claimed.
Future public multi-employer application platforms, such as Wellfound if measured, can declare
the same `marketplace` source kind without changing the Scraper interface.
