# gr8people public GraphQL measurement — 2026-10-02

Issue #970's September 29 figures are hypotheses, not this build's census.
Teradata now returns **168 unique postings; 116 pass HeadStart's title/department
tech gate**. The build found 247 candidate hosts, of which 57 public Boards probe
live, 96 dead and 94 unknown. Two same-client aliases and Carrier's owner-approved
parking leave 54 Scrapable Boards and 37 Hiring Boards. All measurements below
were made on October 2.

## Identity and public access (Q1–2, Q8–9, Q18)

The slug is the lower-case careers host: `careers.teradata.com`,
`batesville.workgr8.com`, or `etrade.gr8people.com`. A vendor label alone loses
the distinction between the two vendor domains and cannot locate a vanity site.
`slug_from` reads the host from a full tenant URL or the candidate's public URL.
All 247 pool rows have distinct normalized keys; there are no casing or URL-form
duplicates. Native ids are numeric; the measured sample carries no colon in them.

The anonymous `/jobs` page must be usable before trusting `/graphql`.
Ardene's API lists 22 jobs although `/`, `/jobs` and `/jobs/21006` all return 404.
Those are real expired public routes, not an invented-host-only discriminator.
Five ledger-dead hosts independently returned 404: abglobal, advan6, agency,
aircanada and alliancebernstein, all under gr8people.com. Five ledger-live hosts
returned 200: absolutecare, actonegovernment, actonegroup, adventhealth and
agileone. Empty public Boards (ActOne Government, AllSourcePPS, AllSTEM and AMN)
have a real page and a valid zero-total API response. A 403, DNS failure, timeout,
non-vendor page or unexplained API response remains unknown, never dead.
The 94 unknown rows are unresolved, not evidence of 94 departed customers.

Board-page titles state the employer: `Search Careers at Teradata`,
`Search Careers at Randstad North America`, `Careers at Morgan Stanley E-TRADE`.
The pattern strips only the observed Careers-at wrapper, with an optional Search.
It does not replace the Board's company with each posting's client or legal entity.

## Listing and pagination (Q3–7, Q10–12)

The public listing is `POST https://{host}/graphql`. The page's own frontend
contains two search operations with the same result contract:
`searchJobPostings` and `searchGoogleJobDiscovery`. The browser chooses Google
when its `google-job-discovery` flag is true and `ops-kill-switch-google-cts` is
false. The scraper and prober make that same choice from `__NEXT_DATA__`.
Carrier's flag is false and its Google surface errors; Randstad's is true.
Randstad's native search states 148, including two deleted nodes with GraphQL
`NOT_FOUND` errors, whereas its Google listing cleanly returns 146. Choosing one
resolver globally would either lose Carrier or scrape Randstad's stale index.
No source-side tech, location, language or employment filter is sent.

Anonymous POSTs returned exactly the same first-page content with and without
the page's visitor token on Teradata, Batesville, Ardene and E-TRADE (four hosts).
No handshake, Referer or token refresh is implemented; token lifetime is irrelevant
to these token-free requests. Introspection is disabled. The requested fields
were taken from the live frontend's `jobPostingFields` fragment, not guessed.

Use `first=100` and omit `after` on the initial request. Continue with
`pageInfo.endCursor` until `hasNextPage=false`. Teradata returned 100 + 68 and
168 unique ids. Requests for 500 and 1,000 returned only 100 and **falsely set
hasNextPage=false**. The Google resolver rejected these oversized requests on
All-In-One. The largest Board measured, RDSolutions, returned **4,334 unique
postings over 44 pages**, with no truncation; Carrier returned 4,167 over 42 pages.
Every Board in the final 57-Board scrape census completed without truncation.
Unique ids are compared with the Board's stated total, using ADR-0121's tolerance.
A repeated page/cursor, missing continuation or failed later request marks the
Board truncated while keeping the readable Jobs. A top-level GraphQL error on
HTTP 200 is not an empty Board. Per-node `NOT_FOUND` errors keep the other rows;
their missing ids still count toward the shortfall.

Teradata's sitemap had only **159** job ids versus the API's 168. It is therefore
an independent discrepancy check, not an authoritative listing. Its nine omitted
ids were 220191, 220321, 220329, 220331, 220332, 220395, 220410, 220412 and 220651.
The browser directly showed 168 results and opened job 220261 correctly.

