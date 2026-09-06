# ADR-0112: Publish the index's own limits in the product, measured live

**Status:** accepted · **Date:** 2026-09-07 · **Extends ADR-0084's counting rule to coverage; makes README §"What this optimises for" reachable from the UI**

## Context

The README states a commitment the product does not yet keep:

> **Publish the limits next to the result.** The retrieval score ships with the two reasons not to
> over-trust it. Coverage tables say what is excluded and why. A number without its caveat is
> treated as a defect.

That is kept scrupulously *for developers* — in ADRs, in the schema table, in the module
docstrings — and only patchily for users. The Search tab does carry real instances of it: the sort
caveat, the salary "never FX-converted" tip, the experience filter's "jobs that don't state a
requirement are kept", the Blocking-filter empty state. But a user cannot find out, anywhere in the
product:

- that the index is deliberately **tech-only** and **English-only**, so an absence may be a policy
  rather than a gap;
- that `posted_at` is the *company's* date, is missing on a large share of rows, and is not
  always ISO;
- that `first_seen` is ours and is null on rows indexed before ADR-0031, so a "new" tag is a
  floor rather than a fact;
- that a closed posting leaves the index only after **two consecutive** scrapes of its Board miss
  it (ADR-0083), which is why a dead link is possible and roughly how long one can persist;
- what the match percentage on a card is a percentage *of*.

Every one of those is written down already. None of it is reachable from the running app.

The numbers are the harder half. README §"The served table" carries measured coverage figures with
dates attached — `posted_at` null on 13.4%, `first_seen` null on 77% of a 1,000-row sample, both
measured 2026-08-18 — and those are already stale by the repo's own freshest-data rule. Copying
them into the UI would ship a number that decays silently, which is the defect the commitment
names.

## Decision

**Add a Data tab that states the index's scope, its known gaps, and how to check any of it — and
compute every number on it from the served table at request time rather than writing it down.**

### Coverage is measured, not asserted

`JobSearch.coverage()` returns the share of the served table carrying each field a user might
otherwise assume is always present: `posted_at`, `first_seen`, a salary, a stated experience
requirement, a stored description. It is built from `count_rows(filter=…)` — the same primitive
ADR-0084's facet counts use, measured at 4–6 ms against a 316,606-row table — so the whole panel
is a handful of counts, not a scan.

This is the point of the ADR. A number the product computes about itself cannot go stale, cannot
be rounded up in a later edit, and gets *worse* on the page when the pipeline gets worse — which
is exactly the incentive a trust surface should have.

Cached per process after the first call. The table is immutable between deploys, the counts move
only when a new index is synced and the Space restarts onto it, and paying ~6 counts once per boot
rather than once per visitor keeps a free-tier dyno honest.

### The prose states mechanism, and links to the decision behind it

Each claim on the tab names the mechanism and links the ADR that argues for it, so a sceptical
reader ends up at the reasoning rather than at more assertion. Where a limit has no fix planned it
says so; a limits page that reads as a roadmap is marketing again.

### Rejected: a static "About the data" page with the README's numbers copied in

Cheapest, and wrong for the reason above — it ships figures dated 2026-08-18 that no process
updates. The repo has already been bitten by exactly this (a coverage analysis run against a
32,179-row local snapshot when the served table held 287,144).

### Rejected: put the caveats only on the controls they qualify

Already done where it fits, and kept. It does not cover the claims that belong to no single
control — scope, eviction, provenance — and a user who wants to decide whether to *trust the whole
thing* should not have to reconstruct that from six tooltips.

### Rejected: fold it into the existing footer line

The footer already carries the one-sentence version and stays. A limits page does not fit in a
sentence, and lengthening the footer would cost the sentence its readability without gaining a
place to put the numbers.

## Consequences

- One new method on `JobSearch` and one new route. Both live behind the wall, like everything else
  the searcher serves.
- The tab is a *claim surface*: a change to the pipeline that invalidates a sentence there is a
  change that must edit it. The linked-ADR discipline is what keeps that cheap — if the ADR is
  amended, the link still lands on current reasoning.
- Coverage counts are computed against the served table only. They say nothing about Boards not
  yet scraped or Jobs never indexed, and the tab says so rather than letting a high percentage
  imply completeness.
