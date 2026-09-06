# ADR-0084: Facet counts are filter-shaped, not query-shaped

**Status:** Accepted · **Date:** 2026-08-25 · **Extends ADR-0031's filter vocabulary and ADR-0074's addressable window; scoped by ADR-0082's no-FX rule**

## Context

Issue #275 asked for result counts beside every filter option, more "first seen" windows, a salary
bracket, and a descending sort — all of it one request: *"show number of results after each filter
is applied actually, so user is well informed what his filters are actually doing."*

The Search tab could not answer any of that. `JobSearch.run` returns one page of rows and nothing
else, so there was no total anywhere, pagination was blind (a short page was the only "no next page"
signal available), and a filter control could not say what it cost. A user who filtered themselves
down to zero results got "Nothing matched" and no way to tell which of six filters did it.

Four calls in building it were not obvious, and each had a plausible alternative that is wrong.

## Decision

### 1. Counts are decided by the where-clause alone, so they get their own endpoint

A vector search **ranks** the filtered set; it does not shrink it. Every row matching the
where-clause is a candidate, and the query only decides the order they come back in. So the count
of "results" is a property of the filters and is *identical* with or without a query — it needs no
encoder call, no vector, and no ranking.

`/facets` is therefore its own route rather than a field on `/search`. Folding it in would have
coupled ~40 counts to every ranked request and changed `/search`'s response from the bare array its
clients already read. The browser fires both at once, so the counts cost the user nothing beyond
the search they were already waiting for.

The consequence the UI must carry: the number is labelled **"matching your filters"**, never
"results for your query". Printing a bare `40,807` beside a relevance-ranked list would promise a
relevance the number never measured.

### 2. A facet lifts its own constraint before counting its options

For each dimension, its own filter is removed and every other filter stays applied. So the ATS
strip answers *"how many would I get if I switched to Greenhouse"*.

The obvious alternative — count each option intersected with the filters as they stand — makes the
selected option's count equal the current total and every other option's count a number it can
never actually deliver. With a 2-hour window active, "Last 24 hours" would report the 2-hour count,
making every longer window look useless. This is per-dimension, not a global unfiltered count.

**The "Any" row obeys the same rule**, and it is the one place this is easy to get backwards: it is
counted server-side with its dimension lifted, not from the running total. Reviewing the first
implementation caught exactly that — with a 7-day window active, "Any time" read 30,177 while "Last
30 days", a strict subset of it, read 107,740. The unconstrained choice looking narrower than
something nested inside it is worse than showing no number at all. Switches (remote, shows-salary)
get no such row: a checkbox's "off" is the absence of the constraint, not another option to draw.

### 3. The salary bracket is scoped to exactly one currency

ADR-0082 normalises salary by period and deliberately never FX-converts. A single numeric bracket
across currencies would therefore rank 60,000 INR beside 60,000 USD as equals. Measured 2026-08-25:
of the 28.5% of Jobs carrying a salary, 86.5% are USD, 4.2% EUR, 3.0% no currency at all, 2.1% GBP,
2.0% INR.

So the currency picker comes first, is whitelisted against what the served table actually holds
(like `ats`), and defaults to USD. Two further calls fall out of it:

- The bracket is an **overlap** test, not containment: the job's top of range must clear the user's
  floor and its bottom sit under the ceiling, so a band wider than the user's still qualifies.
  `COALESCE(max_salary_annual, min_salary_annual)` keeps single-figure postings in play.
- **The currency alone does not filter.** It only bites once a bound is named. Treating the picker
  as a filter in its own right would cut the result set to the 28.5% carrying any salary, for a
  click the user reads as "which currency should the bracket be in".

### 4. Sorting a ranked search re-orders the addressable window; it never pushes ORDER BY down

`search.py` already carried the measured warning that an `order_by` alongside a vector search
**replaces** similarity ranking rather than tie-breaking it. So asking LanceDB to sort a search
would silently discard the query — "backend engineer" would return every job matching the filters,
newest first, which reads as a bug.

Instead, with a query and a sort, the whole window is ranked and then re-ordered in Python. The
window is `max_k * max_page` — exactly what ADR-0074's clamp lets pagination address, so a row
outside it was already unreachable by any request. Measured on a 316,606-row table: 2.7 ms for one
page against 9.2 ms for the full 2,000-row window, so keeping the query costs ~6.5 ms.

This is **not** a global sort, and the UI says so — in words rather than with a row count. The page
cannot see the server's `max_k`, and the number it *can* compute (`PAGE_SIZE * MAX_PAGE`, 400) is a
different one, so printing a figure would state the caveat wrongly. "Newest among your best matches
— not a global date sort" is the fact the user needs and cannot drift out of step with the server.

With no query there is no ranking to protect, so LanceDB orders the whole table directly, with an
`id` tiebreak against the tied-sort pagination bug ADR-0074's browse path already guards. The
re-sorted branch tiebreaks on `id` too, but descending rather than ascending — it sorts the tuple
`(date, id)` in reverse — which is a different order from the LanceDB branch's and matters only in
that neither repeats or drops a row across pages, which is what the tiebreak is for.

