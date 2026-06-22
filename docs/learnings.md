# Learnings

Running log of non-obvious findings worth keeping. Newest first.

## Dropped freshteam / greythr / jobsoid / peoplestrong — dead weight for tech coverage (2026-06-21)

Removed these four from the active pipeline (merged CSVs + every provider list in
fingerprint/verify/merge/feeder). Big tenant counts (freshteam 2699, greythr 886, jobsoid 823,
peoplestrong 422) but those are **historical/dead/auth-walled boards, not live in-scope value**.
Web- + probe-confirmed reasons:
- **freshteam** — Freshworks is **sunsetting** it; renewals ended 2026-03-07, usable to ~2027.
- **greythr** — it's an **HR/payroll HRMS**; the `.greythr.com` tenants are login portals, not
  public careers boards. "greytHR Recruit" is a minor add-on.
- **jobsoid** — still an active product (NOT shut down), but its tenant base is **non-tech SMBs**
  (salons, agencies, small retail) and the tech cos that were on it (Cuemath, Urban Company) now
  return **0 live jobs** (migrated off). Useless for the project's tech-role scope specifically.
- **peoplestrong** — candidate portals are **login-walled** (`abfrl` -> `secureSloginRedirect.jsf`,
  `aavashrms` -> `altLogin.jsf`); can't read jobs without auth. Broad hire-to-exit HCM skewed to
  non-tech (logistics/retail/manufacturing). Kept **ripplehire** though — its `/candidate/` portal
  is token-public (no login) and its tenants are IT-heavy (LTIMindtree, Mphasis, UST, Tata Steel).

General lesson: a provider's **wayback/CC tenant count is not a measure of live, in-scope value**
— a 13-year union accumulates dead boards, sunset products, and off-scope (non-tech) tenants.
Weight providers by live + tech-relevant tenants, not raw historical count.

## Trakstar Hire is DataDome-protected — scrape the document, not the API (2026-06-21)

