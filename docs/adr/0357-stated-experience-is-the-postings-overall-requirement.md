# ADR-0357: Stated experience is the posting's overall requirement, and a rupee figure answers to the place

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0079](0079-smallest-stated-experience-requirement-wins.md) (the smallest stated floor wins) ·
**Relates to:** [ADR-0018](0018-experience-seniority-fallback.md) (seniority calibrated to the
data), [ADR-0061](0061-refreshable-metadata.md) (the version bump that re-derives stored rows),
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (measure changed values),
[ADR-0337](0337-a-derived-field-reads-no-company-history-and-says-what-it-annualised.md) (level
ladders), [ADR-0350](0350-experience-widenings-run-last-and-a-ceiling-an-education-an-age-or-a-window-is-not-a-floor.md)
(the third pass and the ceiling, age and education guards this builds on)

## Context

The round-4 critique of the MCP server rated stated experience its one P0 (P0-1). ADR-0079 answers a
description stating several requirements with the smallest, so Google's "Senior Software Engineer"
("5 years of experience with software development … 1 year of experience with software design")
was served as "1+ yrs". 149 of Google's 394 "Senior" titles passed `max_years: 1`, Capital One's
"Sr. Manager" and "Senior Lead" roles filled a new graduate's first search, and Mastercard's
"Principal DevOps Engineer" read 2+. The same number feeds `role_requirements`' year bands,
`company_profile`'s levels and the Trends level bands.

ADR-0079 chose the smallest because some descriptions state **alternative paths** ("12+ years, or
10+ with a PhD"), and reading those at their largest hides a job from a candidate the posting
invites. It knowingly over-included the commoner shape, **stacked** clauses, where every clause is
required and the largest is the real floor. Read on the served table, the stacked shape is most of
it: 82,673 descriptions state more than one floor, a prototype of this rule moved 58,859 of them up,
and of 90 moved rows read by hand 76 read better.

Two smaller gaps came with it (P2-1): Netflix's level titles on role nouns `_LEVEL` does not hold
("Business Security Partner (L5)", "Creative Tech Researcher 5") read no experience, and
Knowfinity's US "Software Engineer" was served as "INR 100,000–130,000 a year", PyjamaHR's default
currency on a US job.

## Decision

**1. The overall requirement answers (`experience._overall`).** Every surviving Tier-2 match is
still collected; the choice among them changes:

- A match under a "Preferred qualifications" heading (the later of a preferred and a required
  heading before it wins), after "preferred", or tagged "(Preferred)" / ending "… preferred." is
  set aside while any required floor is stated.
- A description states **alternatives** when its matches name a degree in their own clause, an
  advanced degree before or after the number ("8+ years with a Master's degree"), are introduced by
  "or" / "Option 2:" / "Alternatively" / a substitution, or name years offered in place of a
  credential ("PE registration, or 8+ years"); when those disagree on the floor, or one names an
  advanced degree, or a range from 0 is stated as general experience or beside an entry level
  ("Junior: 0 - 3 years … Senior: 8+ years"), the answer is the **smallest alternative**. ADR-0079's
  owner call ("I don't want any user to miss a job they could be qualified for") holds exactly where
  the posting offers another way in.
- Otherwise the answer is the **largest required floor**: a candidate must meet every stacked
  clause, so the posting's floor is its largest, and a candidate below it is not qualified by the
  posting's own text.
- An "up to N" ceiling answers only when it is an alternative path or no required floor is stated,
  which retires ADR-0079's measured consequence that privacy boilerplate ("kept for up to 2 years")
  outvoted a stated requirement.
- A range's second number read again alone ("three (3) to five (5)" collapsed by ADR-0350's third
  pass) is dropped, so it cannot outrank its own range.

**2. The employer's own tenure is not a requirement.** "We bring more than 15 years", "our team has
over 20 years" are refused immediately before the number, like ADR-0337's windows. It lost to any
real requirement under the smallest reading; under the largest it would win.

**3. Netflix reads on its own ladder.** A ladder is the employer's: Netflix's postings that state a
number state medians of 3, 5 and 9 years at L4, L5 and L6 (n=11, 35, 21), where `_LEVEL_YEARS`
gives 7, 7 and nothing, and "(LN)" titles at other employers state 6, 5 and 10. So
`from_seniority` takes the company, and `_COMPANY_LADDERS` maps Netflix's "(LN)" or trailing level
number on any role noun, levels 4-6 only (the levels its postings measure). The company reaches the
cascade from `derived_meta` and `update_meta`, beside the title. No other employer is mapped: the
next largest users of level titles (Capital One, Lam Research, Northrop Grumman) already read by
`_LEVEL` or state numbers, and a ladder joins only with its own medians.

