# Taleo Enterprise Career Section measurement

Measured 2026-09-13 before implementing a HeadStart Enterprise scraper.

## Scope

Enterprise Career Sections use a tenant-specific URL such as
`https://drhorton.taleo.net/careersection/2/jobsearch.ftl?lang=en`.
Oracle documents `jobsearch.ftl`, `jobdetail.ftl?job=...`, and `jobapply.ftl?job=...` as Career
Section URLs. This is a distinct product surface from TBE's `*.tbe.taleo.net/.../v2` URLs and
Oracle Recruiting Cloud.

## Listing API

The shell is HTML but listings are not server-rendered. D.R. Horton, Valero and TTEC shells expose
a numeric `portalNo`, then accept:

```
POST /careersection/rest/jobboard/searchjobs?lang=en&portal={portalNo}
Content-Type: application/json
X-Requested-With: XMLHttpRequest
tz: GMT+00:00
```

with the Career Section's full default-search JSON body. A GET returns 405; incomplete guessed
POST bodies return 400/500. The successful response contains `requisitionList`, `pagingData`,
`facetResults`, and `supportedLanguages`. Each row's `column` is positional, so the shell's own
header sequence must label department/employment/date values; it is not provider-global.

## Pagination

D.R. Horton reported `totalCount=594`, `pageSize=25`: page 24 returned 18 rows, and page 25
repeated the final 18 rows. TTEC returned 24 rows on page 1 while reporting 115 total rows.
Therefore neither a short page nor an empty-overflow probe is a terminator. The walk must stop from
the provider's reported total and deduplicate native job ids as a guard.

## Detail pass

`jobdetail.ftl?lang=en&job={jobId}` is public and D.R. Horton's response was 112 KB. It contains
the full description and qualifications plus schedule, primary/other locations, and a precise
posting timestamp; the listing response lacks this body. The detail HTML initialises visible fields
from its `initialHistory` payload, so this is a mandatory detail pass.

## Rate limit

No rate-limit conclusion is claimed yet. The live listing probes and a 16-detail concurrency probe
did not produce a completed measurement record, so initial implementation must use a conservative
detail width and log all settled detail outcomes for a measured follow-up.

## Sources

- [Oracle supported Career Section URLs](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24d/otrdc/c-taleo10supportedurls.html)
- [Oracle Career Section URL documentation](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/21d/otcug/c-careersectionurl.html)
- [Existing JSON-endpoint implementation description](https://apify.com/usestring/taleo-jobs)
- [Historical public request payload and pagination example](https://stackoverflow.com/questions/41499281/scrapy-not-loading-entire-page-or-i-have-bad-code)