Trakstar boards (`{slug}.hire.trakstar.com`) sit behind **DataDome** bot protection. The HAR
shows the tells: the job-data XHRs go to **obfuscated, rotating URL paths** (random token
segments, not `/api/jobs`), and the page fires `POST` requests with a `DOMIdentifiers` body
(DataDome's client-side fingerprinting) that come back as `application/octet-stream` /
`{"result":0}`. Probing the obvious endpoints (`/api/jobs`, `/jobs.json`, …) all 404 because the
real API path is per-session and signed. So there is **no clean JSON endpoint to hit**.

The way through: **DataDome only guards the XHR layer, not the initial document GET.** A plain
`GET https://{slug}.hire.trakstar.com/` (even with urllib + a basic UA) returns the full ~85KB
careers page with the openings **server-rendered** into the HTML as
`<div class="… js-careers-page-job-list-item" data-href="/jobs/{code}/">` cards (title in the
`<h3 class="… js-job-list-opening-name" title="…">`, location in `js-job-list-opening-loc`). So
`scrapers/trakstar.py` parses that HTML (same shape as the Zoho scraper) instead of chasing the
API — no DataDome solve required. If Trakstar ever moves the listing behind the bot wall too,
this breaks and would need the full DataDome challenge solve (see `experiment/wellfound-datadome`).

Contrast with the other India-tier ATSes built the same week: **Keka** has a clean unauth embed
API (`/careers/api/embedjobs/default/active/{tenantUUID}`, urllib-friendly), and **SenseHQ** has
a clean public JSON feed (`/careers/api/jobs?page=N`). **Darwinbox** is the hard one — its job
API needs Basic-Auth + API key (request-only), so it's a render/HTML route, not a JSON call.

## Bulk-run throughput is bounded by unique-host DNS/connect, not the ATS APIs (2026-06-19)

Spent many cycles chasing why the 396-company run crawled at ~4/min despite a fast network. The
diagnosis matters:
- A concurrent test of 16 workers × 32 calls to **one** host (greenhouse API) finished in 4s,
  zero fails — so the thread pool and bandwidth are fine.
- Instrumenting a single company showed ~16-23s *serially* (heavy SPA homepage 9-11s + /careers
  + 8 slug-probes), but under full concurrency per-company time inflated ~10×.
The cause is **unique-host fan-out**: the real run touches hundreds of distinct hosts (every
company domain + 4 ATS APIs), each needing a fresh DNS lookup + TLS handshake, and a home
router's DNS forwarder chokes on many concurrent *unique* lookups. The single-host test hides
this because the connection is warm. Throughput is gated by the slowest tier (the company's own
heavy SPA homepage), not the ATS APIs.

Levers that helped: trim `careers_html` to 2 fetches (homepage + /careers) instead of 5
(homepage + scraped links + /careers + /jobs) — company homepages are the slow tier; and keep
the expensive subdomain title-probe OUT of the per-company hot path (it's ~20 fetches/company;
at full concurrency it caused self-inflicted congestion collapse). It lives in the separate
`verify_misses.py` pass instead. Net: main run ~15-20 min for 396, verify ~12 min. The per-fetch
wall-clock deadline + per-company budget guarantee completion regardless; they don't make it
fast, they make it *finish*.

## Why the fingerprinter missed catchable boards — three fixes (2026-06-19)

Ran the fingerprinter on all 396 seed companies (76 hits), then a verify pass over the 320
misses recovered **39** more — meaning the main pass was dropping boards it could catch. Web
research on each recovery showed three distinct causes, each with a fix:

1. **Budget starvation (the big one).** `run()` called the heavy `careers_html` homepage/careers
   scan *before* the fast slug-probe, sharing one 45s per-company budget. A heavy SPA homepage
   (phonepe.com) ate ~25s, so the slug-probe hit the deadline and exited early — dropping a clean
   `greenhouse/phonepe` that resolves in one fast call. Fix: **reorder** `run()` so the cheap,
   high-precision slug-probe runs first, then careers scan, then the slow subdomain probe.

2. **No retries on transient failures.** A single timed-out ATS-API call read as "no board," so
   Postman/Druva/Atlan/Freshworks (all clean greenhouse/lever/ashby/SR boards) were dropped on a
   network blip. Fix: **retry transient failures** (timeout/reset/5xx/429) in the probe path;
   never retry a 4xx (greenhouse 404 = definitively no board).

3. **No subdomain probe for the India tier.** The main pass only found darwinbox/keka/jobsoid
   boards if their link happened to be in the careers HTML it fetched. Boards reachable only via
   a deep subpage, a custom domain, or JS were invisible (Delhivery, Perfios, CarDekho, MoEngage,
   …). Fix: fold the **subdomain title-test probe** into `run()` as a last resort — fetch
   `{slug}.darwinbox.in` etc. and confirm by the company name in `<title>`.

**FP caveat surfaced by web research:** the title test trusts enterprise namesakes too easily.
`wipro.jobsoid.com` ("Wipro Ltd. Jobs Portal") and `infosys.zohorecruit.in` ("Jobs at Infosys")
match, but Wipro/Infosys are far too large for those SMB ATSes and use their own portals
(careers.wipro.com on SuccessFactors; infosys.com/careers) — these are namesakes/squats. A
job-marker content guard does NOT separate them (real darwinbox boards are SPA login pages with
zero job markers). Enterprise-name-on-SMB-ATS hits need an eyeball; the self-reference filter
(`PROVIDER_DOMAINS`) already drops the clean provider-self-match case (darwinbox→darwinbox).

## Verifying ATS hits: slug-probe beats crawling; wildcard + cross-company traps (2026-06-18)

**Careers-page crawling is fragile; slug-probing is the high-recall catch.** PhonePe and slice
both *are* on Greenhouse, but the fingerprinter missed them: PhonePe's board link sits one
subpage deep (`/careers/` → `/careers/job-openings/`, which even exposes `?department=engineering`),
and slice fronts its greenhouse board with a custom domain (`slice.careers`). Neither shows on
the careers page we scanned. Probing the clean-JSON APIs with candidate slugs derived from the
name/domain (`greenhouse/phonepe`, `greenhouse/slice`) finds both in one call, regardless of page
structure — so the fingerprinter now unions a slug-probe pass with the careers-page scan. For
*known* companies, recall+cost order is: slug-probe > careers-page fingerprint > headless render.

**Wildcard subdomains make HTTP 200 a useless liveness signal.** The India-tier subdomain ATSes
(darwinbox, keka, zohorecruit, freshteam, turbohire) answer **200 for any subdomain**, real or
fake — `zzfakenonexist.keka.com` returns the same bytes as a real tenant. So a blind subdomain
probe confirms nothing. The reliable content signal: a real tenant renders the **company name**
in the page `<title>` / body (`pinelabsgroup.turbohire.co` → "Pine Labs Group"), while a
nonexistent tenant renders the **generic provider name** ("TurboHire"). Confirm by content, never
by status code. (This also means the earlier 200-based liveness checks on darwinbox/keka hits
proved nothing — those hits were valid only because the company's *own* page named the exact
subdomain.)

**Cross-company false positives.** A rendered careers page can embed *another* company's board.
Setu (acquired by Pine Labs) surfaces `pinelabsgroup.turbohire.co`, so a naive render tags
Setu as `turbohire:pinelabsgroup` — a real board, but the parent's, not Setu's. Flag any hit
whose tenant doesn't relate to the company name/domain; it's usually a parent/partner/template
reference. GCCs and subsidiaries legitimately resolve to the parent's ATS.

**"In-house" is a real, correct outcome — not a tool failure.** Some companies run a custom or
email-only careers flow with no public ATS at all: Zerodha (no homepage careers link), Cashfree
("email us your resume"), Juspay (empty in-house portal), Open, M2P (custom static site on S3 at
`careers.m2pfintech.com`). No rendering trick fingerprints them because there's nothing to find;
the right answer is "no public ATS."

## Faster-than-Playwright headless for SPA careers pages (2026-06-18)

**Context.** The embed/SPA fingerprinter (`scripts/resolve/fingerprint.py`) catches ATS boards baked
into a company's careers HTML or its same-origin JS bundles, but misses boards a single-page
app loads at runtime via XHR (PhonePe, Juspay, slice, Open, Setu in the fintech seed). Those
need a real browser that executes JS. Question: what's faster than Playwright?

**Finding — go CDP-direct, skip the WebDriver/Playwright layer.** The fastest modern Python
headless libraries (pydoll, nodriver, zendriver) drive the *system* Chrome straight over the
Chrome DevTools Protocol (CDP) on its WebSocket debug port. They beat Playwright/Selenium not
because the browser renders faster — it's the same Chrome — but because they remove two layers
of overhead: the WebDriver intermediary process (chromedriver) and Playwright's control-plane
shim. CDP also lets you batch commands in one message, cutting round-trips. Per-page navigation
speed is comparable across all of them; the wins are startup time, install footprint, and
control-plane overhead.

**The contenders.**
- **pydoll** — async, CDP-native, ~2 MB, uses the Chrome you already have (Playwright bundles
  ~500 MB of browsers). Built-in network-event capture (`get_network_logs()`) and Cloudflare
  handling. Stable 1.0 (Feb 2026). Billed as the cleanest python-first Playwright replacement.
- **nodriver** — successor to undetected-chromedriver; async, direct CDP, fastest and
  stealthiest in anti-bot benchmarks. Best when a page sits behind a hard bot wall.
- **zendriver** — community-maintained fork of nodriver; tops several Cloudflare-bypass
  benchmarks.
- **Playwright** — still the safe default for cross-browser (Firefox/WebKit) automation;
  heavier, a known automation signature, but the most batteries-included.

**Decision for HeadStart: pydoll**, with nodriver as the fallback. Reasons: it drives the
existing Windows Chrome (no half-GB download), tiny install, async CDP with native network
capture, and handles Cloudflare. Public careers pages don't need heavy stealth; if a fintech
wall blocks pydoll, switch that one host to nodriver.

**The real lever isn't the library — it's the technique.** For "which ATS does this SPA use,"
don't render-and-reparse the DOM. Enable network capture, navigate, and grep the *request URLs*
for ATS hosts (`boards-api.greenhouse.io`, `api.lever.co`, `*.darwinbox.in`, …): you catch the
ATS the instant the SPA calls it, before the DOM settles. Then amortize browser startup by
reusing one browser across all companies, open a tab per company, **early-exit and close the
tab on the first ATS hit**, and run tabs concurrently. Library choice saves milliseconds;
network-capture + browser-reuse + early-exit save seconds per company.

**Not headless, for contrast.** TLS-impersonation HTTP clients (curl_cffi, rnet) are far faster
than any browser but don't execute JS, so they only help once you already know the runtime API
endpoint to replay — useless for *discovering* an unknown SPA's ATS. They're a step-2
optimization (replay the captured endpoint), not a discovery tool.

Sources: [pydoll](https://github.com/autoscrape-labs/pydoll),
[nodriver](https://pypi.org/project/nodriver/),
[Playwright→CDP rationale (browser-use)](https://browser-use.com/posts/playwright-to-cdp),
[anti-detect benchmark 2026](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/),
[pydoll vs playwright (ByteTunnels)](https://bytetunnels.com/posts/pydoll-vs-playwright-lightweight-python-browser-control/),
[Python headless browsers 2026 (Scrape.do)](https://scrape.do/blog/python-headless-browser/).
