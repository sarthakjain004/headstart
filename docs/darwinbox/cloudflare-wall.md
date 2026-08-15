# Darwinbox: the Cloudflare wall on non-browser clients

**Status (2026-08-15): the fix is measured and ready to build.** Darwinbox yields ~0–5% of its
Boards on CI and has done for over five days. A real Chrome driven over CDP (pydoll) reads the
Boards fine from the *same* GitHub Actions IP that gets 403s from `curl_cffi`, at 153/155 Boards
and 0.38 s/Board — so the fix is a browser-driven scrape, and the volume test that was the last
open question has passed. One contributing defect — a reporting bug that hid
the wall in the logs — is already fixed (`_wall_or_last` in `src/headstart/scrapers/darwinbox.py`).

> **Two corrections (2026-08-15), both kept visible rather than quietly deleted.**
>
> 1. An earlier version concluded the wall had an unbeatable datacentre-IP gate. Wrong, and
>    instructively so: every arm that failed happened also to be a non-browser client, so
>    "datacentre IP" and "not a real browser" were perfectly confounded. Only a same-runner
>    control separated them.
> 2. It then called volume escalation "the strongest lead", from four near-zero samples that were
>    all simply blocked. Measuring 155 Boards bucketed by position shows no escalation at all.
>
> Both errors share a shape: a pattern inferred from failures that had a simpler common cause.
> The fix in each case was a control that varied one thing.

## What is happening

Darwinbox's Cloudflare edge answers the careers API with a 403 error page when the request does
not look like a real browser — and from a datacentre IP it holds that line strictly enough that
`curl_cffi`'s Chrome impersonation, which suffices from a residential connection, no longer
passes. The pipeline runs on GitHub Actions, so ~150 of the ~155 darwinbox Boards in a run's slice
fail. The surviving handful is what reaches `data/jobs/darwinbox.jsonl`.

**What actually discriminates is the client, not the egress.** The decisive measurement is a
same-runner control (run `31828850489`, job `94859502521`, egress `20.163.39.232`): curl_cffi and
cloudscraper took 403 on all six Boards, and minutes later, *on that same IP and the same
Cloudflare colo*, a real Chrome read all six.

| Egress | Client | Result |
|---|---|---|
| GitHub Actions `20.163.39.232` | `curl_cffi impersonate="chrome"` | 403 |
| GitHub Actions `20.163.39.232` | cloudscraper | 403 |
| GitHub Actions `20.163.39.232` | **real Chrome (pydoll)** | **200, all 6 Boards** |
| Residential | `curl_cffi impersonate="chrome"` | 200 |
| Residential | curl_cffi default TLS | 403 |
| Oracle box (datacentre) | plain curl | 403 |

Across two runs: 12/12 browser Board reads passed; 24/24 curl_cffi/cloudscraper attempts on the
same Boards took 403. So a datacentre IP is an *input* to Cloudflare's score, not a veto — what
curl_cffi can no longer satisfy at darwinbox's current setting is the full client fingerprint
(genuine TLS/HTTP2, client hints, a JS runtime). No challenge is ever presented: the document
returns 200 to a browser on the first request, which is also why cloudscraper is useless here —
there is nothing for it to solve.

## This is not a recent regression — a correction

An earlier pass over the last 12 pipeline runs read this as a sharp regression dated to
2026-08-13 20:02 UTC, because the sampling window happened to *begin* at a three-run spike. It
did not. Widening to five days shows darwinbox has been at 0–181 lines per run since at least
2026-08-09; the spike is the anomaly.

| Run start (UTC) | `darwinbox.jsonl` lines |
|---|---|
| 2026-08-09 11:52 | 0 |
| 2026-08-09 17:55 | 181 |
| 2026-08-10 06:49 | 12 |
| 2026-08-10 12:06 | 0 |
| 2026-08-10 20:06 | 15 |
| 2026-08-11 06:12 | 0 |
| 2026-08-12 00:05 | 13 |
| 2026-08-12 16:23 | 96 |
| 2026-08-13 00:05 | 169 |
| *#121 merged 2026-08-13 06:15* | |
| 2026-08-13 10:34 | 12 |
| 2026-08-13 12:05 | **1399** |
| 2026-08-13 16:15 | **1325** |
| 2026-08-13 18:14 | **2540** |
| 2026-08-13 20:02 | 12 |
| 2026-08-14 00:05 | 12 |
| 2026-08-14 12:04 | 0 |

