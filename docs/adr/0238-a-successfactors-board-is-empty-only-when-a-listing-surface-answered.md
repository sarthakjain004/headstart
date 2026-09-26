# ADR-0238: A SuccessFactors Board is empty only when a listing surface answered

**Status:** accepted · **Date:** 2026-09-26 · **Amends:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (for SuccessFactors)

## Context

A SuccessFactors Board is read from up to three surfaces: the `/sitemap.xml` urlset, the `/search/`
walk and, when the sitemap is a feed, the RSS stream. Each one mapped its own non-200 to "nothing
listed" and fell through, so a Board whose every surface failed returned `[]` and was scraped
clean. Run 36218633315 read 19 Boards that way off `sitemap HTTP 429, search HTTP 429`, and about
260 live ids went into ADR-0083's grace period, where one more throttled scrape evicts them.

## Decision

`fetch_raw` raises when nothing was listed and no surface *answered*. A surface answers when it
comes back with a 200 urlset, feed or search page, or with a status that says the Board is gone
(400, 404, 410). A 403, 429 or 5xx is a failed fetch. So is a 200 sitemap that is neither a
urlset nor a feed (a sitemap index or a corporate page), because it lists nothing either way. The
raise makes the Board an error, so ADR-0053 keeps it out of the eviction scope for that run.

403 counts as a failed fetch because it is a block, not an answer: `jobs.witron.com` answered 403
on both surfaces in CI on every run and returned 261 postings from this machine on 2026-09-26.

## Consequences

* A gone tenant still reads as a clean empty Board and drains through eviction as before
  (`jobs.vibrantm.com` answers 400 on both surfaces). It still cannot earn an ADR-0058 gone-verdict.
* A Board whose `/search/` redirects to a non-RMK page that answers 200 still reads as empty.
  `jobs.sap.com` does this now: its sitemap is an index and `/search/` 301s to a new `/en/jobs/`
  site. So do Boards that moved to another ATS (`careers.dovercorporation.com` to Workday) or were
  decommissioned onto `www.sap.com`, and those should evict. This rule does not tell them apart.
