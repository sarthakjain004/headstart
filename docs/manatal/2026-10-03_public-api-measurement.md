# Manatal public careers surfaces: live measurement

Measured **2026-10-03**, on the branch adding the selected ATS scrapers. This report records observations, not a claim that every Manatal installation has been tested. Raw captures and scripts are local under `experiment/manatal-public-api/`; the report stands alone without those ignored files.

## Decision evidence

Manatal has **two materially different public publishing systems**:

1. The legacy Board, `https://www.careers-page.com/{slug}`, has an anonymous documented full-description JSON listing at `https://api.manatal.com/open/v3/career-page/{slug}/jobs/`. Twenty complete Boards returned 921 distinct jobs using 26 listing requests and 4,235,969 decoded body bytes. A title/verified-department tech gate kept 223 rows, **24.21%**, or **18,995 decoded bytes per kept row**. This is a purposive sample, not a platform-wide estimate, but is comfortably below the approximate 2 MB/tech-job comparison point in ADR-0158.
2. Advanced Career Pages use a subdomain or custom host and UUID JobPost URLs, such as `https://manatal.careers-page.com/jobs/{uuid}`. They are server-rendered and paginated. Manatal's own advanced Board returned 40 posts versus its legacy API's 32 jobs. These are not alternate addresses for an identical list.

