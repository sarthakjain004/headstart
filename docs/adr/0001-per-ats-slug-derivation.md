# ADR-0001: Per-ATS slug derivation via `slug_from` on the scraper

- Status: Accepted
- Date: 2026-06-22

## Context

The discovery/liveness pipeline stores each board as a `(tenant, url)` row (e.g.
`data/ats-tenants-merged/active/{ats}.csv`). To scrape it, we need a `CompanyRef.slug` in the exact
shape that ATS's scraper expects. Most scrapers are keyed by the bare tenant label, but a few
aren't: zoho wants the careers host (`acme.zohorecruit.in`), workday wants the full careers URL, and
oracle wants the host plus an optional site number. Something has to map row → slug per ATS. The
question was where that per-ATS knowledge should live.

## Options considered

- **A — an `if ats == …` mapping in `config.py`.** Simplest to read, but it puts ATS-specific
  knowledge *outside* the scraper. The scraper's `url()` already interprets the slug; the inverse
  mapping would sit in a different file. Since new scrapers get added often, every author would have
  to remember to also edit `config.py` — shotgun surgery, and a silent footgun (forget it → wrong
  slug → the scraper quietly fetches the wrong board or nothing).
- **B — an overridable `slug_from(tenant, url)` method on the scraper.** The slug knowledge lives
  with the scraper that owns it; adding an ATS stays a one-file change and `config.py` stays
  generic. This is the cohesive option.
- **C — bake the resolved slug into the active CSV.** Moves the same per-ATS logic upstream into
  `check_liveness.py` — the wrong layer (a liveness validator shouldn't know scraper slug
  internals) — and couples the data file to slug semantics, so the data goes stale if a scraper's
  interpretation ever changes.

## Decision

**B.** The scraper is the single authority on what its slug means, so the inverse constructor
belongs next to `url()`. `config.load_active_companies` resolves the slug through the registry
(`SCRAPERS[ats].slug_from(tenant, url)`) and never names a specific ATS.

A **default method, not an abstract one.** Twelve of fourteen scrapers want the same
`return tenant`. Making `slug_from` abstract would force a dozen boilerplate overrides and provide
no safe default if one were forgotten. So `BaseScraper.slug_from` ships the `tenant` default
(template-method pattern) and only the exceptions opt in:

- `BaseScraper.slug_from` → `tenant`
- `ZohoScraper.slug_from` → the careers host from the url
- `WorkdayScraper.slug_from` → the full careers url

## Consequences

`config.py` now reaches the scraper registry (lazily, inside `load_active_companies`, so the
lightweight `load_companies` toml path stays free of scraper imports). That small `config → registry`
dependency is worth it to keep the per-ATS knowledge in one place.
