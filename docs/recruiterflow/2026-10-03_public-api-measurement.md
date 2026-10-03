# Recruiterflow public careers measurement — 2026-10-03

**Result:** Recruiterflow is readable without an account through its hosted careers HTML. The listing embeds a complete grouped JSON object; each public job page embeds the detailed record used by its application UI. The vendor's separately documented “public API” requires a workspace-bound secret key and is not the chosen surface. [Official API guidance](https://help.recruiterflow.com/en/articles/3671870-build-a-custom-careers-page-with-the-recruiterflow-api).

This session captured **159 sequential HTTP responses** between 12:23 and 12:37 UTC: **155 HTTP 200 and four expected HTTP 404 controls**, with no 403/429, timeout or parse failure on the measured live surfaces. Requests were spaced at least **1.25 seconds** apart, including across tenants. The later concurrency ramps and sustained tests are recorded in the final verification below and supersede that initial pacing choice.

The search-derived candidate pool contained **41 path slugs**: **34 hiring Boards**, **five live-empty Boards**, **one historical Board now returning 404**, and **one historical Board returning an inactive-careers template with HTTP 200**. The 34 hiring Boards listed **662 distinct Board/job pairs**, **137 tech Jobs (20.69%)** under the current `is_tech(title, department)` rules. Details were fetched for **78 postings across all 34 hiring Boards**: each Board's first and last listed posting, an additional tech example where needed, and minority workplace/employment examples. This is a purposive sample, not a market-share or global completeness estimate.

## Captures and reproduction

The local notebook is `experiment/recruiterflow-public-api/LOG.md`, with raw response bodies, response headers, request timestamps, exact URLs, sizes and hashes in `artifacts/`. The directory is intentionally uncommitted; the measurements necessary for interpreting the build are in this document. Reproduction, from repository root:

```bash
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python -u experiment/recruiterflow-public-api/round1.py
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python -u experiment/recruiterflow-public-api/census.py
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python -u experiment/recruiterflow-public-api/round2.py
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python -u experiment/recruiterflow-public-api/round3.py
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python experiment/recruiterflow-public-api/analyze.py
PYTHONPATH=src /Users/sarthakjain/Projects/HeadStart/.venv/bin/python experiment/recruiterflow-public-api/prepare_fixtures.py
```

The census resumes existing captures; use a new named capture directory for a fresh comparison rather than quietly mixing dates. `round1.py`, `round2.py` and `round3.py` perform network reads; `analyze.py` and `prepare_fixtures.py` read saved responses only. The request manifest is append-only and each capture is written before the next request.

Important raw files:

- `paced-rfcareers-1-jobs.json`, `paced-openselect-1-jobs.json`, `paced-opaque-1-jobs.json`: extracted listing payloads.
- `detail-rfcareers-166-data.json` and `detail-rfcareers-166-jsonld.json`: the detailed UI record and its structured-data counterpart.
- `census-summary.json`, `detail-sample-selection.json`, `measurement-summary.json`: census, reproducible sample selection and calculated field distributions.
- `board-identity-evidence.csv`: database identities resolved for **38 of 39** public live/empty Boards; Cyrten's empty page exposes no database id in the observed assets.
- `measured-aliases.json`: the two experimentally confirmed readable-slug/database-slug aliases.
- `trimmed-fixtures/`: real stripped listing/detail/empty/inactive responses for implementation tests, with values preserved.

The prescribed upstream repository, `kalil0321/ats-scrapers`, was inspected through its recursive `main` tree. It has **no Recruiterflow scraper or Recruiterflow seed CSV** at this measurement date; the expected two file paths return 404. No existing main-checkout Recruiterflow pool was found. The 41 pool rows therefore come from indexed first-party Recruiterflow Board/job pages, retaining `source=web-search-2026-10-03`. This was the initial search-only pool; completed archive recovery is recorded below.

## Identity and aliases — checklist Q1–Q2

A public Board is `https://recruiterflow.com/{slug}/jobs`. Slugs can be readable labels (`rfcareers`, `capstonesearchpartners`) or opaque `db_...` identifiers (`db_7c3b58dbe8c6575c053e9d925b833b8a`, named **Rebel Recruiters**). Public job links are `https://recruiterflow.com/{slug}/jobs/{integer-id}`; a widget is `/{slug}/jobs-page-widget`, and job links may carry `?widget=1`. Discovery should normalize these to one Board URL, drop job ids and filters, and lowercase the slug.

