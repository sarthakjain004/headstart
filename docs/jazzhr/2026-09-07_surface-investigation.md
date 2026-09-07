# JazzHR: what the boards actually serve

Investigation behind `src/headstart/scrapers/jazzhr.py`, `check_liveness.p_jazzhr` and the
`jazzhr` entry in `verify_filters.URL_SHAPES`. Everything below was measured on **2026-09-07**
against live `*.applytojob.com` hosts. Sample sizes are stated on every number; where a claim
rests on one tenant it says so.

Scope of the measurement: a **random 1,000-tenant sample** (seeded shuffle) of the 4,647 rows in
`data/ats-tenants-merged/jazzhr.csv`, plus targeted follow-ups. About **6,100 HTTP requests** in
total, at 10-way concurrency, User-Agent `headstart/0.1`.

## 1. There is no JSON API — checked, not assumed

Driven in headless Chromium with a HAR recording (`experiment/jazzhr-surface-2026-09-07/`,
gitignored; `capture_har.py` reproduces it), visiting the career page, the embed listing and a
job detail page on `10pearls`:

| requests | XHR/fetch | first-party data calls |
| --- | --- | --- |
| 119 | 17 | **0** |

All 17 are telemetry — New Relic `bam.nr-data.net`, Google Analytics `collect`, reCAPTCHA. The
board's own script, `/js/apply/jobs.js`, contains no `$.ajax`, `fetch` or XHR of any kind: it is
jQuery UI glue for an accordion and an iframe-resize postMessage. Every page is server-rendered
PHP. So HTML parsing is not a fallback here, it is the only surface.

Endpoints probed and rejected on `10pearls`:

| path | result |
| --- | --- |
| `/apply/jobs.json`, `/jobs.json`, `/sitemap.xml` | 404 |
| `/feed`, `/apply/rss`, `/apply/feed`, `/apply/index.rss` | 302 to `www.applytojob.com`, or 410 |
| `/apply/jobs/rss` | 302 to `app.applytojob.com/notfound.html` |
| `/apply/?format=json` | 200, byte-identical HTML |

## 2. Two listing surfaces, and why the scraper uses the second one

* `/apply/` — the tenant's **career page**. Per-tenant themed.
* `/apply/jobs` — the vendor's **embed listing**, the thing a company iframes into its own site.
  One template for every tenant.

Across the 1,000 sampled tenants, `/apply/` came back in three shapes:

| career-page theme | tenants |
| --- | --- |
| responsive (`job-board-list-wrapper`, `<li class="list-group-item">`) | 903 |
| classic (`resumator-home-joblist`, `resumator-job-title-link`) | 22 |
| fully custom (tenant CSS/markup, e.g. `selectra`) | a tail inside the above counts |
| "JazzHR - Inactive Career Page" (departed tenant) | 66 |
| 302 off-host to the vendor's job-seeker page | 9 |

`/apply/jobs` renders the same table for all of them, and carries two fields the career page
does not: `<span class="resumator_department">` and the posting's internal
`job_{YYYYMMDDHHMMSS}_{KEY}` id.

**Do the two agree?** For the 925 tenants that served a board at all: **918 agreed job-for-job.**
The 7 that differed did so by exactly one, and each was traced by hand:

| tenant | page-only key | what it is |
| --- | --- | --- |
| `outcomes4me` | `WJw65IG0o7` | "Don't see the job you're looking for?" |
| `communitylivingalliance` | `p0UF1n9Poi` | "Submit Resume For Future Consideration" |
| `newyorkcitybarassociation` | `ngeOZuEcV9` | "Future Consideration" |
| `cmccfoundation` | `FMS5T7FoMC` | "Spontaneous Application" |
| `westdaleassetmanagement` | `rh1WQQL3sV` | "Welcome To The Candidate Center" |
| `aoglobelifesholaadebayo` | `vendor` | `/apply/vendor/309`, not a job at all |
| `poweredbysearch` | — | artefact of the harvest's own counting, not a real difference |

Every one is linked from the tenant's *welcome text*, outside the "Current Openings" list. The
embed listing is the openings list; the career page is the openings list plus prose. Reading the
career page whole would ingest evergreen talent-pool postings as jobs.

## 3. No cap, no pagination

| tenant | `/apply/jobs` | `/apply/` | bytes | time |
| --- | --- | --- | --- | --- |
| `milehighadjustershoustoninc` | 3,450 | 3,450 | 3.1 MB | 0.63 s |
| `farinspections` | 3,076 | 3,076 | — | — |
| `bciacrylic` | 755 | 755 | 0.70 MB | — |

No paging control exists in the markup. Nothing in the sample was observed short, and the
listing states no total, so the scraper never calls `mark_truncated` — marking on a signal that
might fire always or never is the ADR-0053 trap.

Distribution over the 810 hiring boards in the sample: **27.5 jobs per hiring board**, 24.1 per
live board (hiring + empty), median 6, p90 40, max 3,450.

**Tech share: 5.1%** — `tech_filter.is_tech(title)` over all 22,287 listing rows returns 1,126.
That is title-only; the pipeline's real gate also sees the department, so this is a floor.

