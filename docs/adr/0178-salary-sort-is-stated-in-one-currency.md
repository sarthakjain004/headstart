# ADR-0178: The salary sort is stated in one currency

**Status:** accepted · **Date:** 2026-09-23 · **Amends:** [ADR-0084](0084-facet-counts-are-filter-shaped-not-query-shaped.md), [ADR-0117](0117-the-salary-bracket-compares-across-currencies.md)

## Context

`sort=salary` ordered by `min_salary_annual`, which ADR-0082 stores in each employer's own
currency and never converts. Both sort paths compared those raw numbers, so ₹30,00,000 (about
$32k) outranked a $650,000 job: on the 2026-09-15 served table, all 400 rows of the first 20
pages were INR. ADR-0117 had already given the salary *bracket* a committed, dated FX table, but
the sort was never given the same treatment.

The two sort paths cannot be fixed the same way. With a query, the ranked path re-sorts a window
of at most `max_k * max_page` rows in Python, where converting each row is cheap. Without one,
LanceDB orders the whole table, and it can only ORDER BY a stored column — never an expression
over two.

## Decision

The sort is stated in **one currency**: the picker's (sent beside a salary sort even with no
bound set), resolved and whitelisted exactly like the bracket's, else `SALARY_DEFAULT_CURRENCY`.

- **Ranked page:** each row in the window is restated in that currency with the ADR-0117 rates.
  A currency with no rate cannot be compared and sorts with the unpriced rows — never 1:1.
- **Browse:** the page is cut from two segments. The sort currency's Jobs come first, in true
  salary order. Every other Job follows, grouped by currency with each group in its own true
  order, and the unpriced last. That costs one extra `count_rows`.

A table with no such currency keeps the raw ordering it always had.

## Consequences

- One control has two orderings: converted on a ranked page, currency-first on a browse. The sort
  note names the currency and says which one is in force, so the user is never told a browse is
  a converted ordering.
- The browse's result **set** is exactly the filter's, so the facet total beside it still
  describes it. Scoping the browse to the sort currency instead was rejected: it emptied an India
  browse sorted in USD.
- Rejected: a stored converted column (e.g. `min_salary_usd`). It would order both paths
  identically, but it is a schema change, a full-table rewrite on HF (storage is the binding
  cost), and a re-derive every time `config/fx_rates.json` changes.
