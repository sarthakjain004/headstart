# ADP Recruiting Management (`myjobs.adp.com`): live measurement, 2026-09-24

ADP Recruiting Management is ADP's enterprise recruiting product, a different platform from
ADP Workforce Now (`adp`, `docs/adp/2026-09-23_careercenter-measurement.md`): its own career-site
host, its own API host and its own Board identity. This note answers the `add-ats-scraper`
measurement checklist against the live hosts. Every figure was taken on 2026-09-24 and carries its
sample size. The probe scripts and raw captures stayed local, in
`experiment/adp_recruiting-myjobs/` (uncommitted), so every number a reader needs is quoted here.

**Where the flow came from.** The first attempt (`docs/adp/…` §"ADP Recruiting Management") got as
far as a 400 `postingChannelId not found` and stopped. Three open-source clients document the
working flow: `amikai/openings-mcp` (`internal/provider/adp_myjobs/`, the most complete, with a
431-site seed list), `Masterjx9/OpenPostings` (`server/ats/adp_myjobs/service.js`, a 545-site seed
list) and `strelov1/freehire` (`internal/ingest/sources/adpmyjobs.go`). They were read as
hypotheses. Two of them were wrong in ways that matter (the `orgoid`-only listing and the missing
`Accept-Language`, below).

## The request flow

1. `GET https://myjobs.adp.com/public/staffing/v1/career-site/{slug}` returns the site record (3.3
   KB). It carries `myJobsToken`, `orgoid`, `clientName`, `name`, `hideDaysPosted` and
   `settings.careerSiteType`. No auth is needed.
2. `GET https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters?$select=…&$top=…&$skip=…`
   with headers `myjobstoken: {token}` and `Accept-Language: en-US`. It returns
   `{count, jobRequisitions[]}`.
3. `GET …/job-requisitions/search-meta/{reqId}` with the same headers returns the posting
   detail.
4. The posting page is `https://myjobs.adp.com/{slug}/cx/job-details?reqId={reqId}`.

## Identity (Q1, Q2)

- **A Board is one career site**, the path word of `myjobs.adp.com/{slug}/cx`. The site record
  answers case-insensitively (`800FlowersCareers`, `800FLOWERSCAREERS` and `800flowerscareers` all
  return the same record), and the record's own `domain` is lowercase on 681 of 681 live sites. So
  the slug is lowercased, and a trailing dot is stripped (`pathgroup.` appeared in a seed list).
- **An org can run several sites.** 85 `orgoid`s held 267 of the 606 hiring sites dumped. The
  sites are distinct records (different `id`, `name` and `externalId`), and the token scopes the
  listing to one site. Across external sites of one org, 2,139 of 73,318 rows (2.9%) repeat a
  `reqId` another site of the same org lists. Only 47 of those rows are tech. In that 606-site
  dump, 31 external sites list a subset of a sibling's postings (`gnc` and
  `generalnutritioncenter`: 753 each, the same ids, that morning). Over the whole ledger's 990
  live sites the alias run buried 131 (§"Sites another site of the same client contains").
- **`reqId`** is all-digit and 13 characters long on 77,242 of 77,242 rows, so it is never split by
  `board_identity.board_of`'s last-`:` rule. Slugs match `[a-z0-9_.-]` on 681 of 681.
- **Discovery spellings.** The seed lists, Wayback and Common Crawl emit the slug as written in a
  URL, sometimes mixed-case (`PathGroup`). `slug_from` lowercases every one.

## Listing (Q3 to Q7)

- **The token is the address (Q3, Q5).** Without `myjobstoken`, with an invalid one, or with another
  site's, the listing answers 400 `{"message":"postingChannelId not found"}` (3 of 3 variants). With
  the token, `orgoid`, `rolecode` and `Origin`/`Referer` change nothing (each dropped alone, and
  all four dropped together: 200, same count). freehire's variant, the plain
  `job-requisitions` endpoint with only `orgoid`, answers **284** for `800flowerscareers`, whose
  site lists **109**. That is the org's whole requisition set, including postings no external
  site shows, so it is not used. A token 62 minutes old still answered (issued 13:25 UTC, read
  14:27 UTC). A fresh record issues a fresh token.
- **`Accept-Language` is a filter (Q5).** The SPA sends `Accept-Language: en-US` on the listing and
  the detail, whatever the browser's locale. This was checked in Chromium with the context locale
  set to `en-US` and `es-US`; both sent `en-US` and rendered "43 jobs found" on `awg`. With the
  header absent or set to `en-US`, the count is the same on 150 of 150 sites. **curl_cffi's
  Chrome default, `en-US,en;q=0.9`, answers `count: 0`**, and so do `en`, `fr` and `*`. That
  was 7 of 7 sites probed, including `lifewisecareers` at 5,776 postings. It is a silent empty, and
  the first run of the scraper hit it on every Board. The scraper and the prober send
  `Accept-Language: en-US` explicitly.
