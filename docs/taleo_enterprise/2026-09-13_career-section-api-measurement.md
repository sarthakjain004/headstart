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

The detail vector is not globally positional. D.R. Horton exposed 41 labelled values with job
field, schedule and posting date; TTEC exposed 26 values whose description begins at a different
position and has no job-field/schedule label. The scraper pairs each page's own `_hlid` labels with
its value vector and leaves unavailable fields null rather than using a fixed offset.

## Rate limit

A controlled D.R. Horton ladder issued 16 distinct detail pages twice at each width. Widths 8 and
16 were clean (32/32 HTTP 200 each); width 32 produced one 30-second timeout. Widths 1 and 4 also
had isolated timeouts, so the failure is tail instability rather than a conventional 403/429/5xx
rate wall. Median successful latency stayed about 1.05 seconds. The scraper therefore uses 16
workers, the highest clean point; raw outcomes are in
`experiment/taleo-enterprise-rate-limit/artifacts/detail_ladder_v2.jsonl`.

## Discovery, liveness and duplicate check

The fixed 2026-09-13 Common Crawl + partial Wayback cut contains 7,442 canonical Career Section
URLs. Its completed liveness ledger has 556 `live`, 141 `dead`, and 6,745 `unknown` rows. Unknown
is deliberately retryable: only DNS and definitive 404/410 responses are dead signals for this
surface, so a non-JSON shell, timeout, or other transient must not bury a board.

A host-stratified sample of 129 unknowns classified 65 as DNS failures, 60 as 200-response shells
without the measured `portalNo` contract, two as timeouts, and two as non-JSON listing responses.
That is a sample classification, not a population estimate; it supports retrying the latter three
classes and does not indicate an Enterprise rate-limit wall.

The candidate pool has zero duplicate canonical URLs, zero duplicate `(host, Career Section)`
identities, and the full redirect dry-run over all 556 live Boards found zero alias clusters and
zero Boards to bury. `TaleoEnterpriseScraper.alias_key()` therefore returns the full canonical
Career Section URL rather than a shared `*.taleo.net` host, so future redirects are compared in
the same identity space as the ledger.

## Sources

- [Oracle supported Career Section URLs](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24d/otrdc/c-taleo10supportedurls.html)
- [Oracle Career Section URL documentation](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/21d/otcug/c-careersectionurl.html)
- [Existing JSON-endpoint implementation description](https://apify.com/usestring/taleo-jobs)
- [Historical public request payload and pagination example](https://stackoverflow.com/questions/41499281/scrapy-not-loading-entire-page-or-i-have-bad-code)
