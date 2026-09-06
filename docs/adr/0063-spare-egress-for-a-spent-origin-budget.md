# ADR-0063: A shard that spends an origin's budget picks up a spare egress IP

**Status:** accepted · **Amended by:**
[ADR-0102](0102-a-400-walls-the-origin-too-not-just-a-429.md) (Workday now walls on 400 as well as 429, so the status behind most of its detail loss can open the spare egress) · **Amended by:**
[ADR-0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) (measured: the spare
egress yields a *different* IP, not an unspent origin budget; its pool-size figure — a 1–3 address
pool, worse diversity than the direct route — was itself corrected by
[ADR-0081](0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md), see ADR-0067's own header) ·
**Date:** 2026-08-18 · **Relates to:**
[ADR-0026](0026-parallelize-nightly-scrape.md),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md),
[ADR-0056](0056-darwinbox-browser-escalation.md) · **Amends:**
[ADR-0047](0047-pace-against-the-origin.md) · **Amended by:**
[ADR-0065](0065-wait-for-the-fresh-ip-rather-than-riding-the-spent-one.md)

> **Read ADR-0065 before applying the exit criterion below.** The "many rotations with a low
> recovery rate" test in *Amendment: the spare egress rotates* was measured, and the metric it
> reads turned out to be mis-specified: it counted attempts rather than settled requests, and
> scored every non-200 — including a dead Board's 404s — as a failure to recover. The criterion is
> restated there as *rotations failing, or rescues staying low across rotations that did produce
> fresh IPs*. On the runs that prompted the test, 2,030 of 2,030 rotations succeeded and #168
> stayed.

## Context

ADR-0047 established how this project treats an ATS that meters **per origin**: parallel Actions
shards get distinct egress IPs, so each shard holds its own budget with that ATS, and the way to
spend those budgets evenly is to cap how many of one ATS's Boards a single shard may take
(`binpack.lpt_pack_capped`). Eightfold was the ATS that motivated it, and 405 was identified there
as the shape its edge returns once a budget is spent.

That cap is **relative** — `ceil(n/m)` for `n` Boards over `m` shards. It distributes load without
bounding it. When #161 restored Workday and Personio priority scoring, the slice changed shape and
`n` for Eightfold grew from ~55 to ~79 per run. The cap dutifully passed the growth through: the
typical shard went from 3–5 Eightfold Boards to 5–6, and the budget went from comfortable to spent.

**Measured across ten runs** (2026-08-17 15:46Z → 2026-08-18 09:55Z, full analysis in
`docs/pipeline/2026-08-18_ten-run-log-review.md`):

| head | Eightfold Boards / run | per-shard spread | fatal Board errors | wall rate |
|---|---|---|---|---|
| `ab23879` (7 runs) | 50–61 | `[5,5,5,5,5,5,4,4,4,4,3,2,2,1,1]` | **0** | 0% |
| `f7415c8`+ (3 runs) | 73–79 | `[6,6,6,6,6,6,6,5,5,5,5,5,4,1,1]` | 18–30 | 25–38% |

The failure is per-shard and scales with that shard's own load, which is what identifies the
mechanism rather than merely correlating with it:

| Eightfold Boards on a shard | shards observed | fatal errors per shard |
|---|---|---|
| 6 | 23 | 2.09 |
| 5 | 15 | 1.73 |
| 4 | 2 | 0.50 |
| 1 | 5 | **0.00** |

Every shard holding a single Eightfold Board finished clean. ADR-0047's model is therefore correct;
what it lacked was any ceiling that survives the slice growing.

Two properties of the wall matter for the decision. **It is about the IP, not the tenant or the
request.** All 18 hosts that failed in run `32124170195` answer 200 from a different, low-volume
client — and the *same host* comes back 403 in one run and 405 in the next (kering 403/403/405,
libertymutual 405/405/403), so neither status can be read as a property of the tenant. **And the
Boards are unrecoverable within the run**: once a shard is walled, every Eightfold Board it has left
fails, because retrying from the same IP is the one thing that cannot work.

## Decision

**Retry is not the last rung. An ATS that walls a shard escalates to a second egress IP — Cloudflare
WARP in proxy mode — for the rest of that shard's run.**

- `http.fetch` gains an opt-in `egress_group`. A response in that group's wall statuses marks the
  group, and this and every later request naming it route through WARP. Callers that name no group
  behave byte-for-byte as before.
- **Keyed on the ATS, not the Board.** The metering is per origin across all of an ATS's tenants,
  so per-Board marking would make each remaining Board spend its own three attempts rediscovering
  what the first one already proved.
