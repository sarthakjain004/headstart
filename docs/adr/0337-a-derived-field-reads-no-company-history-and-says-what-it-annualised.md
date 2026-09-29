# ADR-0337: A derived field reads no company history, and says what it annualised

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0018](0018-experience-seniority-fallback.md) (seniority calibrated to the data),
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (measure changed values),
[ADR-0079](0079-smallest-stated-experience-requirement-wins.md) (the smallest stated floor
wins), [ADR-0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md) (the salary
cascade), [ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) (materialized
employment-type flags)

## Context

The round-3 critique of the MCP server found derived fields printed as facts when they were wrong
(P1-7), and employment types that set no flag (P2-5):

- Monzo's "Associate Software Engineer - Intern" read "10+ years" from "our product offering has
  grown a lot in the last 10 years". A new graduate asking for `max_years: 0` never saw it.
- Netflix's "Software Engineering 5" and "Software Engineering L5" roles read no experience, so
  they led a `max_years: 0` list sorted by salary at $388k–558k.
- "USD$225.00 - USD$275.00 per hour" was served as "USD 572,000 a year": the range failed on the
  code glued to the ceiling's "$", and the bare hourly pattern read the ceiling alone as the rate.
  Nothing said the figure was annualised.
- Rippling's "SALARIED_FT" and plain "Regular" set no employment-type flag, so a filter on
  full-time dropped them.

## Decision

**Experience (`jobs/experience.py`).**

1. A number right after "in / over / during / within / throughout the last / past" is a window of
   time, not a requirement, for every pattern. "Minimum of the past 2 years working with M365"
   keeps its 2: the preposition is what separates the two.
2. Three company-history idioms join `_NARRATIVE_SPAN`, each with no filler between the years and
   the idiom: ", we" / ", our" (lower case, after a comma: a description flattened to one line
   loses its full stops, and "8+ years We are seeking" is a requirement); "years of history" /
   "of heritage" with nothing between (so "of work history" and "of driving history" stay
   requirements); and "years of … growth" where "growth" ends the phrase (so "growth marketing"
   stays one).
3. "Engineering" joins `_LEVEL`'s role nouns, so "Software Engineering 5", "Software Engineering
   L5" and "Software Engineering II" read their level on the same ordinal mapping. Levels 1–3 of
   such titles state medians of 3, 5 and 6 years (n = 25, 40, 3), at or above the mapped 0, 3 and
   5. Netflix's level-5 postings state no number; bare "L5" titles elsewhere state a median of 8
   (n = 31) against the mapped 7. Bare "(L<n>)" titles were not added: Amazon's L4 is entry
   level and the bare-L4 median is 5 against a mapped 7, so the ladders disagree too much.
4. The entry tier is tried before the associate tier. The 759 served titles holding one word of
   each (and no higher tier) state a median of 1 year (n = 313), the entry tier's own, not 3.

**Salary (`jobs/salary.py`).**

5. A range's ceiling may repeat its currency glued to its symbol ("USD$275.00"), or follow "--"
   or "-to-", in `_BARE_RANGE` and `_LABELED`; a first side "USD$225.00" reads in `_LABELED` too.
6. `SalarySpan` carries `period`, the period Tier 2 read the figure in ("hour", "day", "week",
   "month", "year"). It is not part of equality and Tier 1 leaves it None. It is **not** served:
   a period column is a schema change for the owner. `get_job` re-runs `salary.extract` on the
   description `/job` returns and, when the re-run gives the served figure, says "annualised from
   an hourly rate at 2,080 hours a year". When it does not (a description cut at 12,000
   characters, a row not re-derived yet) it says nothing. `search_jobs` rows are unchanged.

**Employment type (`search_filters/employment_type_filter.py`).**

7. Of the served table's 130 most frequent raw values, those that set no flag are mapped where the
   meaning is unambiguous: "_ft" / "_pt" (Rippling), "regular" and "cdi" as "permanent" is read
   (full-time unless "part"), "fte" (unless "after", for "afternoon"), "temps plein", "tiempo
   completo", "vollzeit" and "全职" as full-time; "fixed" (fixed-term) as contract; "co-op" and
   "coop" as internship. iCIMS's "OTHER" (330 of 4,840 titled intern), Radancy's "F", "Temporary"
   (136 of 480 titled intern), "Employee", "Salary" and the like stay unread. An underscore term
   escapes LIKE's wildcard in the SQL clause (`ESCAPE '\'`, accepted by LanceDB and SQLite). No
   title rule reads an internship typed "Full time" at its source (NatWest): that is the ATS's
   value.

8. `DERIVATIONS_VERSION` goes to 23, so `update_meta` re-derives every stored row. The flags need
   no bump: `index._refresh_metadata` recomputes them against every row on each sync.

## Measured

On the served table read off HF on 2026-09-29 (version 41, 500,134 rows) joined to the
description store pulled the same day (498,027 with a stored row). The HEAD extractors reproduce
the served values on 500,123 experience rows and all 500,134 salary rows, so the diff below is
this change alone.

- **Experience, 878 rows move.** 444 seniority 3 → 0 (an associate-tier word beside an entry-tier
  one); 167 regex floors rise, each a window ("within the last 2 years") that had undercut the
  stated floor ("Minimum 8 years … within the last 2 years" read 2, now 8); 99 regex → seniority
  and 71 regex → none, each a window or company history (Monzo's "last 10 years" on 31 of its postings,
  "For the past 20 years, we have" on 33 postings of one Oracle pod, "polygraph within the last 5 years",
  Amazon's "Experience in development in the last 3 years" read as 3 on SDE I roles); 97 none →
  seniority from "Engineering <level>" titles (Netflix's four level-5 postings now read 7+).
  Every regex → none row was read. Coverage by tier (`scripts/enrich/experience_coverage.py` over
  the joined rows) goes field 33,651, regex 304,052 → 303,882, seniority 88,608 → 88,804, none
  73,823 → 73,797: 85% either way. The `--misses` sample left two title forms unread on purpose:
  "Co-Op" and a bare "(L3)" (above).
- **Salary, 142 rows move, all Tier 2.** 120 none → regex: the "USD$… - USD$…" and "CAD$…" ranges
  that had matched nothing (BlackRock on Workday and Radancy, Perseus, Arc'teryx). 22 regain a
  low or high end: Perseus's $225–275 an hour now reads 468,000–572,000, and agency's "$6-to-$65
  per hour" 12,480–135,200 where it read a floor of 12,480.
- **Employment type:** rows with no flag fall from 159,795 to 150,737. 8,716 gain full-time
  ("Regular" 4,894, "SALARIED_FT" 2,711), 705 contract, 49 internship, 29 part-time. The SQL and
  Python verdicts agree on all 1,875 distinct raw values.

## Consequences

- The served values change only after the next pipeline run's `update_meta` sweep; until then,
  `get_job` says nothing of the period for a row the sweep has not reached.
- The version bump is a counting change for Trends' levels (ADR-0336 keeps it out of turnover).
- Deferred: a served period column (owner: schema), and an employment type read from a title.
