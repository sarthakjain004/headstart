# ADR-0191: An ADP Recruiting Management Board is a career site, read through its token in the default language

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) / [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a short list is not a complete one), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (company names), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate), [ADR-0180](0180-an-adp-board-is-a-career-center-read-in-every-language-at-one-paced-budget.md) (ADP Workforce Now)

## Context

ADP runs two recruiting platforms. Workforce Now (`adp`, ADR-0180) serves career centers on
`workforcenow.adp.com`. **ADP Recruiting Management** is the enterprise product: an Angular SPA on
`myjobs.adp.com/{slug}/cx`, backed by an API on `my.adp.com`. The first attempt at it
(ADR-0180's measurement doc) was stopped by a 400 `postingChannelId not found` and filed it under
the fingerprinter's unsupported key `adp_recruiting`. Three open-source clients document a working
flow: `amikai/openings-mcp` (`internal/provider/adp_myjobs/`), `Masterjx9/OpenPostings`
(`server/ats/adp_myjobs/service.js`) and `strelov1/freehire`
(`internal/ingest/sources/adpmyjobs.go`). This build started from openings-mcp's flow and measured
each step against the live hosts (`docs/adp_recruiting/2026-09-24_myjobs-measurement.md`). Two of
the upstream choices were wrong for this repo. Nobody had measured the header that decides whether
a Board reads as empty.

## Decision

**1. The ATS key is `adp_recruiting`, and the Board is the career site.** The repo already keyed
this platform `adp_recruiting` in the fingerprinter, so the scraper takes that key rather than
upstream's `adp_myjobs`. A Board is the path word of `myjobs.adp.com/{slug}/cx`. The site record
(`/public/staffing/v1/career-site/{slug}`) answers any casing, and its own `domain` is lowercase
on 681 of 681 sites, so `slug_from` lowercases. An org can run several sites (85 `orgoid`s held
267 of 606 hiring sites). Each site is its own Board, because the token scopes the listing to one
site. Across external sites of one org, 2.9% of rows repeat, and 47 of those are tech. That
overlap is accepted, as ADR-0180 accepted Workforce Now's cross-center overlap, rather than built
a subset-alias ledger for.

**2. The listing is read through the site's token, with its description.** The site record
carries `myJobsToken`. The listing (`…/job-requisitions/apply-custom-filters`) answers 400
without it and needs nothing else (`orgoid`, `rolecode`, `Origin` are inert). freehire's variant,
the plain `job-requisitions` endpoint with only `orgoid`, is refused. It serves the org's whole
requisition set: 284 postings where the site lists 109, including postings no external site
shows. With `$select`, the listing carries the description (99.8% of 77,242 rows). The detail's
description equalled it on 291 of 300 and was never longer. Pages are 50 rows at a 0-based
`$skip`. A page past about 1 MB answers 502, so a 502 halves the page. The walk ends at `count`
unique `reqId`s. A shortfall goes through `mark_truncated_unless_negligible`, and a page still
refused at 5 rows through `mark_truncated`.

**3. Every request states `Accept-Language: en-US`, and the scraper reads the default view.**
The header is a filter, not a preference. The SPA sends `en-US` whatever the browser's locale, and
absent or `en-US` gives the same count on 150 of 150 sites. **curl_cffi's Chrome default,
`en-US,en;q=0.9`, answers `count: 0`** (7 of 7 sites), and the scraper's first live run read
every Board as empty. So `request_headers` sets the header for the scraper and the prober.

A site that offers another language lists some postings only under it: 236 extra postings on
15 of 150 sites, 1.2% of that sample. The 186 read were English. Walking each offered language
was rejected for now, for three reasons:
- A visitor sees those postings only after switching the site's language.
- The offered languages are not stated on the site record.
- It would re-read a multilingual site's whole listing once per language.

This gap is a named follow-up, not a silent loss.

**4. An employee-only site is DEAD by policy.** `settings.careerSiteType` is `"Internal"` on 15
of 681 sites. Their postings are for the client's staff. Of the 14 hiring ones' 3,924 postings,
3,526 are on an external site of the same client anyway. `p_adp_recruiting` writes such a site
DEAD so it never enters the scrape list, following pyjamahr's precedent of dropping
`published_internally` rows. This is the one DEAD verdict not keyed on absence. Otherwise a site
is dead only when its record answers 400 `Careersite not found` or `Careersite is not active`, as
11 real departed seed sites did. A DNS failure is UNKNOWN (one fixed host), and so is a listing
error (`trulitecareers`' 500).

**5. A tech-gated detail pass fills salary only, and ADR-0048's skip is declined.**
`search-meta/{reqId}` (same token) is the only surface stating pay. It carries either the
tenant's `compensationDetails` free text or the pay-transparency min/max amounts. Over 300 tech
postings, 56 carried pay and 39 parse. 33 of those 39 have no salary the description yields. The
gate is exact: `parse` reads `jobTitle` and `organizationalUnits` off the listing, and the detail
title equalled the listing's on 300 of 300. Amounts are emitted as `"40000-141700 USD"` with no
period, which the parser's annual default and plausibility floor protect. The free text passes
through as stated to `salary._field_generic`, so no new `DERIVATIONS_VERSION` bump is needed (a
new dispatch key). The description store's skip is declined because a held description does
not hold the salary.

**6. The company is `clientName`, at no extra request.** The SPA's title is "Career Site"
everywhere. The site record, already fetched for the token, states `clientName` on 681 of 681
sites, and `company_name.from_title` accepts 680. The record's `name` labels the site
("External"). `_VENDOR_ALIASES["adp_recruiting"]` is empty on purpose. ADP is a genuine client
of its own platform (`apply`, 909 postings), and a field cannot fall back to vendor branding the
way a page title can.

**7. It lands active.** No rate limit was found: 2,500 listing requests at c=128 on one site,
and 1,500 at c=64 across 30 sites, all answered 200 (bar 2 read timeouts). So there is no pacer
and no `_GATES` seed, and `detail_workers` is 16. A full sweep reads ~425 MB of listing pages plus
2,504 tech details at 17.3 KB, about 0.47 GB for 2,504 tech Jobs, or **~187 KB per tech Job**.
ADR-0158's bar is ~2 MB.

## Alternatives considered

- **Upstream's key `adp_myjobs`.** Rejected. The repo's fingerprinter, its test and ADR-0180
  already name this platform `adp_recruiting`, and the skill's first rule is to keep the key the
  repo uses.
- **freehire's `orgoid`-only listing, which needs no token.** Rejected. It reads the org's
  requisitions rather than the site's (284 against 109), so it would serve postings no public
  site shows.
- **A per-site cross-language union** (ADR-0180's shape). Deferred, per §3.
- **Scraping employee-only sites.** Rejected, per §4.
- **No detail pass.** Rejected. It would leave salary empty on the ~11% of tech postings where
  only the detail states one, at a cost of 2,504 requests a sweep.

## Consequences

- The ledger holds 1,499 rows from a pool of Wayback (1,394 slugs, 509 found nowhere else),
  Common Crawl across 33 crawls (853, 29 only there) and three seed lists. It has 990 live rows,
  861 of them hiring with 87,181 postings.
- `wayback_feeder.ATS_HOSTS`, `cc_miner.ATS_PATTERNS` and the fingerprinter now emit the
  lowercased site slug. A legacy `recruiting.adp.com` link still detects the ATS with no Board.
- Follow-ups: the non-default-language postings (§3); the filter harness's live run
  (`verify-search-filters`), which waits on a refreshed session cookie and a pipeline run that
  serves these rows; the ~625 rows whose `workLevelCode` is an abbreviation ("FT", "PT 129 or
  Less Hours"), which reach no employment-type filter as stated.
