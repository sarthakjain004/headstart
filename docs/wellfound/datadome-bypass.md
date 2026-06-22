# Getting past DataDome — methods practitioners actually use

Wellfound's job pages are gated by DataDome (see `docs/wellfound/traffic-analysis.md`), so this
is a survey of the ways scrapers report getting through it. It's synthesized from the
r/webscraping practitioner consensus and the guides that distill those threads — Reddit blocks
automated fetches, so the methods below are the recurring agreements across those discussions
and write-ups, not quotes from specific posts.

One framing to hold onto before the list: **there is no single "DataDome bypass."** DataDome
scores every request on five layers at once — TLS fingerprint, IP reputation, HTTP/header
details, JavaScript/device fingerprint, and behavior — then feeds a per-customer ML model
(DataDome runs tens of thousands of site-specific models, so each protected site is effectively
its own challenge). The trust score is what decides whether you sail through, get a slider
CAPTCHA, or get hard-blocked. **Every reliable approach is a *stack* that answers several
layers at once.** The methods below are the building blocks; you combine them.

The single most repeated lesson: **IP reputation dominates.** It's estimated at ~25–30% of the
score, and a datacenter IP (AWS/Azure/GCP) gets an immediate negative score no matter how
perfect everything else is. This is exactly why our Wellfound scrape passed silently from a
residential connection and why it would get blocked on a GitHub-hosted runner.

---

## 1. Residential / mobile proxies (the foundation)

**What it is.** Route traffic through real home (residential) or cell-network (mobile) IPs
instead of datacenter IPs, so DataDome's IP-reputation layer scores you as a plausible human.

**How it works.** Residential IPs map to real ISP subscribers and carry a positive trust
signal; mobile IPs score highest of all, because many real users share one carrier-NAT IP, so
DataDome can't cleanly single you out. You rotate through a pool, and you align the browser's
geolocation hints (timezone, locale, `Accept-Language`) with the proxy's location so the story
is consistent.

**Tools.** Commercial pools (Bright Data, Oxylabs, Smartproxy/Decodo, IPRoyal, Roundproxies,
etc.). Mobile > residential > datacenter for trust; mobile costs the most.

**Caveat.** Necessary but **not sufficient** on its own — a good IP with a botty browser
fingerprint still gets flagged. Free/cheap proxies often have already-burned reputations and
are worse than useless. This is the layer you can't skip, not the whole solution.

---

## 2. Stealth browser automation (real browser, no automation tells)

**What it is.** Drive a real Chrome/Chromium so the JavaScript challenge actually executes and
the device fingerprint looks real — but strip the signals that reveal automation
(`navigator.webdriver`, chromedriver artifacts, CDP tells).

**How it works.** These tools talk to Chrome over the DevTools Protocol instead of a WebDriver,
which removes the most common automation fingerprints. On a decent IP, DataDome's first-visit
JS challenge then resolves on its own (it sets the `datadome` cookie and proceeds) — no visible
CAPTCHA. This is the category our working Wellfound scraper falls into.

**Tools commonly rated by practitioners:**
- **Nodriver** — successor to undetected-chromedriver; pure CDP, no WebDriver footprint.
- **undetected-chromedriver** — older Selenium-based patcher; still works on weaker setups.
- **SeleniumBase UC / CDP mode** — disconnects the WebDriver before the protected page loads
  (UC) or drives pure CDP; includes a `uc_gui_click_captcha()` helper for the slider.
- **pydoll** — async, zero-WebDriver CDP driver (what HeadStart uses for Wellfound).
- **patchright / rebrowser-patches** — patched Playwright builds that close CDP leaks.

**Caveat.** Most report a ~25% baseline pass rate *without* proxies — the browser handles the
fingerprint layer, but you still need a good IP and some behavioral realism. Headless is easier
to detect than headful; running under a real/virtual display (xvfb on Linux) passes more often.

---

## 3. Anti-detect / fingerprint-spoofing browsers

**What it is.** Browsers purpose-built to present a consistent, realistic, *rotatable* device
fingerprint (canvas, WebGL, fonts, audio, screen, WebRTC) rather than just hiding the webdriver
flag.

