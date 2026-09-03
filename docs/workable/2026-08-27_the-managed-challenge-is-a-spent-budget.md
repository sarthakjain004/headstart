# workable's 429 is a spent request budget, not a bot check (2026-08-27)

`apply.workable.com` is the largest single hole in the liveness ledger: 22,547 rows of which
21,428 sat `unknown`, all stamped 2026-08-14, contributing **190 active Boards** to a 49,803-Board
scrape. This is what its wall actually is, and which of the obvious levers move it.

## What the wall is

Not the legacy IUAM challenge, and not a plain rate limit:

```
HTTP/2 429
cf-mitigated: challenge
server-timing: chlray;desc="a319e44ca927178b"
content-type: text/html; charset=UTF-8      <- 378,615 bytes, <title>Security challenge</title>
accept-ch:   Sec-CH-UA-Bitness, Sec-CH-UA-Arch, Sec-CH-UA-Full-Version, Sec-CH-UA-Mobile, ...
critical-ch: (same list)
(no Retry-After)
```

`cf-mitigated: challenge` is Cloudflare's **Managed Challenge**. The `critical-ch` header asks for
Client Hints the checker never sends. There is no `Retry-After`, so `_on_429`'s ban-length branch
never fires on it — it reaches the challenge branch instead and trips a 1800s cooldown.

## Which clients clear it

Measured against a live wall, one candidate per run (`probe_solvers.py`, `probe_pydoll.py`). A run
trips the wall itself, confirms with a control `curl_cffi` request, then asks the candidate for the
same widget-API URL the checker uses.

| client | result |
| --- | --- |
| `curl_cffi` chrome impersonation (control) | 429, `cf-mitigated: challenge` |
| cloudscraper 1.2.71 | **429**, challenge page |
| Playwright, headful Chromium | **solves the challenge** — `cf_clearance` in ~2s — then 429 |
| pydoll via `browser_http.origin()` | **429** |

cloudscraper has no newer release to try: 1.2.71 is dated 2023-04-25 and is the latest on PyPI.
The GitHub repo has unreleased commits to 2025-06-10 including a merged `StealthMode` PR, so newer
code exists; it was not tried, because a real browser that *does* solve the challenge still gets
429 and a stealthier HTTP client cannot beat that.

## Why solving it does not help (`after_clearance.py`)

Playwright genuinely passes — the navigation carries `__cf_chl_rt_tk`, the interstitial runs, and
`cf_clearance` is issued. Then, holding that cookie:

| after clearance | result |
| --- | --- |
| reload the board page | 429, `<title>Security challenge</title>` |
| in-page `fetch()` of the widget API | 429 |
| `curl_cffi` with the same `cf_clearance` **and** the browser's UA | 429 |
| in-page `fetch()` for a *different* tenant | 429 |

Clearance is granted and immediately ignored. **Solving the challenge does not refund the request
budget**, so no client-side solver can win: cloudscraper, Playwright and pydoll are all the same IP
asking for more.

### The limit of that finding

This says nothing about a *fresh* IP, and an earlier draft of this log wrongly implied it did.
Every clearance test above ran on an address that had just taken a 400-request burst to trip the
wall, so it was deep over budget; no result here could have shown a fresh budget even if one
exists. Solving a challenge and obtaining a new budget are different things and only the second is
what egress rotation buys.

## What the shape actually says

Two independent bursts show the same thing — a run of 200s, then the wall:

- 33 successful requests at ~17.5 req/s, then 429.
- ~600–680 boards settled over ~4 minutes at ~3.2 req/s (production sweep), then 429.

and the wall decays fast: the direct route answered 200 again within about a minute of load
stopping, twice. That is a per-IP allowance being spent and refilled, not a judgement about the
client — which is what makes rotation worth measuring rather than dismissing.

### The confound in the rotation evidence so far

The 2026-08-27 sweeps looked like rotation failing and are not clean evidence of it. This WARP
install is colo-pinned to BOM and produced **four** distinct addresses across 24 rotations
(`104.28.220.169/.174/.175`, `104.28.252.174`), 9 of 14 observations returning the same IP. The
second sweep rotated onto the same four the first had already spent, so it never obtained a fresh
budget from WARP at all. Its collapse to ~0 settled/min is consistent with the budget model rather
than evidence against it.

## Measured: the budget is real, the address supply is not

A third sweep instrumented settled-boards-per-address. Minute by minute:

| minute | probed | settled that minute |
| --- | --- | --- |
| 1-2 | 211 | ~203 (direct route, unspent) |
| 3 | 320 | 77 |
| 4-9 | 592 | **0** every minute |
| 10 | 794 | **140** — moved to 104.28.220.174 |
| 11 | 946 | **127** — moved to 104.28.220.169 |

A rested address delivers ~140 boards immediately, at full rate. The six dead minutes were 21
rotations that all handed back the address already in use. Over the whole run, **53 daemon
restarts produced 3 distinct addresses** (5.7%), and lapping them faster than they refilled drove
the per-address yield down to 173, 95, 0, 63, 0.

So the budget model holds and the constraint is supply. Re-registration does not lift it: four
consecutive `warp-cli registration delete` + `new` cycles — four brand-new device identities — all
came back on `104.28.220.169` via BOM. **This corrects ADR-0063's stated reason for the
stickiness.** It is not that "a registration is sticky to its WARP edge node"; a brand-new
registration gets the same address.

