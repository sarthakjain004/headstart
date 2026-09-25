# ADR-0193: One module per materialized Search filter

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:**
[ADR-0173](0173-rebuild-the-search-indexes-with-the-table.md) (the materialized verdicts this
reorganizes), [ADR-0138](0138-a-materialized-country-column-serves-the-india-filter.md) (the
`country` column), [ADR-0149](0149-search-filters-and-index-capabilities-are-two-objects.md)
(`SearchFilters` and `IndexCapabilities`, kept as two objects),
[ADR-0084](0084-facet-counts-are-filter-shaped-not-query-shaped.md) (the Facet options)

## Context

ADR-0173 materialized five Search-filter verdicts as columns, each with a raw fallback for tables
that predate it. Each one's facts were spread across three or four modules:

- **Experience ceiling** (`max_years`). `experience_filter.py` held the ceilings, the column names
  and the Python verdict. The fallback `(min_years <= N OR min_years IS NULL)` and the choice
  between flag and fallback lived in `search.build_filter`. `facets.py` renamed the ceilings to
  `MAX_YEARS` and wrote the labels. `ingest/index.py` restated the SQL a second time for its
  migration.
- **Employment type** (`etype`). `employment_type.py` held the rules, `matches()` and
  `raw_clause()`. `search.py` rebuilt them as `ETYPE_CLAUSES` and chose the column. `facets.py`
  hard-coded the four values and their labels. `index.py` built the schema fields, the migration
  and the bitmap specs from the rules, and `JobSearch.__init__` ran its own completeness check.
- **India/country** (`india`). `build_filter` chose between `country = 'IN'` and `geo.where()`,
  `ingest/derived_meta.py` wrote the `"country"` key from `geo.classify`, and `index.py` and
  `JobSearch` each named the column.
- **Salary known** (`has_salary`) and **posting-date comparable** (the shape guard on every
  `posted_*` clause). Both matched the pattern, each with its column name, SQL expression and
  Python verdict written in `search.py` (`build_filter`, `coverage()`,
  `posted_at_is_comparable`) and again in `index.py` (`_served_meta` and the migrations).

## Decision

Each materialized Search filter gets its own module under `headstart`. The module holds the
filter's column names, the verdict the index writes, the SQL an old table is migrated with, the
clause (flag form or fallback, picked by the `IndexCapabilities` flag passed to it), the
completeness check `JobSearch` stores on `IndexCapabilities`, and the Facet options where the
filter has a Facet:

| module | Search filter | columns |
| --- | --- | --- |
| `experience_filter` | `max_years` | `experience_at_most_{0,2,5,10}` |
| `employment_type_filter` (renamed from `employment_type`) | `etype` | `is_full_time`, `is_part_time`, `is_contract`, `is_internship` |
| `india_filter` | `india` | `country` |
| `salary_known_filter` | `has_salary` | `salary_known` |
| `posted_date_guard` | shape guard of `posted_within`/`posted_after`/`posted_before`/the posted sort | `posted_at_comparable` |

The members share one set of names: `COLUMN`/`COLUMNS`, `flags(raw)`, `MIGRATION_SQL`,
`has_flags(schema_names)` (`has_column` for `india_filter`, whose column is a value, not a flag),
`clause(…, materialized)` and `FACET_OPTIONS`. `search.build_filter`, `JobSearch`,
`facets.counts` and `ingest/index.py` (the schema fields, `_served_meta`, the four migrations and
`_search_index_specs`) call these members instead of restating them. `SearchFilters` and
`IndexCapabilities` stay two objects, as ADR-0149 decided. `ETYPE_CLAUSES` and
`posted_at_is_comparable` are removed from `search.py`, and `facets.MAX_YEARS` goes away with
them.

`employment_type` was renamed so the five modules share a shape. The old name also read as the
raw column, which stays untouched for display. With the rename, its per-value `EmploymentTypeFilter`
became `EmploymentTypeRule` and `FILTERS` became `RULES`, so that nothing inside the filter's
module is also called a filter. The `_filter` suffix sits beside `tech_filter`. That module is the
ingest-side **Tech filter**, a different CONTEXT term, and `experience_filter` already shared the
suffix with it. `posted_date_guard` breaks the `_filter` suffix on
purpose: the materialized part of the posted-date filters is only their shape guard. The date
clauses stay in `search.py`, where they share `_ago`/`_next_day` with the `first_seen` clauses.
Moving those as well would have made `search.py` and the new module import each other.

### Forks settled without the owner, and why

The owner authorised this refactor and asked for design forks to be settled by measurement or,
failing that, by the smallest interface, then recorded here.

- **No registry object or protocol class.** A tuple of modules that `index.py` iterated over looked
  tidier, but it breaks on order. The schema interleaves the flag columns with the raw ones, and
  README's schema test pins that order. `_served_meta`, the migrations and the index specs each
  list the filters in a different order too. Iterating would have changed the physical column
  order a migration writes and the order indexes are built. `index.py` names each module at its
  existing position instead, which also keeps the interface smallest.
- **Country's value is written through `derived_meta`, not `_served_meta`.** `country` reaches the
  table through the embedding store's meta and `update_meta`'s sweep (ADR-0138), not as a
  served-only flag. `country_meta` and `update_meta`'s sweep now take the column name, and
  `country_meta` the value, from `india_filter`. `india_gazetteer.where`/`india_gazetteer.classify` stay in `headstart.search_filters.india_gazetteer`, which owns the gazetteer both of them read.
- **`description_stored` stays where it was.** It is the Keyword filter's coverage presence flag
  (ADR-0104), not a Search filter; only the coverage counts read it
  (`facets._with_description`, `JobSearch.coverage`).
- **The migrations keep their four names and call order.** Each is now one line over a shared
  `_add_missing_columns`. `sync`, `compact`, `backfill-from-store`, the tests and
  `scripts/bench/search_index_build_retained.py` call them unchanged. The one visible difference
  is a log line: the posted-date migration now logs `['posted_at_comparable']` in the same list
  form as the other three.

### Rejected

- **Each module owning its bitmap index spec.** `Bitmap()` is a LanceDB config object, so a
  filter module would have to import LanceDB, or carry an index-type string that `index.py` maps
  back to the object. `search.py` imports these modules and must stay importable without LanceDB.
  Each module owns its column list instead, and `index.py` builds one bitmap spec per column in
  the order it used before.

- **Pointing `scripts/eval/verify_filters.py`'s `_etype_ok` at `employment_type_filter`.** The
  harness is an oracle run against the deployed Space. Sharing the product's predicate would make
  it agree with the product by construction, so it keeps its own.

## Consequences

- A new materialized Search filter is one module plus one named line at each place in `index.py`
  and `search.py` that lists filters. Its facts are no longer spread over four files.
- Verified byte for byte. `build_filter` was run over 38 `SearchFilters` samples × all 512
  combinations of the nine `IndexCapabilities` flags. `facets.counts` recorded every where-clause
  it compiled. The script also covered `JobSearch.capabilities`, `projection` and `coverage()` on
  seven schemas with columns missing, `_schema()`, `_served_meta` on five sample rows, the SQL of
  all four migrations and `_search_index_specs`. Its 19,907-line output is identical at the
  merge-base and on this branch.
