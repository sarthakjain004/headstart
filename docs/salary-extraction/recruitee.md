# Recruitee — salary extraction findings

Eighth ATS in the salary-extraction initiative. See `README.md` for the overall process and the
seven prior passes: `workable.md` (pilot), `workday.md`, `greenhouse.md`, `smartrecruiters.md`,
`zoho.md`, `teamtailor.md`, `ashby.md` (structured-field-one-level-deeper, and a live truthy-vs-
`is not None` bug caught by code review — both lessons applied directly in this pass).

Recruitee is one of the 9 ATSes that already populated `Job.salary` from a structured field
(`_field_range_currency_interval`, shared with lever/teamtailor/ashby). Applying ashby's own two
carried-forward lessons up front — check for structure one level deeper, and check every
zero/None branch on a raw numeric field with `is not None` — both came back negative here:
recruitee's payload is already flat (no nested tiers to discover), and its `min`/`max` values are
JSON **strings**, not the falsy integers that made the ashby bug possible. The real, evidence-
backed findings this pass turned up instead: two real currencies (PLN, CHF) with strong multi-
company evidence but no recognized bounds, and a large, well-measured, honestly-declined class of
period-ambiguous structured-field values that the no-fabrication principle correctly keeps out of
the count rather than guessed at.

## Methods tried