Case was checked on **four real Boards**: `rfcareers` versus `RFCAREERS`, `GenerationSuccess` versus `generationsuccess`, `CONEXCEL` versus `conexcel`, and a lowercase/uppercase opaque database slug. All four pairs returned **the same job-id sets** (8, 4, 5 and 107). The request's slug spelling is echoed into `apply_link` and the detailed `company_path`; it is not a canonicalization signal. Native posting ids are integers on **662/662** listed rows, with no colon-bearing identifier.

**Two path names can name one Board without redirecting.** Measured pairs:

| Readable slug | Database slug | Identical posting ids |
|---|---|---:|
| `rfcareers` | `db_780ea37ae968e3b894a92be5a88123a7` | 8 / 8 |
| `capstonesearchpartners` | `db_9bb0410857953f82c5c4627632066ed4` | 112 / 112 |

On the same Recruiterflow posting 166, the JSON-LD `identifier.value` is `db_780ea37ae968e3b894a92be5a88123a7__166` through both paths. However, a request through the database alias returns that alias in **both** `company_path` and `careers_page_path`; it does **not** resolve back to `rfcareers`. Thus a runtime `board_key` must not change based on whichever spelling a response echoes. Resolve known aliases before landing, retain one deterministic canonical slug, and record the measured alias. Database ids were found in page asset paths for 35 candidates, and detail identifiers resolved three more public Boards. **The 38 resolved database ids were distinct; one of the 39 public Boards remained unresolved.**

No second regional careers host or independently addressable sites within one database were established. That is unmeasured, not proof none exist. Some asset hosts are region-specific S3/CDN locations; those are not distinct Board namespaces.

## Listing and applicant-visible behavior — Q3–Q7

Exact chosen requests:

```text
GET https://recruiterflow.com/rfcareers/jobs
User-Agent: headstart/0.1
Accept: text/html,application/json

GET https://recruiterflow.com/rfcareers/jobs/166
User-Agent: headstart/0.1
Accept: text/html
```

No cookie, authorization, Referer, token or prior session was supplied. The listing contains:

```javascript
window.jobsList = {
  "department": [["Engineering", [/* posting rows */]]],
  "group": [/* current display grouping */],
  "location": [["Bengaluru", [/* posting rows */]]]
};
```

Each listing row has exactly the same seven fields on **662/662**: `apply_link`, `details`, `employment_type`, `job_id`, `job_name`, `last_opened`, `remote_type`. Under `department`, the group name is the department and `details` is the joined location display text. Under `location`, the group name is one location and `details` instead contains department text. **Do not interpret `details` identically in both collections.** Use the department collection for one row per job and the location groups to preserve every location. `group` is presentation state, not a second independent posting collection.

The page's own `/static/js/manual/careers/careers.js?v=0.11` renders `window.jobsList` and filters it; no jobs-list XHR or page walk was found in that script. It does change the URL and reload for location/department filters. The largest Board found, Capstone Search Partners, listed **112 ids**; base `/jobs`, `/jobs?page=2&limit=1`, `/jobs-page-widget`, and a repeated base fetch all returned the same **112-id set**. The next-largest Board returned 107. The initial 8-, 10- and 107-row Boards also retained identical ids across two reads.

There is **no independent total or next-page marker** in the embedded listing. No pagination or cap was observed through 112 postings; a ceiling above the measured population is **not measurable from this sample**. Successful JSON parsing alone cannot prove that a future server response is complete.

Parameters are filters, not Board coordinates. On Recruiterflow's own eight-job Board:

| Request | Distinct returned jobs |
|---|---:|
| `/rfcareers/jobs` | 8 |
| `?department=Engineering` | 3 |
| `?location=New%20Delhi` | 1 |
| `?department=__no_such_department__` | 0, HTTP 200 |

The fetch URL must therefore be rebuilt as an **unfiltered** Board URL rather than retaining discovery query strings. An empty filtered page cannot determine Board liveness.

A real browser opened `/rfcareers/jobs`, rendered eight jobs, and followed the **Backend Engineer** link to `/rfcareers/jobs/166`. The detail displayed the description and public application form. The observed browser listing's eight ids matched the embedded list. The 78 sampled native apply links all returned 200 with job records and active `job_visibility_id=1`; private/inactive visibility enums were not requested. Broad API-versus-UI equality across all 41 candidates was **not measured**, but the chosen data is the public page's own renderer input.

