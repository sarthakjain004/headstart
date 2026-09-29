# ADR-0320: A description keyword's rows are found once, literal first, and named by row id

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0276](0276-a-hosted-mcp-call-ends-at-its-deadline-and-no-caller-holds-every-place.md) (its
"still finishing" sentence) · **Relates to:**
[ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md) (the
description column), [ADR-0299](0299-a-keyword-word-matches-where-a-word-starts-and-quotes-keep-a-phrase.md)
(the word-start rule this reads),
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md)
(the count-only total, and its deferred full-text index),
[ADR-0276](0276-a-hosted-mcp-call-ends-at-its-deadline-and-no-caller-holds-every-place.md) (the
45 s deadline), [ADR-0325](0325-the-model-retries-an-edge-failure-and-a-description-scan-runs-alone.md)
(the one scan place) · no schema, index or stored-data change

## Context

The round-2 critique of the hosted MCP server (2026-09-29, 6.0/10) found description-keyword
searches ending at the 45 s deadline about half the time (its P0-2): "visa sponsorship" in Germany
and "relocation" in the Netherlands and Sweden timed out, and the answered ones took 20–33 s. A
concise `search_jobs` call asks `/search` and `/facets?counts=total` at once. Each route scanned
every description for the keyword, and the facet total's description coverage counted the other
filters twice more. A country did not shrink the scan: the engine evaluates the whole where-clause
over every row it reads. The refusal then told the agent to "narrow the filters", which it already
had.

Since ADR-0299 a keyword term compiles to `regexp_like(col, '(?i)(^|[^a-z0-9])term')`. Measured on
the served table (498,539 rows, pulled 2026-09-29) under LanceDB 0.36.0, the Space's pinned
version, on a Mac held to two compute threads (`LANCE_CPU_THREADS=2`), whole-table counts:

| Keyword over descriptions | ADR-0104 `lower() LIKE` | ADR-0299 regex | its literal, no anchor |
| --- | --- | --- | --- |
| "relocation" | 5.6–6.0 s | 5.2 s | 1.1 s |
| "visa sponsorship" as a phrase | 7.3–8.4 s (two terms) | 5.0 s | 1.1 s |
| "sponsorship" | | 4.8 s | 1.1 s |
| "kubernetes" | 5.7–6.2 s | 1.1 s | 1.0 s |

The regex costs its leading `(^|[^a-z0-9])`: without it Rust's engine finds the literal with a
prefilter, with it it walks every byte ("kubernetes" happens to escape that). Every row the word
rule keeps holds the literal, so the literal is a sound first pass. Asked as one Lance filter,
`_rowid IN (the literal's rows) AND the exact regex` took 0.13–0.22 s for 12,867–30,439
candidates and kept exactly the rows the whole-table regex keeps, for four keywords. LanceDB reads
only the named rows for it. The other slow part is a country: Germany's gazetteer regex over every
location took 2.4 s.

## Decision

**1. The where-clause of a description-keyword request is answered by row id.**
`headstart.serving.description_matches.DescriptionMatches.where(filters, extra_where)` returns a
clause keeping the same rows as `build_filter(filters)` narrowed by `extra_where`, written as
`id IS NOT NULL AND _rowid IN (…)`. It finds them once:

1. the rows every filter but the keyword keeps, when at most 100,000 (`ROW_LIST_MAX`). The
   request's description coverage counts those same rows, so both read them once;
2. among those, the rows the keyword's literal matches: the ADR-0299 terms without their
   word-start anchor, `compiler.keyword_clause(..., word_start=False)`;
3. among those candidates only, the rows the exact keyword keeps.

When the other filters keep more than 100,000 rows (the United States) or there are none, step 2
reads the whole table instead, kept once per keyword, and step 3 applies the other filters to its
candidates, so a country's regex reads a few thousand rows rather than every location. When the
literal alone has more candidates too ("ai" in descriptions: 475,290 candidates, 229,805 rows),
the compiled clause is returned as it was and each route scans as before.

**2. What is found is kept for the life of the process, 64 where-clauses, least recently used
dropped first.** `/search`, the facet total, the coverage counts, a Blocking recount and every
later page ask the same where-clause strings, so they share one finding. A request for a clause
another request is finding waits for it instead of scanning again. There is no expiry: the served
table never changes under a running process (the Space restarts on a new one, and nothing serving
writes it), and a row id names the same row for as long as the table does.

**3. Both routes use it.** `JobSearch.run` compiles a description-keyword request through it, and
`JobSearch.facets` hands it to `facets.counts` as `table_where`, which compiles every count and read
of the served table through it: the total, the coverage, the keyword read of the full strip, and
Blocking recounts of the table. A count over the keyword's rows already read into memory (#856)
never takes it: a row id there names another row. The website's facet strip gains as the agent
does.

**4. Past the deadline, the advice names the description read.** A `search_jobs` call whose
keyword looks in descriptions (`scans_descriptions`, ADR-0325) that meets the 45 s deadline answers:
"Reading job descriptions for the keyword is the slow part. HeadStart finishes that read after this
call ends and keeps its matches unless there are very many, so the same call in a minute or two is
usually quick. Or look for the keyword in titles (keyword_in: title), or add a company." Every
other call keeps ADR-0276's sentence. ADR-0276's refusal while two abandoned reads still run no
longer asks for narrower filters on a description keyword: "…try again in a minute. A
description-keyword search that finishes keeps its matches unless there are very many, so the same
search is then usually quick."

No new request parameter and no change to what matches, so the agent contract version stays.

## Measured