- **Live board count re-measured: 3,534** (the plan's 3,970 figure was stale). Listing-only
  (`has_detail_pass = False`, confirmed by reading the scraper), so no bounded sampling adapter
  was needed.
- **Applied ashby.md's "check for structure one level deeper" lesson — negative result, confirmed
  by direct API inspection**: every real `salary` object seen across 861 real offers (60 boards)
  had exactly one shape, `{currency, max, min, period}` — no nested tiers, no additional
  compensation-type breakdown the way ashby's `compensationTiers[]` had. `_salary()` in
  `recruitee.py` already reads every key present. Nothing to fix here.
- **Applied ashby.md's "check `is not None` vs. truthiness on any raw-numeric-field formatter"
  lesson — also a negative result, but for a genuinely different, verified reason**:
  `recruitee.py`'s `_salary()` has the *exact same* code shape as ashby's pre-fix bug
  (`rng = f"{lo}-{hi}" if lo and hi else str(lo or hi)`), which would carry the identical risk if
  `min`/`max` were ever a falsy JSON integer `0`. Verified directly against 48 real boards / 357
  salary-bearing offers: every `min`/`max` value observed is a JSON **string** (Rails BigDecimal
  serialization convention), and the string `"0"` is truthy in Python — so the failure mode ashby
  had cannot occur here. Re-verified at scale afterward (0 non-string values, 0 zero-valued
  strings across the full sample). This is a documented, evidenced non-finding, not a code change:
  "fixing" something already evidenced safe would be the same mistake as leaving ashby's real bug
  unfixed, just in the opposite direction.
- **Two real, multi-company currencies (PLN, CHF) were unrecognized** — both were already
  producing a `SalarySpan` via `_bounded`'s USD-shaped fallback (so not a coverage loss), but with
  `currency=None` rather than the real value, and calibrated against the wrong bound. Measured on
  the full 3,000-board sample: PLN in 19+ distinct companies (106 real Tier-1 extractions once
  fixed), CHF in 7+ distinct companies (11 real extractions). Added both to `_CURRENCY_CODES` /
  `_MIN_PLAUSIBLE_ANNUAL` / `_MAX_PLAUSIBLE_ANNUAL`, calibrated against real 2026 minimum-wage
  data (Poland: PLN 4,806/mo gross, PLN 31.40/hr, effective 2026-01-01, no mid-year change;
  Switzerland: no national minimum wage, cantonal floors from CHF 20.00-24.59/hr where a canton
  has one at all) rather than guessed round numbers — floor PLN 30,000 (~52% of Polish minimum
  wage annualized), CHF 20,000 (below every 2026 cantonal floor). Ceilings are a garbage backstop
  rather than a tight real-world cap, so precision matters less here, but the reasoning is stated
  honestly rather than dressed up: PLN 3,000,000 leaves generous headroom for senior Polish
  tech/finance pay; CHF 900,000 reuses CAD/AUD's raw figure, which — since CHF trades meaningfully
  stronger than either — is genuinely *more* generous in real terms, not merely "matching," a
  deliberate choice given Switzerland's high-earning finance/pharma sector rather than an
  equivalence claim (corrected during code review, which caught the original wording implying a
  same-tier match it didn't actually make).
  A handful of weaker-evidence currencies (JPY 4 companies, DKK 3, and 25 more codes at 1-2
  companies each) were measured and deliberately left unsupported — see Known gaps.
- **A large, real, well-measured Tier-1 parse-failure class was investigated and deliberately left
  unfixed**: 1,736 jobs (8.4% of field-present jobs) have a populated `salary` field that still
  fails to parse. Reading the real values found period unreliability behind all of them, in
  several shapes — the two largest, individually verified:
  1. **`period` is populated but appears wrong for a real subset of tenants** (533 postings, 117
     distinct companies) — many EUR/"month" values are clearly hourly rates (`"15-25 EUR month"`,
     `"14.50-14.50 EUR month"` — both bounds in a 10-25 band, an unmistakably hourly not monthly
     figure) or genuine low apprenticeship/internship stipends (a French CFA apprenticeship board,
     `ascor`, alone contributes 210 of the 533 with a real, legally-tiered stipend range,
     `"492.22-1823.03 EUR month"`, that reads as unusually low only because French apprentice pay
     is a percentage of minimum wage scaled by age/year) or outright placeholder zeros
     (`"0-0 EUR month"`, real on 9+ companies including one with 14 identical postings). These
     three sub-patterns are visually indistinguishable in the raw field alone.
  2. **`period` is entirely absent** (740 postings, 191 distinct companies) — `"1500-2700 EUR"`,
     `"16-18 EUR"`, both plausible but for different periods (monthly vs. hourly) with nothing in
     the field itself to disambiguate.
  The remaining 463 (27%) are smaller variations on the same root problem — including the same
  mislabeling running in the opposite direction, an annual figure read as monthly — plus a few
  genuinely distinct minor patterns (placeholder sentinel values, non-EUR zero-pairs); none change
  the conclusion, so none are guessed at either. All would require inferring the period from
  magnitude alone (a value under ~30 is "obviously" hourly, one in the low thousands is "obviously"
  monthly) — a materially different, more speculative kind of inference than anything else this
  initiative has built: every other guard/pattern infers from stated text or structure, never from
  overriding a source's own field based on what the number "looks like." Declining to build this
  is a direct application of the no-fabrication principle, not an oversight — see Known gaps for
  the full accounting of every sub-pattern found.
- **Tier-2 gap analysis, English-language misses only**: sampled real "no field, no Tier-2 hit"
  descriptions that mention a currency symbol or salary-adjacent word. The dominant classes were
  already-correctly-declined funding/valuation narrative (`"$85M in the bank"`, `"€1.2B exit"`) —
  guards working as intended — and a single company (`rebootmonkey`) reposting one templated
  listing across six different currencies (DKK, RON, ZAR, COP, PHP, KRW), each too thin on its own
  to justify support. One candidate pattern, a bare `"$X to $Y"` connector with no "between," was
  measured directly: 3 real matches across 2 companies in the whole 3,000-board sample, one of
  which (`"$2,500 to $5,000 monthly revenue range"`) is a **false-positive risk**, not a salary at
  all. Both too rare and immediately risky — correctly left unbuilt.
- **A genuinely global, non-English customer base measured directly, not guessed at**: of the
  real "no field, no Tier-2 hit" misses that were sampled and run through `langdetect`, only 38.5%
  were English; 27.8% Dutch, 26.5% German, 4.2% French, with a long single-digit tail. This is
  consistent with, not a gap introduced by, this project's own explicit English-only search-corpus
  scope (CLAUDE.md: non-English boards are scraped but held out of the index until multilingual
  retrieval is added) — building German/Dutch/French Tier-2 patterns would be substantial new
  cross-cutting scope (three new languages' worth of period markers, number formats, and
  false-positive guards, none of it reachable by the current search index anyway), not a surgical
  extension of this pass. Flagged as a real, measured, out-of-scope-by-design finding, not silently
  absorbed into a lower coverage number without explanation.
- **A 3,000-board sample (`--seed 7`, 32 workers) hit a 429 wall affecting 1,430/3,000 boards
  (47.7%)** — but bucketing errors by position (per the established diagnostic) showed a
  genuinely different shape from teamtailor's rolling-window pattern: a noisy 17%-64% error rate
  from the very first 100-board window onward, no clean prefix, no sharp trip point. Diagnosed as
  simple too-many-concurrent-connections for this specific host, not a sustained-volume limit — a
  10-board dry-run at 4 workers confirmed (9/10 recovered, the 1 failure a genuine 404). A full
  retry of all 1,430 failures at 4 workers recovered 1,425 (99.65%, even higher than teamtailor's
  98.7%) in 370 seconds, leaving only 5 genuine failures (2 confirmed dead boards, 3 other). Final
  corpus: 2,995/3,000 boards, 56,336 jobs.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 3,000 of 3,534 live boards, 2,995 clean
  after the rate-limit retry (99.8% effective success rate).
- Measured both required percentages: **yes** — 36.5% field presence, 38.2% overall Tier1+Tier2
  coverage, plus the full parse-failure breakdown (not just a raw percentage — the two ambiguity
  buckets are separately counted and evidenced, not lumped into an unexplained residual).
- Live-verified after code changes: **yes** — a fresh, differently-seeded 50-board sample
  (seed=101, 8 workers — deliberately reduced given this pass's own rate-limit finding, not the
  standing 32-worker default, since 32 is now a known-bad concurrency for this specific host) after
  the PLN/CHF fix, 45/50 clean.
- Checked the coverage claim against a more durable source than the frozen sample, per the plan's
  own verification checklist: **partially, same tradeoff ashby.md already named, not silently
  repeated**. The served table's `/search` HTTP endpoint is still behind ADR-0042's sign-in wall,
  and a full LanceDB `snapshot_download` is still ~1.87 GB against CLAUDE.md's documented
  storage-cost constraint — the same reasoning ashby's pass gave for substituting a fresher live
  resample instead of the literal served-table check. This pass's own 50-board seed=101 sample
  (above) serves that same role here; called out explicitly in this checklist rather than left for
  a reader to notice its absence, which is the gap this exact line exists to prevent repeating.
- Applying ashby.md's two carried-forward lessons at the start of the pass (rather than
  rediscovering them) is what README.md's own process asks for every pass, not extra scope — both
  came back negative here, and the negative result is reported with the same rigor as a positive
  one would be (two independent live-data checks, not an assumption). Genuinely additional beyond
  the baseline ask: a real `langdetect` measurement (not a guess) to characterize the non-English
  miss population precisely, and directly measuring a candidate Tier-2 pattern (`"X to Y"`) rather
  than skipping it on intuition — it turned out both rare and risky, with the specific
  false-positive it produces documented rather than just a low count reported.
- Did not: build magnitude-based period-inference for the two large, honestly-declined ambiguity
  buckets (1,273 jobs, 73% of all parse failures) — real coverage left on the table, but building
  it would cross from evidence-based extraction into guessing at what a field's own label should
  have said, a different and more speculative class of inference than anything else in this
  module. Did not build German/Dutch/French Tier-2 patterns despite them covering the majority of
  Tier-2 misses — consistent with, not a deviation from, the project's own English-only search
  scope. Did not add the 27 weaker-evidence currencies (JPY down to single-company codes) —
  correctly recognized fragmentation rather than force a fix past the evidence.

## Live-verification review

Fresh, 50 boards, seed=101, `--workers 8` (deliberately below the standing 32-worker default —
this pass's own dry-run showed 32 workers produces a noisy ~45-64% error rate specific to this
host, not the clean rolling-window pattern a reduced-concurrency retry alone would fix; 8 workers
kept this small confirmatory run itself clean). 45/50 boards succeeded, 5 genuine 429s (10% —
consistent with "meaningfully better at lower concurrency," not zero, which is expected for a
still-nontrivial worker count). 368 jobs, 45.1% field presence / 53.3% raw signal on this specific
small draw — higher than the 3,000-board headline, the same company-size-composition variance
ashby's own pass already documented as the expected shape for a small draw, not a discrepancy to
chase. The PLN/CHF fix was confirmed live and functioning on the full corpus (106 and 11 real
extractions respectively, both previously sitting in the `currency=None` bucket).

## Patterns found

- **The `_field_range_currency_interval` shared parser needed zero recruitee-specific changes** —
  the first ATS in this initiative's "already-partially-handled" group where the existing Tier-1
  path just worked once sampled and traced, a useful confirmation that not every pass needs a
  code fix to be worth running (the PLN/CHF and gap-measurement work still produced real value).
- **Two real, evidenced currencies (PLN, CHF) with genuine multi-company salary disclosures** —
  recruitee's own customer base reaches further beyond its Netherlands/EU home turf than any prior
  ATS's currency spread suggested, though still overwhelmingly EUR (16,455 of 18,799 Tier-1
  extractions, ~87.5%).
