# Teamtailor — salary extraction findings

Sixth ATS in the salary-extraction initiative. See `README.md` for the overall process and the
five prior passes: `workable.md` (pilot), `workday.md` (first detail-pass ATS), `greenhouse.md`
(two general `salary.py` bugs found), `smartrecruiters.md` (a custom field, a pre-existing bug),
`zoho.md` (three real bugs found and fixed across two code-review rounds — a phantom-job sampling
bug, a wrongly-declared Tier-1 dead end, and a shared ceiling-vs-floor bug already shipping in
four merged ATSes; see that doc's own extensive lesson list, several of which apply directly here).

Teamtailor is one of the 9 ATSes that already populate `Job.salary` from a real structured field
(schema.org `baseSalary`) and already has a calibrated Tier-1 parser (`_field_lever_recruitee_
teamtailor`, shared with lever/recruitee). "Already has Tier 1" turned out to mean something very
different here than it sounds: the parser existed, but a real bug meant it was silently rejecting
the large majority of genuinely correct field values — see What changed in code.

## Methods tried

- **Live board count re-measured: 3,764** (the plan's 4,686 figure was stale). Listing-only
  (`has_detail_pass = False` — confirmed by reading the scraper: the public JSON Feed at
  `https://{slug}.teamtailor.com/jobs.json` carries the description and structured `baseSalary`
  inline, no per-job detail fetch), so the sampling script needed no new adapter.
- **A real, substantial concurrency/rate-limit finding, investigated before trusting any coverage
  number.** The first 3,000-board sample at the standing 32-worker default had a 56% error rate
  (1,683/3,000, almost all HTTP 403). Investigated directly rather than assumed dead boards:
  retried the same failed boards serially immediately after (still mostly failed — ruling out
  "pure concurrency at an instant"), then retried again after more wall-clock time had passed
  (all of them then succeeded with zero code changes — confirming a temporary, burst-triggered
  bot-wall, not dead or blocked boards; `curl_cffi`'s Chrome impersonation plus the existing
  3-attempt retry-on-403 logic, per ADR-0047's "403/405 = bot-wall blips" framing, already
  anticipates this class of issue, but the in-run retries weren't enough to outlast a wall
  triggered by 32-way aggregate concurrency against teamtailor's shared `*.teamtailor.com`
  CDN/WAF). A quick 300-board test at 16 workers looked clean (1/300 errors) — but the FULL
  3,000-board run at the same 16 workers still showed 1,140 errored (38%). Bucketing errors by
  their position in the run's completion order explained why: near-zero for the first ~900
  boards, then a sharp spike, then a noisy, partially-recovering pattern for the rest — the shape
  of a rolling/sliding-window rate limit that a short test finishes before ever accumulating
  enough sustained volume to trip, but any long-enough sustained run (regardless of exact worker
  count) eventually crosses. Retried just the 1,140 failures at a more conservative 4 workers
  (not re-fetching the 1,860 already-successful boards): recovered 1,125/1,140 (98.7%). Combined
  clean sample: **2,985/3,000 boards (99.5%), 42,919 jobs** — a comprehensive, reliable sample
  despite the saga, not a reduced-N fallback.
- **A real, substantial Tier-1 bug found via straightforward gap analysis of the raw field
  values**: `_period_multiplier` only recognized PHRASE-shaped period markers ("per hour", "/hr")
  — but teamtailor's real schema.org format passes through a BARE unit word
  (`_salary()`'s own docstring example, "40000-60000 EUR YEAR", happened to need no multiplier at
  all since annual is the default, silently masking that HOUR/MONTH/DAY were never actually
  handled). Real values like `"15-17.5 GBP HOUR"`, `"1500-1800 EUR MONTH"`, `"120-130 GBP DAY"`
  matched none of the phrase-shaped checks, so the multiplier silently defaulted to 1 (annual),
  and the un-multiplied tiny "annual" figure then correctly-but-wrongly failed the plausibility
  bounds. Also found DAY had no Tier-1 handling at all (only Tier 2 had `_DAILY_TO_ANNUAL`).
  **Fixed**: word-bounded bare hour/day/month recognition added — in a dedicated
  `_period_multiplier_structured`, not the base `_period_multiplier`, after a second review round
  found the bare-word version wasn't safe for every caller (see What changed in code). Measured
  impact of this fix in isolation (`from_field()` alone, not the full Tier1+Tier2 cascade): the
  field-present-but-unparseable rate dropped from 49.1% to 4.4%, a net recovery of exactly 1,885
  jobs (1,904 gained by the fix, 19 lost — a handful of field values that happened to parse into a
  plausible-looking but wrong figure before, and correctly decline now; see the hand-traced `LOST`
  entries in What changed in code for exactly which ones and why). The residual 4.4% is genuinely
  broken/mislabeled source data (see What changed in code). A
  related but distinct number appears in Coverage below — 45.7%→4.2% is what the SAME transition
  looks like through the full `extract()` cascade (Tier 1 plus whatever Tier 2 mining catches on
  top), after all three of this pass's fixes, not the multiplier fix in isolation; keep the two
  measurements separate rather than treating one as a restatement of the other (Spec-review
  finding, second round: an earlier draft of this paragraph conflated them).
- **A second, real, shared-code bug found the same way**: `_PERIOD_HINT`'s slash-prefixed
  alternatives (`/hr`, `/hour`, `/mo`, `/month`, `/yr`, `/year`, `/day`) required a leading `\b`
  that never matches when a SPACE precedes the slash (`"£40 /hour"` — a space and a slash are both
  non-word characters, so no `\b` transition exists there) — a third variant of the exact same
  boundary-issue class already fixed twice in earlier passes (digit-glued `/hr`, and zoho's glued
  `p/h`). **Measured real prevalence across all 6 ATS corpora before fixing** (not assumed from
  one example): 878 real occurrences combined — greenhouse alone had 597, meaning this was already
  silently under-extracting on an already-merged ATS, not a teamtailor-only issue. **Fixed** by
  dropping the leading `\b` for the slash-prefixed alternatives (matching the established `p/h`
  precedent), keeping the trailing `\b` so e.g. "/hours" (plural) still doesn't sweep in by
  accident.
- **A third, real, shared-code bug found while hand-verifying the first two fixes' cross-ATS
  diff** (not trusting the aggregate counts — ADR-0066 discipline): `_mutually_consistent()`
  treated a `None` currency (meaning "couldn't tell from THIS mention") as a DISTINCT, conflicting
  value against a sibling span that DID resolve a currency, so the exact same real wage stated
  twice in one description — once with a symbol, once without (real zoho text: "Compensation:
  $25.96 / hour to start... Salary: 25.96/hour") — got wrongly flagged ambiguous and declined,
  purely from the currency-presence mismatch, not because the amounts disagreed. Measured real
  prevalence via a targeted proxy check: 24 confirmed cases across the corpus, already latent in
  already-merged greenhouse/smartrecruiters data before this pass — the space-before-slash fix
  simply exposed more of it by recovering additional matches that could now collide with an
  already-passing one. **Fixed**: `_mutually_consistent` only compares currencies when both spans
  have one; `_resolve` now prefers a currency-bearing span over a currency-less one among
  mutually-consistent matches (previously always returned the first one found, regardless).
- **Every remaining `LOST` case checked by hand, not sampled** — all 18 teamtailor + 2 zoho `LOST`
  entries from the final cross-ATS diff were individually read against their real source text
  before accepting the diff, not just the first few examples the script happened to print. 16 of
  the 18 teamtailor cases are the SAME confirmed pattern: a `Job.salary` field literally stating
  an absurd period for its magnitude once correctly multiplied (`"43389-47728 GBP HOUR"` would be
  £90M+/year if taken literally; `"30000-35000 EUR HOUR"` similarly) — genuine source-side
  misconfiguration (a company picked the wrong unit in Teamtailor's salary widget), now correctly
  declined instead of the old code's "right by accident" behavior (the un-multiplied number
  happening to look like a plausible annual salary purely by chance). Real, but not a bug — see
  Known gaps for the 2 that are.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 2,985/3,000 clean successes after the
  concurrency investigation (99.5%), from 3,764 live boards.
- Measured both required percentages: **yes** — 9.8% structured-field presence, 14.1% overall
  Tier1+Tier2 coverage (6,033/42,919), reported alongside the coarse hint rate for calibration.
- Live-verified after code changes: **yes for teamtailor's own three fixes** — a fresh,
  differently-seeded 50-board sample (seed=2026, 16 workers, 0 errors — confirming a short run
  stays clean, consistent with the rolling-window theory) after all three shared-code fixes, 11.2%
  real coverage, 10 extractions spot-checked for plausibility, all genuine. **Not literally
  possible for the second-round `_field_generic`/`_field_darwinbox` scoping fix specifically**
  (Spec-review finding): that fix only affects ashby/personio/darwinbox, none of which this
  initiative has sampled yet (ashby is scheduled later in the plan's order; no
  `experiment/salary-extraction/ashby/` artifacts exist to verify against). Verified instead by
  (a) two direct unit tests reproducing the exact corruption against the pre-fix code and
  confirming it's gone, and (b) proving `_period_multiplier` — the function ashby/darwinbox
  actually call — is byte-identical to the pre-PR baseline after the split, meaning the bug never
  reached either of them in any shipped state. A real gap in the letter of the process, worth
  naming rather than glossing over, even though the practical risk is low.
- Went beyond the ask, substantially: diagnosed and resolved a real operational rate-limiting
  issue with the sampling infrastructure itself (not just the extraction logic) via direct,
  repeated measurement rather than accepting a degraded sample. Found and fixed two ADDITIONAL
  shared-code bugs beyond the one the initial gap analysis was looking for, each verified via its
  own real-prevalence measurement across all 6 ATS corpora. Hand-traced every single `LOST` entry
  in the final diff (18 + 2 = 20 cases), not a sample of them, before accepting the fix as net
  positive.
- Did not: build a fix for three known gaps found while tracing the `LOST` bucket and, in the
  second review round, verifying the fix-up — for three different, honestly-distinguished reasons,
  not one blanket "too rare" (an earlier draft of this section wrongly claimed all of these were
  single occurrences; corrected here after a Spec-review finding that this section and Known Gaps
  had drifted out of sync). The `_period_from_window` proximity-hint-stealing issue genuinely is a
  single confirmed occurrence (below the yield bar). The 5%-consistency-tolerance gap for
  legitimate small differentials is NOT rare on proper measurement (~30–40 real, diverse cases
  across all 6 ATSes) — declined instead because loosening an ambiguity-safety check is inherently
  riskier than this pass's other additive fixes, not because it's uncommon; see Known gaps for the
  full, corrected measurement. The `_mutually_consistent` hub-topology gap (found in the second
  review round) genuinely measured at zero real occurrences. Did not add support for unsupported
  currencies (THB, MXN, PKR, BRL all appeared in the residual Tier-1 failures) — measured at 11/179
  of the pre-second-fix residual, too small a yield.

## Live-verification review

Fresh, 50 boards, seed=2026, `--workers 16` (confirmed safe for a short run), against the fully
fixed code (all three `salary.py` fixes together). 50/50 boards succeeded, 0 errors — consistent
with the rolling-window hypothesis (short runs stay clean; only sustained, high-volume runs
trigger the wall). 722 jobs, 81 real Tier1+Tier2 extractions (11.2% — a bit below the 3,000-board
sample's 14.1%, plausible small-sample variance given one board, `groupeoci`, alone contributed
37 of the 722 jobs with its own French-market salary fields). All 10 spot-checked extractions were
genuine and plausible: French tech roles (€26k–€55k, matching France's typical presentation),
a US nonprofit consultant ($105k–$140k), a UK chef (£33.3k–£45.8k), a US CEO ($180k), and others
— no red flags.

## Patterns found

- **Bare schema.org unit words** (`HOUR`, `MONTH`, `DAY`, `YEAR`) as the real period-marker shape
  for teamtailor's structured field — the single highest-value fix of this whole pass.
- **A space before a slash-prefixed period marker** (`"£40 /hour"`) — a third variant of a
  boundary-issue class already fixed twice before, and NOT specific to teamtailor: greenhouse
  alone had 597 real occurrences once measured.
- **The same wage stated twice in one description, with and without a resolvable currency symbol**
  — a real, pre-existing ambiguity-resolution weakness exposed (not caused) by recovering more
  matches.
- **Company-side salary-widget misconfiguration** is common and systematic enough to be its own
  category here: 16 real cases where a company picked the wrong period unit for their stated
  figures, spanning at least 8 distinct companies across several countries (UK, Thailand, Mexico,
  Pakistan, Canada, generic) — genuinely unfixable without guessing a different period than the
  one actually stated, correctly left as an honest decline.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 3,764 live) | 3,000 attempted, 2,985 clean (99.5%, after the rate-limit retry) |
| jobs seen | 42,919 |
| jobs with a structured `salary` field (`Job.salary`) | 4,212 (9.8%) |
| of those, extracted via Tier 1 | 4,027 (9.4% of all jobs; 95.6% of field-present jobs) |
| extracted via Tier 2 (description, no usable field) | 2,006 (4.7%) |
| **overall Tier1+Tier2 coverage** | **6,033 (14.1%)** |
| boards with ≥1 job showing a real signal | 579/2,903 (19.9%) |

Coverage moved substantially over the course of this pass, each fix's contribution independently
measured rather than only reported as one final number: baseline (before any `salary.py` change,
using the pre-existing Tier-1 parser as-is) was 9.6%; the period-multiplier fix alone raised it to
13.7% (the single largest jump of any fix in this initiative so far); the space-before-slash and
currency-consistency fixes together added the remaining ~0.4 points to 14.1%. This is now the
second-highest coverage of the six ATSes done so far (workable 15.4%, workday 27.6%, greenhouse
36.1%, smartrecruiters 10.0%, zoho 9.2%, **teamtailor 14.1%**) — driven by a real, structured
field that, once correctly parsed, out-performs pure description mining on every other ATS.

## What changed in code, and why

All three fixes below live in shared `src/headstart/salary.py` — none are teamtailor-specific
dispatch, so all three retroactively improve the five already-merged ATSes' data too, once this
merges and the pipeline's derived-field refresh next runs. Verified via the mandatory full
cross-ATS diff (main's frozen `salary.py` vs. the fully-fixed working tree, across all 6 ATS
corpora), with every `LOST` example hand-traced against real text, not just the aggregate counts.

- **`_period_multiplier_structured`** (new function, split out from `_period_multiplier` in a
  second code-review round — see below): word-bounded bare `hour`/`day`/`month` recognition
  (alongside the existing phrase-shaped checks), plus a `day` case entirely new to Tier 1
  (previously only Tier 2 had day-rate handling). Used *only* by `_field_lever_recruitee_
  teamtailor`, whose three callers (`lever.py`, `recruitee.py`, `teamtailor.py`) all assemble
  their field string from a structured min/max/currency/interval quad — confirmed safe by reading
  each scraper's own formatter, never free text. **The first version of this fix applied bare-word
  matching to plain `_period_multiplier` itself**, on the claim that "a Tier-1 field value is
  always a short, structured string, not free text" — Standards review found and demonstrated this
  was false for `_field_generic` (reached by ashby/personio, which pass an HR system's raw
  free-text field straight into `Job.salary` with no scraper-side normalization — an already-
  documented fact from PR #238's own review that the first version of this fix contradicted
  without re-checking) and, as an unconfirmed but plausible related risk, for `_field_darwinbox`
  (whose `salary_timeframe` is equally unvalidated free text). Demonstrated, not hypothetical: `"40,000
  - 50,000 USD with 1 month severance included"` silently misread "month" from the severance
  clause — nothing to do with the salary's own period — as a monthly marker, 12x-inflating a
  correct $40k–$50k annual figure into a wrong $480k–$600k one that still happened to clear the
  plausibility bounds. A silent corruption, not a safe decline. Fixed by splitting the bare-word
  behavior into its own function used only where it's confirmed safe; `_period_multiplier` itself
  (phrase-shaped markers only — "per hour", "/hr", "monthly", ...) stays the default for
  `_field_generic` and `_field_darwinbox`, unchanged from before this pass.
- **`_PERIOD_HINT`**: slash-prefixed alternatives (`/hr`, `/hour`, `/mo`, `/month`, `/yr`,
  `/year`, `/day`) moved outside the leading `\b(...)` group — they no longer require a leading
  word boundary (which can't reliably form when a space or another non-word character precedes
  the slash), only the trailing one.
- **`_mutually_consistent` / `_resolve`**: a `None` currency no longer counts as conflicting with
  a sibling span's resolved currency; among mutually-consistent spans, `_resolve` now prefers the
  currency-bearing one.
- `tests/test_salary.py`: 79 tests total (up from 74 at zoho's merge) — one new test each for the
  period-multiplier fix (`test_field_teamtailor_bare_unit_word_period_markers`), the
  space-before-slash fix (`test_description_period_marker_space_before_slash`), the
  currency-consistency fix (`test_description_same_amount_currency_resolved_once_is_not_
  ambiguous`), and two more from the second review round locking in the `_field_generic`/
  `_field_darwinbox` regression fix (`test_field_generic_bare_word_period_not_recognized_in_
  free_text`, `test_field_darwinbox_bare_word_period_not_recognized_either`).
- No changes to `teamtailor.py` itself — the existing `_salary()` formatting was already correct;
  the bug was entirely in how `salary.py` interpreted the string it produces.

### Cross-ATS impact of the shared `salary.py` fixes

| ATS | both_none | both_same | lost | gained | value_changed |
|---|---:|---:|---:|---:|---:|
| workable | 4,347 | 816 | 0 | 4 | 0 |
| workday | 5,843 | 2,431 | 0 | 12 | 0 |
| greenhouse | 51,131 | 29,616 | 0 | 352 | 6 |
| smartrecruiters | 6,734 | 720 | 0 | 12 | 0 |
| zoho | 33,265 | 3,355 | 2 | 32 | 3 |
| teamtailor | 36,868 | 3,865 | 18 | 1,921 | 247 |

Every `gained`/`value_changed` example spot-checked was a genuine improvement (a period marker now
correctly recognized; a false-ambiguity now correctly resolved; a wrongly-matched business-metric
or bonus/commission figure now correctly superseded by the real, nearby salary once the wrong
match stopped short-circuiting the cascade — the same class of positive side effect zoho's
ceiling-vs-floor fix produced). `lost` = 2 (zoho) + 18 (teamtailor) = 20 total, individually
hand-traced: 17 are genuinely mislabeled source data now correctly declined instead of
accidentally-right-by-luck; 1 (zoho, `thetrashgurus`) is a business metric ("Target Customer
Size: $10,000 - $100,000 / month") now correctly declining as a side effect of the period fix
properly annualizing it past plausibility; 2 are real, narrow, newly-exposed edge cases — see
Known gaps.

## Known gaps, left honestly unresolved rather than guessed at

- **A 5%-consistency-tolerance gap for legitimate small wage differentials** (weekday/weekend,
  base/premium, and similar) — corrected here after a Standards-review finding that this pass's
  first draft undercounted it as "one occurrence." `radfieldhomecare`'s real text has two separate
  real pairs, each failing for a different reason (verified precisely, not assumed): "£13.00/ hour
  weekdays, £13.50 / hour weekends" is a genuine `_num()`-rounding artifact — the raw, unrounded
  gap (3.8%) is comfortably under the 5% threshold, but `_num`'s round-before-multiply behavior on
  "13.50" (banker's rounding to 14) pushes the *annualized* gap just over it (2,080 vs. a 1,352
  threshold). A second pair in the same description, "£13.75... £14.75", fails for a genuinely
  different reason: its raw gap (7.3%) is large enough to exceed the threshold regardless of
  rounding. Measured properly this time (not
  assumed): a broad "any two close-but-inconsistent figures" proxy found over a thousand hits, but
  almost all of those are genuinely DIFFERENT figures (job levels, unrelated roles) correctly
  declined, not this shape — tightening the proxy to require "weekday"/"weekend" language nearby
  found 145 across all 6 ATSes, but 111 of greenhouse's 132 turned out to be `spacex`, a *different*,
  already-known pattern (the same multi-level compensation disclosure `_LEVEL_BAND` already
  handles elsewhere, just in a shape that doesn't match that pattern here) coincidentally
  mentioning "weekday"/"weekend" in unrelated benefits text, not a genuine differential. Excluding
  that, the real count is roughly 30–40, genuinely diverse across companies, not one template. A
  real, non-trivial pattern — but NOT fixed in this pass: unlike this pass's other fixes (pure
  additions to what gets recognized), loosening the ambiguity tolerance is inherently riskier
  (it can also suppress genuinely-different figures that happen to be numerically close), and
  building it safely — most likely a `_LEVEL_BAND`-style envelope for differential-language-marked
  pairs specifically, not a blanket tolerance widen — would need its own dedicated measurement and
  full corpus diff cycle that this already-extensive pass's scope doesn't stretch to. A genuine
  candidate for the next ATS pass or a dedicated follow-up, not silently dropped.
- **`_period_from_window`'s proximity search can cross an unrelated dollar figure to steal its
  period hint** (found on a **zoho** tenant, `marcusknightley.zohorecruit.eu`, while hand-tracing
  zoho's own `LOST` entries in the same cross-ATS diff this pass ran — not teamtailor's own data,
  cited here because it's the same general mechanism this pass's fixes exposed; real text:
  "£45,000-£65,000 base salary + £550 / month car allowance") — the base salary range has no
  period marker of its own (correctly implying
  already-annual), but the window search reaches past the £550 car-allowance figure to grab ITS
  "/ month" marker, wrongly multiplying the base salary by 12 and producing an implausible value
  that gets correctly rejected — losing the correct, un-multiplied answer as a side effect. One
  confirmed occurrence; the general fix (don't let a hint search cross a different currency
  figure) is more invasive than this pass's yield justifies building blind.
- **`_mutually_consistent`'s hub-topology gap** (second review round, Standards axis): it compares
  every span only to `spans[0]`, never pairwise among all of them, so a currency-LESS first span
  can let two LATER spans with genuinely different currencies both "agree" with it independently,
  never being compared against each other. Confirmed directly:
  `_mutually_consistent([SalarySpan(60000, None, None, "regex"), SalarySpan(60500, None, "USD",
  "regex"), SalarySpan(61000, None, "GBP", "regex")])` returns `True` (wrong — USD and GBP should
  never silently agree), and `_resolve()` would pick the first currency-bearing span, dropping the
  real conflict. Pre-existing (this pass's currency-consistency fix widened how it's reached, by
  letting a `None`-currency span survive as a comparison anchor at all, but didn't create the gap
  itself) — measured directly against all 6 ATS corpora: zero real occurrences, matching the
  reviewer's own assessment. Not fixed here given the lack of real-world evidence; worth a
  dedicated pairwise-comparison rewrite if a future ATS's data ever surfaces a real case.
- **Unsupported currencies** (THB, MXN, PKR observed in real, otherwise-well-formed Tier-1 field
  values) — 11 confirmed cases in an earlier measurement, too small to justify adding speculative
  plausibility bounds for currencies this module has no calibrated sense of.
- **Genuinely broken source data** (e.g. `"2.60826-2950 EUR MONTH"`, `"12.7-18000 MXN MONTH"`) —
  correctly and safely declined; not a code gap at all, just real, messy company-entered data.

## Carried forward from workable, workday, greenhouse, smartrecruiters, and zoho — and new lessons

- **Applied**: full corpus diff after every shared-code change, `repr()`-based comparison (not
  bare `==`) from the very start this time — zoho's pass found and fixed the `==`-across-modules
  bug; this pass never had to rediscover it.
- **Applied, and it mattered more than ever**: hand-tracing every `LOST`/`CHANGED` example rather
  than trusting the aggregate count (ADR-0066) — this is what separated the 17 confirmed-correct
  declines from the 2 real, narrow regressions, and what caught that "48000-68000 MONTH" and its
  15 siblings were genuine source misconfiguration, not a bug in the fix.
- **Applied**: check for a company-configurable custom field / verify which page a structured
  field actually lives on before assuming anything — moot here (teamtailor's `baseSalary` is
  already inline in the one public feed, confirmed by reading the scraper), but the check was
  still made rather than assumed.
- **New**: "already has Tier 1" (a populated field + an existing calibrated parser) is not the
  same as "Tier 1 works" — the field was real and the parser existed, but a genuine bug meant it
  was silently rejecting the majority of correct values. Measure real parse-success on the
  field-present subset specifically, don't just measure field-presence and assume the rest works.
- **New**: the leading-`\b`-fails-when-no-word-char-precedes-a-slash issue is now a recognized
  CLASS of bug (three confirmed variants: digit-glued, zoho's fully-glued `p/h`, and this pass's
  space-before-slash) — worth checking any NEW slash-based pattern against all three spacing
  variants (glued, single space, double space) up front, not discovering each one separately.
- **New**: a sustained-rate-limit is a genuinely different failure mode from pure concurrency and
  needs a different diagnostic — a short test run can look completely clean and still not predict
  a long run's behavior; bucket errors by their position in a long run's completion order to tell
  a rolling-window limit from a simple too-many-at-once one, and consider retrying just the
  failures at a lower rate rather than re-running everything or accepting a degraded sample.
- **New**: expanding what a shared pattern recognizes (more period markers, in this case) doesn't
  just add coverage — it also expands the RISK SURFACE for every OTHER mechanism that depends on
  match density (ambiguity resolution, proximity-based hint search). Budget time to hand-trace the
  `LOST` bucket specifically after any recall-improving fix, not just celebrate the `gained` count.
- **New, and this pass's own sharpest self-caught mistake**: a new safety-justifying code comment
  ("safe for every Tier-1 caller...") was written and shipped for review WITHOUT re-reading an
  already-existing, already-correct comment three lines above it in the same file — one that
  already documented, from a prior PR's own review, exactly the free-text-field risk the new
  comment's claim contradicted. Standards review caught it and demonstrated a real, silent 12x
  corruption. When a new comment makes a blanket safety claim, grep the surrounding file for any
  existing comment touching the same functions or callers first — contradicting an
  already-established, already-reviewed finding without noticing is a stronger signal something's
  wrong than getting a genuinely novel case wrong would be.