- **Marking is independent of retry budget.** A wall on the final attempt still loses that request,
  but it is exactly as informative about the origin as an early one, and recording it is what
  spares every subsequent Board.
- **Opt-in per scraper** via `BaseScraper.egress_fallback_on`. Eightfold declares `{403, 405}`.
  Every other ATS keeps its direct route unconditionally.
- **Lazy connect, eager install.** `headstart.spare_egress` dials only once something has actually walled,
  and caches the outcome — including failure — for the process. `pipeline.yml` installs and
  registers `warp-cli` before the scrape, because an on-demand `apt-get` would land inside the
  first ten minutes of the shard, which is precisely the window Eightfold's Boards occupy and the
  window the fallback exists to save.
- **It degrades, it never raises.** A missing binary, an unregistered client, a tunnel that will not
  come up: all return None and leave the scrape on its direct route — today's behaviour exactly.
  A fallback is only worth having if its absence costs nothing.
- **Proxy mode, never VPN mode.** VPN mode routes the whole runner through Cloudflare, including the
  artifact upload, the HF push and the GitHub API. Proxy mode moves only the clients pointed at the
  SOCKS5 port.

**Routing and marking are separate knobs**, because Eightfold needs exactly one of each:
`egress_group` says *route this with that group once the group is walled*, `egress_on` says *these
statuses, seen here, are what walls it*.

That split exists for the **API-availability probe**. Eightfold's first `/api/pcsx/search` page is
how the scraper asks whether a tenant exposes the API at all, and ~40% answer a steady 403 there
and a healthy 200 on the sitemap immediately after (8 of 18 hosts probed). So the probe must not
*mark* — that would dial the spare egress on nearly every shard, on the normal path. But it must
still *route*: exempting it from routing too would send it over the spent IP on exactly the walled
shard this exists to rescue, `_api_search` would return None, and every remaining Board would fall
through to the per-job sitemap path — thousands of fetches for Boards like nvidia (2,629 postings)
inside a 60-minute budget, plus the loss of `data.count`'s exact ADR-0053 truncation. Every other
surface — the careers page, the sitemap, pagination past the first page — both routes and marks.

**It reports what it did.** `spare_egress` counts requests carried and requests recovered per
group, and the shard report prints one line per walled ATS: `eightfold: walled; spare egress
carried 412 request(s), 398 recovered (97%)`. A walled group that carried *nothing* is reported
too — that is the worst case and the one a bare traffic counter would hide, since it means no
spare egress could be raised and those Boards were lost exactly as before. Recovery rate is the
number to watch: high routed with low recovery means the spare egress is walled as well, which is
the signal to stop trusting it rather than to route more.

Verified before building, per the live-API rule in `CLAUDE.md`: all 8 probed Eightfold hosts —
including the persistently API-disabled ones — serve **200 on both surfaces through a WARP egress**
(`104.28.252.175`), which falsifies the one risk `.claude/skills/ats-gap-search/resilience.md`
flags about WARP's shared ranges. Eightfold is fronted by AWS CloudFront, not Cloudflare, and does
not block the range.

**Reconciling the darwinbox measurement.** `docs/darwinbox/cloudflare-wall.md` already tried WARP
and rejected it: "as the first arm it passed 6/40 (15%)", and "it also re-routes every ATS on the
runner, not just darwinbox". Both objections survive and neither applies here. The pass rate was
measured against *darwinbox's* Cloudflare wall, which that document concludes is a client-fingerprint
problem rather than an egress one — Eightfold's is an origin budget, which is precisely the case an
IP change does address, and the 8/8 probe above measures that separately. The re-routing objection
is answered by construction: `egress_group` moves only the ATS that walled, and WARP runs in proxy
mode, so darwinbox, Workday and every other ATS on the same runner keep their direct route. This
decision therefore does **not** reopen WARP for darwinbox.

## Consequences

**A walled shard keeps its remaining Boards** instead of losing 1–3 of them, and the 18–30 fatal
Board errors per run should go to roughly zero. That also shrinks the ADR-0053 truncation set,
since `HTTP 405 on page N` mid-pagination is the same wall arriving later in the same Board.

**The politeness question is real and is answered narrowly.**
`.claude/skills/ats-gap-search/resilience.md` states that rotating
an IP to dodge a 429 is ineffective and impolite, and ADR-0026 makes per-host politeness binding.
This decision does not weaken either: it applies only to statuses an ATS returns *instead of* a
rate-limit signal, only where the same request demonstrably succeeds from another IP moments later,
and only for an ATS that has opted in by name. The per-host pacing, the detail-pass widths and the
`Retry-After` honouring in ADR-0047 all stand unchanged — this is what happens *after* they have
been respected and the origin has refused anyway. A 429 is deliberately **not** in Eightfold's
`egress_fallback_on`.