- **A structured field's own `period` value can be wrong for a real subset of tenants** — the
  clearest new pattern class this pass surfaces: recruitee's platform apparently doesn't
  distinguish "hourly" cleanly enough in its own data model for every tenant, producing real
  10-25-magnitude EUR figures labeled `period: "month"`. This is a genuinely different failure
  mode from every prior pass's gaps (all of which were either missing regex coverage or found in
  free-text description mining, never a structured field actively misreporting its own unit).
- **A repeat-poster pattern recurs at real scale** (`ascor`: 210 nearly-identical apprenticeship
  postings on one board; `rebootmonkey`: one templated listing reposted across 6 currencies) — a
  reminder that a raw per-posting count can overstate how many genuinely independent data points a
  "N distinct occurrences" finding represents; this pass reports distinct-company counts alongside
  posting counts specifically because of this.
- **A genuinely non-English-majority description corpus**, measured directly rather than assumed —
  the first ATS in this initiative where more Tier-2 misses are non-English than English.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 3,534 live) | 3,000 attempted, 2,995 clean (99.8% after retry) |
| jobs seen | 56,336 |
| jobs with a structured `salary` field (`Job.salary`) | 20,575 (36.5%) |
| of those, extracted via Tier 1 | 18,799 (33.3% of all jobs; 91.4% of field-present jobs) |
| extracted via Tier 2 (description, no usable field) | 2,706 (4.8%) |
| **overall Tier1+Tier2 coverage** | **21,505 (38.2%)** |
| boards with ≥1 job showing a real signal | 1,413/2,995 (47.2%) |

