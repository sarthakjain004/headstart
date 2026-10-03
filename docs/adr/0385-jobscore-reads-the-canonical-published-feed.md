# JobScore reads the canonical published feed

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** ADR-0017, ADR-0158, ADR-0197

## Context

JobScore publishes full job feeds without credentials. The 2026-10-03 sitemap named 498
Boards; all were live and 270 had openings. Four additional Common Crawl labels were a
retired Board (410) and three aliases returning another `company_code`. The public feed
already includes the description, so a per-posting fetch would buy no missing text.

## Decision

Use the lowercase canonical `company_code` as the Board slug and read
`https://careers.jobscore.com/jobs/{slug}/feed.json` once per scrape. A feed naming another
code is an alias, ineligible under that old label. The scraper fails an unreadable feed;
the prober distinguishes a named empty jobs list from measured 404/410 or canonical aliases.
Use public detail links, avoiding the robots-disallowed application-flow route.

Retain `opened_date`, native department/type/experience, and the full location. Native
Yes/No/Hybrid controls remote. Decode compensation as minor units: divide by 100 for the
seven measured fractional currencies, but preserve JPY units (imgix's ¥10–20 million).
Use the shared structured salary codec; a lone maximum provides no floor. No detail pass
or source-side tech gate is needed. Process-wide starts are 1.5 seconds apart, and the daily
pipeline remains below the vendor's hourly per-Board guidance.

Enable it: 12,978,552 bytes / 221 tech postings ≈ 59 KB per tech posting, versus ADR-0158's
~2 MB bar. Exclude seven confirmed integration/test Boards. See the
[measurement](../jobscore/2026-10-03_public-api-measurement.md) for sample fields and limits.

## Alternatives considered

HTML/job-detail scraping adds requests and boilerplate to a complete feed. Treating every
published label as distinct would duplicate renamed accounts. Treating edit timestamps as
publication dates would make old roles look new.

## Consequences

Sitemap discovery is reproducible via `mine_jobscore.py`; Common Crawl and fingerprinting
also recognize the public paths. An unmeasured pagination ceiling or rate knee is not claimed.
New salary dispatch keys affect no existing served rows. Served-row checks follow the normal
pipeline; this build does not dispatch it.
