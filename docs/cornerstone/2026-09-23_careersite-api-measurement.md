# Cornerstone OnDemand career sites: what the API actually does

Measured 2026-09-23, before `headstart.scrapers.cornerstone` was written, against the live
career-site pages, the regional search API and the tenant-host job-ad API. The hypotheses came
from the upstream `kalil0321/ats-scrapers` scraper (MIT) and its 564-row seed list, plus the
orchestrator's recon; each is confirmed or killed below. The probe scripts and raw captures are
kept locally, not committed; every number a reader needs is inlined here.

Samples: **404 tenants** (every tenant in the upstream seed; the existing 252-row pool is a subset
of it), a full career-site walk of each, **42,534 unique postings** across the 368 hiring tenants,
**24,800 listing rows** captured field by field from 191 tenants, **924 job ads** and **924
`jobDetails`** payloads (up to 5 per tenant), and rate ramps of 3,904 requests (992 search and 992 job-ad on one tenant each, 960 of each
across 60 tenants).

## Upstream got four things wrong

1. **The default API host does not exist.** `https://na.api.csod.com` fails DNS resolution. The
   pod comes from the page's `csod.context.endpoints.cloud`: `us` 162 tenants, `uk` 109, `eu-fra`
   77, `eu-cdg` 33, `au` 14, `eu-cdg-hs` 2, `us-galaxy` 1 (398 tenants with a context).
2. **Site id 1 is not "the" career site.** One tenant runs up to 114 site ids (53 active). The
   site id is a *filter* on the search, and a wrong one answers `200 {"totalCount": 0}` — the
   oracle `siteNumber` trap again. The seed's own per-site URLs reach **31,545** of the **42,534**
   postings the same tenants actually publish (74%); on `aswatsoneurope` they reach 119 of 3,757.
3. **The token regex never matches.** The page sets `csod.context={...,"token":"…",...}` as a JSON
   object literal; upstream's primary regex looks for `csod.context.token = "…"` and only its
   fallback (`"token":"…"`) works.
4. **The listing's `externalDescription` is not the posting.** It is one field of the job ad,
   HTML-stripped (see §Detail).

Its "~60 req/min" rate limit was not reproduced either (§Operating limits).

## Identity (Q1, Q2)

- **Slug = the `{corp}.csod.com` label.** It equals the URL's `?c=` value on 564 of 564 seed URLs
  and the page's `csod.context.corp`. Host and `c=` are case-insensitive (`ASWATSONEUROPE`
  answers the same board; `corp` echoes the case given), so the slug is lowercased.
- **The Board is the tenant, not the tenant + career site.** A requisition is posted to one or
  more career sites; 119 of 368 hiring tenants post at least one requisition to several (12,798
  multi-site postings). Summed per site the population is 58,083; the union by `requisitionId` is
  42,534 — per-site Boards would serve 15,549 rows twice under different Board keys, with nothing
  to deduplicate them (`evict_duplicate` groups within a Board). The scraper therefore walks the
  tenant's sites and unions by `requisitionId`.
- **Site ids are enumerable.** `GET https://{slug}.csod.com/services/x/career-site/v1/careersites/{id}`
  answers `200 {"active": bool}` for an existing site and `404 {"error":{"code":40}}` past the last
  one. On 396 of 398 tenants the ids are exactly `1..N`; the other two have one id that answers
  **500** (`nlb` id 6, `myhr-ece` id 1) with live sites after it, and a search on the 500 id
  returns 0 postings. No tenant had a 404 before a live site. **Inactive sites never carry
  postings** (0 across every inactive site of 398 tenants). Rule: walk from 1 until the first 404,
  search every id that did not answer `active: false`.
- Native ids are integers (no `:`), so `board_of` is safe.
- Discovery spellings: seed URLs (`https://{label}.csod.com/ux/ats/careersite/{n}/home?c={label}`),
  bare labels (existing pool, Wayback `sub` style, Common Crawl `label` kind) and the
  fingerprinter's `SUB + csod\.com` label — all reduce to the lowercased label.

## Token (Q11b)

The career-site home page (`/ux/ats/careersite/{id}/home?c={slug}`, 5.3 KB) embeds an anonymous
JWT (`sub: -100`) and the pod. Measured properties:

- **Per corp, not per site**: the site-1 token reads every site of the tenant.
- **Pod-bound**: the same token answers 401 on another pod (`eu-fra`, `us`); another tenant's
  token answers 401; a tampered or empty one answers 401 — with an empty body, not a 200 empty.