**How it works.** They spoof fingerprint surfaces low enough in the stack that page JavaScript
can't catch the inconsistency. Camoufox does it at the C++ level in a custom Firefox build;
commercial anti-detect browsers manage many distinct "profiles," each a coherent fake identity,
and pair with proxy rotation so each profile has its own IP.

**Tools.** **Camoufox** (open-source, Firefox-based, BrowserForge fingerprints, WebRTC
spoofing, humanized cursor — but note the original maintainer went quiet in 2025 and the
Firefox base is aging; use community forks). Commercial: **Kameleo** (pairs with Playwright/
Selenium), **Multilogin**, **Nstbrowser**, GoLogin.

**Caveat.** Strong on the fingerprint layer, but the same rules apply — needs good IPs and
human-like behavior. Commercial options cost money and add a moving part.

---

## 4. Playwright / Puppeteer + stealth plugins

**What it is.** Standard browser automation with a stealth plugin that patches the well-known
headless leaks before page scripts run.

**How it works.** `playwright-extra` / `puppeteer-extra` with the stealth plugin overrides 200+
detectable properties — `navigator.webdriver`, missing plugins, canvas/WebGL quirks, permission
mismatches — so a vanilla automated browser stops looking obviously automated.

**Tools.** `puppeteer-extra-plugin-stealth` (Node), `playwright-stealth` (Python),
`playwright-extra`.

**Caveat.** The most widely reported as *no longer enough on its own* against modern DataDome —
the stealth plugins patch JS-visible leaks but not newer CDP-level detection. Treat it as a
weaker member of the category in #2, not a current best pick.

---

## 5. TLS / HTTP fingerprint impersonation (the no-browser layer)

**What it is.** Make a plain HTTP client's TLS handshake and HTTP/2 fingerprint identical to a
real browser's, so you can fetch without paying the cost of running a browser.

**How it works.** Plain `requests`/`httpx` have a JA3/TLS signature that screams "not a
browser," which DataDome flags instantly. `curl_cffi` wraps curl-impersonate to replicate
Chrome/Safari/Edge cipher ordering, extensions, and HTTP/2 settings: `requests.get(url,
impersonate="chrome")`. You still need correct `Sec-Fetch-*` headers and header *ordering*.

**Tools.** `curl_cffi`, `curl-impersonate`, `tls-client` (Go/Python).

**Caveat.** Only works for endpoints that **don't require executing the JS challenge.** For
Wellfound's role page it was not enough by itself (we tested — straight to 403), because the
gate is a JS device-check. It's the right HTTP layer *under* a browser flow, or for APIs that
only check TLS, not the answer for a JS-challenged page.

---

## 6. Human-behavior simulation & warm-up navigation

**What it is.** Make the *session* look like a person browsing, not a bot deep-linking straight
to data.

**How it works.** DataDome added "intent-based" detection in 2025 — it weighs what the visitor
appears to be *trying to do*. So you warm up: land on the homepage, click into a category, then
the target page; add randomized delays (~100–300 ms between actions), curved multi-step mouse
movements, and natural scrolling. Machine-precise timing and straight-line cursor paths are
themselves a tell.

**Tools.** Built into the better stealth browsers (pydoll's humanized input, Camoufox's
cursor); otherwise hand-rolled with random delays and Bezier mouse paths.

**Caveat.** Helps meaningfully but never carries a scrape alone — it's a multiplier on top of a
good IP and fingerprint.

---

## 7. Solve the slider once, then reuse the `datadome` cookie

**What it is.** When DataDome does show its GeeTest-style slider/puzzle CAPTCHA, solve it once
and reuse the `datadome` cookie it issues for the rest of that cookie's life.

**How it works.** A successful challenge mints a `datadome` cookie that grants access for a
window. You can solve the slider with an OpenCV puzzle-piece solver, with a stealth browser's
built-in click helper, or by handing it to a paid solving service that returns a valid token/
cookie; then attach that cookie to cheaper follow-up requests.