- **The non-default languages hold postings the default view does not (Q5).** A site offering
  Spanish lists some postings only under `es-US`. On 15 of 150 sites, some locale's count exceeded
  the default view's, by 236 postings on a base of 18,942 (1.2%). All 186 such extras read on 7 of
  those sites are in English (`langdetect`). Examples are "QAQC Tech III" on `brownandrootexternal`
  and "Lead Appl. Developer" on `apply`. A visitor sees them only after switching the site's
  language. The scraper reads the default view, as the SPA does (ADR-0202 §3).
- **`$select` decides the fields (Q3a, Q6).** Without it, a row has only `reqId`, `jobTitle`, the
  location arrays, `organizationalUnits` and flags. With it, a row adds `jobDescription` on 99.8% of
  77,242 rows, `jobQualifications` on 47.7%, `postingDate` on 57.8% and `workLevelCode` on 98.2%.
  `jobQualifications` is a separate "Requirements" block. It is contained in the description on
  only 1,851 rows, and the posting page shows both blocks. The listing description equals the
  detail's `requisitionDescription` on 291 of 300 detail pairs, and the detail was never longer.
  `customFieldGroup` and the detail-only fields cannot be selected on the listing (5 names tried).
- **Pagination (Q4).** `$skip` is 0-based: `$skip=1` drops exactly the first row. `count` equalled
  the rows served on 606 of 606 hiring sites. 603 of 606 served no `reqId` twice. On the other 3,
  one posting moved across pages mid-walk, so progress is counted in unique ids. A walk of the
  largest site, `lifewisecareers` (5,777), read 5,777 rows and 5,776 unique ids unordered, and
  5,777 unique ids under `$orderby=postingDate desc` or `reqId`. Re-measured after review, with
  each of those three sites walked twice unordered and twice under `$orderby=reqId`: the 6
  unordered walks lost 0, 3, 1, 2, 2 and 1 postings (9), and the 6 ordered walks lost 0, 0, 0, 0,
  1 and 0 (1). The scraper asks in `reqId` order. `$skip=count` returns an empty page.
- **A page past about 1 MB answers 502 (Q4).** `$top` has no clamp: 1,000 rows came back as one
  694,697 B page on `lifewisecareers`, where rows are small. But 100-row pages failed with 502 on
  the sites whose rows run to 11-12 KB. `brcoffeejobs` passed at 90 rows (1,009,319 B) and failed
  at 100. Rows run p50 5.6 KB, p95 10.3 KB and max 44 KB, and the largest site-mean row is 14.2 KB.
  So pages are 50 rows, and a 502 halves the page.
- **Hidden rows (Q7).** The token listing matched the rendered page on both sites checked
  (`800flowerscareers` 109, `awg` 43).

## Dead versus empty (Q8, Q8a, Q8b, Q9)

- **A gone site answers 400 on its record:** `{"message":"Careersite not found"}` on 5 real
  departed sites from the seed lists (`bastiansolutions`, `carolinapowerscareers`,
  `workforcenow`, …) and on invented slugs, and `{"message":"Careersite is not active"}` on 6
  (`cityofpeoriaaz`, `claritevcareers`, `crunchcareers`, `lkqexternalcareersite`, `ymcasfcareers`,
  `ymcasfinternal`). Nothing redirects, so Q9 does not apply.
- **An empty live site** answers its record with 200 and the listing with `count: 0`. That was 70
  of 680 seed sites. Re-read about 70 minutes later, all 70 were still 0 (Q8b).
- **One site's listing errored:** `trulitecareers` answered 500
  `ErrCode=ERR_BAD_REQUEST … status code 404`, with its record fine. The prober leaves it UNKNOWN.
- **Employee-only sites.** `settings.careerSiteType` is `"Internal"` on 15 of the 681 seed-census
  sites (25 across the whole 1,499-slug pool, §"The ledger"). The 14
  hiring ones list 3,924 postings. 3,526 of those are also on an external site of the same org,
  and the other 398 are for the client's own staff. The prober writes them DEAD by policy
  (ADR-0202 §4).