## 4. Dead, empty and blocked are three different things here

All 1,000 sampled tenants answered HTTP **200** at the transport layer. Zero 403s, zero 429s,
zero 5xx, one timeout. A departed tenant is a **soft-404**:

| what | how it looks | count |
| --- | --- | --- |
| live, hiring | 200 + `id="jobs_table"` + ≥1 `row_job` | 810 |
| live, nothing open | 200 + `id="jobs_table"` + 0 rows | 115 |
| tenant gone | 200 + "JazzHR - Inactive Career Page" | 66 |
| tenant gone | 302 → `info.jazzhr.com/job-seekers.html` | 9 |

A slug that was never a tenant behaves exactly like the last row (`*.applytojob.com` is wildcard
DNS, so the host resolves and then 302s) — **DNS never settles a board here**. Hence
`p_jazzhr` reads the page shape, not the status: a 200 without the `jobs_table` shell is DEAD;
anything that is not a 200/404/410 is UNKNOWN.

Run independently over the same 1,000 tenants, that probe returned **live 924, dead 75,
unknown 1**, and cross-checked against the career-page harvest: 923 live/live, 75 dead/dead,
**zero contradictions**. The two rows that differed were the one transport timeout (correctly
UNKNOWN) and one empty board the career-page harvest miscounted.

The probe counts **distinct** `row_job` ids: the same response renders every posting twice, once
in a desktop `<tr>` table and once in a mobile `<div class="jobs_row">` list (60 elements for 30
postings on `10pearls`).

## 5. Field coverage

### The listing (`/apply/jobs`), n = 22,287 rows

| field | coverage |
| --- | --- |
| title | 100% |
| location | 100% |
| department | 24.4% |
| `job_{stamp}_{KEY}` internal id | 100% |
| company name (Organization JSON-LD on the page) | **300/300 boards probed**, properly cased |

The listing page's own `<title>` is the vendor's ("JazzHR » Job Listings"), so the schema.org
`Organization` blob is the only place the tenant's name appears — and it is always there, in a
better form than the slug ("TWD Technologies Ltd." for `twd`). That removes any need for a
`board_page()` override or a `company_name.PATTERNS` entry.

### The detail page (`/apply/{key}`), n = 1,526 pages (2 per board, stratified)

| field | source | coverage |
| --- | --- | --- |
| description | `#job-description` / `#resumator-job-description` | **400/400** (re-fetched sample) |
| employment type | `id="resumator-job-{employment,type}"` | 400/400 |
| experience | `id="resumator-job-experience"` | 400/400 |
| department | `title="Department"` / `id="resumator-job-department"` | 32.8% combined with the listing |
| JSON-LD `JobPosting` present | — | **69.7%** |
| ↳ `datePosted`, `employmentType`, `experienceRequirements`, `jobLocation`, `uniqueJobCode`, `validThrough` | | 100% *of those* |
| ↳ `baseSalary` | | 37.0% of those = **25.8% of all pages** |
| ↳ `jobLocationType` | | 13.6% of those |

**JSON-LD presence is per posting, not per tenant** — 163 of the 717 tenants with more than one
sampled page had it on some jobs and not others. So it cannot be treated as a tenant capability.

**The HTML beats the JSON-LD where they overlap**, which is why the scraper prefers it:

* Coverage: 100% vs 69.7%.
* Granularity: the schema enum collapses JazzHR's own labels. `FULL_TIME` covers "Full Time",
  "Part Time to Full Time", "Contracted to Full Time" and "Temporary to Full Time"; `TEMPORARY`
  covers "Temporary" and "Seasonal".
* Fidelity: scored on the 286 sampled pages carrying both, the HTML description matched the
  JSON-LD exactly on 270 and to within 2% on **286/286**; the experience label matched
  **286/286**.

JazzHR's own `Experience` vocabulary, over 1,496 pages: Experienced 500, Mid Level 429, Entry
Level 380, Manager/Supervisor 77, Student (College) 35, Senior Manager/Supervisor 35, Student
(High School) 21, Executive 14, Senior Executive 5. `experience.from_seniority` reads all of
them except "Manager/Supervisor", which correctly falls through to the title.

### Why there is no ADR-0048 `needs_detail` skip

Eightfold skips the detail fetch for postings whose description is already in the ADR-0050
store, because for eightfold the detail fetch supplies *only* the description. That does not
hold here: this page is also the only source of `employment_type`, `experience`, `posted_at`
and `salary`, none of which the description store holds. Skipping it on an already-described
Job would blank four fields that had values. Zoho reached the identical conclusion for the
identical reason (`zoho.py fetch_raw`: gating on description presence made Salary structurally
invisible on ~60% of its jobs). So JazzHR pays ~27.5 detail fetches per hiring board per run.

Measured end to end on `bciacrylic` (755 postings, one run through
`get_scraper("jazzhr", …).fetch()`): description 755/755, employment_type 755/755, experience
755/755, location 755/755, posted_at 581/755, salary 413/755, department 16/755. No failures,
no truncation.

