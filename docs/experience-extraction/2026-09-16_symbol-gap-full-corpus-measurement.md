# The `#`/`+` gap fix — full-corpus old-vs-new measurement

**Date:** 2026-09-16. The `_GAP`/`_WORDS` change (see `_WORDS`'s comment in `experience.py` and
`DERIVATIONS_VERSION` v12) was found via a live-API investigation into a handful of ATSes, which
only measured *new coverage* on a bounded, missing-`min_years` sample (51 rows across 9 ATSes).
CLAUDE.md's own rule for any `experience.py` pattern change is stricter than that: measure changed
*values*, not only coverage, and bucket every record old-tier → new-tier — exactly the discipline
ADR-0066/0079 exist because of. This is that fuller measurement, run against the full local served
table before merging.

## Method

Loaded two standalone copies of `experience.py` — `origin/main`'s (pre-fix) and this branch's
(post-fix) — and ran `extract(field, description, title)` under both across every row of the local
`jobs` table (459,291 rows, snapshot 2026-09-15). A cheap, *exact* (not approximate) pre-filter cut
this to the 268,537 rows whose `description`/`experience`/`title` contain a literal `#` or `+`
anywhere — the only rows the character-class widening can possibly touch, since text with neither
character exercises identical regex behavior under both versions.

## Result

```
candidate rows (contain # or +): 268,537
both_none                        21,353
unchanged_value (same answer)   247,085
new_coverage (None -> value)         39
value_changed (value -> value)       60
regressed (value -> None)             0
```

Zero regressions. 39 rows gained a Tier-2 answer where there was none before (a narrower slice of
this than the 51-row top-10-ATS figure, since that scan covered only currently-*missing* rows
across 10 specific ATSes; this is the full served table, all ATSes, including rows that already had
*some* answer).

## The 60 value-changed rows are corrections, not regressions

Every one of the 60 follows the same shape: the *same* `#`/`+` gap bug was also blocking Tier 2 on
these rows, so the old code silently fell through to Tier 3's generic seniority estimate (`old=5`
in all but one case — the flat "Senior"/"Mid-Senior Level" floor) instead of the number the posting
actually states. The new code recovers the real, more specific number, and per the cascade
(`extract()`'s docstring: "Concrete numbers always win over the seniority fallback") that's exactly
the intended precedence — Tier 2 was just unable to fire.

Manually read every ambiguous one in full (not just the 350-char snippet the scan printed) rather
than trusting the diff at a glance:

- `smartrecruiters:krgtechnologyinc:115029350` — field `"Mid-Senior Level"` (→ old 5), body "need
  **2+ years** C# Developers" → new 2. The exact bug shape the investigation started from.
- `smartrecruiters:VTechSolution1:106622402` — body states three requirements ("5+ years... Site
  Core", "7+ years... .Net, C#", "**3-5 years** MVC"); old fell to seniority-5, new correctly
  reads the smallest stated floor (3), per ADR-0079.
- `smartrecruiters:everience:744000134479535` — field `"Mid-Senior Level"` (→ old 5), body
  "Experience with C# and .NET Core (**2 - 5 years**)" → new 2, the parenthetical pattern reaching
  through a `C#` in its own gap.
- `smartrecruiters:NorthStarStaffingSolutions1:80861927` — body "**10 Years +** in Product Dev...
  Automotive Elec. Industry" → new 10, correct.
- `smartrecruiters:TheCulperGroup:743999702913312` — body "Extensive Hands on experience with
  .Net/C# (**more than 10 years**)" → new 10, correct.
- `smartrecruiters:AlphaTechnologiesIncUSA1:86855625` — body "Minimum **3 years** MS Excel/VBA and
  C#, .Net development experience" → new 3, correct (smallest of several requirements stated).
- `greenhouse:motional:6655571003`, `greenhouse:motional:7789999003` — both state a `C++`-adjacent
  floor ("**5+ years** of C++ software development", "**3+ years** of C++ software development")
  smaller than the seniority-tier estimate the old code fell back to; new values (5, 3) are the
  correct smallest-stated floors.

No case read as a false positive — every new value traces to a real, stated number in the posting,
usually the smallest of several the description gives (matching `_scan`'s documented behavior).

## Why the pre-filter is exact, not approximate

The diff only *adds* `#`/`+` to two character classes (`_GAP`, `_WORDS`); it changes nothing else.
On any input containing neither character, both regex sets are byte-identical automata over that
input, so their match results cannot differ. This makes the 268,537-row candidate set a complete
superset of every row that could possibly change — not a sample, a partition.