**Tools.** **2Captcha**, **CapSolver** (DataDome slider endpoints); OpenCV solvers
(`Puzzle-Captcha-Solver`, `glizzykingdreko/Datadome-GeeTest-Captcha-Solver`).

**Caveat.** The cookie is **short-lived and bound to the IP (and UA) that solved it** — you
can't solve from one IP and replay from another, which is precisely why "solve at home, run in
CI" doesn't work. Solving services cost per-solve and DataDome periodically changes the puzzle.

---

## 8. Managed unblocking / scraping APIs (offload everything)

**What it is.** Send the URL to a third-party API that internally does all of the above —
proxies, fingerprints, JS execution, challenge solving — and returns the HTML.

**How it works.** One call with an "anti-bot" flag (e.g. Scrapfly `asp=True`) and the service
rotates residential IPs, manages fingerprints, runs a browser, and solves challenges, adapting
as DataDome changes so you don't have to.

**Tools.** **Scrapfly**, **ZenRows**, **ScraperAPI**, **Bright Data Web Unlocker**, Oxylabs Web
Unblocker, Zyte API.

**Caveat.** The most *reliable* and the least *work*, but it's a paid per-request dependency and
you're trusting a vendor with your targets. For a hobby/personal feed the per-request cost adds
up; for reliability-critical scale it's often the rational choice.

---

## 9. Cached / alternative sources (low-effort, low-yield)

**What it is.** Sidestep the live site by reading a cached or syndicated copy of the data.

**How it works.** Google's cache (and similar) historically served snapshots that bypass the
origin's bot wall; more usefully for jobs, the *same* postings frequently exist on the
underlying ATS (Wellfound's `atsSource` told us many are Greenhouse/Lever — which HeadStart
already scrapes with no anti-bot) or on aggregators.

**Caveat.** Google cache is deprecated/unreliable and stale, and won't render JS content. The
real win here for HeadStart is the ATS-source angle, not web caches.

---

## 10. Honeypot & hygiene avoidance

**What it is.** Don't trip the invisible traps DataDome-protected pages plant for bots.

**How it works.** Pages hide links/fields (`display:none`, `opacity:0`, off-screen offsets)
that a human never clicks but a naive crawler does — interacting with one is an instant block.
Check computed styles / bounding boxes and only interact with genuinely visible elements. Also
use HTTP/2 or HTTP/3 (not HTTP/1.1) and match real header values and order.

**Caveat.** This is table-stakes hygiene that prevents own-goals; it doesn't get you *in* by
itself.

---

## Cross-cutting truths

- **No universal bypass.** Per-customer ML means a stack that clears one DataDome site can fail
  on another. Expect to tune per target.
- **It rots.** What works this week can break next week without warning; budget for maintenance,
  not a one-time solve.
- **Datacenter IP is fatal.** The most common single reason scrapes fail. This is the hard
  constraint on running any of this from CI.
- **Layer, don't shop for a silver bullet.** The reliable recipe is consistently: *real/stealth
  browser + residential (or mobile) IP + warm-up/behavioral realism*, with a solver or a managed
  API bolted on only when the slider actually appears.

## What this means for HeadStart / Wellfound