Two things follow. The wall predates every commit in the 2026-08-13 window, so no code change
caused it. And any future measurement of a darwinbox fix must be read against a **0–181 baseline
with occasional spikes**, not against 2540 — a single good run proves nothing here.

## The wall does not escalate with volume — a second correction

An earlier version of this document called escalation "the strongest lead", from a 40-Board probe
run twice with the arm order flipped, where whichever arm ran **second** scored worse:

| Arm order | First arm | Second arm |
|---|---|---|
| direct → warp | direct 5/40 | warp 3/40 |
| warp → direct | warp 6/40 | direct **0/40** |

**That reading was wrong.** All four numbers are near zero — that is a walled client producing
noise, not an escalation curve. A pattern was read into four samples that were all simply
"blocked". Measuring it properly settles it: 155 real Boards, bucketed by dispatch position, over
four independent runs on four different egress IPs.

| Bucket | 1-25 | 26-50 | 51-100 | 101-155 |
|---|---|---|---|---|
| Pass rate | 100% | 96% | 98% | **100%** |

The final bucket is 100% in *every* run. The two failures are the **same two Boards every time**,
at the same dispatch positions: `tokopedia` answering 500 (the wrong TLD is recorded in the
ledger — a data bug, not a wall) and one genuine 403 on `academy`. That is **one real block in
155**, reproducible and position-independent — a property of that tenant, not of request volume.

So neither pacing nor egress is the lever. The client is.

## What does not work

**cloudscraper.** It solves Cloudflare's JS/managed challenges. No challenge is presented here, so
it has nothing to solve. Verified 0/6 from GitHub Actions (run `31823159547`) against 6/6 from
residential. Adding it would change nothing on CI.

**Cloudflare WARP.** Better than direct but nowhere near a fix: as the first arm it passed 6/40
(15%). WARP egress is a shared Cloudflare range, which the repo's own resilience notes already
warn is blocked outright by some origins
(`.claude/skills/ats-gap-search/resilience.md`). It also re-routes every ATS on the runner, not
just darwinbox.

## The reporting bug that hid this (fixed)

`fetch_raw` tries `.in` then `.com` — only one hosts a given tenant, and the other answers
500 "Invalid subdomain". It kept the **last** error, so a 403 from the tenant's real host was
overwritten by the other TLD's expected 500. Walled Boards therefore logged as
`HTTPError: HTTP Error 500`, which reads as a dead tenant. That is why a fleet-wide 403 wall
appeared in the logs as scattered 500s and went unexamined for days.

`_wall_or_last` now prefers the 403. One subtlety worth remembering: the HTTP status comes off
`exc.response.status_code`, **not** `exc.code` — `curl_cffi` raises `HTTPError(msg, 0, response)`
where that `0` is a curl errno. A first version of the fix keyed on `exc.code` and was a silent
no-op that still passed its test, because the test stubbed `http.fetch` to raise `urllib`'s
`HTTPError`, which *does* carry the status on `.code`. The regression test now returns a real
`curl_cffi` response and lets `raise_for_status()` build the exception, so it exercises the real
type.

## Reproducing

Both probes live in `scripts/bench/` and are safe to run from a laptop (they will pass there —
that is the point):

- `probe_darwinbox_wall.py` — one Board per request shape (scraper / Chrome UA / plain /
  cloudscraper / WARP), prints status, `cf-*` headers and body. Shows *which gate* rejected you.
- `probe_darwinbox_warp_yield.py` — 40 Boards that failed in a real run, through direct and WARP
  egress, reporting per-arm success rate. Set `PROBE_PROXY` to include the WARP arm.

