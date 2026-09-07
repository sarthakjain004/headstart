# Jobvite: surface investigation and scraper

Captured 2026-09-07 against the live pool — 517 tenants (`data/ats-tenants-merged/jobvite.csv`,
gitignored), 7,582 requests, everything at 6 concurrent workers with `User-Agent: headstart/0.1`.
Zero non-200s that were not a redirect: no rate limit, no bot wall, no `Retry-After` was ever seen.

The scraper is `src/headstart/scrapers/jobvite.py`; its module docstring is the summary of record
and this file is the working. Captures are in `artifacts/`.

## What the browser found that HTML scraping would have missed

Three HAR captures (Chromium via Playwright, `artifacts/*_browser.har`,
`artifacts/*_facets-xhr.har`). **Jobvite's public career site makes no first-party XHR for
postings.** Every `jobs.jobvite.com` entry is a `document` navigation; the Angular bundle
(`jv.careersite.desktop.app.js`, 400 KB) contains exactly one `$http.get`. Two first-party JSON
endpoints exist and neither is a jobs API:

| endpoint | returns | useful? |
| --- | --- | --- |
| `/{slug}/search/facets?nl=1` | `{facets: {regions, locations, departments, categories, jobTypes, subsidiaries}}`, each `{EId, name}` | the filter taxonomy, **no postings** (`artifacts/…_search-facets.json`) |
| `/{slug}/job/{id}/recommend?nl=1` | five `{jobEId, title}` pairs | no fields, capped at 5 |

The historical partner API is auth-walled: `api.jobvite.com/api/v2/job` answers
`401 {"status":{"code":401,"messages":["Invalid api/secret. Try again with a valid api/secret"]}}`
with and without a `companyId` (`artifacts/…_partner-api-401.txt`) — a per-tenant key we cannot
have for unaffiliated tenants, the same dead end as Zoho's authenticated API.
`app.jobvite.com/CompanyJobs/Careers.aspx?c={companyEId}` is a legacy alias serving byte-identical
HTML (28,814 bytes on barracuda, same as `/jobs`).

The facets endpoint is the one thing the HAR added that reading the page would not have: it named
the query parameters `/search` accepts (`r` regions, `c` categories, `d` departments, `l`
locations, `t` jobTypes, `s` subsidiaries, `q` keyword, `fd`/`td` dates) and it is how the
**subsidiary nesting** below was first suspected.

## The listing surface

Measured on all 517 tenants, both `/jobs` and `/search`, then a full paginated walk
(`artifacts/2026-09-07_board-surfaces.csv` has every board).

| | boards |
| --- | --- |
| 200 on `/jobs` and `/search` | **434** |
| 3xx (dead / walled / moved) | **83** |

Of the 434 live boards: **401 hiring**, **33 empty** (200, no postings, no pagination counter —
"No results found."). **23,461 postings** in one sweep, mean **58.5** per hiring board, median
**21**, max **2,831** (`firstcash-holdings-inc`), **761 pages** to walk the whole pool.

### `/jobs` is not the listing, and it fails silently

| surface | verdict |
| --- | --- |
| `/{slug}/search?p=N` | complete. 50/page, 0-indexed `?p=`, `jv-pagination-next` link, counter states the true total |
| `/{slug}/jobs` | **short on 139 of 434 boards.** On a branded career site it is a marketing landing page with no job list at all — 43 boards return zero there while `/search` serves 6–78 |
| `/{slug}/jobs/viewall` | exists on only 28 boards; a subset of `/search` on all of them |

**Zero boards had `/jobs` carry a posting `/search` lacked.** Reading only `/search` page 0 — the
obvious first move — caps 65 of the 66 boards over 50 postings at exactly 50.

### Five row templates, and each row-shaped parse died on a different slice

Tenants customise the row markup. Every parse anchored on the row returned **zero rows, silently**,
on some fraction of boards:

| parse anchored on | boards returning 0 rows despite a non-zero counter |
| --- | --- |
| `<td class="jv-job-list-name">` + fixed column order | 84 / 434 (19.4%) |
| the same class, any element, link *inside* it | 64 / 434 (14.7%) |
| `<a href=".../job/{id}">` anywhere | 6 / 434 (1.4%) |
| **the `/{slug}/job/{id}` path itself** | **0** |