Second-highest coverage of any ATS in this initiative so far (workable 15.4%, workday 27.6%,
greenhouse 36.1%, smartrecruiters 10.0%, zoho 9.2%, teamtailor 14.1%, ashby 49.7%,
**recruitee 38.2%**) — ahead of greenhouse, behind ashby. Unlike ashby's pass (where one
structural fix drove nearly the entire number), recruitee's coverage was already largely
realized by the existing shared parser; this pass's real contribution is measuring and honestly
accounting for the residual gap (the two period-ambiguity buckets, 1,273 jobs) rather than a
single dominant fix.

## What changed in code, and why

- `src/headstart/salary.py`: added `"PLN"` and `"CHF"` to `_CURRENCY_CODES`,
  `_MIN_PLAUSIBLE_ANNUAL`, and `_MAX_PLAUSIBLE_ANNUAL`. This does not change *which* jobs produce a
  span (both currencies' real values were already clearing `_bounded`'s USD-shaped fallback) — it
  resolves the `currency` field correctly instead of leaving it `None`, and replaces a
  coincidentally-permissive fallback bound with one calibrated for the actual currency. One
  genuine new-extraction side effect, fully understood and verified (see Cross-ATS impact): a
  small number of *unlabeled*, currency-anchored bare ranges (no "salary"/"compensation" word,
  just a number range next to a recognized currency code, e.g. `"Rate: 89 — 119 PLN/hour"`) now
  pass a pre-existing anti-false-positive gate in Tier 2 that requires a recognized currency nearby
  before trusting a label-less number range as a wage at all — previously invisible because PLN
  wasn't a recognized code, now correctly visible because it is.