- **The click (Q8a).** `/{slug}/cx/job-details?reqId={reqId}`, rendered in Chromium, shows the
  posting on 3 of 3 sites (`800flowerscareers`, `churchmutual`, `lifewisecareers`). The page
  fetches `search-meta/{reqId}` itself. `/{slug}/cx/job/{reqId}` (openings-mcp's URL) rendered the
  site chrome with no posting. The SPA's route table lists `job/:reqId` only under its `legacy`
  module, and the render hit ADP's login redirect.

## Detail (Q10, Q11, Q11b, Q12)

- `search-meta/{reqId}` needs the token: without it the answer is 400. A closed or unknown id
  answers 400 `{"message":"Bad Request"}`. It returns `requisitionTitle` (equal to the listing's
  `jobTitle` on 300 of 300), `requisitionDescription` (never longer than the listing's),
  `postingInstructions` and `customFieldGroup`. **Pay lives only in `customFieldGroup`.** It is either
  `RTiReqExtended_compensationDetails`, the tenant's free text ("$85,000 – $95,000/year",
  "107,000 to 160,400"), or the pay-transparency pair
  `RTiReqExtended_payTransparencyMinSalary`/`MaxSalary` (`amountValue`, `currencyCode`). The
  sample was 300 tech postings, 3 per site across the dump. Of those, 41 carried
  `compensationDetails` and 15 the amounts (56 either). 39 parse to a salary through
  `salary._field_generic`, and 33 of the 39 have no salary the description yields. Details
  average 17.3 KB.
- **The gate is exact.** `parse` reads the title and department off the listing row, and the detail
  overrides neither. A tech-gated detail pass adds salary only.
- `payGrade` on the listing (67.3%) is a pay-group code ("AYVP-HRLY-T7P"), not an amount.
  `departmentName` (75.9%) is a payroll code ("GC Retail-GC-RETAIL"). Neither is read.

## Fields (Q13 to Q18)

- **Dates (Q13).** `postingDate` (ISO-8601 `Z`) is present exactly where the site does not set
  `hideDaysPosted`: 44,643 of 44,647 rows on such sites, and 0 of 32,595 where it is set (300 of
  606 sites). It did not move between two reads about 40 minutes apart (348 of 348 postings on
  10 sites). The oldest is 2010-07-13, an evergreen posting. It is served as stated, and a hidden
  date stays hidden.
- **Remote (Q14).** There is no remote field. Remote shows up in a location's address (459 of
  83,190 addresses say "remote") and in its name ("Remote" under several location codes,
  "Telework", "Remote/Nationwide", "Virtual - USA"). For
  example, `Work from home, Virginia, United States` is named "Remote". `is_remote` reads the
  address and the names together. "Telework" and "Virtual" reach no remote verdict: `is_remote`
  matches only "remote".
- **Salary (Q15).** See the detail section above. Amounts are emitted as `"40000-141700 USD"`
  with no period, because the pair states none, and the parser's annual default plus its
  plausibility floor refuses an hourly figure. `compensationDetails` passes through as stated.
- **Employment type (Q16).** `workLevelCode` takes 183 distinct values: "Full-time" 33,624,
  "Part-time" 17,323, "Full Time" 4,650 and so on. 7,718 rows reach no `employment_type` filter.
  Most of those are "Variable" (4,149), none (1,375), "PRN" or "Seasonal", which stay as stated,
  as they do on every other scraper. 645 rows name "FT" or "PT" whole ("PT 129 or Less Hours" 340,
  "FT" 95, "PT (17-29 hours/week)" 72, "FT (min 35 hours/week)" 71, "Regular FT" 33, ...). The
  scraper labels those, as "Part-time (PT 129 or Less Hours)", so the filter reaches them. There
  is no experience field.
- **Location (Q17).** `requisitionLocations` holds every place. 773 rows have none, 73,880 have
  one, and 2,589 (3.4%) have two or more (up to 9+). `workLocations` and `postingLocations` were
  empty on 77,242 of 77,242. Each place is rendered as the site renders it, "City, State,
  Country", and joined with "; ". Only 24 of 83,190 locations have an empty address, and those
  fall back to the location's name. 89% of rows are in the US, then Canada, India, Germany and the
  Philippines.
- **Department (Q17a).** `organizationalUnits[].name` is present on 92.6% of rows: "Restaurant
  Crew" 4,424, "Retail", "Teaching", "Information Technology" and so on.
- **Company name (Q18).** The SPA's `<title>` is "Career Site" on every site. The site record's
  `clientName` names the employer on 681 of 681 sites, and `company_name.from_title` accepts 680.
  The one refused is "Helena - Agri Enterprises, LLC", whose " - " reads as a title separator.
  `clientName` is ADP's client record and often the legal or parent entity: "Seaboard
  Corporation" for `stfcareers`, "Steel Partners" for `mticareers`. The record's `name` labels the
  site instead ("External", "External Career Site"). ADP is itself a client (`apply`, 909
  postings, `clientName` "ADP").

## Operating limits (Q19 to Q21)