`GET /robots.txt` returned 200 with **`User-agent: *` and `Allow: /`**, plus `Sitemap: https://recruiterflow.com/sitemap.xml` and `https://wf.recruiterflow.com/sitemap.xml`. The fetched root sitemap (6,862 bytes) describes vendor website content, not a customer/job roster. No anonymous JSON listing or RSS feed was discovered in the examined listing, script and robots sources. The authenticated documented API loses because it requires each customer's secret; the embedded JSON already supplies public ids and metadata. This does not claim that every possible unpublished API route was exhaustively tested.

## Live-empty, departed and closed — Q8–Q9

| Control | Result | Rule supported |
|---|---|---|
| Five real empty Boards: `cyrten`, `entrada`, `hyrup`, `talental`, `db_4e0c51fc9c958e883a2d5015dc0f27a7` | HTTP 200, real company metadata, `window.jobsList = {"department": [], "group": [], "location": []}`; **5/5 stayed empty on repeat** | A parsed unfiltered empty listing is a real live-empty Board. |
| Real historical `Careerkafe` | HTTP 404, `<title>Not Found</title>`, no listing, **2/2 cases** including lowercase | 404 is a measured departed/absent public Board response, not just an invented-slug hypothesis. |
| Invented Board | HTTP 404 with the same error-template length as Careerkafe | Supplementary absent control; not the sole evidence for DEAD. |
| Real historical `nearshorebusinesssolutions` Board | HTTP 200, `<title>No jobs found</title>`, no listing; **2/2 repeats** | A distinct inactive-careers template, not the live-empty payload. |
| Its historically indexed job `/nearshorebusinesssolutions/jobs/100` | HTTP 200, same inactive text, no detail JSON | A user reaches a closed/unavailable application rather than a valid job. |
| Invented numeric job id on real `rfcareers` | HTTP 404, no detail JSON | Missing-job control is distinguishable from a good detail. |

The inactive template's exact visible markers are **`Oops! No jobs found.`** and **`We are currently not accepting any applications`**. Use the template/absence of public job data together, rather than the phrase “No jobs found” alone: a valid empty Board's JavaScript renderer can also display a no-results message. Unexpected HTML, malformed JSON, 429/5xx or transport failure remain unknown/unreadable, not empty.

No marketing redirect was observed for the two real departed/inactive controls. A five-dead-Board validation sample is **not available** in this pool: only these two known historical cases were found. No false-empty result was observed in the five repeated empty Boards; larger temporal sampling would be needed to bound a rare occurrence.

## Detail, field coverage and tech gate — Q10–Q18

The job page contains strict JSON after **`var convertedToJSON =`**, later assigned to `window.applyJobData`. Parse the JSON value with a JSON decoder, not by evaluating page scripts. Every sampled detail also contains JobPosting JSON-LD, mirrored in `google_for_jobs_fragment`.

| Job field | Listing evidence (662 jobs) | Detail evidence (78 jobs) | Mapping consequence |
|---|---|---|---|
| Native id | 662 integer `job_id` values | 78 matching job records | Board-scoped numeric id; separate JSON-LD `db__id` supports alias checks. |
| Company | `og:title` carries a company name on **39/39 public Boards**, including empty ones | `company_name` **78/78**, matching the listing metadata on 78/78 | Read Board metadata once; opaque slugs never need to display as the employer name. |
| Title | `job_name` 662/662 | Same title **78/78** | Listing is authoritative for gate/metadata. |
| Department | Department group on 662/662; **36 use literal `No department`** | No actual department field found on 78/78 | Normalize the sentinel to missing; do not confuse form-type constant `VIEW_FIELD_MEMBER_DEPARTMENT` with a job's department. |
| Location | Display text and location groups 662/662; **170/662 multi-location**, maximum **32** | `job_location` 78/78; JSON-LD `jobLocation` has only **one** place on 78/78 | Preserve every listing location. Do not overwrite a multi-location listing with the JSON-LD's first address. |
| Remote | 172 `Remote`, 56 `Hybrid`, 434 null | Same native value **78/78** after empty-string/null normalization | Remote → true; Hybrid → unknown/None; only absent native value uses location fallback. |
| Description | No description field, **0/662** | `about_position` has actual text **76/78**; maximum 8,818 stripped characters | Detail pass required; no measured fixed truncation length. |
| Date | `last_opened` 662/662 | `datePosted` 78/78, same calendar date as listing **78/78** | It is the latest opened date, not established as the original first publication date. |
| Experience | Absent | Lower bound 28/78; upper bound 20/78; either bound 29/78 | Detail-only native range; do not turn an upper-only bound into a minimum. |
| Employment type | 639 Full time, 6 Part time, 16 Contract, 1 Temporary | Same value **78/78** | Current `employment_type_filter.flags` maps all four correctly, including Temporary → contract. |
| Salary | Absent | No native salary/baseSalary on **78/78**; shared description extraction finds pay on **14/78** | `_salary_field` can remain None; use normal downstream description extraction. |
| Requisition | No distinct requisition field | JSON-LD id duplicates native database/job identity | Do not invent a separate employer requisition id. |
| URL | `apply_link` 662/662, points to native job path | 78/78 sampled links render public job data with `Accept: text/html` | Build canonical HTTPS link from normalized Board slug and integer id, preserving Board identity. |
| Scrape timestamp | Not an upstream field | Not an upstream field | Set locally at the observation time. |

