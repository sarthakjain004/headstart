# MyNextHire `reqlist/get`: what the careers API actually does

Measured 2026-09-30, before and while `headstart.scrapers.mynexthire` was written, against the
live tenant hosts. MyNextHire (the product of Smaclify Technologies, Pune; issue #971 calls it
Nexthire) hosts each customer at `{label}.mynexthire.com`. Three public implementations existed to
adapt (`AkashKumar7902/jobwatch` `internal/source/mynexthire.go`,
`aman123424/jobwatcher` `backend/fetchers/mynexthire.py`,
`shivam07-hub/myro-job-scraper` `scraper/providers/mynexthire.py`); each hypothesis they carried is
confirmed or killed below. The probe scripts, captures, sweep logs and browser renders are kept
locally (`experiment/mynexthire-reqlist/`), not committed; every number a reader needs is stated
here. The decisions are ADR-0364.

Sample: 94 pool labels probed (32 tenants hold a Board), every live Board's full listing read
twice (345 postings over 12 Boards in the first read, 630 over 30 in the second), 35 client
records, 2 detail records, 7 browser renders, a 704-request rate ramp, and a label sieve of
12,360 candidate labels.

## Identity

**Q1 — slug.** The subdomain label of `{label}.mynexthire.com`. DNS is a wildcard
(`zzqxnotatenant12.mynexthire.com` resolves to the same three addresses as `swiggy`), so an
invented label reaches the application, which answers it. Case-insensitive: `SWIGGY` and `Swiggy`
return the same 420,476 bytes as `swiggy`. `{label}.careers.mynexthire.io` is a newer career front
for the same tenant, not a second Board: `azentio` and `conseroglobal`, the two such hosts seen,
both list on the `.com` label, and the `.io` host serves only the SPA shell. One tenant is one
site: the client record (below) has one `clientId` per label, and the one relabelled customer seen
(`msystechnologies`, Aziro's old name) answers 417 on the old label rather than serving it twice.
`reqId` is an integer, unique within a Board (83 of 83 at swiggy), never containing `:`.

**Q2 — spellings.** Every source emits the bare lowercase label; `slug_from` keeps the default.
The Wayback feeder canonicalises a `.careers.mynexthire.io` capture onto `mynexthire.com`
(`_CANONICAL_HOST`), so both fronts dedupe to one pool row.

## Listing

**Q3 — surfaces.** The careers page (`/employer/jobs/careers`, an AngularJS 1.3 app,
`/employer/ui/js/jobboard/careers.js`) makes three calls: `GET
/employer/jobboard/details_by_shortname/get/{label}/` (the client record), `POST
/employer/careers/reqlist/get` (the listing) and `POST /employer/careers/activity/log`
(telemetry). `robots.txt` and `sitemap.xml` are 404 ("Requested resource is no longer
available."). The listing is the only surface, and it is complete (Q6).

**Q4 — pagination.** None. One POST returns every open requisition: aziro, the largest Board
(163), came back whole, and its careers page reads "Current Openings [163]" off the same call;
clarion 8 and 8.

**Q5 — body fields.** `{"source": "careers"}` is the whole required body. It returned exactly the
bytes the page's own `{"source":"careers","code":"","filterByBuId":-1}` does (swiggy, 420,476
bytes, `cmp` equal). An empty body answers 417 "Source is mandatory."; `{"source":"x"}` 417
"Invalid source"; a `GET` 405; a POST without `Content-Type: application/json` 415. ShareChat's
`filterCriteria` body (issue #971) is not needed: the minimal body lists ShareChat's 5. myro's
claim that an empty `filterByCustomField` returns 0 and categories must be iterated is false: the
minimal body lists everything.

**Q6 — description.** `jdDisplay` is the full description as plain text: no HTML tag on 630 of
630, blank on 5 (test requisitions). The detail record the page opens for one posting (`POST
{label}.prod.us1.mynexthire.io/d17/careers/requisition/object`, host chosen by
`getMSBaseUrl`, `in1` for ten named India-region clients) carries the same text as HTML, plus
`mandatorySkillList` and `educationList`: swiggy 28894 matched character for character after
`html_to_text`, microlise 187 matched except its bullet characters (1,848 vs 1,895 chars). No
detail pass.

**Q7 — hidden rows.** `statusId` is 3 on 630 of 630 rows, and the page counts the rows the API
returns (aziro 163, clarion 8, meesho 0).

## Dead versus empty

**Q8.** Measured on real labels:

| answer | meaning | seen on |
| --- | --- | --- |
| 200, `reqDetailsBOList: [...]` | a live Board | 30 tenants |
| 200, `reqDetailsBOList: null` | a live Board with nothing open; the page renders "There are no open requisitions at this time!" | meesho, prodindefault |
| 417 `41703001:Invalid company short name: {label}` | no such tenant | an invented label, `app`, `msystechnologies`, and every sieve miss |
| 402 `MyNextHire account subscription for client {LABEL} has expired.` | a lapsed customer | jupitermoney, sirion, and 27 more in the sieve |
| 417 `41701003:Invalid request URL` | vendor infrastructure | `wow` |
| 500 "Unable to process your request at this time…" | unexplained | obmajesco, obquantinsti, spicinemas (the client record 500s too) |

The first four settle a verdict; the last two stay UNKNOWN. **8a** — a job link opens the posting
(Detail link, below). **8b** — meesho's null list came back identical on three reads.

**Q9 — departed tenants** answer the 402 above on the listing and on the client record alike; no
redirect was seen, so the prober need not stop redirects.

## Detail link

The page builds a posting's link in `encoder.js` `getEncodedJobboardLink`: the careers URL with
`src=careers&p={base64 of JSON}`, the JSON being `getQStringObject`'s page context with `pageType`
"jd" and the `reqId`. The scraper builds
`https://{label}.mynexthire.com/employer/jobs/careers?src=careers&p=…` with that exact context.
Rendered in headless Chromium on swiggy 28894, sharechat 2450 and microlise 187 it showed the
posting's title, id, experience, location and full description; an unknown id (sharechat 99999)
renders "Oops! Something went wrong!" at HTTP 200 — the link cannot be checked by status. The
issue's `/employer/jobs?src=…` form is the iframe holder page: rendered bare, it shows only the
footer. Swiggy's configured public page is `careers.swiggy.com/#/careers` (the client record's
`career_page_url`), a wrapper that iframes the same app; the `mynexthire.com` link works for every
tenant without knowing the wrapper. The base64 of this context never contains `+` or `/` for any
`reqId` from 1 to 199,999, so the link needs no escaping.

## Fields (630 postings over 30 Boards, second read)

| `Job` field | source | non-null |
| --- | --- | --- |
| title | `reqTitle`, stripped | 630 |
| location | `locationList[].office`, "; "-joined; one posting names two | 630 |
| remote | none stated; `location` says "Remote" on 1 | — |
| department | `careerStream`, else `buName` where it is "NA" | 630 |
| posted_at | `approvedOn` (`2026-09-07T05:46:36.697+0000`) | 630 |
| description | `jdDisplay` | 625 |
| experience | `expMin`-`expMax` years | 630 |
| employment_type | `employmentType`, mapped | 608 |
| salary | none | 0 |

**Q13 — dates.** `approvedOn` is stated on 630 of 630; two reads of swiggy seconds apart were
byte-identical, so it is not generated per request.

**Q14 — remote.** No field states it. `location` names remote work on 1 of 630 (cstep); the
description on 35, not read.

**Q15 — salary.** `ctcBandLowEnd` and `ctcBandHighEnd` are 0.0 on 630 of 630, whatever
`reqCurrency` (INR_Annual 598, USD_Hourly 11, INR_Monthly 9, …) says. No salary.

**Q16 — experience and type.** `expMin`/`expMax` are stated on every posting, floats; 34 are 0-0
(the `fresher` ones) and 3 fractional (0.6-2, 6.5-7.5, 5.5-7.5). `experience.from_field` reads
"6.5-7.5 years" as 6 with no ceiling, so the bounds are widened to whole years. `employmentType`
took twelve values across the two reads: `full-time`, `full_time`, `permanent`, `onroll`,
`contract`, `fixed-term-contract`, `consultant`, `third_party_consultant`, `intern`, `internship`,
`azentio_group`, `conversion`. Raw, `employment_type_filter.flags` reads `third_party_consultant`
as part-time (the substring "part") and `consultant` as full-time, so each is mapped;
`azentio_group` (one tenant's label, 12 postings) and `conversion` (2, meaning stated nowhere)
name no type.

**Q17 — location.** `locationList` held two offices on 1 posting of 630 (Mumbai and Pune) whose
flat `location` said only "Mumbai".

**Q17a — department.** Two candidates, both on every posting. `careerStream` is often a
tenant-wide default: aziro files 159 of 163 under "Engineering", its marketing and sales roles
included, and ShareChat's content roles are "Engineering" too. `buName` is a business unit, and
on aziro a client name ("Rubrik- US"). Five tenants state `careerStream` "NA" on every posting
(bindz 36, licious 32, amagi 15, azentio 12, daloopa 8). Over the first read (345 postings, 12
Boards) `tech_filter.is_tech(title, department)` kept 202 with `careerStream`, 164 with `buName`
and 162 on the title alone, and every posting `buName` kept, `careerStream` kept too — so
`careerStream` is the recall-safe choice, with `buName` where the stream is "NA". The 38 it adds
include real tech ("Associate Director - C/C++ Linux Kernel", "Flutter Architect", two "Python
Automation QA") and creep ("Field Marketing Manager", "AI Animator").

**Q18 — company.** The client record states `clientName` on 32 of 32 tenants: "Swiggy",
"ShareChat", "Aziro", "Medline India Pvt Ltd", "Dailyrounds/Marrow". The page `<title>` is the
vendor's ("Approved Jobs, powered by Smaclify Technologies!"). The vendor's own tenant `smaclify`
states "MyNextHire".

## Operating limits

**Q19 — rate limit.** None found. One tenant (`experience`): 64 requests at each of 1, 4, 16,
32 and 64 concurrent — 3.4, 12.4, 37.3, 57.2, 58.1 req/s, every one 200; throughput flattens at
~58 req/s with p50 rising from 0.29 s to 0.84 s. Across 7 tenants: 256 requests at each of 16, 64
and 128 concurrent — 43.3, 107.9, 133.6 req/s, every one 200. No gate is seeded.

**Q20 — User-Agent.** `headstart/0.1`, no UA and `python-requests/2.32` all answered 200.

**Q21 — size.** 5,038 bytes a posting on average (3,133,880 bytes for the 622 postings of the 28
customer Boards with postings); the largest listing is aziro's 731,166 bytes. The client record
is ~34 KB.

## Population

**Q22 — tech share and volume.** Second read, the 28 customer Boards with postings (the vendor's
test tenants excluded, below): 622 postings, of which the tech gate keeps **293 (47.1%)** over 26
Boards; aziro alone holds 156. Swiggy's 83 yield 5 (its "Engineering" stream is mostly "Sales
Manager II"), ShareChat's 5 yield 4 only through its "Engineering" stream default.

**Q23 — language.** `langdetect` over title + description: 625 of 630 English; the other 5 are
test requisitions ("test", "mdl test", a "Software Tester" whose description is "Test JD") read
as French, German or Estonian.

**Q24 — overlap with held Boards.** Joined on label and on the client's website domain against
every committed ledger: six held Boards with postings name the same company. Their titles
against this ATS's: rippling `daloopa` 0 of 11 shared, darwinbox `licious` 0 of 32, ripplehire
`anuntatech` 0 of 1, greenhouse and keka `sapro` 0 of 36 (bindz is CBIZ's Indian arm), and
Workday `medline` 2 of 49 ("Prime Vendor Analyst", "Specialist - Integration (AEM-IS)"; Medline's
Workday Board lists 75 India postings of 631). Swiggy and ShareChat's other rows are all dead or
live at 0. The duplication is 2 postings; no gate is needed.

**Vendor tenants.** `consultant` is the vendor's test tenant (client "Consultant test1", site
`www.mynexthire.com`; 27 postings such as "Test Req 90" and "Java Developer" whose description is
"this is test jd", at places "aassrr11"). `mars` names itself "Mars" but is a trial: 7 of its 8
postings are "test mars2", "mdl test" or a "Software Tester" described "Test JD", "ok" or nothing,
ids 3-47 over two years. `prodindefault` is the default tenant. All three are in
`EXCLUDED_BOARDS`. `smaclify` (4 real postings, the vendor hiring for itself) and `indevia`
(1 real posting, 2 test ones) are kept.

## Discovery

The client ids run from 1001 (the vendor's own) to 1165, so the vendor has had at most ~165
customers; the pool is the search for those still subscribed.

DISCOVERY_TABLE

## Reproducing

Every listing: `curl -sS -X POST -H 'Content-Type: application/json' -d '{"source":"careers"}'
https://{label}.mynexthire.com/employer/careers/reqlist/get`. Client record: `curl -sS
https://{label}.mynexthire.com/employer/jobboard/details_by_shortname/get/{label}/`.