> **Superseded later the same day by [ADR-0092](../adr/0092-resolve-through-warp-not-before-it.md).**
> The conclusion drawn here — that the pool is three addresses and cannot be widened from this
> machine — was **wrong**, and wrong in an instructive way. Every measurement above was taken over
> **IPv4**, because `spare_egress` handed callers `socks5://` and curl_cffi then resolved hostnames
> locally; this machine has no global IPv6, so every request took an A record into Cloudflare's
> small recycled IPv4 pool. Switching to `socks5h://` lets WARP resolve, reaches workable's AAAA
> record, and draws from the IPv6 `/32` instead: five consecutive rotations gave **5 distinct
> addresses instead of 3**, and `apply.workable.com` answered **200 over IPv6 while still
> answering 429 over IPv4 in the same second**.
>
> The colo never mattered. It was BOM for the IPv6 addresses too. What follows below about pool
> depth and "a WARP-config problem" should be read as an artifact of the resolver, not a finding.

### Consequence for the ladder

With about three addresses and a sub-minute refill, the supply is *time*, not restarts — and a
restart that returns the same address still costs ~7s of gate downtime. So a repeat now rests the
gate for `_EGRESS_REST_S` (60s, the measured refill) instead of spinning. Observed throughput was
~80 boards/min against ~140 during the minutes a rested address was carrying.

**Resting is a delay, not a breaker trip, and the first attempt got that wrong.** `_HostGate.trip`
is how a gate *gives up*: `_through_gate` reads `blocked()` and short-circuits every board behind
it to UNKNOWN without sending a request. Building the pause on `trip(60, ...)` therefore discarded
the queue rather than holding it — a sweep ended in 72 seconds with `breaker-open=19117` against 8
real 429s. `rest()` pushes the gate's next permitted start instead, which is the field `wait_turn`
already paces on, so workers sleep in the queue they were already in and no board is abandoned.

## Scripts

Under `experiment/workable-cloudflare-challenge/` (gitignored, local to whoever ran it):
`probe_solvers.py` (cloudscraper / Playwright legs), `probe_pydoll.py` (through
`browser_http.origin()`), `after_clearance.py` (the four post-clearance probes), and
`pacing_ladder.py` (a 1/2/3/4 req/s ladder on 3-minute rungs, written but stopped during its
cooldown to free the IP for a rotation measurement).

## Corrects

`scripts/validate/check_liveness_browser.py`'s header calls this "a per-IP Cloudflare 429 (~20h
retry-after)". The conclusion it draws — a browser is the same IP making more requests — holds and
is confirmed above. The mechanism does not: there is no `Retry-After` on this response at all, and
the wall decays in under a minute rather than ~20 hours.

## The scrape path never used any of this (2026-09-03)

Everything above changed the **liveness prober** — `_HostGate`, `rest()`, `wait_turn`, the pacing
ladder. `WorkableScraper` got nothing. It left `egress_fallback_on` unset, which is the default,
so a 429 on the scrape path was never a wall at all: no marking, no reroute, no rotation. The
three-attempt `http.fetch` ladder simply spent itself against the challenge and the Board was lost.

Run `33725210468` is what that costs. One shard lost **149 of its 241 workable Boards** to
`HTTP Error 429` in 126 seconds, each after 3 attempts and ~5s, at a steady ~1.2 Boards/s — the
signature of a wall that is up for the whole window rather than a burst. The other **14 shards
lost none**, which is the per-IP shape stated plainly: fifteen addresses read the same origin in
the same run and one of them was over budget.

### Re-measured, because the earlier rotation evidence was confounded

The IPv4 confound above (superseded by ADR-0092) means the 2026-08-27 sweeps could not answer
whether rotation buys a fresh budget. Re-run 2026-09-03 over `socks5h://`, against the same 149
slugs the run lost:

| probe | result |
| --- | --- |
| all 149 lost slugs, direct, at 16 then 32 then 64 concurrent (peak 65 req/s) | **200 × 149** every time — none is a dead tenant |
| ~180 requests to one slug at 48 req/s | trips the wall: 429, `server: cloudflare`, 378,156-byte HTML, no `Retry-After` |
| that slug, walled address, twice | **429**, **429** |
| that slug, WARP, same moment, twice | **200**, **200** |
| five *other* tenants, walled address | **429 × 5** |
| three of those, WARP, same moment | **200 × 3** |
| the walled slug, direct, every 15s | 429 at t+0, 429 at t+15, **200 at t+31** |

So the wall is keyed on the client IP, spans the whole origin rather than the tenant, and clears
in 15–31 seconds with no `Retry-After` to honour. Three attempts inside ~5s cannot outlast it;
a different address clears it immediately. That is the measurement ADR-0063 requires of an opt-in,
in both directions, and it is the discriminator freshteam (#311, really 502s) and personio (#312,
really departed tenants) each turned out to lack — here the departed-tenant reading is ruled out
by the first row, where every lost Board serves 200 from a rested address.

`WorkableScraper.egress_fallback_on = frozenset({429})` follows. It is the whole change: workable
reaches the network through `BaseScraper._get`, which already threads `**self._egress()`.

**What this does not claim.** The rescuing WARP address had spent no workable budget, so it was
rested by construction. A shard that drives all ~241 of its workable Boards through one spare
address can spend that too and start rotating, exactly as workday already does — the supply
caution in this log still stands, and ADR-0081's deep-pool measurement is what makes rotation
worth reaching for rather than a guarantee that it always wins.
