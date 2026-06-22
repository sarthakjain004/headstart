# Design choices

A log of non-obvious design decisions — the option picked, the ones rejected, and why — so the
reasoning survives past the commit that made it. Append new entries at the top.

---

## HTTP layer: one pooled, thread-local curl_cffi session (2026-06-22)

**Context.** Scrapers used the stdlib `urllib` (a fresh TCP+TLS connection per request) for plain
boards and a separate `curl_cffi` path for the TLS-fingerprinted ones (darwinbox, trakstar). A
quick benchmark on this machine showed `urllib` at ~827 ms/request vs ~110 ms for a pooled client
on same-host bursts — a 5–7x penalty, paid hardest exactly where we burst: every board on
`boards-api.greenhouse.io`, every detail fetch on one tenant's host.

**Options.** (A) keep stdlib `urllib`; (B) add connection pooling to the sync layer, keep the
thread model; (C) rewrite the framework to async (httpx/aiohttp). The pooling — not raw library
speed — is the lever, so B captures the win with a small change; C only pays off at much larger
fan-out and is a big rewrite.

**Decision: B, unified on `curl_cffi.Session`.** All scraper HTTP now goes through
`headstart.http`, which hands out **one `curl_cffi` Session per thread** (keep-alive pools
connections; thread-local because a libcurl session isn't safe to share across threads — and the
scrapers already run under thread pools, per-company and per-detail-pass). It impersonates Chrome,
so the *same* client serves both plain JSON APIs and the Cloudflare/DataDome boards — collapsing
the two HTTP stacks into one. Async (C) is deferred until threads-plus-pooling stops being enough.

**Contract preserved.** `BaseScraper._get` still raises `urllib.error.HTTPError` on a definitive
HTTP error, so lever's EU-instance 404 fallback (which branches on `exc.code`) is untouched — only
the transport changed underneath. The liveness script still uses `urllib`; converting it is the
remaining high-value follow-up (it's the biggest same-host burst of all).

---

## Per-ATS slug derivation: `slug_from` on the scraper (2026-06-22)

**Context.** The discovery/liveness pipeline stores each board as a `(tenant, url)` row (e.g.
`data/ats-tenants-merged/active/{ats}.csv`). To scrape it, we need a `CompanyRef.slug` in the
exact shape that ATS's scraper expects. Most scrapers are keyed by the bare tenant label, but a
few aren't: zoho wants the careers host (`acme.zohorecruit.in`), workday wants the full careers
URL, and oracle wants the host plus an optional site number. Something has to map row → slug per
ATS. The question was where that per-ATS knowledge should live.

**Options weighed.**

- **A — an `if ats == …` mapping in `config.py`.** Simplest to read, but it puts ATS-specific
  knowledge *outside* the scraper. The scraper's `url()` already interprets the slug; the inverse
  mapping would sit in a different file. Since new scrapers get added often, every author would
  have to remember to also edit `config.py` — shotgun surgery, and a silent footgun (forget it →
  wrong slug → the scraper quietly fetches the wrong board or nothing).
- **B — an overridable `slug_from(tenant, url)` method on the scraper.** The slug knowledge lives
  with the scraper that owns it; adding an ATS stays a one-file change and `config.py` stays
  generic. This is the cohesive option.
- **C — bake the resolved slug into the active CSV.** Moves the same per-ATS logic upstream into
  `check_liveness.py` — the wrong layer (a liveness validator shouldn't know scraper slug
  internals) — and couples the data file to slug semantics, so the data goes stale if a scraper's
  interpretation ever changes.

**Decision: B.** The scraper is the single authority on what its slug means, so the inverse
constructor belongs next to `url()`. `config.load_active_companies` resolves the slug through the
registry (`SCRAPERS[ats].slug_from(tenant, url)`) and never names a specific ATS.

**A default method, not an abstract one.** Twelve of fourteen scrapers want the same
`return tenant`. Making `slug_from` abstract would force a dozen boilerplate overrides and provide
no safe default if one were forgotten. So `BaseScraper.slug_from` ships the `tenant` default
(template-method pattern) and only the exceptions opt in:

- `BaseScraper.slug_from` → `tenant`
- `ZohoScraper.slug_from` → the careers host from the url
- `WorkdayScraper.slug_from` → the full careers url

**Trade-off accepted.** `config.py` now reaches the scraper registry (lazily, inside
`load_active_companies`, so the lightweight `load_companies` toml path stays free of scraper
imports). That small `config → registry` dependency is worth it to keep the per-ATS knowledge in
one place.
