# Tesla careers: API measurement (2026-09-11)

`scrapers/tesla.py`, wired through `data/validate/liveness/tesla.csv` (one hand-entered row,
8,105 jobs). A single-source ats (ADR-0139): `www.tesla.com` runs its own in-house board, one
company, never a second tenant.

## No ordinary HTTP client can read this board at all

`www.tesla.com` sits behind Akamai Bot Manager, and the wall covers the whole origin, not just
the API:

| request | result |
| --- | --- |
| bare `curl`, no headers, `GET /cua-api/apps/careers/state` | `403`, `AkamaiGHost` |
| bare `curl`, a real Chrome `User-Agent`, same URL | `403` |
| bare `curl`, same UA, `GET /` (homepage) | `403` |
| bare `curl`, same UA, `GET /robots.txt` | `403` |
| `curl_cffi` (Chrome TLS impersonation — this repo's `http.py` transport), `GET /cua-api/apps/careers/state` | `429`, `{"cpr_chlge":"true","t":"<ts>"}` |
| `curl_cffi`, homepage first (picks up `_abck`/`bm_s`/`bm_sc` Akamai cookies), then the same GET in the same session | `429`, same body |

4 request shapes, all 403 or 429; 0 got through. The `429` body is Akamai's own behavioural
challenge marker, not a rate-limit in the ordinary sense — see below.

## A real, JS-capable browser gets further, but only for a request it issues itself

Tested with `pydoll` driving real, headful Google Chrome (no stealth flags beyond
`--disable-blink-features=AutomationControlled`; headless was not tried — ADR-0056's darwinbox
precedent already found this class of wall headless-hostile, so headful was the starting point,
not a fallback):

1. Navigate to `https://www.tesla.com/careers/search/`. The page renders a real single-page app
   (DOM carries `moka-version` — this is a white-labelled Moka HR front end on Tesla's own path,
   not a shared Moka tenant surface) and, on its own, calls `GET
   https://www.tesla.com/cua-api/apps/careers/state`. **That call gets a clean `200`** — captured
   off the CDP `Network` domain (`Network.responseReceived` + `Network.getResponseBody`), no
   request issued by this code.
2. From that same warmed tab (Akamai cookies present, page fully settled 10s+), issue a **second**
   explicit request to that exact URL — via a raw injected `fetch()`, or via `pydoll`'s own
   `tab.request.get()` (itself a page-context `fetch`, inheriting cookies) — and it comes back
   `429 {"cpr_chlge":"true"}`, identical to the plain-curl case. 2 of 2 explicit-request methods
   tried, both walled.
3. The same failure reproduces on a **different, not-yet-hit** URL: the per-job detail endpoint
   (`GET /cua-api/careers/job/224501`), issued explicitly from the same warmed tab — `429`, same
   body. So this isn't "the state endpoint specifically is guarded harder"; any request this code
   issues gets it, whatever URL.
4. But navigating (not fetching) directly to that job's own page —
   `https://www.tesla.com/careers/search/job/ai-engineer-manipulation-optimus-224501` — succeeds:
   the page's own natural call to `/cua-api/careers/job/224501` gets `200`, capturable the same
   way as step 1.

The pattern across all four: **a request the page issues for itself, on a fresh navigation, is
trusted; a request this code issues on top of an already-navigated page is not** — regardless of
cookies, warm-up time, or whether the URL was already hit. That is consistent with (not
contradicted by) `kalil0321/ats-scrapers`' independent finding that Tesla needs a purpose-built
stealth Chromium fork plus a residential proxy: they hit the same wall and built more machinery
around it than this repo's stock `pydoll` session needed for the one call that matters (the
full-catalog listing).

## What this means for the scraper

`fetch_raw()` never issues an HTTP request. It drives one headful Chrome, navigates once to the
careers search page, and reads the body of *that page's own* first-load call to the state
endpoint straight off the CDP `Network` domain. This is a different contract from
`headstart.browser_http.origin()` (navigate, then explicitly `get_json`/`post_json` on the warmed
tab) — that explicit-request half is exactly what step 2 above shows failing — so `tesla.py`
keeps its own minimal, single-purpose Chrome lifecycle rather than bending the shared one to a
second shape for a single caller.

## The listing: one document, no pagination, 8,105 jobs

`GET /cua-api/apps/careers/state` (only reachable per above) returns the *entire* catalog in one
response: `{"listings": [...], "lookup": {...}}`, measured 8,105 listings 2026-09-11. Each
listing is minified onto short keys:

