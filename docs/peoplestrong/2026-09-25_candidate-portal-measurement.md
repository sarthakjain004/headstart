# PeopleStrong candidate portals: what the API actually does

Measured 2026-09-25, before `headstart.scrapers.peoplestrong` was written, against the live
candidate-portal API. Two earlier claims in this repo were wrong: `docs/learnings.md` (2026-06-21)
dropped PeopleStrong because its "candidate portals are login-walled", and CLAUDE.md filed it as an
"Angular SPA XHR" to reverse-engineer. The portals are an Angular SPA, but the API it calls is public
and needs no session. The decisions are ADR-0234.

Sample: a census of **423 pool labels** (Wayback, Common Crawl, GitHub code search, local captures;
the pool reached 426 once the oldest Common Crawl indexes were swept, and the ledger run probed all 426),
then a full read of **all 56 hiring Boards**: every listing page and every posting's detail,
**35,732 postings**, zero failures. About 90,000 requests in total. The run record and captures stay
local (`experiment/peoplestrong-portal/`); every number a reader needs is below.

## 1. A Board is a subdomain label

Each tenant's candidate portal is `{label}.peoplestrong.com` (e.g. `hdfcergocareers`,
`careers-bmwtechworks`). The SPA calls its own host's API at `/api/cp/rest/altone/cp/`, so the label
is the URL, the API key and the discovery key at once.

- **Case-insensitive.** `Careers-BMWTechWorks` reads the same Board, and the portal's own `urlinfo`
  echoes the label lowercased. `slug_from` lowercases it and reduces a host or URL to its label.
- **One host, no pods.** `*.altone.io` and `*.peoplestrong.in` (named in `fingerprint_careers.py`)
  resolve no tenant.
- **A wildcard zone.** An invented label resolves and answers. A DNS failure is therefore never a
  verdict about a tenant.
- **One company can run several portals** (`sobha-careers` and `sobhalimited-careers` are two
  Sobha entities). Each is its own Board: no job code appears on two Boards (§8).
- **Native ids never contain `:`.** Job codes contain only letters, digits, `/` and `-`.
- **Discovery emits one spelling.** All 426 pool labels (Wayback, Common Crawl, GitHub, local
  captures) are lowercase `[a-z0-9-]`, none a host or URL; `slug_from` lowercases anyway.

## 2. The listing: public, 99 rows a page

```
POST https://{label}.peoplestrong.com/api/cp/rest/altone/cp/jobs/v1?offset={n}&limit={k}
Content-Type: application/json
{}
```

The body the page sends carries only band and grade filters, empty by default. No cookie, session,
token or Referer is needed, including on HDFC ERGO's 1,923-posting Board (an upstream scraper had
reported that one as needing "session/payload cracking").

| request | rows |
| --- | --- |
| `limit=10` | 10 |
| `limit=50` / `99` | 50 / 99 |
| `limit=100`, `500`, `1000`, `2000`, `5000` | **99** |
| `offset=5&limit=10` vs `offset=0` | starts at row 5 (a row offset, not a page) |

So `limit` clamps silently at 99, and `offset` counts rows from 0. A walk at 99 per page, stepping
by the rows read and ending on a short page, read HDFC ERGO whole twice (1,923 of 1,923 unique over
20 pages) and every one of the 56 hiring Boards whole (stated `totalRecords` = rows read, 56 of 56).
The largest Board is Muthoot Fincorp (`mpgcareers`) at 16,315 postings.

The page renders the API's response as-is (`this.joblist = r.response`), and `requisitionStatus` was
`OPEN` on all 35,732 postings, so no rows are served that the board hides.

**The listing carries no description.** A row has the title, code, dates, place path, org unit,
`expRange` and skill keywords (85.7%); its `role` and `roleType` text fields were null or a label on
every one of 35,732 rows. The description exists only on the detail (§4).

**No other surface.** `robots.txt` and `sitemap.xml` return the SPA shell or a 404, and page routes
(`/job/joblist`) return HTTP 404 around the SPA shell, so page status says nothing.

## 3. Dead versus empty: the platform answers three different ways

One `POST jobs/v1?limit=1` per label, over 423 labels:

