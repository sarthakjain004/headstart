# ADR-0056: Escalate walled darwinbox boards to a real browser

**Status:** accepted · **Date:** 2026-08-15 · **Amends:**
[ADR-0026](0026-parallelize-nightly-scrape.md) / [ADR-0047](0047-pace-against-the-origin.md) —
the fan-out and the ATS spread stand; what changes is that a shard can now open a browser

## Context

Since ~2026-08-09, Cloudflare in front of `{tenant}.darwinbox.{in,com}` 403s **every non-browser
client**: plain urllib, `curl_cffi impersonate="chrome"`, cloudscraper — any TLS fingerprint, from
any IP, datacentre or residential. The investigation (`docs/darwinbox/cloudflare-wall.md`,
PR #137) established the wall's model the hard way, publishing and then correcting two wrong
mechanisms; what survived measurement is:

- **The gate is the client, not the IP.** A real Chrome on the *same* GitHub runner that gets
  403s reads the same boards 200 (runs 31828454486, 31828850489).
- **It does not escalate with volume.** A 155-board slice from one runner passed with no
  degradation by dispatch position; the apparent escalation was four samples that were all simply
  blocked.
- **Headless is a flat block**, not a raised challenge rate. Production must be headful.
- The shape probes priced the read: **navigate the tenant's careers page once** (clearing the
  wall for that origin), then call the same `alljobs` JSON API via an **in-page fetch on the
  warmed tab** — no click, no rendering, heavy subresource blocking (media + all JS, Turnstile
  included), width 4, ~a few seconds per board against 20.6s for the naive click-through shape.
  Arm C (fetch from a browser parked on a foreign origin) worked briefly and then regressed to
  403 — it depended on permissive CORS darwinbox has since revoked — which confirms the
  navigation is load-bearing.

Meanwhile 446 live darwinbox boards (CarDekho, Licious, Upstox, smallcase, …) have been
unscrapeable for a week: every run reports the wall (#137 made that report honest) and banks
nothing.

## Decision

**Escalate to a real browser per walled board, inside the existing scraper.** Three parts:

1. **`headstart/browser_http.py`** — the browser twin of `http.py`, a deep module whose whole
   interface is `origin(page_url)` yielding `post_json`/`get_json`. Behind it: one headful Chrome
   per process, lazily started with a launch retry (probe legs died at startup 2/9 under xvfb), a
   dedicated asyncio loop thread so harvest's worker threads call it synchronously, a
   4-tab semaphore, heavy subresource blocking, a 20s navigation deadline, and a
   never-retry-HTTP-answers rule (one stated retry exists for pydoll's own evaluate-shape hiccup).
   Clearance is per-origin — every tenant is its own subdomain — so each board pays exactly one
   navigation.

2. **Routing in `DarwinboxScraper.fetch_raw`: curl first, browser on the wall.** The TLD probe
   already classifies a 403 as the wall on the tenant's *real* host (the wrong TLD answers 500
   "Invalid subdomain", never 403 — #137). On a wall it now routes to `_fetch_raw_browser`, which
   returns the same JSON the curl path would have; `parse` is untouched, job URLs are untouched,
   and the wall-vs-dead reporting distinction #137 fixed is preserved by construction, because a
   wall now *scrapes* instead of raising. If Cloudflare drops the wall, the curl path silently
   resumes costing nothing.

3. **Deployment: every shard, lazily.** ADR-0047's ATS spread stands — darwinbox's ~30 boards per
   run land across all ≤15 shards, so each shard's scrape step runs under `xvfb-run` and installs
   the `[scrape]` extra (pydoll). Chrome starts only when a darwinbox board actually hits the
   wall; shards without walled boards never open a browser.

## Alternatives considered

- **Pin darwinbox to one browser-equipped lane** (a `scrape_plan` pin or a dedicated pipeline
  job). Contains Chrome to one runner and one startup — but needs new planner or workflow
  machinery, concentrates ~446 boards' browser time on one runner, and reverses ADR-0047 for one
  ATS. The spread costs almost nothing (Chrome and xvfb ship on `ubuntu-latest`; startup is lazy),
  so the machinery buys nothing measured. Rejected.
- **Proxy/egress changes (WARP, residential proxies).** The IP was proven not to be the gate;
  routing around it attacks the wrong variable. Rejected by measurement (PR #137's corrections).
- **Wait the wall out.** It has held for a week and darwinbox sells to enterprises that want
  scraping stopped; there is no signal it is temporary. Rejected.

## What this costs, stated plainly

- **A walled Board pays the curl 403 cycle before the browser.** Curl-first means each escalation
  first burns `http.py`'s retry/backoff on the 403 (it is still in the retry set) on both TLDs.
  That is the price of keeping curl primary so the wall dropping needs no code change; it is not
  free, and it is why the per-Board cost is more than the probe's 0.38 s read.
- **Subresource blocking is best-effort.** It uses pydoll's private command API, so it is wrapped
  and degrades rather than failing the Board — but the wall doc measured an *unblocked*
  navigation at 20.6 s, above the 20 s deadline, so silent degradation would look like a fleet of
  timeouts. It therefore logs a warning, once per process, naming the exception.
- **Only the first page escalates.** A 403 arriving mid-pagination raises, so the Board reports a
  truncated read rather than re-reading half of it through a second transport.
- **`python -m headstart` (the curated feed) has no pydoll.** It installs base deps only, so a
  walled Board there raises `BrowserUnavailable` naming the `[scrape]` extra, rather than a
  misleading "Chrome failed to start".

## Consequences

- Walled darwinbox boards scrape again, at browser cost only while the wall holds. The cost
  ledger (ADR-0027) measures the browser seconds per board automatically, so bin-packing and the
  makespan prediction (ADR-0054) absorb the new price after one run; the first run's
  underestimate is bounded by the shards' existing time-budget banking.
- The shard image dependency grows by pydoll (~2 MB) and an `xvfb-run` wrapper. Both are inert
  for every other ATS.
- Chrome startup flakiness is now a production concern; the launch retry covers it, and a board
  whose escalation still fails surfaces the browser's error rather than a misleading TLD error.
- `tests/test_browser_http.py` exercises the module through a fake Chrome behind the
  `_chrome_factory` seam; the darwinbox routing tests stub `browser_http.origin`, and the
  wall-detection stubs use real `curl_cffi` Responses so the 403 predicate is tested against the
  exception production actually raises (the no-op-fix class the #137 review caught).