- **Lifetime varies by tenant**: `exp − nbd` over 398 tenants is 21 min (1 tenant) to 24 h (34),
  modal 61 min (154). A Board's scrape takes seconds (§Operating limits), so one token per Board
  suffices. **An expired token answers 401 with an empty body** on both the pod search and the
  tenant host, from the first request after `exp` (replayed every 20 s across `exp` on `sncfl`,
  a 21-minute token: 200 at −5 s, 401 at +16 s through +100 s). A 401 is never a 200-empty, so
  it cannot silently read as an empty Board.
- **The tenant-host endpoints need the session cookie on US-pod tenants.** The JWT's `aud` is the
  page's `ASP.NET_SessionId` cookie. `careersites/{id}`, `jobDetails` and the job-ad endpoint
  answered 401 without it on 14 of 40 tenants sampled — all 14 on the `us` pod, all 26 others
  200 — and 200 on 40 of 40 with `Cookie: ASP.NET_SessionId={aud}` alone (`cscx` alone: 401).
  The pod search needs no cookie (1,952 cookieless ramp searches, all 200).
- **The page's cookies must not stay in a pooled session's jar.** Found running the scraper
  through the repo's thread-pooled client, whose jar keeps the page's `ASP.NET_SessionId`,
  `cscx` and `AWSALB`: with the jar populated, the explicit session header answers 401 on US-pod
  tenants (ama-assn and alamo, 3 of 3 trials each; aak and aswatsoneurope 200), while the jar
  alone (12 of 12) and the header alone (12 of 12) both answer 200; and re-reading the career-site
  page with the jar populated redirects to `/ui/error` on ama-assn and aswatsoneurope, which would
  make every token refresh fail. The API responses set no cookies. So the scraper drops the host's
  cookies from the jar the moment the page is read and carries the session only as the header.
- The home page for a site id that does not exist answers `302 → /ui/error`. `myhr-ece` has no
  site 1 (the API says 500), so its site-1 page 302s while its site-2 page carries the token:
  the token must be sought on more than one site id.

## Listing (Q3–Q7)

`POST {pod}rec-job-search/external/jobs`, bearer token, body
`{careerSiteId, careerSitePageId, pageNumber, pageSize, cultureId, cultureName}` (the page's own
`buildSearchRequest`). Measured:

- **`careerSitePageId` is the filter; `careerSiteId` is ignored** (`careerSiteId 1 /
  careerSitePageId 16` → 32 postings, the reverse → 0). Both are sent equal, as the page does.
- **`pageSize` clamps silently at 1,000** (asked 5,000, served 1,000; 2.1 MB).
- **`totalCount` is truthful**: on the five largest sites (3,372 / 2,458 / 1,569 / 1,382 / 1,086)
  the walk read exactly `totalCount` unique ids, and the page after the last answers 0 rows. No
  hard window was reached (largest site 3,372). The walk ends on a short page or on reaching
  `totalCount`.
- `cultureId`/`cultureName` do not filter (`nl-NL`, `en-GB`, `en-US` all 2,460) but **format the
  date**: `postingEffectiveDate` is `9/23/2026` under `en-US`, `23-09-2026` under `nl-NL`,
  `23/09/2026` under `en-GB`. The scraper always sends `en-US` and parses `M/D/YYYY`.
- `Origin`/`Referer`/User-Agent are not needed.
- A listing row has exactly six keys on 24,800 of 24,800 rows: `requisitionId`,
  `displayJobTitle`, `externalDescription`, `locations`, `postingEffectiveDate`,
  `postingExpirationDate`. No department, employment type, salary or remote flag.
- The board page renders this same search, so it hides nothing the API returns (Q7).

## Dead versus empty (Q8, Q9)

- **Unknown slug**: `{slug}.csod.com` does not resolve (no wildcard DNS) — an invented slug and 6
  of 404 seed tenants (`a2dominion`, `atu`, `kuehne-nagel`, `mthgroup`, `carriere-primonial`,
  `portofhoustonrecruitment`).
- **Corp without a career site** (an LMS-only customer; `csod.com` hosts both products on one
  label): every career-site page answers `302 Location: /ui/error` — 5 tenants × site ids 1–6,
  and 12 of a 30-tenant Wayback sample. The host root 302s to the tenant's SSO, so `GET /` says
  nothing.
- **Live tenant with nothing open**: page 200 with a context, sites walk, every search
  `totalCount: 0` — 30 of 398.
- **A search that answers 404 `ResourceNotFound`** (6 tenants in the ledger pass: metso,
  transgourmet, getingeacademy, layahealthcare, packaging-technology, prosegurlearning — all on
  `eu-fra`) while the page and its active sites answer 200: the page itself, rendered in Chrome
  (metso), shows "Current Openings" with none listed. Read as an empty site; any other 404 raises.
