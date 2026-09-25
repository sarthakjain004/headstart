# Cross-stage source contracts

Use this map when adding an ATS, a Job field, a served column, or a Search filter.
The declarations are projections with different purposes, not interchangeable schemas.

| Change | Producers and consumers to check | Verification |
| --- | --- | --- |
| Job field | `jobs.job.Job`; Scraper output; `ingest.doc_prep.to_meta`; `ingest.update_meta.refresh_row` | Scraper fixture and metadata-refresh tests; confirm existing rows receive changed derivations |
| Served column | `ingest.index._schema` and migration; `README` served table/examples; `search.RESULT_COLUMNS` if returned; Facets if counted | `test_readme_schema.py`, index migration tests, result projection check |
| Derived value | `experience`/`salary`/`geo`; `doc_prep`; `update_meta` overwrite/repair rules | Old/new value comparisons; required `DERIVATIONS_VERSION` bump for stored inputs; live-data claims need fresh data |
| Search filter | `search_filter_compiler.build_filter`; Facets' lifted dimension; UI controls, active pills and defaults | `test_search_filter_compiler.py`, `test_search.py`, `test_facets.py`, real-source JS tests; live harness when semantics change |
| Persisted filter | `alerts.store.SEARCH_FILTERS` versus `SET_SEARCH_FILTERS`; UI save/reload; Space projection; `alerts.space_query` | Round-trip through the actual Store/Space interface; explicitly document intentional omissions |
| ATS | `scrapers.registry`; liveness/config identity; discovery/prober routing; filter harness `URL_SHAPES` | `test_scraper_registry.py` catches missing shapes; `verify-search-filters` still validates routing against real endpoints |
| Board outcome | `harvest` authoritative journal; shard artifacts; `scrape_join`; `index` scope and exclusions | Empty/full/failed/truncated outcomes and two-absence lifecycle; canonical Workday-style identities |
| Subscription state | Store confirmed-missing/unreadable distinction; opt-out marker; conditional writes; explicit re-enable call sites | Unsubscribe/re-enrollment, failed-read and stale-write tests; 409/503 callers must preserve intent |

`description` belongs in the Description store and served table, not the embedding
metadata JSON. `first_seen` belongs to the index: metadata refresh must not reset it.
Saved sets may retain `seen_within`; Subscriptions drop it because the Watermark
already defines their window. Keyword filters and numeric salary brackets remain
deliberately unsaved (ADR-0104). None of those differences should be removed to make
declarations look identical.

For schema checks, install `[dev]`: quality CI explicitly verifies the Arrow/Lance
imports before pytest. A clean lightweight environment that skipped an optional
suite is not equivalent to that gate. Existing projection tests should be extended
when the corresponding interface changes; do not generate all projections from one
universal record or add a second module merely to rename the same contract.