The pair a concise `search_jobs` call sends, `/search` and `/facets?counts=total` at once, through
`JobSearch` on the table above (LanceDB 0.36.0, two compute threads; the model faked, so encoding
is not in it). "Plain" is main before this change (ADR-0299's regex, each route scanning), the
first "this" column a fresh process, the second the same call again or its next page:

| Call | Plain | This, first | This, again or page 2 |
| --- | --- | --- | --- |
| "software engineer", "visa sponsorship" in descriptions, DE | 8.0–8.6 s | 2.0 s | 0.04 s |
| the same, quoted, after the unquoted one | 5.9–6.4 s | 0.11 s | 0.05 s |
| its page 2 | 6.5–6.9 s | | 0.05 s |
| "relocation" in descriptions, NL | 6.1–6.6 s | 1.8 s | 0.02 s |
| "data engineer", "relocation" in titles or descriptions | 5.0–5.4 s | 1.7 s | 0.32 s |
| "kubernetes" in descriptions, the whole index | 1.0–1.1 s | 1.5 s | 0.06 s |
| "backend engineer", "sponsorship" in descriptions, US | 6.4–6.9 s | 4.4 s | 1.1 s |
| "ai" in descriptions, too many to name | 2.4–3.1 s | 4.2 s | 2.5 s |

Before ADR-0299 (`lower() LIKE`), the unquoted visa call took 11.4–12.8 s, "relocation" in NL
8.4–9.0 s, in titles or descriptions 8.1–8.2 s, and "kubernetes" 7.3–7.7 s. Ten requests
through `JobSearch` on the served table, with this and with the compiled clause (two ranked by a
query over the table's vector index, pages 2 and 3, a posted and a salary sort, `both`, a Blocking
filter, a too-common keyword), gave the same rows, totals and full facet strips, on LanceDB 0.33
and 0.36. These are local measurements; the hosted Space's are to be read after the deploy.

## Options rejected

- **A cheap prefilter ANDed before the regex.** `description ILIKE
  '%relocation%'` alone took 1.2–1.7 s, but ANDed with the regex 5.2–6.1 s: DataFusion evaluates
  both sides of `AND` over each batch, so the regex still reads every row. `CASE WHEN … ILIKE …
  THEN regexp_like(…) ELSE false END` would evaluate it per row, but Lance's planner refuses `CASE`
  ("not supported SQL in lance").
- **A faster spelling of the same regex.** Non-capturing groups, the alternatives swapped, the
  class spelled `[^a-zA-Z0-9]` or by ranges: all 4.0–5.6 s. `(?i)\brelocation` took 0.94 s, but
  `\b` is not the rule: it counts `_` and non-ASCII letters as word characters, and kept 26,966
  rows where the rule keeps 26,968.
- **Reading the candidates' text by row id and matching in memory.** Same rows, 0.5–1.2 s against
  0.13–0.22 s for the one Lance filter. LanceDB's `take_row_ids` also returns rows out of the order
  asked and cannot return their row ids.
- **Keeping row verdicts per keyword and reading only rows without one.** More bookkeeping for the
  same reads: the other filters' rows are read once anyway, for the coverage.
- **A full-text index on `description`.** Still ADR-0274's deferred fix; HF storage is the binding
  cost and this needs none.
- **Caching the answers of `/search` and `/facets` themselves.** The facet total already is, for
  60 s. A finding shared below both routes also serves the next page, another query over the same
  filters, and the website.

## Risks, stated plainly

- **Row ids hold only while the table does.** Both apps open the table once per process and never
  refresh it; one that did would need to drop what is kept.
- **Three LanceDB behaviours this works around, each measured.** 0.36's `count_rows` fails on a
  filter naming only `_rowid` ("count_pushdown: FilteredReadExec.index_input is not a
  ScalarIndexExec"), so the clause carries `id IS NOT NULL`. 0.36 cannot plan an unlimited read
  whose filter names `_rowid` unless the id is in the plan ("TakeExec requires … `_rowid`"), so the
  full strip's keyword read asks for the row id and drops it. 0.33 answered a filtered read limited
  to 100,001 rows with 1,113 of its 3,754, so no read here takes a limit. A LanceDB upgrade should
  re-run `tests/test_serving_description_matches.py` under the new version, and compare a few
  description searches on the served table with and without `DescriptionMatches`.
- **A too-common keyword pays one more scan, and keeps nothing.** "ai" took 4.2 s against 3.1 s
  the first time; that it has too many rows is kept, so the next call costs what it did before,
  but both routes still scan. So the deadline sentence and ADR-0276's "still finishing" one say a
  finished search is kept "unless there are very many".
- **The first search still reads the other filters over the whole table.** For a country that is
  its gazetteer regex, about 2.4 s here, and the coverage needs those rows anyway. A materialised
  country column for every country would remove it, which is a schema change.
- **Memory.** 64 kept clauses of up to 100,000 ids, about 1.5 MB of text each (a row id is
  `fragment << 32 | offset`, 13 digits or so).
- **The retry advice relies on the abandoned read finishing.** ADR-0276's abandoned reads run to
  their end, and what they find is kept, so a retry after them is served from it. While two are
  still running, the retry is told HeadStart is still finishing; that sentence no longer asks for
  narrower filters, and says a finished description search is then usually quick.
- **`id IS NOT NULL` is not in the compiled clause.** The row list agrees with it only because the
  pipeline never writes a null id.

## Consequences

- `tests/test_serving_description_matches.py` holds every clause to the compiled clause's rows on a
  real table, at three row-list bounds, and counts the reads; `tests/test_serving_job_search.py`
  holds the page, its total and page 2 to two description reads between them;
  `tests/test_space_mcp_server.py` holds the deadline advice.
- `docs/agents/space-mcp-server.md` describes both, and the iteration tasks gain t27 (page 2 of a
  country-scoped description search).
- ADR-0325's one scan place stays: a first finding still reads descriptions, and it now turns over
  faster.
