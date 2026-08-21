# ADR-0082: Salary extraction — a two-tier cascade, period-normalized, no estimate tier

**Status:** accepted · **Date:** 2026-08-21 · **Amends:**
[ADR-0019](0019-tech-corpus-search-index.md) (closes the loop it deliberately left open: "salary
parsing... its own step" — this is that step) · **Relates to:**
[ADR-0009](0009-experience-extraction.md),
[ADR-0018](0018-experience-seniority-fallback.md) (the precedent this
mirrors, and the one place it deliberately diverges)

## Context

`Job.salary` has existed since early scraper work as a raw, unnormalized string — 9 of 20 active
scrapers already populate it from a structured API field. But nothing parses that string into a
number, nothing mines the job description for a salary mention when the field is empty, and
nobody had ever measured how good coverage actually is. The served schema has `min_years`/
`max_years`/`experience_source` alongside the raw `experience` string, but no equivalent for
salary — only the raw string and a boolean `has_salary` filter (`salary IS NOT NULL`, presence
only, no numeric range).

ADR-0019 named exactly this gap and deferred it: salary parsing "is ambiguous (lakhs vs absolute,
doubled suffixes, multi-currency)... its own step, and it shouldn't block serving the real
corpus." This ADR is that step, built the same way `headstart.experience` was (ADR-0009 →
ADR-0018 → five more): start with one ATS, read real captured data, build patterns from evidence,
measure, document, repeat — not a single speculative design landed everywhere at once. This ADR
covers the module and schema design; per-ATS coverage numbers live in
`docs/salary-extraction/<ats>.md`, starting with the pilot (`workable`).

## Decision

**A two-tier cascade — structured field, then description — with no third tier, in
`src/headstart/salary.py`:**

```python
def extract(salary, description, ats=None) -> SalarySpan | None:
    return from_field(salary, ats) or from_description(description, ats)
```

Three design choices, each confirmed with the project owner before implementation began (all three
recorded here as the single citable record; per-ATS docs point here rather than re-explaining):

1. **Currency: period-normalized, not currency-normalized.** Every figure is converted to an
   annual amount (hourly × 2080, monthly × 12, daily × 260, LPA × 100,000) but stays in its
   native currency, carried as its own `salary_currency` column. No FX conversion, no single
   cross-currency number, no exchange-rate dependency to keep fresh. A `$` figure and a `₹` figure
   are never compared numerically — exactly the "multi-currency" ambiguity ADR-0019 flagged, now
   resolved by not conflating currencies rather than by converting between them.
2. **No estimate fallback — the deliberate divergence from `experience.py`.** Experience's Tier 3
   floor-estimates years from a seniority label when no number is stated ("Senior" → 5+), a
   defensible inference. Salary has no equivalent tier: a fabricated dollar figure risks
   misleading a real financial decision in a way a years-of-experience floor doesn't. `extract()`
   returns `None` when neither tier finds a real number — unknown stays unknown, and per
   `CONTEXT.md`'s "Salary" entry, unknown is never treated as exclusionary.
3. **Bounded, cheap-first live sampling** for the research this module is built from:
   `scripts/enrich/salary_sample.py` samples up to 3000 live boards per ATS (or the full
   live-board count if smaller) via `config.load_active_companies` — never a hand-parsed CSV,
   which would inherit the liveness ledger's documented duplicate-row issues. One listing fetch
   per board for listing-only ATSes; a bounded ~3-detail-fetch/board adapter, built per-ATS as
   needed, for the 8 ATSes whose description requires a separate detail pass.

**Schema**: 4 new nullable columns, inserted after `salary` in `_schema()`
(`src/headstart/ingest/index.py`), mirroring `min_years`/`max_years`/`experience_source`'s shape:

```python
pa.field("min_salary_annual", pa.int32()),
pa.field("max_salary_annual", pa.int32()),
pa.field("salary_currency", pa.string()),
pa.field("salary_source", pa.string()),   # "field" | "regex" | null — no "seniority"
```