To reproduce the wall you need a datacentre IP: push them to a branch running the temporary
`probe-darwinbox-wall` workflow, which installs WARP in proxy mode and runs both.

## The fix, and what is still unproven

**Drive a real browser.** `scripts/bench/probe_darwinbox_pydoll.py` navigates the careers page,
clicks the "Open Jobs" CTA, and reads the Board. Two details matter for a production scraper:

- `/ms/candidate/careers` redirects to `/careers/home`, which fires only `getLandingPage` and
  `companyinfo` — **`alljobs` is never requested**. A fetch-only probe would answer a different
  question; the click through to `/careers/allJobs` is what triggers it.
- The SPA's own XHR returns only the first 10 for a large Board. A page-context `fetch` carrying
  our own `limit=100` body returned the full set and also passed, so the shape is navigate once,
  then paginate via in-page fetch on the warmed tab.

**Recommended production shape**, measured: one **headful** Chrome under `xvfb-run` per shard,
reused across Boards; a fresh tab per Board with heavy subresource blocking (media *and* all JS,
Turnstile included); navigate, then one page-context `fetch` of `alljobs`; concurrency width 4-6.
That is **0.38 s/Board** at 155 Boards, and 18-21 s for a 10-Board shard including browser
startup, against a 60-minute budget. `ubuntu-latest` already ships Chrome.

A cross-origin variant that never navigates at all is equally fast (0.39 s/Board), but it depends
on darwinbox serving permissive CORS, which they can revoke without warning for a 0.01 s/Board
saving. And revoke it they did: on the 2026-08-15 probe run that variant regressed from 6/6 to
3/6, every `.com` board answering `HTTP 403 THE WALL`, while the navigate-first shape held. Keep
it out of production entirely.

**Shipped** as ADR-0056: `headstart/browser_http.py` (the transport) + the wall route in
`DarwinboxScraper.fetch_raw` — curl first, browser on the wall, `parse` untouched.

**Now measured at production scale.** 155 Boards, four runs, no escalation (above); ~10 Boards on
one runner — the real per-shard shape — passed 10/10 twice in 18-21 s including Chrome startup.
Concurrency holds to width 6 with no pass-rate loss.

Two findings change the implementation:

* **Headless is a flat block, not a raised challenge rate.** One leg fell back to headless and
  took 403 on all six Boards while headful on a sibling runner read all six. ADR-0037's note
  understates this: production must be headful under `xvfb-run`.
* **The click is an Angular-router requirement, not a Cloudflare one.** It genuinely fires the
  XHR, but dropping it costs nothing against the wall — and blocking every subresource including
  Cloudflare's own Turnstile `api.js` also costs nothing, which is what takes a Board from 20.6 s
  to **0.38 s**. Cloudflare is not requiring the page's JS to run.

Two operational caveats: Chrome startup under xvfb is flaky (2 of 9 probe legs died reporting
nothing, which reads exactly like a wall result — production needs a retry), and the probe fetches
only page 1, so a real scrape must paginate.

## Still-open alternatives, if the volume test fails

1. **Pace and concentrate** — exempt darwinbox from the ATS-spreading cap, put it on one shard,
   pace it. Cheapest, no new infrastructure.
2. **Residential proxy** — needs a paid provider and a per-ATS proxy hook. Now looks like
   over-engineering unless volume proves the browser path cannot hold.
3. **Park it** — `config.PARKED_BOARDS` until one of the above lands. Darwinbox currently spends
   ~155 Boards of scrape budget and its share of the run's retries every two hours for near-zero
   yield, and its truncated scrapes feed the ADR-0046 collapse guard. Note the cost: a parked
   Board leaves `index_plan.live_keep_set`, so the darwinbox rows still in the index are evicted
   as off-Board. Against a Board yielding ~0–5% that is a small loss, but it is not free, and
   `PARKED_BOARDS` is keyed on the lowercased `board_key`, not on `ats:slug`.