**It treats the symptom, not the cause.** The relative cap is still relative, so a further growth in
`n` still raises per-shard load; this buys a second budget rather than bounding the first. An
absolute per-shard ceiling remains the complementary fix and is not taken here — measured against
this window it would defer ~19 Boards per run, and the spare egress recovers them instead.

**A WARP that connects but cannot carry traffic is the one bad case.** The status poll before
handing out the proxy is what guards it; if it slips through anyway, that ATS's requests fail and
the run degrades to exactly the fatal errors we have today — no worse, but no better.

**Scope: the sync path only.** `fetch_async` takes a caller-supplied session, so Eightfold's async
detail pass is not routed. Both symptoms this addresses — the fatal Board error and the
mid-pagination truncation — occur on the sync path, so this is a real limit rather than a gap in
the fix; widening it means giving `fan_out_async` a proxied session.

---

## Amendment, 2026-08-18: Workday opts in on 429 — provisional

The Consequences above say a 429 deliberately does not trigger the spare egress, on the grounds
that it is the origin's polite signal and ADR-0026 makes honouring it binding. **Workday is now
opted in on 429 anyway, as a bounded experiment.** Recording the reversal rather than leaving the
paragraph above quietly false.

**What changed the argument.** The ten-run diagnosis measured Workday's metering to be per
**(source IP × instance host)**, not global: a shard's failure rate on an instance tracks *its own*
load on that instance, monotonically, on every instance tested.

| per-shard Boards on the instance | wd1 | wd5 | wd3 |
|---|---|---|---|
| 0–9 | 0.0% | 25.0% | 10.8% |
| 10–19 | 17.9% | 25.3% | 17.5% |
| 20–29 | 28.2% | 32.2% | — |
| 30–39 | 34.4% | 35.7% | — |
| 40–49 | 36.0% | — | — |

Under that model a second egress is a second *allocation* — the same logic ADR-0047 already uses
when it spreads an ATS across shards to spend one budget per IP — rather than a way of ignoring the
first. Retry and `Retry-After` are still honoured ahead of it; this is only what happens once the
ladder is spent and the Board would otherwise be lost.

**What is genuinely unlike the Eightfold case, and why this is provisional:**

- **A 429 is a signal; a 403/405 is a wall.** Eightfold tells us nothing and refuses; Workday tells
  us exactly what it wants. Moving IP in response to the second is a weaker justification than the
  first, and reasonable people would draw this line differently.
- **The blast radius is far larger.** Eightfold's wall touched 18–30 Boards per run. Workday 429s
  are pervasive, so the group will be marked walled within the first minutes of nearly every shard
  and stay there — meaning most Workday listing traffic rides a shared Cloudflare range every run.
- **It moves the fatals, not the load.** `async_fanout_enabled()` is on by default, so Workday's
  detail pass runs on `fetch_async`, which this design does not route. Only the sync listing POST
  can lose a Board (a failed detail returns None), so the spare egress catches the measured symptom
  while ~95% of request volume stays on the direct IP. That is favourable for the experiment — the
  spare egress carries little — but it means this does **not** reduce the pressure that causes the
  429s.

**Exit criterion.** Watch the shard report's `recovered` rate. A high routed count with a low
recovery rate means the spare egress is saturated too, and this comes back out.

> **This exit criterion is blind — see the 2026-08-26 Personio amendment below before applying
> it.** `note_settled` buckets *every* request the spare egress carries once the group is walled,
> so the rate is pinned high by healthy Boards that were never refused. It cannot answer whether
> the fallback bought anything; only a per-Board outcome can. Workday's opt-in is **unchanged** —
> its per-origin evidence is the table above, not this metric — but nothing has yet re-tested it
> against a measurement that could fail.

The measured root cause — a concurrency bound that is per *Board* while the budget is per *host*
(`harvest._default_workers`: peak ≈ `workers × detail_streams`, ~400 in flight, ~150 to one
instance) — is untouched by this amendment and remains the real fix.

---

## Amendment, 2026-08-18: the spare egress rotates

The original decision treats the spare egress as a single second IP: dial once, cache the outcome,
stay there. That is not enough for Workday, where the volume is large enough to spend the *second*
budget too — and a spare egress that is itself walled is indistinguishable, in the logs, from one
that is working.

