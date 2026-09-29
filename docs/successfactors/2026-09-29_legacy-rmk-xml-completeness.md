# Legacy SuccessFactors `Job-Listing` XML: complete enough to build on? (2026-09-29)

The measurement #536 asked for before any scraper is built. The short answer is no: the XML
alone is not a complete listing, but the career site's own DWR job search is. That search is
the listing and the terminator a build would need. Even so, at about 580 net-new English tech
Jobs, the build is parked.

## The surface

`GET career{N}.successfactors.{com,eu}/career?company={ID}&career_ns=job_listing_summary&resultType=XML`
returns a single-shot `<Job-Listing>`. It has no pagination and no stated total (#536). Each `<Job>`
carries `JobTitle`, `Job-Description` (inline and full), `ReqId`, `Location`, `Posted-Date` and
`filter{N}`/`mfield{N}` pairs.

## Population

Source: the Indeed v3 harvest (`experiment/indeed-harvest/`, local). The finished file holds
2,469 unique postings across **132** `(host, company)` legacy tenants on six hosts: career012.eu,
career10.com, career2.eu, career4.com, career5.eu and career8.com. #536's earlier count of 68
tenants was taken while that harvest was still running.

## Containment of open Indeed postings

Each posting the XML missed had its detail page probed as open or closed.

| XML read | contains | of open postings |
|---|---:|---:|
| default (no `lang`) | 1,876 / 2,469 (76.0%) | — |
| union over each site's own locales | 2,106 / 2,469 (85.3%) | **2,106 / 2,163 (97.4%)** |

- **The default feed is one locale, not the tenant.** `&lang=xx_YY` (or `rcm_site_locale`)
  selects another. The site's locales are listed in the career page's `<input id="rcmlocales">`.
- **Of the 57 open postings the union still misses:**
  - 37 sit on three tenants whose XML is an empty 69-byte `<Job-Listing>` in every locale,
    although their site lists jobs: churchilld (462), EnerSysDP2 (213) and Shawcor (48).
  - 20 are in locales the site doesn't list (hatchassocP `fr_CA`, hotelestur `es_ES`,
    kwssaatse `pt_BR`).
- Neither the XML nor the career page's HTML states a total. So both failures above are
  invisible to a scraper that reads only the XML: it would read a live tenant as empty, or as
  complete while short.

## The complete surface: the career site's DWR job search

The career page drives its search through DWR (`careerJobSearchControllerProxy`) with no login:
1. GET the career page with a cookie jar. Read `var ajaxSecKey="…"` and the engine script's
   `_origScriptSessionId`.
2. POST `text/plain` to
   `/xi/ajax/remoting/call/plaincall/careerJobSearchControllerProxy.{searchJobs|search}.dwr?_s.crb={ajaxSecKey}`
   with the header `viewId: /ui/rcmcareer/pages/careersite/career.jsp.xhtml`. Without `_s.crb`
   the answer is "You are not authorized".
3. `searchJobs(userValues)` returns `postingCount`, and `search(options)` pages the results.
   A page size of 100 is accepted.

Its traps:
- **Select every keyword language.** Each tenant pre-selects different ones: InnoProd all 9,
  prodigiosiP only the page's locale.
- **Serialise arrays without a space after the comma** (`Array:[reference:c0-eA,reference:c0-eB]`).
  With a space, only the first element is read.
- **Treat `postingCount` as an upper bound.** It can exceed what the search lists (groupelact
  states 386 and lists 359), so a completeness check against it needs a tolerance.

With all 35 locales seen anywhere selected, DWR listed **24,222** postings (stated 24,289) across
the 132 tenants, with 0 errors, and contained **2,156 / 2,163 (99.7%)** of the open Indeed
postings. Against the XML union, DWR has 1,033 postings the XML lacks and the XML has 35 that DWR
lacks:
- 723 of the 1,033 are on the three XML-empty tenants;
- 293 are in unlisted locales, and the XML fetched with the locale DWR reports recovers
  293 / 293;
- about 17 are drift between the two reads.

## Feed health

- 132 / 132 answer 200 with a `<Job-Listing>`; there were no HTML soft-404s.
- **34 / 132 are malformed XML.** Every case is an empty-name element (`<>N</>`), on all six
  hosts. ElementTree fails on them, and a regex parse succeeds.
- `[[token]]` template placeholders appear in 40.8% of descriptions, so they must be rendered
  before embedding, as upstream does.

## Value

Over 23,222 unique XML jobs (tech filter v6, `doc_prep.is_english`):
- 2,321 are tech (10.0%), 19,117 English (82.3%), and 2,117 both (9.1%).
- **Most tenants are already served through their RMK front.** 60 of the 132 have an RMK Board
  we serve that carries at least 50% of their tech titles, e.g. Atos (185/186 on
  `jobs.atos.net`) and ExxonMobil (173/173 on `jobs.exxonmobil.com`). Those tenants hold 1,776
  of the 2,321 tech Jobs. Two RMK fronts link their `career4…company=` tenant outright
  (`jobs.hatch.com`, `careers.watco.com`).
- **Net-new English tech: about 580 Jobs**, against 44,103 SuccessFactors rows served today.

## Recommendation (the decision on #536)

**Park.** A build would need all of this:
- DWR across all languages as the listing and terminator;
- descriptions from the per-locale XML, using the locales DWR reports;
- detail pages for the XML-empty tenants;
- `[[token]]` rendering and a regex parser for the malformed feeds;
- a Phenom-style gate, so the 60 tenants whose RMK front we already scrape aren't served twice.

That is add-ats-scraper-sized work plus a cross-surface dedup gate, for about 580 Jobs. Revisit
it if RMK-less SuccessFactors tenants become a coverage priority. This document is then the
starting point.
