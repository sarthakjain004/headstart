# ADR-0197: `salary.py` owns the salary field codec and one currency-symbol resolver

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:**
[ADR-0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md) (the Tier-1 per-ATS
dispatch this gives an encoder),
[ADR-0061](0061-refreshable-metadata.md) (why a changed answer would need a
`DERIVATIONS_VERSION` bump),
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (how the change was
measured), [ADR-0117](0117-the-salary-bracket-compares-across-currencies.md) (the FX table every
emitted currency must be in), [ADR-0181](0181-a-breezy-board-is-one-verbose-json-listing.md) (breezy's bare-`$` exception)

## Context

`Job.salary` is a persisted fact. The served table carries it raw, and `update_meta` re-derives
the salary columns from it (`salary_from_field(row.get("salary"), row.get("ats"))`). So the string
itself cannot be replaced by a typed value; whatever changes, the strings already stored must keep
reading the same way.

Two halves of one contract lived apart:

- **The encoder was hand-written in 17 scrapers.** Each `_salary_field()` joined
  `"LOW-HIGH CODE PERIOD"` itself (`" ".join(str(x) for x in (span, currency, period) if x)` in
  lever, recruitee, teamtailor, ashby, personio, rippling, smartrecruiters, jazzhr, breezy, jibe,
  keka, adp, pinpoint, pyjamahr, join, taleo_enterprise and greenhouse), and the shape it had to
  match was stated only in comments naming salary.py privates (`_field_range_currency_interval`,
  `_period_multiplier`, `_field_generic`, `_symbol_currency`, `_guess_currency`).
- **Three currency-symbol maps disagreed on a bare `$`.** `_DOLLAR_PREFIX` + `_CURRENCY_SYM` via
  `_guess_currency` (Tier 2: bare `$` is USD), `_symbol_currency` (a free-text field: bare `$` is
  unknown) and `_gem_currency` (gem's template: bare `$` is USD). The first two share a map; gem's
  was a third, case-sensitive copy.

`from_description(description, ats=None)` also took an `ats` it never read.

## Decision

**1. salary.py owns the codec.** `to_field(low, high=None, currency=None, period=None)` spells the
string `from_field` reads: `FIGURES [CURRENCY] [PERIOD]`, space-separated, `FIGURES` being
`LOW-HIGH` or `LOW` alone when `high` is None, empty parts left out. Every scraper above now calls
it. It takes structured parts and nothing else: each scraper keeps its own policies (which figure
is the floor, whether a lone ceiling is emitted at all, how a number is spelt, which native period
word is passed), because those differ per ATS and moving them would change stored strings. The
per-ATS parser dispatch (`_FIELD_PARSERS`) is unchanged; the codec's docstring states which period
spellings `from_field` annualises for which ATSes, and the scrapers' comments now name
`to_field`/`from_field` instead of privates.

Left on hand-built strings because they are not this shape: darwinbox (`"INR 3 - 5 (Annual)"`),
jobvite (`"LOW - HIGH"` with spaces), zwayam (its `"Upto HIGH CODE"` branch), zoho, bamboohr,
clearcompany, icims, ripplehire, taleo_be and gem (the ATS's own text passed through).

**2. One resolver, with the context difference as an explicit policy.**
`_currency_for_symbol(symbol, stated_text, *, bare_dollar)` and one map, `_SYMBOL_CURRENCY`,
replace all three. A named symbol (`CA$`, `A$`, `£`, …) decides alone, in any letter case; else an
ISO code in `stated_text`; else a bare `$` names `bare_dollar`. Tier 2 and gem pass `"USD"`,
`_field_generic` passes `None` — the one place the contexts legitimately differ, now a parameter
rather than three maps.

**3. `from_description` loses its unused `ats` parameter.** No caller outside tests passed it.

**4. A test holds every currency salary.py can emit to `config/fx_rates.json`.** All 13
(`_CURRENCY_CODES` plus SGD and NZD, which only a symbol can produce) have a rate today.

**5. SGD and NZD keep USD-shaped plausibility bounds — not fixed here.** `_bounded` falls back to
the USD bounds for a currency with none of its own. Giving them their own bounds would change
stored answers, which this refactor must not do. The served counts do not warrant it yet: of
143,288 served rows with a derived salary in the 2026-09-23 snapshot, 7 are SGD and 1 is NZD.

## Measurement

Stored data must read the same, so everything was measured against the merge-base
(`12d45409`), per ADR-0066 (old tier → new tier, plus same-tier value changes):

MEASUREMENT_PLACEHOLDER

## Consequences

- No stored answer changes, so there is **no `DERIVATIONS_VERSION` bump**.
- One parser-side behaviour difference exists and is deliberate: gem's own symbol match is
  case-insensitive, and `_gem_currency` compared case-sensitively, so a lowercase `ca$`/`c$`/`a$`
  read as USD there while Tier 2 read it as CAD/AUD. The one map reads it CAD/AUD everywhere.
  It occurs in none of the 1,757 served gem rows' fields, and the diff above found no change.
- The round-trip test found a pre-existing gap: keka.py emits a lone figure when only one of
  minimum/maximum is set, and `_field_keka` reads ranges only, so it declines — 64 of 1,376
  served keka salary fields (mostly `"1 INR"` placeholders). It is pinned by a test rather than
  fixed: keka's lone figure may be a ceiling, and reading it would serve it as a floor.
- A new scraper that spells a structured field should call `to_field`; its round trip is one
  entry in the parametrized test.

## Alternatives considered

- **Store a typed salary value instead of the string.** Rejected: the string is the persisted
  fact `update_meta` re-derives from; replacing it would orphan every stored row's input.
- **A codec that also owns the policies** (lone ceiling, number spelling, period mapping).
  Rejected: those differ per ATS on measured evidence, and folding them into one function either
  grows it a flag per ATS or changes stored strings.
- **Keep gem's resolver separate for byte-identical behaviour on lowercase prefixes.** Rejected:
  the difference is absent from stored data, and a second map is exactly what drifted.
