# ADR-0060: The work-word patterns carry narrative guards and their own requirement ceiling

**Status:** accepted · **Amended by:**
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (the guard set is now a
flag carried beside each pattern, not derived from the pattern text; the premise that anchored
patterns cannot reach narrative was measured false, so the idiom, ceiling-phrase and 20-year
requirement guards now apply to every pattern — 1,066 descriptions were being answered above 20
years and none was a real requirement) · **Date:** 2026-08-18 · **Amends:**
[ADR-0013](0013-experience-plausibility-guards.md) (its consequence "both tiers enforce identical
plausibility rules" no longer holds in full) · **Relates to:**
[ADR-0009](0009-experience-extraction.md), [ADR-0018](0018-experience-seniority-fallback.md)

## Context

Tier 2's **work-word patterns** are the ones that match without the literal word "experience"
nearby — `"5+ years in software testing"`. They previously required an `of|in|as` connector after
"years". Measured over the 303,983 descriptions held in the ADR-0050 store, that connector was
excluding a large, legitimate class:

```
"4+ years building distributed & scalable software and systems"
"3+ years working with a variety of programming languages"
"3+ years hands-on software engineering"
```

Making the connector optional captures them. It also reopens a class the connector had been
suppressing **as a side effect nobody had written down** — corporate narrative, where "N years" is
company age, founder tenure, or a benefit rather than a requirement:

```
"has spent the last 15 years building one of the most modern …"   -> 15
"Founded in New Zealand 12 years ago, we're working with …"       -> 12
"the founding team spent a combined 40+ years at Palantir …"      -> 40
```

A 150-sample read of newly captured spans put this class at ~2.7% of new captures before guarding.
A `min_years` of 40 is not a cosmetic error: it bands the row `staff` and hides it from every
candidate under 40 years of experience, because `search.py` filters `min_years <= your_years`.

## Decision

The connector becomes optional, and the two costs it was silently paying are made **explicit and
local to the patterns that incur them**:

- `_NARRATIVE_BEFORE` / `_NARRATIVE_AFTER` reject a match sitting in company history
  (`spent`, `combined`, `founded`, `sabbatical`, `vested`, … before; `ago`, `at <Capitalised>`
  after). `_NARRATIVE_AFTER` is **case-sensitive on purpose**: `"at Palantir"` is tenure, but
  `"at a startup"` and `"at the company"` are ordinary requirement prose, and a case-insensitive
  `[A-Z]` discards a real number.
- `_MAX_PLAUSIBLE_REQUIREMENT = 20` rejects a stated requirement above 20 years.
- `from_description` iterates **occurrences**, not just the first match per pattern, so a guarded
  rejection falls through to the next candidate rather than abandoning the description:
  `"Founded 12 years ago. Requirements: 5+ years building …"` still yields 5.

Both guards apply **only to the work-word patterns**, and the index set that selects them is
**derived from the pattern text**, never hardcoded — Tier 2's "ranges before single values" ordering
means a new range phrasing must be *inserted*, and a literal index set would silently re-bind the
guards to the wrong pattern.

This makes the two tiers' plausibility rules deliberately **asymmetric**, which is where ADR-0013's
consequence is amended. `from_field` reads a structured field a source labelled as experience, so
its only real risk is arithmetic nonsense — `_MAX_PLAUSIBLE_YEARS = 50` still covers it.
`from_description` mines free prose, where the failure mode is *genre*, not magnitude: 40 is a
perfectly plausible number that is simply not a requirement. Guarding both tiers identically would
mean either dropping `"25 years of experience"` from an anchored field or leaving prose unguarded.

## Alternatives considered

- **Keep the strict connector.** Safe, and needs no guards — but leaves the measured recall on the
  floor and keeps the exclusion implicit, so the next person to relax the connector reopens the
  same class with no test to catch it.
- **Guard every pattern uniformly.** Simpler to state, but the experience-anchored patterns cannot
  reach narrative (the literal word "experience" has to be nearby), so the guard would be pure risk
  — `"25 years of experience"` is a real, if rare, requirement.
- **One shared ceiling at 20 for both tiers.** Would silently truncate legitimate high values a
  source states in a structured field, and contradicts ADR-0013's reason for the 50 bound.

## Consequences

Description-tier coverage rises and the narrative class is suppressed rather than merely dodged;
new captures sitting near benefit/tenure wording fall from 1.3% to 0.7%. The guards are pinned by
tests, including the case-sensitivity of `_NARRATIVE_AFTER` and the derivation of the work-word
index set — the two failure modes that are silent rather than loud.

ADR-0013's `"N years ago … experience"` xfail is **untouched**: it fires through an
experience-anchored pattern, which by this decision carries no guard.

Nothing here reaches rows already in the index — `min_years` is written at embed time and
`embed_plan` skips embedded ids (see #162).