| key | meaning | notes |
| --- | --- | --- |
| `id` | native job id | |
| `t` | title | |
| `dp` | department id | resolves through `lookup.departments` (18 entries) |
| `f` | an internal "function"/team id | 17 distinct values, **not resolvable to a name anywhere in this payload** — dropped, not carried onto `Job` |
| `l` | location id | resolves through `lookup.locations` (13,234 entries); **4 of 1,309 ids referenced by the live listing set were absent from it** — those resolve to `None` |
| `y` | employment-type id | resolves through `lookup.types` (`{"1":"fulltime","2":"parttime","3":"intern","4":"seasonal"}`) — measured 1=7,277, 2=113, 3=576, 4=139, matching the lookup exactly |
| `sp` | a per-listing sort index | not a real field — near-unique per row, not carried onto `Job` |
| `pu` | an ISO application deadline | non-null on only 39 of 8,105 — an expiry, not a posted date, not carried onto `Job` |

No field states when a posting went up. The per-job detail payload (below) doesn't either — only
`postUntilDate`, the same deadline as `pu`. `Job.posted_at` is therefore always `None`.

## The per-job detail payload exists, but a detail pass is not implemented

`GET /cua-api/careers/job/{id}` (reachable only by *navigating* to that job's own page — see
above) returns `id`, `title`, `department`, `jobFamily`, `location`, `state`, `country`,
`description` (empty in the one job sampled), `jobDescription`/`jobResponsibilities`/
`jobRequirements`/`jobCompensationAndBenefits` (HTML), `applicationType`, `timeType`,
`subWorkerType`, `url`, `applyUrl`, `postUntilDate`. Real content — but reaching it costs a full
browser navigation per job, not a cheap JSON `GET` the way every other detail-pass ATS in this
repo works. At 8,105 postings that's thousands of navigations every run, which doesn't fit a
nightly pipeline's time budget. `has_detail_pass` is `False` and `Job.description` is `None` for
every Tesla row in this version — a deliberate scope cut, documented so it's revisited
deliberately rather than silently, not an oversight.

## Sample size

This is a single-tenant board — there is exactly one `www.tesla.com` to measure, so "sample size"
here means requests, not boards: 2 shapes of failing HTTP client (curl bare, curl_cffi
Chrome-impersonated) × a handful of paths each (root, robots.txt, the state API, with and without
a homepage warm-up), 2 explicit-request methods tried from a warmed browser (raw `fetch`,
`pydoll`'s `tab.request`) against 2 different URLs (the state endpoint, a job detail endpoint),
and 2 successful natural-navigation captures (the listing page, one job's detail page). Small,
deliberately — this is a discovery/design probe for one board, not a rate-limit characterization
across many tenants (contrast Oracle's 6,351-request sweep across 670 hosts), and it was
timeboxed rather than run to exhaustion once the working mechanism (navigate-and-capture) was
confirmed twice (listing + one job detail).

## Completeness: is the one document actually the whole board? (2026-09-12)

The single-document, no-pagination shape (above) trades away the usual completeness check a
paginated API gets (a stated total to page towards) — so it's checked a different way, against
the real scraper (`TeslaScraper.fetch_raw()`), live:

- **No stated total anywhere in the payload.** Walked every key of a live response recursively
  looking for anything named `total`/`count`; none exists. The frontend has nothing to check its
  own render against either, and neither does this scraper.
- **The frontend never fetches more.** Captured every `tesla.com` network request during and
  after 5 `scrollTo(bottom)` actions on the loaded search page: 3 requests total, all made before
  the first scroll, zero new ones after. If the UI paginated or lazy-loaded further listings on
  scroll, this would show it; it doesn't, which is consistent with `apps/careers/state` being a
  one-shot full-state dump the client filters/pages *locally* (matching its own name — a client
  "state" object, not a search-results page).
- **No hidden job ids elsewhere in the payload.** The response also carries `geo` (a
  region→site→state→city location hierarchy) and top-level `departments` (a department→
  sub-department id map) trees, either of which could in principle reference postings the
  `listings` array omits. Cross-checked live: every id in the `geo` tree's leaf arrays is a
  *location* id (13,034 distinct, overlapping `lookup.locations`' 13,234 — not job ids at all;
  zero overlap with `listings`' own ids). No job id exists anywhere in the payload outside
  `listings`.
- **The count isn't suspiciously round.** Two live re-runs a day apart: 8,105 (2026-09-11) then
  8,115 (2026-09-12) — a +10 (+0.12%) day-over-day change consistent with ordinary posting churn,
  not a fixed cap (neither number is a multiple of any common page size: mod 10/20/25/50/100 all
  nonzero).

No stated ground truth exists to check against directly, so this is structural evidence, not a
count matched against an authority — but three independent signals (no lazy-load on scroll, no
job ids hiding in the other trees, a non-round and naturally-varying count) all point the same
way. Confidence: high that `fetch_raw()`'s one document is the whole board, on the evidence
available; there remains no way to *prove* it against a total Tesla itself never states.