- `src/headstart/scrapers/recruitee.py`: **unchanged**. `_salary()`'s existing truthy-check shape
  was investigated (per ashby.md's "check `is not None` vs. truthiness" lesson) and confirmed safe
  for this ATS's actual data (string-typed fields, no observed zero values) rather than changed
  defensively without evidence.
- `tests/test_salary.py`: 4 new tests — PLN/CHF recognition (`test_field_pln_currency_recognized_
  and_bounded`, `test_field_chf_currency_recognized_and_bounded`) and, for each, a case that
  clears the *old* USD-shaped fallback floor but correctly fails the *new*, properly-calibrated
  currency-specific floor (`..._below_its_own_floor_rejected_though_it_cleared_the_old_usd_
  fallback`) — demonstrating the fix is a real correctness improvement, not just a currency-label
  change.

### Cross-ATS impact of the PLN/CHF addition

Mandatory full cross-ATS diff (main's frozen `salary.py` vs. the current working tree, across all
7 previously-merged ATSes' frozen corpora) — every difference hand-traced against real source
text, not sampled:

Every one of the 63 differences across all 7 corpora was individually categorized and hand-
checked, not sampled:

| ATS | jobs | differences | shape |
|---|---:|---:|---|
| workable | 5,167 | 0 | — |
| workday | 8,286 | 1 | currency resolved (None → PLN), min/max unchanged |
| greenhouse | 81,105 | 10 | 3 currency-resolved; 7 genuinely new extractions — real, unlabeled PLN-hourly B2B-contractor rates ("Rate: 89 — 119 PLN/hour") that a pre-existing anti-false-positive gate correctly withheld while PLN was unrecognized, now correctly passes |
| smartrecruiters | 7,466 | 0 | — |
| zoho | 36,657 | 0 | — |
| teamtailor | 43,018 | 18 | 14 currency-resolved; 4 genuinely new extractions, same PLN-hourly-rate shape as greenhouse's |
| ashby | 48,891 | 34 | 33 currency-resolved; **1 genuinely different case, understood and left as-is** (see below) |

The greenhouse/teamtailor "genuinely new" cases were individually verified against real source
text — hand-computed the annualization (89 × 2080 = 185,120; 119 × 2080 = 247,520, exact matches)
to confirm the extraction is arithmetically correct, not just plausible-looking.

**The one ashby exception, traced to its root cause rather than accepted on faith**: a Polish
company (`znanylekarz`)'s description contains `"OTE 15000-20000pln brutto"` — note "pln" glued
directly to "20000" with no space, a real Polish formatting convention. `_BARE_RANGE_CODE`
(one of the pre-existing bare-range Tier-2 patterns, unrelated to this pass's own changes) matches
this because its own regex requires only a *trailing* word boundary after the currency code, not a
leading one — but `_guess_currency` (which decides what currency to attribute to an already-
matched span) re-searches the matched text with the *strict*, both-boundaries `_CURRENCY_CODE`
pattern, which fails on "20000pln" for the same glued-digit reason. The span still gets created
(`_bounded` falls back to the USD-shaped bound since currency resolved to `None`), but the
resulting `15000-20000` reads as an *annual* USD-shaped figure when it's almost certainly a
*monthly* OTE (on-target-earnings) figure — the same period-ambiguity class as this pass's two
large, deliberately-declined buckets, just reached through a completely different code path
(`_BARE_RANGE_CODE`'s pre-existing loose/strict boundary mismatch, not anything this pass added).
This is a genuine, pre-existing inconsistency between `_BARE_RANGE_CODE`'s matching boundary and
`_guess_currency`'s attribution boundary — PLN is simply the first currency where a real glued
instance happened to also clear the plausibility bounds, in seven ATSes' worth of frozen corpora.
A proper fix (capturing the matched currency code directly in `_BARE_RANGE_CODE`/
`_BARE_RANGE_CODE_EACH` rather than re-deriving it) touches two more shared regexes and every
caller of `_guess_currency`, for a benefit measured at exactly one real occurrence across ~230,000
jobs checked. Left as a documented, understood, single-occurrence gap rather than built past the
evidence — consistent with this pass's own declined period-ambiguity buckets above.

## Known gaps, left honestly unresolved rather than guessed at

- **Period unreliability accounts for all 1,736 Tier-1 parse failures (8.4% of field-present
  jobs), not a clean two-bucket split** — the two clearest, largest, individually-verified
  patterns are a `period` that appears mislabeled (533 postings / 117 companies — mostly real EUR
  hourly rates carrying `period: "month"`) and a `period` entirely absent (740 postings / 191
  companies). The remaining 463 (27%) were checked, not left silently unexplained: more of the
  same mislabeling in shapes the two headline patterns' own regex characterization didn't happen
  to match — single-value (non-range) low figures (`"400 EUR month"`, `"564 EUR month"`),
  small-magnitude values labeled `"year"` instead of `"month"` (`"54.00-60.00 EUR year"`, an
  hourly rate by its size), and the *same* mislabeling running in the opposite direction
  (`"36000-70000 EUR month"` — 36k-70k EUR is a wholly plausible **annual** senior salary, almost
  certainly mislabeled the other way) — plus a handful of genuinely distinct, smaller patterns:
  placeholder-zero pairs in non-EUR currencies (`"0-0 DKK month"`, `"0-0 USD year"`), a sentinel
  max value that reads as a "no real ceiling" placeholder (`"36000-999999 EUR year"`), and
  low-value declines in currencies far below even the 27-currency long tail's weakest evidence
  (`"1-2 AFN hour"`). Every one of these is the same underlying problem in a different shape: a
  structured field's own stated value can't be trusted at face value for a real subset of
  tenants, and fixing any of them would mean inferring the field's true meaning from the number's
  magnitude rather than from anything the source actually said — a materially different, more
  speculative kind of inference than any guard or pattern already built in this module. Same
  conclusion across every sub-shape: deliberately left unbuilt rather than risk the exact
  silent-corruption class the no-fabrication principle exists to prevent, and reported as one
  connected 1,736-job finding rather than an under-counted 1,273 with an unexplained remainder.
- **27 unsupported currencies below the PLN/CHF evidence threshold** (JPY the strongest at 4
  companies, DKK at 3, the remaining 25 codes at 1-2 companies each — mostly `rebootmonkey`'s own
  repeated multi-currency repost) — measured, real, but too fragmented to responsibly calibrate
  without company-specific research disproportionate to the evidence.
- **Non-English descriptions** (61.5% of Tier-2 misses in a direct `langdetect` measurement,
  dominated by Dutch and German) — consistent with, not a deviation from, this project's explicit
  English-only search-corpus scope; flagged as a large, real, measured, deliberately out-of-scope
  finding rather than silently folded into "coverage is lower than expected."
- **A bare `"$X to $Y"` Tier-2 connector** — measured at 3 real occurrences across the whole
  3,000-board sample, one of which is a confirmed false-positive risk (a revenue figure, not a
  salary). Too rare and too immediately risky to build.

## Carried forward from workable through ashby — and new lessons

- **Applied directly, both came back negative but were checked with the same rigor as if they
  might not have been**: ashby.md's "check for structure one level deeper" lesson (recruitee's
  field is already flat) and its "check `is not None` vs. truthiness on any raw-numeric formatter"
  lesson (recruitee's fields are strings, so the same code shape as ashby's bug isn't
  exploitable here). A negative finding checked with real data is worth exactly as much as a
  positive one — it closes a question instead of leaving it open by assumption.
- **Applied**: measure before building, every time — the PLN/CHF addition, both declined
  ambiguity buckets, and the declined `"X to Y"` pattern all have a specific company/occurrence
  count behind the decision, not an impression.
- **Applied**: hand-trace every diff example against real text, not just the aggregate count —
  this is what turned "5 unexplained new extractions" into a fully understood, arithmetically
  verified mechanism (an anti-false-positive gate now correctly recognizing PLN) rather than an
  assumed-safe change.
- **Applied**: a sustained error rate needs the right diagnostic before choosing a fix — bucketing
  by position showed this pass's rate-limit shape was genuinely different from teamtailor's
  (no clean prefix, no sharp trip point), correctly pointing at "too many concurrent connections"
  rather than "sustained-volume rolling window," and a cheap 10-board dry-run confirmed it before
  committing to a full 1,430-board retry.
- **New**: when a structured field's OWN stated value (like `period`) can be wrong for a real
  subset of tenants, that is a fundamentally different, harder problem than a missing regex
  pattern or an unrecognized currency — the fix would require distrusting the source data based on
  inferred magnitude, which this module has never done and shouldn't start doing without a much
  stronger evidentiary bar than "the numbers look hourly to me."
- **New**: a repeat-poster (one company, many near-identical listings) can make a real pattern look
  more widespread than it is in a raw occurrence count — report distinct-company counts alongside
  posting counts whenever a single high-volume poster is found, so a reader can tell "widespread
  real phenomenon" from "one board's worth of templated listings" at a glance.
- **New**: adding a currency to the shared bound tables is not always a coverage-count change — it
  can be a pure correctness fix (resolving `currency` from `None` to a real value with no min/max
  change) that only shows its coverage effect indirectly, through a *different* mechanism (here: an
  anti-false-positive gate elsewhere in the cascade that happens to depend on the same currency
  recognition). Don't assume a currency addition's blast radius is limited to Tier 1's own
  `_field_range_currency_interval` — check the full cascade.
- **New**: this project's own English-only search-corpus scope decision (CLAUDE.md) is directly
  relevant to salary-extraction scope too, not just the embedding/search layer — measure the
  non-English share of a miss population precisely when an ATS's customer base makes it material,
  and treat it as an explicit, cited scope boundary rather than an unexplained coverage ceiling.
- **New**: adding a currency code can surface a real, pre-existing inconsistency between two
  *different* shared patterns that happen to both touch currency — not just extend the one
  function that looks like the obvious owner. `_BARE_RANGE_CODE`'s matching boundary (trailing
  only) and `_guess_currency`'s attribution boundary (leading and trailing) were already
  inconsistent before this pass; PLN was just the first code where a real glued instance
  (`"20000pln"`, a genuine Polish formatting convention) happened to also clear the plausibility
  bounds, across all 7 previously-merged corpora. The mandatory cross-ATS diff caught it because it
  checks every corpus, not because anything about this pass's own change was unusual — a reminder
  that a currency addition's blast radius includes every function that references currency
  matching, not just the one being extended, and that hand-tracing a diff to its exact regex-level
  mechanism (not just confirming "the number is plausible") is what catches this class of finding.