Named `min_salary_annual`/`max_salary_annual` rather than the more literal `min_salary`/
`max_salary` — the period-normalization is a real transform the column name should self-document,
the one place this deliberately isn't an exact mirror of experience's naming. No `salary_period`
column: once stored, every figure is already annual, so period stops being a comparable dimension.

**Pipeline wiring** follows `experience.extract()`'s existing two call sites exactly — no new
stage: `doc_prep.to_meta()` (embed time) and `update_meta.refresh_row()` (the ADR-0061 refresh
sweep, with a `_rederive_salary_without_text()` one branch shorter than experience's version,
since there's no seniority tier to fall through to). One shared `DERIVATIONS_VERSION` bump covers
both families — simpler than a second watermark file; the wasted recompute on an unrelated bump is
cheap regex work, not network or LLM cost, and this can be revisited if that assumption stops
holding.

**Tier 1 is a per-ATS dispatch, not one universal regex.** `Job.salary` strings are formatted by
*our own* per-scraper code (9 known shapes today — lever/recruitee/teamtailor converge on one
shape, keka and darwinbox each need their own), not organic free text pulled from 9 unrelated
sources — so a small `{ats: parser}` table, calibrated against each scraper's real known format,
beats one generic pattern guessing across shapes that don't converge. An ATS without a calibrated
parser yet falls through to a conservative generic reader that under-extracts rather than
mis-extracts.

**Tier 2 is evidence-built, not speculative.** Every pattern and guard in the initial version came
from reading real `workable` description text during the pilot — including two confirmed
false-positive classes (company revenue/funding narrative, benefit-contribution amounts like an
HSA/401k figure) that would otherwise misread as salary, exactly the way `experience.py`'s
narrative guards exist for company-tenure phrases. LPA ("Lakhs Per Annum") gets its own
first-class pattern given this project's explicit India-strong-segment scope, built proactively
even though the pilot ATS's own sample happened not to surface an LPA-phrased posting — the
pattern is a near-certain future need across other ATSes, not a speculative one.

## Alternatives considered

- **Normalize every figure to USD.** Rejected: requires a maintained, refreshed FX-rate table —
  staleness risk and an ongoing dependency for a feature that doesn't need it. A `has_salary`-style
  presence filter and even a same-currency range filter both work without it.
- **Add a seniority/title-based salary estimate**, mirroring experience's Tier 3. Rejected on the
  fabrication risk described above — the project owner's explicit call, not a close one.
- **One universal Tier-1 regex** across all 9+ scraper formats. Rejected: the formats are our own
  controlled output, and a per-ATS dispatch calibrated against each one's real shape is more
  accurate than a generic pattern approximating all of them, for a similarly small amount of code.
- **Sample every live board immediately (no 3000 cap).** Rejected on cost: several ATSes have
  tens of thousands of live boards, and a full-population first pass across 20 ATSes would be
  hundreds of thousands of requests before a single pattern was validated. The cap is explicitly a
  first pass, not a ceiling — `docs/salary-extraction/README.md` frames a full-CSV sweep as later,
  optional, opt-in work once patterns are validated on the capped sample.

## Consequences

- Search can eventually filter on a real numeric salary range, same-currency — not built yet (the
  `has_salary` UI checkbox stays boolean-only for now), but the schema doesn't block it, the same
  way `min_years` only got a UI slider after its own schema landed.
- Every ATS's real coverage is unmeasured until that ATS gets its own pass — "already has a raw
  `salary` field" (9 of 20 ATSes) is not the same claim as "coverage is good," and per-ATS docs
  make that distinction explicit rather than implying the 9 are already handled.
- A generic-currency `$` is resolved to USD absent a stronger signal (statistically dominant in
  this corpus) — a deliberate, named guess, not a silent one; genuinely ambiguous cases (no unit,
  no period marker, mutually-inconsistent multiple ranges in one description) return `None` rather
  than guess, extending the no-fabrication principle from estimation to disambiguation.
- Known, explicitly documented gaps from the pilot (European decimal-comma monthly figures,
  narrower label phrasings like "Compensation Base:") are left unresolved rather than chased into
  an unbounded pattern list from single examples — `docs/salary-extraction/workable.md` names them
  for a future pass to pick up if they turn out to matter at scale.
