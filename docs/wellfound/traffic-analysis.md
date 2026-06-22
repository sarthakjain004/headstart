# Wellfound — HAR traffic analysis

Reverse-engineering notes for Wellfound (formerly AngelList Talent) job traffic, derived
from two browser HAR captures in `HAR_Analysis/Wellfound/`. The goal is to understand how
the careers/job pages load their data and what stands between a scraper and that data.

## Source captures

- `wellfound1.har` — 562 requests, complete. Logged-in browsing of the homepage, the jobs
  hub, a role landing page, a company page, and a quick-apply flow.
- `wellfound2.har` — truncated mid-export (the file ends inside a base64 response body);
  239 of its entries are recoverable, the rest are unparseable. It corroborates
  `wellfound1.har` and adds nothing new, so the notes below are keyed off file 1.

Both captures had **cookies and `Set-Cookie` stripped** by the export tool, so session/auth
cookie names are not visible here. Everything else (headers, bodies, paths) is intact.

## What's actually serving the data

Wellfound's candidate site is a **Next.js app** mounted under `/talent/` (assets live at
`/talent/_next/...`, the GraphQL client identifies itself as `apollographql-client-name:
talent-web`). Traffic splits into three buckets:

1. **Server-rendered HTML pages** — the job listings themselves. These are normal document
   navigations; the job data is rendered into the HTML server-side, *not* fetched by a
   client-side "search jobs" GraphQL call in this capture.
