# Getting past the "Vercel Security Checkpoint" (Personio)

> **Correction, 2026-08-26 — this document names the wrong host, and the fix it points at is the
> wrong lever.** See
> [`2026-08-26_the-429-is-a-dead-tenant-tombstone.md`](2026-08-26_the-429-is-a-dead-tenant-tombstone.md).
>
> What survives: the identification of the challenge itself (Vercel Firewall "Challenge" action,
> not BotID) is correct and was reproduced exactly — 429, `x-vercel-mitigated: challenge`, no
> `Retry-After`.
>
> What does not: **the tenant boards are not behind it.** `{tenant}.jobs.personio.{de|com}` is
> served by personio's own Express/CloudFront stack — 44 of 44 randomly-drawn live boards answered
> 200 with a real `<workzag-jobs>` feed at 16 concurrent workers, and 592 of 600 in a wider sample.
> The Vercel challenge fronts **`www.personio.com`, the marketing site**, which the scrape reaches
> only by following a **307 from a tenant that has left personio**. The wall is a tombstone
> redirect, not a board protection.
>
> So §"the one cheap, evidence-backed thing worth actually trying" — running the personio pass
> from a non-datacenter egress — is aimed at the wrong variable, and was falsified directly:
> rotating through three verified-distinct egress addresses got 429 from every one, while the same
> IP in the same second got 200 or 429 depending only on the `User-Agent`. And there is nothing
> worth solving: a solved challenge yields 1.7 MB of marketing HTML that holds no job data.
>
> This document's own evidence pointed here already — "a fresh, unthrottled curl from a home
> network got a clean 200 on the first try against a **live** tenant" — but read it as the wall
> being intermittent rather than as the live tenant never having been walled.

Personio's `{tenant}.jobs.personio.{de|com}` boards sit behind a **Vercel Firewall Challenge**
(what its own docs call the **Attack Mode / WAF "Challenge" action**) — *not* Vercel BotID.
That distinction is the single most important fact here, because the two are built
differently and only one of them is a pure computation:

