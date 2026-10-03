# Instahyre: anonymous job API, upstream implementations, and HeadStart fit

**Measured:** 2026-10-03. **Result:** Instahyre exposes an anonymous JSON listing and a
per-job detail endpoint, both readable with HeadStart's normal `headstart/0.1` user agent.
The technical extraction path is therefore clear. It is **not yet a safe HeadStart ATS
addition**, however: Instahyre is one multi-employer job marketplace, not a company-owned ATS
Board. Its list records do carry an Instahyre-side numeric employer ID, but the API does not
establish the employer's original ATS Board identity or application URL.

No production code, registry entry, liveness ledger, or candidate pool was changed by this
research. Measurements were low-volume, sequential anonymous `GET`s; they establish response
shape, not a safe concurrency/rate limit or permission to bulk crawl.

## Sources and reproducibility

- Instahyre frontend evidence: the public [shared Angular bundle](https://static.instahyre.com/js/output.6ca49128f860.js)
  registers `jobSearchService` at `/api/v1/job_search`, `publicJobService` at
  `/api/v1/employer_public_jobs/:id`, and `jobFunctionService` at `/api/v1/job_function/`.
- Primary live endpoints: [function catalogue](https://www.instahyre.com/api/v1/job_function/),
  [unfiltered first listing page](https://www.instahyre.com/api/v1/job_search?limit=35&offset=0),
  [listing record 352689](https://www.instahyre.com/api/v1/job_search/352689), and
  [full public record 352689](https://www.instahyre.com/api/v1/employer_public_jobs/352689).
  The numeric records can close or change; the request shapes, fields, and measured counts below
  are observations from the stated date, not a permanence claim.
- Additional primary response examples: [industry catalogue](https://www.instahyre.com/api/v1/industry_type/),
  [name-based industry suggestion](https://www.instahyre.com/api/v1/industry_type/suggest_industry_types?company_name=GeoServe),
  [limited employer record](https://www.instahyre.com/api/v1/employer_misc/employer_profile/anon_employer_limited/48297),
  [location vocabulary](https://www.instahyre.com/api/v1/candidate_misc/profile/candidate_locations/location_data/?separateRemote=true),
  [country vocabulary](https://www.instahyre.com/api/v1/candidate_misc/profile/candidate_locations/countries_data/),
  and the [anonymous candidate-matching auth boundary](https://www.instahyre.com/api/v1/candidate_opportunities/candidate_matching/fetch_filter_counts).
- [Instahyre robots.txt](https://www.instahyre.com/robots.txt) returned `200` and names a sitemap.
  The sitemap, public job page, Terms, and Privacy routes returned a Cloudflare challenge from this
  environment. The documented JSON routes above returned JSON directly. This research did not
  solve or bypass a challenge.

Reproduce the core low-volume checks with the repository's actual agent identifier:

```bash
curl --compressed -A 'headstart/0.1' \
  'https://www.instahyre.com/api/v1/job_search?limit=35&offset=0'
curl --compressed -A 'headstart/0.1' \
  'https://www.instahyre.com/api/v1/employer_public_jobs/352689'
```

Both returned `200` in this check without an incoming cookie, login, authorization header,
CSRF header, or Referer. The responses do set a CSRF cookie, which was not replayed. That is
evidence of anonymous read access for these requests, not a documented public API contract.

## Scoped public-route inventory

This is exhaustive for routes in the shared first-party bundle that either return public job
records or provide metadata used to interpret/filter them. It intentionally excludes the
bundle's account-management APIs: no account was created, no form was submitted, and no attempt
was made to authenticate or to access a candidate's or recruiter's records. Each live call below
was one sequential, cookie-free anonymous `GET` on 2026-10-03.

| Route | Anonymous outcome and response shape | What it supplies |
|---|---|---|
| `GET /api/v1/job_search?limit=35&offset=N` | `200`; `{objects, meta}`. `meta` has `offset`, `limit`, `total_count`, `total_count_relation`, `previous`, `next`, facet counts, `max_experience`, and `job_experience_levels`. | Current-list membership, marketplace job ID, Instahyre job-page URL, title, one string location, keyword list, and the nested Instahyre employer object. This is the only endpoint that supplies the employer's numeric ID. |
| `GET /api/v1/job_search/{job_id}` | `200`; one shallow object with the same fields as a list item. | No additional job data over the list row; it is not a useful detail pass. |
| `GET /api/v1/employer_public_jobs/{job_id}` | `200`; one full public-job object. Both a current full-time record and a current internship record responded without auth. | HTML description, active state, plural locations, job taxonomy, native experience range, internship/stipend object when applicable, and the relative Instahyre public-job path. This is the required detail read. |
| `GET /api/v1/job_function/` | `200`; `{objects, meta}` with 107 objects in this observation. An object has `id`, `name`, `slug`, `previous_slug`, `is_live`, `resource_uri`, and nested `job_category`. | Platform job-function vocabulary and its `job_category` (`id`, `name`, `previous_slug`, `is_tech`). It explains platform taxonomy only. |
| `GET /api/v1/industry_type/` | `200`; `{objects, meta}` with 74 objects in this observation. Each object has `id`, `name`, and `resource_uri`. | Decodes the numeric industry facet values; it does not add a job's source company or ATS. |
| `GET /api/v1/industry_type/suggest_industry_types?company_name={name}` | `200`; `{success: true, data: [industry_id, ...]}` for `company_name=GeoServe`. With no `company_name`, it returned `400` and a field-validation error. | A name-based industry suggestion only. It is not a company identity lookup. |
| `GET /api/v1/employer_misc/employer_profile/anon_employer_limited/{employer_id}` | `200`; `resource_uri`, `company_name`, `company_name_nopunc`, and `alternate_names_nopunc`. | A limited anonymous lookup for the list row's Instahyre employer ID. It gives platform-normalized display names, not a website, legal entity, ATS Board, or source provenance. |
| `GET /api/v1/candidate_misc/profile/candidate_locations/location_data/?separateRemote=true` | `200`; `{success, data}`. `data` has `cities_preferred`, `cities_current`, and their group arrays. | Location/region/remote vocabulary for the public UI. It does not change the job record's raw location field. |
| `GET /api/v1/candidate_misc/profile/candidate_locations/countries_data/` | `200`; `{success, data}`. Country objects carry `type`, `name`, `value`, `show_in_dropdown`, `alter`, `is_country`, and ISO alpha/numeric codes. | Country vocabulary only. |

The list row's nested `employer.resource_uri` looks dereferenceable (`/api/v1/candidate_opportunity_employer/{id}`), but the one anonymous read of that referenced path returned an HTML `404`, not an employer JSON record. Treat the list's nested object and the limited-anonymous employer route above as the observed public employer data; do not rely on that `resource_uri` as a public API.

### Frontend-declared account-only group (not an ingestion source)

The same bundle declares candidate matching and many candidate/employer management prefixes via
`API_PATHS`. They are a different product surface from the anonymous job feed. The one safe,
identifier-free read used to establish the boundary,
`GET /api/v1/candidate_opportunities/candidate_matching/fetch_filter_counts`, returned `401`
with `{"logged_out": true}` without credentials. Its related candidate-matching routes include
apply/interest actions, so they were not called. No account-only route provides a necessary
replacement for `job_search` plus `employer_public_jobs`.

## Exact observed job field contract

### Listing row: `job_search`

The 2026-10-03 first-page row had these top-level keys:

`id`, `resource_uri`, `public_url`, `title`, `candidate_title`, `locations`, `keywords`,
`accept_outstation`, `gender`, `employer`, `interview_status`, `reviewed_at`,
`is_strong_match`, and `score`.

`locations` was one display string, while `keywords` was an array of strings. `interview_status`,
`reviewed_at`, `is_strong_match`, and `score` were null in the anonymous sample; they appear to
be candidate-product state rather than public vacancy facts, so they must not be ingested as job
attributes.

The nested `employer` object had: `id`, `resource_uri`, `company_name`, `company_tagline`,
`company_founded`, `employee_count`, `instahyre_note`, `profile_image_src`,
`show_diversity_employer_icon`, and `show_featured_employer_icon`.

This **does** give a platform-side company grouping key: the seven records returned by
`companies=GeoServe` all carried `employer.id: 48297`. It is stable-looking within the observed
marketplace response and preferable to grouping on a display name. It is still not proof that the
record identifies one real-world employer or one HeadStart Board: there is no registered domain,
company website, legal identifier, original ATS provider, ATS tenant/Board key, or employer-owned
application URL.

### Detail row: `employer_public_jobs`

The union of fields from one full-time and one internship detail record was:

`id`, `resource_uri`, `opportunity_url`, `title`, `candidate_title`, `description`, `is_active`,
`is_internship`, `internship`, `locations`, `keywords`, `accept_outstation`, `gender`,
`job_category`, `job_functions`, `job_function_dict`, `workex_min`, `workex_max`,
`hiring_company_name`, `employer_profile_url`, `recruiter_name`, `recruiter_designation`,
`recruiter_company_name`, `recruiter_profile_url`, and either `agency_function_names` or
`job_function_names`.

- `description` is HTML and is the full description source. `is_active` is **not** membership
  authority: a complete 12,919-row list crawl found 2,944 current list rows whose details said
  `false`. The adapter retains every valid list row; the detail flag is only a detail-time fact.
- `opportunity_url` is only a relative Instahyre job-page path. Together with the list's absolute
  `public_url`, it supplies the Instahyre destination—not an external ATS or direct apply link.
- `workex_min` and `workex_max` are numeric native experience bounds. There is no observed
  posting, update, expiry, or other job timestamp. `reviewed_at` on the list is not such a
  timestamp and was null in the anonymous sample.
- The only observed employment-type signal is `is_internship`. The first-party bundle defines
  `job_type` values `0` (both), `1` (full-time), and `2` (internships); `job_type=2` returned 65
  current jobs in this check. Do not infer an arbitrary employment type beyond that signal.
- There is no native full-time salary field in either the list or full-time detail sample. An
  internship can carry `internship.resource_uri`, `hide_stipend`, `duration`, `start_date`,
  `is_paid`, and `stipend`; the sampled paid internship exposed `stipend: 10000`, but no currency
  or pay-period field. It cannot safely be normalized to a salary without additional evidence.
- `job_category`, `job_functions`, `job_function_names`/`agency_function_names`, and
  `job_function_dict` are the platform's role taxonomy, not an employer department. Do not map
  them to a company department.
- `hiring_company_name` repeats only a display name and the detail omits the list's
  `employer.id`. Preserve the list-to-detail join by job ID if the marketplace employer grouping
  is ever needed. The recruiter fields are personal/profile data unrelated to a HeadStart job
  feed and should not be stored.

No inspected response carries original-ATS provenance, an ATS application destination, an
employer-owned careers URL, or evidence that a company name maps one-to-one to a real employer.

## Live API observations

| Route | Measured behavior | Extraction consequence |
|---|---|---|
| `GET /api/v1/job_function/` | `200`, 107 function objects. Each has numeric `id`, `name`, `resource_uri`, and a nested category with `is_tech`. | Useful taxonomy metadata; it is not needed to make title/description filtering authoritative. |
| `GET /api/v1/job_search?limit=35&offset=N` | `200` with `objects` and `meta`. On 2026-10-03 the unfiltered feed reported **12,919** items; `meta.next` advanced by 35. Requesting `limit=70` still returned `meta.limit=35` and 35 objects. | Walk the server-provided `next` cursor or 35-step offsets; do not assume a caller-selected page size. |
| `GET /api/v1/job_search/{id}` | `200` but still shallow: id, title, `public_url`, locations, keywords and nested `employer`; no description or dates. | It is not a useful detail pass. |
| `GET /api/v1/employer_public_jobs/{id}` | `200` for listing id 352689 and six additional current listing ids. It returned full HTML `description`, `hiring_company_name`, `title`, `locations` array, keywords, `workex_min`, `workex_max`, category/function fields, `opportunity_url`, and `is_active`. The six sampled live ids all had a nonempty string description (1,190–3,562 characters). | This is the required detail endpoint. Convert HTML to text with the shared HTML path; retain the public job URL, not the API URL. Do not store recruiter name/profile fields, which are unrelated to the job feed. |

The endpoint can also return historical records: `/api/v1/employer_public_jobs/48297` returned
`200` with `is_active: false` while it was not a current list item. The later complete crawl shows
the converse too, so list membership is the only authority in either direction.

### Pagination and filters

The live list response's `meta` carried an exact `total_count`, a relative `next` path, `offset`,
and `limit`. A complete unfiltered run at the measured total needs about 370 listing responses
before details. This is only an arithmetic estimate; the population changes and no throughput or
rate-limit knee was tested.

`job_functions` works and accepts at most three values: ids `10`, `1`, and `3` returned 5,326
items in this check, while adding a fourth id returned `400` with a `job_functions` validation
error. The source code in an existing scraper below uses triples for that reason. It is not a
valid HeadStart completeness strategy: project policy requires the full raw source feed and the
post-hoc tech filter, not an unverified platform category filter.

Some apparent filters either had no effect or have a non-obvious shape. `q`, `location`,
`company`, `company_id`, `employer`, and `employer_id` returned the unfiltered first page in the
small check. `companies=48297` and the employer `resource_uri` returned `400`, while
`companies=GeoServe` returned seven GeoServe records. This name-only filter does not make a
reliable Board namespace: the API exposes a nested employer id, but the checked filter does not
accept it, and a display name is not a HeadStart identity.

The listing payload has neither a description nor a posting date. The full detail payload also
has no observed posting timestamp or native **full-time** salary field. Do not invent either: use
the normal local scrape time/first-seen behavior and downstream description extraction. A paid
internship can expose a stipend, but without currency/pay-period semantics it is not a salary
normalization source. Its `workex_min` and `workex_max` are useful native bounds, but should be
mapped only after the normal field semantics and tests are reviewed.

## Existing GitHub implementations

| Repository | Evidence | Reuse verdict |
|---|---|---|
| [Girish-Garg/jobdekho](https://github.com/Girish-Garg/jobdekho) — [Instahyre source](https://github.com/Girish-Garg/jobdekho/blob/3b168f56b159c1a8f429cd00b72e2592e9fa7d6c/packages/sources/src/boards/instahyre.js) | MIT-licensed, actively updated repository. Its source independently names the working `job_search` route, 35-row cap, three-function validation limit, nested employer mapping, and public URL. It intentionally samples tech function triples and leaves `description` empty. | Useful independent hypothesis and field-map cross-check; do **not** copy it or adopt its category-sliced, incomplete collection path. Reimplement only after the HeadStart scope decision. |
| [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) | This is the likely “Kali” upstream: it is MIT-licensed and is already cited by this repository's PageUp research. Its current scraper tree and GitHub code search contained **no Instahyre scraper or Instahyre reference** on 2026-10-03. | No implementation to reuse for Instahyre. It can remain a comparison source for repository style, not API behavior. |
| [kayden-vs/jobradar](https://github.com/kayden-vs/jobradar) — [Instahyre source](https://github.com/kayden-vs/jobradar/blob/5227e4389bf8c261420c4a7284aca12d0b413085/sources/instahyre.py) | MIT-licensed, but its own source says `/api/v1/opportunity/` returns `404` and falls back to browser DOM selectors. | Do not reuse. The live `job_search` and `employer_public_jobs` routes above supersede its stale route. |
| [roshtarg-cpu/instahyre-scraper](https://github.com/roshtarg-cpu/instahyre-scraper) | Public repository but no declared license in GitHub metadata; its description advertises bypassing Cloudflare. | Excluded: no license for reuse and circumventing the site's protection is out of scope. |

There are many public auto-apply and Selenium scripts, but they target authenticated candidate
flows rather than a complete, public, employer-owned job source. They are neither an API contract
nor a suitable extraction dependency.

## Employer-ID evidence: a stable Instahyre grouping, not proof of a real-world entity

**Question tested:** whether `job_search[].employer.id` can safely be used as the key for an
Instahyre-side employer grouping, and whether it proves a one-to-one mapping to a real company.

**Conclusion:** use the numeric ID as an **Instahyre marketplace-employer key** if the
marketplace ingestion policy is approved. It is materially better than grouping on a name: the
anonymous list, limited employer record, and public-job detail all agreed in this sample. It is
**not evidence sufficient to call it a verified-company, legal-entity, or cross-source identity**.
The public API gives no domain, verified website, corporate registration identifier, or provenance
for the posting. It also shows agencies recruiting for a differently named hiring company. No
low-volume live sample can prove that an ID is never reused, renamed, merged, split, or attached
to a client on behalf of an agency.

### Low-volume, sequential sample

All requests below were anonymous first-party `GET`s on 2026-10-03 with `headstart/0.1`, one at a
time. This is **five employer IDs, 114 currently listed rows visible from the five first result
pages, five limited employer records, and ten current public-job details** (two details for each
employer). It is a consistency sample, not a census of the 12,919-row marketplace.

| Canonical `companies` query | `meta.total_count`; rows inspected | List `employer.id` and name in every inspected row | Limited record | Two details: `hiring_company_name`; numeric ID embedded in `employer_profile_url` |
|---|---:|---|---|---|
| [`GeoServe`](https://www.instahyre.com/api/v1/job_search?companies=GeoServe&limit=35&offset=0) | 7; 7 | `48297`; `GeoServe` | [`48297`](https://www.instahyre.com/api/v1/employer_misc/employer_profile/anon_employer_limited/48297): `GeoServe` | `GeoServe`; `/employer/48297/...` for jobs [`352689`](https://www.instahyre.com/api/v1/employer_public_jobs/352689) and [`342524`](https://www.instahyre.com/api/v1/employer_public_jobs/342524) |
| [`Big Assets Infra`](https://www.instahyre.com/api/v1/job_search?companies=Big%20Assets%20Infra&limit=35&offset=0) | 54; 35 | `53913`; `Big Assets Infra` | [`53913`](https://www.instahyre.com/api/v1/employer_misc/employer_profile/anon_employer_limited/53913): `Big Assets Infra` | `Big Assets Infra`; `/employer/53913/...` for [`414980`](https://www.instahyre.com/api/v1/employer_public_jobs/414980) and [`439179`](https://www.instahyre.com/api/v1/employer_public_jobs/439179) |
| [`Tekion`](https://www.instahyre.com/api/v1/job_search?companies=Tekion&limit=35&offset=0) | 67; 35 | `5276`; `Tekion` | [`5276`](https://www.instahyre.com/api/v1/employer_misc/employer_profile/anon_employer_limited/5276): `Tekion`; aliases include `tekioncorp` | `Tekion`; `/employer/5276/...` for [`445450`](https://www.instahyre.com/api/v1/employer_public_jobs/445450) and [`445475`](https://www.instahyre.com/api/v1/employer_public_jobs/445475) |
| [`Oron Systems`](https://www.instahyre.com/api/v1/job_search?companies=Oron%20Systems&limit=35&offset=0) | 2; 2 | `55881`; `Oron Systems` | [`55881`](https://www.instahyre.com/api/v1/employer_misc/employer_profile/anon_employer_limited/55881): `Oron Systems` | `Oron Systems`; `/employer/55881/...` for [`441836`](https://www.instahyre.com/api/v1/employer_public_jobs/441836) and [`440780`](https://www.instahyre.com/api/v1/employer_public_jobs/440780) |
| [`Accenture`](https://www.instahyre.com/api/v1/job_search?companies=Accenture&limit=35&offset=0) | 405; 35 | `2070`; `Accenture` | [`2070`](https://www.instahyre.com/api/v1/employer_misc/employer_profile/anon_employer_limited/2070): `Accenture`; aliases include `accentureindia`, `accentureservices`, and `accentureai` | `Accenture`; `/employer/2070/...` for [`439973`](https://www.instahyre.com/api/v1/employer_public_jobs/439973) and [`445412`](https://www.instahyre.com/api/v1/employer_public_jobs/445412) |

The stable relationship is meaningful: each list row has the numeric ID, each limited lookup is
addressed by that exact ID and returns the same canonical name, and each corresponding job detail
has the same hiring-company display name plus a media URL whose path contains that ID. All 114
inspected current-list rows had exactly one ID for their queried canonical name; none showed an
ID/name collision. This demonstrates a platform-maintained employer-profile grouping in the
sample. It does **not** prove one-to-one real-company identity, because the same name query is
not an independent entity-resolution system and no historical or whole-corpus scan was performed.

The limited profile can normalize aliases but does not make them externally verifiable. For
example, Instahyre's `48297` endpoint returns `company_name_nopunc: "geoserve"`, while `5276`
returns `"tekion"` and the alias `"tekioncorp"`; nevertheless, the public list filter rejected
the five noncanonical queries `Accenture India`, `Accenture Solutions`, `Accenture Services`,
`Tekion Corp`, and `Geo Serve` with `{"companies": ["Invalid company"]}`. Thus aliases are
profile metadata, not a usable public, cross-company resolution interface.

### Agency and public-profile limits

The ten detail records also show that employer identity and the recruiter/account acting on the
listing are different concepts. The two GeoServe details named recruiter company **Talent
pipeline**, the two Big Assets Infra details **Hire Steezy**, the two Tekion details **Pylon
Management Consulting**, and the two Accenture details **Placewell Group**. Only both Oron Systems
details used `recruiter_company_name: "Oron Systems"`. This is direct first-party evidence that a
third-party recruiter can publish a job for an Instahyre employer profile. It does not show that
the numeric employer ID is wrong; it does mean the anonymous data cannot establish whether the
profile is controlled by the named company, its parent, or a recruiting intermediary.

Neither anonymous employer endpoint supplies a company website or social-account field. The list
only has profile image metadata, and the detail's `employer_profile_url` is a logo-media URL (not
a company profile page or external website). The shared frontend bundle references
`employer.social_accounts` for its employer UI, but declares no anonymous public employer-profile
route that returns those fields; the documented anonymous route returns only canonical and
alternate names. The job public pages, including GeoServe's, returned a Cloudflare `403` in this
environment, so no brand-page profile was read and no challenge was solved or bypassed. Therefore
there is no primary, guest-readable website/social evidence here with which to verify the claimed
company against an external corporate identity.

**Implementation boundary if approved:** persist this as something like
`instahyre_employer_id` and scope all identity language to *"Instahyre employer profile"*. Do not
promote it to `company_id`, merge it with an ATS Board by name, or use it as cross-marketplace
deduplication proof. Preserve the observed name/alias snapshot for display and drift detection;
if the ID's canonical name changes later, treat that as an identity-review event rather than an
automatic company merge.

## Build measurement: complete surface and rate limit

The global `job_search` feed is the only complete membership surface. Five employer-profile reads
returned `jobs_count` / `jobs` of **7/7, 54/10, 67/10, 2/2 and 405/10**: a per-profile scraper
would silently omit every row beyond its ten-card display cap. The adapter must therefore keep one
global `instahyre:global` Board, preserve the listing row's profile ID beside each Job, and fetch
the global offsets from the first page's authoritative `meta.total_count`. The unfiltered endpoint
serves offsets through 9,940 (9,975 rows) then returns `400` from offset 9,975 even while stating
12,919 total. The 107 public job-function IDs, requested in the API's maximum triples, unioned
with the accessible global prefix to exactly **12,919** unique listing IDs, so they are the
required cap subdivision rather than an optional tech filter.

The cap-safe listing union took 1,032 listing/function-catalog requests and 42,633,835 response
bytes. Applying the authoritative title-only tech gate to all 12,919 rows retained **9,223**
(71.39%). Five live detail responses measured 2,085–4,473 bytes (mean 3,281.8); one full detail
pass therefore projects 42.4 MB. Combined with the measured listing bytes, this is about 85.0 MB
or **9.2 KB per tech Job**, well below ADR-0158's 2 MB enablement bar.

Two burst checks initially returned only `200`: 245 global-list reads at widths 1→128, peaking at
55.21 requests/second, and 245 repeated reads of one detail at widths 1→128, peaking at 249.47
requests/second. Those are not sustainable rate limits. A later full distinct-detail crawl made
the direct route return `429` even at one request, with `Retry-After: 55`; a subsequent 125-job
distinct-detail ramp during that window was all `429`. The scraper therefore uses bounded parallel
listing/detail work and `egress_fallback_on={429}` rather than treating the repeated-ID burst as
an origin budget. The direct route's sustainable per-window quota is not established; the runtime
429/`Retry-After` response is the authority, so production rotates egress instead of claiming a
fixed safe detail width.

## Marketplace decision

The owner authorized marketplace ingestion. Instahyre is one synthetic `instahyre:global` Board
for liveness and eviction scope, while each Job retains its source-local employer profile id and
display name. It is not marked as a Company-directory `aggregator`, and the implementation makes
no cross-source company merge or deduplication claim. The rationale, rejected profile-Board
alternative, source-kind definition and 429 spare-egress fallback are recorded in ADR-0389.
