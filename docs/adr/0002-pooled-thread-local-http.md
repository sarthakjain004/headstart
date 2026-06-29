# ADR-0002: One pooled, thread-local curl_cffi HTTP session

- Status: Accepted
- Date: 2026-06-22

## Context

Scrapers used the stdlib `urllib` (a fresh TCP+TLS connection per request) for plain boards and a
separate `curl_cffi` path for the TLS-fingerprinted ones (darwinbox, trakstar). A quick benchmark on
this machine showed `urllib` at ~827 ms/request vs ~110 ms for a pooled client on same-host bursts —
a 5–7x penalty, paid hardest exactly where we burst: every board on `boards-api.greenhouse.io`,
every detail fetch on one tenant's host.

## Options considered

- **A — keep stdlib `urllib`.**
- **B — add connection pooling to the sync layer, keep the thread model.**
- **C — rewrite the framework to async (httpx/aiohttp).**

The pooling — not raw library speed — is the lever, so B captures the win with a small change; C
only pays off at much larger fan-out and is a big rewrite.

## Decision

**B, unified on `curl_cffi.Session`.** All scraper HTTP now goes through `headstart.http`, which
hands out **one `curl_cffi` Session per thread** (keep-alive pools connections; thread-local because
a libcurl session isn't safe to share across threads — and the scrapers already run under thread
pools, per-company and per-detail-pass). It impersonates Chrome, so the *same* client serves both
plain JSON APIs and the Cloudflare/DataDome boards — collapsing the two HTTP stacks into one. Async
(C) is deferred until threads-plus-pooling stops being enough.

## Consequences

`BaseScraper._get` still raises `urllib.error.HTTPError` on a definitive HTTP error, so lever's
EU-instance 404 fallback (which branches on `exc.code`) is untouched — only the transport changed
underneath. The liveness script still uses `urllib`; converting it is the remaining high-value
follow-up (it's the biggest same-host burst of all).