The two empty descriptions are HireCouncil postings 60 and 58. Their JSON-LD `description` is **identical to the job title** (14 and 7 characters); it is a template fallback, not recovered substantive text. Keep their description missing. For the other **76/76**, stripped `about_position` equals stripped JSON-LD description.

**Experience is real candidate-facing data, even when the employer contradicts itself.** On Recruiterflow's Backend Engineer 166, the native fields state **2–8** and a real browser displays **“2 - 8 Years of Experience”** above the body. The description separately asks for **5–9**. Preserve the explicit native range rather than pretending these agree. Across the 78 details there are 19 two-sided ranges, nine lower-only values, one upper-only value and 49 with neither. The upper-only example is Triggerfish Recruitment's Civil Engineer 12 (`experience_range_start=""`, `experience_range_end=3`); omission of that native field is preferable to accidentally making three years a minimum. The current generic field parser does not parse “up to 3 years” as a structured range.

Date stability was checked by repeated detail reads on **three postings** (Recruiterflow 166; Rebel Recruiters 3269 and 3223), all unchanged, and by the repeated listing reads. There is no `validThrough` field in the sampled job structured data. This rules out a request-clock date in those examples, not every possible tenant-specific date policy.

Native remote flags matter: **159/172 Remote** listings have no “remote” word in their location; conversely **40/434 null** native flags have a location that the existing `is_remote` heuristic recognizes as remote. None of the 56 Hybrid postings had a remote-word location. Do not replace the native value wholesale with the text heuristic.

Company naming is available even when the page title is customized: the opaque Rebel Recruiters Board says `<title>Find A Job</title>` but its `og:title` names Rebel Recruiters using the same `"{company} is hiring! Apply now."` pattern present on **39/39** live/empty pages. The metadata matched all 78 details. For staffing Boards this names the publishing agency, not necessarily the client employer described in the job body; that is the source's own operator identity.

**Tech gate verdict: exact on the measured field path.** All 78 detailed titles match listing titles; the detail supplies no department to override the listing group. The gate must use the same listing title/department as final parsing. It keeps **137/662 jobs**, avoiding **525/662 detail requests (79.31%)** on this sample. No description-based classification was introduced. Because native experience exists only on the detail, the generic description-store skip would also skip refreshing that field; this build should not silently blank it merely because a description is already held.

## Access, performance, language and overlap — Q11b, Q19–Q24

**Tokens/encoding.** No secret token or cookie is needed for the selected HTML surface. Public page data contains application-form configuration, but it is not necessary to submit any form or fetch any applicant record. All 159 captured bodies decoded as UTF-8. A token lifetime is **not applicable** to this surface; the unrelated customer API key is expressly outside scope.

**Rate observation before census.** Six initial sequential reads across three Boards all returned 200 at a planned ceiling of 0.8 requests/second, with response times 0.978–1.205 seconds. That was followed by the paced census, controls and details. The initial159-request set had mean response time **1.121 seconds**, maximum **1.460 seconds**, and no refusal. Subsequent one-Board and many-Board ramps reached concurrency128 on both direct and spare routes; see final verification. A single shared origin should be paced globally, rather than multiplying a per-Board delay by concurrent Boards.

**User-Agent/content negotiation.** `headstart/0.1` succeeded throughout. One additional Board request each with `curl/8.7.1` and `python-requests/2.32` User-Agent strings also returned 200. These isolate User-Agent differences on the same urllib transport; they are not a broad transport benchmark. One detail with a browser-style `Accept` header returned the same usable record; all 78 sampled details used `Accept: text/html` successfully.

**Payload cost.** The 39 live/empty listings total **4,493,078 bytes** without content compression; the 34 hiring listings account for 4,027,146 bytes. The detail sample averages **119,075 bytes** per response (median 119,388). Initial gated scrape estimate:

