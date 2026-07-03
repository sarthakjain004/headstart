# ADR-0013: Experience-extraction plausibility guards — fix Tier 1, defer the Tier 2 anchor

- Status: Accepted
- Date: 2026-07-03

## Context

ADR-0009 built the years-of-experience cascade but shipped with **zero unit tests** (the module was
0% covered — flagged in the June-2026 audit) even though it feeds the live `--max-years` filter, so
every recall-widening edit to its patterns was a blind change to a user-facing filter. Writing
characterization tests surfaced three defects, confirmed by running the functions directly:

1. **Silent truncation.** `_FIELD` captured `\d{1,2}`, so `from_field("100")` matched only `"10"`
   and returned a plausible-looking `ExperienceSpan(10, …)`. The `_MAX_PLAUSIBLE_YEARS = 50` guard
   was effectively dead in Tier 1 — a 3-digit number could never reach it, so the guard could only
   ever fire for 51–99. A `"100 years"` parse artifact became a *valid* 10-year filter value.
2. **Unchecked ceiling.** `from_field("3 to 99")` kept `max_years = 99`, while the equivalent
   `from_description("3 to 99 years experience")` correctly nulled it — Tier 2 had the
   `hi > _MAX_PLAUSIBLE_YEARS` guard, Tier 1 did not. Two tiers that should agree, disagreeing.
3. **Direction-agnostic anchor.** The Tier-2 regex only requires `"experience"` within ~25 chars of
   the number in *either* direction, so `"5 years ago I gained experience"` reads as a 5-year
   requirement.

## Decision

Fix the two Tier-1 arithmetic guards; **defer** the Tier-2 anchor fix and pin it as a test.

- **Truncation:** widen `_FIELD` to `\d{1,3}` so a 3-digit value is captured whole and the
  plausibility guard can reject it (anything real is `< 100` anyway).
- **Ceiling:** `from_field` now applies the same `hi > _MAX_PLAUSIBLE_YEARS` check Tier 2 already
  had, so both tiers enforce identical plausibility rules.
- **Anchor (deferred):** left unchanged, pinned by a **strict `xfail`** test asserting the desired
  `None` result — the suite stays green today and *fails* the moment someone tightens the anchor,
  telling them to delete the marker.

## Why defer the anchor rather than fix it now

The two arithmetic fixes are local and only ever change already-implausible values, so ADR-0009's
measured coverage (82.6% overall, 18.1% from the description tier) is unaffected. Tightening the
anchor is a different risk class: it edits the *core matching regex* whose recall was measured
against `data/jobs/wellfound.csv`, which is gitignored and not on disk. A blind tightening (e.g.
rejecting a trailing `"ago"`, or requiring the number to precede `"experience"`) could silently drop
legitimate matches like `"experience gained over 5 years"` with no way to re-verify recall. The
honest move is to defer until the corpus is available to re-measure — the standing rule being *don't
change what you can't verify*.

## Rejected alternatives

- **Pin all current behavior as-is** (pure characterization net) — would enshrine `100 → 10` as the
  documented contract; a test asserting wrong behavior is a landmine for whoever later fixes it.
- **Fix all three now, including the anchor** — closes the audit finding fully but risks the
  measured description recall with no way to re-verify it here. Deferred instead.
- **Tests only, no source change** — leaves the two clean, local, coverage-safe fixes live in
  production for no reason.

## Consequences

`experience.py` goes from 0% to full unit coverage. `from_field` and `from_description` now enforce
identical plausibility rules. One known false-positive class (`"N years ago … experience"`) remains,
now *documented and test-pinned* rather than silently present, with its fix gated on corpus access.
No change to the ADR-0009 cascade design or the LanceDB schema join.
