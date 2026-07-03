# ADR-0015: Async HTTP/2-multiplexed detail fan-out, opt-in per scraper

- Status: Accepted
- Date: 2026-07-03

## Context

The detail-fetch scrapers (SmartRecruiters, Workday, Join, Rippling, Trakstar) issue *many* requests
to the *same host* per board — one per posting — through `BaseScraper.fan_out`, a
`ThreadPoolExecutor(workers=8)`. Each worker thread has its own thread-local `curl_cffi` session
(ADR-0002), hence its **own connection**, and each connection carries one request at a time. So a
board's detail pass runs over up to 8 connections with **zero HTTP/2 multiplexing** — even though
Chrome impersonation negotiates h2, the thread-per-connection model never has concurrent streams on
one connection to interleave. ADR-0002 banked the keep-alive pooling win (5–7×) and explicitly
deferred the async rewrite.

A same-host detail burst is exactly the multiplexing sweet spot: one connection, many concurrent
streams. We built a separate async path and measured it on SmartRecruiters (30 busiest boards, 3000
jobs, deterministic ranked selection — identical every run; 0 failures in every arm):

| detail pass | model | time | jobs/s | vs sync |
| --- | --- | --- | --- | --- |
| sync `fan_out` | 8 threads → 8 connections | 20.0s | 150 | 1.00× |
| async, width 8 | 8 streams / 1 connection | 17.3s | 173 | 1.16× |
| async, width 20 | 20 streams / 1 connection | 16.4s | 182 | 1.22× |
| async, width 50 | 50 streams / 1 connection | 14.3s | 210 | 1.40× |
| async, width 100 | 100 streams / 1 connection | 12.4s | 241 | 1.61× |

At *matched* concurrency (8 vs 8) multiplexing is already ~14% faster — the pure protocol win (one h2
connection vs eight, fewer TLS handshakes). Widening the stream window scales it to ~38% at width
100, still climbing (SmartRecruiters caps its list at 100 postings/board, so per-board detail count —
and thus the multiplexing window — is bounded). No correctness cost at any width.

## Decision

Add `BaseScraper.fan_out_async` — a **separate, opt-in** counterpart to `fan_out` — and adopt it
**one scraper at a time**, not as a wholesale replacement.

- `http.fetch_async(session, ...)` mirrors `fetch`'s retry policy (403/429/5xx + transient network,
  backoff, no DNS retry) but `await`s over a caller-supplied `curl_cffi` `AsyncSession`.
- `fan_out_async(items, fn, *, concurrency, default)` shares **one** `AsyncSession` across all items,
  so same-host requests ride as concurrent **streams over one h2 connection**; an `asyncio.Semaphore`
  bounds the in-flight streams — the *multiplexing width*, env-tunable via `HEADSTART_H2_STREAMS`
  (default 8). It runs its own event loop, so the sync per-company thread pool can call it per board.
  Same contract as `fan_out`: input-aligned results, a raising item → `default`.
- A scraper opts in by branching its detail pass on `HEADSTART_ASYNC_FANOUT` — keeping the sync path
  as default. This makes the A/B a same-scraper, flip-one-env comparison. **SmartRecruiters is wired
  first.**

## Rejected alternatives

- **Replace `fan_out` wholesale.** One risky, un-measured cutover across five scrapers with different
  host aggression profiles; no per-ATS tuning. The opt-in gate lets each be measured and rolled back.
- **Just raise the sync thread/connection count.** More connections = more TLS handshakes and more
  per-host aggression (ban risk), and it still never exploits h2 multiplexing. Multiplexing gets more
  concurrency *per connection*, not more connections.
- **HTTP/3.** Marginal for this workload (keep-alive already amortizes handshakes on the same-host
  paths; the win would be connection-setup on one-shot hosts, which the detail burst isn't).
- **Full async rewrite of the pipeline.** The company-level parallelism is across *different* hosts,
  which multiplexing can't help — so the async win is confined to the per-board detail fan-out.
  Rewriting the outer loop buys nothing here; deferred.

## Consequences

The sync path stays the default; async is adopted per scraper behind the env gate, each validated by
its own A/B. The multiplexing width is tunable without a code edit. Trakstar (DataDome) must keep a
*small* width if adopted — multiplexing concentrates load on one connection/host. One further
optimization is deferred: same-host ATSes (all SmartRecruiters boards hit `api.smartrecruiters.com`)
currently open one `AsyncSession` per board; a session shared across boards would collapse that to ~1
connection. Next adoption target is **Workday**, whose boards have thousands of details per host — a
far bigger same-host burst than SmartRecruiters, so a larger expected win.
