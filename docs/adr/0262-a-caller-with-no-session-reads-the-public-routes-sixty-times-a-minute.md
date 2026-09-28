# ADR-0262: A caller with no session reads the public routes sixty times a minute

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:**
[ADR-0258](0258-the-spaces-read-routes-answer-anyone.md) (the routes this limits, and the risks it
named), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (the
MCP server and the `X-HeadStart` marker), [ADR-0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md)
(what an unasked Trends question costs), [ADR-0035](0035-email-job-alerts.md) (the Digest run)

## Context

ADR-0258 opened six read routes to anyone, so that anyone can use HeadStart's MCP server:
`/search`, `/facets`, `/trends`, `/hot`, `/companies/suggest` and `/companies/lookup`. It named two
risks and left both open. Anonymous callers can load the one free CPU Space the product runs on:
a new query is an encoder call and a vector search, and a Trends question not yet asked this boot
costs up to ~1.5 s of its CPU, worked out one at a time (ADR-0251). And anyone can copy the index
page by page, bounded only per request (`k` ≤ 100, `page` ≤ 20).

The Space is one process. `start.sh` ends in `exec python app.py`, Werkzeug's threaded server
(`server: Werkzeug/3.1.8` on the live Space's replies, one `x-proxied-replica`), so a count kept in
memory is authoritative.

### Who a caller is, measured 2026-09-28

Behind Hugging Face's proxy the app's socket peer is the proxy, not the caller. The app cannot
echo its own request headers, so the proxy was measured through a public Docker Space running
httpbin on the same edge (`baw-appie-httpbinorg.hf.space`). Its `/ip` echoes the
`X-Forwarded-For` it receives. Six probes from one machine, whose address `api.ipify.org` reported
as the one below:

| Sent | `/ip` answered |
|---|---|
| no header | `106.51.119.248` |
| `X-Forwarded-For: 203.0.113.7` | `203.0.113.7, 106.51.119.248` |
| `X-Forwarded-For: 203.0.113.7, 198.51.100.9` | `203.0.113.7, 198.51.100.9, 106.51.119.248` |
| `X-Real-IP: 203.0.113.8` | `106.51.119.248` |
| `Forwarded: for=203.0.113.9` | `106.51.119.248` |
| `X-Forwarded-For: 203.0.113.7`, through `/--replicas/cbm6z/ip` | `203.0.113.7, 106.51.119.248` |

The edge appends the address it accepted the connection from and keeps what the caller sent to
its left, adding no hops of its own. **The last entry is the edge's word; any earlier one is the
caller's to forge.** Three more findings:

- HF also sends the app an `X-Ip-Token`, a JWT naming the caller's address, but a caller-sent
  `X-Ip-Token` reached the app unchanged. It is forgeable, and only HF can check its signature.
- `*.hf.space` publishes no AAAA record, so callers reach the edge over IPv4, and one caller cannot
  step through the addresses of an IPv6 prefix.
- HF's edge answers a CORS preflight itself and reflects any `Origin` (measured by the hosted
  endpoint's design work the same day: an `OPTIONS` from `evil.example.com` answered 200, the
  origin echoed, no `X-HeadStart` marker).

The sample is one Space, one machine and one address. The edge is the one in front of this Space
(the same `*.hf.space` domain and the same `x-proxied-*` reply headers), but this app itself was
not probed.

## Decision

**One sliding window per address, in memory.** `headstart.serving.rate_limit.RateLimit` keeps each
client's admitted request times behind one lock. A refused request is not counted, so a caller that
keeps asking while refused is admitted as soon as its oldest request leaves the window. A client
idle for a whole window is dropped, so memory holds one window's callers, not every address since
boot. It is empty after every restart, which every pipeline publication causes.

**Who and what it limits.** `_limit_the_anonymous` in the Space's `app.py` runs before every
request to the six read routes. A caller with a signed-in session is not limited, and its requests
do not count against its address. A caller without one is counted at the last `X-Forwarded-For`
entry, or at the socket peer when there is no header, as in a local run. The door, `/me` and every
Account route are untouched. With the wall off, everyone is a caller without a session.

**60 requests in any 60 s from one address, the six routes together.** Past it the app answers:

```text
429 {"error": "too many requests",
     "detail": "at most 60 requests in 60 s from one address; retry in N s"}
Retry-After: N
X-HeadStart: app; agent-api=1
```

`N` is the whole seconds until the caller's oldest counted request leaves the window, 1 to 60. The
marker is there because every reply the app makes carries it (ADR-0253), so a client tells this
refusal from the edge's.

Why 60:

- **The page's own busiest minute fits.** A Search is two requests, `/search` and `/facets`, and so
  is each page turn. The Trends tab's worst burst is one `/trends` per box unticked in its ATS
  picker, about 20. The company picker asks once per pause in typing, and a settled view is
  prefetched once. A reader who clicks briskly stays well under 60, and the worst burst fits three
  times over.
