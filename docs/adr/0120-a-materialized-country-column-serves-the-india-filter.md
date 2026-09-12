# ADR-0120: A materialized `country` column serves the India filter's country-level case

**Status:** accepted · **Date:** 2026-09-11 · **Amends:**
[ADR-0086](0086-country-tag-signals-in-the-india-gazetteer.md) (reconciles its Consequences
argument against a stored country column — see below) · **Relates to:**
[ADR-0024](0024-india-location-gazetteer-filter.md) (the gazetteer this reuses),
[ADR-0061](0061-refreshable-metadata.md) (the `DERIVATIONS_VERSION` cascade contract this
follows), [ADR-0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md) (the derivation
shape this mirrors), [ADR-0104](0104-a-keyword-filter-with-a-scope-map-and-a-stored-description-column.md)
(the backfill precedent this deliberately diverges from, and why), [ADR-0118](0118-a-fact-can-wear-a-derivations-column.md)
(the shape this is **not** — `country` has no competing raw field)

## Context

The search API's India filter (`build_filter(india="india")`) compiles to `geo.where("india")` —
a 3,068-character `regexp_like` alternation over the free-text `location` column, built fresh on
every request. Measured unindexed (`experiment/lancedb-scalar-index/LOG.md`, 2026-09-07 session):
`count_rows` 352.6ms, vector page 1,338.1ms — 7–13x every other filter cost measured in that
investigation. No scalar index type can serve a `regexp_like` alternation (the same session
confirmed building scalar indexes changes this filter's cost by nothing), so indexing cannot fix
it — only a materialized, precomputed value can.

**This reopens a decision the codebase already made once.** ADR-0086 explicitly weighed a stored
country column against query-time matching and chose query-time, specifically because a gazetteer
fix then reaches every row instantly with no backfill lag:

> "Query-time only. Nothing is re-derived, re-embedded or re-indexed; the next search simply
> matches more rows. That is the standing advantage of ADR-0024's LIKE-expansion design over a
> stored country column, and it is why this was cheap enough to be worth doing for 0.8%."

That argument justified a small, purely *additive* gazetteer expansion — 429 rows, 0.8% of the
49,892 India rows measured on 2026-08-25 — where the win of instant reach outweighed one more
branch's cost on an alternation that was already being paid for on every request regardless. It
never claimed the alternation's own **baseline** cost was cheap; that wasn't measured until this
later session, two weeks after ADR-0086 shipped. The two ADRs answer different questions: ADR-0086
asked "is a gazetteer fix worth query-time reach for a small addition," this one asks "is the
alternation's baseline cost, now measured, worth a stored column for the dominant case."

The tradeoff this ADR accepts in exchange — a fix to the gazetteer reaching already-indexed rows
only on the next sweep, not instantly — is not new to this codebase. It is exactly the cost
`DERIVATIONS_VERSION` already imposes on `experience.py` and `salary.py`: a fix to either doesn't
reach an already-embedded row until a version-bump sweep runs (CLAUDE.md's own documented, accepted
rule). This ADR gives `geo.py`'s country-level rule that same tradeoff, for the country-level case
only — `geo.where()` itself is unchanged, and every other caller (city/region filtering, `/facets`,
any future consumer) keeps its existing instant reach.

## Decision

Materialize a nullable `country` column — `"IN"` | null — via a new function,
`headstart.geo.classify(location: str | None) -> str | None`. It replicates `where("india")`'s
exact rule (the country substring minus `INDIA_EXCLUDE`; the `IND_FORMS` positions minus
`IND_EXCLUDE`; the subdivision tail; every city alias; every state name) in pure Python rather than
compiled regex, because every alternative `where()` ORs together is itself a substring, prefix,
suffix, or equality test on a literal (`_rx` is `re.escape` plus SQL-quote-doubling — it changes
nothing about *what* matches). `classify()` reads the exact same `CITIES`/`STATES`/`IND_FORMS`/
`SUBDIVISIONS`/`EXCLUDE`/`IND_EXCLUDE`/`INDIA_EXCLUDE` constants `where()` does, so a future data
edit (a new alias, a new exclusion) reaches both paths without a second change. The two can still
drift if the rule's *shape* changes (a new part added to `where()`'s composition) — that risk is
closed by `tests/test_geo.py::test_classify_agrees_with_the_country_level_rule_on_every_oracle_row`,
which asserts agreement on every real location string in the module's own trap/recall oracle, not
by the shared-constants argument alone.