The proven-working combination for us is already the practitioner default: **pydoll (stealth
real-browser, category #2) + a residential IP + waiting the challenge out.** To operationalize:

- **Run it from a residential IP** — your machine, a self-hosted runner on a home connection, or
  a hosted runner behind a residential/mobile proxy (method #1). Don't run it on a hosted CI
  datacenter IP.
- **Add behavioral warm-up (method #6)** if pass rates dip — land on `/jobs` before the role
  page, add small random delays.
- **Only reach for a solver (#7) or a managed API (#8)** if DataDome starts showing the slider;
  for now the silent JS challenge resolves on its own from a good IP.
- **Prefer the ATS-source shortcut (#9)** where it exists — many Wellfound listings are really
  Greenhouse/Lever boards HeadStart can read directly with zero anti-bot.

Sources: [Scrapfly — bypass DataDome](https://scrapfly.io/blog/posts/how-to-bypass-datadome-anti-scraping),
[Roundproxies — 6 working methods](https://roundproxies.com/blog/bypass-datadome/),
[ZenRows — DataDome bypass](https://www.zenrows.com/blog/datadome-bypass),
[Kameleo — guide to bypassing DataDome](https://kameleo.io/blog/guide-to-bypassing-datadome).

---

## Our open-source solver (built 2026-06-21) — findings

Because the standing rule is **always WARP, never residential**, the silent auto-pass we got
from a residential IP is no longer reliable: from WARP, DataDome frequently shows its
interactive captcha. We built a free, open-source solver (no paid CAPTCHA service) and wired it
into both Wellfound scrapers. Code: `scripts/scrape/datadome_slider.py`, called from
`run_wellfound.py` (anonymous SSR path) and `wellfound_recon.py` (jwc20 login path). Captures
and the full chronology live in `experiment/wellfound-datadome/`.

**What DataDome actually serves here.** A screenshot of the live challenge (artifact
`2026-06-21_datadome-slider+audio_warp.png`) settled the design:
- The page `dd` config has `rt:'c'` — an interactive **captcha** is offered (solvable), not a
  hard block (`rt:'b'`).
- The visual challenge is **"Slide right to secure your access"** — a simple *slide-the-handle-
  to-the-end* slider, **not** a GeeTest jigsaw puzzle. So the OpenCV piece-offset solvers
  (`vsmutok/PuzzleCaptchaSolver`, `glizzykingdreko/Datadome-GeeTest-Captcha-Solver`) are
  overkill — there is no offset to compute; you just drag to the end and the handle clamps.
- There is also an **audio tab** (accessibility option) alongside the image tab.
- The challenge explicitly names the WARP IP ("Automated (bot) activity on your network").

**How the solver reaches the challenge.** The captcha renders in an out-of-process iframe
(`geo.captcha-delivery.com`). pydoll can attach to that OOPIF *target* to read its DOM, and a
page-level CDP mouse drag reaches it even cross-origin. The solver: attach to the OOPIF →
dump its DOM (so handle selectors can be refined from real markup) → locate the slider handle
→ drag to the track end with an **ease-in/out velocity profile + positional jitter** (DataDome
validates the *trajectory*, not just the endpoint), overshooting since slide-to-end clamps.
A fixed-coordinate fallback (measured from the 1400×1000 window) is used if the handle element
isn't found.

**Audio fallback (Whisper).** If the slider drag fails twice, the solver switches to the audio
tab, downloads the challenge clip, transcribes it with **`faster-whisper`** (open-source,
offline, free — `tiny`/`base` model is enough for short digit/word clips), types the answer,
and submits. Caveat from the research above: DataDome audio cookies are *lower-trust* and often
re-challenge — worst on an already-distrusted WARP IP — so audio is a last resort, not the
primary.

**Key empirical findings:**
- **The challenge is intermittent on WARP**, not constant — DataDome scores per request. In one
  window the anonymous SSR scrape passed silently and pulled **131 jobs across 4 pages**
  (pagination + dedupe working); minutes earlier the same IP was hard-challenged. So some WARP
  runs need no solving at all.
- **WARP rotation doesn't escape it** — `warp-cli disconnect/connect` stays in the same
  `2a09:bac5::…:dd:*` range DataDome flags as VPN (IPv4 egress seen as `104.28.x`).
- **The honest ceiling is IP trust.** A correctly executed slide can still be rejected by score
  on a low-trust IP, and solving mints a cookie bound to that IP. Treat the solver as
  best-effort, not a guarantee — the first challenged run is as much a diagnostic (it dumps
  `captcha-iframe-dom.html`) as a solve.
- **Payload/cookie generators** (`combo23/datadome_generator`, `gravilk/datadome-documented`,
  `ahluwalij/Cracking-Datadome`) forge the `datadome` cookie by reverse-engineering the JS —
  more powerful but an arms race that breaks on every DataDome update; not adopted.

**Net recommendation unchanged:** prefer the `atsSource` → Greenhouse/Lever shortcut (no IP
gate at all) for companies that have it; use the solver only for the Wellfound-native companies
that exist nowhere else, accepting it's flaky from WARP.