- **The MCP server meets its own limit first.** `space_mcp.space_client.RequestBudget` holds each
  server process to 60 requests in any 60 s, so a lone server stops itself without a round trip.
  A normal agent task is a few tool calls of one to three requests each.
- **The Digest run is never throttled into a lost Digest.** The alerts workflow
  (`.github/workflows/alerts.yml`, through `headstart.alerts.space_query`) calls `/search` with no
  session and no credential, since ADR-0258 retired `ALERTS_TOKEN`: once per Subscription, one at a
  time, from one GitHub runner's address. The last three successful alerts runs (2026-09-24 and
  2026-09-25) each held 4 Subscriptions and made at most one search; the latest logged "1
  digest(s) sent, 3 skipped". It would need 60 searches inside one minute to meet the limit. Even
  then it retries anything but a 400 or 401 after 15, 30 and 60 s. A refused request is not
  counted and a one-at-a-time caller asks nothing else while it waits, so its window only drains,
  and 105 s outlasts the window: the limit could delay a Digest but never cost one. A test pins
  both halves. So no credential comes back to exempt it, which ADR-0258 left open.
- **The cost of one address at the limit** is one request a second.

## Options rejected

- **The first `X-Forwarded-For` entry.** The caller writes it, so a caller that changes it on every
  request never meets the limit.
- **One global budget per route.** The fallback if a caller's address could not be established. It
  can be, and one busy caller would then lock every other anonymous caller out.
- **`X-Ip-Token`.** Forgeable, as above.
- **A budget per route, weighted by cost.** More machinery than the evidence supports yet. See the
  first risk below.
- **A shared store.** There is one process, so there is nothing to share it with.

## Risks, stated plainly

- **An unasked Trends question is the weak spot.** Such questions are worked out one at a time, at
  up to ~1.5 s each (ADR-0251). One address at the limit asking only new ones, such as a `since` a
  millisecond different each time, could keep that work busy most of each minute, and other
  callers' new Trends questions would wait behind it. Kept answers are unaffected. If the Space's
  logs show this, a tighter budget for `/trends` alone is the next step.
- **Copying is slowed, not stopped.** 60 requests of at most 100 rows each is at most 6,000 rows a
  minute from one address. Even with perfectly partitioned filter sets, the 533,500-row table
  takes at least ~90 minutes, and many addresses multiply that. An hourly or daily budget would
  bound it further. It is not built.
- **Many addresses are not bounded.** This limit is per address. Because the edge reflects any
  `Origin`, any website can have its visitors' browsers call these routes, each counted at its own
  address. Refusing a request whose `Origin` names another site would close that for browsers:
  the page's own requests are same-origin GETs, which carry no `Origin`, and the MCP server sends
  none. It is not built, because whether other sites may use these routes is the owner's call.
- **Callers behind one address share its 60**, such as people behind one office NAT.
- **If HF's edge ever appends a hop of its own,** the last entry would name HF and every caller
  would share one budget. That would fail toward stricter, visibly: everyone would see 429s.

## Consequences

- The MCP client (`space_mcp.space_client`) reads a 429 as this limit only when the reply carries
  `X-HeadStart: app; agent-api=1`, and tells its agent "retry in N s" from a whole-seconds
  `Retry-After`. A 429 without the marker is HF's edge, which it retries. A test pins both
  headers on this refusal, so changing either breaks that contract visibly.
- The limit holds an anonymous caller's address in memory, for at most a minute after its last
  counted request, and never writes it down or logs it. PRIVACY.md says so.
- After deploy, check it on the live Space: 60 requests to a cheap route answer, the 61st answers
  429 with the marker, and the same request with a forged first `X-Forwarded-For` entry still
  answers 429.

## Amendment (2026-09-28): an Account is a caller too, and writes are limited (#592)

A signed-in session is no longer exempt. Sign-up is open to any Google account, so an unlimited
session let one client out-read the address limit by signing in, and each distinct `/search` query
runs the encoder. `_limit_the_anonymous` is now `_limit_each_caller`: a caller with a session is
counted as its Account, one without as its address, and the two are counted apart, so a reader
signing in starts a fresh window rather than inheriting its address's. Two limits join the read
limit, both per caller over the same 60 s:

- **30 writes** (`POST`, `PUT`, `DELETE`, any path, and `GET /unsubscribe`, which commits). Every
  Account write is its own HF commit on the one token every Account shares. A résumé being edited
  pushes at most once every three minutes plus once per tab or document switch, so the busiest
  real writer is a reader starring jobs.
- **20 `GET /saved`**. It lists the whole Subscriptions repo. `store._list_files` also reuses a
  listing of the head for 5 s. Every write through the store drops it once the write lands, and a
  listing taken while a write landed is not kept, so a request after a write never lists without
  it; another process's write can go unseen for up to 5 s.

These bound one caller, not the total, as the issue says: sign-up is open, so N Accounts still
spend N times the budget on the one token. Bounding the total would take a global limit, which
would let one abuser lock out every Account; that is not built.

The 429 names the unit: "from one Account" or "from one address". The owner chose this in-process
window over flask-limiter, which would be a new pin in the production image.