- So DEAD = DNS failure, or the site-1..3 pages all `302 → /ui/error` (read with redirects off).
  Three ids, not one, because `myhr-ece` starts at 2 (1 of 398).

## Detail (Q6, Q10, Q11, Q12)

Three per-posting surfaces exist on the tenant host:

| surface | what it adds over the listing | p50 bytes |
|---|---|---|
| `Services/API/ATS/CareerSite/{site}/JobRequisitions/{id}?useMobileAd=false&cultureId=1` (job ad) | `ad`: the posting as the page shows it | 8,181 |
| `services/x/job-requisition/v2/requisitions/{id}/jobDetails?cultureId=1` | HTML of `externalDescription`, `additionalLocations`, `openDate`, `defaultCultureId`, `positionOUId` | 2,237 |
| job page `/ux/ats/careersite/{site}/home/requisition/{id}?c={slug}` JSON-LD | `Description` (= the ad), `DatePosted`, `HiringOrganization` — **absent** on some tenants' pages | 10,721 |

- **The listing description is a fragment of the posting.** Over 924 postings on 191 tenants the
  job-ad text is >20% longer than the listing's `externalDescription` on **750 (81%)**, median
  **3.24×** (p90 70×): the ad is the tenant's template — intro, the description, requirements,
  benefits, sometimes a salary line — and `externalDescription` is only the middle. The listing
  text is also lossy: HTML-stripped with `&` deleted (`R&D` → `R D`, 12 of 12 R&D titles checked),
  paragraphs flattened, and on one tenant (`gmv`) it carries raw CSS.
- `jobDetails` adds nothing a Job needs: its `externalDescription` is the same field as the
  listing's (same length after stripping on 624 of 923, the same first 80 characters on 744, the
  rest differing by the deleted `&`, whitespace and entities), and the listing's `locations` has
  exactly `1 + len(additionalLocations)` places on 923 of 923. `positionOUId` → `services/api/OrgUnit/{id}/title` names positions ("Portfolio
  Analyst", "All Open Positions"), not departments, and 401s on some tenants — not used.
- So the detail pass is the **job ad**, for the description alone. The ad is empty on 21 of 923
  (2.3%) — on some of those the listing text is real (`fluidra`, `draxgroup` 6,668 chars) — so an
  empty ad falls back to the listing text. Both carry tenant placeholders: 1,384 of 24,800 listing
  descriptions (5.6%) are a bare `<<INTERNAL JOB DESCRIPTION>>`-style token, and empty ads read `>`.
  The ad needs the id of a site the posting is on (it is keyed by site; the job page for a site
  the posting is not on is a 200 SPA shell with no posting).
- **Tech gate: exact.** `department` exists on no surface, `parse` reads `displayJobTitle` from
  the listing, and the ad's `title` equals it on every ad fetched (58 of a 100-posting random
  sample; the other 42 failed to connect during a local network outage — google.com failed too). The gate asks `is_tech(title,
  None)` on both sides.
- ADR-0048's skip applies: the ad supplies only `description`.

## Fields (Q13–Q18)

- **Dates (Q13)**: `postingEffectiveDate` (the posting's own start; equals the per-site posting's
  `startDate`) is present on 24,800 of 24,800 rows, oldest 2017-05-05, p50 age 54 days, none in
  the future. It is a calendar date, so the icims fabricated-timestamp test does not apply; it
  equalled `jobDetails.openDate` on 511 of 923, later on 298 (reposts), earlier on 114.
  `postingExpirationDate` is `-` on 17,200 rows and a date on 7,600 (3,025 of them `1/5/2027`),
  none in the past — not mapped.
- **Remote (Q14)**: no field. Tenants write it into the city (`Deutscher Standort/Remote`);
  `is_remote(location)` fires on 80 of 24,800 rows.
- **Salary (Q15)**: no structured field on any surface. 75 of 924 job ads end in a templated
  "`Monthly Salary 25,000.00 - 28,000.00`" line, 45 of them `0.00 - 0.00`;
  `salary.extract(None, text)` returns None on the zero line (no fabricated floor).
  `_salary_field` returns None.
- **Experience / employment type (Q16)**: no field (the ad template sometimes spells
  "Permanent"/"Tiempo completo" in prose).
- **Location (Q17)**: `locations` is a list of `{city, state, country}` (keys vary per entry: all
  three on 16,932 entries, city+country 8,190, country only 2,377); 1–45 places per posting, more
  than one on 1,848 of 24,800.
  All are joined with `"; "`.