| answer | labels | meaning |
| --- | --- | --- |
| 200 with `totalRecords` > 0 | 56 | a hiring portal |
| 200 with `totalRecords: 0` | 47 | a live portal with nothing open |
| 200 `{"response":null,"messageCode":{"code":201,"messages":[{"message":"Inside getTpUrl(...)"}]}}` | ~192 | not a registered candidate portal |
| 403, body "Request forbidden by administrative rules." (HAProxy's deny page) | ~65 | a departed tenant |
| HTML 404/200, TLS errors | ~57 | PeopleStrong's own non-portal hosts (LMS, helpdesk, alumni, marketing) |

- **The 201 envelope** is what an invented label (`zzqxnotatenant123`) gets, and also what the hosts
  the June note called login-walled get (`abfrl`, `aavashrms`). Those are PeopleStrong HRMS logins;
  ABFRL's candidate portal is `abfrlcareers` (67 open). `urlinfo` agrees, answering "Something went
  wrong" for these hosts.
- **The HAProxy 403** is host-scoped. It's the same for our User-Agent and a browser's, on `/`,
  on page routes and on the API, from the same Imperva edge IP as live tenants. It never appeared on
  any of the 103 registered portals. The hosts checked belong to companies that left: CitiusTech is
  live on RippleHire (64 jobs in our ledger), Wayback last saw `exlcareers` in May 2024 and
  `tatapowercareers` in May 2022, and `careers-bounce` was already a 404 in 2022. Over the spare
  egress (Cloudflare WARP, a different IPv6 address), `exlcareers`, `tatapowercareers`, `citiustech`
  and `careers-bounce` got the same deny page, while `larsentoubrocareers` (1,392) and `abfrl` (the
  201 envelope) answered as they did directly. The prober reads the page as DEAD, matched on its
  exact text, only when a pinned direct ask and a pinned spare-egress ask both get it: a bare 403 trips no prober gate, so an
  IP-wide block serving the same page must not read as a departed tenant. A different 403 body
  (the marketing host's "Request Forbidden") stays UNKNOWN.
- **Empty is stable.** All 47 empty portals, re-fetched three times each (141 fetches), stayed 0.
- **A user's click lands.** `https://{label}.peoplestrong.com/job/detail/{code}` answers a browser
  200, and it equals the API's own `jobDetailUrl` on 35,732 of 35,732 postings.

## 4. The detail carries the description

```
GET .../cp/job/{code with "/" as "_"}/v2?part=basic,organisational,descriprion,workflow,skill,
    qualification,certification,language,applied&isReqId=false
```

That is the part list the job page itself requests. `descriprion` is the API's own spelling. The
upstream implementation that found the API's text "incomplete" and rendered pages instead was asking
`part=basic` alone.

Per `Job` field, the share of 35,732 postings each surface fills:

| `Job` field | listing | detail |
| --- | --- | --- |
| title | 100% (`jobTitle`) | 100% (`jobTitle`, equal on every posting) |
| department | 100% (`organizationUnit`) | 0% (`departmentHierarchy`) |
| location | 100% (`locationHierarchyComplete`) | 99.9% |
| posted_at | 100% (`jobPostedDate`) | 100% (`CandidatePortalStartDate`, equal on every posting) |
| experience | 97.5% (`expRange`) | 97.1% (`maximumExp`) |
| description | — | 99.8% (`jobDescription`) |
| employment_type | — | 91.5% (`employmentType`) |
| salary | 48.2% (`CTCRange`) | 64.0% (`maxSalary`, non-zero) |
| remote | — | 0% (`Onsite`) |

Other detail fields:

| field (detail) | share of 35,732 |
| --- | --- |
| `jobDescription` (HTML) | 99.8% |
| `employmentType` | 91.5% |
| `maximumExp` / `minimumExp` | 97.1% / 77.8% |
| `minSalary` / `maxSalary` (non-zero) | 63.8% / 64.0% |
| `currency` | 18.9% |
| `Onsite`, `departmentHierarchy` | 0% |

- The median description is 1,743 characters (10th percentile 496). 58 are empty, and 2 (Equitas)
  are a single embedded `data:image/png` with no text.
- **A missing code is a silent empty.** A code the endpoint does not hold (closed, or another
  tenant's) answers **200** with a ~250-byte skeleton: zero salaries, no `jobTitle`, no description.
  This was the same on a live tenant and an invented one. The scraper counts it as a lost detail
  ("no jobTitle on a 200").
- **The detail changes nothing the listing states.** Title differs on 0 of 35,732, and `jobPostedDate`
  equals the detail's `CandidatePortalStartDate` on 35,732 of 35,732.
- **No token, UTF-8 JSON** (`Content-Type: application/json`).

## 5. Fields

**Tech gate: exact.** The listing states `jobTitle` and `organizationUnit` (the department, 100% of
rows), and the detail overrides neither. 1,964 of 35,732 postings pass `tech_filter.is_tech`
(5.5%), over 36 of the 56 hiring Boards: Bajaj Finance 499, L&T 455, QualityKiosk 403, NewVision
154, BMW TechWorks 94, ABG 90, Muthoot 51.

**Location.** `locationHierarchyComplete` names one place per posting as a `>` path
("India>Maharashtra>Pune>Pune"). A `,` inside a segment belongs to a place name ("Industry House,
Bangalore", 1,724 rows); 14 postings have none. The root is the tenant's own choice: a country on
most Boards, but sales territories on Muthoot's ("TERRITORY-II>NORTH-1>NCR>…", 16,100 of its
16,315 rows). Depth runs from 1 to 9 segments. Served as a comma list in the provider's order with repeated
segments dropped; `geo.classify` reads both shapes as India.

**Remote.** No surface states it (`Onsite` null on all 35,732), so it is read off the location text.

**Experience.** The listing's `expRange` ("8-14 years", 97.5%) equals the detail's min–max and is
already the shape `experience.from_field` reads.

**Dates are real.** 243 postings on three Boards, fetched twice 8 s apart, carried identical
`jobPostedDate` and `jobClosureDate` both times; the dates are date-only, so a fabricated "now" would
read as today on every posting. `jobPostedDate` is the portal start date (equal on 35,732 of 35,732),
and 0 of 35,732 listed postings are past their own `jobClosureDate`. Ages run long: median 94 days,
90th percentile 644, max 1,444. Boards keep evergreen requisitions open for years.

**Employment type.** `Permanent` 31,105, blank 3,045, `On Roll` 397, `Employee` 320, `Full Time`
291, `Direct Overseas Hire` 141, `Contract` 102, `Regular` 45, and a long tail. `employment_type_
filter.flags` reads none of `On Roll`, `Employee` or `Regular`, the Indian payroll words for a
permanent hire, so those three are served as "Full Time (On Roll)" and similar. Everything else
passes through. One shared-filter defect showed up here and is not fixed by this change:
`flags("Third Party Agency")` reads part-time (the substring "part" in "Party", 3 postings).

**Salary: not served.** The job page shows the salary line only where the entity's display config,
`GET /api/cp/rest/altone/api/cp/security/job/{entityID}` (public), sets `ctcMaxRendered`. That was
true for 365 of 35,732 postings, 103 of them with a non-zero value, many on the vendor's demo tenant.
Even when shown, the page prints a bare `min - max` with no currency or period, in mixed units
("800000 - 1000000" beside "23 - 37", placeholders like "1 - 1"). A figure the employer does not
publish stays unpublished, pyjamahr's rule. The description's own figures still reach Tier 2.
`employmentType` is gated the same way (`employmentTypeRendered`, 20,516 postings) but is served
regardless: it is a plain fact about the job, not a sensitive figure (a decision recorded in the ADR).

**Company name.** Nothing names the employer at Board level: `urlinfo.title` is empty or null on 90
of 104 live portals (the rest read "Candidate portal" twice, "Infra-Careers", "PeopleStrong-Careers").
Each posting's `organizationUnitComplete` starts with a legal entity, but that can be a subsidiary
("NOVERRA HOSPITALITY PRIVATE LIMITED" on Lodha's Board) and needs the detail pass. The label is
readable ("hdfcergocareers"), so it stays the name.

**Language.** 1,987 of 2,000 sampled descriptions detect as English (99.4%).

## 6. One rate limit spans the platform, and the edge drops connections

**The budget.** Kong answers every request with `X-RateLimit-Limit-minute: 5000`. The
`Remaining-minute` counter fell by one per request **across four different hosts**, including an
invented one, and across endpoints (`jobs/v1`, `urlinfo`). So it is one budget per client IP for the
whole platform. It is a fixed calendar-minute window: Remaining reset to 4,999 at each UTC minute.
At 16 threads, 10,555 requests ran in 130 s with no refusal: 81 req/s on average, and 4,268 in the
one full minute measured (71 req/s). At 32 threads, the
5,000 were spent in ~33 s and the rest got **429 `{"message":"API rate limit exceeded"}`** with
Remaining 0 and no Retry-After. The scraper spaces request starts process-wide at 16 ms (3,750 a
minute), and on a 429 rests the whole process to the window's end. The prober gates
`peoplestrong.com` at 50 req/s.

**The connections.** The edge speaks HTTP/1.1 and answers `Connection: close` (60 of 60), so every
request opens a new TCP+TLS connection. On 60 fresh connections, **18 took ~2.0 s to connect**
against a 25 ms median: the first SYN dropped and retransmitted. This happened at ~5 requests/s,
so it's the edge's baseline behaviour, not a response to our load. Server time, from handshake to
first byte, is a median 73 ms. The same latency showed through the repo's fetch seam (median 0.20 s,
90th percentile 1.64 s) and a plain `requests.Session` (0.25 s / 1.38 s), so this is the edge, not
our client. Consequences for throughput on L&T's 1,394 details:

| transport | width | details/s |
| --- | --- | --- |
| threads | 16 | 20.7 |
| threads | 48 | 39.3 |
| multiplexed (`AsyncSession`) | 16 / 48 | 13.0 / 13.5 |

Width buys rate because the time goes to waiting on connects. The multiplexed path is capped by
the shared session's 10-connection default (`curl_cffi` `max_clients=10`) and has no reused
connection to multiplex over. The scraper runs 48 threads with `async_fanout = False`.

**User-Agent.** `headstart/0.1`, curl's default and `python-requests/2.32` all get 200.

**Sizes.** A 99-row listing page is ~126 KB (~1.3 KB a row). A full detail averages 7.7 KB (ABG's
run to ~17 KB).

## 7. The pool, the vendor's demo tenant, and PeopleStrong's own hosts

`*.peoplestrong.com` carries a wildcard certificate, so certificate transparency names 36 hosts,
none of them a candidate portal. Discovery sources (distinct labels, and how many only that source
found) are in ADR-0234. `candidate.peoplestrong.com` is **PeopleStrong's own demo tenant**: every
job code is `BOS/…`, titles are "Test Job 1909", "sdfghj sdfg" and "Excel Job patch 17sep", and org
units are "Company test" and "Company Y". It is in `config.EXCLUDED_BOARDS`. QualityKiosk's 380
titles containing "test" are a QA company's real Test Engineer roles.

## 8. Duplicates

- **Within PeopleStrong:** over the 56 fully read Boards, no job code appears on two Boards, and no
  (title, location, first 300 description characters) repeats across Boards. Every hiring Board is
  one employer or group (the org-unit root names it: ABG's spans "Financial Services" and
  "Cement"). No alias ledger is needed.
- **Across ATSes:** six employers we already hold elsewhere were scraped live with our own scrapers
  and compared title by title:

| PeopleStrong Board | postings | other Board | its jobs | same title |
| --- | --- | --- | --- | --- |
| `careers-bmwtechworks` | 106 | `zwayam:bmwtechworks.openings.co` | 27 | 2 (one title, posted twice) |
| `rblcareers` | 4 | `ripplehire:rblbank` | 2 | 0 |
| `tatamotorscareers` | 3 | `successfactors:careers.tatamotors.com` | 144 | 0 |
| `careers-akasa` | 7 | `zoho:akasaair` | 20 | 0 |
| `leindiacareers` | 25 | `cornerstone:linde` | 1,008 | 0 |
| `omegacareers` | 34 | `bamboohr:omegahealth` | 16 | 0 |

PeopleStrong is not a skin over any Board we scrape.

## 9. Reproduction

```bash
# the listing: public, clamps at 99, totalRecords is the Board's whole count
curl -sS -X POST -H 'Content-Type: application/json' -d '{}' \
  'https://hdfcergocareers.peoplestrong.com/api/cp/rest/altone/cp/jobs/v1?offset=0&limit=5000' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["totalRecords"], len(d["response"]))'
# not a portal: the 201 envelope
curl -sS -X POST -H 'Content-Type: application/json' -d '{}' \
  'https://abfrl.peoplestrong.com/api/cp/rest/altone/cp/jobs/v1?offset=0&limit=1'
# a departed tenant: HAProxy's deny page
curl -sS 'https://exlcareers.peoplestrong.com/api/cp/rest/altone/cp/urlinfo'
# the shared budget, in the response headers
curl -sS -D- -o /dev/null 'https://lodhacareers.peoplestrong.com/api/cp/rest/altone/cp/urlinfo' \
  | grep -i ratelimit
```
