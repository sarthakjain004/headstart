# How WARP egress works: colos, address families, and why the pool looked shallow

Background for anyone reading `headstart.spare_egress` or wondering why a rotation did or didn't
get a fresh IP. Written 2026-08-27, after a local sweep appeared to be capped at three addresses
and turned out not to be.

## Colos

A **colo** is a colocation facility — a data centre. Cloudflare runs a few hundred, each named
after the nearest airport's IATA code:

| code | where |
| --- | --- |
| BOM | Mumbai (Bombay) |
| IAD | Washington Dulles, northern Virginia — the densest cloud region on earth |
| DFW | Dallas–Fort Worth |
| LAX | Los Angeles |
| ORD | Chicago O'Hare |
| SEA | Seattle |
| MSP | Minneapolis–St Paul |
| YYZ | Toronto |

Cloudflare uses **anycast**: the same IP address is announced from every colo at once, and the
internet's own routing delivers you to the nearest one. You do not choose it and cannot change it
from the client. A laptop in India lands on BOM and stays there for the life of the connection —
ADR-0067 measured the colo changing **zero times** across 30 rotations by any method.

GitHub-hosted runners sit in US Azure data centres, so they land on whichever US colo is nearest
that runner. Different runners get different ones: a single pipeline run was observed spanning
DFW, IAD, LAX, MSP, ORD, SEA and YYZ.

## Address families, and why they decide pool depth

This is the part that matters for rotation.

**IPv4 has ~4 billion addresses in total**, and they have been scarce for a decade. Cloudflare
therefore recycles a small set per colo. Rotating the WARP daemon on a machine pinned to one colo
draws from that small set, so repeats are the normal outcome.

**IPv6 gives each site something on the order of a `/32` or `/48`** — trillions of addresses. There
is no reason to recycle, so a rotation lands a genuinely new address nearly every time.

Measured on one machine, same daemon, same colo (BOM), five consecutive rotations:

| resolution | distinct addresses |
| --- | --- |
| IPv4 | **3** of 5 — `104.28.220.169` returned three times |
| IPv6 | **5** of 5 |

And on GitHub runners, 1,898 rotations across six shards produced **1,853 distinct IPv6
addresses** — 1,897 of 1,916 observations reported `(moved)`, with exactly one repeat.

## What actually selects the family: `socks5` vs `socks5h`

WARP runs in proxy mode, listening as SOCKS5 on a local port. The scheme in the proxy URL decides
**who resolves the hostname**, and that in turn decides which family the request leaves on:

- `socks5://` — the *client* resolves. On a machine with no global IPv6 address (only `fe80::`
  link-local, which is the normal state on a home ISP without IPv6), the client can only use the
  A record, so the request egresses over IPv4 — the shallow, recycled pool.
- `socks5h://` — the *proxy* resolves. WARP finds the AAAA record where the host publishes one and
  egresses over IPv6, from the deep pool. Where a host is IPv4-only it finds only an A record and
  egresses IPv4 exactly as before.

So the deep pool was always available locally. One missing letter kept every request on IPv4.

**You do not need IPv6 from your ISP for this.** WARP carries IPv6 on your behalf over an IPv4
tunnel. What you must not do is resolve the name before handing the request over.

The difference is visible on a walled host in the same second:

```
apply.workable.com  via socks5://   -> 429  (cf-mitigated: challenge)
apply.workable.com  via socks5h://  -> 200
```

## Why two ADRs disagreed for a week

[ADR-0067](../adr/0067-the-spare-egress-buys-a-different-ip-not-a-fresh-budget.md) measured "one to
three addresses" per colo and built `stream_width` narrowing on it.
[ADR-0081](../adr/0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md) then measured 12,702
rotations producing 11,007 distinct addresses and called the first number wrong.

Neither was wrong. ADR-0081 states its addresses were **IPv6**; ADR-0067 does not name a family at
all. They were almost certainly counting different pools, and the controlled comparison above —
both families, same colo, same rotations — is what makes that explanation concrete. Confirming it
outright would mean re-reading ADR-0067's probe methodology to see which family it resolved; that
has not been done, so treat the reconciliation as strongly supported rather than proven.

The practical lesson generalises past WARP: **a measurement of "how many addresses does rotation
give me" is meaningless without saying which address family was measured.**

## What this means in practice

- Rotation on a laptop is not inherently worse than on CI. It was only worse because of the
  resolver.
- A host with no AAAA record (measured: `api.lever.co`, Workday's `*.myworkdayjobs.com`) still
  egresses IPv4 and still draws from the shallow pool. For those, rotation genuinely is limited and
  pacing is the better lever.
- `headstart.spare_egress.proxy_url()` returns `socks5h://` for these reasons. Do not "simplify" it
  back.
