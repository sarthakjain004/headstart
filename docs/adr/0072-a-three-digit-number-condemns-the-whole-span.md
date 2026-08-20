# ADR-0072: A 3-digit number condemns the whole span, floor included

**Status:** accepted · **Date:** 2026-08-20 · **Amends:**
[ADR-0013](0013-experience-plausibility-guards.md) (its ceiling rule, for 3-digit ceilings only)
· **Relates to:** [ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md),
[ADR-0060](0060-narrative-guards-for-the-work-word-patterns.md)

## Context

ADR-0013 widened Tier 1's `_FIELD` to `\d{1,3}` **so the plausibility guard could reject a
3-digit value** rather than truncate it. #203 carried the same widening into Tier 2's `_DIGITS`.

For a *floor* that works: `lo > _MAX_PLAUSIBLE_REQUIREMENT` rejects the match outright. For a
*ceiling* it does not. ADR-0013's ceiling rule says an implausible `hi` is dropped and the floor
kept —

> `from_field("3 to 99")` kept `max_years = 99`, while the equivalent … `from_description` already
> dropped it

— which is right when the floor is real. Combined with a 3-digit capture it stops being right,
because a range pattern will now match narrative prose that previously could not match at all:

| sentence | before #203 | after #203 |
|---|---|---|
| `Our team brings 8 to 150 years of combined experience` | `None` | **`min_years=8`** |
| `a leadership team with 10 to 175 years of collective experience` | `None` | **`min_years=10`** |
| `the founding team brings 10 to 300 years of experience` | `min_years=0` | **`min_years=10`** |

These are strictly worse than what they replaced. A bogus `0` constrains nobody; a plausible-looking
`8` hides the job from every candidate with less than eight years. That is the failure ADR-0066
names as the one worth designing against — *"the risk it carries — silently changing an answer that
was already right — is unbounded."*

Before the widening the ceiling group could capture at most two digits, so `8 to 150` never
matched and this class did not exist.

## Decision

**A span whose ceiling is 100 or more is not a requirement, and is rejected whole — floor
included. Below 100, ADR-0013's rule stands unchanged: drop the absurd ceiling, keep the floor.**

The threshold is not a new judgement about magnitude. It is exactly the value the third digit made
reachable, so the rule restores the pre-widening invariant for ceilings while leaving the floor
widening — the thing #203 was for — intact. `3 to 99 years experience` still answers 3.

Rejected: **condemn any span whose ceiling exceeds `_MAX_PLAUSIBLE_YEARS` (50).** Simpler to state,
and it was the first version of this change, but it contradicts ADR-0013's ceiling decision and
breaks its test for `3 to 99` — throwing away a floor that is genuinely stated.

Rejected: **mark the range patterns `guarded=True`** so `_is_narrative` runs. Closer to the real
distinction, since what makes these sentences narrative is "combined"/"collective"/"the team", not
the number's size. Deferred because it changes which sentences every range pattern accepts, needs
its own corpus measurement, and this defect is live now.

## Consequences

Measured over the whole description store, 328,923 descriptions, every ATS: **1 record changes** —
`workday:husqvarnagroup/…:R-17525`, whose text reads *"You have 1-100 years of work experience"*,
from `min_years=1` to no answer. Both mean "no effective constraint", so the practical difference
is nil.

The narrative class this closes is likewise near-absent from today's corpus; it is fixed because
it is wrong, not because it is common. The cost of leaving it is unbounded and the cost of fixing
it is one row.