**The most important fact:** the interstitial `check_liveness.py` detects (`b"Vercel Security
Checkpoint"`, 429, no `Retry-After`) is Vercel's own docs' "Challenge" action, captioned
*"visitors see a **Vercel Security Checkpoint** screen"* in
[Firewall concepts](https://vercel.com/docs/vercel-firewall/firewall-concepts#challenge). Its
mechanism, confirmed both by Vercel's docs and by reading a (now-broken) public solver's actual
source, is **a client-side SHA-256 proof-of-work puzzle plus a browser-characteristics check** —
conceptually closer to Cloudflare's old "I'm Under Attack Mode" than to BotID's ML fingerprinting.
That means it is *not* inherently unsolvable without a browser (a PoW loop is just arithmetic) —
but Vercel has since moved the computation into an obfuscated WASM blob and changed the wire
format, and the one public non-browser implementation is confirmed dead by its own author. No
current, working, publicly documented non-browser solver exists. **BotID (the Kasada-powered
product) is a separate, opt-in, per-route SDK feature that Personio would have to deliberately
wire into their own app code** — it is not what's fronting a static XML feed, and conflating the
two would send this project down the wrong reverse-engineering target.

## What "Vercel Security Checkpoint" actually is

Vercel ships **three** distinct protection layers that are easy to conflate; only one of them
matches what we're seeing.

- **BotID** ([vercel.com/docs/botid](https://vercel.com/docs/botid)) — an "invisible CAPTCHA"
  SDK. A developer imports it and calls `checkBotId()` inside specific route handlers
  (checkout, signup, LLM APIs) they've chosen to protect. **Basic** mode (free) validates
  challenge-response integrity and light client signals (`navigator.webdriver`, headless UA
  strings, WebGL renderer, CDP-instrumentation tells). **Deep Analysis** ($1/1000 calls, Pro+) is
  "powered by [Kasada](https://www.kasada.io/)" — Vercel and Kasada announced this as a
  **strategic partnership launched June 2025** (not an acquisition — no evidence of one turned
  up), described in Kasada's own post as putting "Kasada's bot defense directly into the
  workflows of ... Vercel developers." Deep Analysis is ML-driven behavioral/environmental
  fingerprinting layered on top of Basic. Nothing about BotID fronts a public XML feed by
  default — it has to be opted into per route, in the target's own code.
- **Attack (Challenge) Mode / WAF "Challenge" action** — the platform firewall feature that
  actually shows the **"Vercel Security Checkpoint"** screen. Per
  [Firewall concepts](https://vercel.com/docs/vercel-firewall/firewall-concepts#challenge): *"A
  security challenge verifies that incoming traffic originates from a real web browser with
  JavaScript capabilities... The browser must execute JavaScript code to prove it's a real
  browser. The code computes and submits a challenge solution. The system validates browser
  characteristics to prevent automated tools from passing."* It can be triggered project-wide by
  [Attack Mode](https://vercel.com/docs/vercel-firewall/attack-mode) (manually enabled during a
  DDoS, or as a semi-permanent setting) or per-path by a
  [WAF Custom Rule](https://vercel.com/docs/vercel-firewall/vercel-waf/custom-rules) with a
  Challenge action. Docs are explicit about the intent: *"Automated tools and scripts cannot
  establish challenge sessions... Direct API calls (e.g., from scripts, cURL, or Postman) will
  fail."* A solved challenge grants a **session valid for 1 hour**, and the docs say sessions are
  "tied to the browser that completed the challenge" without spelling out what that means
  mechanically (see §4 below — this is the one place primary sources leave a real gap).
- **Deployment Protection** (password/SSO gate on preview or production deployments, with its
  own [`x-vercel-protection-bypass` secret](https://vercel.com/docs/deployment-protection/automated-agent-access))
  — a *third*, unrelated feature. It only matters here to rule it out: its bypass secret is
  generated by the **project owner** in their own dashboard; a third party scraping someone
  else's public board has no access to it and no way to obtain one. Not applicable.

Our own evidence backs the second bucket, not the first: `check_liveness.py`'s comments record
that the wall "still fires at ONE request every 3 seconds" and call it explicitly "not a request-
rate limit we can simply out-wait" — consistent with Attack Mode / a Custom Rule keyed on
IP/ASN reputation rather than a naive per-request counter. A fresh, unthrottled curl from a home
network during this research got a clean `200` on the first try against a live tenant
(`1000satellites-coworking.jobs.personio.de/xml`) — the wall is intermittent/reputation-driven,
not a permanent gate on the endpoint.

## Methods (high → low practicality for us)

### 1. Reproduce the PoW + browser-characteristics check in pure Python — the "dream" route
The mechanism itself is not fundamentally opaque: it's a deterministic proof-of-work loop, not
an ML classifier. Reading
[YZYLAB/vercel-firewall-bypass](https://github.com/YZYLAB/vercel-firewall-bypass)'s actual
source (`index.ts`, not just its README) confirms what it does end-to-end, with **no browser,
headless Chrome, Puppeteer/Playwright, or JS engine anywhere in it** — pure `fetch` calls:
1. `POST /.well-known/vercel/security/request-challenge` and read a `window._vcrct` token out
   of the error page body.
2. Base64-decode the token's 4th segment into `prefix;suffix;startingHash;iterationCount`.
3. For `iterationCount` rounds: brute-force a random string `k` such that
   `SHA-256(suffix + k)` starts with `prefix`; chain each round's hash into the next.
4. `POST` the joined keys back as `x-vercel-challenge-solution` with the original
   `x-vercel-challenge-token`; a `204` response means a valid clearance cookie was issued.

That confirms the challenge *can* be solved by pure computation, no browser required — **as of
whatever Vercel build existed in March 2024**, when this repo's only two commits landed. It has
not been touched since (`created_at`/`pushed_at` 2024-03-01, 8 stars, 2 open issues, verified via
the GitHub API). Both open issues report it broken: [issue
#1](https://github.com/YZYLAB/vercel-firewall-bypass/issues/1) ("It's not working...") has the
repo's own author, YZYLAB, commenting *"This has not been updated since their first version.
Last time I checked it was essentially the same but they converted it to WASM and you could
still just copy the `_vcrct` token and reuse it unlimited times"* — but the issue reporter
(3lang3) then shows the current computed hash **not matching** what the live site expects,
i.e. Vercel changed enough of the format (moved the PoW into an obfuscated WASM blob) that this
specific implementation no longer produces valid solutions. [Issue
#2](https://github.com/YZYLAB/vercel-firewall-bypass/issues/2) is an unanswered "any chance for
an update?" from October 2024. No comment in either thread claims a working fix.
**Verdict: the technique class is sound, but no current, publicly working, non-browser
implementation exists that we found.** Rebuilding one means reverse-engineering a WASM blob
Vercel actively varies — real, open-ended effort with no guarantee of a stable target, the same
shape of cost the sibling Cloudflare-JSD doc describes for that vendor.

### 2. TLS/JA3-JA4 impersonation alone (curl_cffi, what we already do) — necessary, not sufficient
`http.py`'s `curl_cffi` Chrome impersonation is real infrastructure Vercel's firewall *does*
look at — [Firewall concepts](https://vercel.com/docs/vercel-firewall/firewall-concepts#ja3-and-ja4)
documents JA3/JA4 fingerprinting as an input to its traffic-restriction decisions. But nothing in
Vercel's docs suggests a "browser-shaped" TLS fingerprint alone satisfies the Challenge action —
the Challenge explicitly requires "JavaScript execution" and "a challenge solution," which a TLS
handshake can't produce. This mirrors the sibling Cloudflare finding almost exactly: TLS
impersonation gets you past reputation-only gates, not a challenge that demands a computed
answer. Evidence from the Vercel community also points at **IP/ASN reputation**, not TLS, as the
recurring trigger: a [community
thread](https://community.vercel.com/t/v0-api-http-429-security-checkpoint-errors-when-calling-from-vps/17033)
describes identical requests succeeding from a home network and getting Security-Checkpoint'd
from VPS/datacenter IPs, with no resolution offered.

### 3. cloudscraper (already wired as our fallback) — confirmed no overlap, verify this repo's assumption
Cloudscraper targets Cloudflare's legacy **"I'm Under Attack Mode" (IUAM)** challenge
specifically: an obfuscated **arithmetic** puzzle served from Cloudflare's own
`/cdn-cgi/challenge-platform/` path, which cloudscraper solves either by regexing the stable
constants out of Cloudflare's JS or (per its own README) running it through a JS
engine/interpreter, then POSTing back to a Cloudflare-specific endpoint for a `cf_clearance`
cookie. Vercel's challenge lives at a completely different path
(`/.well-known/vercel/security/request-challenge`), uses a completely different token shape
(`window._vcrct`, semicolon-delimited PoW params), and a completely different puzzle (SHA-256
prefix-hunting, not arithmetic). There is no shared code path, format, or vendor between them.
**Confirmed: cloudscraper cannot and does not solve Vercel's checkpoint.** It should stay wired
in `check_liveness.py` purely as free insurance for the *other* ATSes that do sit behind
Cloudflare — for Personio specifically, every attempt through it is guaranteed to keep failing
the same challenge check, which is exactly the "cloudscraper can't clear it either" branch
`_fetch()` already falls through on.

### 4. Solve once with a real/headless browser, reuse the cookie — blocked by an unconfirmed binding + a 1-hour TTL
The pattern that worked for Cloudflare's `cf_clearance` (solve once, cache, replay from cheap
HTTP) is *available in principle* here too, but two primary-source gaps make it weaker for
Vercel:
- **Session length**: Vercel's own docs state the challenge session is *"valid for 1 hour"* —
  an order of magnitude shorter than Cloudflare's typical multi-hour `cf_clearance`. A once-a-day
  or once-a-run solve-and-cache would need re-solving on almost every pipeline run, which in
  practice means running a browser on the same cadence as the scrape — not meaningfully "no
  browser" anymore unless that refresh step is decoupled into its own rare, human-triggered task.
- **IP/UA binding — unconfirmed.** Vercel's docs say only that the session is *"tied to the
  browser that completed the challenge, ensuring secure session management"* — deliberately
  vague on the mechanism. No primary source we found states outright whether that's a
  TLS-fingerprint/IP binding (Cloudflare-`cf_clearance`-style, which would kill "solve at home,
  run in CI") or a pure signed-cookie session (which would survive an IP change). **We are
  explicitly not asserting either answer — say "unconfirmed" rather than guessing**, per this
  task's own instruction. The one secondary data point (YZYLAB's issue-#1 comment, *"you could
  still just copy the `_vcrct` token and reuse it unlimited times"*) suggests the pre-WASM token
  was portable, but that's an unverified claim about a since-changed, no-longer-working format —
  not evidence about the current cookie.

### 5. Pace/backoff tuning — already tried, already ruled out by this repo's own data
`check_liveness.py`'s own comments record that the wall "still fires at ONE request every 3
seconds," i.e. slowing down does not make it go away — this repo has already empirically
falsified "it's a throughput limiter." Combined with the VPS-vs-home-network split reported in
the Vercel community, the more likely trigger is **the requesting IP/ASN's standing reputation**
(cloud/datacenter ranges — which is what CI runners are — read as suspicious to Vercel's system
independent of request rate), not anything client-side tunable.

### 6. Managed unblocking APIs (Scrapfly, ZenRows, Bright Data, etc.)
Same category as the sibling Cloudflare doc's #6: paid services that run their own browser farms
behind the scenes and hand back clean HTML/JSON. They'd very likely clear this too (they clear
BotID and Attack-Mode Vercel challenges as part of general "anti-bot" positioning), but that's a
browser under someone else's roof, priced per request — not in scope for what this task is
after, and not proportionate for a personal project's public-data aggregation.

## What this means for HeadStart

- **No true non-browser solve exists today, and building one is a real reverse-engineering
  project against a target Vercel keeps changing** (WASM migration already broke the one public
  attempt). Given Personio is one ATS among many in the TODO list and the wall is already
  handled gracefully (challenge detected → 30-minute gate cooldown → UNKNOWN → re-probed later,
  never mis-marked DEAD), **building a custom solver is not worth it right now** — same
  conclusion the sibling Cloudflare doc reached for JSD, and for the same reason: the cost is
  open-ended, the target moves, and the current fallback already fails safe.
- **cloudscraper is correctly a no-op here and that's fine.** Leave it wired — it's free
  insurance for genuinely-Cloudflare ATSes, costs nothing extra on Personio (one wasted attempt
  per already-confirmed challenge, already gated behind the same host lock), and the code
  comments already describe exactly this degrade-gracefully behavior.
- **The one cheap, evidence-backed thing worth actually trying: check whether the trigger tracks
  the requesting IP/ASN rather than anything about the request itself.** This research's own
  unthrottled curl from a home network cleared a live tenant on the first try with zero
  fanfare, while `check_liveness.py`'s comments describe the wall firing reliably in whatever
  environment that 2026-08-14 measurement ran in. If that environment is a datacenter/CI IP
  range (GitHub Actions, cloud VPS, etc. — the same pattern the Vercel community thread
  reports), then **running the Personio liveness pass from a non-datacenter egress (residential
  proxy, or simply a workstation) may clear far more boards than any client-side trick**, at a
  fraction of the effort of a solver. Worth a controlled A/B (same tenant list, same day, two
  egress IPs) before investing further — this is a diagnostic, not a code change, and cheap to
  run once.
- **Don't touch BotID research for this problem.** It's a different Vercel product, opt-in per
  route, and irrelevant to a static XML feed unless Personio explicitly wired `checkBotId()` into
  that specific path (no evidence found that they have — the wall's own behavior, an unthemed
  429 challenge page rather than an invisible pass/fail gate, matches the Firewall Challenge
  action, not BotID).

Legality note: this only concerns Personio's already-public job listings for a personal
job-search aggregator — the same framing `docs/wellfound/cloudflare-bypass.md` already
documents. Fetching public data for personal/research use is generally fine; keep respecting
rate limits and the host's ToS, and never touch anything behind an actual login.

## Sources

- [Vercel — Firewall concepts (Challenge action, JA3/JA4)](https://vercel.com/docs/vercel-firewall/firewall-concepts)
- [Vercel — Attack Mode](https://vercel.com/docs/vercel-firewall/attack-mode)
- [Vercel — BotID](https://vercel.com/docs/botid)
- [Vercel — Automated & Agent Access (Deployment Protection bypass, confirmed inapplicable)](https://vercel.com/docs/deployment-protection/automated-agent-access)
- [Vercel — Challenge cookie-less requests on a specific path (KB)](https://vercel.com/kb/guide/challenge-cookieless-requests-on-a-specific-path)
- [Kasada — Kasada and Vercel Launch BotID (partnership announcement)](https://www.kasada.io/kasada-and-vercel-launch-botid/)
- [GitHub — YZYLAB/vercel-firewall-bypass (README + `index.ts` source)](https://github.com/YZYLAB/vercel-firewall-bypass)
- [GitHub — YZYLAB/vercel-firewall-bypass issue #1, "It's not working..." (confirms broken post-WASM)](https://github.com/YZYLAB/vercel-firewall-bypass/issues/1)
- [GitHub — YZYLAB/vercel-firewall-bypass issue #2, "Is there any chance for update?"](https://github.com/YZYLAB/vercel-firewall-bypass/issues/2)
- [GitHub API — repo metadata (created 2024-03-01, 2 commits, 8 stars)](https://api.github.com/repos/YZYLAB/vercel-firewall-bypass)
- [GitHub — vercel/community discussion #7167, "Vercel Security Checkpoint"](https://github.com/vercel/community/discussions/7167)
- [GitHub — vercel/next.js discussion #59436, "Getting 'Vercel Security Checkpoint' Page on every load"](https://github.com/vercel/next.js/discussions/59436)
- [Vercel Community — "V0 API HTTP 429 Security Checkpoint errors when calling from VPS"](https://community.vercel.com/t/v0-api-http-429-security-checkpoint-errors-when-calling-from-vps/17033)
- [nullpt.rs — Reverse Engineering Vercel's BotID](https://nullpt.rs/reversing-botid)
- [GitHub — VeNoMouS/cloudscraper (Cloudflare IUAM-specific mechanism)](https://github.com/venomous/cloudscraper)
- `scripts/validate/check_liveness.py` and `src/headstart/scrapers/personio.py` in this repo (current detection/backoff behavior, direct verification of trigger pacing)
- Direct `curl` probes against live Personio tenants (`1000satellites-coworking.jobs.personio.de/xml`) run during this research, 2026-08-14
