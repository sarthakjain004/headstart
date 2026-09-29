# ADR-0268: A served posting date is never later than the day we first saw the Job

**Status:** accepted · **Date:** 2026-09-29 · **Supersedes in part:**
[ADR-0061](0061-refreshable-metadata.md) (its invariant that the table's metadata always equals
the store's: the served `posted_at` is derived from the store's date and the row's `first_seen`) ·
**Relates to:**
[ADR-0031](0031-first-seen-index-stamp.md) (`first_seen`),
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) (`posted_at_comparable`),
[ADR-0061](0061-refreshable-metadata.md) (the metadata refresh that carries it, and its amendment that a `None` fact is not an observation) ·
**Issue:** #696

## Context

The "posted within N days" filter, the posted-date sort and the "posted" tag all read `posted_at`
as the company's original posting date. On several ATSes it is not. Measured on served table v277
(498,853 rows, 2026-09-29):

- **35,366 rows have a `posted_at` day later than their own `first_seen` day.** The posting was
  already in our index before the date it now claims. Median lag 21 days, p90 41. Most are
  Workday (14,176) and SuccessFactors (13,986), then iCIMS, Eightfold, Ashby and Phenom.
  Workday already reads CXS `startDate`, and it still moves: these ATSes restamp a posting when
  it is reposted or refreshed.
- **112 rows are dated in the future** (latest 2028-07-01): closing or start dates read as posting
  dates, on SuccessFactors, Radancy, Zoho, Taleo and Phenom.
- **19 Keka rows are dated `0001-01-01` or `1900-01-01`,** null sentinels passed through as dates.

As of 2026-09-28, 76,507 rows answered "posted within 7 days". About 11,000 of them were postings
we had already held for more than a week.

Since ADR-0061's amendment (#735), `posted_at` is also a fact where a `None` is not an observation
(`update_meta._NONE_IS_NOT_OBSERVED`), so a scraper that stopped emitting a junk date would not clear one
already stored.

## Decision

**The table serves `posted_at` bounded by the row's own `first_seen`.** In `index._served_posted_at`:

- An ISO-shaped date on a day after the row's `first_seen` day is served as the `first_seen` day
  (`YYYY-MM-DD`). We held the posting that day, so it was posted on or before it.
- An ISO-shaped date before the year 2000 is served as no date (null).
- Everything else is served as the ATS wrote it: dates on or before `first_seen`, non-ISO strings
  (already kept out of every date filter by the ADR-0173 guard), and rows with no `first_seen`.

The rule lives in `_served_meta`, which now takes the row's `first_seen`. The store's metadata
keeps the raw date. So the served value is derived from (store date, row stamp), and ADR-0061's
metadata refresh compares and rewrites it like every other served column. Rows indexed earlier
are bounded on the first run after this ships, with no migration. `posted_at_comparable` is
computed from the served value.

The bound also stops churn. A refresh date that moves forward every run still serves the same
`first_seen` day, so the row already matches and is not rewritten.

## Alternatives

- **Sort and filter on `min(posted_at, first_seen)` at query time.** The same answer without
  changing the column, but the "posted" tag and the MCP tool would still show the repost date,
  and it needs a SQL `least()` over a mixed-shape string column on every query.
- **Drop the future and pre-2000 dates at parse time in each scraper.** It covers the junk dates
  only, not the repost half. And because of ADR-0061's amendment it would never clear a date already
  stored.
- **Find, per ATS, a field carrying the original posting date.** Still worth doing where one
  exists, and it composes with this rule: an earlier date always survives the bound. But Workday's
  `startDate` already moves, so it is not a fix on its own.

## Consequences

- On v277, 35,366 rows are rewritten once by the next metadata refresh, and 19 lose a sentinel
  date. "Posted within 7 days" would fall from 76,507 rows to 65,551.
- A posting reposted **before** we first saw it still serves the repost date. Only an original-date
  field from its ATS could correct that.
- `first_seen` is our UTC stamp. A posting dated one day later in the company's own timezone is
  served one day earlier (3,243 of the 35,366 rows differ by exactly one day).
- The 41,692 rows with no `first_seen` (indexed before ADR-0031) keep their raw date. Only 1 of
  the 112 future-dated rows is among them.

## Amendment (2026-09-29): one day of slack, RippleHire's dates in ISO

A code review of #838 found three gaps. Measured on served table v45 (500,568 rows) and the
store's `meta.jsonl`, both read off HF on 2026-09-29.

**One day later is kept.** #696 asked to bound dates "later than `first_seen` + 1 day", and the
decision above bounded every later date. `first_seen` is our UTC stamp, and a company east of UTC
dates the same moment a day later in its own zone. So `_served_posted_at` now serves a date that
is exactly one day after the `first_seen` day as written, and bounds only those two or more days
later. Of the 35,801 served rows whose raw date is later than their `first_seen` day, this serves
3,265 as the company wrote them (Workday 1,547, SuccessFactors 1,050) instead of a day earlier.
The other 32,536 are bounded as before.

**RippleHire's dates are ISO.** Where a detail record carries no `publishDetails.CAREER_SITE`,
the scraper fell back to `jobPostingDate`, `26-Dec-2022`. It was the only non-ISO `posted_at` in
the served table (765 rows, 260 of them on nttltd), so no date filter could read those
rows. `ripplehire._iso_date` now writes that day as `2022-12-26`. `posted_at` is a fact, so
`update_meta` refreshes each row when its Board is next read. No migration is needed.

**Not done: each ATS's original posting-date field.** The per-ATS measurement #696 suggested
(Workday `startDate` against the listing, SuccessFactors' page against the sitemap's `lastmod`)
is still open, as #917.
