# ADR-0149: Search filters and index capabilities are two objects, not one 27-keyword function

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:** ADR-0031 (`build_filter` itself), ADR-0084 (facet counts), ADR-0104 (the Keyword filter's `has_description`), ADR-0035 (the alerts Watermark / `ALLOWED_SEARCH_FILTERS`)

## Context

`headstart.search.build_filter` is the reference Search-filter compiler (ADR-0031): the one
place a request's filters become a LanceDB where-clause. Its signature had grown to 27 keyword
parameters. Twenty-one are real **Search filters** — `remote`, `max_years`, `ats`, `kw`, the
salary bracket, the recency windows, and so on — things a user (or a Subscription) actually sets.
Six are not filters at all: `atses`, `currencies`, `has_first_seen`, `has_min_salary_annual`,
`has_description`, `has_country` are runtime facts about *which columns and whitelist values the
currently-open Search index happens to carry* — learned once per process in `JobSearch.__init__`
from two full-table scans and a schema check, and otherwise unrelated to what the user asked for.
`build_filter`'s own docstring spent nine lines warning which of the six silently misbehave if a
caller forgets to pass them — an ATS whitelist that quietly admits nothing, or a salary bracket
that compiles to `None` with no error.

Three call sites paid for that shape:

1. `JobSearch.filter_kwargs()` existed solely to rebuild the flat 27-key dict from `self`'s
   learned state every request, mixing the two kinds of value back together.
2. `facets.counts()` calls back into `build_filter` up to 46 times per request (ADR-0084) — once
   per facet option being counted — each time re-merging the same 27-key dict with one override,
   `{**base, **overrides}`. This is also why `search.py` and `facets.py` import each other (the
   deferred import inside `JobSearch.facets()`, `search.py:936-947`) and why a rename in
   `build_filter`'s signature couldn't be checked against anything — there was one flat namespace
   shared by 27 unrelated concerns.
3. `alerts/store.py`'s `ALLOWED_SEARCH_FILTERS` is a **fourth, independent, hand-written** list
   of 9 filter names a Subscription may carry over `/search` — nothing checked that it stayed a
   subset of `build_filter`'s real parameter names, and a rename in `search.py` would have
   silently dropped a filter from every Digest with no error anywhere (worse: `alerts/
   space_query.py`'s `_PERMANENT_HTTP = {400, 401}` means the resulting HTTP 400 would not even
   be retried).

## Options considered

**(a) Two objects: `SearchFilters` for the real vocabulary, a separate capabilities object for
the six runtime facts.** `build_filter(filters, capabilities)`. Both objects are dataclasses,
learned/validated once, and `facets.counts` varies only `filters` per option (via
`dataclasses.replace`) while `capabilities` passes through unchanged.

**(b) Keep `build_filter`'s flat signature but accept one merged object instead of 27 kwargs** —
a single `SearchQuery`-style bag holding all 27 fields together. Collapses the call site to one
argument, but keeps the exact conflation this ADR exists to remove: a facet-count override still
can't cheaply vary "the user's choice" independently of "the table's capability," and a subset
check against `alerts.store.ALLOWED_SEARCH_FILTERS` would still have to know which six of the
bag's fields to exclude by hand — the same manual exclusion list the current code effectively
carries in its callers' heads.

**(c) Leave `build_filter` alone; only ease the two heaviest symptoms** — e.g. give
`JobSearch` a cached capabilities dict and leave `facets.counts` doing `{**base, **overrides}`
against it. Cheapest diff, but does nothing for the actual defect: 27 unrelated names still share
one namespace, `ALLOWED_SEARCH_FILTERS` still has nothing to be checked against, and the next
person adding a filter still has to know, by convention alone, not to add it next to `has_country`.

## Decision

**(a).** Two frozen dataclasses in `headstart.search`:

- **`SearchFilters`** — the 21 real fields, one per Search filter, every one defaulting to
  "unset" exactly as an absent query-string parameter does today.
- **`IndexCapabilities`** — the six runtime facts. `atses`, `has_first_seen` and
  `has_min_salary_annual` keep no default (the three whose omission used to fail *silently and
  dangerously* — an admitted-nothing ATS whitelist, a Watermark cutoff turned into no clause);
  `currencies`, `has_description` and `has_country` keep theirs, because omitting those only ever
  narrows a feature to "not offered on this table."

`build_filter(filters: SearchFilters, capabilities: IndexCapabilities) -> str | None` replaces
the 27-keyword signature. `JobSearch.filter_kwargs()` now returns a `SearchFilters` (parsed once
per request, same as before — the name is kept because the deliverable list treats it as a fixed
call site, even though it no longer literally returns kwargs). A new `JobSearch.capabilities`
**property** repacks the six already-computed attributes (`atses`, `has_first_seen`, …) into an
`IndexCapabilities` on every access — a property rather than a field set once in `__init__`,
because the UI templates (`deploy/hf-space/app.py`, `scripts/ui/serve.py`) and the test suite
(`tests/test_space_app.py`) both read *and monkeypatch* those six attributes individually; making
`capabilities` a stored field would have broken every `monkeypatch.setattr(searcher,
"has_first_seen", False)` in the suite. The six attributes stay exactly as they were; only a
lightweight view over them is new.

`facets.counts(table, filters, capabilities)` replaces `facets.counts(table, filter_kwargs)`.
Its per-option rebuild becomes `build_filter(dataclasses.replace(filters, **overrides),
capabilities)` — `capabilities` is never touched by the 46-times-a-request loop at all, which is
the direct payoff of the split: varying "what the user asked for" no longer has to drag "what
this table supports" along for the ride. `facets._blocking` walks `SearchFilters`'s own fields
(`vars(filters)`) rather than a merged dict, so the six capability names are no longer denylisted
in `NEVER_BLOCKING` by hand — they are structurally absent from the object `_blocking` ever
looks at, which a test now asserts directly rather than by enumeration.

**`ALLOWED_SEARCH_FILTERS` stays a hand-curated `frozenset` in `alerts/store.py` — it does not
import `SearchFilters`.** This was the harder call, and worth stating why explicitly.
`alerts/store.py` runs in two places: in-process during the pipeline's alerts run, and copied
flat into the deployed Space (`deploy-space.yml`) where the Space app imports it directly. A
Python import would make `ALLOWED_SEARCH_FILTERS` a real subset of whatever `SearchFilters`
*this process's* `search.py` defines — but `alerts/space_query.py` calls a **deployed** Space
over plain HTTP, from the pipeline's own process, and that Space can be a different commit than
the one running the alerts job. A shared Python object cannot make two different deployments
agree on a filter vocabulary at request time; the two are only ever as in-sync as the last
deploy. So a runtime import would add a real dependency (this module currently has none beyond
`.access`) to buy a guarantee it cannot actually make. What a same-repo, same-commit check
*can* catch is the failure this ADR's Context section actually named: a rename or typo in
`search.py` that nothing notices before it ships. `tests/test_alerts_store.py` now asserts
`ALLOWED_SEARCH_FILTERS <= {f.name for f in fields(SearchFilters)}` (and the same for
`SET_SEARCH_FILTERS`) — a plain, fast, in-repo test that fails the same PR that breaks the
invariant, rather than a comment nobody re-checks. A genuine live-drift check — fetching the
*deployed* Space's actual accepted-filter behaviour and diffing it against
`ALLOWED_SEARCH_FILTERS` — is a different, real gap this ADR does not close: `/search` has no
endpoint that enumerates its accepted names (an unknown filter is silently dropped, not
rejected), so answering that question needs differential probing of live behaviour, which is
closer in kind to `scripts/eval/verify_filters.py` / the `verify-search-filters` skill than to a
unit test. Left as a residual gap rather than a false sense of coverage.

The import cycle at `search.py:936-947` (`JobSearch.facets()`'s deferred import of
`headstart.facets`, needed because `facets.py` imports `build_filter` etc. from `search.py` at
module level) is unchanged by this ADR and not touched: `facets.py` still needs
`SearchFilters`/`IndexCapabilities`/`build_filter` from `search.py` at module level, and
`search.py`'s `JobSearch.facets()` still needs `facets.counts` — the same shape, same direction,
same reason. There is nothing about splitting the parameter list that removes that dependency,
so chasing it here would be scope creep on an unrelated defect.

## Consequences

- `build_filter`'s signature shrinks from 27 keywords to 2. `JobSearch.n_seen_within` — which
  used to hand-spread five of the six capability facts every call — is now `build_filter
  (SearchFilters(seen_within=hours), self.capabilities)`.
- `facets.counts`'s ~46-times-a-request rebuild now varies only `SearchFilters` (21 fields)
  through `dataclasses.replace`; `IndexCapabilities` (6 fields) is constructed once per request,
  not once per option.
- `NEVER_BLOCKING` in `facets.py` drops its six capability entries — they are unreachable by
  construction now, not denylisted, and `tests/test_facets.py` asserts the two dataclasses'
  field sets are disjoint as a standing regression guard.
- `scripts/bench/scalar_index_bench_v2.py` — the one non-test caller of `build_filter` outside
  `search.py`/`facets.py` — is updated to build a `SearchFilters` per case and one
  `IndexCapabilities` per pass. `scripts/eval/verify_filters.py` and `scripts/ui/serve.py` needed
  no change: neither calls `build_filter` directly, both go through `JobSearch`.
- `CONTEXT.md`'s **Search filter** glossary entry now names `headstart.search.SearchFilters` as
  its implementation and calls out `IndexCapabilities` as the thing it deliberately is not — a
  runtime fact of the index, never a Search filter, never in `alerts.store.
  ALLOWED_SEARCH_FILTERS`'s vocabulary, never nameable as the **Blocking filter**.
- Full suite (`tests/test_search.py`, `tests/test_facets.py`, `tests/test_alerts_store.py`,
  `tests/test_alerts_space_query.py`, `tests/test_space_app.py`, and the rest of `tests/`) passes
  green after the change; `tests/test_space_app.py`'s `monkeypatch.setattr(searcher,
  "has_first_seen", False)`-style tests pass unmodified, confirming the property design didn't
  disturb that surface.
