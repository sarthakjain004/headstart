# Getting past Cloudflare's wall (Wellfound)

Wellfound runs **Cloudflare Bot Management with JavaScript Detections (JSD)** *alongside*
DataDome (see `experiment/wellfound-datadome/device-check-map.md`). This is a survey of how
practitioners get past Cloudflare, and what it means for HeadStart. Companion to
`docs/wellfound/datadome-bypass.md`.

**The single most important fact first:** the thing that beats Cloudflare is a **real browser
driven CDP-direct** (no WebDriver, no Playwright shim). pydoll — what HeadStart already uses —
is exactly that. In 2026 benchmarks, nodriver (pure-CDP) cleared Cloudflare targets where
Playwright-shim tools failed, because anti-bot fingerprints *how* the browser is driven, not
just its fingerprint. So for the **browser path, Cloudflare is already handled** — it's why we
pull 573 jobs silently. The "wall" below only matters for the *pure-API* (no-browser) dream.

## What JSD actually is
- An invisible script loaded from `/cdn-cgi/challenge-platform/.../scripts/jsd/main.js`, run on
  a background thread. It collects environmental telemetry (screen, colour depth, timezone,
  WebGL renderer, AudioContext, canvas hash, automation tells).
- That telemetry is **zlib-compressed → base64 → form-encoded → POSTed to
  `/cdn-cgi/challenge-platform/h/{b,g}/jsd/oneshot`**. If the server accepts the fingerprint, it
  returns a **`cf_clearance`** cookie that grants access for hours.
- The `main.js` is **VM/control-flow-flattened obfuscation** (string-array rotation + flattening)
  — confirmed on Wellfound's copy (`cloudflare-jsd-main.js`: `!![]` idioms, `for(;!![];)`
  dispatch, numeric state-maps, string-decoder). This is the genuinely hard layer.

## Methods (high → low practicality for us)

### 1. Real / stealth browser, CDP-direct (what we do)
A real browser executes the JSD, mints `cf_clearance`, and — if driven without WebDriver/CDP
tells — isn't flagged. This is the reliable path and needs no JSD reversing.
- **nodriver** — pure-CDP successor to undetected-chromedriver; top performer in 2026 benchmarks,
  zero blocked targets, AGPL, free.
- **pydoll** — same category (async, zero-WebDriver CDP). *This is HeadStart's stack.*
- **Patchright** — Playwright fork that patches CDP leaks (`channel=chrome` for real TLS), Apache-2.0.
- **Camoufox** — fingerprint-spoofing Firefox; strong but ~200 MB/instance and ~40 s/solve.
- **SeleniumBase UC** — disconnects WebDriver before the protected page loads.

### 2. Solve once, reuse `cf_clearance`
The cookie is valid for hours and reusable across requests — solve in a browser, cache it, attach
to cheap HTTP calls. Bound to **IP + User-Agent**, so it can't move between IPs (same constraint
as the `datadome` cookie — fatal for "solve at home, run in CI").

### 3. TLS / HTTP-2 fingerprint impersonation
`curl_cffi` (impersonate Chrome) to match JA3/HTTP-2. Necessary under any non-browser path, but
**not sufficient** — it doesn't run the JSD, so it can't mint `cf_clearance` alone.

### 4. Pure-API JSD solver (the hard route)
Reproduce JSD without a browser: extract challenge params from the HTML, build the telemetry
payload (synthetic but coherent fingerprint), zlib+base64 it, POST to `jsd/oneshot`, get
`cf_clearance`. Practitioners **don't fully deobfuscate** the VM script — they regex the few
stable values out and reconstruct the payload format, accepting it breaks on Cloudflare updates.
- **`xKiian/cloudflare-jsd`** — request-based JSD solver in Go (the closest reference for this
  exact endpoint).
- **`munew/cloudflare-turnstile-solver`** (Rust, "fully reversed", request-based),
  **`scaredos/cfresearch`** (CF anti-DDoS research notes).

### 5. Turnstile widget solvers (only if the visible widget appears)
If Cloudflare shows the interactive Turnstile checkbox/"Just a moment", browser-clickers handle
it: **`Theyka/Turnstile-Solver`** (Patchright-based), **nodriver-cf-bypass** plugin,
**`Body-Alhoha/turnaround`**. (Our gate is mostly invisible JSD, not this.)

### 6. Proxy / "scraper" servers and managed APIs
- **FlareSolverr** (14k★) — headless-browser proxy. We already proved it **fails on DataDome**,
  and it's weak against modern Bot Management/Turnstile; not useful here.
- **`sarperavci/CloudflareBypassForScraping`** (CDP/DrissionPage), **`Xewdy444/CF-Clearance-Scraper`**
  (mints `cf_clearance` via browser) — current, browser-backed.
- **`cloudscraper` / `cloudflare-scrape`** — old; target the legacy JS challenge, largely
  ineffective against current Bot Management.
- **Managed** (Scrapfly, ZenRows, ScrapeOps, Bright Data) — offload both CF + DataDome; paid.

## What this means for HeadStart
- **Browser route (live):** nothing new needed. pydoll is CDP-direct (nodriver-class), so it
  already clears Cloudflare's JSD *and* DataDome on a decent (WARP, mostly) IP — that's the 573
  jobs. If pass rates dip, the upgrade path is nodriver/Patchright-class stealth + `cf_clearance`/
  `datadome` cookie reuse within a session + a cleaner (residential/mobile) IP. No JSD reversing.
- **Pure-API route (the dream):** you'd need a **JSD solver (`xKiian/cloudflare-jsd` style) on top
  of the DataDome forge** — two independently-reversed, VM-class systems to build and maintain.
  This is the real cost of "no browser," and Cloudflare's JSD is the harder of the two. For a
  personal project it isn't worth it; the browser already does both for free.
- **Always prefer the `atsSource` shortcut** (Greenhouse/Lever) where it exists — those have
  neither Cloudflare nor DataDome.

Legality note: building or using a solver for **public** data for personal/research use is generally
fine; respect robots/ToS and rate limits, and don't touch private/personal data or paywalls.

Sources: [ScrapeOps — bypass Cloudflare 2026](https://scrapeops.io/web-scraping-playbook/how-to-bypass-cloudflare/),
[Roundproxies — build a JSD solver](https://roundproxies.com/blog/jsd-solver-cloudflare/),
[Cloudflare docs — JavaScript Detections](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/javascript-detections/),
[Anti-detect browser benchmark 2026 (nodriver/patchright/curl_cffi)](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/),
[ZenRows — bypass Cloudflare](https://www.zenrows.com/blog/bypass-cloudflare).