**4. A small rupee figure on a job placed wholly abroad is dropped (`salary.placed`).** An INR
figure whose top is below ₹5 lakh a year, on a row whose every place `country_gazetteer.classify`
names is outside India, serves no salary; the tools then quote the raw field. Of 7,631 served INR
rows, 70 sit on jobs placed wholly abroad; all 19 below the line were read, and 17 serve a wrong
annual figure: 7 are dollar or euro figures typed under an ATS's default currency (Knowfinity's US
"100000-130000 INR per-year", GreyOrange's Redwood City "200000-215000 INR" three times, AuxoAI's
"$120,000 - $160,000" served as INR, Phizenix's Reston "100000-120000 INR", IndiSquad's Milan
"20000-28000 INR per-month") and 10 are monthly Gulf pay read as annual ("₹1–₹2 Lakh" for Doha);
2 are unclear. Dropping every rupee figure abroad, the brief's first form, was read at 40 of the 70
and would have removed more right values than wrong ones: 11 were an Indian agency's rupee quote for
a Gulf job and 15 an Indian job whose location the ATS misplaced (zwayam's "Catalina Foothills,
Arizona" for Kotak Mahindra), against 5 wrong currencies and 9 unclear. Other single-country currencies were read and left:
CAD's mismatches are mostly the gazetteer reading "CA" as California, and AUD's are Australian
employers paying offshore staff in AUD.

**5. `DERIVATIONS_VERSION` 25 → 26.** Every change above moves already-stored rows.

## Measurements

Served table version 326 (499,841 rows, 497,733 with a description) read off HF on 2026-09-29,
joined to the description store pulled the same day. Old is ADR-0350's `extract()` (PR #966's head,
`da572951`), new is this change; per ADR-0066, old tier → new tier and same-tier moves:

| | rows |
| --- | --- |
| changed | 54,824 (11.0%) |
| regex, floor rises | 54,301 |
| regex, ceiling only | 421 |
| regex, floor falls | 1 |
| regex → none / regex → seniority | 7 / 3 (employer tenure: "We have 3 years of proprietary data", Akkodis's "over 20 years") |
| none → seniority | 16 (Netflix levels) |
| seniority falls / rises | 73 / 2 (Netflix L4 7 → 3, L5 7 → 5; L6 7 → 9) |
| field tier | 0 |

Titles with Senior, Sr, Staff, Principal, Manager, Director or Lead and no Associate or Junior that
pass `max_years: 1`: 6,435 → 3,273 of 201,221; "Senior" titles 2,778 → 1,367. Google's "Senior"
titles 149 → 0 of 394 (all seniority-class titles 165 → 6 of 970); Capital One 478 → 18 of 889;
Mastercard 2 → 2 of 345, and its "Principal DevOps Engineer" reads 8 (was 2). The 3,273 that remain
are mostly a degree ladder's PhD path ("PhD and 1+ year", Qualcomm and Northrop Grumman: an
alternative, left at its smallest on purpose), postings that state ≤1 year themselves, and
structured fields; a seniority override of the stated number was considered and rejected below.

**Read by hand.** 90 changed rows across three prototype iterations (76 better, 10 worse, 4
neutral), then 60 on the rule as shipped: 54 better, 4 worse, 2 neutral. Three of the four worse
were fixed and pinned by tests ("MS … and 4 years" as a ladder rung, "Or a secondary educational
qualification with 5 years", "preferred 4 years"); the one left is a two-level posting that names its
senior variant in prose ("For Senior Engineer more than 10 years"), read at 10 where its base level
asks 7. Earlier residues that stay: a posting hiring at several levels with no entry range and no
level label ("5-8", "9-15", "12-16" as progression bands) reads its largest, and "Level 1 - Two
years … Level 3 - Six years" is misread as ranges by `_RANGE_TAIL` (both old and new are wrong).

## Interaction with the sweeps in flight

`update_meta` re-derives every stored row once when `DERIVATIONS_VERSION` exceeds the stamped one,
checkpointed so a partial sweep resumes, and a higher version restarts the sweep over rows stamped
lower (`test_new_version_resweeps_checkpointed_rows`). v23's sweep, reported in progress at ~312k of
~524k rows during the round-4 critique, had finished by version 326: main's code reproduced the
served experience value on 499,675 of 499,841 rows. v24 (ADR-0347) and v25 (ADR-0350) are not yet
swept; v26 subsumes both, so the first run after this merges re-derives every row once under all
three changes. Until that sweep reaches a row, the row serves its v23-era value.

`country_gazetteer` is now a derivation input (the salary rule reads it): a change to it that moves
which rows are placed wholly abroad needs a bump like any extractor change.

## Rejected alternatives

- **The largest floor, full stop.** Simple, and wrong on every alternative-path posting: the
  education ladders ("Bachelor's and 2 years OR Associate's and 6 OR High school and 8") would read
  8. ADR-0079's reason for the smallest is real; it applies to alternatives, not to stacks.
- **The first stated floor.** Position again decides the answer, which is what ADR-0079 removed.
- **Keep the smallest and override with the title's seniority** (the critique's suggested guard).
  It would override a number the posting states with a guess, in both directions, and the numbers
  that remain low on senior titles are mostly the posting's own alternative paths. The seniority tier
  stays a fallback for descriptions stating nothing (ADR-0018).
- **A per-level map for every employer.** Only Netflix's ladder is both unread and measured; the
  others either read through `_LEVEL` or state numbers.
- **Drop or flag every single-country currency on a job placed abroad.** Measured above: most such
  rows are right-currency rows on misplaced locations or offshore pay. A flag would be a new served
  column, which is the owner's call (the served period column was already declined).
- **An LLM tier reading AND from OR.** Still deferred (ADR-0009); the regex reading above is
  measured, and it is what the filter needs now.

## Consequences

- A new graduate's `max_years: 0` or `1` search stops leading with senior roles whose descriptions
  state a senior floor; eval task t37 checks it by title.
- Level mixes shift senior in `role_requirements`, `company_profile` and the Trends level bands the
  run after the v26 sweep: an extraction step, not a market change.
- Rows whose description alone states a requirement are admitted to fewer searches. Where a posting
  offers another way in, the smallest path still admits.