Full `descriptionHTML`, locations, category, dates and pay fields are available
in the listing; no per-job detail pass or pre-detail gate is needed. Browser job
pages and listing descriptions were compared on three postings: Teradata 220261
was 2,932 plain-text characters on both surfaces; Batesville 1140 was 4,494 on
both; E-TRADE 4709 was 1,034 on both. Each pair was exactly equal after stripping
HTML. This is a three-posting check, not a claim of universal equality.
The id-only URL `/jobs/{key}` redirects to the title-slug URL on the first two,
and renders directly on E-TRADE. The scraper serves the id-only public job page,
never the application/login URL. Ardene's unavailable job page is excluded.

## Fields and population (Q13–17, Q21–23)

The final census reads all 57 public hosts; the table and arithmetic below remove
the two measured same-client aliases. Counts are a snapshot, not a future yield
promise. Jobs changed during measurement (e.g. West Star 258 → 256).

| Job field | Non-null out of 11,865 postings | Mapping |
| --- | ---: | --- |
| description | 11,863 | full descriptionHTML, stripped with html_to_text |
| location | 11,660 | every places.nodes.name, deduplicated and semicolon-joined; primaryPlace fallback, then structuredDataJSON.jobLocation |
| department | 11,503 | jobCategory.name |
| remote | 11,659 | REMOTE true, ON_SITE false; HYBRID/unknown null |
| posted_at | 11,758 | postedOn only; no scrape-time/datePosted fallback |
| salary | 704 | native lower/upper bounds, currency and explicit ANNUAL/HOURLY period |
| employment_type | 6,918 | positionType.name; employmentType is often exempt/non-exempt, not full-/part-time |

All locations are retained: Teradata job 220447 names Bengaluru and Hyderabad;
220076 names San Diego and Seattle. E-TRADE's 12 records have no native places,
but JSON-LD names their cities (e.g. Chicago, Illinois, United States). Native
workplaceType distinguishes hybrid from remote even when location text is vague.
Across 6,665 postings with an explicit REMOTE/ON_SITE value and nonempty native
location, native and location-keyword verdicts differ on **113 (1.70%)**. All 113
are native REMOTE with a location lacking the word remote (e.g. Financial
Operations Manager at West Star, located United States). There are **zero**
native ON_SITE records whose location says remote. The native explicit label
supplies information the location fallback misses; HYBRID stays unknown.
Teradata's repeated responses carried unchanged postedOn values; the scraper
keeps missing dates missing rather than reading an uncalibrated fallback.

The native pay fields include a midpoint as well as bounds. Use the floor and
ceiling, not the midpoint. Annual Teradata job 220076 states 178,800–268,200 USD.
Randstad and Rookie Kids supplied HOURLY values. `salary.to_field` preserves the
currency and period for the shared parser. `ON_TARGET_EARNINGS` states no period,
so those values are left to description extraction rather than assumed annual.
No native required-years field was seen in the public fragment; experience remains
available through the existing description/title cascade. Unknown positionType
wording is preserved, except the measured standalone abbreviation Temp is expanded
to Temporary. Six postings across AgileOne Global, AppleOne and Aspiranet carried
`Temp Full Time` or `In-House Temp - Full Time - Non-Exempt`; the shared flags read
both full-time and contract after expansion. A seventh `Full Time Temporary`
posting already reaches both flags. All 21 observed nonempty position labels
were passed through employment_type.flags; unfamiliar labels are not guessed.

The 55-Board set yields **1,307 tech-filter matches from 11,865 postings** (11.0%);
11,606 pass the ingestion English gate (97.8%). These are classifier outcomes,
not a hand-labelled software-role census. Carrier contributes 923 tech-filter
matches from 4,167 postings. Without Carrier the set yields **384 / 7,698**, and
7,447 pass the English gate. Teradata alone contributes 116 / 168.

Captured, serialized raw listing records occupy 148,283,952 bytes for the 55
Boards, or **0.113 MB per tech-filter match**. Without Carrier: 102,947,459 /
384 = **0.268 MB per match**, both comfortably below ADR-0158's approximately
2 MB bar. This is a conservative uncompressed-payload proxy, not a measurement
of future HF growth; facts retain normalized Job fields, not the raw envelope.

## Rate and robots (Q19–20)

The bounded ramp sent 512 successful listing requests: widths 1/4/16/32/64/128,
both on Teradata and across four hosts (Teradata, Batesville, E-TRADE, All-In-One).
All returned 200. At width 128 throughput was 84.64 requests/s on Teradata and
98.68 across hosts. **No refusal knee was found within this sample**; this is not
a claim of unlimited traffic. No detail fan-out is added. headstart/0.1,
curl/8.7.1 and python-requests/2.32 each obtained Teradata's public page with 200.

