# ADR-0357: The owner keeps the smallest stated experience, and get_job names the others

**Status:** accepted · **Date:** 2026-09-29 · **Re-affirms:**
[ADR-0079](0079-smallest-stated-experience-requirement-wins.md) · **Relates to:**
[ADR-0018](0018-experience-seniority-fallback.md) (seniority calibrated to the data),
[ADR-0061](0061-refreshable-metadata.md) (the version bump),
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (measure changed values),
[ADR-0337](0337-a-derived-field-reads-no-company-history-and-says-what-it-annualised.md) (level
ladders and derived fields printed as fact)

## Context

The round-4 critique of the MCP server rated stated experience its P0 (P0-1). Because the smallest
stated floor answers (ADR-0079), Google's "Senior Software Engineer" ("5 years of experience with
software development … 3 years … 1 year of experience with software design") is served "1+ yrs",
149 of Google's 394 "Senior" titles pass `max_years: 1`, and Capital One's Sr. Managers fill a new
graduate's first search. A rule reading the posting's overall requirement (the largest stacked
floor, the smallest alternative path) was built and measured: it moved 54,824 served rows and took
Google's senior titles passing `max_years: 1` from 149 to 0 (#984).

The owner saw that evidence and, on 2026-09-29, **kept ADR-0079**: the smallest stated floor wins,
because a user must not miss a job they could be qualified for, and a posting's clauses are as often
alternatives as demands. #984 was closed.

The critique's two P2-1 derived-field gaps do not touch that choice: Netflix's level titles on role
nouns `_LEVEL` does not hold ("Business Security Partner (L5)") read no experience, and Knowfinity's
US "Software Engineer" was served "INR 100,000–130,000 a year", PyjamaHR's default currency.

## Decision

1. **ADR-0079 stands.** Which clause wins does not change.
2. **Disclosure, not a new answer.** `get_job` carries the description, so it reads the description
   again (`experience.stated_floors`, the distinct floors of the Tier-2 pass that answered, "up to
   N" ceilings left out) and, when there are several and the served floor is their smallest and no
   field states the years, adds one line: "States 1, 3 and 5 years in separate clauses; HeadStart
   shows the smallest (ADR-0079), so check which applies to you." A reader who sees "1+ years" on a
   senior role learns why, and what else the posting asks, without the filter hiding the job from
   anyone. No served column is added. On the served table (v326), 78,471 of 499,841 rows would
   carry the line.
3. **Netflix reads on its own ladder, only where nothing is stated.** Netflix's postings that
   state a number state medians of 3, 5 and 9 years at L4, L5 and L6 (n=11, 35, 21), where
   `_LEVEL_YEARS` gives 7, 7 and nothing, and "(LN)" titles at other employers state 6, 5 and 10:
   the ladder is the employer's. `from_seniority` takes the company and maps Netflix's "(LN)" or
   trailing level number on any role noun, levels 4-6. It is Tier 3, so a number the field or the
   description states always wins.
4. **A small rupee figure on a job placed wholly abroad is dropped** (`salary.placed`, applied in
   `derived_meta` and `update_meta`): INR below ₹5 lakh a year on a row whose every place
   `country_gazetteer.classify` names is outside India. All 19 such rows were read: 7 are dollar or
   euro figures under an ATS's default currency (Knowfinity; GreyOrange's Redwood City
   "200000-215000 INR"), 10 monthly Gulf pay read as annual, 2 unclear. Dropping every INR figure
   abroad was measured and rejected: at 40 of the 70 such rows, 26 were right rupee values (an
   Indian agency's quote for a Gulf job, an Indian job whose location the ATS misplaced). The tools
   then quote the raw salary field. `country_gazetteer` is now a derivation input.
5. **`DERIVATIONS_VERSION` 26** for 3 and 4 (#966 holds 25; main is at 24).

## Measured (ADR-0066)

Served table v326, 499,841 rows, read off HF on 2026-09-29, with the description store pulled the
same day; old is main's code. Experience: 91 rows move, all Netflix, none at the field or regex
tier: none → seniority 16, seniority down 73 (L4 7 → 3, L5 7 → 5), up 2 (L6 7 → 9). Salary: 19 rows
move, field INR → none. Main's code reproduces the served experience value on 499,675 rows, so
v23's sweep has reached the table; v26 re-derives every row once and subsumes the unswept v24 and
v25.

## Rejected alternatives

- **The overall-requirement rule of #984.** Measured and read (54 of 60 hand-read rows better), and
  declined by the owner: it hides a posting from a candidate its own text may admit.
- **A seniority override of a stated number.** Replaces what a posting states with a guess.
- **A served column of every stated floor.** A schema change for something `get_job` can read from
  the description it already holds.
