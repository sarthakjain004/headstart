# ADR-0232: The shared library is grouped into packages by the question each module answers

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0028](0028-ingest-package.md) (amends its "stays in `headstart` proper" list),
[ADR-0153](0153-a-fetcher-seam-replaces-the-module-global-http-import.md),
[ADR-0155](0155-one-module-for-board-identity-two-failure-policies-by-name.md),
[ADR-0156](0156-the-space-installs-headstart-as-a-real-package.md),
[ADR-0193](0193-one-module-per-materialized-search-filter.md),
[ADR-0194](0194-job-search-absorbs-what-its-adapters-copy.md),
[ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md)
· **Superseded in part by:** [ADR-0235](0235-where-the-package-layout-keeps-a-name-and-what-its-rewrites-leave-alone.md)
(the names of `tech_filter`, the four Search-filter modules and the serving path, and the text a
rewrite leaves alone)

## Context

ADR-0028 gave the ingest run its own package and left everything else flat in `src/headstart/`.
By 2026-09-25 that flat level held 43 files, and its names had stopped saying what the files are:

* `tech_filter` (the tech classifier) and `ingest/filter_tech` (the stage that calls it) are the
  near-homograph pair CLAUDE.md's naming rule warns about. `tech_filter` is also not a Search
  filter, though five modules beside it now are.
* `config` held no configuration: about 95% of it is two hand-curated Board lists
  (`EXCLUDED_BOARDS`, `PARKED_BOARDS`), the rest `CompanyRef` and a TOML loader. It also reads as
  the repo's `config/` data directory.
* `geo` is an India-only gazetteer in a product whose scope is global. `models` holds one class.
  `roles` holds the Trends taxonomy. `experience_filter` (the `max_years` ceiling) sits beside
  `experience` (the extractor) and reads as the same thing; `company_match` (the Trends picker's
  suggestions) sits beside `company_name` (a Board's served name) the same way.
* Modules that answer one question were scattered: the five per-Board CSV ledgers shared a
  `board_` prefix only in part (`liveness` did not), and the HTTP client, its browser twin, the
  seam over both and the spare egress had nothing grouping them at all.

ADR-0028 also says logic the curated feed (`python -m headstart`) reaches stays in `headstart`
proper, and names `harvest`, `board_cost`, `board_priority` and `corpus`. Measured on the import
graph that day, the feed reaches `harvest` and `board_cost` only; `board_priority` and `corpus`
are imported by `ingest` and scripts alone.

## Decision

Each top-level module moves into the package that names the question it answers. A module keeps
its name unless the name misled; a package supplies the context a short name lacks.

| Package | The question | Modules (old name → new name) |
|---|---|---|
| `boards/` | Which Boards exist, which get scraped, and how each is keyed and named | `board_identity` → `board_key`; `liveness` → `liveness_ledger`; `board_aliases` → `alias_ledger`; `board_priority` → `priority_ledger`; `board_cost` → `cost_ledger`; `board_description_gap` → `description_gap_ledger`; `scrapable_boards` → `scrapable`; `eightfold_backing`; `company_name`; `config` → split into `company_ref` (`CompanyRef`, `load_companies`) and `excluded_and_parked` (the two lists) |
| `network/` | How a request leaves the machine | `http`, `browser_http`, `fetcher`, `spare_egress`, `fanout_stats` |
| `jobs/` | One Job: its shape, and every field derived from its own text | `models` → `job`; `experience`; `salary`; `remote`; `tech_filter` → `tech_classifier` |
| `search_filters/` | The Search-filter vocabulary: what the index materializes and what the compiler turns into a where-clause | `search_filter_compiler` → `compiler`; `employment_type_filter` → `employment_type`; `experience_filter` → `experience_ceiling`; `salary_known_filter` → `salary_known`; `india_filter` → `india`; `posted_date_guard`; `geo` → `india_gazetteer`; `fx` |
| `search/` | The serving path the Space and the local dev server run | `search` → `job_search`; `facets`; `profile_extract` |
| `trends/` | What Trends reads from its history | `trend_history` → `history`; `trend_netting` → `netting`; `trend_history_migration` → `history_migration`; `hot_ranking`; `roles` → `role_taxonomy`; `company_match` → `company_suggestions` |

Three modules move into packages that already exist: `telegram_bot_api` into `alerts/` (its only
caller is `alerts.bot`), `corpus` into `ingest/` (only the run reads it, which is ADR-0028's own
rule), and `harvest` into `scrapers/` (the engine that runs every scraper; ADR-0028 chose its name).

Five files stay at the top level, and a new module joins them only on the same grounds:
`__init__`, `__main__` (the curated feed's entry point), `log` (every package imports it),
`llm_router` (the one LLM client any package may call), and `embedding_conventions` (the contract
between the run that writes vectors and the search that queries them, which ADR-0194 took out of
`search` on purpose).

Two boundaries decide placements that the questions alone do not:

* **The Space never imports `headstart.ingest`.** So nothing the Space reads moves into `ingest/`.
* **The run does not import the serving path** (ADR-0194). `ingest` writes the materialized
  Search-filter columns, so the filter modules live in `search_filters/`, beside `search/` rather
  than under it, and every package `__init__` is a docstring and nothing else: importing
  `headstart.search_filters.india` must never pull in `JobSearch`.

A moved module's tests are named `tests/test_{package}_{module}.py`, the precedent `alerts/`
already set (`test_alerts_store.py`), so `tests/` groups by package and a short module name like
`compiler` or `history` keeps its context in the file name.

The layout lands one package per PR, each rewriting every reference in the same change (imports,
`mock.patch` targets, workflows, the Space, docs and ADRs, per CLAUDE.md's naming rule), with no
compatibility shims.

A rewritten reference tells a reader where the code lives now, so the rewrite reaches past ADRs and
dated docs too. Two kinds of text keep the old name, because they describe the tree as it was: a
link pinned to a commit (`blob/<sha>/src/headstart/http.py`), and a sentence stating what was true
of the tree at a stated time or commit ("measured on `main` at `12d45409`", "`tests/test_http.py`
had to autouse-stub `rotate`"). The table above is the map from those names to today's.

## Consequences

* A log line's tag is the last segment of its logger's name, so a module that is renamed logs
  under its new name (`[board_key]`, not `[board_identity]`); a module that only moves keeps its
  tag. On 2026-09-25 exactly one analyser parsed a tag, `scripts/runlog/fanout_retries.py`'s
  `[spare_egress]`, and that module keeps its name. Logs of runs before a package landed carry the
  old tags; analyse them from the run's own SHA, as for any other change.
* A module that finds files relative to `__file__` sits one directory deeper after its move, and
  its `parents[N]` index must grow by one (`eightfold_backing` reads `parents[2]`).
* ADR-0028's list of modules the curated feed reaches is superseded by the measurement above: of
  the four, only `harvest` (now `scrapers/harvest.py`) and `board_cost` (now
  `boards/cost_ledger.py`) are reached from the feed.
