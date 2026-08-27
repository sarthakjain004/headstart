# ADR-0092: Resolve through WARP, not before it

**Status:** accepted · **Date:** 2026-08-27 · **Amends:**
[ADR-0067](0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) (its "one to three
addresses per colo" is an IPv4 measurement, not a property of WARP),
[ADR-0081](0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md) (right about the depth, and
this is why the two never reconciled) · **Relates to:**
[ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress itself),
[ADR-0078](0078-width-narrows-once-the-origin-has-walled.md) (narrowing justified by the shallow
pool), [ADR-0091](0091-compaction-outranks-the-pipeline.md) (the same day's other measured fix)

## Context

`spare_egress` dialled WARP and handed callers `socks5://127.0.0.1:40000`. With that scheme
**curl_cffi resolves the hostname itself**, before the request reaches the proxy.

That is fine on a machine with global IPv6. It is not fine on one without — and neither a laptop on
a home ISP nor anything else measured here has one; `ifconfig` shows only `fe80::` link-local. With
no route for AAAA, the client takes the A record, and the request egresses over **IPv4**.

Cloudflare's IPv4 egress space is scarce and recycled per colo. Its IPv6 space is a `/32`. So the
scheme silently selected the address family, and the family selected the pool depth.

The cost was concrete. A liveness sweep of `apply.workable.com` — whose wall is a spent per-IP
budget that refills in under a minute, so rotation is exactly the right tool — was measured getting
**3 distinct addresses across 53 daemon restarts**. Four consecutive `warp-cli registration delete`
+ `new` cycles all returned the same IP. The conclusion drawn at the time was that the colo was
pinned and the pool could not be widened from this machine. That conclusion was wrong.

## Decision

**`proxy_url()` returns `socks5h://`.** The `h` hands hostname resolution to WARP, which finds the
AAAA record where the host publishes one and egresses from the deep IPv6 pool.

Measured the same day, same daemon, same colo (BOM), five consecutive rotations:

| resolution | distinct addresses |
| --- | --- |
| `socks5://` (client resolves, IPv4) | **3** of 5 — `104.28.220.169` three times |
| `socks5h://` (WARP resolves, IPv6) | **5** of 5 |

And on the host that prompted it, in the same second:

```
apply.workable.com  via socks5://   -> 429  cf-mitigated: challenge
apply.workable.com  via socks5h://  -> 200
```

**Safe for IPv4-only hosts.** WARP resolves, finds only an A record, and egresses IPv4 exactly as
before — verified against `api.lever.co` and `*.myworkdayjobs.com`, both without AAAA. Greenhouse
and Ashby (both with AAAA) were unchanged at 200. Nothing regressed under either scheme.

## Why ADR-0067 and ADR-0081 disagreed

ADR-0067 measured "one to three addresses" per colo. ADR-0081 measured 12,702 rotations producing
11,007 distinct addresses and concluded the first figure was simply wrong, while noting that
0067's colo-pinning finding still held. The two were never reconciled; ADR-0067 carries an
amendment header saying as much.

They were counting different pools. ADR-0081 states its addresses were **IPv6**. ADR-0067 does not
name a family. The controlled comparison above — both families, one colo, the same rotations —
makes that the natural reading: 0067 measured the IPv4 pool and 0081 the IPv6 one, and both numbers
are correct about their own.

This is **strongly supported rather than proven**: confirming it outright means re-reading
ADR-0067's `warp-colo-probe` methodology to establish which family it resolved, and those
diagnostics were removed in the change that recorded it. The generalisable lesson does not depend
on settling it — *a count of "addresses per rotation" is meaningless without naming the address
family it counted.*

## Consequences

- Rotation on a developer machine is no longer structurally worse than on CI. The observed
  difference — 5.7% fresh addresses locally against 99.8% on runners — was the resolver, not the
  hardware, the colo, or the registration.
- **`stream_width`'s narrowing (ADR-0078) rests on a premise that is now doubly undermined.** It
  narrows fan-out because "rotation buys one address at a time"; ADR-0081 already weakened that,
  and IPv6 resolution weakens it further. Not changed here — it wants its own measurement — but it
  should not be read as settled.
- Hosts without AAAA still draw from the shallow IPv4 pool, so for those, pacing remains the better
  lever than rotation. Worth checking the AAAA record before assuming rotation will help.
- Concepts, colo codes and the full measurement are written up for newcomers in
  `docs/spare-egress/how-warp-egress-works.md`.
