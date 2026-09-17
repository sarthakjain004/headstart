# ADR-0165: A soft-404 is not an ambiguous detail loss

**Status:** accepted · **Date:** 2026-09-17 · **Relates to:** [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the eviction-scope exclusion this narrows), [ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (the per-Job mechanism a confirmed closure now reaches), [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (the `MIN_AUTHORITATIVE_SHARE` tolerance this feeds)

## Context

`careers.hcltech.com` (SuccessFactors) has been losing a stable ~15–16% of its listing to
title-less detail pages on every pipeline run for at least eight days, and the loss is
monotonically worsening: 1,323 → 1,981 rows held under ADR-0053's scope exclusion, roughly
+80/day. `MIN_AUTHORITATIVE_SHARE` is 0.99 (`base.py`), so a stable ~84% read never crosses the
tolerance ADR-0121 added, and the Board never re-enters the eviction scope. That has one further
consequence beyond the accretion itself: while a Board sits outside the eviction scope, ADR-0083's
per-Job grace period never runs for any of its ids — including the ones that have genuinely
closed — so a permanently-short Board can never confirm a single real closure. ADR-0053's own
exclusion has no drain of its own, and this is one more path into that same hole.

`_fields_of` (`successfactors.py`) already distinguishes "the origin refused us" (`HTTP {status}`)
from "the page loaded but nothing recognisable came back" (`"200 without a parseable title"`) —
the fix that PR #266 shipped for the false-eviction root cause in
`docs/pipeline/2026-08-23_false-board-eviction-root-cause.md`. Both labels currently count
identically against the authoritative-share calculation, because both were, at the time, equally
ambiguous: neither said anything about *why* the page had no title.

**Verified live, 2026-09-16/17** (not reasoned from the code): a title-less HCLTech page's own
`<link rel="canonical">` never gets a slug/id filled in — `.../job///` instead of the real
`.../job/Real-Title/158715-en_US/` a live posting resolves to — and carries no JSON-LD and no CSB
microdata either. Every title-less page checked across two independent samples (120 URLs on
`careers.hcltech.com`, 13/13 matching; a smaller sweep on `careers.wipro.com`, 1/1 matching)
carried this exact canonical, byte-length-identical to every other one (111,526–111,527 bytes for
HCLTech). A live posting never produces this shape. This is the platform's own generic template
rendering with nothing job-specific injected — SuccessFactors' soft-404 for a requisition id that
no longer resolves, served at HTTP 200 rather than a real 404 — not a parser gap, a different
template, or a transient glitch. A final live check on the real board measured what this changes:
of 150 sampled pages, 119 had a title, 31 hit this exact soft-404 shape, and **zero** were any
other kind of title-less failure — old share 79.3% (truncates), new share **100.000%** once the
soft-404s are set aside.

## Decision

**A title-less page is checked for this specific soft-404 shape, and only counted against the
Board's authoritative share if it isn't one.** `_is_confirmed_gone(page)` matches the canonical
link's empty-segment shape (`_EMPTY_JOB_CANONICAL`); a match gets its own loss cause,
`_CONFIRMED_GONE_CAUSE`, kept apart from `"200 without a parseable title"` so the two remain
independently visible in `report_detail_gaps`'s per-Board log line.

`fetch_raw`'s authoritative-share math then excludes confirmed-gone pages from **both** sides of
the ratio: `expected = len(tech_listed) - confirmed_gone`, and only the remaining `ambiguous`
losses are measured against it. A Board whose entire loss is confirmed-gone reads 100% — fully
authoritative — rather than being judged against a denominator that includes ids the platform
itself already says don't exist.

**The Job is still dropped from the returned list exactly as before.** `_titled_fields` still
returns `None` for a confirmed-gone page, `parse()` still drops it for having no title. Nothing
about *that* path changes — what changes is only whether the loss counts toward the Board's
overall authority. Because the Board now (usually) stays authoritative, that dropped id reaches
ADR-0083's ordinary per-Job grace period — absent this run, unconfirmed, evicted only on a second
consecutive absence — instead of being shielded, along with everything else on the Board, by
ADR-0053's scope exclusion having no drain.

## Rejected alternatives

- **A new cross-run "confirmed-gone ids" ledger**, tracking which specific ids have soft-404'd
  before and excluding only *repeat* offenders. Considered first, since the TODO register's
  original framing ("let ids that are repeatedly unavailable... leave ADR-0053 exclusion") implied
  per-id memory. Rejected once the live signal turned out to be unambiguous on a single read: the
  canonical shape doesn't need a second observation to trust, so a persistent ledger would add a
  new state file, a new merge/state-fetch wiring path, and a new failure mode for no measured
  benefit over checking the page in front of you.
- **Lowering `MIN_AUTHORITATIVE_SHARE`** for SuccessFactors, or for this Board specifically.
  Rejected: it would also swallow genuinely ambiguous losses (a real parser gap, a WAF wall) on
  the same Board, which is exactly the protection ADR-0121 exists to keep. The fix needs to
  distinguish *why* a page is missing, not tolerate more missing pages indiscriminately.
- **Treating a confirmed-gone id as an immediate, authoritative eviction**, bypassing ADR-0083
  entirely on the theory that the soft-404 is already stronger evidence than an ordinary listing
  absence. Rejected on parsimony: ADR-0083's two-strike grace period is already the mechanism this
  repo trusts for "this id didn't show up this run," it costs nothing extra to route through, and
  skipping it would need its own justification for why this one path deserves less caution than
  every other closure signal in the pipeline.

## Consequences

- `careers.hcltech.com` (and any other SuccessFactors tenant carrying this same platform-level
  soft-404 shape — confirmed on `careers.wipro.com` too, so this is not HCLTech-specific) can now
  re-enter the eviction scope on a normal run, and its genuinely closed postings can finally drain
  instead of accreting behind ADR-0053 forever.
- **Scoped to SuccessFactors.** The canonical-shape signal was verified only on this ATS; nothing
  here claims another platform's soft-404 (if it has one) looks the same. A future report on
  another ATS needs its own live measurement, not an assumption that this pattern generalises.
- **This does not fix ADR-0053's exclusion having no drain in general** — it closes the specific
  path where SuccessFactors' own soft-404 was feeding that hole. A Board excluded for a genuinely
  ambiguous reason (a real parser gap, a WAF wall, a listing that's actually short) still has no
  drain, and still needs the general fix the TODO register's issue 3/15 discussion points at.
- A future SuccessFactors template change could alter or drop this canonical shape. If the
  soft-404 rate on a tenant suddenly reads as `"200 without a parseable title"` again instead of
  the confirmed-gone cause, that is the signal this detector has gone stale and needs re-measuring
  against a live sample, not silently re-widening the ambiguous bucket's tolerance.
