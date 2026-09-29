# ADR-0340: The employment-type flags read the title and more raw values

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) (the four materialized flags were the
exact verdict of the old substring rules), [ADR-0193](0193-one-module-per-materialized-search-filter.md)
(`employment_type_filter` owns the verdicts)

## Context

The 2026-09-29 audit of the served table (v18, 500,167 rows) found two defects in the employment-type
filter. 11,993 rows carry `intern` or `internship` as a whole word in the title, and `is_internship`
was True on 2,639 of them (22%): the flag read only the raw ATS string, Workday says "Full time" for
an intern and Greenhouse says nothing. And 10,790 rows held a raw value that reads as full time and
set no flag: "Regular" (Radancy, TikTok, ByteDance, 4,910), "SALARIED_FT" and "HOURLY_FT" (Rippling,
2,902), "F" (Applied Materials, 1,009), "CDI", "Tiempo completo", "全职"; 1,451 temporary or
fixed-term rows were not `is_contract`.

The descriptions of those rows say "part-time" as rarely as stated full-time rows' do (0.0–0.2%
against 0.8%; stated part-time rows say it 22.1%), so the values are what they look like.

## Decision

`EmploymentTypeRule` gains two ways to match. `equals` lists whole values (lowercased, not trimmed:
Lance's SQL has no `trim`) for codes too short to be a substring: `f` or `ft` would match "soft" and
"left". `title_pattern` is a whole-word pattern searched in the title when the raw value does not
match; only `is_internship` has one, `\bintern(?:ship)?s?\b`, so "International", "Internal" and
"Internet" never match, and "Internship Program" titles, which are the postings themselves, do.

The rules widen as measured: full-time gains `regular` (unless `part`, like `permanent`), and the
whole values `salaried_ft`, `hourly_ft`, `f`, `ft`, `fte`, `cdi`, `tiempo completo`, `全职`;
part-time gains `salaried_pt`, `hourly_pt`; contract gains `temporary` and `fixed`. Hours and
duration stack, so "fulltime_fixed_term" is full-time and contract.

`flags(value, title)` takes the title; `_served_meta` passes it. The SQL fallback (`raw_clause`, used
by a table without the columns and by the migration) stays value-only: a title is not something the
fallback can pattern-match, and the materialized flag carries the cue. Python and SQL still agree on
every raw value (`test_raw_clauses_agree_with_the_python_flags`).

## Consequences

On the audited table 20,316 rows (4.1%) change, all additions and none removed: `is_internship`
2,951 to 12,305, `is_full_time` 321,636 to 331,312, `is_contract` 11,870 to 13,321, `is_part_time`
5,838 to 5,867. Existing rows pick this up through `_refresh_metadata`, which rewrites a row whose
served flags differ from what the rules now compute (delete then add, about 25 KB of vector each),
so the one-time cost is about 0.5 GB in the first merge after this lands. No compaction or schema
change is needed.

The ADP scraper labels a bare "FT" or "Regular FT" only when the filter cannot read it, so those rows are now served as stated ("FT") instead of "Full-time (FT)";
the filter verdict is unchanged.

Unchanged and still open: a row whose source states no type (145,483 rows after this change, 29.1%)
matches none of the four filters, so "Full-time" still excludes it. That is a separate decision.

## Alternatives

Capturing schema.org `employmentType` from job pages was rejected: on Eightfold and Teamtailor it is
a constant `FULL_TIME` default (53 of 53 and 52 of 53 pages, including interns and a part-time
tutor). A regex in SQL for the title cue would need `regexp_like` on both the migration and the
SQLite stand-in the equivalence test uses, for a verdict the materialized column already carries.
Restating the flags in `compact` would avoid the row rewrite but leaves `_refresh_metadata` seeing
every changed row as stale between the merge and the next compaction, so it would rewrite them
anyway.
