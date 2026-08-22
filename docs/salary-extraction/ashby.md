# Ashby — salary extraction findings

Seventh ATS in the salary-extraction initiative. See `README.md` for the overall process and the
six prior passes: `workable.md` (pilot), `workday.md`, `greenhouse.md`, `smartrecruiters.md`,
`zoho.md` (three real bugs, two review rounds), `teamtailor.md` (three more shared-code bugs, two
review rounds — its own carried-forward lesson directly motivated this pass's headline finding).

Ashby is one of the 9 ATSes that already populated `Job.salary` from a structured field. This pass
found that field was itself incomplete — a human-formatted summary string one level shallower than
the real structured data Ashby's API actually returns — and fixing it at the source produced, by a
wide margin, the highest coverage of any ATS in this initiative so far.

## Methods tried

- **Live board count re-measured: 3,823** (the plan's 4,347 figure was stale). Listing-only
  (`has_detail_pass = False`, confirmed by reading the scraper — `includeCompensation=true` adds
  the compensation block to the one listing response, no per-job detail fetch), so the sampling
  script needed no new adapter.
- **The headline finding, directly applying teamtailor's own carried-forward lesson** ("always
  check for structured data one level deeper than what a scraper currently extracts before
  accepting a 'free text' characterization as complete"): `ashby.py` was extracting
  `compensationTierSummary`, a human-formatted string (`"$80K – $100K • Offers Bonus"`) — real,
  legitimately mixed prose, which is why PR #238/#239's own code comments correctly characterized
  ashby as "genuinely free text" for `_field_generic`'s safety analysis. But `includeCompensation=
  true` returns much more than that summary: `compensationTiers[].components[]` carries a fully
  structured breakdown — `compensationType` (`"Salary"` vs `"Bonus"`/`"Commission"`/
  `"EquityPercentage"`/`"EquityCashValue"`), `interval` (`"1 YEAR"`/`"1 HOUR"`/`"1 MONTH"`/
  `"1 WEEK"`/`"1 TIME"`), `currencyCode`, `minValue`, `maxValue` — the same clean shape lever's own
  `_salary()` already produces. Confirmed by direct API inspection (150 real boards, 1,972 jobs):
  34.0% had a real, populated Salary-typed component — the highest structured-field-presence rate
  measured for any ATS in this initiative (previous best: teamtailor's 9.8%). Zero tiers had more
  than one Salary component; 13/1,972 jobs had multiple compensation tiers (takes the first tier
  with a usable Salary component).
- **Fixed at the source** (the plan's own pre-approved "fix ambiguity at the source when the
  source has more context than the final string does" latitude): `ashby.py`'s `parse()` now
  extracts the structured Salary component and formats it as `"80000-100000 USD 1 YEAR"` — the
  same range+currency+interval shape lever/recruitee/teamtailor already produce. **A "1 TIME"
  interval (a one-off payment — real: "Compensation per finished project", an onboarding rate, 3
  confirmed occurrences) is deliberately excluded** rather than guessed at as if it were a
  recurring annual salary, the same no-fabrication principle every other Tier-1 parser follows.
- **Renamed the now-4-caller shared parser** from `_field_lever_recruitee_teamtailor` to
  `_field_range_currency_interval` (CLAUDE.md's naming rule: a name enumerating 3 ATSes goes stale
  the moment a 4th genuinely-matching caller joins) and registered `"ashby"` in `_FIELD_PARSERS`.
  Verified zero impact on every already-merged/sampled ATS via the full cross-ATS diff (a pure
  rename plus one new dispatch entry cannot change behavior for existing callers) — confirmed:
  0 differences across all 6 already-processed corpora.
- **Investigating the 1.9% residual field-parse-failure rate surfaced two more real, evidence-
  measured gaps in the shared parser itself** — not ashby-specific dispatch, genuine gaps in
  `_field_range_currency_interval`/`_period_multiplier_structured` that only ashby's real data
  shape happened to expose:
  1. A `"1 WEEK"` interval (a contractor-style weekly rate) wasn't recognized at all — 50 real
     occurrences across 10 distinct values (`"796 USD 1 WEEK"`, `"2500-3500 USD 1 WEEK"`, both
     annualize plausibly at ×52). Added `_WEEKLY_TO_ANNUAL = 52` and a bare `"week"` check,
     mirroring the existing hour/day/month treatment exactly.
  2. `_field_range_currency_interval` only ever handled a `_RANGE` match, silently dropping every
     bare SINGLE value with no dash (a fixed-rate compensation tier with only one of
     minValue/maxValue set) — 24 confirmed real cases on ashby, **zero on teamtailor's own corpus
     when checked** (so this was a genuine pre-existing gap in the shared parser that teamtailor's
     own real data shape never happened to expose, not a bug already silently shipped to a merged
     ATS). Added a `_SINGLE_NUM` fallback matching `_field_generic`'s existing pattern. Verified a
     placeholder/test value some company's Ashby config left in place (real: `"0.01 USD 1 HOUR"`,
     `"0 USD 1 YEAR"`) still correctly declines via the plausibility bounds rather than extracting
     as if genuine.
- **Tier-2 gap analysis found one more real, evidence-measured pattern**: an explicit
  `"minimum $X ... maximum $Y"` compensation-band disclosure (real: `"minimum annual salary of
  $169,200, a midpoint of $199,100, and a maximum salary of $228,900"`; `"Minimum $320K - Maximum
  $390K USD"`) was being fragmented by `_LABELED`, which independently matches `"...salary of
  $169,200"` and `"...salary of $228,900"` as two separate, mutually-inconsistent spans and
  declines the whole thing as ambiguous — a real false-ambiguity case, not just a coverage gap.
  23 confirmed occurrences (2 companies: jobber, xero). **Fixed** with a new `_scan_min_max_band`
  tier, checked before `_LABELED` for the same reason `_scan_level_bands`/`_LPA` already are.
  **A third company's superficially similar text was checked closely and found to be a genuinely
  different pattern**: scribdinc's `"the reasonably expected salary range is between $171,000
  [minimum salary in our lowest geographic market...] to $267,000 [maximum salary in our
  highest...]"` looked like a min/max pair from a truncated snippet, but reading the FULL
  surrounding text revealed it's actually `"between $X ... to $Y"` with an inline bracketed
  geographic aside — `_BARE_BETWEEN` only supports `"and"`, not `"to"`, and this specific shape
  also requires tolerating an arbitrary bracketed interruption between the number and the
  connector. Left as a known, documented gap rather than widening the new pattern to a shape it
  wasn't built or verified for (see Known gaps).
- **The mandatory full cross-ATS diff for the min/max band fix produced an unexpected, entirely
  positive bonus**: this pattern also recovers real, previously-missed salary bands on FOUR of the
  five already-merged ATSes it was checked against (workday: 21 gained, greenhouse: 11 gained,
  smartrecruiters: 1 gained, zoho: 5 improved from unresolved to resolved currency) — every single
  one hand-traced against real text, not sampled (see What changed in code). Only workable and
  teamtailor showed zero difference.
- **Code review (Standards axis) caught and live-reconfirmed a real bug**: `_salary()`'s range
  formatting used Python truthiness (`lo and hi`, `lo or hi`) instead of `is not None` checks, so a
  genuine `minValue=0` was treated as absent. Live-reconfirmed against Ramp's real board the same
  day (2026-08-22): a real "Solutions Consultant, Post Sales, Enterprise" posting has
  `minValue=0, maxValue=250000, currencyCode=USD, interval="1 YEAR"` — the buggy code silently
  reported this as `SalarySpan(250000, None, "USD")`, i.e. "$250k+, no ceiling," the exact inverse
  of the true "$0–$250k" disclosure — the same class of silent floor/ceiling corruption the
  no-fabrication principle exists to prevent, not a safe decline. Fixed with `is not None` checks
  on both branches (the range case and the single-value fallback's `str(lo or hi)`, which had its
  own, lower-impact mirror defect: `minValue=0, maxValue=None` rendered the literal string
  `"None"`). Fixed, the Ramp example now correctly reaches `_bounded` as `(0, 250000)`, which
  **declines** it — 0 fails the $10k USD floor — rather than reporting either wrong value; a
  correct decline, not a corrected extraction, and additional confirmation the plausibility bounds
  are doing real work. A direct re-probe of 4 real boards immediately after the fix (820 real
  Salary components: ramp, openai, notion, linear) found exactly 1 more `minValue=0` case (the
  same Ramp job) and, checked at the same time, **zero** occurrences of the mirror shape (`hi` set,
  `lo` unset — a ceiling-only "up to $X" tier) — real, permitted by the schema, but with no
  measured occurrence to build special handling against, so it's deliberately left on the existing
  bare-single-value path (which reads it as floor-only) rather than guessed at (see Known gaps).
- **The 15-currency unsupported-currency gap was measured and deliberately left undone**: PHP
  (32), JPY (14), HUF (12), COP (11), MXN (9), CRC (5), KRW (4), SGD (3), TWD (3), CNY (2), ZAR
  (2), KHR (2), CZK (1), PLN (1), BRL (1) — 102 total real occurrences, genuinely global (Ashby's
  customer base skews toward well-funded startups hiring internationally). Unlike the narrower
  1-2-currency gaps fixed on earlier passes, adding responsible plausibility bounds for 15
  different currencies is real, dedicated cross-currency economic research this pass's scope
  doesn't stretch to — left as a documented, evidence-based gap, not silently dropped.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 3,000 of 3,823 live boards, 2,960 clean
  successes (98.7%, no rate-limit issue found — a short 300-board dry-run at the standing 32-worker
  default came back clean, unlike teamtailor's rolling-window wall).
- Measured both required percentages: **yes** — 38.9% structured-field presence, 49.7%+ overall
  Tier1+Tier2 coverage (see Coverage for the fully-fixed final number), reported alongside every
  intermediate measurement as each of the four code fixes landed, not just the final total.
- Live-verified after code changes: **yes** — a fresh, differently-seeded 50-board sample
  (seed=2026, 32 workers, 0 errors) after all four extraction fixes, 65.4% real coverage on that
  sample, 10 extractions spot-checked for plausibility, all genuine (including a €65k-90k EU role,
  correctly resolving EUR). A second, larger confirmatory check (400 boards, seed=3001, run after
  a code-review round found and fixed a real zero-value bug) reproduced the headline 49.7% figure
  within noise on a combined 3,093-board pool (50.0%) — see Live-verification review.
- Code review found a real, live bug: **yes, and fixed** — Standards axis review caught
  `_salary()`'s truthy check silently dropping a genuine `minValue=0`, live-reconfirmed against a
  real Ramp posting the same day, fixed with `is not None` checks, and covered by two new
  regression test cases. This is exactly the review process working as this initiative intends —
  see Methods tried for the full account.
- Went beyond the ask, substantially: found and fixed a genuine gap in the SHARED parser
  (`_field_range_currency_interval`'s missing single-value handling) that had nothing to do with
  ashby's headline structured-field fix — evidence-measured, not guessed, and confirmed zero
  impact on the already-merged ATS using the same function. Found that a new Tier-2 pattern built
  for ashby's own gap ALSO recovers real coverage on four already-merged ATSes, hand-traced every
  one of those 38 differences against real text rather than trusting the aggregate diff count.
  Caught, mid-investigation, that a THIRD company's apparent match for the new min/max pattern was
  actually a different shape entirely once the full text (not a truncated snippet) was read —
  correctly scoped the fix to what was actually verified, not generalized past the evidence.
- Did not: add support for the 15 unsupported currencies (102 occurrences, too fragmented to
  responsibly add without dedicated per-currency research). Did not build the scribdinc
  `"between...to"` gap (16 occurrences, one company, needs a genuinely different, more invasive
  fix than this pass's evidence justifies building blind). Did not chase the handful of
  genuinely-broken-source-data cases (a $0.01/hour or $0/year placeholder value, a 70-90,000
  spread that's clearly a formatting artifact) — correctly and safely declined already, not a gap.

## Live-verification review

Fresh, 50 boards, seed=2026, `--workers 32` (confirmed safe — no rate-limit issue for ashby,
unlike teamtailor). 50/50 boards succeeded, 0 errors. 664 jobs, 434 real Tier1+Tier2 extractions
(65.4% — higher than the 3,000-board sample's 49.7%, plausible small-sample variance given
Ashby's customer base skews toward well-funded, comp-transparent startups and a 50-board draw can
easily over- or under-sample that population). All 10 spot-checked extractions were genuine and
plausible: a Business Development Manager ($160k–$180k), a BDR ($80k–$115k), a Mission Architect
($137k–$203k), a Revenue Operations admin ($140k–$180k), an Office Manager ($75k–$100k), a Senior
State Estimation Engineer ($200,454–$260,590 — an unusually precise band, genuine per-level comp
banding), a Product Manager ($161k–$225k), a Staff Software Engineer ($190k–$210k), and a
Staff Product Designer based in Europe (€65k–€90k, correctly resolving EUR) — all genuine, no red
flags.

**A second, larger confirmatory check** (post the zero-value fix, 2026-08-22): the plan's own
verification checklist asks the coverage claim be checked "against the served LanceDB table...
not just the frozen sample." The served table's HTTP `/search` endpoint now sits behind a sign-in
wall (ADR-0042, added since the README's own documented `curl` example was written — the deployed
app has moved on from that doc, worth a separate flag), and a direct `snapshot_download` of the
full served table is 1.87 GB against a resource CLAUDE.md explicitly calls out as storage-cost-
constrained — disproportionate for a single sanity check this initiative's other 5 non-pilot
passes already chose to skip entirely. Took the cheaper, and arguably fresher, path instead: a
fresh 400-board sample (`--seed 3001`, distinct from both the original `--seed 7` and the
live-verification `--seed 2026`) against Ashby's own live API directly — the same source the
served table is itself built from, one pipeline cycle fresher. Re-running `headstart.salary.
extract()` over the combined artifact pool this produced (3,093 distinct boards once merged with
the original sample, 48,891 jobs — closer to the full 3,823-board population than either
individual run) gives: 39.0% field-present, 38.4% Tier 1, 11.6% Tier 2, **50.0% overall**,
62.9% of boards with ≥1 hit — all within a few tenths of a point of the headline 38.9%/49.7%/64.7%
figures above. The headline numbers are reported from the original, single, cleanly-seeded
3,000-board run rather than restated from this mixed-seed pool (methodologically cleaner: one
run, one seed, fully reproducible) — this check exists only to confirm they hold up, which they
do. (The zero-value fix's own effect on this recheck is real but far below reporting precision:
1 confirmed occurrence per ~820 real Salary components checked, ≈0.1%.)

**What this recheck does and doesn't cover, precisely**: it confirms the extraction logic itself
is stable against sampling variance — both draws hit Ashby's own live API directly. It does *not*
exercise the served pipeline downstream of the scrape (tech filter, description store, embed,
LanceDB sync) the way a genuine served-table check would, so it can't catch drift introduced
between scrape and serve specifically. That residual gap is the same one all 5 non-pilot passes
before this one already carried; nothing here closes it, only the honesty of naming it does.

## Patterns found

- **Structured compensation data one level deeper than what the scraper was extracting** — the
  single highest-value finding of this entire initiative so far, directly recovered by applying a
  lesson carried forward from the immediately prior pass rather than treating "already has Tier 1"
  as settled.
- **A "1 WEEK" contractor-style interval** and **a bare single-value compensation tier** — both
  real gaps in the shared parser that only ashby's specific real data shape exposed.
- **An explicit "minimum $X ... maximum $Y" compensation-band disclosure** — a real, if
  company-concentrated, corporate-transparency phrasing that turned out to also help four other
  already-merged ATSes once built.
- **A genuinely global company base**: real salary/compensation-tier data in USD, EUR, CAD, GBP,
  HUF, INR, and — among the currently-unsupported set — PHP, JPY, COP, MXN, CRC, KRW, SGD, TWD,
  CNY, ZAR, KHR, CZK, PLN, BRL. No other ATS sampled so far has shown this much currency diversity.
- **Company revenue/funding/valuation narrative remains the dominant false-positive risk class**
  in real misses ("$300M+ in annual transactions", "$1.7 trillion problem", "$220M in total
  funding", "$11 billion" valuation, "$27M raised") — every one read here was already correctly
  declined by the existing patterns' structural requirements (no label word, no range, no hourly
  marker nearby), not a new gap.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 3,823 live) | 3,000 attempted, 2,960 clean (98.7%) |
| jobs seen | 47,767 |
| jobs with a structured `salary` field (`Job.salary`) | 18,578 (38.9%) |
| of those, extracted via Tier 1 | 18,263 (38.2% of all jobs; 98.3% of field-present jobs) |
| extracted via Tier 2 (description, no usable field) | 5,497 (11.5%) |
| **overall Tier1+Tier2 coverage** | **23,760 (49.7%)** |
| boards with ≥1 job showing a real signal | 1,894/2,926 (64.7%) |

**By far the highest coverage of any ATS in this initiative** (workable 15.4%, workday 27.6%,
greenhouse 36.1%, smartrecruiters 10.0%, zoho 9.2%, teamtailor 14.1%, **ashby 49.7%**) — driven
almost entirely by the structured-field fix: Tier 1 alone (38.2%) already exceeds every other
ATS's *combined* Tier1+Tier2 total except greenhouse's. This isn't a weaker-signal-elsewhere
story the way some of the lower-coverage passes were — it's a genuinely different company mix
(modern, VC-funded startups using Ashby's own comp-transparency tooling) combined with a
structured field that, once correctly read, is extremely reliable.

## What changed in code, and why

- `src/headstart/scrapers/ashby.py`: new `_salary(compensation)` helper extracting the structured
  Salary-typed `compensationTiers[].components[]` entry instead of `compensationTierSummary`,
  formatted to match `_field_range_currency_interval`'s expected shape. `url()` is unchanged
  (`includeCompensation=true` was already being requested; the fix only changes which part of the
  already-fetched response `parse()` reads) — confirmed no `verify-search-filters` re-run is
  needed, since that skill exists to catch a *new* ATS's URL-shape gap, and no URL construction
  changed here at all.
  **`_salary()`'s range formatting uses `is not None` checks, not truthiness** (code-review fix —
  see Methods tried): `lo and hi`/`lo or hi` silently dropped a real `minValue=0` (Ramp,
  live-reconfirmed), inverting "$0–$250k" into "$250k+, no ceiling." Fixed; the corrected pair now
  reaches `_bounded` as `(0, 250000)` and is correctly declined, not misreported.
- `src/headstart/salary.py` (all four fixes below are shared code, not ashby-specific dispatch):
  - **Renamed** `_field_lever_recruitee_teamtailor` → `_field_range_currency_interval`; registered
    `"ashby"` in `_FIELD_PARSERS`. Confirmed zero behavior change for lever/recruitee/teamtailor
    via the full cross-ATS diff.
  - **`_period_multiplier_structured`**: added `_WEEKLY_TO_ANNUAL = 52` and a bare `"week"` check.
  - **`_field_range_currency_interval`**: added a `_SINGLE_NUM` fallback for a bare value with no
    captured range, mirroring `_field_generic`'s existing pattern exactly.
  - **`_scan_min_max_band`** (new): a `"minimum $X ... maximum $Y"` band, checked before
    `_LABELED` in `from_description`'s cascade (alongside `_scan_level_bands`/`_LPA`, for the same
    reason both already run first — a narrower, competing pattern would otherwise fragment and
    decline it).
- `tests/test_scrapers.py`: 1 existing test (`test_ashby_parse_skips_unlisted`) updated — its
  fixture predates `compensationTiers` entirely (only `compensationTierSummary` is present; every
  real, current ashby response carries the `compensationTiers` key even when empty), so the
  correct new expected `salary` value is `None`, not the old summary string. 1 new parametrized
  test (`test_ashby_salary_from_structured_compensation_tier`, 6 cases) covering the real
  extraction shape directly, including the "1 TIME" exclusion and an equity-only-no-salary case.
- `tests/test_salary.py`: 84 tests total (up from 79 at teamtailor's merge) — 5 new
  (`test_field_range_currency_interval_ashby_structured_tier`, `..._bare_week`,
  `..._bare_single_value`, `test_description_min_max_band`,
  `test_description_min_max_band_without_a_maximum_falls_through`), plus 2 pre-existing tests
  (`test_field_generic_bare_word_period_not_recognized_in_free_text`,
  `test_field_generic_up_to_states_a_ceiling_not_a_floor`) updated from using `"ashby"` to
  `"personio"` as their `_field_generic`-safety example ATS, since ashby itself moved OFF
  `_field_generic` once its own compensation data turned out to be structured.

### Cross-ATS impact of the shared `salary.py` fixes

Two separate full cross-ATS diffs were run this pass (main's frozen `salary.py` vs. the current
working tree, across all 6 previously-processed ATSes' frozen corpora): one confirming the rename,
the WEEK support, and the single-value fallback are a genuine zero-op for every already-processed
ATS (0 differences across all 6), and a second specifically for the new `_scan_min_max_band`
pattern. The first diff's "0 across all 6" is real but weaker than it sounds: only teamtailor's
`Job.salary` dispatches through `_field_range_currency_interval` at all among those 6 (lever and
recruitee, the parser's two other real callers, are both still "not started" per the status
table) — the other 5 ATSes are structurally guaranteed a zero diff regardless of whether the fix
is correct, since none of their data ever reaches this function. Only teamtailor's corpus is a
genuine behavioral check, and it too shows zero difference (real: teamtailor's own data has no
WEEK intervals or bare single values in the sampled corpus).

| ATS | differences | direction |
|---|---:|---|
| workable | 0 | — |
| workday | 21 | all gained (real bands recovered, hand-traced, e.g. "Salary minimum: $115,000; Salary maximum: $140,000") |
| greenhouse | 11 | all gained (e.g. "Minimum: $164,114 Maximum: $186,512") |
| smartrecruiters | 1 | gained ($475,000–$600,000, a specialist physician role) |
| zoho | 5 | improved: same figure, `currency=None` → `currency='INR'` (the wider matched span happened to also capture a currency code a narrower prior match had missed) |
| teamtailor | 0 | — |

Every one of these 38 differences was individually read against real source text before accepting
the diff, not sampled — including confirming the workday case's matched span is genuinely the
salary disclosure ("Salary minimum: $115,000; Salary maximum: $140,000"), not an accidental bridge
to an unrelated "Minimum Qualifications" heading earlier in the same description (the regex's
required nearby-dollar-sign window correctly prevented that). This fix will also improve the five
already-merged ATSes' data once this PR merges and the pipeline's derived-field refresh next runs.

## Known gaps, left honestly unresolved rather than guessed at

- **15 unsupported currencies** (PHP, JPY, HUF, COP, MXN, CRC, KRW, SGD, TWD, CNY, ZAR, KHR, CZK,
  PLN, BRL — 102 total real occurrences) — genuinely global, but too fragmented across this many
  currencies to responsibly add without dedicated per-currency plausibility-bound research.
- **scribdinc's "between $X [bracketed geographic aside] to $Y" pattern** (16 real occurrences,
  one company) — superficially resembled the min/max band pattern from a truncated snippet, but
  turned out to be a different shape on reading the full text: `_BARE_BETWEEN` only supports
  `"and"`, not `"to"`, and this shape also needs to tolerate an arbitrary bracketed interruption
  between the number and the connector. Real, but a genuinely different, more invasive fix than
  this pass's evidence justifies building blind.
- **Genuinely broken/placeholder source data** (`"0.01 USD 1 HOUR"`, `"0 USD 1 YEAR"`, a
  `"70-90000 USD 1 YEAR"` spread that's clearly a dropped-digit formatting artifact) — correctly
  and safely declined by the plausibility bounds; not a code gap at all.
- **A ceiling-only compensation tier** (`maxValue` set, `minValue` unset — the structural mirror of
  the real floor-only shape `_SINGLE_NUM` already handles) is permitted by Ashby's own schema (the
  `_salary()` guard checks `lo is None and hi is None`, not either alone, specifically because one
  can be set without the other) but was checked directly against live data during the zero-value
  fix — 0/820 real Salary components across 4 boards — and has no confirmed real occurrence to
  build against. Left on the existing bare-single-value path, which reads any lone value as a
  floor, not a ceiling; if a real occurrence ever surfaces, it would silently invert the same way
  the zero-value bug did, so this is worth a direct re-check on the next pass that touches this
  parser rather than assuming it stays zero forever.

## Carried forward from workable through teamtailor — and new lessons

- **Applied, and this is the whole story of this pass**: teamtailor's own carried-forward lesson —
  "already has Tier 1 is not the same as Tier 1 works" — led directly to checking whether ashby's
  raw API had richer data than what the scraper extracted, which it did, spectacularly.
- **Applied**: hand-trace every diff example against real text before accepting a fix, not just
  the aggregate count — this is what caught the workday "Minimum Qualifications" question (and
  resolved it correctly, with evidence) and what caught scribdinc's true, different shape.
- **Applied**: measure a guard/gap candidate's real prevalence before building or declining —
  every one of this pass's four fixes and two declined gaps has a real, precise measurement behind
  it, not an estimate.
- **New, the headline lesson of this whole pass**: when a scraper already populates a structured
  field from an ATS's API, check whether that ATS's raw response carries EVEN MORE structure one
  level deeper than what's currently extracted — `includeCompensation=true` was already being
  requested, but only the human-formatted summary was being read, not the fully structured
  breakdown sitting right next to it. This is worth checking on every remaining ATS that already
  populates `Job.salary`, not just ones flagged as "genuinely free text."
- **New**: a shared field-parser built and verified against one ATS's real data shape can still
  have real, undiscovered gaps that only a DIFFERENT ATS's real data exposes (`_field_
  range_currency_interval`'s missing single-value handling — zero impact on teamtailor, a real
  24-case gap on ashby). When reusing a shared parser for a new caller, check its behavior against
  the New caller's actual data shape directly, don't assume "it already works for ATS X" implies
  "it's complete."
- **New**: when two real examples look like the same pattern from a truncated snippet, read the
  FULL surrounding text for each before generalizing a fix to cover both — scribdinc's case looked
  identical to jobber/xero's "minimum...maximum" shape until the full text revealed a genuinely
  different underlying structure ("between...to" with a bracketed aside, not a labeled pair at
  all). Building for the truncated-snippet impression alone would have either missed scribdinc
  entirely (if the pattern were narrow) or introduced real risk (if widened to fit both shapes
  without evidence either shape is actually safe).
- **New**: a Tier-2 pattern built to fix one ATS's own gap can recover real coverage on ALREADY-
  MERGED ATSes too, not just the one it was built for — always run the full cross-ATS diff for
  shared-code Tier-2 additions, not just Tier-1/period-multiplier changes, and don't assume a
  Tier-2 pattern's blast radius is naturally narrower just because it's "just a new regex."
- **New**: when a scraper's own `_salary()`-style helper formats REAL numeric fields straight from
  a structured API response (not text needing a regex), check every zero/None branch with
  `is not None`, never truthiness — `0` is a legitimate value for a numeric compensation field in a
  way it's essentially never a legitimate stand-in for "missing" in this codebase's other string-
  matching code, and a truthy check silently drops it exactly where the no-fabrication principle
  is supposed to prevent silent corruption. This is a narrower, sharper case of the "up to states a
  ceiling, not a floor" family of bugs (zoho's pass): there, the ambiguity was in TEXT and needed a
  phrase-detection guard; here the ambiguity was in already-structured numbers and needed nothing
  more than the right null check — worth checking for on every future ATS whose fix touches a raw
  numeric field directly, before it ships rather than after review catches it live.
- **New**: when the plan's own verification checklist asks for a check that's become newly
  expensive or newly blocked since it was written (here: the served table's sign-in wall, and its
  size against a documented storage-cost constraint), don't silently skip it the way five prior
  passes already did — say so explicitly, explain the tradeoff, and substitute the cheapest
  available check that serves the same actual intent (a fresher, differently-seeded live resample
  stood in for "check against a more durable source" here, since it's fresher than the served
  table would be anyway). An explicitly-reasoned skip is a different, better thing than a silent
  one, even when the end action is the same.
