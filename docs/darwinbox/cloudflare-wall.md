# Darwinbox: the Cloudflare wall on datacentre egress

**Status (2026-08-14): open.** Darwinbox yields ~0–5% of its Boards on CI and has done for at
least five days. The cause is diagnosed and reproducible; the fix is not chosen. One contributing
defect — a reporting bug that hid the wall in the logs — is fixed
(`_wall_or_last` in `src/headstart/scrapers/darwinbox.py`).

## What is happening

Darwinbox's Cloudflare edge answers the careers API with a 403 error page when the request comes
from a datacentre IP. The pipeline runs on GitHub Actions, so every scrape shard is a datacentre
IP, and ~150 of the ~155 darwinbox Boards in a run's slice fail. The surviving handful is what
reaches `data/jobs/darwinbox.jsonl`.

The wall has **two independent gates**, and a request must clear both:

| Egress | TLS fingerprint | Result |
|---|---|---|
| Residential | browser (`curl_cffi impersonate="chrome"`) | 200 |
| Residential | non-browser (curl_cffi default) | 403 |
| GitHub Actions | browser | 403 |
| GitHub Actions | browser, via cloudscraper | 403 |
| Oracle box (datacentre) | non-browser curl | 403 |

The scraper already clears the TLS gate — that is why it uses `curl_cffi` (see the module
docstring). It cannot clear the IP gate from a hosted runner.

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

## The strongest lead: the wall escalates within a run

The 40-Board yield probe (`scripts/bench/probe_darwinbox_warp_yield.py`) was run twice on one
runner with the arm order flipped. Whichever arm ran **second** did dramatically worse:

| Arm order | First arm | Second arm |
|---|---|---|
| direct → warp | direct 5/40 (27 jobs) | warp 3/40 (92 jobs) |
| warp → direct | warp 6/40 (318 jobs) | direct **0/40** (0 jobs) |

Forty requests are enough to tighten the wall against the *next* forty, on a different egress IP.
So darwinbox is not merely filtering by IP reputation — it responds to request volume, and it
does so fast.

That reframes the problem. A run currently asks darwinbox for ~155 Boards from 15 shards inside a
~4-minute burst at shard start (every shard log shows ~10 darwinbox failures, all clustered
there). That burst is plausibly what keeps the wall up. Note `lpt_pack_capped` (ADR-0047, #121)
deliberately *spreads* each ATS across all 15 shards so each gets its own per-origin budget —
correct for Eightfold, which throttles per origin, but for darwinbox it may simply present 15
IPs to a range-level reputation system. This is a hypothesis, not a measured result: the
timeline above rules #121 out as the *cause*, but not as an aggravator.

**Pacing, not egress, is the next thing to test.** Concentrating darwinbox on one shard and
pacing it is cheap to try and does not need new infrastructure.

## What does not work

**cloudscraper.** It solves Cloudflare's JS/managed challenges. There is no challenge here — the
first request returns a static error page with no `cf-mitigated` header, i.e. a firewall block.
Verified 0/6 from GitHub Actions (run `31823159547`) against 6/6 from residential. Adding it
would change nothing on CI.

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

## Open decision

1. **Pace and concentrate** — exempt darwinbox from the ATS-spreading cap, put it on one shard,
   pace it. Cheapest, tests the strongest hypothesis, no new infrastructure.
2. **Residential proxy** — the only option that certainly clears the IP gate. Needs a paid
   provider and a per-ATS proxy hook.
3. **Park it** — `config.PARKED_BOARDS` until one of the above lands. Darwinbox currently spends
   ~155 Boards of scrape budget and its share of the run's retries every two hours for near-zero
   yield, and its truncated scrapes feed the ADR-0046 collapse guard. Note the cost: a parked
   Board leaves `index_plan.live_keep_set`, so the darwinbox rows still in the index are evicted
   as off-Board. Against a Board yielding ~0–5% that is a small loss, but it is not free, and
   `PARKED_BOARDS` is keyed on the lowercased `board_key`, not on `ats:slug`.
