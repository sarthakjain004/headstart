# TurboHire career API: measurement (2026-09-30)

Issue #976. Everything here was measured on 2026-09-30 against the live API, in two censuses:

- **The first census** covered the 104 labels in the pool before this build's sweeps: 65
  organizations, 1,268 listing rows and 1,268 details. The field shares below are over it unless
  a line says otherwise.
- **The landing census** covered all 114 labels after the fresh Wayback and Common Crawl sweeps
  (see [Discovery](#discovery)): 74 organizations and 1,341 postings, each with its detail. It
  confirmed the gate and cost figures.

The probe scripts and raw captures are kept locally in `experiment/turbohire/` and are not
committed. Every number a reader needs is inline.

## Summary

- A **Board is a career-page subdomain label**: `flipkart` in `flipkart.turbohire.co`. The label
  resolves to an organization through the API, and the listing is keyed on that organization's
  GUID.
- **One request lists a whole Board.** One `POST` returns every posting, with `Total` equal to the
  rows returned on 65 of 65 organizations.
- **A detail pass is needed.** The listing cuts the description at 500 characters. It also blanks
  the salary and hides the employment type.
- **Dead versus empty** is decided by the organization lookup: 404 means no organization, 200
  means one exists.
- **Tech share is 25.1%** (337 of 1,341 in the landing census). The tech gate before the detail
  pass is exact: the detail's title and department equalled the listing's on 1,341 of 1,341.
- **Cost is ~33 KB per tech Job**, well under ADR-0158's ~2 MB bar. The Boards land active.
- **Cleartrip has no Board of its own.** Its careers site links to Flipkart's Board.

## The flow

The career page is a client-rendered React SPA. Its main chunk (10.4 MB) names two API hosts:
`https://api.turbohire.co` (the SPA's `API_SERVER_COPY_BASE_URL`) and
`https://thapi.azurewebsites.net`. Every call below also answered on `api.turbohire.co`, so the
scraper uses only that host.

1. **Token.** `GET https://api.turbohire.co/api/token/noauth` returns
   `{access_token, refresh_token, expires_in: 3600, token_type: "Bearer", OrgId: 000…}`, about 1 KB.
   - The endpoint checks the **Referer**:

     | Referer sent | Answer |
     |---|---|
     | none | 403 `"Invalid request."` |
     | any `*.turbohire.co` host, including an unknown label or `www` | 200 |
     | `example.com` | 403 |
     | `careers.cleartrip.com` | 403 |

   - An `Origin` header alone is not enough: 403.
   - An empty `Authorization: Bearer ` header is answered **401**, so the token request carries no
     Authorization header at all. The first live run of the scraper failed on exactly this, 5 of 5
     Boards.
   - The User-Agent is not checked: `headstart/0.1`, bare curl and `python-requests/2.32` all got 200.
   - The token is anonymous. Its JWT carries `client_id: RO.Client` and no organization, and
     `exp - iat = 3600`. One token serves every tenant.
   - Without a token, the listing answers 401 `"Authorization has been denied for this request."`.
2. **Organization.** `GET /api/publicorganizations?accountName={label}` (with Bearer) returns the
   organization record, 5 to 8 KB: `OrgID`, `OrgName`, `CareerPageSubdomain`,
   `IsCareerPagePublished`, `OrganizationDefaults.Language`, …
   - A label that no organization holds returns **404 with an empty body**.
   - The lookup is case-insensitive: `FLIPKART` returned the same record as `flipkart`.
3. **Listing.** `POST /api/careerpagev2/filteredjobs?orgId={OrgID}&pageType=0` with body `{}`
   returns `{Total, Result[]}`.
   - `pageType=0` is `CAREER_PAGE_ENUM.CAREER_PAGE` and returns the same bytes as leaving the
     parameter out.
   - `pageType=1` is the **internal job page**. It returned 7,556 Flipkart postings, and
     `pageType=2` (referral page) returned 71. Neither is public, so neither is read.
4. **Detail.** `GET /api/publicjobs?jobId={JobId}&fieldVisibility=CareerPage` (with Bearer).
   - `jobId` accepts both the `JobId` GUID and the `JobIdObfuscated` token.
   - `/api/publicjobs/{id}` returns 404.
   - `fieldVisibility` decides the payload size. On Flipkart's security-architect posting it was
     33,716 bytes with `CareerPage`, 2,290,569 bytes with `0` and 8,385,726 bytes with `None`. The
     difference is `AdditionalFields`, which `CareerPage` empties.

## Identity

- **Slug: the subdomain label**, lowercase `[a-z0-9-]`. The label is at once the discovery key,
  the career-page host and the job link's host, and the API resolves it. The org GUID would be an
  opaque key that discovery never yields.
- `CareerPageSubdomain` equalled the label on 65 of 65 organizations. The 65 labels held 65
  distinct `OrgID`s, so there are no alias labels.
- `tatamotors` and `tatamotorscampus` are two organizations that share the name "Tata Motors".
  The second has 0 postings.
- Native id: **`JobId`**, a GUID. It was unique on 1,268 of 1,268 rows, contains no `:`, and was
  stable across two reads an hour apart.

## Listing

- **No server-side paging.** The SPA sets `totalCount = Result.length` and pages client-side.
  - `Total` equalled `len(Result)` on 65 of 65 organizations.
  - The largest Board is avanse, with 162 postings (271 KB).
  - The internal page's 7,556 rows arrived in one response, so there is no cap at least up to
    that size.
- **The listing truncates the description:** `JobDescV2` is exactly 500 characters on 877 of
  1,268 rows and empty on 202.
- **The listing also blanks or hides fields:**

  | Field | On the listing | On the detail |
  |---|---|---|
  | `CTCInfo` | `{Type: UNSPECIFIED}` on all 1,268 | carries the figures |
  | `Type` | UNSPECIFIED on 1,266 (INTERN on 2) | FTE 580, CONTRACT 12, INTERN 6, CONTRACT_TO_HIRE 1, OTHER 1, UNSPECIFIED 668 |
  | `JobTypeV2` | non-empty on 60 | non-empty on 338 |

- **Hidden rows:** none found. The listing is what the career page renders, and the SPA applies no
  row filter.

## Dead versus empty

Every `*.turbohire.co` label serves the SPA with 200, so the page cannot tell dead from empty.

- A live organization's page names the organization in `<title>`, and its HTML carries a
  `company/{OrgID}/` logo URL.
- A dead label renders `<title>TurboHire</title>` with neither.
- **The organization lookup settles it:**
  - 404 for all 39 dead pool labels, and for an invented label (`zzznotarealtenant123`).
  - 200 for all 65 live ones.
  - **A live organization with no postings** (olacareers, pinelabsgroup, and 15 more) answers
    `{"Total":0,"Result":[]}`.

## Detail

- Each of the 1,268 detail requests returned 200, at concurrency 4.
- The detail's `JobTitle` and `Department` **equalled the listing's on 1,268 of 1,268**. So the
  tech gate (ADR-0166) runs before the detail and is **exact**.
- **Description:** `JobDescriptionV2` (and the V1 field) on 1,066 of 1,268. V1 and V2 were always
  both present or both absent. Of the 202 postings with none:
  - 27 attach a PDF or DOCX (`JobDescriptionBlobUrl`), which is not read.
  - 175 state nothing at all.
- **Tech rows:** 238 of the 319 carry a description, 18 only a blob, and 63 none.
- **Sections:** 56 postings add `RolesAndResponsibilitiesV2` and `EligibilityV2`. The page
  concatenates them after the description under their headings, and the scraper does the same.
- **Size:** p50 22,995 bytes, p95 52,532, max 92,676. All 1,268 details together came to 32.9 MB.

## Fields

- **Dates.**
  - `PublishedDate` (listing, zoned `Z`) equals the detail's `PublishedDates.CAREERPAGE` on all
    827 rows that state either.
  - The other 441 state only the detail's `CreatedDate`, which carries no zone.
  - `CreatedDate` is UTC: it preceded the zoned publish dates on 2,888 of 2,888 (created,
    published) pairs, and on 92 of them by under a minute.
  - A second read an hour later moved no `PublishedDate` (1,268 of 1,268).
  - Some Boards list old postings. i4consulting and avanse carry rows created in 2021 and 2022.
- **Remote.** No field states it. TurboHire spells a remote place as the address **"Remote Job"**
  (42 postings; plus "Remote, OR, USA" once), so `is_remote` over the joined location reads it.
- **Location.** `Location` is a JSON *string* holding a list of `{Address, PlaceId, …}`.
  - 117 of 1,268 name more than one place, up to 25.
  - 4 carry a place with no `Address`.
- **Experience.** `{MinExp, MaxExp}`: both on 1,197 rows, min only on 5, max only on 1, neither on
  65. The page renders "Min - Max Years" and "Min+ Years".
- **Employment type.** The detail's `Type` code (FTE, CONTRACT, INTERN, CONTRACT_TO_HIRE) is read
  first. Where the code is UNSPECIFIED, the `JobTypeV2` label ("Full Time" on 61 rows) is read.
  `employment_type_filter.flags` reads "Full Time", "Contract", "Internship" and
  "Contract to Hire" correctly.
- **Salary.** `CTCInfo {CurrencyCode, Min, Max, Type, IsHidden, HiddenFrom}`, on 1,185 details.
  - The page's `getCTCString` shows it **unless `HiddenFrom` contains "JobSeekers"**, whatever
    `IsHidden` says. By that rule, 794 are hidden, 72 are shown with no figures and 319 are shown
    with figures.
  - Currency: INR 312, USD 3, SGD, AED and GBP 1 each.
  - Period: ANNUAL 289, MONTHLY 29, UNSPECIFIED 1.
  - Many tenants type lakhs or placeholders: "12"–"16", "01"–"4000000".
  - Spelled `"LOW-HIGH CUR per-year|per-month"`, `salary.from_field` reads 246 and refuses 73 on
    its plausibility floor. That is the reading wanted, because the page itself shows "₹ 12 - ₹ 16
    Annual".
  - No new parser is needed.
- **Company.** `OrgName` from the organization record equals the page `<title>` on 65 of 65, so the
  scraper adopts it (`adopt_company`). The postings of an agency Board (`act` "Talent Network",
  `elementshrs`, `i4consulting`) name their client in `ClientName` when `ShowClientName` is set,
  but the Board is served under its organization's name.

## Operating limits

- **Rate limit: none found.**

  | Target | Concurrency | Throughput | Latency | Non-200s |
  |---|---|---|---|---|
  | One organization (Flipkart) | 1, 4, 16, 32, 64 | 0.6 → 4.0 req/s, then flat | p50 1.6 s → 12 s | none in 480 requests |
  | Across the 65 organizations | 1 → 64 | 4.8 → 45.7 req/s | p50 0.15 → 0.38 s | none in 480 requests |

  Flipkart's listing alone is slow: 1.6 s single, against 0.2 s for the others. It is server
  compute, not a limit.
- **robots.txt.**
  - `api.turbohire.co/robots.txt` answers 404, so under ADR-0189's per-host rule no rules apply to
    the API.
  - Tenant career hosts serve `User-agent: * Disallow: /` (Googlebot allowed on `/job/`). The
    scraper and the prober request nothing from a tenant host; the job link points there for the
    reader to open.
  - `googe_jobs_sitemap.xml`, which robots.txt names, answers 404.

## Population

- **Census:**
  - first: 104 pool labels → 65 organizations → 48 hiring → 1,268 postings;
  - landing: 114 labels → 74 organizations → 54 hiring → 1,341 postings.
- **Tech:** 337 of 1,341 are tech (25.1%; 319 of 1,268 in the first census), on 38 Boards. The
  largest tech counts are `act` 63, `jswgroup` 36, `elementshrs` 32, `tatamotors` 26, and
  `anuntatech`, `dtdl` and `i4consulting` 21 each.
- **Language:** English is `OrganizationDefaults.Language` on 65 of 65 organizations. langdetect
  over title plus teaser reads `en` on 1,177 of 1,268. The rest are short or empty teasers
  misread (de 20, nl 14, da 13, …).
- **Vendor demo organizations:** `thdemo` (27 postings: "Hotel Operations Trainee - Copy Test",
  salary 12123213–432432433) and `democareers` (6), both named "TurboHire - Demo Account". Both
  are in `EXCLUDED_BOARDS`.
- **Overlap with Boards already held.** Title overlap (distinct titles) was measured against the
  hiring employers that also hold a live Board on another ATS:

  | Employer | Other Board | Titles shared | Verdict |
  |---|---|---|---|
  | Tata Motors | successfactors | 0 of 120 | kept |
  | Lenskart | recruitee | 0 of 160 | kept |
  | TresVista | ripplehire | 2 of 37 | kept |
  | Anunta | ripplehire | 0 of 25 | kept |
  | Kauvery | zwayam | 0 of 5 | kept |
  | Sigma | trakstar | 0 of 6 | kept |
  | TravClan | freshteam, its `/jobs` page | 1 of 25 | kept |
  | **Cipla** | successfactors `careers.cipla.com`, 80 postings | **17 of 18** | parked |
  | **Cipla South Africa** | the same Board | **2 of 3** | parked |
  | SISA | keka | — | not measured: the keka host failed TLS |

  Cipla's TurboHire pages mirror its SuccessFactors Board, so `turbohire:cipla` and
  `turbohire:ciplasouthafrica` are in `PARKED_BOARDS`.
- **Flipkart and Cleartrip.**
  - Flipkart's Board lists 9 postings, 1 of them tech by `is_tech` (the security architect). The
    issue counted 3 "tech-ish". The other two, "DP - Lead" (Infosec) and "FCC Senior Engineering
    Manager" (department Externalization), are left to the tech filter, which decides.
  - `careers.cleartrip.com` links only to `flipkart.turbohire.co/careerpage/{Flipkart OrgID}`. The
    `cleartrip` label returns 404, and Flipkart's organization lists `@cleartrip.com` among its
    verified domains.
  - So Cleartrip is served by the `flipkart` Board and has no row of its own.
  - Ola (`olacareers`, ANI Technologies) is live with 0 postings.

## Discovery

The pool (`data/ats-tenants-merged/turbohire.csv`, not committed) is the union of three sources:

| Source | Labels | Found only here |
|---|---|---|
| The existing pool, from earlier Common Crawl and Wayback mining | 104 | 2 |
| A fresh Wayback CDX sweep of `turbohire.co` (7 pages) | 104 | 3: `pwc`, `tigeranalytics`, `travclan` |
| A Common Crawl sweep of 33 crawls, `CC-MAIN-2023-40` through `CC-MAIN-2026-39` | 71 | 6: `ceat`, `ciplasouthafrica`, `idfy`, `kecrpg`, `maxivision`, `rpglifesciences` |

- **The union is 114 labels, 10 of them new.** 9 of the 10 probe live, 6 of them hiring; `new` is
  vendor infrastructure and probes dead.
- **The Common Crawl sweep:** the index API throttled from `CC-MAIN-2025-43` on, because a sibling
  sweep shared the address. Those crawls were read off `data.commoncrawl.org` instead
  (`CC_DATA_HOST=1`). All 33 completed.
- **The pool's urls** carried Common Crawl's captures (often `/robots.txt`), so they were rewritten
  to `https://{label}.turbohire.co`.
- **The feeders:** both sweeps are now in them, `ATS_HOSTS` in `wayback_feeder.py` and
  `ATS_PATTERNS` in `cc_miner.py`.
- **A cheap oracle:** the organization lookup answers any guessed label (a 404 in about 0.1 s),
  which a later gap search can use.

**The ledger.** `p_turbohire` over the 114 labels wrote 74 live (54 hiring) and 40 dead in 2.6 s,
with no unknown.

- **Checked against an independent signal**, the page `<title>`, on all 114 rows:
  - all 74 live rows name their organization;
  - 28 of the 40 dead rows render "TurboHire";
  - the other 12 dead rows are vendor infrastructure (`identity`, `meet`, `knowledge`, `security`,
    `partner`, `release`, `route`, `new`, `customersuccess`, `identity-dev`) or a live
    organization's referral page (`springernature-referral`, `zeetechandinnovationcentre-referral`).
    Neither is a Board.
- **Duplicates:** the 74 live labels resolve to 74 distinct `OrgID`s, and every label is a
  lowercase bare label, so there are no alias, spelling or casing duplicates.
- **One label was added after review:** `careers`, the vendor's own hiring organization
  ("TurboHire Technologies Private Limited (Official)", its own `OrgID`, 0 postings). No sweep
  found it. The committed ledger therefore holds 115 rows: 75 live (54 hiring) and 40 dead.

## Cost (ADR-0158's bar)

The landing census, less the two demo organizations and the two parked Cipla pages: 70
organizations, 50 hiring, 1,270 postings, 327 tech.

| Part of a run | Size |
|---|---|
| Tokens (~1 KB each) | ~0.1 MB |
| Organization records | 0.38 MB |
| Listings | 2.19 MB |
| Details for tech rows only | 8.14 MB |
| **Total** | **~10.8 MB for 327 tech Jobs, about 33 KB per tech Job** |

ADR-0158's accepted bar is about 2 MB per tech Job, 60 times higher. The Boards land active.