**The model is now two routes, not three rungs: direct, then a rotating spare egress.** The spare
egress is a supply of IPs rather than one fallback address. A wall on the direct route moves a
group onto it; a wall seen *through* it moves it again; it keeps moving while it keeps being
refused.

Three things were taken from a sibling project that had already solved this, rather than
rediscovered:

- **`systemctl restart warp-svc`, not `warp-cli disconnect` + `connect`.** The CLI pair is a no-op
  for rotation — a registration is sticky to its WARP edge node, so disconnect/connect returns the
  *same* egress IP. Measured there on 2026-05-29: `104.28.232.96` before and after the CLI pair; a
  daemon restart moved it to `104.28.200.91`. `resilience.md` records the symptom ("rotation can be
  a no-op"); this is the working answer. `sudo -n`, so a runner without passwordless sudo fails
  fast instead of blocking on a TTY prompt nobody will answer.
- **Readiness is a real SOCKS5 handshake** (RFC 1928: send `05 01 00`, expect `05 00`), replacing
  the `warp-cli status` poll. "Connected" is a claim about the tunnel, not about the listener, and
  any stale listener on the port would otherwise be adopted and then fail every request during
  negotiation. This closes what the original decision called "the one bad case".
- **A rotation gate, and coalescing.** Two separate needs, and only taking the second is what a
  first pass got wrong. The **gate** is closed for the duration of a restart, because peers would
  otherwise keep firing at a SOCKS5 port the restart has just taken away — every one a
  `RequestsError` that burns an attempt and can lose a Board. The **generation counter** stops a
  thread that queued behind a peer's rotation from immediately bouncing the daemon again. The
  cooldown alone would coalesce most of the herd; the counter closes the window where a rotation
  outlasts the cooldown, since the cooldown stamps at the *start*.
- **A settle after the restart.** `systemctl restart` returns once the *unit* is back, not once the
  daemon is listening; re-arming immediately makes all three `warp-cli` calls land on a dead socket,
  fail silently, and the readiness wait then burn its whole deadline for nothing.
- **A failed rotation must not be permanent.** Clearing the cached proxy without clearing the
  *resolved* flag pins the process to the direct route for the rest of the run — strictly worse
  than never having rotated, and the exact opposite of "it keeps moving while it keeps being
  refused". Both are cleared, so a later caller re-dials.

**A cooldown bounds it** (`_ROTATION_COOLDOWN`, 60 s). "Keep rotating on every 429" and "restart
the daemon every few seconds" are the same instruction without one, and a shard meeting 429s
continuously would spend its 60-minute budget bouncing the tunnel rather than scraping. The floor
also buys each new IP a fair trial before it is abandoned. Throttled attempts are counted, so the
shard report distinguishes "we did not rotate" from "we were not allowed to yet".

**What to watch.** The shard report now carries both the recovery rate and
`spare egress rotations: attempted N, succeeded N, failed N, throttled N`. Many rotations with a
low recovery rate means the whole WARP range is refused, not just one IP — at which point this
mechanism is not the answer and the per-host concurrency bound (the measured root cause) is.


## Amendment, 2026-08-18: the eager install is bounded

The contract above was *installed eagerly, connected lazily, never fatal* — `continue-on-error`
plus a `proxy_url()` that returns None meant a runner without WARP kept its direct route. Run
`32157275202` showed the hole in that reasoning: `continue-on-error` covers the install *failing*,
not *hanging*. Four shards sat ~72 minutes inside `apt-get update` (the `set -x` trace ends on
that exact line; `pkg.cloudflareclient.com` itself was healthy when probed — Release and
Packages.gz in under 0.5 s — but its index is `cf-cache-status: DYNAMIC`, so fifteen
simultaneously-starting shards all reach origin), blew the 72-minute job cap, and were cancelled
with their scrape step still `skipped`. Five of fifteen shards produced nothing; the join ran on
ten; the run stayed green. The unbounded convenience step cost more scrape coverage in one run
than the fallback it installs has ever recovered.

So the contract gains a third leg: **bounded**. Every layer carries its own ceiling (curl
`--max-time`/`--retry`, apt `Acquire::http::Timeout`/`Acquire::Retries` under `timeout`, a bounded
`systemctl start`, bounded `warp-cli` calls), the install gets two attempts, `apt-get update` is
scoped to the Cloudflare source list so no unrelated mirror is on the critical path (measured in
`ubuntu:24.04` with a blackholed mirror: full update 13 s, narrowed 1 s, the package still
resolves), and the step's `timeout-minutes` is the backstop, budgeted in `pipeline.yml` against
the sum of the layers. A shard that still ends up without WARP scrapes over its direct route —
which is the degradation this ADR promised all along; it just never priced the install itself.


## Amendment, 2026-08-26: Personio's 429 opt-in is REVERTED — the premise was false

Personio was opted in on 429 earlier the same day (#312), provisionally, on an aggregate failure
count rather than a per-origin measurement, with the exit criterion *"watch the shard report's
recovered rate in later runs and revert this if it doesn't hold up."* The first two runs to carry
it were `32936269675` and `32942748996`. **It is reverted.** The premise was not merely unproven,
it was wrong: personio's 429 is not an origin budget and is not keyed on the client IP at all.

**What the 429 actually is.** A tenant that has left personio need not 404 — most do (184 of 200
sampled dead ledger rows), but a departed subdomain that is still routed does not:
`https://{host}/xml` answers **307 -> `https://personio.com`**, and personio's marketing site is
behind Vercel bot mitigation, which answers **429** with `x-vercel-mitigated: challenge`. The
scrape followed that redirect and read the challenge as the Board's own rate limit — so these
Boards never got to report the 404 that would have retired them.

Measured live 2026-08-26, three independent ways, each on its own sample:

- **Every** Board that failed terminally with `HTTP Error 429` across both runs — 22 of 22, the
  union of the two runs' failure lists — redirects to the marketing site. Against a base rate of
  **8 of 600** randomly sampled live Boards (1.33%), with **0 of 600** redirecting anywhere else
  and **0** that redirect and still serve a feed. One of the 8, `brugg-rohrsysteme-gmbh`, is itself
  a terminal-429 Board. The association is total, in both directions. (The `live` figures are the
  original survey; the dead-row figures quoted elsewhere in this amendment — 184 of 200 — come
  from an independent re-measure during review of #313, on its own sample.)
- **A different egress IP does not clear it.** The real scraper, driven against those Boards with
  this opt-in live, rotated the spare egress through **three verified-distinct addresses**
  (`104.28.220.169`, `104.28.220.175`, `104.28.252.174` — the module's own trace confirming
  `moved`) across 16 rotations, and was answered 429 by every one.
- **It is keyed on the request, not the client.** Same IP, same second, TLS fingerprint held at
  `curl_cffi impersonate="chrome"`: a Chrome `User-Agent` gets 200, and `headstart/0.1 (job-board
  reader)` gets 429 + `challenge`. A live tenant's own feed is unaffected by either (200 both
  ways), which is the control. **The held fingerprint is part of the claim, not scenery** —
  re-measured 2026-08-26 in four arms from one IP, ~10s apart: under this scraper's *own* TLS a
  Chrome `User-Agent` is still refused (429), and under Chrome's TLS our own `User-Agent` is
  still refused (429). Only the browser-shaped request as a whole gets through. Read the short
  way — "just send a Chrome `User-Agent`" — this is false, and it is not a lever anyway: what
  clears the challenge is 1.7 MB of marketing HTML.

**What the shard report said, and why it could not have caught this.** The reported rescue rate was
95.9% and 95.0% — healthy by the stated criterion — while the outcome the opt-in was adopted to fix
did not move at all: terminal 429 Board failures were 14 and 14 (in the run order named above,
as is every paired figure here), against a pre-fix baseline of
**12.1/run (n=8 runs, sd 2.7, range 8-16)**, i.e. inside noise and marginally *worse* by rate. The
tightest control, run `32932727429`, differs from the post-fix runs by exactly this one commit and
had 16.

The criterion could not fail, because the two numbers count different things. `note_settled` is
called for **every** request the spare egress carries once a group is walled, and buckets any 200
as `rescued` — but by then the group is walled for the whole shard, so the overwhelming majority of
those requests are *healthy Boards that were never refused*. The rate therefore measures "what
fraction of this ATS's Boards on this shard are healthy", which was ~95% before the opt-in too. The
residual is the tell: **unrescued requests were exactly 14 in both runs, exactly the terminal Board
count.** A rescue rate is pinned high by traffic that never needed rescuing; only a per-Board
outcome can answer whether the fallback bought anything. *Any* future opt-in judged by this metric
inherits the same blind spot — Workday's amendment above included.

**And it cost something.** `ProxyError` on personio (`curl: (97) Failed to receive SOCKS response,
proxy closed connection`) was 0 across all 8 pre-fix runs and 1 and 3 across the two post-fix ones —
all 4 ProxyErrors in those runs are personio. Total personio terminal Board failures went
12.1/run -> 15 and 17. A wall that no IP can clear turns rotation into a tight loop (`deltia-ai`
rotated 4 times in 20s), and a Board riding the tunnel when it restarts dies with it. `fetch`'s
transport-error path does not rotate or re-dial — it retries the same cached SOCKS port — so once
the spare egress is not carrying traffic, the remaining attempts are spent on a route that cannot
answer. That gap is latent for workday and eightfold (0 ProxyErrors from either across the same 10
runs) and is not addressed here; removing personio's rotation removes every instance of it observed.

**The fix is upstream of all of it.** `PersonioScraper.fetch_raw` no longer follows a redirect at
all, and reads the **target** as the signal: an off-host one is reported in the shape
`board_failures.is_gone` recognises — the way lever reports a slug that is on no Lever board —
while a same-host or relative `Location` fails the fetch *without* aging the Board, since a path
normalisation is not the origin saying the Board is gone. Nothing observed redirects on-host (0 of
600 live and 0 of 200 dead Boards sampled), so that branch is about which way the check fails when
personio changes, not about traffic today. That matters beyond the message: a 429 deliberately never
ages a Board (ADR-0058), so read as a rate limit these departed tenants stayed in the slice failing
every run indefinitely — which is why 4 of them (`pitch`, `zellerfeld`, `hishab`, `egym`) failed in
10 of 10 runs. Read as gone, the existing quarantine retires them after five agreeing runs, no
request ever reaches the marketing site, personio is never marked walled, and the shard's healthy
personio Boards keep their direct route and their full fan-out width.

**What survives for the ADR as a whole.** Nothing here weakens the Eightfold case, which was
measured per origin before it was built. It does sharpen the bar the Workday amendment set: an
opt-in needs a measurement that the wall moves with the client IP, and *`egress_fallback_on` must
not be reached for by an ATS whose 429 has not been traced to its actual origin* — the status code
is where the investigation starts, not where it ends. Both ATSes that have now failed that test
(freshteam in #311, whose 429s turned out to be 502s from a down origin; personio here) failed it
the same way: an aggregate count of a symptom was read as evidence of a mechanism.

## Amendment, 2026-09-03: Workable opts in on 429 — and clears the bar the paragraph above sets

`WorkableScraper.egress_fallback_on = frozenset({429})`. This is the first opt-in added *after*
the two reversions, so it is the first one that had to pass the test they failed, and it is
recorded here because the sentence above — that a 429 must be traced to its origin before this
attribute is reached for — is what made the measurement mandatory rather than optional.

**What it cost to not have it.** Run `33725210468` lost **149 of one shard's 241 workable Boards**
to `HTTP Error 429` in 126 seconds, each after 3 attempts and ~5s, while the other **14 shards
lost none**. With the attribute unset a 429 was not a wall in any sense: nothing marked the group,
nothing rerouted, nothing rotated, and the ladder spent itself against a challenge page.

**The measurement**, live 2026-09-03 over `socks5h://` against those same 149 slugs
(`docs/workable/2026-08-27_the-managed-challenge-is-a-spent-budget.md` has the full table):

- the same Board answers **429 on the walled address and 200 over WARP in the same second**, twice
  each way — the wall moves with the client IP, which is precisely what this ADR demands;
- **five other tenants** answer 429 from that address while **three** of them answer 200 over WARP
  — so the wall spans the origin, which is why grouping on `ats` rather than the Board is right;
- **all 149** lost Boards serve 200 from a rested address at up to 65 req/s, which rules out
  personio's departed-tenant shape directly rather than by argument;
- the response is a 429 with `server: cloudflare` and a 378 KB `cf-mitigated: challenge` page, not
  freshteam's 502-from-a-down-origin;
- the wall clears in **15–31s** and carries **no `Retry-After`**, so the ~5s ladder cannot outlast
  it and there is no polite signal being ignored — the objection the Workday amendment had to
  argue past does not arise here.

**Why this one is easier than Workday's.** That amendment called itself provisional on three
counts, and workable answers all three differently. A Managed Challenge is a wall, not a courteous
signal. The blast radius is small: of the nine full runs on 2026-09-03, this shard is the only one
in any of them that lost a Board to workable, so the group is marked rarely rather than within the
first minutes of every shard.
And workable is one request per Board with no detail pass, so the rescued traffic *is* the traffic
that can lose a Board — where Workday's fallback catches the symptom while ~95% of its volume
stays direct.

That blast-radius claim is bounded by what the logs can show, which is narrower than it sounds: a
429 a retry settles leaves no board error behind, and a shard's `429-ratelimit` retry column
aggregates every ATS at once, so it cannot separate workable's share. What is measured is Boards
*lost*, not walls *met*.

**What is not claimed.** The rescuing address had spent no workable budget, so it was rested by
construction; a shard driving all ~241 Boards through one spare address can spend that too and
start rotating, as Workday already does. Opting a third ATS in also inherits the transport-error
gap the Personio amendment names above — `fetch` neither rotates nor re-dials on a `ProxyError`,
so a Board riding the tunnel when it restarts dies with it, and that gap is still unaddressed.
It stays *latent* here for a measured reason rather than an assumed one: personio turned it into a
tight loop because its wall cleared on no address at all, where workable's clears on a rested one
in 15-31s, so rotation has somewhere to go. ADR-0081's deep-pool measurement is what makes rotation
worth reaching for, not a guarantee it always wins. And the exit criterion this ADR already calls
blind stays blind — `recovered` cannot adjudicate workable either, so a future re-test must be a
per-Board outcome: terminal 429s per 1,000 workable Boards attempted, which was 618 on the walled
shard and 0 on the other fourteen.

## Amendment, 2026-09-03: a rotation drains the tunnel before restarting it

The "Amendment, 2026-08-18: the spare egress rotates" above established that a spent IP is escaped
by restarting `warp-svc`. What it did not settle is what happens to the requests riding that tunnel
when it goes. `_severed_by_our_rotation` in `http` acknowledged the problem — it refunds an attempt
to a request our own rotation killed — but a refund only helps if a *later* attempt can succeed.

**It could not.** Two measured numbers collide (`experiment/workday-rotation-severed-pages/`):

| quantity | measured |
| --- | --- |
| `_ROTATION_COOLDOWN` | 5.0s |
| median real gap between rotations, 80,094 gaps over 758 shard-runs (the 760 below, less the 2 with under two rotations to form a gap) | **5.0s** — the floor |
| Workday CXS page through the tunnel at the walled width | p50 **6.2s**, p95 10.3s, max 12.7s |

The tunnel was restarting faster than a page could cross it. A median shard-run rotates **89 times
in 11 minutes**, and 85% of gaps are under 7s, so during a burst a page was severed, retried,
severed again, until its 5-attempt budget was gone. Each restart took the whole in-flight cohort —
exactly 12, the walled `stream_width` — so the losses arrived in blocks, and the unjittered
`1.5 * (attempt + 1)` curve sent that block back onto the wire together to be caught again.

This is what produced **roughly half of** the 105-170 `ConnectionError` listing pages a run seen
against 0-12 HTTP 429s, and with them a share of the 28-52 Boards a run entering ADR-0053 scope
exclusion. Only half: across 760 shard-runs the mean is 5.3 such pages per shard against a 2.9
baseline measured at *zero* rotations, so 45% of the mean is rotation-attributable, rising to 60%
on the shards that rotate more than 120 times. The rest is not claimed here and is not fixed by
this change — see "What this does not fix" below.

**Decision.** `rotate` now clears `_gate` (as before, so no *new* proxied request starts) and then
**waits for the requests already on the wire**, bounded by `_DRAIN_CAP`, before restarting.
`http.fetch`/`fetch_async` mark a proxied request with `spare_egress.riding_the_tunnel(proxy)` for
exactly as long as it is on the wire; a direct request is never counted, since a restart cannot
reach it. The backoff curve is additionally multiplied by `uniform(0.5, 1.5)` so a severed cohort
does not retry in lockstep — never applied to an honoured `Retry-After`.

Gate-before-drain is load-bearing: without the gate, new requests keep arriving and the in-flight
count need never reach zero.

**This partly closes a gap the Workable amendment above left open.** That entry noted that "a Board
riding the tunnel when it restarts dies with it, and that gap is still unaddressed". A rotation no
longer restarts *under* an in-flight request, which removes the cause; what remains open is the
narrower thing that amendment also named — `fetch`'s transport-error path still neither rotates nor
re-dials on a `ProxyError`, so a request that dies for some other reason still spends its remaining
attempts on a route that cannot answer.

**Measured, at production's own cadence.** The harness drives the real `rotate` — gate, drain,
cooldown and coalescing are all the production ones — and stubs only the daemon restart itself. It
lives under `experiment/workday-rotation-severed-pages/`, which like every `experiment/` directory
is **gitignored and local to whoever ran it**; the numbers below and the drain tests in
`tests/test_spare_egress.py` are the durable record.

| | pages lost | severed | served | rotations | wall | trials |
| --- | --- | --- | --- | --- | --- | --- |
| pre-fix (`--drain-cap 0`) | **48/48** | 240 | 0 | 39-40 | ~190s | 3 of 3 |
| with the drain | **0/48** | 0 | 48 | 5 | 24.8s | 5 of 5 |

Also 7x faster, because the pre-fix run spent everything on retries that were severed before they
could land, and rotations fall ~40 → 5: paced by the traffic rather than by the cooldown floor.

**One measurement trap on the way, recorded because it nearly shipped machinery.** Early harness
runs showed an intermittent mode — one trial in three or so losing 5-9 pages with 13-21 rotations —
that looked exactly like the residual race `_drain`'s docstring names (a request past `proxy_for`
but not yet joined). A version that closes that race atomically was built. It did not help: the
bad mode kept appearing with it too. The cause was the harness — its rotator thread was never
joined, so one trial's rotator kept firing into the next. With that fixed, both arms are exactly
reproducible (five identical clean trials), and the atomic version was reverted as unproven
concurrency on the critical path. The race is real in principle, unobserved in practice, and is
written down in `_drain` rather than closed.

**The cap is a cliff, and it nearly shipped mis-sized.** The first draft was 12.0s, taken from a
*direct* latency measurement; the real proxied max is 12.7s. At a cap just under the page latency
the whole mechanism is inert — 87.9% of pages still lost — while just above it, 0%. `_DRAIN_CAP` is
20.0s: over the measured max with margin, under the 30s request timeout its callers use, so a
wedged request cannot hold rotation for its full timeout.

**What this does not fix, and what it costs.** A baseline of ~2.9 `ConnectionError` pages per shard
persists at *zero* rotations (57 shard-runs), so roughly half the page losses are not rotation-caused
and are not claimed here. A rotation can now take up to `_DRAIN_CAP` longer, which is the intended
trade — 89 restarts in 11 minutes was pathological — but the scrape has a wall-clock budget, so the
signal to watch after this ships is the shard's `time budget reached` line alongside the
`ConnectionError` counts. And the drain is shard-wide, not per-ATS: one tunnel, one daemon, so
rotating for Workday now waits on Eightfold's in-flight requests too. That is correct — they would
otherwise be severed — but it is real coupling between ATSes that did not exist before.

## Amendment (2026-09-05): the drain's cost was a blocked event loop, not its cap

The drain shipped and worked — production `ConnectionError` pages fell from 105–170 per run to
3–9 — but it cost far more wall clock than the trade above priced in. Across ten runs on `f74892b`
the scrape stage ran 47.5–58.0 min against 20.0–30.2 min on the ten runs before it, with the same
~1,200–1,400 rotations per run on both sides. Same rotations, roughly double the stage: the price
was per rotation, and the obvious reading was that rotations were sitting on `_DRAIN_CAP`.

They were, and the cap was not why. `fetch_async` resolved its route through the **blocking**
`proxy_for`, whose `_gate.wait` holds for the length of a rotation. On the event loop that froze
every request already riding the tunnel: their `riding_the_tunnel` blocks could not exit, so
`_inflight` could not fall, so `_drain` could never see the tunnel empty. It burned the whole cap
and then restarted through the very cohort it existed to protect. Sampling `in_flight_count()` every
0.25s across a drain shows the tell — 79 consecutive samples reading exactly `1`, another drain
pinned at `2`, for a full 20s, on requests whose own latency tops out at 8.7s. 29 of one trial's
drains did this.

The harness missed it for a mechanical reason worth recording: it held every request at one *fixed*
latency, so the whole cohort landed together and the tunnel emptied before anything asked for a
route. Replaying it with the measured *spread* of real page latencies reproduces the failure
immediately — a slot frees mid-drain, and that is all it takes. A uniform-latency model of a
fan-out is not a conservative simplification; it is the one shape that cannot exhibit this.

`proxy_for_async` polls the gate instead, leaving the loop free, and `proxy_url` goes to a thread.
On the same harness, same cadence, staggered latencies: pages lost 18–23 of 48 → **0 of 48**,
severed connections 122–149 → **0**, wall 449–551s → **35–38s**, and the drain completes in 8.0–8.4s
against an 8.67s slowest page instead of pinning at 20.0s.

`_DRAIN_CAP` stays at 20.0. Tightening it was the obvious response to the wall-clock cost and would
have bought nothing: the drain returns when the tunnel empties, so the cap is a backstop that is now
never reached. It still needs its margin — the tunnel carries every walled group at once, so
aggregate concurrency can exceed any one group's clamped width — and a cap under the real tail is
the cliff the 12.0s draft already fell off.
