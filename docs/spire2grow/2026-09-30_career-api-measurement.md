# Spire2Grow career API: what it actually does

Measured 2026-09-30, before `headstart.scrapers.spire2grow` was written, against the live API
(`io.spire2grow.com/ies/v1/p`), the vendor's UAT API (`io-uat.spire2grow.com`) and the tenants'
own career hosts. Issue #975 carried the starting hypotheses from the 2026-09-29 coverage probe;
three of them turned out wrong or incomplete (the `workflowid` header, the Board key, the claim
that no other tenant was known). The probe scripts and raw captures are kept locally under
`experiment/spire2grow/` and are not committed; every number a reader needs is below. The
decisions are ADR-0362.

Sample: **4 production workspaces** (every one discovery found), **307 postings** read whole,
**1 UAT demo workspace of 1,547 rows** for paging, **24 non-tenant hosts** for the dead verdict,
and about 1,300 requests in total, most of them the rate-limit ramps.

## 1. A Board is its career-site host

A Spire2Grow career site (`jobs.myntra.com`) is a Flutter web app served from Firebase Hosting on
the customer's own host. At start-up it calls:

```
GET /ies/v1/p/workspaceId?domain=jobs.myntra.com   -> 200 "MYNTRA-93as3"   (text/plain)
GET /ies/v1/p/workspaceId?domain=nope.example.com  -> 404 {"errorMessages":["No Workspace Found for the domain name :: nope.example.com"], ...}
```

and then sends that workspace id as a `workspaceid` header on every other call. The issue
suggested keying the Board on the workspace id. Three measurements say the host instead:

- **The public job link needs the host.** A posting's page is `https://{host}/jobs/{displayId}`
  (§5), and no endpoint maps a workspace back to its host: `workspace/static-content/{ws}` and
  `workspace/theme/{ws}` name logos, colours and a tagline, never a domain.
- **The workspace id is case-sensitive; Board keys are not.** `myntra-93as3` answers `_count`
  with `{"totalCount":0}` and `workspace/theme/myntra-93as3` with 404, where `MYNTRA-93as3`
  answers the Board. `scrapable_boards` compares keys case-folded, so a mixed-case id would need
  `keeps_slug_case` and would still fold on every comparison.
- **An unknown workspace is not an error; an unknown host is.** Any `workspaceid` the API does not
  know answers `_count` and `_search` with HTTP 200 and zero rows, the same as a real workspace
  with nothing open. The domain lookup answers a real 404 for a host that is not a tenant: 24 of
  24 non-tenant hosts measured, among them the vendor's own UAT hosts and `careers.myntra.com`.

The lookup is case-sensitive too (`JOBS.MYNTRA.COM` → 404), so the slug is the lower-cased host.

