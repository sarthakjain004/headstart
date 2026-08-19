# ADR-0067: The spare egress buys a different IP, not a fresh budget

**Status:** accepted · **Date:** 2026-08-19 · **Amends:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (its premise that a walled shard picks
up an *unspent* origin budget) · **Relates to:**
[ADR-0047](0047-pace-against-the-origin.md),
[ADR-0065](0065-wait-for-the-fresh-ip-rather-than-riding-the-spent-one.md)

## Context

ADR-0063 gave a shard whose origin budget is spent a second route: dial Cloudflare WARP and carry
on through it. ADR-0065 then made a walled caller *wait* for a fresh IP rather than spend an
attempt on the spent one. Both rest on an assumption neither measured: that rotating the spare
egress yields an address the origin has not already seen.

Three probes on real runners measured it — `warp-install-probe`, `warp-rotate-probe` and
`warp-colo-probe`, `workflow_dispatch` diagnostics removed in the same change that records this.
The numbers below are what they returned; the runs are 32264439126, 32266015124 and 32270727776.

**Rotation usually returns the same address.** Across 30 jobs, `systemctl restart warp-svc` — what
`spare_egress.rotate()` does — produced a genuinely different egress IP **11 times out of 30**. A
second probe over 8 jobs × 4 rotations put it at 15/32 (47%).

**No rotation verb is better.** `registration delete` + `registration new` — a new identity rather
than a reconnect — did **worse**: 10/32 (31%). `tunnel rotate-keys` was no better. `tunnel endpoint
set`, the one command that could move us to a different Cloudflare datacentre, **broke the tunnel**:
the endpoint arm produced 6 usable readings against 35 for the others, and one endpoint was refused
outright. Its own help text explains why — it is a Zero Trust feature, and this is a consumer
registration.

**The reason is the colo, and it never moves.** Cloudflare puts a WARP client on the datacentre
nearest it, and across 18 jobs the colo changed **0 times** in any rotation, by any method. Each
colo carries a very small egress pool:

| colo | distinct egress IPs observed |
| --- | --- |
| MSP, IAD, SEA | 3 each |
| ORD | 2 |
| LAX, SJC | 1 each |

A shard is pinned to one colo for its whole life, so rotation redraws from **one to three
addresses**. At that pool size a collision is the expected outcome, not bad luck — and a shard on
LAX or SJC can never rotate to anything at all.

**And the spare egress is worse for diversity than the route it replaces.** ADR-0047 verified that
parallel shards get distinct direct IPs; that held here, **30 distinct direct IPs for 30 jobs**.
The same 30 jobs shared just **11 WARP IPs, one of them across 8 jobs**. So a walled shard moves
off an address nobody else is using and onto one up to seven siblings are already spending.

Region control was considered and does not help. Standard GitHub-hosted runners cannot choose an
Azure region (only larger runners with Azure private networking can, which this project's free-tier
constraint rules out), and it would not matter: the runners already spread across six colos on
their own. Cross-shard diversity is not the problem. A single shard being pinned to a 1–3 address
pool is, and a different region only relabels which small pool it is stuck in.

## Decision

**Treat the spare egress as "one different IP", not as a fresh origin budget, and stop spending
retry budget as though rotation will rescue a shard.**

Concretely, and in the order they pay:

1. **Nothing in the retry path may assume a rotation produced an unseen address.** `rotate()`'s
   return value already means only "a fresh IP is in service"; ADR-0065's refund of an attempt on
   that signal stands, because waiting still beats riding a known-spent route — but it is now
   understood to be buying a coin flip, not a new budget.
2. **A request killed by our own rotation earns its attempt back.** A rotation restarts the tunnel
   every in-flight request is riding, so those requests die through no fault of the origin — 27 of
   them in run 32249345870 against zero the run before. This is the one place the measurement
   showed budget being spent on something we did to ourselves.
3. **Concurrency, not rotation, is the lever that remains.** Since a walled shard cannot expect a
   fresh budget, the width it fans out at has to be sized for the budget it actually has. That is
   left to its own change; ADR-0047's measured width/loss table is the input.

## Alternatives considered

- **Cloudflare Zero Trust.** The free tier unlocks `tunnel endpoint set`, which would let a shard
  choose its colo and therefore its pool. Rejected for now on the device cap: the free tier covers
  50 devices, and 15 shards across the measured 12 runs a day is ~180 registrations a day, so it
  needs a revoke-on-exit step this pipeline has no place to run reliably. Worth revisiting if the
  egress problem outgrows the concurrency fix.
- **A commercial proxy pool.** Buys genuine diversity, and is the only option that actually
  delivers what ADR-0063 assumed. Rejected on cost and on adding a paid dependency to a free-tier
  deployment.
- **Choosing the runner region.** Covered above: unavailable on standard runners, and would not fix
  within-shard rotation even if it were.
- **Dropping the spare egress entirely.** Tempting given the direct route has better IP diversity —
  but the spare is dialled only *after* the direct IP is already walled, so its worse pool is still
  better than the address that just refused us. It keeps its place as a fallback; it loses its
  billing as a budget.

## Consequences

**ADR-0063's headline claim is narrowed.** A shard that spends its origin budget picks up a
*different* egress IP, drawn from a pool of one to three, quite possibly shared with other shards.
It does not pick up an unspent budget. Anything reasoning from "the spare gives this shard room"
should be re-read against that.

**Rotation counters are not a health signal.** `spare_egress.rotations()` counts restarts, and a
restart that returns the same address counts the same as one that does not. A future change that
wants to know whether rotation is *working* has to compare egress addresses, not count rotations —
which is what the probes had to do.

**The pool size is a property of the colo, so it varies by runner and cannot be relied on.** Two
shards in the same run can have three usable addresses and one respectively. Any future tuning that
assumes a uniform spare-egress capacity across shards is assuming something false.