The vendor explicitly distinguishes underlying **Jobs** from published **Job Posts**, including many-to-many linking. A legacy slug and an advanced host must retain distinct source identities; do not map an advanced URL to the legacy API and call it complete. Initial Board selection should choose the employer-endorsed publication surface and avoid enabling mirrored old/new surfaces for the same account without reconciliation. [Vendor model](https://support.manatal.com/docs/advanced-career-page-link-job-post), [migration behavior](https://support.manatal.com/docs/upgrade-to-the-advanced-career-page).

**Advanced operating limit:** a four-request warm burst succeeded, but a subsequent four-worker pagination walk on MicroSourcing received HTTP 429 with `Cf-Mitigated: challenge` on pages 6 and 7. Measurement stopped with no retry or challenge bypass. The first five pages contained 100 unique posts out of the stated 1,967. This is a measured partial read, not proof of a pagination cap or successful full scrape. No exact quota, safe sustained rate or retry interval was established. The native browser also displayed Cloudflare verification on Manatal's advanced Board; no CAPTCHA was interacted with.

## Surfaces and identities

| Property | Legacy | Advanced |
|---|---|---|
| Board identity | Exact published slug after `careers-page.com/` | Actual public careers host; preserve custom host until an alias is proven |
| Posting identity | API numeric `id` plus public `hash`; preserve the returned hash for the browser URL | Published JobPost UUID from `data-job-id` and `/jobs/{uuid}` |
| Public job URL | `https://www.careers-page.com/{slug}/job/{hash}` | `https://{host}/jobs/{uuid}` |
| Listing | `/open/v3/career-page/{slug}/jobs/?page=1&page_size=100` | Public HTML, followed through its actual pagination links |
| Detail | API `/open/v3/career-page/{slug}/jobs/{numeric_id}/`; identical to list object in 13/13 comparisons | HTML title and `.job-post-description`; one detail fully inspected before the later refusal |
| Company name | Board `<title>` such as ` - 10Folders \| Career Page`; trim the fixed wrapper | Board `h4.text-h4` text `Jobs at Endowus`; detail title `Title \| Company` |
| Main trap | `organization_name` can mean department or client; it is not universally the employer | Old Jobs and new JobPosts can differ in titles, counts and location multiplicity |

The numeric IDs in all 921 legacy rows contained no colon. Public hashes were 8 characters in 876 rows, 6 in 33 and 5 in 12; do not require an eight-character hash. All 2,824 upstream candidate slugs were lowercase. The actual lowercase `manatal` API and HTML work, while `Manatal` returns 404 on both. Case normalization must use an observed canonical slug; the result does not prove an unseen mixed-case tenant never exists.

The first upstream checked, `kalil0321/ats-scrapers`, had no Manatal implementation in its current main tree. A second upstream, `datascry/openroles`, assumes no listing JSON, no pagination and one JSON-LD block on every detail. Those assumptions did not survive measurement: the public JSON API works, larger Boards paginate, and only 2/13 sampled legacy HTML details contained JSON-LD.

## Legacy listing and pagination

The documented endpoint returned `count`, `next`, `previous`, `results`. No credential, cookie or public bootstrap token was supplied. A per-job detail API returns the same object as a list row; it did not add a date or employment field. The legacy Board's own search UI calls `/api/v1.0/c/{slug}/jobs/` on `www.careers-page.com`, but that representation omits `is_remote` and `contract_details` in the measured vendor sample. The documented v3 surface is richer. [Official endpoint](https://developers.manatal.com/reference/career-page_jobs_list), [API introduction](https://developers.manatal.com/reference/getting-started).

Pagination was measured on `24-mag`, with 344 jobs:

| Request | Observed result |
|---|---|
| No pagination parameters | 10 rows, count344, next page2 |
| `page=0&page_size=2` | 404 `{"detail":"Invalid page."}` |
| `page=1&page_size=2`, then page2 | Two rows each; distinct IDs |
| `page=1&page_size=1000` | **100 rows**, count344, next still echoes `page_size=1000` |
| `page_size=100`, pages1–4 | 100,100,100,44; 344 unique IDs, matching count |
| `page=5&page_size=100` | 404 `{"detail":"Invalid page."}` |
| Explicit impossible `search` value | 200, count0, nextnull, emptyresults |

Use size100 and the measured pagination terminator. Do not equate an invalid-page 404 with a dead tenant. `next` URLs name `core.api.manatal.com` even when the request used `api.manatal.com`; either deliberately allow both documented/observed hosts with shared request control, or keep the known API host while incrementing the page. Do not silently follow arbitrary foreign hosts.

The HTML search code exposes `search`, organization and location filters. They reduce the population and must not be used as tenant identity. For Manatal's own legacy Board, its 32 public HTML hashes exactly matched the 32 API hashes.

## Complete legacy population sample

| Board | Jobs = unique IDs = unique hashes = API count | Requests at100 | Decoded bytes |
|---|---:|---:|---:|
| manatal | 32 | 1 | 219,751 |
| 10folders | 2 | 1 | 3,302 |
| 5ivetech-recruitment | 32 | 1 | 174,965 |
| bayantech-2 | 4 | 1 | 11,827 |
| bazaar-technologies | 8 | 1 | 24,208 |
| be-it-recruitment | 14 | 1 | 81,386 |
| blackfluoai | 21 | 1 | 64,930 |
| 10-percent-recruiting | 23 | 1 | 146,819 |
| 1010-solutions | 4 | 1 | 10,626 |
| 10xtalents | 11 | 1 | 91,213 |
| 24-mag | 344 | 4 | 2,192,989 |
| 1st-jobscom | 256 | 3 | 826,828 |
| 2020-engineering | 10 | 1 | 18,350 |
| 2020people2 | 12 | 1 | 46,107 |
| 2nd-chance-staffing | 3 | 1 | 7,350 |
| chooch-intelligence-technologies | 1 | 1 | 3,628 |
| cleantechtalent | 37 | 1 | 168,957 |
| code-recruitment | 0 | 1 | 52 |
| deftdevtech | 102 | 2 | 129,099 |
| geodata-systems-technologies-inc | 5 | 1 | 13,582 |

There were no repeated numeric IDs or hashes across these Boards. That observation does not exclude re-posted agency advertisements under new IDs or across another ATS.

## Legacy field coverage and traps

Denominator: **921 job rows**, including the non-tech population. API details were compared for 13 selected rows across six Boards; 13/13 entire objects equaled the corresponding list object.

| Job field / source fact | Coverage | Interpretation |
|---|---:|---|
| Numeric `id`, public `hash`, `position_name` | 921/921 each | Preserve source title and hash case; no fabricated slug |
| `description` after HTML-to-text | 904/921 | Empty in17. Detail API does not automatically recover an empty listing description |
| `location_display`, country | 784/921 each | Native city/state/country/address/zipcode also available; no multi-location array in this sample |
| `is_remote` boolean | 419/921 | true399,false20,null502. Null must stay unknown unless a location explicitly states remote |
| `organization_name` | 56/921 | Often absent. Board HTML declares `organization_singular_name`; measured values include `department` and `client` |
| `contract_details` | 921/921 | full_time602,contractor292,consultancy20,internship3,temporary3,part_time1 |
| Visible structured salary | 49/921 | Currency code present49/49; period absent. Some visible records still have null bounds |
| Native posted date, experience, requisition | 0/921 | Do not infer them from hash, crawl time or numeric ID |

`organization_name` is a department on Manatal, Bazaar Technologies and Blackfluo's measured HTML configuration. It is a client category on 10Folders, Integrity Recruitment (`be-it-recruitment`), 24-MAG and 1st-jobs.com. A missing configuration is unknown, not proof that the label means department. The safe measured department gate retained223 rows versus222 by title only; passing every raw organization string also retained223 here, but those identical sample counts do not justify a wrong general mapping.

All six observed contract labels were passed through the repository's current employment flag function. Full/part/internship/contractor/temporary map through the existing rules. `consultancy` receives the repository's fallback full-time verdict rather than a contract verdict; a scraper should make a deliberate source-label mapping if consultancy is intended to be filterable as contract, rather than assume the raw label does so.

Salary requires particular care. Legacy fields are `salary_min`, `salary_max`, `currency_code`, gated by `is_salary_visible`; no period is emitted. A real C# developer advert (`5ivetech-recruitment`, ID116821/hash7566WX) has GBP450–650 and its description explicitly says a daily ceiling. Rendering that as an annual range would be wrong. Another posting (`10folders`,2665834/L8V8R88V) states USD350–500 without a period even in its public HTML. Keep an unstated period unknown. A generic parser declining a low value is not a valid guarantee for all currencies/amounts. Prefer a source-specific handling decision or description-derived salary when its unit is actually stated.

Descriptions ranged from0 to48,657 HTML characters, median4,159. Two of13 legacy HTML detail samples, both on1st-jobs.com, contained JSON-LD with a `datePosted`; both had the same timestamp to microsecond precision and `validThrough` earlier than `datePosted` and already expired despite being currently listed. The source did not supply enough reliable, uniform evidence to use a per-detail date fallback as a universal posting date. The other11 details had no JSON-LD.

## Advanced HTML evidence

Three real advanced hosts were found from actual published URLs and fetched directly; no customer hostname was guessed:

| Board | Stated posts | Observed pages | Distinct posts read | Result |
|---|---:|---|---:|---|
| manatal.careers-page.com | 40 | 30+10 across2 pages | 40 | Complete list,314,830 decodedbytes |
| endowus.careers-page.com | 21 | 10+10+1 across3 pages | 21 | Complete list,155,023 decodedbytes |
| microresumes.careers-page.com | 1,967 | First5 pages,20 each;99 pages advertised | 100 | Partial;429 challenge atpages6/7,696,398 successful-pagebytes |

Primary Board sources: [Manatal](https://manatal.careers-page.com/), [Endowus](https://endowus.careers-page.com/), [MicroSourcing](https://microresumes.careers-page.com/).

Measured shared markup across the three first pages:

- `article.job-card` contains one `a.job-title-link` whose href is `/jobs/{UUID}`. `data-job-id`, `data-job-title`, and optional `data-job-city`/`data-job-country` repeat its identity and metadata. Do not count desktop/mobile apply and refer links as additional postings.
- `ul[aria-label="Job details"]` inside each card contains location and, when present, department text. Endowus and MicroSourcing demonstrate that the department item is optional; some locations display `-`.
- `.search-header-right` contains a number followed by `Open Positions`. `.page-indicator` states current/last page. `a.page-link` and a visible Next link carry exact pagination URLs. Page size is configured per Board: observed10,20,30, so no hardcoded30-row termination.
- Manatal's inspected detail has `h4.single-job-title`, `.job-post-description` and `.job-location > ul > li`. The latter gives location, department, employment label and workplace label in that observed page: `Bangkok, Bangkok, Thailand`, `Engineering`, `Full-Time`, `On-Site`. Do not infer a universal item position when optional fields are absent.
- Company is available from Board text `Jobs at ...` and detail title. `og:site_name=Manatal` is the vendor, not the employer.
- The inspected advanced detail contained no JSON-LD, no native date, and no structural link to a legacy numeric ID/hash. Its description happens to link an old vacancy, but prose links are not a general identity contract.

The complete40-post advanced Manatal Board had30 distinct titles also present in the32-job legacy API. Differences include renamed titles and additional posts; two advanced posts share a title. That is consistent with the vendor's many-to-many Job/JobPost model and makes title-only deduplication unsafe.

The three-host listing sample establishes a practical title/location/optional-department pre-detail gate. Only one advanced detail was inspected before the later refusal, so exact title/department agreement across every theme and an advanced detail cost estimate remain **unmeasured**. Preserve this limitation in validation and do not claim the99-page Board was fully read.

## Dead, empty and request behavior

The initial31-tenant legacy count sample returned20 HTTP200 responses (one empty) and11 HTTP404 responses. Five real historical candidates were corroborated on their public HTML roots: `1950labs`, `10000-solutions-llc`, `1powerconsulting`, `4creek-inc`, `9xero`. Each API returned:

```json
{"detail":"No ClientPortalSettings matches the given query."}
```

Their HTML roots were404 without a marketing redirect. In contrast, `code-recruitment` returned200 with `count:0`, `next:null`, `previous:null`, `results:[]`; its HTML root was200 with the proper Career Page company title. Its census, full-list and follow-up control reads all remained empty. An invented unknown slug matched the dead signature but was not the sole dead-control evidence.

Robots were read before the API requests. `www.careers-page.com/robots.txt`, `api.manatal.com/robots.txt`, and all three measured advanced-host robots returned404; no robots prohibition was observed in those responses. This is not a claim about a custom host not examined. Global sitemap/RSS availability was not established; API plus observed public HTML were the measured surfaces.

Rate evidence was gathered before parallel provider probes. Four serial API requests with one-second gaps returned200; a bounded same-tenant four-request burst returned4/4 HTTP200 in0.754seconds (~5.31req/s). A bounded cross-tenant four-request check had expected live/dead outcomes and no refusal. This short test did **not** establish safe sustained throughput. The later advanced HTML walk hit a Cloudflare challenge response as documented above. No `Retry-After` or quota headers were present, and no fixed-window quota is inferred. All subsequent provider requests in this measurement task stopped.

All requests used `headstart/0.1`, with either JSON Accept or browser-style HTML Accept. The required default-curl and python-requests user-agent comparisons were **not completed** after the advanced refusal; do not claim equivalence. Legacy13/13 job HTML samples returned200 with browser Accept. An actual browser job click remains unverified because the advanced browser presented Cloudflare verification.

## Checklist coverage and remaining uncertainty

| Checklist | Resolution |
|---|---|
| Q1–2 Identity/spelling | Legacy slug/hash and advanced host/UUID separately measured; numeric/hash lengths, casing and old/new mismatch documented above. Custom-domain alias identity not generalized. |
| Q3/3a Public browser/surfaces | Public HTML/API captured;13 legacy link GETs200; native browser blocked by verification. Robots404 on measured hosts; no global RSS/sitemap claim. |
| Q4–7 Pagination/filtering/description/hidden rows | Legacy totals exact20/20, size clamp100 and invalid page tested; advanced two complete/one partial. Legacy32-Board HTML/API hash match verified for vendor Board only. Hidden/internal exposure across other clients unmeasured. |
| Q8/8a/8b/9 Empty/dead/click/redirect | One real empty repeated3 times, five corroborated real dead controls,13 public job GETs200. No advanced empty/dead-root controls measured before refusal. A404 robots response is not a dead Board. |
| Q10/11/11b Detail/charset/token |13 legacy detailAPI objects exactly match list;2/13 HTMLJSONLD. Advanced one detail parsed. UTF-8 in inspected HTML/API; no token required for v3 career-page list/detail. No token lifetime dependency. |
| Q12 Tech gate | Legacy full descriptions remove need for detail gate. Advanced title/location/optional-department cards available; cross-theme exact gate equivalence remains unmeasured. |
| Q13 Dates |No legacyAPI native date. Two sampledJSONLD dates inconsistent with expired validThrough; stability over time not established. Advanced inspected detail no date. |
| Q14 Remote |399true/20false/502null nativelegacy flags. Advanced sampled detail explicitlyOn-Site. A systematic contradiction audit against prose is not claimed; null is unknown. |
| Q15 Salary |49 visiblelegacy entries, currency49, no unit; daily example demonstrates why annual assumption is unsafe. Advanced salary examples not reached before refusal. |
| Q16 Employment/experience |Six raw contract values and current shared flags measured; experience absent in API. Advanced sampled Full-Time label. |
| Q17/17a Location/department |784/921 statedlegacy locations/countries, scalar location schema. Organization meaning varies by Board. Advanced optionaldepartment and missinglocation demonstrated. Full advanced multi-location coverage not established. |
| Q18 Company |Legacy Board titles and advanced Jobs-at heading; vendor og:site_name explicitly rejected as employer. Per-advert true client naming may remain undisclosed by staffing agencies. |
| Q19–21 Operating behavior/bytes |Short API concurrency4 successes; advanced burst then429 challenge; no bypass and no exact quota claim. Decoded byte counts, not compressed network bytes. AlternateUA tests unmeasured. |
| Q22–23 Population/language |921 legacyrows,223tech,4.236MB; langdetect en893,pt14,th9,nl3,pl1,no1. Automated language estimate, not manual audit. Sample biased toward English/tech prospects. |
| Q24 Overlap |No native ID/hash collisions across20 legacyBoards; clear old/new Manatal overlap with incompatible identities. Full cross-ATS employer-domain matching not performed by this measurement task. |

## Captures and candidate pool

Candidate-grade union before advanced hosts: **2,948**, comprising2,824 upstream slugs and124 main-checkout-only slugs (main input193). Adding the three directly observed advanced hosts produces **2,951 source-surface candidates**. Old and new surfaces may belong to the same account, so this is not a unique-employer count. No CC/Wayback requests were made by this task, and no liveness ledger was written.

Key local reproducible artifacts:

- `api-rate-serial-0.body`: initial full vendor legacy API response; `pagination-*.body`: all pagination controls.
- `full-{slug}-{page}.body`, `full-summary.json`, `full-rows.json`: complete legacy capture set.
- `census-1950labs.body` and `board-1950labs.body`: real dead controls; `control-code-recruitment.body` and `board-code-recruitment.body`: real empty controls.
- `board-legacy.body`, `board-advanced.body`, `advanced-manatal-page2.body`, `advanced-endowus-{board,page2,page3}.body`: complete old/new and pagination fixtures.
- `detail-advanced-platform.body`: the inspected advanced detail; `advanced-microresumes-page6.body/.meta.json`: refused advanced request, not a normal page fixture.
- `analysis-summary.json`: all field, contract, language, cost and legacy detail comparisons; `advanced-evidence-summary.json`: complete/partial advanced card inventories.

`measure.py`, `study.py`, `deepen.py`, `advanced.py`, `analyze.py` reproduce these checks. Run with `PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python`. **Do not blindly rerun `advanced.py`: its4-worker walk is the measurement that triggered the refusal.** A future approved validation should use explicit pacing/backoff and stop when refused, while keeping incomplete reads marked truncated.

## Implementation and archive follow-up (2026-10-03)

The enabled adapter, probe and discovery hooks are implemented (ADR-0384). A complete 23/23-page Wayback sweep yielded 4,414 valid candidates;1,715 are archive-only. The union has 4,666 source surfaces. Two attempts at the latest CC page returned truncated JSON despite HTTP 200; neither was checkpointed or counted as complete. The ledger contains 3,788 live,479 dead,399 unknown. Advanced-host expansion was stopped after repeated 429 challenges;367 unresolved/unattempted candidates were conservatively recorded unknown, never called dead or live. No egress change was used. Raw checkpoints preserve which candidates actually settled.

`verify_scraper.py manatal 20` read all 19 live sample Boards:1,211 Jobs,1,200 descriptions; its sole failure was a known departed client. An optional public company-page failure now preserves a successfully fetched listing and leaves unverified department/name metadata absent.

The largest discovered legacy Board, Mercor, reported 16,888 Jobs but its full offset walk yielded 15,927 distinct ids with descriptions,locations,type andpublicURLs. It is explicitly **truncated**, protecting absent Jobs from closure inference. Four follow-up pages still reported 16,888; proposed `ordering=id`/`-id` parameters did not produce monotonically ordered ids and were not adopted. No stable-order or complete-read claim is made for this Board. Endowus advanced yielded all 21 JobPost identities,18 descriptions and 3 recorded HTTP 429 detail failures; listing completeness is separate from detail success.

Browser checks opened the actual Manatal legacy Business Development Internship and advanced Senior Platform Engineer routes, including full description and application links. The vendor's public careers page endorses its legacy Board. Archive expansion found 13 more same-label live legacy/advanced pairs; their advanced surfaces are parked pending employer endorsement and overlap reconciliation, rather than serving both unidentified publication systems. This hold is not an exact-alias claim. Advanced-only live Boards remain eligible.

Raw captures and all limitations are retained under `experiment/manatal-public-api/` and `experiment/three-ats-build/`; the source-specific protocol decisions are committed here.

Archive sanitation rejected 39 malformed URL path labels (RGB colours, JavaScript expressions and pasted text). None resolved live: 37 were dead and two unresolved. They remain in raw captures but are absent from the valid candidate pool and committed ledger.