The five templates: classic `<td>`; `<div class="tr">` with div cells (`lhhcareers`); extra
`jv-job-list-req` / `jv-job-list-type` columns (`von`); an `<a>` that *wraps* the name element
(`aryaka`); and `<tr onclick="window.location.href='/agscareer/job/…'">` with **no anchor at all**
(`agscareer`). So the scraper scans for the path and takes the id, nothing else — every field comes
from the detail page.

Validated against the boards' own counters: **395 of 434 exact, 6 short, 0 over.** Zero over is
what says no stray link is mistaken for a posting.

### The 6 "short" boards are not short

Verified page by page: the counter is stable across every page and the last page is reached. The
gap is Jobvite serving one posting in **two pagination slots** — re-measured on `fprs`,
1,933 slots across 39 pages against 1,896 distinct ids (37 duplicated). `cascade` was the original
example at 71 slots and 70
distinct ids, `oPSIAfwJ` on both pages. So `distinct < total` must **not** drive
`mark_truncated`; only stopping with a next link still on offer does.

### Localisation

The counter is `1-50 of 2,831`, and on 4 pages `1-50 de 166` (`samtec-sp`, Portuguese). Only two
locales in the pool, but the total is parsed as the **last number** so no connecting word matters.
One board wraps it in `<strong>` and adds a class (`affcareers`:
`class="jv-pagination-text ml-auto">1-17 of <strong>17</strong>`).

## Dead vs empty vs blocked

A departed tenant answers **`302 Location: http://search.jobvite.com?invalid=1`**, and that chain
ends on a 174 KB marketing page with **HTTP 200** (`artifacts/2026-09-07_dead-tenant-302.txt`). A
redirect-following fetch therefore reads a dead tenant as a live board with zero postings — and
`index sync` would evict every row it ever had. Both the scraper and the liveness probe use
`allow_redirects=False`.

The 83 redirects break down as:

| destination | n | meaning |
| --- | --- | --- |
| `search.jobvite.com?invalid=1` | 78 | tenant gone |
| `app.jobvite.com/Login/Login.aspx` | 1 | internal, login-walled board (`hachette-internal`) |
| the customer's own domain, `?p=search&nl=1` | 4 | career site moved off the Jobvite-hosted surface (`imprivata`, `isg-one`, `opentrons`, `shutterfly`) |

The four moved tenants are not recoverable elsewhere: their destinations render the listing
client-side and serve no `/job/` links. All three classes mean "no public board here", so the probe
returns `dead` for any 3xx — and **not one of the 434 live boards redirects**, which is what makes
that safe.

An **empty** board is different and must stay `live`: 200, no `/job/` links, no counter at all.

## Detail-page field coverage

1,157 detail pages, 3 per hiring board across all 401 (`artifacts/…_detail-field-coverage.json`).

- **1,155 answered 200**; 2 were 302 (one tenant, `g100-companies`, redirects each posting to its
  own site).
- **1,077 (93.2%) carry a parseable schema.org `JobPosting` JSON-LD block.** 51 pages have none at
  all and 27 have one that will not parse — 78 pages on **29 boards**.
- The HTML fallback (`jv-header` / `jv-job-detail-meta` / `jv-job-detail-description`) recovered
  title **and** description on **78 of 78** of those, and the meta line on 75.

Coverage where the JSON-LD exists:

| field | Job field | coverage |
| --- | --- | --- |
| `title`, `description`, `datePosted`, `industry`, `addressCountry` | title, description, posted_at, department, location | **100%** |
| `employmentType` | employment_type | 84.4% |
| `addressLocality` / `addressRegion` | location | 78.6% |
| `hiringOrganization` | company | 100% (68.4% as a bare string, 31.6% as an `Organization` object — **both shapes are live**) |
| `baseSalary` with a real `minValue`/`maxValue` | salary | **10.96%** |

As a share of all 1,155 sampled 200s that is 93.2% for the always-present group and 9.7% for
salary. `jobLocation` is an array: 951 postings have one entry, the rest up to 14 — only the first
is read, which is the usual "N Locations" posting.