```text
4,493,078 listing bytes + 137 tech details × 119,075.49 bytes
≈ 20.81 MB / 137 tech Jobs
≈ 151,872 bytes per tech Job (0.152 MB)
```

This is comfortably below ADR-0158's approximately 2 MB per tech Job reference, using measured decoded/public-HTML body sizes. It is an estimate for this seed sample, not a forecast for every undiscovered customer; no production corpus or global Hiring Board count was inferred. Ungated, the same sample would be roughly 83.32 MB, or 0.608 MB per tech Job.

**Language.** Deterministic `langdetect` on sampled title plus detail text marked **70/78 English**, 3 German, 2 Dutch, and one each Hungarian, Portuguese and Romanian. The two descriptionless HireCouncil rows are too short for reliable language classification, and the sample is purposive. English UI does not imply English employer text; retain the pipeline's own ingestion language gate.

**Overlap.** The public apply links in this sample are Recruiterflow's own application pages, not measured redirects to Workday/Greenhouse or another held ATS. This establishes an ATS application surface rather than a mere career front. It does **not** rule out staffing agencies advertising roles also published by their client employers. Cross-ATS corpus-level duplication was not measured: public pages do not consistently disclose the customer's own Board/requisition coordinates, and this task did not fetch the served dataset. Same-system aliases were measured separately above and must be reconciled during archive discovery.

## Checklist limits and follow-through

The initial41-Board protocol cohort was later extended with large-Board, browser, archive, concurrency and corpus-wide comparison checks; final evidence is summarized below.

Recommended implementation: unfiltered hosted HTML listing; strict embedded-JSON parsing; listing department/location groups; detail `convertedToJSON` for description and native experience; Board name from listing `og:title`; exact pre-detail tech gate; explicit live-empty/inactive/404 distinction; conservative shared-origin pacing; and measured alias records before landing any database/readable duplicate. Production source, registry, liveness ledger and final enable/alias decisions are owned by the integrating change, not by this measurement notebook.

## Implementation validation (2026-10-03)

The enabled adapter, probe, archive/fingerprint hooks and alias reconciler are implemented (ADR-0382). The 836-row candidate pool contains 812 Wayback candidates (8/8 pages),139 in CC-MAIN-2026-39 (complete), and 41 web-search leads. Source-exclusive contributions are 667 Wayback,9 CC and 15 web-search. The initial older-CC failures were subsequently recovered through stored CDXJ blocks; the final intended archive range is complete. The ledger contains 700 live,136 dead,0 unknown;96 live Boards are empty. Fresh database-and-complete-set comparison confirmed 21 aliases;23 Boards had no usable database identity and were not aliased.

The standard `verify_scraper.py recruiterflow 20` run read all 17 live sample Boards:812 Jobs,737 descriptions. Its three errors matched three known-dead candidates. A six-detail follow-up on Pink Tile found four genuine empty `about_position` fields and two full descriptions; empty content stays absent, not replaced with a title. The largest Board, Desort, returned all 10,809 identities. In pipeline-gated mode its two tech matches both obtained details, with no losses; the other 10,807 detail calls were correctly skipped. Every Job had title,location,department,type,date andURL. This is a gated largest-Board measurement, not an ungated 10,809-detail test.

A rendered browser verified `/rfcareers/jobs/166` opens Backend Engineer with the displayed 2–8 year experience range, full posting and application form. Readable names are adopted from public metadata or the verified detail. Alias refresh now removes obsolete aliases when their canonical Board is confirmed dead or truly empty; unresolved prior endpoints leave the old ledger untouched.

## Final verification after archive and overlap recovery

The pool now has 853 candidates; liveness is 708 live, 145 dead, 0 unknown. Fresh reconciliation retains 21 aliases. Complete archive coverage spans all 33 requested Common Crawl collections and 8/8 Wayback pages. No older-crawl outage remains unprocessed.

The 2,048 listing-request ramp covered concurrency 1/4/8/16/32/64/128, one Board and 24 Boards, direct and spare; all returned 200. The separate detail ramp passed through 16. Production now uses 8 detail workers and 16 shared starts/s, with one transport-only spare recovery. See ADR-0388 for the common retry contract.

The completed cross-provider audit includes Avomind and Smartworks. Verified official sources and posting descriptions establish partial overlap, not whole-Board equivalence; no title-only alias or park was applied. Full corpus denominators and native comparison tables are retained locally in `experiment/cross-provider-ats-overlap-2026-10-03/`.
