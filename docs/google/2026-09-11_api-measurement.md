# Google careers: API measurement (2026-09-11)

Google is the first "single-company board" ATS (ADR-0139) — one in-house careers system, one
tenant, forever. This is the live research behind `src/headstart/scrapers/google.py`: the real
requests made, what came back, and the sample sizes each finding rests on.

## No public JSON API

The plausible guess — `careers.google.com/api/v3/search/` — 404s:

```
$ curl -s -A "Mozilla/5.0" "https://careers.google.com/api/v3/search/?location=&q=&page=1"
{"detail":"Not Found"}
```

The listing is server-rendered HTML at
`https://www.google.com/about/careers/applications/jobs/results?hl=en_US&page=N`. The response's
own CSP confirms there is no separate API origin for client-side XHR to call — `connect-src` lists
only `'self'` and Google's own first-party hosts (`www.google.com`, `accounts.google.com`,
`*.doubleclick.net`, `*.google-analytics.com`, …), nothing that looks like a dedicated careers API
host.

`careers.google.com` — Google's own public careers vanity domain, used as this board's `slug` — 301s
to the real serving host:

```
$ curl -sI -A "Mozilla/5.0" "https://careers.google.com/jobs/results/"
HTTP/2 301
location: https://www.google.com/about/careers/applications/jobs/results/
```

`url()` targets `www.google.com` directly rather than paying that redirect on every request.

## The listing page embeds a full per-job JSON payload — no detail pass needed

Every listing page carries two `AF_initDataCallback({key: 'ds:N', ...})` script blocks — Google's
generic closure-compiled wire format used across many of its web properties, not a documented
public contract. `ds:0` is small (company-name lookup); `ds:1`'s `data` is
`[jobs, null, total, page_size]`. Verified live: `www.google.com`'s own `headstart/0.1` User-Agent
gets the identical two blocks a browser UA gets (`curl -A "headstart/0.1" ... | grep -c
AF_initDataCallback` → 2), so no UA restriction exists and the scraper uses the shared default.

Each of the 20 entries in `jobs` is itself a ~21-element array, already carrying:

| index | content | measured |
| --- | --- | --- |
| 0 | native id | always present |
| 1 | title | always present |
| 3 | `[null, responsibilities_html]` | present on every job sampled |
| 4 | `[null, qualifications_html]` — already has its own `<h3>Minimum qualifications:</h3>` / `<h3>Preferred qualifications:</h3>` | present on every job sampled |
| 7 | company (legal/brand name) | "Google" and "YouTube" both seen on one page-1 fetch |
| 9 | locations: `[[display, [addresses], city, zip, state, country], ...]` | multi-location jobs list every location |
| 10 | `[null, about_the_job_html]` | present on every job sampled |
| 12 | `[seconds, nanos]` — creation timestamp | see "posted date" below |
| 13, 14 | `[seconds, nanos]` — later timestamps, always `>=` index 12 | not used |
| 11, 20 | undecoded enum fields (`[2,3,4]`, `2`) | no string table found anywhere on the page — left unmapped |

This makes the scraper detail-pass-free (`has_detail_pass = False`): every field this scraper
needs, description included, comes off the listing page itself. Confirmed the id-only job URL
resolves the right posting without needing the page's own slug:

```
$ curl -s -A "headstart/0.1" ".../jobs/results/100397577702122182?hl=en_US" | grep og:title
<meta property="og:title" content="Software Engineer, Chrome Networking Security"
```

**Risk, stated plainly:** the field layout is positional indices into an undocumented, versioned
internal array with no field names — a genuine fragility a stable public API wouldn't have. Every
access in the scraper goes through a bounds-checked helper (`_field`) so a shape change drops only
the field whose index moved, not the whole scrape.

## `datePosted` is not fabricated (unlike ICIMS)

CLAUDE.md's ICIMS entry documents `datePosted` fabricated on 22% of that ATS's boards (a value
computed as `now - 2y` at request time). The same check here: re-fetched page 1 twice, ~10s apart.

```
first fetch:  job 100397577702122182 -> [1787782363, 839000000], [1789139424, 899000000], [1789139425, 365000000]
second fetch: job 100397577702122182 -> [1787782363, 839000000], [1789139424, 899000000], [1789139425, 365000000]
```

All three fields identical across the gap — not recomputed per request. And across the 20 jobs on
one page, the earliest field (index 12) ranged from 2026-04-13 to 2026-09-11 rather than every job
reading "now":

```
100397577702122182 | 12: 2026-08-26T22:12:43+00:00
86058825089458886  | 12: 2026-09-11T15:12:53+00:00
106771249476051654 | 12: 2026-04-13T17:30:37+00:00
108457600739091142 | 12: 2026-05-18T16:30:15+00:00
... (20 sampled, spread over 5 months)
```

`posted_at` uses index 12 (the earliest of the three timestamps on every job sampled).

## Pagination: a real stated total, live-churning during the walk

`data[2]` on page 1 is the board's own total; `data[3]` is the page size (20, echoed on every page
sampled, never observed to vary). One page-1 fetch read `total=3414`; a second fetch about a
minute later (after fetching page 100 in between) read `total=3387` — a drop of 27 (0.8%) during
normal use, not an artifact of one request. This is why `fetch_raw` treats the total as a rough
guide (compute pages needed, fan out concurrently) and folds any shortfall through
`mark_truncated_unless_negligible`'s existing 0.99-share tolerance rather than inventing a
bespoke slack constant — the mechanism Oracle/Eightfold already use for exactly this kind of
real-world drift.

Binary-searched the true end of the board (2026-09-11, one session):

```
page 150 -> 20 jobs        page 168 -> 20 jobs
page 165 -> 20 jobs        page 170 -> 20 jobs
page 172 -> 0 jobs         page 171 -> 14 jobs   <- the true last page
```

`171 x 20 - 6 = 3414` exactly matches page 1's stated total (170 full pages x 20 + 14 = 3,414) — the
stated total and the empirical walk agree precisely at that moment. Pages sampled across the whole
range (1, 2, 50, 100, 150, 165, 168, 170) all read exactly 20 — no short page found mid-walk (the
Oracle-class trap), only the genuine last page is short.

## Rate limit: none found at a modest burst

20 concurrent requests (`ThreadPoolExecutor(max_workers=10)`, pages 1-20): 20/20 HTTP 200, wall time
5.2s, ~3.85 req/s aggregate, no 429/403, latency flat (~2.0-2.1s/request, one outlier at 4.78s).
Sample size: 20 requests, one burst — enough to clear the modest concurrency (`_PAGE_WORKERS = 10`)
this scraper actually uses, not exhaustive load testing.

## Fields deliberately left unset

- **`employment_type`**: no decoded label found anywhere on the page for the one enum field
  present (index 20); guessing what the integer means would misrepresent it. Same call Eightfold's
  PCSX scraper makes for the same reason.
- **`department`**: no team/org field found in the listing payload distinct from `company` — the
  rendered detail page's `corporate_fare` icon chip reads "Google" (the company, not a team), so it
  carries no extra signal either.
- **`remote`**: no explicit remote/hybrid flag found in the payload; falls back to
  `headstart.models.is_remote` on the location string, same as every scraper with no native field.

## `company` varies per job, not hardcoded

The careers site serves several Alphabet brands through this one board — "Google" and "YouTube"
both appear on a single page-1 fetch (index 7). The scraper reads this field per job rather than
assuming every posting is "Google".