- **Department (Q17a)**: none (see Detail).
- **Company name (Q18)**: nothing at Board level names the employer. Probed on 30 hiring tenants
  (27 random plus the opaque `bba`, `hps`, `aet`): the career-site page has no `<title>` on 30 of
  30; `csod.context` carries only `corp` (the slug); `careersites/{id}`'s only name-like fields
  are its languages' display titles; header-image alt text named the employer on 2 of 30 (`BBA`,
  `AWC Logo`). The job page's JSON-LD `HiringOrganization` does name it (`A.S. Watson Health &
  Beauty Europe`) but is per posting — the phenom subsidiary trap — absent on some pages, and on a
  page the steady-state scrape never fetches. The slug stays the name: readable for most tenants
  (`aswatsoneurope`, `apollotyres`), opaque for some (`bba`, `hps`, `aet`).
- **Descriptions under 30 word characters are placeholders.** Once `<<…>>` tokens are removed,
  2,406 of 24,800 listing texts fall under 30 word characters and every one read is a label or a
  placeholder (`>`, `...`, `Template`, `see JD`, `TBC`, `Insert job description here`, `Enter
  Description`, a repeated job title); 20–29 still holds `Please enter job description here.`.
- **Titles** are HTML-escaped when they contain `<` (`&lt;&lt;ENTER DISPLAY JOB TITLE&gt;&gt;`, 835
  rows on 20+ tenants, all tenant template junk the page itself shows); unescaped in `parse`.

## Where a job seeker actually lands, and charset

- **The csod career site is the Board seekers reach.** Rendered in Chrome, Linde's German
  careers page links its job search to `linde.csod.com/ux/ats/careersite/20/home?c=linde`, and Strabag's
  `jobboerse.strabag.at` links to `strabag.csod.com` (4 links). Henkel's and Apollo Tyres' pages
  rendered no job links without interaction (2 of 4 unsettled). No acquired-product domain was
  seen; Cornerstone's separately acquired TalentLink (`*.tal.net`) is a different product and
  not what this scraper reads.
- **Job URLs land on the posting.** Six scraper-built URLs (ama-assn, henkel, aak, apollotyres,
  bitdefender, laerdal; the lowest site each posting is listed on) all answer 200; on five the
  page's JSON-LD `Title` equals the listing title, and aak (SEO off, no JSON-LD) renders the title
  in Chrome — while the same requisition on aak's inactive site 3 renders no posting.
- **Charset** (3 tenants: aswatsoneurope, apprentis-auteuil, henkel): the page and the search say
  `charset=utf-8`; the job ad says only `application/json`, with no charset, and its body is UTF-8
  (`én`, `–`, `“`). The scraper decodes the ad's bytes as JSON (UTF-8 by the JSON spec) rather
  than trusting a guessed text encoding.

## Operating limits (Q19–Q21)

- **No rate limit found.** Single tenant, search on `uk`: 1→128 concurrent, up to 99.7 req/s, 512/512
  200. Single tenant, job ad on `alamo` (`us`): up to 195.7 req/s, all 200. Across 60 tenants at
  once: job ad 171.8 req/s, search 90.8 req/s, zero non-200s. No gate entry is needed; the
  scraper ships at 16 detail workers like icims/oracle/pyjamahr.
- **User-Agent-agnostic** (Q20): `headstart/0.1`, `python-requests/2.32` and no UA all 200 on the
  page, the search and the ad, with plain curl (no TLS impersonation needed).
- **Sizes** (Q21): home 5.3 KB; `careersites/{id}` ~1 KB; search ~2.1 KB per posting (2,106,187
  bytes for 1,000 rows); job ad p50 8,181 bytes, mean 10,018.

## Population (Q22–Q24)

- **Volume**: 368 of 398 context-bearing seed tenants hiring, 42,534 unique postings, p50 36 per
  hiring tenant, mean 116, max 3,757 (`aswatsoneurope`).
- **Tech share**: `is_tech(title, None)` keeps **1,656 of 24,800 (6.7%)**, on 115 of 191 tenants
  (henkel 194, gmv 160, bradesco 139, macomtech 128, hella 119).
- **Language** (langdetect over title + listing text): English 58.7%, Dutch 16.7%, French 7.9%,
  German 6.3%, Spanish 4.5%, Portuguese 3.5%; **78.0% of the tech rows are English**.
- **Overlap (Q24)**: Cornerstone is the ATS itself, not a skin. 34 of 368 hiring labels also name
  a hiring Board on another ledger, mostly coincidental short labels (`bba`, `bc`); the real
  companies among them run a different set there (`grupobimbo` 45 here vs 563 on eightfold, `bbva`
  4 vs 582 on workday). `cross_ats_duplicates.py` cannot join a vendor-subdomain label.
