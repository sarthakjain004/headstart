# ADR-0194: JobSearch absorbs what its adapters copy

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0149](0149-search-filters-and-index-capabilities-are-two-objects.md) (capabilities stop being a
property repacked from loose attributes, and the `search`/`facets` cycle it left alone is broken)
· **Relates to:** [ADR-0042](0042-signed-in-ui-saved-sets.md) (both apps are thin adapters over
`JobSearch`), [ADR-0005](0005-embedding-model.md) (the task prefixes),
[ADR-0193](0193-one-module-per-materialized-search-filter.md) (the filter modules the compiler
calls)

## Context

ADR-0042 made `JobSearch` the one serving path, with the Space app (`deploy/hf-space/app.py`) and
the local renderer (`scripts/ui/serve.py`) as thin adapters over it. By 2026-09-24 the adapters
were carrying code again, and the modules around `search.py` leaned on it for the wrong reasons:

- **Verbatim copies in both adapters.** Each defined `_fx_converts(currencies)`, which asks
  whether two served currencies both carry a rate, and `_fx_as_of()`. Each also had a
  `_company_where` that parsed `mine` from the query string before calling `account_clause`.
- **The pipeline imported the serving path for four constants.** `embed_plan`, `embed_run`,
  `embed_merge`, `doc_prep`, `index`, `embed_prune`, `role_trends`, `hot_boards` and
  `company_directory` imported `headstart.search` only for `MODEL`, `DOC_PREFIX` or
  `PROD_TABLE`.
- **Capabilities stored twice.** `JobSearch` held eleven `has_*`/`atses`/`currencies`
  attributes, and a `capabilities` property repacked them into an `IndexCapabilities` on every
  access. The adapters read the attributes one by one, while `build_filter` and `facets.counts`
  got the repacked object. ADR-0149 chose the property so tests could monkeypatch one attribute
  at a time.
- **The result row was written out beside its projection.** `RESULT_COLUMNS` named the columns
  `run()` selects, and a hand-written 17-key dict in `run()` restated them.
- **An import cycle.** `facets` imported the compiler from `search`, and `search` could only
  reach `facets` through a deferred import inside `JobSearch.facets`. A test pinned that
  deferral.

## Decision

1. **`headstart.embedding_conventions`** holds `MODEL`, `DOC_PREFIX`, `QUERY_PREFIX`,
   `PROD_TABLE`, `load_encoder` and `encode_query`: the conventions a Doc vector and a Query
   vector must share. Every ingest stage, script and workflow that needed them imports them from
   there, and `search` imports `encode_query` from there. The name was checked against its
   neighbours. `ingest/embed_*` are pipeline stages, a different kind of module in a different
   package, and `search_index` was ruled out because ADR-0173 already uses "Search index" for the
   LanceDB scalar and vector indexes.
2. **`headstart.search_filter_compiler`** is the Search-filter compiler moved out of `search.py`:
   `SearchFilters`, `IndexCapabilities`, `build_filter` and its clause helpers, the Keyword scope
   map, `SALARY_DEFAULT_CURRENCY`, and the Account clause (`board_clause`, `account_clause`,
   `with_extra`). The Account clause lives with the compiler because it shares the LIKE escaping.
   `search` and `facets` both import `search_filter_compiler`, so `search` imports `facets` at module
   level and the cycle is gone. `test_facets.py` now pins the direction ("facets never imports
   the serving path") where `test_search.py` used to pin the deferral. The compiler's tests
   moved to `tests/test_search_filter_compiler.py`, named after the module. It is not called
   `search_filters`, because that plural would sit among the one-filter-per-module family
   (`experience_filter`, `india_filter`, …, ADR-0193) and read as one more of them.
3. **The adapters' copies move behind the search interface.**
   `search.request_account_clause(args, followed, hidden)` reads `mine` from the query string
   once. It lives beside `JobSearch.parse_filters` and the other query-string readers, not in
   the compiler, which only takes values that are already parsed. `JobSearch.salary_bracket_converts` replaces both `_fx_converts`, and `fx.as_of()`
   replaces both `_fx_as_of`, next to the rate table it reads. Each adapter's `_company_where`
   still fetches its own Account's lists, because those differ: the Space reads the signed-in
   Account's stored record, and the local renderer reads its one in-memory record.
4. **Capabilities are held only as `IndexCapabilities`.** `JobSearch.__init__` builds
   `self.capabilities` once. The eleven attributes are gone, and the adapters read
   `_searcher.capabilities.atses` and the like. A test that needs a different table swaps the
   whole object with `dataclasses.replace`, which the three `test_space_app.py` monkeypatches
   now do. This amends ADR-0149's choice of a property.
5. **One column source for the result row.** `_result_row` builds each served row from
   `RESULT_COLUMNS`, in order, with `score` after `id` and the serve-time `url` rewrite applied
   last. The key order is unchanged.

### Forks settled without the owner, and why

The owner authorised this refactor and asked for design forks to be settled by measurement or,
failing that, by the smallest interface.

- **`request_account_clause` takes the two lists, not the `CompanyPrefs` record.** Moving the
  Account lookup into `JobSearch` would have tied the serving path to the alerts store, which the
  local renderer does not have. Passing `CompanyPrefs` would have made `search` import
  `alerts.store`. Two collections is the smallest interface both adapters can meet.
- **`run(args, extra_where=…)` keeps its signature.** Taking follow/hide lists in place of
  `extra_where` would have changed the cache keys and every caller, and `request_account_clause`
  already removes the duplication.
- **`fx.as_of()` is in the change though the brief did not name it.** It was the second verbatim
  copy in both adapters, next to `_fx_converts`, and it belongs with the rate table it reads.

### Behaviour that changed

- A result row now carries `None` for `title`, `company` or `remote` when the table lacks the
  column. Before, the hand-written dict read those three with `row[...]` and raised a `KeyError`.
  All three are in the base `_schema()`, so no served table reaches this path.

No glossary term changes. The compiler, the embedding conventions and the Account clause are
modules, not domain concepts, so CONTEXT.md only has its two module paths updated.

## Consequences

- The pipeline no longer imports the serving path. `search.py` went from about 1,500 lines to 867:
  the compiler is 616 lines in `search_filter_compiler.py`, and the conventions are in their own small
  module.
- An adapter that needs a new fact about the table reads it from `capabilities`, and it is
  already on the object `build_filter` compiles against.
- Verified against the pre-refactor branch. The same scratch script as ADR-0193 (38
  `SearchFilters` × 512 capability combinations, `facets.counts`, `JobSearch.capabilities`,
  `projection`, `coverage()`, `_schema()`, `_served_meta`, the migrations and the index specs)
  printed 19,907 lines, byte-identical. A second script ran `JobSearch` against a real 80-row
  LanceDB table built with the served schema: 17 request shapes, each with and without an Account
  clause, covering semantic and browse queries, all three sorts, salary sorts in another
  currency, pagination and every filter family, plus `facets()`, `coverage()` and
  `n_seen_within()`. Its 696 lines are byte-identical before and after.
- New tests pin the seams this change created: `request_account_clause`,
  `salary_bracket_converts`, `fx.as_of`, the result row's key order, and the direction of the
  `facets` import.
