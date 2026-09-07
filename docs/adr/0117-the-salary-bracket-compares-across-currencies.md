# ADR-0117: The salary bracket compares across currencies, at a dated rate

**Status:** accepted · **Date:** 2026-09-07 · **Supersedes ADR-0082's "never FX-converted" clause for the *filter* only; ADR-0082's storage decision is unchanged**

## Context

ADR-0082 derives `min_salary_annual` / `max_salary_annual` by period-normalising whatever the
board published, in the job's own currency, and deliberately does **not** FX-convert. The reasoning
was sound and still is for storage: a stored converted number would be wrong the day after it was
written, and the employer's own figure is the only one that is a fact.

The filter inherited that rule and it did not survive contact with use. `build_filter` pinned
`salary_currency = 'USD'`, so a user who set a bracket in USD stopped seeing every INR, EUR and GBP
job in the index — silently, with no message, and with the results simply thinner. For an index
whose India coverage is one of its strengths, "pick a salary range, lose India" is not a caveat, it
is a trap. The rail's own tip said the bracket "only means something inside a single currency",
which described the behaviour accurately and did nothing to stop a user walking into it.

## Decision

**The bracket compares across every currency the served table carries, converting the *user's
bounds* rather than the *stored data*.**

`build_filter` expands one range into an OR of per-currency arms:

```sql
((salary_currency = 'USD' AND COALESCE(max_salary_annual, min_salary_annual) >= 100000
                         AND min_salary_annual <= 200000)
 OR (salary_currency = 'INR' AND COALESCE(max_salary_annual, min_salary_annual) >= 8300000
                            AND min_salary_annual <= 16600001))
```

Nothing in the index is rewritten. The numbers in the where-clause are still the employer's own,
compared against a bound restated in their currency — so ADR-0082's storage decision stands
untouched, and a rate change alters which rows match rather than what any row *says*.

### The rates are committed and dated, never fetched

`config/fx_rates.json` carries `as_of` beside the numbers; `headstart.fx` reads it; the rail prints
the date next to the control. A live lookup was rejected on three counts: it puts a third-party
call on the search path, it fails invisibly when that call does, and it makes the same query on the
same data return different results to two users at the same moment.

The cost is honest and visible: the rates go stale, and the UI says when they were taken so a user
can discount them. Refreshing means replacing `rates` and `as_of` together — a rate without its
date is precisely the defect this arrangement exists to avoid.

### A missing rate excludes; a missing table degrades

A currency absent from the table cannot be compared, so its Jobs stay out of a cross-currency
bracket. Treating it as 1:1 would be the silent wrong answer. If the table is unreadable entirely,
`fx.table()` returns `None` and the bracket falls back to the single-currency clause that predates
this ADR — a narrower answer, never a wrong one, and the same fail-safe direction the rest of this
codebase takes.

Converted bounds round **outward** — floor down, ceiling up — so arithmetic can never drop a job
sitting exactly on the boundary the user typed. The user's own currency is not converted at all and
keeps the number they entered.

### What this does NOT claim

**Market FX is not purchasing power.** ₹40,00,000 in Bengaluru and the ~$48,000 it converts to are
not the same offer, and no rate table makes them one. The rail says so in as many words: this is
currency conversion, not cost of living, and a converted salary should be read as "roughly this
much money" rather than as a comparable job. That sentence is load-bearing — it is what keeps the
feature from quietly implying the comparison it enables is a fair one.

### Rejected: keep the pinning and explain it better

The cheapest option, and what the old tip attempted. It was rejected because the failure is silent
by construction: a user does not notice results that were never shown, so no amount of adjacent
prose reaches the moment the loss happens.

### Rejected: store a converted column

It would make the query trivial and the data wrong. Every rate refresh would need a full re-derive,
and between refreshes the index would hold a number no employer ever published — which is the thing
ADR-0082 exists to prevent.

## Consequences

- `fx.py` and `fx_rates.json` must reach the Space. Both are in `deploy-space.yml`'s sync list
  **and** the Dockerfile's `COPY` — `tests/test_space_deploy_sync.py` caught the second half of
  that when only the first had been done, which is exactly the gap that would have made this
  feature silently do nothing in production while passing every test locally.
- **The rates are real, cross-checked, and dated 2026-09-07.** Taken from exchangerate-api.com
  and cross-checked against the ECB via frankfurter.dev: 28 of 33 currencies agreed within 2%,
  and the five that did not appear in both are carried by one source and named in the file. The
  first draft shipped placeholders dated 2024-06-01 whose INR rate was 83.0 against a real
  94.55 — a **14% error**, large enough to move which jobs a cross-currency bracket returns, and
  a reminder that "approximate" is not a licence to be wrong by an eighth.
- **Refreshing is a two-line edit and must stay one.** `rates` and `as_of` move together, and the
  UI prints the date beside the filter — a rate whose date is not visible is the failure this
  whole arrangement is built to avoid.
- Two existing tests asserted the single-currency clause verbatim and were updated: the
  overlap-not-containment rule still holds per arm, and the injection guard now asserts that every
  currency reaching the clause came from the table's own whitelist — the expansion widened what is
  emitted, not what is trusted.
