# ADP Workforce Now career center — live API measurement

**Date:** 2026-09-23 · **For:** `scrapers/adp.py` (ADR-0180) · The raw captures and probe
scripts are kept locally, not committed; every number a reader needs is inlined here.

Every number below was taken from `workforcenow.adp.com` in this session. The upstream
implementation (`kalil0321/ats-scrapers`, `scrapers/adp.py`, MIT) and its 342-row seed list were
the starting hypotheses; five of its assumptions were wrong (§Listing, §Detail).

After the rate-limit measurement every probe went through one process-wide pacer spacing request
starts at 0.4 s, the pacing the scraper ships with.

## Samples

| sample | size | used for |
| --- | --- | --- |
| seed list page 1 (`$top=100`) | 342 tenants, 200 answered before the rate limit, 2,069 rows | listing fields, tech share, salary shapes |
| lang census | 200 seed Boards x 4 langs + content-links; 155 clean (45 hit local connection resets) | languages, the dead-vs-empty rule |
| detail census | 120 postings over 60 Boards (stopped by a local connection reset) | detail fields and sizes |
| pagination | Lifemark Health Group, 460 postings, the largest seed Board; walked twice | page clamp, `$skip`, terminator |
| multi-career-center | 12 `cid`s the pool lists under more than one `ccId` | whether `ccId` is an address |
| pool sample | 60 random non-seed rows (Indeed harvest) + 50 older third-party rows | real departed tenants |
| rate limit | ~1,700 requests across 195 tenants in five runs | the window |
| `client-features` | 120 centers (60 seed, 60 non-seed), plus 5 rendered in Chromium | the company name |
| content-links `Locale` | 45 Boards (all 26 multi-language or non-`en_US` ones in the lang census, 19 more) | a center's languages |

## Identity (Q1, Q2)

- A Board is a **career center: `cid` (client GUID) + `ccId`**, both query values on one fixed host
  (`recruitment.html?cid=…&ccId=…`). Slug: `{cid}/{ccId}`. `cid` is **case-sensitive** — an
  uppercased real GUID answers 404. No `:` in either part.
- **`ccId` is an address, not only a filter.** Of 12 multi-career-center `cid`s, 8 had postings: 4
  pairs disjoint, 3 non-default centers wholly contained in the default `19000101_000001`, 1 partial
  (13 of 31 shared). Omitting `ccId` equals `19000101_000001` on all 8. So one employer can publish
  one posting on two Boards. 95 of 2,811 pooled `cid`s (3.4%) carry more
  than one `ccId`.
- **`lang` is a filter, not part of the identity** (see Listing). 237 of 281 career-center URLs
  found in local harvested text carry no `lang` at all, so it cannot be read from discovery.
- Every discovery source reads a URL through one function (`wayback_feeder.extract`, style `adp`),
  which emits `{cid}/{ccId}` and a canonical `recruitment.html?cid=…&ccId=…` URL and drops `lang`,
  `jobId`, `source`, `type`, `selectedMenuKey`. 0 of 281 harvested `cid`s were uppercase.
