# ADR-0341: A job that states no employment type is full-time to the filter

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md),
[ADR-0340](0340-the-employment-type-flags-read-the-title-and-more-raw-values.md)

## Context

The 2026-09-29 audit found that 145,355 served rows (29.1%) match none of the four employment-type
filters because their source states no type, or states a word no rule reads ("OTHER", "Remote"). Six
ATSes state none at all: Greenhouse, Eightfold, Teamtailor, Zwayam, Cornerstone and ClearCompany, 70,727
rows, and the "Full-time" filter reached none of them. A probe of the live sources found a type for
about 10% of those rows (Greenhouse's `metadata[]` custom field on about 10% of Boards, some Avature,
SuccessFactors and Radancy labels, captured by #942, #944, #945, #946), and schema.org `employmentType`
is a constant `FULL_TIME` default on Eightfold and Teamtailor (53 of 53 and 52 of 53 pages, interns
included), so it says nothing. About 125,000 rows stay unstated at the source.

Their descriptions behave like full-time rows: the first 3,000 characters say "part-time" for 1.1% of
no-type rows, against 0.8% of stated full-time rows and 22.1% of stated part-time rows.

## Decision

A Job that no rule reads as any of the four types is full-time to the filter. `flags()` sets
`is_full_time` when none of the four verdicts is True, so the materialized column carries it and the
bitmap index serves it. Part-time, contract and internship still need positive evidence (a raw value,
or for internship the title), and a Job whose title says intern is evidence, so an unstated intern is
an internship and not full-time.

The SQL fallback for a table without the columns (`RAW_CLAUSES["full-time"]`, also the migration) says
the same on the raw column: the raw full-time rule, or none of the four raw rules matches, over
`coalesce(employment_type, '')` so a NULL type is unstated. It cannot read the title, so on such a
table an unstated internship named only in its title is counted as full-time until the columns exist.
`reads_as_a_type(value)` answers "does any rule read this value" for a caller that needs that, which
`flags` no longer can: the ADP scraper's relabelling of "PT 129 or Less Hours" asks it.

## Consequences

On the audited table Full-time goes from 331,407 rows (66.3%) to 476,762 (95.3%); the six ATSes go from
0 to 69,294 of 70,727. The other three filters do not change. "Full-time" now excludes only Jobs that say
part-time, contract, temporary, fixed-term, freelance or internship. It stops being a narrowing filter
in the usual sense, which is the price of not hiding a third of the corpus.

It changes what a saved search or alert Digest with `etype=full-time` matches: it now includes the unstated
Jobs. Existing rows pick it up through `_refresh_metadata` (145,355 rows, delete then add with the vector,
about 0.85 GB once at the table's 5.9 KB a row; 0.1 s per 2,048-row chunk on a copy of the served table,
so about ten seconds). No schema change, and no API field is added: the flags are index columns, not
response fields, and `employment_type` is still served as the source stated it (null when unstated).

The filter harness (`scripts/eval/verify_filters.py`) accepts an unstated row under Full-time, and the
MCP `employment_type` parameter now says so.

ADR-0340 estimated its own rewrite at 0.3 GB from a stale "about 25 KB a vector" comment; a vector is
3 KB (768 floats), so its 10,970 rows rewrite about 65 MB.

## Alternatives

A fifth "Not stated" option with Full-time left strict was rejected: the six ATSes would stay hidden from
anyone who picks Full-time, and it needs a new column. Inferring from the description only recovers about
10% of unstated rows (9.6% say full-time in their first 3,000 characters).