Teradata and Batesville robots.txt allow User-agent:* (empty Disallow), while
GPTBot and MagnetmeBot are disallowed. Ardene's robots endpoint returns 404.
Nothing follows an application flow, enters applicant data or bypasses a challenge.
Blocked discovery hosts stay unknown.

## Discovery, aliases and backing Boards (Q24)

Wayback's two-domain sweep found **238** hosts. Common Crawl's index API was
throttled; the data-host route completed both domains over **33 crawls**, newest
2026-39 through 2023-40, yielding **109** hosts: 8 only CC found, 101 shared with
Wayback, and 137 only Wayback found. Teradata's vanity host contributes one more:
238 + 8 + 1 = **247 candidates**. The requested upstream scraper/seed paths in
kalil0321/ats-scrapers returned 404; they contributed no seeds. No enumerable
vendor-wide vanity-host roster was found. The miner unions the archive inputs;
the fingerprinter detects both vendor domains and the app-career-site asset on
vanity domains. Captures are candidate-grade; the committed liveness ledger is
the authority. The final pool is also copied to the main checkout's ignored input.

No hostname redirects grouped the 57 live hosts. The public page's
`(visit.orgId, visit.clientId)` identified two non-redirecting pairs. Full public
id sets were equal: Johnson & Johnson / Randstad Sourceright, **49/49**, and
randstadus / Randstad North America, **146/146**. Both pairs enter the alias ledger
as shared-reqs, keeping the descriptive brand host. Organisation alone is unsafe:
ActOne's different client ids are different Boards. Re-run
`scripts/validate/gr8people_shared_clients.py --apply` after a ledger refresh;
the generic redirect scan refuses to overwrite its results. No vendor test
postings were identified among the landed public Boards; infrastructure/internal
pool candidates are dead or unknown, not assumed customer Boards.

The registrable-domain cross-ATS scan reported zero collisions; it cannot see
vendor-hosted clients' external backing ATSes. Carrier demonstrates that blind
spot: browser Apply → Skip & Continue goes to `carrier.wd5.myworkdayjobs.com/jobs`,
a held Workday Board. Its live 1,047-job listing shares **439** requisition ids
with gr8people's 4,167 postings (4,162 distinct stated requisitions). The other
gr8people records' live/dead state was unmeasured in this initial count. The
[freshness follow-up](2026-10-02_carrier-workday-freshness-comparison.md) then read
all 1,054 current Workday details, found all 41 gr8people recent-week requisitions
already held, and checked all 36 gr8people-only recent-month redirects. The owner
approved parking Carrier's gr8people Board; its Workday Board remains active.
No platform-wide backing-Board exclusion is inferred from this one case.

## Verification and reproducibility

The fixtures contain five trimmed, real postings (three Teradata, one E-TRADE,
one Rookie Kids); tokens and visitor
data are omitted. Regression tests cover fields, cursor paging, false terminators,
HTTP/GraphQL failure, deleted nodes, aliases, empty Boards and canonical identity.
The requested `verify_scraper.py gr8people 20` sample included 5 readable Boards
(261 Jobs, every one with a description) and 15 departed/blocked pool candidates.
The exhaustive public-Board census is the stronger completeness check.

`verify-search-filters` ran the live Space harness: **151 semantic checks, zero
violations/errors, atses_without_shape=[]**. The full run is not globally clean:
two working Freshteam widget URLs fail its existing shape (#997); one Zartis
Lever posting and one UnitedHealthGroup Radancy posting now 404; Xpertabs' WP
Job Openings page negotiates 406. None is a gr8people output. Gr8people has no
served rows yet; its live-index URL/filter checks remain a post-pipeline follow-up.
No pipeline, deployment or bench workflow was manually dispatched.

Local notebook: `experiment/gr8people-graphql/LOG.md`, with capture/probe scripts
`recon.py`, `api_probe.py`, `variants.py`, `census.py`, `cc_sweep.py`,
`measure_pool.py`, `pagination_probe.py`, `backing_probe.py` and
`rate_and_spotchecks.py` and `paired_measurement.py`; captures are in its artifacts/
directory and uncommitted.
The committed miner, liveness probe and shared-client validator reproduce the
discovery/landing procedure without those local captures.