2. **`POST /graphql`** — a persisted-query Apollo endpoint used for on-demand fragments
   (autocomplete, a single job's apply modal, company profile, view tracking).
3. **Anti-bot / telemetry** — Cloudflare Turnstile, a first-party DataDome proxy
   (`ddm.wellfound.com`), and rotating obfuscated first-party beacon POSTs.

Of 434 first-party requests in file 1, the overwhelming majority are static `_next` assets,
fonts, and beacons. The data-bearing surface is small.

### Host inventory (first capture)

| Host | Role |
|---|---|
| `wellfound.com` | App, HTML pages, `/graphql`, beacons |
| `ddm.wellfound.com` | First-party **DataDome** proxy (`tags.js` loader + `/js/` sensor POSTs) |
| `photos.wellfound.com` | Company logos / user avatars (CDN) |
| `challenges.cloudflare.com` | **Cloudflare Turnstile** challenge widget |
| `api.rudderstack.com`, `cdn.rudderlabs.com` | RudderStack analytics |
| `www.clarity.ms` | Microsoft Clarity session recording |
| `accounts.google.com`, `play.google.com` | Google One-Tap sign-in |
| `consent.trustarc.com` | Cookie-consent banner |

## Job-page URL taxonomy

The HTML pages a scraper would crawl. All are `GET`, return `text/html`, status `200`:

| Path | What it is |
|---|---|
| `/jobs` | Jobs hub / search entry point |
| `/role/l/{role-slug}/{location}` | **SEO role+location landing page** — the primary job list. Example captured: `/role/l/software-engineer/india` |
| `/company/{company-slug}` | A company's profile + its open roles. Example: `/company/metropolistech` |
| `/jobs/signup?...&job_listing_id={id}&slug={company}&source=...` | Save-job / apply gate that redirects anonymous users to signup |

The `/role/l/{role}/{location}` pattern is the most useful for HeadStart: it is exactly the
engineering-role-filtered, location-scoped slice the project wants, and the slug is
human-derivable (`software-engineer`, `india`). Job data for these pages is in the rendered
HTML — expect it inside the Next.js hydration payload (`__NEXT_DATA__` / `_next/data`).
**Caveat:** this HAR did not record the HTML response bodies (export captured `0` bytes for
document responses), so the exact in-HTML JSON shape must be confirmed against a live fetch.

## The `/graphql` endpoint

```
POST https://wellfound.com/graphql
content-type: application/json
```

This is a **persisted-query** endpoint. The client does **not** send GraphQL query text; it
sends an operation name plus a pre-registered operation hash. Arbitrary queries are not
accepted — you can only invoke operations the server already knows.

### Request body shape

```json
{
  "operationName": "QuickApplyModal",
  "variables": { "jobListingId": "4209895" },
  "extensions": {
    "operationId": "tfe/8ac57d9aae922ab2a6f38edc49925318517a83fdebfefe0538169a27889f9c7f"
  }
}
```

Operations observed, with their persisted `operationId` (the `tfe/<sha256>` is the registered
query hash; it pins the query to a build):

| operationName | operationId | Purpose |
|---|---|---|
| `LocationTagAutocompleteField` | `tfe/9b79e3f292313c0d3fd7afcae87ba698b6b9f0a2a9b41038763c3f2308cf5954` | Location filter typeahead |
| `QuickApplyModal` | `tfe/8ac57d9aae922ab2a6f38edc49925318517a83fdebfefe0538169a27889f9c7f` | Single job-listing detail for the apply modal |
| `CompanyProfile` | `tfe/d5b37f1509f4a633e86e3227b51f54588ec64ba9e92b34b895bb50da05e2c8ec` | Company page data |
| `Overview` | `tfe/392c6aeaffc066d4b2b573ef140374ec9bd0762b15843ffc0dca5b3c2dd9ce8e` | Candidate overview |
| `TrackView` | `tfe/61ca0ca12384e80359923dcca62b807d2fd48ea96c260ce1493e8ce0db3428c7` | Analytics view ping |

### Request headers

The headers that matter (a Chrome-on-Windows session; cookies redacted in capture):

| Header | Example value | Notes |
|---|---|---|
| `content-type` | `application/json` | |
| `apollographql-client-name` | `talent-web` | Static client identifier |
| `x-apollo-operation-name` | `LocationTagAutocompleteField` | Mirrors `operationName` |
| `x-apollo-signature` | `1781970915-TAyS%2BxHH...%3D` | **`<unix-ts>-<base64 HMAC>`**. Signs the request; computed client-side. Reused across all ops in the same page/session window (the timestamp changes per page load, not per call) |
| `x-wf-cfp` | `a331e18a9de04655a9fc152415ef12e4` | Wellfound CSRF/anti-forgery token; stable within a session, rotates across sessions |
| `x-angellist-dd-client-referrer-resource` | `/jobs` | **DataDome** (`dd`) client hint — the logical page the call came from |
| `x-original-referer` | `https://wellfound.com/candidates/overview` | Real originating page |
| `x-requested-with` | `XMLHttpRequest` | |
| `origin` / `referer` | `https://wellfound.com` / `.../jobs` | `sec-fetch-site: same-origin` enforced |
| `user-agent` + `sec-ch-ua*` | Chrome 149 / Windows client hints | Full UA-CH set sent (`sec-ch-ua-arch`, `-platform`, `-full-version-list`, `-device-memory`) |

There is **no `authorization` header** — authentication rides on cookies (redacted here).
All calls are `POST`; no APQ `GET` form was used.

### Response shape

Successful responses are `application/json; charset=utf-8`, `cache-control: private`,
`server: cloudflare`, with a top-level `{"data": ..., "schemaVersion": 2}`.

`QuickApplyModal` returns the cleanest per-job record — useful as a reference for the fields
Wellfound exposes per listing:

```json
{
  "data": {
    "jobListing": {
      "__typename": "JobListing",
      "id": "4209895",
      "title": "Software Engineer",
      "slug": "software-engineer",
      "jobType": "full_time",
      "locationNames": ["Bengaluru"],
      "acceptedRemoteLocationNames": [],
      "remote": false,
      "visaSponsorship": false,
      "salaryCurrency": "USD",
      "offsiteListingUrl": null,
      "startup": {
        "__typename": "Startup",
        "id": "4655945",
        "name": "Applied Intuition",
        "slug": "applied-intuition",
        "avatarUrl": "https://photos.wellfound.com/startups/i/4655945-...-thumb_jpg.jpg"
      }
    }
  },
  "schemaVersion": 2
}
```

This is fetched **per job id** (`variables.jobListingId`), so it's a detail call, not a list
call. The job ids themselves come from the SSR list pages.

## Anti-bot gating (the real obstacle)

Two bot-management vendors sit in front of this site, both confirmed in the capture:

- **Cloudflare** Bot Management + **Turnstile**. `challenges.cloudflare.com/turnstile/v0/api.js`
  loads, a `cdn-cgi/challenge-platform/.../turnstile/...` request fires, and the app code
  references `cf_clearance`. When Cloudflare decides to challenge, the response is a `403`
  serving the **"Just a moment…"** interstitial HTML instead of JSON.
- **DataDome**, proxied first-party as `ddm.wellfound.com` (the `tags.js` loader and `/js/`
  sensor POSTs literally contain `datadome`; the `x-angellist-dd-*` request header is its
  client hint).

In addition, the capture shows many **rotating obfuscated first-party POSTs** —
`POST wellfound.com/{~108-char base64-ish path}` with a `text/plain;charset=UTF-8` body
(~4 KB) and a binary (`application/octet-stream`) response, each paired with a same-named
`GET …js` sensor and a `HEAD 204`. These are client-side bot-detection sensor beacons
(consistent with the DataDome/Cloudflare integrations above); the path rotates to resist
blocklisting. They carry no scrapeable data and don't need to be replayed — but their absence
is part of what a fresh, scriptless client looks like to the scorer.

Note: substring scans also surfaced `_px`, `bmak`, and `_abck` tokens, but every hit was
inside CSS/JS/font/binary bytes — **not** PerimeterX/Akamai integrations. Only Cloudflare and
DataDome are real here.

### Observed challenge behaviour

Status mix for `wellfound.com` in the clean capture: `200`×608, `304`×36, `204`×8, `302`×3,
`403`×2. The two `403`s are the telling part: mid-session, with valid `x-apollo-signature`
and `x-wf-cfp` headers present, the `CompanyProfile` and `Overview` GraphQL operations were
**served the Cloudflare challenge page** while `QuickApplyModal` and `TrackView` in the same
burst returned `200`. So challenges are driven by Cloudflare's per-request bot *score*, not by
a missing header — a correct-looking request can still be challenged.

## Implications for a HeadStart scraper

- **Prefer the SSR HTML route.** `/role/l/{role}/{location}` already encodes the
  engineering-role + location filter the project wants and renders job data into the page.
  This avoids reconstructing the signed GraphQL headers entirely. Confirm the in-HTML JSON
  shape against a live fetch (the HAR didn't record document bodies).
- **The GraphQL endpoint is hostile to replay.** Persisted `operationId`s pin queries to a
  build (they break on redeploy), and `x-apollo-signature` is an HMAC computed client-side —
  you can't forge it without the signing routine from the bundle. Treat `/graphql` as a
  per-job *detail* enrichment at best, not a list source.
- **Expect Cloudflare + DataDome.** A plain HTTP client will draw `403` "Just a moment…"
  challenges. Realistic options are a real browser engine (Playwright/headful) that solves
  Turnstile and accrues `cf_clearance` + DataDome cookies, residential-grade requests, and
  conservative rate limits. Header spoofing alone is insufficient — the `403`s landed *with*
  the right headers.
- **This is a JS-rendered, actively-defended ATS**, unlike the static/JSON boards HeadStart
  currently scrapes (Greenhouse/Lever/Ashby/Zoho). Budget for a browser-driven fingerprinter
  path before committing Wellfound to the active list.

## Confirmed by a live run (`scripts/scrape/run_wellfound.py`)

A working scrape (pydoll driving real Chrome) resolved the two open questions above:

- **The automated-client block is DataDome, not Cloudflare.** A plain/`curl_cffi` request to
  the role page returns a `403` whose body is a DataDome JS challenge
  (`var dd={...'host':'geo.captcha-delivery.com'...}`), with Cloudflare layered behind it.
  pydoll's Cloudflare Turnstile auto-solver times out (no Turnstile widget present); what
  actually clears the gate is a real browser executing DataDome's challenge JS, which then
  proceeds to the app in ~6–30s on a residential IP. So drive a real browser and **wait the
  challenge out** rather than reaching for a Turnstile solver.
- **The job data is in `__NEXT_DATA__` → `props.pageProps.apolloState.data`.** The role page
  groups jobs by company: `StartupResult:{id}` entries carry `name`/`slug` plus
  `highlightedJobListings` refs into `JobListingSearchResult:{id}` entries. Each job entry has
  `title`, `slug`, `locationNames`, `remote`, `jobType`, `primaryRoleTitle`, `compensation`,
  `liveStartAt` (epoch **seconds**), `yearsExperienceMin/Max`, and `atsSource` (the *underlying*
  ATS, e.g. `AtsIntegration::Greenhouse::Listing` — a useful cross-reference for HeadStart).
  The `<script id="__NEXT_DATA__">` tag carries an extra `crossorigin="anonymous"` attribute,
  so match it with `id="__NEXT_DATA__"[^>]*>`, not a fixed attribute string.

One captured role page yields ~40 jobs (the SSR first page, already filtered to the role by
the `/role/l/{role}/...` URL — filtering at the source, per project scope). Full coverage would
need the page's load-more/pagination, which isn't wired up yet.

## Bypass tooling (candidate libraries)

Concrete tools to handle the two gates. None is a magic bullet — every source below stresses
that success depends on **IP reputation, browser fingerprint, and behaviour**, not just the
library. The tool gives you the *mechanism* (a click, a TLS profile, a solved puzzle); the
environment decides whether it's accepted. Mirror HeadStart's existing discipline: real
browser, residential-grade IP, conservative rate limits, and stop if challenged rather than
hammering.

### Cloudflare Turnstile / Bot Management

| Tool | What it does | Notes |
|---|---|---|
| [`cloudflare-bypass`](https://github.com/topics/cloudflare-turnstile) (PyPI) | Detects, clicks, and waits out the Turnstile checkbox | Requires **GUI mode** — won't work as a hidden/headless background process |
| [PyDoll](https://pydoll.tech/docs/features/advanced/behavioral-captcha-bypass/) | Async, zero-WebDriver Chromium driven over CDP; **native** Turnstile + reCAPTCHA v3 handling via realistic clicks, plus humanized input (Bessel-curve mouse paths, typing jitter, random delays), fingerprint spoofing, request interception | v1.0 shipped Feb 2026. Works headless or GUI. Doubles as the DataDome option below |
| [`turnstile_solver`](https://github.com/topics/turnstile-solver) | CDP script that finds and clicks the hidden Turnstile element | Works in both headless and GUI environments |

PyDoll is the strongest fit since it covers Turnstile *and* DataDome in one library and runs
headless — a single dependency for the whole Wellfound path.

### DataDome (the "similar" set)

DataDome is multi-layered, so the tooling splits by which layer it targets — you generally
stack them rather than pick one:

| Tool | Layer it targets | Notes |
|---|---|---|
| [PyDoll](https://pydoll.tech/docs/features/advanced/behavioral-captcha-bypass/) | JS fingerprint + behavioural | Same library as above; specialized in WAF/anti-bot bypass including DataDome. Best single starting point |
| [`curl_cffi`](https://github.com/lexiforest/curl_cffi) | **TLS / JA3 / HTTP-2 fingerprint** | The first thing DataDome flags is that plain `requests`/`httpx` have a non-browser JA3. `curl_cffi` impersonates real browsers: `requests.get(url, impersonate="chrome124")`. Use it as the HTTP layer under any non-browser path |
| [`Puzzle-Captcha-Solver`](https://github.com/vsmutok/Puzzle-Captcha-Solver) | The slider/puzzle **CAPTCHA** | OpenCV-based; locates the puzzle-piece offset for DataDome/GeeTest-style sliders |
| [`glizzykingdreko/Datadome-GeeTest-Captcha-Solver`](https://github.com/glizzykingdreko/Datadome-GeeTest-Captcha-Solver) | Puzzle CAPTCHA | OpenCV piece-position solver focused on DataDome's GeeTest variant |
| [`Zawerz/datadome-solver`](https://github.com/Zawerz/datadome-solver), [`Papuretoz/datadome-solver-python`](https://github.com/Papuretoz/datadome-solver-python) | End-to-end | General-purpose DataDome solver repos; quality varies, audit before depending on them |
| [2Captcha](https://2captcha.com/blog/datadome-bypass-python-guide-with-2captcha-service) / [CapSolver](https://www.capsolver.com/) | CAPTCHA-as-a-service | Paid API that returns a solved DataDome token/cookie to reuse; the low-effort fallback when self-hosted solvers drift |

**How they layer for Wellfound:** in this capture the *active block* was Cloudflare's
"Just a moment…" (handle with PyDoll/`cloudflare-bypass`); DataDome was running as a passive
sensor (`ddm.wellfound.com`) and would only escalate to its own device-check or slider puzzle
if the session looked automated. So the practical stack is: `curl_cffi` (or a real browser) for
the TLS/fingerprint layer → PyDoll for the JS/behavioural layer and the Turnstile click → an
OpenCV or 2Captcha solver only if DataDome escalates to a visible puzzle.

One caveat: this is web automation against a third-party site that defends with anti-bot. Keep
it to the public job-listing data HeadStart already aggregates, honour rate limits, and treat
Wellfound's ToS/robots as a real constraint on how hard you crawl.

Sources: [PyDoll docs](https://pydoll.tech/docs/features/advanced/behavioral-captcha-bypass/),
[Bright Data — Pydoll guide](https://brightdata.com/blog/web-data/web-scraping-with-pydoll),
[Scrapfly — bypass DataDome](https://scrapfly.io/blog/posts/how-to-bypass-datadome-anti-scraping),
[ZenRows — DataDome bypass](https://www.zenrows.com/blog/datadome-bypass),
[Bright Data — curl_cffi](https://brightdata.com/blog/web-data/web-scraping-with-curl-cffi),
[2Captcha — DataDome guide](https://2captcha.com/blog/datadome-bypass-python-guide-with-2captcha-service),
[GitHub `datadome-solver` topic](https://github.com/topics/datadome-solver).