- **No rate limit was found (Q19).** Measured first, before any parallel probe:
  - One site, site record: 30 at c=1, 120 at c=4, 400 at c=16, 800 at c=32 and 2,000 at c=128.
    All 200, up to 155 req/s.
  - One site, listing: 30, 120, 400, 800, 1,500 at c=64 (65 req/s) and 2,500 at c=128
    (105 req/s). All 200. p95 latency rose from 1.2 s to 2.3 s at c=128.
  - Across 30 sites, listing: 1,500 at c=64, 1,498 answered 200 and 2 read timeouts (60 s).
  - The 692-slug census, the 606-site dump and a 300-detail pass at c=8 to c=16 were all clean.

  ADP Workforce Now's F5 budget (200 per 60 s) does not apply here. This is a different edge
  (CloudFront and AWS API Gateway on `myjobs.adp.com`, `my.adp.com`).
- **User-Agent (Q20).** `headstart/0.1`, curl's default and `python-requests/2.32` all answered
  200.
- **Response size (Q21).** A listing row averages 5.8 KB (424.9 MB over 73,318 external rows). A
  detail averages 17.3 KB.

## Population (Q22 to Q24)

- **Tech share (Q22).** `is_tech(jobTitle, department)` keeps 2,504 of 73,318 rows on the 592
  external hiring sites (3.42%). The title alone keeps 2,174 of 77,242. Most of the corpus is
  retail, restaurants, healthcare and logistics. Tech concentrates on a few sites:
  `starplusenergycx`, `churchmutual`, `apply` (ADP itself).
- **Language (Q23).** 1,994 of 2,000 sampled rows are English (`langdetect`, title plus the first
  1,500 description characters). The rest were 3 Spanish, 1 French, 1 Romanian and 1 Croatian.
- **Overlap (Q24).** This is not a skin over another ATS. The postings are ADP's own requisitions.
  Cross-site overlap within one org is covered under Identity.

## Discovery sources

Pool: 1,499 slugs. Per-source counts (each slug is counted once per source that has it):

| Source | Slugs | Only here |
| --- | --- | --- |
| Wayback (`myjobs.adp.com`, path style, 11 CDX pages) | 1,394 | 509 |
| Common Crawl, 33 crawls `CC-MAIN-2023-40`..`2026-39` (data host) | 853 | 29 |
| Local harvested lists | 552 | 1 |
| `Masterjx9/OpenPostings` `companies_full.csv` | 545 | 0 |
| `amikai/openings-mcp` `companies.yaml` | 431 | 7 |
| `andreasasprou/ats-finder` | 1 | 0 |

Wayback and Common Crawl also emit the SPA's own asset paths (`pt-br`, `cx.skip`, `nl-be`). The
prober answers those "Careersite not found".

## Cost (step 6)

A full sweep reads 424.9 MB of listing pages and 2,504 tech details at 17.3 KB, about **0.47 GB for
2,504 tech Jobs, or ~187 KB per tech Job**. ADR-0158's bar is ~2 MB per tech Job, so this ATS
lands active.

## The ledger

`PYTHONPATH=src python scripts/validate/check_liveness.py adp_recruiting` over the 1,499-slug pool
wrote `data/validate/liveness/adp_recruiting.csv`:

- 990 live, of which 861 are hiring with 87,181 postings, and 129 are empty.
- 498 dead: 438 "Careersite not found" (mostly asset paths and stale seed slugs), 35
  "Careersite is not active" and 25 employee-only sites.
- 11 unknown, each a listing that answered 500 on all four passes (`trulitecareers`,
  `academybank`, `starplusenergy`, …).

The row count equals the unique lowercased slug count (1,499). There is one host and no redirect,
so the cross-hostname and casing duplicate mechanisms cannot occur. A spot check of 5 live rows
(`midcocareers` 49, `tnacareers` 16, `webbankcareers` 2, `hygeiadairy` 0,
`tpghotelsandresorts` 0) and 5 dead rows (4 "not found", including `fr-ca` and a font file, and
`southshoretransport` "not active") against the host agreed on all 10.

## Sites another site of the same client contains

`scripts/validate/adp_recruiting_subset_sites.py` walked all 990 live sites (0 unreadable) and
grouped them by `orgoid`. It buried 131 sites, holding 3,601 of 87,181 postings, onto 41 kept
sites in `data/validate/aliases/adp_recruiting.csv` (signal `subset-reqs`, ADR-0186's rule). Each
buried site's whole posting set is contained in a kept site of the same client. For example,
`gnc` (751 that afternoon) is buried onto `generalnutritioncenter` (751, the same ids). `clientName` was
identical across every site of the 98 multi-site clients in the census, so a buried site's
postings keep their company name. Sites that only partly overlap both stay.
