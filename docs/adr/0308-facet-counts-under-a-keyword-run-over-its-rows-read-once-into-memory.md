# ADR-0308: Facet counts under a keyword run over its rows, read once into memory

**Status:** accepted · **Date:** 2026-09-29 (records #856, merged 2026-09-29, and its code-review
follow-up) · **Amends:** [ADR-0084](0084-facet-counts-are-filter-shaped-not-query-shaped.md) (its
§Cost: one `count_rows` per option no longer holds under a keyword) · **Relates to:**
[ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md) (the Keyword
filter and its description coverage),
[ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) (follow and hide, the Account clause),
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md)
(the count-only total),
[ADR-0320](0320-a-description-keywords-rows-are-found-once-literal-first-and-named-by-row-id.md)
(the served table's where-clauses by row id) · no schema, index or stored-data change

## Context

ADR-0084 counts every facet option with its own `count_rows` over the served table, and costs the
strip as that many counts issued through one thread pool. Issue #834 found what that did under a
keyword. Every count but the two coverage counts carried the keyword's clause, so a `kw_in=both`
or `kw_in=description` request scanned the description column about 78 times. On the Space's two
vCPUs `/facets?kw=haskell&kw_in=both` took 103.4 s from a fresh boot, and other requests got HF's
502 while it ran.

## Decision

**1. A keyword's rows are read once, and every count that keeps the keyword runs over them.**
`facets.counts` reads the rows matching the keyword and every filter no count lifts into an
in-memory LanceDB table. The filters every count keeps are all but the listed dimensions
(`seen_within`, `posted_within`, `max_years`, `etype`, `ats`), whose "Any" rows lift them. Each
count then runs over that table with its compiled where-clause minus the keyword clause:
`(A AND keyword AND B)` over the served table is `(A AND B)` over the rows the keyword matches,
evaluated by the same engine.

- The two description-coverage counts lift the keyword, as before, and still count the served
  table. They run while the rows are read.
- A Blocking recount that drops a listed dimension is answered from the rows read. One that drops
  any other filter, the keyword included, counts the served table, as before.
- A lone total (ADR-0274) reads nothing first: it is one count, and a read would only lengthen it.
- With no keyword nothing changes: every count goes to the served table.
- Since ADR-0320 a description keyword compiles its reads and counts of the served table by row
  id. A count over the in-memory rows never does: a row id there names another row.

**2. The read keeps only the columns the counts name, found outside quoted terms.** A column is
kept when its name appears as a word in any count's compiled where-clause, after every quoted SQL
string is blanked. So the description and the 768-float vector are never copied, because no count
names them. The quotes matter. #856 matched names inside quoted terms too, and the code review
found that hiding a Board whose key holds the word "vector" (`ashby:vector` is a real Board)
copied the vector of every row read. Measured on the local snapshot of the served table
(514,163 rows, pulled 2026-09-23), `kw=engineer&kw_in=both` read 406,777 rows:

| Account clause | columns kept | in-memory table | peak resident size |
| --- | --- | --- | --- |
| none | 14 | 24.9 MB | 2.0 GB |
| hiding `ashby:vector`, matched inside quotes (#856) | 16, with `vector` | 1,293 MB | 6.6 GB |
| hiding `ashby:vector`, quotes blanked (this ADR) | 15 (`id` for the clause) | 43.7 MB | 2.5 GB |

A company or location term with the word "description" or "vector" copied that column too, but
over the few rows it matches. The compiler writes every user term and Board key as a single-quoted
string with its quotes doubled, so blanking `'…'` (a doubled quote inside included) leaves only
identifiers. All 188 country clauses blank cleanly.

## Measured

Local, the same snapshot, one `counts` call, the code before #856 against #856:

| request | before | #856 |
| --- | --- | --- |
| `kw=golang&kw_in=both` | 7.50 s | 2.66 s |
| `kw=haskell&kw_in=description` | 7.51 s | 2.35 s |
| `kw=rust&kw_in=both&salary_min=150000&has_salary=true` | 4.65 s | 1.29 s |
| `kw=zzqxv&kw_in=both&ats=lever` (zero; Blocking names `kw`) | 6.76 s | 2.91 s |
| `kw=golang` (title) | 0.20 s | 0.08 s |
| `remote=true&ats=lever` (no keyword) | 0.03 s | 0.03 s |

Live on the Space after #856 deployed (one request each): `kw=golang&kw_in=both` 98.3 s → 12.3 s,
`kw=scala&kw_in=both` 118.8 s → 13.6 s, `kw=python&kw_in=both&remote=true` 19.5 s → 5.3 s. Old and
new `counts` gave identical output for 26 of 26 requests on one fixed table.
`tests/test_serving_facets.py` holds every count to counting each compiled clause over the whole
table: 156 cases on a table with the served schema, and the Account clause over three keywords.

## Options rejected

- **Reading the keyword's rows alone**, with no other filter. It was slower than the old code when
  the other filters were selective: `kw=haskell&kw_in=both&ats=workday&remote=true` went from 2.3 s
  to 4.4 s locally, because Lance evaluates the description regex only on the rows the indexed
  filters leave. Narrowing the read by every filter no count lifts fixed that.
- **Projecting from a column list the compiler reports**, instead of matching names in the clause.
  Exact, but it gives every clause builder in `search_filters` a second output to keep in step.
  Blanking quoted strings gets the same columns from the text the counts already run.

## Risks, stated plainly

- **Memory.** The read holds every matching row's kept columns for one request. `kw=engineer`,
  called 12 times in one process, levelled off at about 1.5 GB resident, against 2.9–3.3 GB over 5
  calls with the old code. Each call connects its own in-memory database, freed with the request.
- **Columns are matched by name.** A where-clause that names a column only through an alias or a
  double-quoted identifier would lose it from the read, and its count would fail. The compiler
  writes neither today.
- **A zero total still reads the served table once per active filter outside the listed
  dimensions.** Blocking recounts run one after another, and each drops a different filter, so
  each is a different where-clause with nothing kept to reuse. The dear part is the other filters
  more than the keyword: a country's gazetteer regex reads every location. Measured live on
  2026-09-29, from cold, one request each: `kw=qqzzvx&kw_in=both&country=US&location=austin&
  has_salary=true` took 19.5 s, and the same filters without the keyword, 8.1 s. Tracked in #912.

## Consequences

- ADR-0084's §Cost ("46 counts per search … 53–83 ms") describes a request without a keyword.
  Under a keyword the cost is one read plus that many counts over memory.
- `tests/test_serving_facets.py` holds the read to one clause reaching the table
  (`test_a_keyword_reaches_the_table_once_per_request`), to every filter no count lifts
  (`test_the_read_keeps_every_filter_no_count_lifts`), and to no column a quoted term alone names
  (`test_the_read_never_copies_a_column_only_a_quoted_term_names`).