The salary strings this scraper builds (`105000 - 130000`, `13990 - 19375 USD`,
`65321 - 97981 USD Annually`) all parse through `salary.extract`'s existing `_field_generic` with
`source='field'` and no `salary.py` change — so **no `DERIVATIONS_VERSION` bump is needed**
(CLAUDE.md's stated exemption: nothing already indexed can change).

`employmentType` is free text and wildly per-tenant — "Full-Time" (526), "Full Time", "Regular
Full-Time", "FULL-FULL TIME", "F - Full Time", "Colleague (Salary)". Passed through as the provider
phrases it, per the `Job` model's contract.

## Company names

Every board titles itself `"{Company} Careers"` — the wrapper `headstart.company_name` already
models for eightfold and keka. Measured across all 434 live boards, `from_title` resolves a real
name on **424 (97.7%)**, which beats the 93.2% of postings whose JSON-LD names a
`hiringOrganization` and is the only source for the 29 no-JSON-LD boards. The 10 misses all keep
the slug (7 whose name still carries "Careers", one Portuguese "carreras", one with no wrapper).
So `board_page()` returns the board (ripplehire's precedent, one request per Board), and
`company_name.PATTERNS`/`_VENDOR_ALIASES` gain a `jobvite` row.

## Tech share and yield

`headstart.tech_filter.is_tech` over 23,436 listing titles on 400 boards: **1,642 tech (7.0%)**,
spread over 195 boards. The task brief's 14% came from a 70-tenant sample; at pool scale it is
half that. The titles are genuinely software though — *Staff QA Automation Engineer*, *Senior
Machine Learning Engineer*, *Full Stack Developer*, *AI Engineer*, *Sr. Platform Engineer* — and
the densest boards are real tech employers (`nutanix` 59/233, `ninjaone` 42/94, `samtec` 43/166,
`enphase-energy` 36/77) alongside a cluster of US cleared-defence integrators (`ovt` 94/110,
`sagecor` 86/94, `redalpha` 64/77, `visionist` 42/47).

## Known property: Jobvite tenants nest

A parent tenant serves its subsidiaries' postings. Measured over the whole pool, **579 of 22,857
distinct postings (2.5%) are served by exactly two boards** — never more than two:

- `ziffdavis` ⊇ `ookla` (33), `spiceworks` (7), `ziff-davis` (3), `everyday-health-*`, `ign`,
  `retailmenot`
- `firstcash-holdings-inc` ⊇ `affcareers` (17); `sumitomo-electric` ⊇ `judd` (16);
  `pt-holdings` ⊇ `generalparts` (56); `gvwgroup` ⊇ `autocar` (20), `aculocity`,
  `autocar-parts-llc`; `lhhcareers` ⊇ `ezra`; `willsgroup` ⊇ `dashin`; `leadventure` ⊇
  `dealer-spike-belize`
- two pairs are the same board under two slugs: `samtec`/`samtec-sp` (166 ids, identical — the
  Portuguese-localised twin) and `exabeam`/`logrhythm` (3)

Those reach the index as two rows under two Board keys, which is ADR-0023's duplicate-prune case.
Nothing is done about it in the scraper.

## Cost

One full sweep of the pool: **761 listing pages + 434 board-title fetches + ~23,500 detail
fetches**. Detail pages are 40–110 KB, so a cold full sweep is roughly 1.5–2 GB — comparable to
zoho's, and ADR-0048/0050's `have_details` skips postings whose description is already stored, so
steady state is only new postings.

## Reproduction

Nothing here needs a token. The whole investigation is `curl` plus Playwright against
`jobs.jobvite.com`; `artifacts/2026-09-07_board-surfaces.csv` is the per-board table every count
above is computed from.


## A note on the HAR captures

The three browser HARs this investigation used (`barracuda-networks-inc`, `firstcash-holdings-inc`,
`evergreenhealth`, 1.28 MB together) are **not committed**. They were scanned first and carried no
cookies, no `Authorization` and no `Set-Cookie`, so nothing was leaked by having them — they are
left out because this repo routes raw captures to `experiment/` (gitignored) and its binding cost
constraint is storage, and because what they established compresses to one sentence: across 256
entries on three boards, every `jobs.jobvite.com` request is a `document` navigation and the only
first-party JSON is `/search/facets` (taxonomy, no postings) and `/job/{id}/recommend` (5 related
postings). Re-capture with `playwright` against any board if that ever needs re-checking.
