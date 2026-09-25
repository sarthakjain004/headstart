# Why spare-egress recovery collapsed after #172 — rotation was on, and starved

**Date:** 2026-08-19 · **Runs read:** `32146017194` (pre-#172 baseline), `32178532129`,
`32189304871`, `32198367156` (all three completed runs on `465cd74`) · **Method:** shard-report
lines from the Actions log zips; 58 shard-scrapes total.

**Verdict: saturation, not range refusal. ADR-0063's exit criterion is not met — #168 stays.**
The recovery rate fell because the *denominator* grew ~21× while the numerator stayed pinned to
the work, and because rotation is supply-capped by our own cooldown at ~0.4% of demand.

## 1. Rotation was never failing — it was being denied

| run | rotation demand (attempted+throttled) | granted | **failed** | granted share |
|---|---|---|---|---|
| `32178532129` | 153,800 | 654 | **0** | 0.43% |
| `32189304871` | 163,543 | 677 | **0** | 0.41% |
| `32198367156` | 161,059 | 699 | **0** | 0.43% |

Every rotation that was *permitted* produced a fresh working SOCKS5 listener — 2,030 of them
across three runs, zero failures. The other 99.6% were refused by `_ROTATION_COOLDOWN = 20.0`
(`src/headstart/spare_egress.py`), which books them as `throttled`.

Rotation ran flat-out against that floor for the whole scrape window, not in bursts:

| shard (`32198367156`) | rotations | window | median gap | gaps < 25 s | ceiling (dur/20 s) |
|---|---|---|---|---|---|
| 2 | 109 | 42.9 min | **20.3 s** | 106/108 (98%) | 131 |
| 13 | 42 | 13.8 min | **20.1 s** | 41/41 (100%) | 44 |
| 6 | 26 | 10.6 min | **20.8 s** | 20/25 (80%) | 50 |

Fleet-wide the run granted 699 rotations against a structural ceiling of 929 — 75% of the
absolute maximum the cooldown physically allows. **The binding constraint is the cooldown, not
WARP.** Each fresh IP absorbs ~230 walls (161,059 / 699) before another is allowed; in between,
every walled request rides the same burned IP and books as routed-not-recovered.

## 2. What #172 actually changed: the request *rate* through one tunnel

| | pre-#172 (`32146017194`) | post-#172 (three runs) |
|---|---|---|
| proxied req/s per shard (median) | **1.4** | **51.7 / 55.7 / 64.0** |
| routed fleet-wide | 42,908 | 899k–945k |
| rotations attempted | **0** | 654–699 |
| workday recovery | 98% | 69 / 69 / 72% |

Pre-#172 a single WARP IP carried 1.4 req/s and was *never metered* — zero rotations were ever
attempted, which is why the rotation line does not appear in that run's logs at all. Post-#172 the
same single IP faces ~40× the rate. One IP per 20 s cannot supply that.

## 3. The misses are real 429s, not an accounting artifact

`note_routed` counts per *attempt*, not per request, so an inflated denominator was the first
thing to rule out. Non-recovered / wall-retries = **1.54**, against the 1.5 expected from
3-attempt requests that wall on all three. The ~2.7% excess is the only room for non-transient
statuses (404s etc.). The denominator is honest.

## 4. The decisive evidence against range refusal

Across all 43 post-#172 shards that raised an egress:

| quantity | mean | **CV** |
|---|---|---|
| **recovered** | 43,504 | **0.09** |
| jobs scraped | 95,735 | 0.07 |
| routed | 64,203 | 0.13 |
| shard duration | 1,322 s | 0.47 |

`recovered` is as tight as *jobs scraped*, and holds steady at **~0.45 successful detail fetches
per job** while duration varies by half. Recovery tracks the **work**, not the tunnel's capacity.
Under a refused range `recovered` collapses toward zero; instead it is the most stable number in
the whole report. The rate fell purely because `routed` grew around a pinned numerator.

## 5. The one genuine refusal — and it is a single board

`eightfold:nttdata.eightfold.ai` returned **0 jobs in 1,131 s** (`32189304871`) and **0 jobs in
913 s** (`32178532129`), burning 16,304 and 20,940 requests with `16304/16304 detail fields
missing`. That one board *is* the eightfold outlier — the 1%-recovery and 7%-recovery shards. For
that tenant every fresh IP is refused, which is real refusal scoped to one board, not to the WARP
range. Run `32198367156` has no such board and eightfold fleet recovery is correspondingly better
(49% vs 33–36%).

Eightfold's 403/405 is a hard edge wall; Workday's 429 is a per-IP meter a fresh IP genuinely
resets. **The two ATSes should not be judged against one recovery threshold.**

## 6. Side finding — one shard per run runs with no egress at all

Shard 7 in `32178532129` and `32189304871`: `apt-get install cloudflare-warp` failed both
attempts, `warp-cli` absent, `spare egress: unavailable after 0.0s — every walled Board this run
stays on the spent origin`. #173's bounded install degraded correctly instead of hanging, but that
shard ran fully direct and lost every walled Board. 1 of 15 shards in 2 of 3 runs.

## 7. What this makes the real lever

Not #168. The candidates, in the order the evidence supports them:

1. **The recovery metric itself is mis-specified** — it is attempts-based, so it cannot separate
   "one IP absorbed 230 walls" from "the range refuses us", which is precisely the judgment
   ADR-0063's exit criterion asks it to support. A per-request outcome counter would.
2. **Demand-side throttling** of the async detail pass, so its concurrency does not outrun a
   single tunnel.
3. **Egress capacity in parallel** (a pool) rather than faster rotation — supply then scales with
   rate instead of with wall-clock.
4. **Lower `_ROTATION_COOLDOWN`** — its docstring already nominates it as the first number to
   revisit. But each rotation is a `systemctl restart` behind a closed gate, and `network` retries
   already rose from 250 fleet-wide pre-#172 to 52k–61k post; more restarts may cost more than
   they buy. Measure before changing.