- `workforcenow.cloud.adp.com` (upstream's second host) answers **500** on both the API and the page.

## Listing (Q3–Q7)

`GET /mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?cid&ccId&lang&$top&$skip`
— the JSON call the career-center page makes itself. No sitemap or RSS surface was found; the page
is a client-rendered SPA whose raw HTML carries no postings.

- **`$top` clamps at 20, silently.** 47 of 195 seed Boards returned 20 rows when asked for 100
  (totals 21–461); `$top=21/50/100/1000` all return 20.
- **`$skip` is 1-based** (`meta.startSequence` echoes it). `$skip=0` returns 19 rows; `$skip=1` and
  no `$skip` are identical. Upstream's `0, 19, 39, …` walk reads row 19 twice.
- **`meta.totalNumber` is the terminator.** Lifemark: 460 of 460 read in 23 pages, two walks
  identical; one page past the end is `{"jobRequisitions": []}`.
- **`lang` is a filter (Q5).** Lifemark answers `{"jobRequisitions":[]}` (22 B, no `meta`) under
  `en_US` or no `lang`, 460 under `en_CA`. Across 155 cleanly-measured seed Boards: 129 post only in
  `en_US`, 10 only `en_CA`, 6 `en_CA`+`fr_CA`, 4 `en_US`+`es_US`, 2 in both `en_US` and `en_CA`
  (Canduct 8+9 overlapping, Klipboard 81+4), 1 only `fr_CA`, 3 nothing. Postings: `en_US` 2,833,
  `en_CA` 602, `fr_CA` 89, `es_US` 14 — **2.9% non-English**. A translated posting keeps its
  `ExternalJobID` across languages (Cox & Palmer 577400 in `en_CA` and `fr_CA`).
- **No description on the listing** (`requisitionDescription` on 0 of 2,069 rows).
- **Hidden rows (Q7):** `InternalPostingFlag` false on 2,069/2,069. The rendered 2Life board reads
  "Current Openings (11 of 11)" against the API's `totalNumber` 11.

## Dead versus empty (Q8, Q9)

| request | answer |
| --- | --- |
| unknown `cid` (2 random GUIDs, a malformed one, a real one uppercased) | **404**, 150 B openresty HTML |
| real `cid`, invented/malformed `ccId` | 200 `{"jobRequisitions":[]}`, 22 B, no `meta` |
| real `cid`, a language the Board does not post in | the same 22 B |
| real career center with nothing open (Bowman and Brooke, all 4 langs) | the same 22 B |
| real career center with postings | 200 with `meta.totalNumber` |

`v1/content-links/career-center` separates them, but not by its link count — 7 of 152 real hiring
centers return zero links. It does by its `PublishedIndicator`: **true** on every real center
checked (2Life, the three empty seed centers, CHM Hotels with zero links, and the 5 metaless
non-seed pool rows), **false** on an invented `ccId` (whose `stringFields` are also empty). It
404s an unknown `cid` exactly as the listing does, so the prober asks it first:

| content-links answer | verdict |
| --- | --- |
| 404 | DEAD — the `cid` is unknown |
| 200, `PublishedIndicator` false | DEAD — no such career center for this client |
| 200, published | LIVE, count = sum of `totalNumber` over its `Locale` languages |

No departed client was observed among 110 non-seed pool rows (60 Indeed-harvested, 50 from older
third-party lists): all answered 200 — 105 with postings (89 under `en_US`, 16 only under
`en_CA`), 5 metaless under both and published. `ClientClassification` "Client Term" is not a dead
signal: all 8 "Client Term" centers in a 120-center sample still listed postings (1-16 each).

**Languages.** The same content-links body lists the center's languages as `Locale` values. Over
45 Boards (every multi-language or non-`en_US` one in the clean lang census, plus 19 `en_US`
ones) the list covered every language with postings on 45 of 45 (it names a superset on some —
`en_CA+fr_CA` where only `en_CA` posts); `de_DE` and `ko_KR`, the other two languages the page's
own `langMap` supports, held no posting on any of the 45. The list is identical whatever `lang`
the request carries (3 Boards x 3 variants).

## Detail (Q10–Q12)

`GET …/job-requisitions/{ExternalJobID}?cid&ccId&lang` — the call the job page makes.

- It returns the listing row **plus `requisitionDescription` and nothing else**: 120/120 details
  add exactly that key; title, `postDate`, pay and locations equal the listing on 120/120.
  Description present on 120/120 (HTML; chars p10 3,740 / p50 7,420 / p90 21,832 / max 118,294).
  Upstream's 25,000-char cap is its own choice.
- A posting that is closed, unknown, or asked under the wrong `lang`/`ccId`/another tenant's `cid`
  answers **200 with a ~1.2 KB skeleton** — no title, no description. A silent empty, not an error.
  The detail needs the posting's own `lang`: an `en_US` posting answers with no `lang` at all,
  but an `en_CA` (Lifemark) or `fr_CA` (Avocats sans frontières, Canadian Cancer Society) posting
  answers the skeleton with no `lang` or with `en_US` — 3 of 3 Boards. The correct `ccId` may be
  omitted.
- Charset: `application/json;charset=UTF-8`; non-ASCII arrives as HTML entities (`&eacute;`).
- **Native id: `ExternalJobID`**, all-digit and unique within its Board on 2,069/2,069 rows.
  `itemID` (upstream's id) contains `:` on 8 rows (`HRB:2048292:11346772`) and would split wrong
  in `board_identity.board_of`.
- **Tech gate (Q12): exact.** No department exists on either surface (`HomeDepartment` empty on
  2,069/2,069 listing rows and 120/120 details; `organizationalUnits` always empty) and the detail
  overrides nothing, so `is_tech(title, None)` on the listing is `filter_tech`'s own question.
- **ADR-0048 skip: safe** — the detail supplies only the description.

## Fields (Q13–Q18)

- **Dates (Q13):** `postDate` (ISO-8601, minute precision, tenant offset) on 100%; identical on
  120/120 postings between the listing fetch (~19:20) and the detail fetch (~20:25) — not
  fabricated relative to now.
- **Remote (Q14):** no native field. 27 of 2,897 location strings read `Remote- …`
  (`Remote- National, US`); 7 titles say remote. `is_remote(location)` is the only signal.
- **Salary (Q15):** `payGradeRange.{minimumRate,maximumRate}.{amountValue,currencyCode}` on
  1,080/2,069 (52.2%), always with `SalaryType` `HR` (668) or `AN` (412) and `SalaryRangeType`
  `RANGE`. Currencies USD 970, CAD 109, ZMW 1. Traps: `Up to 43.46 (CAD) Hourly` arrives as
  **min 0.0**, max 43.46 (a lone ceiling); `18.00 To 20.00 (CAD) Annually` is hourly money
  labelled annual by the tenant.
- **Experience (Q16):** no native field. **Employment type:** `workLevelCode.shortName`, free text
  per tenant, 83.7% populated, 126 distinct values; `employment_type.flags` reads none of
  `Seasonal` (23), `Casual` (14), `Temporary` (11), `Student` (10), `FT FACULTY…` (11) — 136 of
  1,731 (7.9%).
- **Location (Q17):** `requisitionLocations[]` — 1 on 1,829 rows, 0 on 52, up to 62 on one.
  `nameCode.shortName` (`" Newton, MA, US"`, leading space) on 2,849 of 2,897 entries; the other
  48 carry only `address.{cityName,countrySubdivisionLevel1,postalCode}` (no country).
- **Department (Q17a):** none (above). `JobClass` (50.5%) is a job class — `Professional`,
  `Clerical`, `Manual Labor`, `Technical` — not a department.
- **Company name (Q18): `client-features`' `ClientName`.** Nothing rendered names the employer:
  5 centers (2Life, Lifemark, Klipboard and two random pool rows) rendered in Chromium through
  the board, a job and the Apply step all titled "Career Center | Recruitment" (Lifemark "Career
  Centre"), with no og: tags, no JSON-LD and only ADP's own logo alt text; no posting field names
  it (`organizationalUnits` always empty). Every XHR the page makes was captured; the one that
  names the employer is `…/staffing/client-features?cid&ccId`, whose `stringFields` carry
  `ClientName` — present on **120 of 120** centers sampled (60 seed, 60 non-seed) and on all 5
  rendered. It is ADP's payroll-client record: against 60 known seed-list brand names, 18 equal,
  16 differ by a legal suffix or case, 26 name a parent or legal entity ("KERRIDGE COMMERCIAL
  SYSTEMS CORP" for Klipboard). 27 of 120 are all capitals. Through `company_name.from_title`'s
  guards 119 of 120 survive; the 65-character "Ontario College Of Pharmacists Ordre des
  Pharmaciens de L Ontario" exceeds the 60-character cap. The same body carries
  `ClientDefaultLocale` (`en_US` 110, `en_CA` 10) and `ClientClassification` (below).

## Operating limits (Q19–Q21)

- **Rate limit: 200 requests per fixed 60-second window, across tenants.** F5 BigIP answers
  `HTTP/1.0 429`, `Server: BigIP`, `Request blockedExceeded requests limit.`, no Retry-After.
  Rested runs at 5 and 8 req/s were refused on exactly request #201; one polled every ~3 s stayed
  429 until ~60 s after the window opened. 450 requests at 3 req/s and 300 at ~1.1 req/s ran clean.
  The 200 spanned 195 different `cid`s.
- **Per IP, and the spare egress cannot reach the host.** A burst on the direct address, 8 requests
  at a time, saw its first 429 in the batch holding requests 201-208 (consistent with the #201
  limit above); in that same window 4 of 20 requests through the local
  Cloudflare WARP SOCKS proxy (an IPv6 WARP address, IPv4 egress 104.28.220.175) answered 200,
  so the meter is not global. The other 16 failed with proxy errors, and WARP stayed unusable:
  5 of 60 requests succeeded across two WARP addresses (one before and one after
  `spare_egress.rotate()`, 2.1 s), the rest "connection to proxy closed", proxy errors or
  timeouts — while google and www.adp.com through the same proxy answered 3 of 3.
  An independent check agreed (through `socks5h://`, 4 of 8 then 0 of 12, each failure a 5 s
  timeout; plain `socks5://`, 0 of 8). The cause is not established — `workforcenow.adp.com`
  publishes no AAAA record, and an edge block on Cloudflare's ranges would fit equally. So the ADP
  scraper does not opt into `egress_fallback_on`, and the liveness pass runs paced on the direct
  route.
- **User-Agent:** `headstart/0.1`, curl's default, `python-requests/2.32` and `Mozilla/5.0` all 200.
- **Sizes:** listing ~2.5 KB per row (5.2 MB / 2,069 rows), so a 20-row page is ~50 KB; detail
  mean 16.8 KB, p50 11.7 KB. Median listing latency ~0.9 s.
- TCP refusals within ~10 ms came and went several times this session. They are local, not ADP's:
  `static.workforcenow.adp.com` (Akamai, never loaded by any probe) refused 17 of 30 attempts in
  the same minute `workforcenow` refused 17 of 30, and the Archive and Common Crawl refused alike.

## Population (Q22–Q24)

- **Tech share: 116 / 2,069 = 5.6%** (`is_tech(title, None)`, first 20 rows of 200 Boards).
  Postings per hiring seed Board: median 10, mean 22.3 (4,346 over 195).
- **Language:** 2.9% of postings are `fr_CA`/`es_US`; 1 of 155 Boards posts only in French.
- **Not a skin.** Workforce Now is ADP's own ATS; no Board here is backed by an ATS already held.
  The overlap that exists is *within* ADP — one employer's career centers sharing postings (§Identity).

## Discovery and the ledger

No vendor roster exists and the Board key is two query values, so every source reads career-center
URLs through one extractor (`wayback_feeder.extract`, style `adp`) that keeps `cid` + `ccId`:

| source | candidate centers | new to the pool |
| --- | ---: | ---: |
| upstream seed list (`kalil0321/ats-scrapers`) | 342 | 342 |
| local third-party company lists (`mine_adp.py`) | 263 | 255 |
| local Indeed harvest (`mine_adp.py`) | 2,432 | 2,325 |
| Wayback CDX, all 186 pages of `workforcenow.adp.com` | 22,232 | 20,030 |
| Common Crawl, 33 indexes `CC-MAIN-2023-40`..`2026-39`, off `data.commoncrawl.org` | 16,822 | 667 |
| **pool** | | **23,619** |

The Common Crawl count never went flat inside the three-year cap (each index added ~300-1,100
centers, the last still +460), so older indexes likely hold more; 16,155 of its 16,822 were already in the pool.

**The ledger holds all 23,619 centers**: 22,027 live, 1,005 dead, 587 unknown, with 15,318
hiring and 287,619 postings (per-language totals summed). A local paced pass on one IP settled
13,762; 15 GitHub Actions runners (15 IPs) probed the other 11,260 in 12.7 minutes without a 429,
agreeing with the local pass on all 1,403 centers both probed. Among the hiring centers, 2,378
were found by Wayback alone, 657 by the Indeed harvest alone, 556 by Common Crawl alone, 3 by the
third-party lists alone and 1 by the seed list alone. Spot checks against the host: the first
snapshot's 495 dead rows re-probed dead, 10 local live rows re-probed live (one count 5 -> 4), and
5 dead, 5 live and 5 unknown rows of the Actions half re-probed the same. Two rules came out of probing the whole pool: a DNS failure on the fixed host
is UNKNOWN, never DEAD (the resolver failed mid-pass on 2026-09-24 while the host kept answering),
and a center whose listing answers 403 "Job listing is not allowed for external candidates." is
DEAD (8 of 40 sampled unknowns; 157 of the Actions half's 744 unknowns on re-probe). The 587
unknowns left are ADP answering 500 on both calls (32 of 40 sampled).

## Where a posting really lands

A company careers page followed in Chromium: 2Life Communities and
Klipboard link straight to their `recruitment.html?cid=…&ccId=…` career center, and the rendered
board matched the API's count. Canadian Cancer Society's and Lifemark's landing pages carry no ADP
link at all (it sits deeper or behind script). The job URL
`recruitment.html?cid&ccId&lang&jobId={ExternalJobID}` renders the posting, with or without
`source=`; the page itself fetches the detail by `ExternalJobID`. Indeed's links use
`jobId={ExternalJobID}&source=IN`.

## The other ADP product (follow-up, not this scraper)

ADP Recruiting Management (`myjobs.adp.com/{slug}/cx`, `recruiting.adp.com`) is a different
platform: an Angular SPA whose site config is `myjobs.adp.com/public/staffing/v1/career-site/{slug}`
(it names `clientName` and `orgoid`) and whose listing is
`my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters` — `400
Missing orgoid header`, then `postingChannelId not found` with `orgoid` set; three guesses at the
channel id failed. Different host, slug and API. 549 distinct `myjobs.adp.com/{slug}` slugs appear
in local harvested data.