`country` is wired through the plain experience/salary-shaped derivation cascade —
`doc_prep.to_meta()` computes it for every new Job, `update_meta.refresh_row()` re-derives it on
`sweep or rederive or location moved`, one shared `DERIVATIONS_VERSION` bump (8 → 9). This is
**not** `remote`'s ADR-0118 "fact wearing a derivation's column" shape: `country` has no competing
raw ATS field a resync could silently revert to, since it is a pure function of `location` alone
(itself already a fact, resynced every run) — so, unlike `remote`, it needs no exclusion from
`FACT_FIELDS` and no held-description branch at all.

`build_filter()` prefers `country = 'IN'` over `geo.where(india)` only for the literal top-level
`"india"` value (the exact sentinel `where()` itself uses for "whole country," as opposed to a
`CITIES`/`REGIONS` key), gated on a `has_country` dark-until-migrated flag mirroring
`has_first_seen`/`has_min_salary_annual`/`has_description`. Every city or region place
(`"bengaluru"`, `"delhi ncr"`, …) keeps the unchanged `geo.where()` path unconditionally — only the
country-level alternation was ever measured as expensive; individual city clauses are far smaller
and were never flagged.

No scalar index on the new column, and no change to `compact()`'s silent index-dropping behavior —
both explicitly out of scope. A plain equality scan on a low-cardinality string column is already
far cheaper than the alternation it replaces, without indexing at all; indexing `country` is a
future, separate change, gated on fixing `compact` first (`experiment/lancedb-scalar-index/LOG.md`'s
own "blocker that applies to every index type here" section).

## Why not a boolean `is_india` column

A boolean would be simpler given only India is classified today. `country` is a nullable string
instead so a future country tag needs no second schema migration — it currently only ever holds
`"IN"` or null, and nothing here commits to classifying anything else yet.

## Consequences

- **The accepted lag.** A future `geo.py` alias or rule fix reaches the served `country` column
  only on the next `DERIVATIONS_VERSION` sweep + `index sync`, not instantly — the exact property
  ADR-0086 called the "standing advantage" of query-time matching, now deliberately given up for
  the country-level case. `geo.where()` itself is untouched and un-deprecated: city/region
  filtering keeps instant reach, and any caller that still needs the always-fresh regex can use it.
- **No bespoke backfill command, unlike `description` (ADR-0104).** `description`'s true value
  (raw text) was never stored in `meta.jsonl` — only a `has_description` bit was — so backfilling
  pre-existing rows needed its own `backfill-from-store` command reading a third source. `country`'s
  true value *is* fully computed into `meta.jsonl` by the sweep, so `index.py`'s existing,
  unconditional `_refresh_metadata()` compare-and-rewrite loop backfills it automatically on the
  first ordinary `sync` after the sweep — the same mechanism salary's own v3 bump already relied on.
- `tests/test_geo.py`'s trap/recall oracle becomes the shared correctness gate for both
  `where("india")` and `classify()` — a future regression in either path that the other doesn't
  share now surfaces as a real disagreement between the fast path and the fallback, not just a
  silent one.
- Python's `str.lower()` and DataFusion's SQL `lower()` could in principle disagree on some exotic
  Unicode casefolding, which would make `classify()` and `where()` disagree on a location neither
  test suite has observed yet. Every alias/state string in the current oracle (including the
  macron in `"Karnātaka"`) already passes through both correctly — this is a theoretical gap, not
  an observed one, and is not guarded against here.
- `search.py`'s `coverage()` method is deliberately unchanged, the same call already made for
  `remote`: most rows legitimately aren't India, and that is not a data-quality gap the Data tab
  should report.
