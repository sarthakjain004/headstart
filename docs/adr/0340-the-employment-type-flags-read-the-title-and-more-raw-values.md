# ADR-0340: The employment-type flags read the title and more raw values

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0337](0337-a-derived-field-reads-no-company-history-and-says-what-it-annualised.md) (which mapped
the raw values the flags read as none, and left "Temporary" and "F" unread),
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md), [ADR-0193](0193-one-module-per-materialized-search-filter.md)

## Context

The 2026-09-29 audit of the served table (v18, 500,167 rows) found that 11,993 rows carry `intern`,
`internship` or `interns` as a whole word in the title and `is_internship` was True on 2,639 of them
(22%): the flag read only the raw ATS string, Workday says "Full time" for an intern and Greenhouse says
nothing. ADR-0337 then mapped the ambiguous-free raw values but kept the flag a function of the raw value
alone, and left "Temporary" (481 rows) and Radancy's "F" (Applied Materials, 1,009 rows) unread as
ambiguous.

Measured against the descriptions of those rows: "F" rows say "part-time" 0.2% of the time and "full
time" 9.3%, against 0.8% and 9.3% for stated full-time rows (stated part-time rows say "part-time" 22.1%).
"Temporary" is time-limited like a contract.

## Decision

`EmploymentTypeRule` gains `whole_values` and `title_pattern`. `whole_values` lists whole values
(lowercased, compared as written) for codes too short to be a substring: `f` and `ft` would match "soft"
and "left". Not trimmed: Lance answers `TRIM(lower(x))` with "not supported SQL" (lance-datafusion 7.0.0,
measured 2026-09-29). Full-time takes `f` and `ft` (a bare "FT" is ADP's, about 95 rows; the ADP scraper
labelled it only because the filter could not read it). Contract takes `temporary`.

`title_pattern` is a whole-word pattern searched in the title as well as the raw value. Only
`is_internship` has one, `\bintern(?:ship)?s?\b`, so "International", "Internal" and "Internet" never
match, and "Internship Program" titles, which are the postings themselves, do. 40 of 40 unflagged titles
read were real internships.

`flags(value, title)` takes the title and `_served_meta` passes it. The SQL fallback (`raw_clause`, used
by a table without the columns and by the migration) stays value-only: a title is not something the
fallback can pattern-match, so on such a table an internship named only in its title is missed until the
columns exist. Python and SQL still agree on every raw value.

## Consequences

Against ADR-0337's rules on the audited table 10,970 rows change, all additions: `is_internship` 3,000
to 12,350, `is_contract` 12,571 to 13,321, `is_full_time` 330,365 to 331,407. Existing rows pick this up
through `_refresh_metadata`, which rewrites a row whose served flags differ from what the rules now
compute (delete then add): about 65 MB once (a served row averages 5.9 KB; a vector is 3 KB). No schema change.

Still unchanged here: a row whose source states no type (145,355 rows, 29.1%) matches none of the four
filters. That is decided in ADR-0341.

## Alternatives

Capturing schema.org `employmentType` from job pages was rejected: on Eightfold and Teamtailor it is a
constant `FULL_TIME` default (53 of 53 and 52 of 53 pages, including interns). A regex in SQL for the
title cue would need `regexp_like` in the migration and in the SQLite stand-in the equivalence test uses,
for a verdict the materialized column already carries.