## 6. `posted_at`: the tempting field is the wrong one

Every listing row carries `job_{YYYYMMDDHHMMSS}_{KEY}`, and it is 100% present — far better
coverage than the JSON-LD's 69.7% `datePosted`. It is still the wrong field. Compared on the
1,063 pages carrying both:

| relation | count |
| --- | --- |
| stamp date **==** `datePosted` | 752 |
| stamp date **earlier** than `datePosted` | 311 |
| stamp date **later** than `datePosted` | **0** |

The stamp is the record's *creation* time; `datePosted` is publication. The gap reaches years
(`job_20171027155703` on a posting dated 2026-06-17). Using it as a fallback could only ever
make a posting look older than it is, on ~29% of jobs. So the scraper takes `datePosted` or
nothing, and 30.3% of JazzHR Jobs carry no `posted_at`.

## 7. `salary`: a real structured field, and one line in `salary.py`

`baseSalary` is a schema.org `MonetaryAmount` — `currency` (USD 355, CAD 31, GBP 4, EUR 3 of the
393 observed), `value.unitText` (HOUR or YEAR), and `minValue`/`maxValue` (360) or a single
`value` (33). The scraper renders it as `"MIN-MAX CUR UNIT"`, the shape
`salary._field_range_currency_interval` already reads for rippling and ashby, and registers
`jazzhr` on that parser.

That registration is load-bearing, not tidiness. `_field_generic` uses the phrase-only
`_period_multiplier`, which does not recognise a bare `HOUR`, so every hourly figure was read as
annual and then correctly rejected by the plausibility floor:

| parser | of the 393 real values, parsed |
| --- | --- |
| `_field_generic` (the default) | 198 |
| `_field_range_currency_interval` (registered) | **368** |

The 25 that still decline are the bound doing its job on a tenant's own data-entry error — an
hourly rate typed under `unitText: YEAR`, e.g. `"35-60 USD YEAR"`.

`DERIVATIONS_VERSION` is deliberately **not** bumped: the change is a new key in
`_FIELD_PARSERS` that no already-indexed row dispatches on (zero jazzhr rows exist), which is
exactly the "provably inert on anything already stored" exemption in CLAUDE.md.

## 8. `remote`: the native flag was checked and adds nothing

145 of the 1,063 JSON-LD pages carry `jobLocationType`, and on every one of them the value is
`TELECOMMUTE`. On 60 of 60 sampled, the listing location string already read exactly `"Remote"`,
so `models.is_remote(location)` already returns True. Reading the field would add zero rows.
Recorded so nobody re-derives it as free signal later.

## 9. Job URLs

The board links to `/apply/{key}/{Title-Slug}`. The title slug is decorative: `/apply/{key}`
alone serves the same page (200 on 1,526 of 1,527 fetched that way; the one miss was a transport
timeout), and even a deliberately wrong slug 200s. A key that does not exist answers **410**,
which is what makes these links checkable by `verify_filters`. Keys are 10 alphanumerics on most
tenants and a long hex string on some (`/apply/07350d2d7f03…/`), so the URL shape's character
class is loose on purpose.

## 10. Side finding: a vendor-wide discovery surface

Every tenant's `robots.txt` names five sitemaps on a **vendor** host:

```text
http://app.jazz.co/feeds/google/xml/{0,1,2,3,4}
```

Together they list **114,963 job URLs across 7,602 tenants** (~20 MB; feed 5 onward is an empty
urlset). The current pool has 4,647 tenants, of which 978 do not appear in the feeds (boards
with nothing open) — and the feeds name **3,933 tenants the pool does not have**, a 1.85x
expansion available from five GETs.

Two caveats before anyone builds on it. It is a **tenant**-discovery surface, not a job one: for
some tenants the feed's keys are a long-hex alias that shares nothing with the keys the board
serves (`milehighadjustershoustoninc`: 3,450 keys on each side, zero overlap), so feed URLs
cannot be joined to scraped Jobs by id. And it is a daily snapshot — on `farinspections` the
feed listed 64 keys the live board no longer had.

## Reproducing

The scripts live in `experiment/jazzhr-surface-2026-09-07/` (gitignored, per the repo's
experiment-captures rule):

| script | what it measures |
| --- | --- |
| `harvest_boards.py` | both listing surfaces for a 1,000-tenant sample → `artifacts/boards.jsonl` |
| `harvest_details.py` | 2 detail pages per hiring board → `artifacts/details.jsonl` |
| `capture_har.py` | the Chromium HAR + a request-type census |
| `probe_liveness_shape.py` | the `p_jazzhr` discriminator over the same 1,000 tenants |
| `measure_extractors.py` | the HTML extractors scored against the JSON-LD |
| `check_derived.py` | stamp-vs-`datePosted` direction; `salary`/`experience` round-trips |
| `check_department.py`, `check_page_only_keys.py`, `probe_org_name.py`, `probe_location_type.py` | the single-question follow-ups above |
| `compare_surfaces.py`, `live_smoke.py` | per-tenant surface diff; end-to-end scrape |