**One workspace can have several hosts.** Tata Communications' `TCLPROD-c62po` answers on four:
`jobs.tatacommunications.com` (the company's own domain), `tcl-career.spire2grow.com`,
`tcl-career.iexchange.ai` and `i-exchange-row.web.app` (the Firebase site `jobs.myntra.com` and
`jobs.tatacommunications.com` both CNAME to). All four read the same 210 postings. Landing more
than one would serve every posting once per host, so the three second hosts are parked
(ADR-0362) and the company's own host is the Board.

## 2. Headers

| header | measured |
| --- | --- |
| `workspaceid` | required: without it every call answers 401 with an empty body |
| `workflowid` | ignored: a fresh `WFU_<ms>`, the run log's `WFU_<ms>0000`, a constant `WFU_1`, a 2020 timestamp, `hello` and no header at all each answered the same 200 (7 of 7) |
| `language` | no effect: `fr` and no header read the same 53 postings as `en` |
| User-Agent | no effect: `headstart/0.1`, none and `python-requests/2.32` all 200 |

The scraper sends `workspaceid` and the app's own `language: en`, and no `workflowid`.

## 3. The listing is one request, and it is the whole posting

```
GET /ies/v1/p/requisition/_search?page=1&size=1000&selectedSortOrder=desc&selectedSortField=postedOn
    workspaceid: MYNTRA-93as3
-> {"entities": [ ...53 postings... ], "total": 53}
```

- **Pages count from 1.** `page=0` answers 401.
- **`size` is not clamped** up to 2,000: on the UAT demo `IEXCHANGE-UAT-DEMO2-ibtl2` (1,547 rows,
  the largest workspace reachable), `size=500/1000/1500` returned exactly that many and
  `size=2000/5000` all 1,547. The largest production Board is 211, so `size=1000` reads every
  known Board in one call.
- **`total` is truthful.** It equalled the rows served on all 4 production Boards and on the UAT
  demo, and it equals `_count`'s `totalCount`.
- **A result window at 10,000.** `page * size` past 10,000 answers 401 (`page=11&size=1000`,
  `page=6&size=2000`): an Elasticsearch window. No production Board is near it.
- **Small pages are unstable.** Walking Tata Communications at `size=50` returned 211 rows with
  one repeated and one missing on 2 of 3 walks; `size=100` was clean 3 of 3, and the 1,547-row
  UAT demo was clean at `size=100` and `size=1000`. The sort key (`postedOn`) has ties. The
  scraper reads one large page, de-duplicates by `displayId`, and reports a shortfall against
  `total` (ADR-0121).
- **The listing row is the detail.** `/requisition/displayId/{displayId}` returned exactly the
  listing row's keys with the same `jobDescription` (1 of 1 compared). The app's own detail page
  calls it; the scraper does not need to. There is no detail pass. (`/requisition/{r_id}` with the
  internal id answers 401.)

Bytes per posting on the listing: Myntra 5,518, Tata Communications 5,180, Spire 3,847. One run
over the three hiring Boards reads **1,551,149 bytes**.

## 4. Dead versus empty

| case | lookup | `_count` |
| --- | --- | --- |
| tenant with postings (`jobs.myntra.com`) | 200 `MYNTRA-93as3` | `{"totalCount":53}` |
| tenant with none (`godigit-careers.spire2grow.com`) | 200 `GODIGIT-7ekw3` | `{"totalCount":0}` |
| not a tenant (24 hosts) | 404 `No Workspace Found for the domain name` | — |

So `p_spire2grow` asks the lookup first: its 404 is the dead verdict, and only a workspace it
returned is counted. The count is read from `_count`, which is unmetered (§6), not `_search`.
`io.spire2grow.com` is one fixed host, so a DNS failure is the resolver's, never a verdict.

The career host itself proves nothing: Firebase Hosting answers **200 with the app shell for any
path** on the host (`/jobs/2163749296`, `/jobs/r_…`, `/jobs/nonsense-xyz`, `/definitely-not-a-route`,
4 of 4).

## 5. The public job link

`https://{host}/jobs/{displayId}`. The app builds its share link as `origin + "/jobs/" + id`, and
its `/jobs/{x}` route fetches `/requisition/displayId/{x}`. Rendered in headed Chrome (Playwright,
`channel=chrome`), because an HTTP status cannot tell (§4):

- `https://jobs.myntra.com/jobs/2163749296` → the posting ("Udaan- Talent Acquisition", Job ID
  2163749296, Bangalore), via `requisition/displayId/2163749296` → 200.
- `https://jobs.tatacommunications.com/jobs/5733952549` and `https://jobs.spire.ai/jobs/S-032` →
  each posting, so the shape holds on all three hiring Boards.
- `https://jobs.myntra.com/jobs/r_51b2743def7f397a90d961a02d915ac0` (the internal id) → "Oops! The
  job you're looking for doesn't seem to exist", via `requisition/displayId/r_…` → 404.

Headless was not retried: headed Chrome rendered every page asked. `displayId` is digits on Myntra and Tata
Communications (`2163749296`, one `07178950` with a leading zero) and `S-048`-style on Spire; none
holds a `:` (0 of 307), so `board_identity.board_of` splits the Job id correctly.

## 6. Rate limit: the search is metered, per client, across tenants

| probe | result |
| --- | --- |
| `_count`, 150 sequential | 150 × 200 (2.9 req/s) |
| `_count`, 600 at concurrency 64 | 600 × 200 (167 req/s) |
| `_search`, 60 back to back | 6 × 200 then 429 `Too many requests`, `X-Rate-Limit-Retry-After-Seconds` 53 → 31 |
| `_search`, 14 at 10 s spacing | 8 × 200, 6 × 429 |
| `_search` drained on Myntra, then 3 calls each on Tata and Spire | 6 of 6 → 429 |
| `displayId` detail and `_count` right after | 200, 200 |

`X-Rate-Limit-Remaining` alternated between two counters (1, 3, 0, 2, 1, 0 …), which reads as two
servers allowing about two calls a minute each. The meter is per client, not per workspace. The scraper spaces every `_search` in the process 31 s apart through one `Pacer`, rests
it for the stated window on a 429, and fails the Board after three refusals rather than serving a
short list as whole; past the first page, a refusal that never clears truncates the Board instead.
One Board is one `_search`: the four known Boards took 94 s in all through the real scraper.
The prober never calls `_search`.

## 7. Fields

Across the 307 postings of the three hiring workspaces:

| field | source | fill | note |
| --- | --- | ---: | --- |
| title | `jobTitle` | 307 | |
| description | `jobDescription` (HTML) | 307 | `aboutCompany` is employer boilerplate, not read |
| location | `jobLocation[].fqLocationName` | 307 | one distinct place per posting; 38 of 43 Spire rows list it twice, joined without repeats |
| department | `departmentName` | 263 | Myntra 53 of 53 (`COE (F1121)`-style codes), Tata 210 of 211, Spire 0 of 43 |
| posted_at | `jobPosting.startDate` (epoch ms) | 307 | what the page shows as "Posted"; unchanged across two fetches 20 s apart (53 of 53); equal to `createdOn` on 303 |
| experience | `requiredExperienceInMonths` | 307 | months; 12 of 614 bounds not whole years; served as `N-M months` |
| employment_type | `employmentType` | 307 | FULL_TIME 304, PART_TIME 2, APPRENTICESHIP 1 |
| remote | `jobType` | 247 stated | ONSITE 36, HYBRID 4, `NA` 207, absent 60; no REMOTE, and no location or title says remote (0 of 307) |
| salary | — | 0 | no field whose name suggests pay (`sal`/`ctc`/`pay`/`comp`) on any posting |

`jobPosting.endDate` was in the future on 307 of 307 listed postings and is not mapped. `recruiter`
names a person and their email on 305 postings and is never read. `skills`, `requiredEducation`,
`hotJob`, `isFresherJob` and `fcpr` are not mapped.

**Company name.** `aboutCompany` names Myntra (53 of 53) and Tata Communications (211 of 211);
Spire's postings carry none, and `static-content`'s `name` is a tagline ("Myntra is India's leading
fashion and lifestyle destination"). The humanised host serves "Myntra", "Spire" and "Godigit";
`jobs.tatacommunications.com` humanises to "Tatacommunications", so it is named in
`config/company_names.csv`.

## 8. Population

| Board | workspace | postings | tech (`is_tech`) | English |
| --- | --- | ---: | ---: | ---: |
| `jobs.myntra.com` | `MYNTRA-93as3` | 53 | 6 | 53 |
| `jobs.tatacommunications.com` | `TCLPROD-c62po` | 211 | 60 | 211 |
| `jobs.spire.ai` | `SPIRETA-t207n` | 43 | 11 | 43 |
| `godigit-careers.spire2grow.com` | `GODIGIT-7ekw3` | 0 | 0 | — |
| **total** | | **307** | **77 (25.1%)** | **307** |

Language is `langdetect` over title plus description. Tata Communications' 211 fell to 210 between
the census and the scrape an hour later. All 43 of Spire's postings (the vendor hiring for itself)
were posted about sixteen months ago and never updated.

**Overlap with held Boards.** Myntra's rows in the darwinbox, freshteam, ripplehire and greenhouse
ledgers are all dead (issue #975), and the one Tata Communications row held elsewhere
(`eightfold:tatacommunications.eightfold.ai`) is dead. Nothing collides.

## 9. Discovery

The vendor publishes no roster. The sources, and what each found:

- **The production build's CSP.** Every production host answers with
  `frame-ancestors 'self' https://.spire2grow.com … https://jobs.spire.ai https://jobs.tatacommunications.com https://jobs.myntra.com`:
  the three hiring hosts, in one header.
- **Certificate transparency** (certspotter, on `spire2grow.com`, `iexchange.ai`, `spire.ai`,
  `talentexchange.ai` and `recruitment.exchange`): 22 relevant hosts, which added
  `godigit-careers.spire2grow.com`, two of the second hosts of §1 and 16 hosts that are not
  production tenants.
- **The vendor's UAT API.** 11 of those 16 resolve on `io-uat.spire2grow.com` instead
  (`genpact-jobs`, `ps-jobs` = `IEXCHANGE-UAT-DEMO2`, `ci-jobs` = `IEXCHANGE-UAT-DEMO1`, …): staging
  copies and demos, which the production lookup reads as dead. Not landed.
- **Wayback CDX** on `io.spire2grow.com` and `io-public.spire2grow.com`: 2 domains
  (`jobs.myntra.com`, `jobs.tatacommunications.com`) and 2 workspace ids
  (`SpireDevTestTenant-te70l`, `TCLPROD-c62po`), nothing new.
- **DNS**: `jobs.myntra.com` is a CNAME to `myntra-jobs.iexchange.ai` and then
  `i-exchange-row.web.app`; `jobs.tatacommunications.com` to the latter directly. That Firebase
  site is the third second host of §1.
- **Web search** ("spire2grow" careers): `genpact-jobs.spire2grow.com`, a UAT host.
- **Common Crawl** (index API, the 18 newest crawls, `*.spire2grow.com` and `*.iexchange.ai`): only
  partly measured. The index answered 7 of 18 `spire2grow.com` queries, each naming the vendor's
  marketing host alone (`www.spire2grow.com`, `spire2grow.com`), and none of 18 `iexchange.ai`
  queries (502, 504 and 400). A tenant's pages are a Flutter shell with no crawlable job links, and
  its host is the customer's own, so a crawl can only name one by its vendor-zone alias; the
  unanswered queries are not a zero.

The pool is 23 hosts; the ledger reads **7 live** (4 workspaces) and **16 dead**. The careers-page
fingerprinter now knows the Flutter shell's app title (`apple-mobile-web-app-title` =
`iexchange`, on 3 of 3 tenant hosts), so a new tenant surfaces when a sweep reaches its host.

## 10. Storage cost per tech Job

One run reads 1,551,149 listing bytes across the three hiring Boards for 77 tech Jobs, about
**20 KB fetched per tech Job**. Priced the way ADR-0158 priced jazzhr (~10.7 GB for 99,963
postings, about 107 KB stored per posting), the 307 postings cost about 33 MB for 77 tech Jobs,
**about 0.43 MB per tech Job** against the accepted bar of about 2 MB. The yield is small in
absolute terms (77 tech Jobs, 66 of them fresh) but cheap per Job, so the ATS lands enabled.
