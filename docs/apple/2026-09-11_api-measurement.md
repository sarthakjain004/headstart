# jobs.apple.com: what the public API actually returns

Measured live 2026-09-11 against `jobs.apple.com` itself — there is no fleet of tenants here.
Apple is a **Single source scraper** (ADR-0139): one company, one host, `slug` fixed to
`jobs.apple.com` and never discovered. Every figure below is a measurement against the real
endpoint, not a reading of the vendored JS bundle (which was used only to *find* the endpoints —
see §0).

Evidence base: the SPA's own bundle (`jobsite.main.*.js`, 1.5 MB) to locate the API surface, ~15
POSTs to `/api/v1/search` across pages 1-400, 8 concurrent GETs to `/api/v1/jobDetails/{id}`, and
one detail page render (`/en-us/details/{id}/{slug}`) to confirm the job-URL shape.

## 0. No third-party research was carried over

Unlike `oracle.py`/`workday.py`, this scraper has no `kalil0321/ats-scrapers` equivalent worth
adapting — Apple's own `jobs.apple.com/en-us/search` is a React SPA (`window.__staticRouterHydrationData`
carries the SSR'd first page) whose bundle statically declares its API config object:

```
{search:{search:{url:"/api/v1/search"}}, job:{details:{url:"/api/v1/jobDetails/:jobId"}}, ...}
```

found by grepping the fetched bundle for `role`/`search`/`job` path fragments. Everything after
that point is a live measurement against the real endpoints, not a port.

## 1. The listing endpoint is a plain, unauthenticated JSON POST

`POST https://jobs.apple.com/api/v1/search`, body:

```json
{"query": "", "filters": {}, "page": 1, "locale": "en-us", "sort": "newest",
 "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}}
```

Verified with **zero cookies and no `Referer` header at all**: this returns the identical
`totalRecords` and result set as the same request carrying a fresh session cookie captured from a
real browser navigation. There is no CSRF token to fetch, despite the bundle wiring one
(`/api/v1/CSRFToken`) for the signed-in profile flow — that flow is unrelated to search. An empty
`query`/`filters` returns the **whole board**, unfiltered by team, product, or location — exactly
what this scraper wants, since HeadStart's tech gate is the post-hoc `headstart.tech_filter`, never
a scraper-side query narrowing (CLAUDE.md's Project Scope).

The bundle also wires `GET /api/v1/ref/teams` (used to populate the signed-in filter UI). That one
answers `401` anonymously — not needed here, since the empty-filter search already returns every
posting.

## 2. Pagination: fixed 20/page, 1-indexed, terminated by a short page — never by the total

Every page requested, in-range or not, answered `HTTP 200`. Seven calls across pages
`1, 2, 3, 300, 301, 304, 305, 306, 400` (2026-09-11, one host, `totalRecords` read each time):

| page | `searchResults` len | `totalRecords` |
|---|---|---|
| 1-304 | 20 | 6,083 (stable across all of them) |
| 305 (the true last page: `304*20+3=6083`) | 3 | 6,083 |
| 306, 400 (past the end) | 0 | **0** |

**The total is not a safe terminator.** It reads correctly on every in-range page but resets to
`0` the moment the page runs past the end — an out-of-range page answers a blank envelope, not an
error, the same "envelope goes blank" shape Oracle's offset ceiling has. So `_listing()` walks
until a page returns fewer than 20 rows, and only afterward compares the total *captured from an
earlier page* against what was actually read, via `mark_truncated_unless_negligible` (ADR-0053/
ADR-0121) — the same tolerance every other scraper uses, sized for this board's own total rather
than assumed.

No cap was found below 6,083; `_MAX_PAGES=1000` (20,000 postings) is a safety backstop against a
runaway loop, not a measured ceiling.

## 3. Two listing row types, and both are real, applyable postings

Every row's `type` is `REQ` (a specific requisition — `id` already shaped
`{positionId}-{reqSuffix}`, e.g. `200681917-3715`) or `PIPE` (an evergreen "pipeline" role, mostly
Retail — `id` shaped `PIPE-{positionId}`). Tallied over 15 pages / 300 rows: 41% `REQ`, 59%
`PIPE`. Both render identically on the real search page and both accept applications, so both are
scraped — this is not a filter to narrow the way Oracle's `siteNumber` was.

