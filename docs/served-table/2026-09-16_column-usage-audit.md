# Served-table column audit: two columns with no found reader — 2026-09-16

**Question:** does every column in the LanceDB `jobs` table (`index._schema()`, 23 columns) earn
its place, or are any written and stored on every row while nothing downstream reads them?

**Method:** for each of the 7 columns the API doesn't project (`search.RESULT_COLUMNS` has 16 of
the 23), traced every consumer across the codebase — `search.py`'s filter/sort/where-clause
construction, `facets.py`'s facet-count queries, the UI templates, and `update_meta.py`'s
re-derivation logic (the one internal reader that touches stored-row values without going through
the API). Verified against `origin/main` at `697ece08`.

## The 7 non-projected columns, and what actually reads each one

| column | read by | verdict |
| --- | --- | --- |
| `description` | `search.py` Keyword filter (`description IS NOT NULL...`, `_OPTIONAL_KEYWORD_COLUMN`) | used |
| `country` | `search.py` India filter (`self.has_country`, materialized equality vs. regex, ADR-0138) | used |
| `experience` | `update_meta.py:282` — compares the row's stored raw value against the freshly-scraped one to detect drift; one of the three cascade inputs (`_rederive_without_text` at line 365 also reads it) | used, internally |
| `experience_source` | `update_meta.py:153,368` — distinguishes a description-sourced value from a title-only guess when deciding whether re-derivation may trust or must redo a row | used, internally |
| **`max_years`** | *(searched: `search.py`, `facets.py`, UI templates, `update_meta.py`'s trigger logic — no read of this column found)* | **no reader found** |
| **`department`** | *(same search — no read of this column found)* | **no reader found** |
| `vector` | the entire point of the table | used |

## The two candidates, in detail

### `max_years`

Parsed alongside `min_years` by the same experience-extraction cascade and stored on every row
(`derived_meta.py:41`). The `max_years` **query parameter** on `/search` — which sounds like the
obvious consumer — filters on the `min_years` **column** instead:

```python
# src/headstart/search.py:91-92, 715-716
if max_years is not None:
    filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
```

This is intentional and correct as a filter (it means "roles that need at most N years," which is
what the parameter name promises) — but it means the table's own `max_years` column, a job's
stated *upper* bound, is not what answers that query. `facets.py`'s `max_years` facet dimension
(line 136) calls the same query parameter, so it doesn't touch the column either. `SORT_COLUMNS`
doesn't include it. `RESULT_COLUMNS` doesn't include it. The only place `max_years` (the column)
appears again after being written is `update_meta.py`'s `DERIVED_FIELDS` tuple — which just means
it's one of the fields the re-derivation delta report (`gained`/`lost`/`retiered`/`moved`) tracks
as a byproduct of being a schema field, not because anything reads its value to decide something.

### `department`

Raw ATS text, stored on every row (`index._schema()` line 196, carried through `doc_prep.py`'s
`META_FIELDS`). Its one real functional use is upstream of the table entirely:
`tech_filter.is_tech(title, department)` reads it off the **raw scrape record**, before a row is
even filtered into the tech corpus, let alone indexed — that job is already done by the time a row
reaches `index sync`. Not in `RESULT_COLUMNS`, not in any `search.py` filter or sort, not in
`facets.py`, not read by `update_meta.py`'s re-derivation logic (unlike `experience`/
`experience_source`, which the cascade actively reads back).

## What this means, and what it doesn't

This is a **finding**, not a recommendation to act unilaterally. Two real columns, written on
every merge and stored on every one of the table's 468,376+ rows, currently serve no purpose once
a row reaches the served table — but removing either is:

- a **live schema change** against a deployed LanceDB table and the `/search` API projection,
- **hard to reverse cheaply** — dropping a column means the code that reads it (should a future
  feature want it) has to be rebuilt from scratch, and `department`'s pre-index role in
  `tech_filter` would need to stay fed from somewhere even if the served copy goes,
- worth its own **ADR**, per this repo's own rule 6 (architectural/schema decisions get options
  laid out and a deliberate choice, not a silent pick) — this file is that laying-out, not that
  choice.

## Options, if this is worth acting on

1. **Drop both from `_schema()`.** Smallest table, matches "every column earns its place."
   `department` would need its own path from raw scrape record into `tech_filter.is_tech()` to
   keep working — check that it doesn't currently reach `is_tech()` *via* the table anywhere
   (it doesn't; `is_tech` is called at scrape/filter time on the raw record, not from the index).
2. **Drop only `max_years`.** `department` at least has a plausible future use (a department
   facet/filter nobody has built yet); `max_years` genuinely duplicates information already
   answerable from `min_years` plus the raw `experience` string.
3. **Keep both, note the `max_years` parameter's naming is what makes this confusing** — a reader
   who sees a `max_years` query parameter *and* a `max_years` column reasonably assumes the first
   reads the second. Worth a one-line comment at minimum even if the column stays.
4. **Leave as-is.** Two int32/short-string columns per row is not a meaningful storage cost at
   this table's size — not measured here, since the schema-change cost (points above) dominates
   the decision either way.

No file in `src/` was changed to produce this report — it's read-only tracing, not a schema edit.
