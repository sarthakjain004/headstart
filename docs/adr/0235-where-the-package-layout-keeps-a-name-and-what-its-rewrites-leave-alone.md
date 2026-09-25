# ADR-0235: Where the package layout keeps a name, and what its rewrites leave alone

**Status:** accepted · **Date:** 2026-09-26 · **Supersedes in part:**
[ADR-0232](0232-the-shared-library-is-grouped-into-packages-by-the-question-each-module-answers.md)
(four rows of its table, its paragraph on the text that keeps an old name, and its 2026-09-25
amendment, which moves here)

## Context

ADR-0232 regrouped `src/headstart/` into packages, one PR per package, each rewriting every
reference. Landing the first three steps (#719 `network/`, #722 `jobs/`, #725 `search_filters/` and
the serving path) showed four of its names were wrong, and showed which text a mechanical rewrite
must not touch:

* `tech_filter` → `tech_classifier` would name the module against CONTEXT.md's **Tech filter**.
* Without their `_filter` suffix, three Search-filter modules take the name of the request parameter
  or Job field their callers handle (`india`, `employment_type`, `salary_known`), and a caller that
  binds that name as a local hides the module. `_warn_unknown_filters` already did: it binds
  `india = filters.india` in the function that reads `india_filter.WHOLE_COUNTRY`.
* `search/` beside `search_filters/` is the near-homograph CLAUDE.md warns about, and the
  `tests/test_{package}_{module}.py` names made it worse: `tests/test_search_*` mixed both packages,
  and `test_search_filters_fx.py` read as `search/filters_fx`.
* Rewriting a name inside a sentence about the past made it false: a `git log <sha>..<sha> -- path`
  range that returned commits returned none, a `DERIVATIONS_VERSION` entry named a module that did
  not exist at that version, and ADRs written before ADR-0194 split `search.py` came to cite
  `headstart.search.job_search.build_filter`, which has never existed.

## Decision

**Names.** Where this ADR and ADR-0232's table disagree, this ADR holds:

| Module | ADR-0232 said | Now | Why |
|---|---|---|---|
| `tech_filter` | `jobs/tech_classifier.py` | `jobs/tech_filter.py` | CONTEXT.md's **Tech filter**; `jobs/` and `ingest/` tell it apart from the `filter_tech` stage |
| `employment_type_filter`, `experience_filter`, `salary_known_filter`, `india_filter` | suffix dropped (`india`, …, `experience_ceiling`) | `search_filters/{name}_filter.py` | a module never shares a name with the field it filters |
| `search`, `facets`, `profile_extract` | `search/` | `serving/` (`serving/job_search.py`, …) | "serving path" is ADR-0042's and ADR-0194's term for this code, and no package or test prefix overlaps `search_filters` |

Two modules added since ADR-0232 get homes by its rule: `country_codes` moves to
`scrapers/country_codes.py`, since its only readers are the Lever and SuccessFactors scrapers, and
`trend_reading` moves to `trends/line_reading.py`, CONTEXT.md's **Line reading**.

**What a rewrite leaves alone.** A rewrite updates code, comments, living docs and ADRs, except
where the text describes the tree as it was:

* dated records: any doc whose file name starts with a date, every `LOG.md`, `docs/code-review/`,
  `docs/upstream-comparison/`, and the pass logs in `docs/salary-extraction/` (every file but its
  `README.md`);
* a path pinned to a commit: a `blob/<sha>/…` link, a `git show <rev>:<path>`, and a
  `git log <sha>..<sha> -- <path>` range, including one that wraps a comment line. CLAUDE.md's
  `DERIVATIONS_VERSION` citations are such ranges;
* a sentence stating what was true at a stated time, commit or version: an ADR's account of how
  the tree stood when it was written (the ten ADRs from 0020 to 0194 that cite `search.py`'s old
  contents keep them), and a version-history entry such as `doc_prep.py`'s `v8:` or `v9:`.

## Consequences

* ADR-0232 carries a note pointing here, and its 2026-09-25 amendment is removed from it, not
  repeated. Its table still maps every other module.
* `search_filters/` stutters (`search_filters/india_filter.py`), which is the price of a module
  name no local variable can shadow.
* #722 had rewritten `doc_prep.py`'s `v8:` entry; #725 restores it with the `v9:` entry.
