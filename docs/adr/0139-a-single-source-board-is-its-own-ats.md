# ADR-0139: A Single source scraper is its own `ats`, not a slug under one

**Status:** accepted · **Date:** 2026-09-11 · **Relates to:**
[ADR-0028](0028-ingest-package-layout.md) (the module layout this stays inside),
[ADR-0053](0053-scope-exclude-a-non-authoritative-board-instead-of-evicting-it.md) (truncation
travelling with a Job rather than being inferred)

## Context

Every scraper in `src/headstart/scrapers/` today adapts one third-party ATS *platform* that many
companies rent: `ats="workday"` serves thousands of tenants, keyed apart by `slug`. Eight large
employers — Amazon, Apple, Google, Meta, Tesla, Uber, ByteDance, TikTok — run their public careers
site on an in-house system instead: one company, one board, never a second tenant. None of them
sits on a platform this repo (or the wider ATS-scraper ecosystem) already speaks. This ADR names
that shape a **Single source scraper** (CONTEXT.md's glossary carries the term).

These still need to enter the pipeline the same way every other Board does — scraped, tech-filtered,
embedded, indexed, evicted, prioritized — so the question is only how to name them, not whether to
build a parallel path for them.

## Decision

Model each as its own `ats` value (`ats="amazon"`, `ats="apple"`, …), reusing `BaseScraper` and the
registry exactly as written. `slug` is fixed to that company's own primary careers host (e.g.
`"amazon.jobs"`), never discovered and never varying — there is only ever one Board. `board_key()`
stays the inherited `f"{self.ats}:{self.slug}"`, so every downstream consumer that already keys off
`ats:slug` (index, dedup, the priority/cost ledgers, `verify_filters.URL_SHAPES`, the liveness
ledger schema) needs no new code path, only a new row.

The alternative considered and rejected: a single `ats="custom"` scraper class dispatching internally
on `slug` (the company name). That would need its own dispatch table duplicating what `SCRAPERS` in
`registry.py` already is, and would make `detail_pass_atses()`, `DISABLED_ATS`, and every per-ATS
class attribute (`egress_fallback_on`, `detail_workers`, …) apply to all eight at once when they in
fact need to vary — Meta's GraphQL egress ceiling has nothing to do with Amazon's REST one. Eight
first-class `ats` values cost eight registry lines and buy independent tuning for free.

## Consequences

- **No discovery step.** `scripts/discover/` finds tenants on a platform other companies also use;
  there is nothing to find here. Each of these eight gets `data/validate/liveness/{ats}.csv` with
  exactly one hand-entered row, not a crawler output. `update_ledgers priority`/`cost` and
  `load_active_companies()` are unaffected — a population of one is still a population.
- **The word "ats" stretches.** These are not third-party Applicant Tracking Systems; they are each
  company's own in-house system. The dispatch key stays `ats` because that string is what
  `SCRAPERS: dict[str, type[BaseScraper]]`, `board_key()`, and every ledger column already are —
  introducing a second vocabulary word for the same slot would be the near-synonym trap CLAUDE.md's
  module-naming rule warns against, not a fix for the stretch.
- **`alias_key()` is a per-company judgment call, not a blanket default.** The base implementation
  (follow a redirect, compare hosts) assumes a platform with vanity-hostname proliferation; a
  Single source scraper's board has no sibling to alias against, so most of these can return their
  own slug outright. Decide it per scraper against what that company's site actually does, the same
  way `alias_vendor_hosts` is decided per ATS today — don't default all eight without checking.
- **`DISABLED_ATS` does not apply.** These ship live-wired, not arrival-disabled — unlike jazzhr/
  jobvite, cost and tech-yield are unknown per company until measured, so each is judged on its own
  numbers rather than pre-gated.