One guard the ordering needs and the filter already had — and it is compiled by `build_filter`,
not bolted onto the where-clause afterwards, so the facet counts get it too. A guard applied only on
the search path would have counted rows the sorted list excludes, and the header would have
overstated the result set by exactly the 8.4% below. `posted_at` is a raw per-ATS string, and a
non-ISO form like darwinbox's `21-Apr-2026` sorts lexicographically **above** every ISO date.
Measured 2026-08-25 without it, a "newest posted" page led with `22-Jun-2026` above `2028-07-01`.
Sorting by posting date therefore applies the same `LIKE '____-__-__%'` shape guard `posted_within`
uses. Rows with no readable posting date cannot be placed on a date ordering at all, so they leave
the result set rather than being strewn through it — 8.4% of the table, almost all of which carry
no `posted_at` whatsoever rather than an unparseable one (2 rows).

## Consequences

The filter parse moved to `JobSearch.filter_kwargs`, shared by the ranked search and the counts, so
the two can never describe different queries — a count that disagrees with the list it is counting
would be worse than no count, because a wrong number is trusted where a missing one is not.

Zero-count options are disabled rather than hidden: hiding one would rewrite the control under the
user's cursor, and "Contract (0)" is itself the answer to "why are there no contract jobs". Two
options are never disabled whatever they count — the "Any" row, which is how you get back out, and
whichever option is currently selected, since disabling it would strand the user on a choice whose
name they can no longer read.

The same machinery pays for a **why-zero** diagnostic. On an empty result set, `blocking` names the
single active filter whose removal recovers the most rows, so the empty state points at the culprit
instead of advising the user to go and guess. It stays silent when nothing matched even with every
filter dropped, because then no filter is to blame.

Cost: 46 counts per search on today's index (8 recency windows + 4 posting windows + 4 experience
tiers + 4 employment types + remote + salary-known + 18 ATSes + 4 "Any" rows + the total), measured
53–83 ms on a 316,606-row table, issued through one `ThreadPoolExecutor` (LanceDB counts in Rust
with the GIL released, so the wall cost is roughly the slowest count rather than their sum).

`india` is deliberately **not** faceted: its 25 options would add more counts than everything else
combined, for a control most users never open. That is a cost decision, not an oversight — revisit
it if the gazetteer dropdown turns out to be used.

One import detail the Space makes load-bearing: `JobSearch.facets` imports `headstart.facets` with a
flat fallback, because the image has no `headstart` package — it lays every module down beside
`app.py`. A package-only import raised `ModuleNotFoundError`, which the route's `except ValueError`
does not catch, so `/facets` 500'd and the browser's `.catch` degraded it to silence: counts simply
absent, in production only. `tests/test_space_deploy_sync.py` now fails on any synced module whose
`headstart` imports outnumber its `except ImportError` fallbacks.

## Amendment (2026-09-07): the window's cost was re-measured, and it had moved

This ADR justified taking the whole `max_k * max_page` window on the sorted-query path with a
measurement: *"2.7 ms for one page against 9.2 ms for the full 2,000-row window, so keeping the
query costs ~6.5 ms rather than a redesign."* That was measured 2026-08-25. ADR-0104 added a
stored `description` column on 2026-09-02, and nothing re-measured.

Re-measured through `JobSearch.run` on the served table (318,003 rows), and the figure is now
**264.9 ms**, not 9.2 ms — the window was materialising 2,000 whole rows, each carrying its
768-float `vector` and a description averaging ~5,000 characters, only to discard both when the
response was projected one line later.

The fix is a `select()` of exactly the columns `run` reads (`RESULT_COLUMNS`), not a redesign, so
this ADR's decision stands — the window is still the right shape. Only its price changes:

| path | before | after |
| --- | --- | --- |
| query + sort (the 2,000-row window) | 264.9 ms | **83.6 ms** |
| browse, no query | 105.5 ms | **55.8 ms** |
| query, no sort (one page of 20) | 21.2 ms | 20.4 ms |

The last row is the control: with only 20 rows to carry, the projection is worth nothing, which
is what confirms the saving is per-row payload rather than anything about the query.

Two things the projection cannot be naive about, both measured against the real backend:
`select()` **raises** on a column the table lacks, so it is intersected with the live schema at
construction — half of `RESULT_COLUMNS` arrive by migration, and naming one unconditionally would
turn ADR-0031's dark-until-migrated rule into a 500 on every search. And the two paths need
different extras: the vector path names `_distance` explicitly, because `score` is that value and
lancedb warns its auto-projection "will change in the future"; the browse path names `_rowid`,
without which an `order_by` over a projected scan fails planning outright.

**Not done here, and deliberately:** the corpus still carries no index of any kind. Scalar indexes
(~10 MB, 0.15 s to build) and an IVF_PQ vector index are the next levers, but the latter is only
viable with `refine_factor` — at defaults it returns recall@20 of 0.55 and changes the top result
on 48% of queries, and the `_distance` it reports is the quantized estimate, so every score the UI
prints drops by ~0.25. That is a quality decision, not a latency one, and is left open.
