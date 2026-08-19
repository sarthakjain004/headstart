# ADR-0065: Wait for the fresh IP rather than riding the spent one

**Status:** Accepted · **Date:** 2026-08-19 · **Supersedes nothing; amends ADR-0063**

## Context

ADR-0063 gave a shard that spends an ATS's Origin budget a second one: a rotating spare egress. Its
third amendment set an exit criterion — *many rotations with a low recovery rate means the whole
WARP range is refused, not one IP, at which point Workday-on-429 comes back out.*

After #172 put the async detail pass on the spare egress, the reported recovery rate collapsed:
Workday from 98.3% fleet-wide to 62–85% per shard, Eightfold from 99.2% to 29–73%. On the criterion
as written, that reads as the range being refused.

It is not. Three completed runs on `465cd74` (`32178532129`, `32189304871`, `32198367156`) measure:

| | |
|---|---|
| Rotation demand (`attempted + throttled`) | 153,800 / 163,543 / 161,059 per run |
| Rotations granted | 654 / 677 / 699 — **0.41–0.43%** |
| Rotations **failed** | **0** |
| Inter-rotation gap | median **20.1–20.8 s** against a 20.0 s cooldown; 80–100% under 25 s |
| Granted vs ceiling (`duration / cooldown`) | 699 of 929 — 75% of the physical maximum |
| Cost of one rotation | **2.05 s median, 4.06 s max** (n=2,030) |
| Proxied req/s per shard | **1.4 before #172 → 52–64 after** |

Rotation was never failing; it was being *denied* by our own cooldown, while #172 raised the rate
through the single tunnel ~40×. And `recovered` per shard is the most stable number in the report —
mean 43,504, CV **0.09**, as tight as jobs scraped (0.07) while shard duration varies at CV 0.47.
Recovery tracks the work, not the tunnel. Full analysis:
`docs/pipeline/2026-08-19_spare-egress-recovery-collapse.md`.

Two defects made the criterion unable to see this:

1. **The metric counted attempts, not requests.** A request that walled twice before succeeding
   scored 1/3, so every retry pushed the rate down.
2. **It scored every non-200 as a failure to recover.** `eightfold:nttdata.eightfold.ai` migrated
   off the ATS; its sitemap still serves 200 with 16,304 stale URLs that all 404. Those 404s made
   that shard read 1% recovered — indistinguishable from a refused range.

## Decision

Three changes, plus what we deliberately did not do.

**1. Rate rescues over walls, per settled request.** `note_routed` counts attempts (cost);
`note_settled` counts settled requests into `rescued` / `walled` / `other`, where `walled` means
refused, after every attempt, with a status **that request** treats as a wall (its own `egress_on`).
The headline is `rescued / (rescued + walled)`. Anything else is excluded from the rate rather than
counted against it — the safe way to be wrong.

**2. Cooldown 20 s → 5 s.** The 20 s came from a sibling project and was never measured here; it
sat an order of magnitude above the 2.05 s median it was bounding. 5 s keeps ~2.4× headroom, clears
the observed 4.06 s max, and lifts the per-shard ceiling from ~66 rotations to ~264.

**3. A throttled caller waits for the fresh IP instead of being handed the spent one.** Every
caller of `rotate` has just been refused *through* the spare egress, so the current IP is known
bad and returning it spends an attempt that will fail. The wait is bounded by `_ROTATION_WAIT_CAP`
(10 s) and ends early when a peer rotates first. The cap bounds the *wait*: a caller that waits it
out and then finds itself eligible still performs one rotation, so the worst case is the cap plus
one rotation round-trip. The async path starts that clock on the event loop (`wait_deadline`)
rather than inside `rotate`, so time spent queueing for an executor thread counts against it too.

Because the wait would otherwise consume the attempt it was queueing to spend, a caller that comes
back to a **fresh** IP earns one attempt back, capped at `_MAX_EARNED_ATTEMPTS = 2`. `rotate`
returns exactly that one bit, and it doubles as "this call cost the caller time": no path reaches a
fresh IP without paying about the same for it — rotating it yourself is the measured 2.05 s with
the gate closed, waiting out the cooldown is its remainder, and queueing on the rotation lock is
the peer's whole rotation, since that lock is held across the restart. A caller that waited the cap
out and is still on the spent route earns nothing: it has no new route to retry on, and crediting
it would turn a hard wall into a retry loop.

An earlier draft carried a separate `waited` flag alongside `fresh` and keyed the credit on both.
It was wrong in a way worth recording: a caller that blocked on the rotation lock for a peer's
entire rotation reported `waited=False` and earned nothing, while one that blocked the same
seconds on the condition earned an attempt. Two flags that must always agree are one flag.

**We did not reduce the detail pass's concurrency.** `_DETAIL_STREAMS`/`_DETAIL_WORKERS` are
untouched: reducing demand would trade recall for a metric, and outcomes were improving throughout
(description gap 188,612 → 181,428, board errors 131 → 116). The honest caveat: a request waiting
for a fresh IP holds its stream slot, so *in-flight* requests do dip during a wait. That is
inherent to waiting rather than a width change, it is bounded by the cap above, and the alternative
was spending the attempt on a route already known to be refused.

`rotate` also now takes the Board that walled it, so the shard report names *which* Boards spent
the IP supply rather than only how much of it went. Its bool return changes meaning — from "is
there a proxy to ride", which no production caller ever read, to "is a fresh IP now in service",
which is the bit the retry budget turns on.

`note_settled` classifies against **the request's own** `egress_on`, not a set accumulated per
group. Eightfold's API-availability probe opts out of marking because its steady 403 means "this
tenant has no API"; scored against the group's wall shapes it would land in `walled` and deflate
the rate — reintroducing, one layer down, the misattribution this change exists to remove. A
request that never settles on a status at all (a transport failure through the proxy) is counted as
`other`. That keeps it out of the rate — it is not evidence either way about a wall — while still
showing up in `requests` and in the report's `settled non-wall` tail, so a spare egress whose
listener is down is visible rather than simply absent. `network` retries went from 250 fleet-wide
before #172 to 52k–61k after, so that path is not hypothetical.

## Consequences

- The exit criterion is restated: *rotations failing, or `rescued/(rescued+walled)` staying low
  across rotations that did produce fresh IPs.* Throttled-vs-granted is a supply reading, not a
  refusal reading. On the measured runs, `failed 0` settles it — #168 stays.
- The rotation gate closes ~4× more often (2.05 s in every 5 s at saturation, ~41% worst case), so
  `proxy_for`'s bounded gate-wait is paid more often. Watch `network` retries, already up from 250
  fleet-wide pre-#172 to 52k–61k.
- Requests can now take up to 5 attempts instead of 3, bounded, and only when a wait genuinely
  produced a new IP.
- **This does not close the supply gap.** Demand is ~3,700 rotations/shard; serial rotation cannot
  reach that at any cooldown. Parallel egress capacity is the structural answer — filed as #174.

## Alternatives rejected

- **Take #168 back out (the literal exit criterion).** Rejected: `failed 0` across 2,030 rotations,
  and outcomes improved throughout. The criterion was reading a mis-specified metric.
- **Cooldown at the 2.1 s floor.** Rejected: below the observed 4.06 s max, and the marginal
  rotations are small against a gap this size. Not worth the restart churn.
- **Throttle the async detail pass.** Rejected above — trades the product for the metric.