## 4. The listing carries no posting-specific text — a detail pass is required

Every row states a `jobSummary`: a team-level overview paragraph ("Apple Retail is where the best
of Apple comes together...", "Apple is a place where extraordinary people gather..."). It is
**not** a per-posting description — measured across 46 Apple Retail (`APPST`) rows spanning 3
search pages, every one opens with the identical 50-character prefix, varying only in length
(626-1,055 chars) as later, more specific sentences are appended. Using it as `description` would
serve the same boilerplate paragraph on every Retail posting.

`GET https://jobs.apple.com/api/v1/jobDetails/{jobNumber}` — also unauthenticated, also no
`Referer` needed — is the only source of the posting-specific fields:

| field | present on listing | present on detail (2 sampled postings) |
|---|---|---|
| `description` | never | both |
| `minimumQualifications` | never | both |
| `preferredQualifications` | never | both |
| `employmentType` | never | 1 of 2 (`"Intern"` on the REQ; absent on the PIPE) |

`jobNumber` is the listing's own `id` with a `PIPE-` prefix stripped when present — a `REQ` id
already has no prefix and is passed through verbatim (confirmed live: `jobDetails/200313970` for
the `PIPE-200313970` listing row, `jobDetails/200681917-3715` for the `REQ` row, both `200`). An
unknown id answers a clean `HTTP 404` (`{"error":"jobsite.general.serviceError"}`) — unlike
Oracle's silent `200` with `items: []`, so a stale id is distinguishable without extra logic.

`description` is built as `description + minimumQualifications + preferredQualifications`
(joined, then `html_to_text`'d) — never `jobSummary`, for the reason above.

## 5. Remote is a stated field, not a location guess

`homeOffice` (boolean) is present on both listing and detail rows and read directly, the same
precedent Oracle's `WorkplaceTypeCode` set — no location-string fallback was implemented, since
the field is always present (never null on any sampled row).

## 6. No rate limit found

8 concurrent `jobDetails` GETs (deliberately small — this is one host, not a population of tenants
to protect): zero non-200s, 2.3-3.4s each. Not pushed wider — a single-tenant scraper has no
per-Board budget to calibrate against, and the measured concurrency already clears this repo's
usual detail-pass width (16).

## 7. Job URL

`https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}` — verified live: the
page answers `200` and its `<title>` carries the exact posting title
("Apple Vision Pro Hardware System EE Intern - Jobs - Careers at Apple").

## 8. Company name and alias key

`company` is hardcoded to `"Apple"` in `__init__` rather than resolved from a scraped page title
(ADR-0114's `resolve_company` path) — a Single source scraper has exactly one company, so there is
nothing to resolve. `alias_key()` returns the slug itself rather than the base class's
redirect-following default: per ADR-0139, a Single source Board has no sibling tenant to alias
against, so the default's live probe (a GET + redirect-follow) would only ever find nothing.

## 9. `verify-search-filters` could not run against the deployed Space

CLAUDE.md requires running that skill before a new scraper's PR is done. The `URL_SHAPES` entry
was added (§7 above verifies the shape live, independently of the harness), but the harness itself
never ran end-to-end here: it 401s without a signed-in session cookie for
`imposeidon-headstart-search.hf.space` (ADR-0042's wall), and no such cookie was available in this
sandbox. There is also nothing yet for it to check against — Apple has zero indexed rows, since
this scraper has not been through a pipeline run. Flagging this plainly rather than silently
treating the shape check above as a substitute for the skill.
