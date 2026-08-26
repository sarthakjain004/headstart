# SmartRecruiters — salary extraction findings

Fourth ATS in the salary-extraction initiative. See `README.md` for the overall process and the
three prior passes: `workable.md` (pilot), `workday.md` (first detail-pass ATS, two code-review
rounds), `greenhouse.md` (highest coverage so far, two general `salary.py` bugs found and fixed).

## Methods tried

- **Sampled 3,000 of 5,659 live boards** (`config.load_active_companies` — freshly measured
  2026-08-22; the README's earlier "10,845" figure was a stale liveness snapshot, same pattern as
  greenhouse's own stale count). `has_detail_pass = True` — `fetch_raw()` pages the whole board
  (uncapped, ADR-0070/#227) *and* fans out a per-posting detail fetch, the same shape as workday's
  own scraper. Built a bounded `_fetch_smartrecruiters` adapter in `salary_sample.py` mirroring
  `_fetch_workday`: one listing page via `_get()` (no offset), then up to 3 real
  `_job_description()` detail fetches. 2,999/3,000 boards succeeded (1 error, negligible). 7,308
  jobs (bounded ≤3/board, same design as every other detail-pass ATS so far).
- **Checked the raw payload directly before assuming Tier 1 was a dead end** (the same discipline
  workday's and greenhouse's passes applied): the listing and detail payloads have no top-level
  compensation field, but DO carry a company-configurable `customField` array (SmartRecruiters'
  equivalent of greenhouse's `metadata`). **Real, evidenced finding, not assumed**: some companies
  configure a free-text custom field specifically for pay info — found via direct API inspection,
  e.g. `asurequality`'s "Enter salary or hourly pay range (+ pay grade, if known)" →
  `"Grade 15. $100K - $115K"`. Measured prevalence across a 150-board probe: 3/150 boards (2%)
  used a salary-labeled custom field, but skewed heavily toward high-volume employers — at the
  full 7,308-job sample, **716 jobs (9.8%) carried a compensation-labeled custom field, and 319 of
  those (44.6%) successfully extracted a real figure** — very close to half of everything this ATS
  extracted came from this one discovery.
- **A real semantic trap, found and deliberately avoided**: some companies use a MAX-ONLY field
  ("Target Salary Range Max" → `"$220,000"`, no companion Min) — forcing this into the existing
  `min`-required schema would misrepresent a ceiling as a floor (e.g. reading a Machine Operator
  role's stated $44,346 *maximum* as if it were the minimum). Deliberately did **not** build
  special-case handling for this shape; it's covered by the general "no clean number to extract"
  outcome instead (see What changed in code).
- **Two gap-analysis rounds**: round 1 measured the customField discovery's real yield and read
  real misses (mostly genuinely non-numeric boilerplate — "Competitive salary", Harvard-style
  internal grade codes with no dollar mapping — confirming the low remaining yield was mostly
  real data sparsity, not a pattern gap). Round 2, reading misses *without* any customField
  involvement, found a genuinely valuable new pattern (bare hourly/daily rates with no label at
  all) — which itself then needed two rounds of hardening, one against a false positive its own
  new code introduced, one against a bug that turned out to be genuinely pre-existing in code
  every already-merged ATS shares (see below, and What changed in code).
- **A false positive in this pass's own new pattern, caught before it ever shipped** (via the
  mandatory cross-ATS regression diff, not the original sample — so it never affected any
  already-merged ATS's real output): a shift-differential list states several genuinely different
  ADD-ON figures ("+$4.50/hr → Mon-Thu Nights +$9.00/hr → Fri-Sun Nights"); when the plausibility
  floor happened to filter out all but one, the ambiguity that should have blocked this got
  masked and the sole
  survivor was wrongly reported as the base wage. Fixed at the pattern level (a leading "+" is a
  reliable, structural "this is an add-on" signal) rather than via a word-based context guard,
  after a word-based ("differential" nearby) attempt was built, tested, and found to break a
  currently-correct case — reverted before shipping.
- **A second, independent, pre-existing bug found the same way**: `_period_from_window` picked
  the *first* period-hint word found scanning a window left-to-right, not the *closest* one to
  the number — "Shift: Day Salary Range: $44.00 - $57.00/hour" read "Day" (the work-shift type,
  unrelated to pay) as the period instead of the genuine "/hour" that comes right after the
  number. Fixing this naively (closest-wins) then introduced its *own* regression
  ("hourly rate: $25-35 Annual continuing education benefit..." — "Annual" opens an unrelated new
  sentence right where the number ends, and is *also* the closest match) — fixed with a second,
  narrower refinement (sentence-initial capitalization deprioritizes a same-distance "after" hint,
  distinct from ALL-CAPS emphasis like "$22.50/HOUR!!" which must keep working). Three edit
  iterations, each verified against the full corpus before moving to the next.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 3,000 (5,659 live, above the cap).
- Measured both required percentages: **yes** — 0.0% structured-field (the raw API truly has no
  compensation field to read directly), 10.0% overall description-mining coverage (729/7,308),
  18.8% coarse-hint rate reported alongside for calibration.
- Live-verified after code changes: **yes** — one fresh, differently-seeded round (40 boards,
  seed=271) after every fix in this pass was in, plus the mandatory full-corpus diff against all
  3 already-merged ATSes' frozen corpora (workable, workday, greenhouse) for the shared-code
  changes, run three times across three fix iterations.
- Went beyond the ask, explicitly: found and fixed **two real correctness issues in shared
  `salary.py` code** that this specific ATS's own pass didn't strictly require, both surfaced only
  by the depth of investigation this pass did. Precisely: the closest-period-hint bug is genuinely
  **pre-existing** — it affected already-merged workable/workday/greenhouse output before this
  pass ever touched it (see the Cross-ATS table). The shift-differential false positive is real
  but narrower in scope than that — it lived only in this pass's own new `_BARE_HOURLY_OR_DAILY`
  pattern and was caught by the cross-ATS diff *before* that pattern ever shipped, so it never
  affected any already-merged ATS's real output; it's reported here because the fix (the `(?<!\+)`
  exclusion) is itself a general, reusable correctness property, not because it was ever a live
  bug elsewhere. Fixed a scraper-level data-loss gap
  at the source (`smartrecruiters.py`'s `customField` was being silently dropped entirely before
  this pass) rather than working around it downstream. Deliberately avoided a schema-mismatch trap
  (the "Max"-only custom field) that a less careful pass could easily have gotten wrong.
- **Two mistakes made and caught before shipping, reported honestly**: (1) a word-based
  "differential" false-positive guard was built, found to break a currently-correct case
  ("$21/hr + $0.50 shift differential" — the genuine $21 base rate), and reverted in favor of the
  narrower, more precise "+" prefix exclusion. (2) the first "closest period hint" fix introduced
  its own regression (the "Annual continuing education" case) before the capitalization
  refinement caught and fixed it. Both were caught by the same discipline this initiative has
  relied on throughout: a full corpus diff after every change, not just re-running the aggregate
  percentage or trusting the unit tests (which passed against *both* broken intermediate versions
  — neither happened to be covered by the existing 57 tests until new ones were added for exactly
  these cases).
- Did not: build a dedicated pattern for the remaining tiny-yield gaps found during round-2 gap
  analysis (day-rate/hourly-rate label words, weekly period markers, currency-code `_BARE_BETWEEN`
  support, "paid $X" bare connector) — each measured at single-digit-to-low-teens occurrences,
  below the yield bar this initiative has consistently used to decide what's worth a dedicated
  pattern (see workday.md's "per load hour" precedent, greenhouse.md's declined `$X/hr-$Y/hr`
  each-side pattern at 107 jobs). Did not attempt the "Target Salary Range Max"-only shape — a
  deliberate, reasoned exclusion (see Methods tried), not an oversight.

## Live-verification review

One round, against real current `api.smartrecruiters.com` hosts, after every fix in this pass:

- **40 fresh boards, seed=271** (distinct from the main sample's seed=7), `--workers 20`. 40/40
  succeeded, 0 errors, 97 jobs, 5 real extractions (5.2% — well below the frozen sample's 10.0%,
  expected board-mix variance at this small an n: this seed happened to draw fewer
  salary-transparent boards). All 5 spot-checked for plausibility: Texas Workforce Commission
  state-government postings (Systems Analyst V $78k-$86.4k, HVAC Mechanic III $39.5k-$58.9k — both
  clean, specific state-pay-scale figures) and an Australian Duty Supervisor role ($110,240 AUD,
  plausible for a supervisory position). No CAD/currency mislabeling, no shift-differential
  false positives, no day/hour period confusion in this fresh sample.

## Patterns found

- **A company-configurable `customField` array occasionally carries real salary data**
  (`asurequality`: `"Enter salary or hourly pay range (+ pay grade, if known)"` →
  `"Grade 15. $100K - $115K"`, `"SP10 Grade 23 ($180K - $210K base salary) + KiwiSaver + 10% STI"`,
  `"$27.49"`; `cornerstonebuildingbrandscareers`: `"Target Salary Range Max"` → `"$44,346"`,
  `"19/hr"`, `"85,000"`) — rare at the board level (2%) but disproportionately high-yield at the
  job level (9.8% of all jobs) because a handful of high-volume employers populate it on every
  posting. The two real shapes (a genuine free-text range/figure vs. a bare "Max"-only ceiling)
  are structurally different enough that only the former is safely extractable without guessing.
- **Bare hourly/daily rates with zero label or connector** — very common on this ATS's
  retail/logistics/care-work postings specifically (a different company mix than the other three
  ATSes so far): `"Support Workers in Nottinghamshire £8.72 per hour"`,
  `"BENEFITS & SCHEDULING: $22.50/HOUR!! PAID WEEKLY!!"`. 94 real occurrences read by hand before
  building the pattern, all genuine wage mentions — zero false positives in that initial sample
  (the shift-differential false positive that *was* found came from the separate, later
  cross-ATS regression diff, not this read).
- **Shared code keeps surfacing general issues, not ATS-specific ones**: greenhouse's pass already
  fixed a trailing-comma-in-number-capture bug (not re-found broken here, confirming that fix
  holds). This pass found a genuinely different, previously-unnoticed one in the same shared
  `_period_from_window` function — the period-hint proximity bug — which, once fixed, changed
  already-merged ATSes' jobs retroactively (see Coverage's cross-ATS section). The
  shift-differential false positive, by contrast, was caught within this pass's own new code
  before it ever shipped (see Instruction-adherence self-assessment) — real, but never live in
  already-merged output.

## Coverage

**Corrected 2026-08-25 (this PR) — see "Post-merge correction" below.** The "0 (0.0%) — no
compensation field" line is now known wrong: a native `compensation` block exists on the detail
response and is read starting with this fix. Kept as the honest historical record of this pass's
own original (incomplete) finding, not rewritten in place.

| metric | value |
|---|---|
| boards sampled (of 5,659 live) | 3,000 |
| jobs seen (bounded ≤3/board, detail-pass adapter) | 7,308 |
| jobs with a structured `salary` field | 0 (0.0%) — no compensation field in the raw payload |
| jobs with a description-only signal (incl. appended customField text) | 729 (10.0%) |
| overall Tier1+Tier2 coverage | 10.0% |
| jobs with a compensation-labeled customField appended | 716 (9.8% of all jobs) |
| of those, successfully extracted | 319 (44.6% of that subset — ~44% of all extractions) |
| coarse hint rate (calibration only) | 18.8% |
| boards with ≥1 job showing a signal (coarse) | 742/2,999 (24.7%) |

The lowest overall coverage of the four ATSes done so far (workable 15.4%, workday 27.6%,
greenhouse 36.1%, smartrecruiters 10.0%) — not a weaker extraction pass, but a genuinely different
company mix: this sample skewed toward staffing agencies, small non-US employers, and boards with
sparse/templated postings that state no figure at all, confirmed by reading real misses (see
Methods tried) rather than assumed from the low number alone.

### Cross-ATS impact of the pre-existing closest-period-hint fix

This table covers the closest-period-hint fix specifically — the one bug from this pass that was
genuinely pre-existing in shared `salary.py` code (the shift-differential fix, by contrast, only
ever lived in this same pass's own new pattern; see Instruction-adherence self-assessment). Fixing
it changed already-merged ATSes' extraction results too, verified via a full per-job diff against
each ATS's frozen corpus (not just re-running the aggregate percentage), repeated after each of
the three fix iterations in this pass:

| ATS | lost | gained | value changed | net |
|---|---:|---:|---:|---:|
| workable | 0 | 21 | 1 (verified correct) | +21 |
| workday | 0 | 126 | 0 | +126 |
| greenhouse | 2 (both verified: genuinely ambiguous, correctly `None`) | 490 | 7 (all individually traced and verified as improvements) | +488 |

Every "lost" or "value changed" row was individually traced to its source text and verified
correct before accepting the diff — not just counted. None of the already-published coverage
percentages for workable/workday/greenhouse are updated in their own docs for this small a
movement (all round to the same headline figure at the precision those docs report); this table
is the record of what actually changed and why.

## What changed in code, and why

- `src/headstart/scrapers/smartrecruiters.py`:
  - New `_compensation_custom_fields()` helper + `_COMPENSATION_FIELD_LABEL` regex: scans
    `customField` for a salary/compensation/pay-range-labeled entry and appends
    `"{label}: {value}"` to the job description, so `headstart.salary`'s existing, well-tested
    Tier 2 cascade parses whatever shape shows up — not a bespoke Tier 1 parser, since the field is
    company-configured and genuinely non-standardized (free text for one company, a bare max-only
    figure for another).
- `src/headstart/salary.py`:
  - **New `_BARE_HOURLY_OR_DAILY` pattern**, run last (lowest priority) in `from_description`'s
    cascade — a bare `"$X/hour"` or `"$X per day"` with no label at all. Deliberately scoped to
    hourly/daily only, not monthly/yearly (unmeasured, likely higher false-positive risk — a bare
    unlabeled `"$X/month"` is more plausibly rent or a subscription).
  - **Fixed: `_BARE_HOURLY_OR_DAILY` excludes a leading `"+"`** (`(?<!\+)`) — shift-differential
    add-on figures ("+$4.50/hr") are structurally distinguishable from base rates this way; a
    word-based "differential" context guard was tried first, found to break a genuine base-rate
    case, and reverted (see Instruction-adherence self-assessment).
  - **Fixed: `_period_from_window` now picks the period-hint match CLOSEST to the number**
    (by character distance), not simply the first one found scanning the whole window
    left-to-right — the root cause of the "Shift: Day ... $X/hour" bug. Refined a second time to
    deprioritize an "after" hint that starts a new sentence (sentence-initial capitalization, not
    ALL-CAPS emphasis) right where the number ends, after that exact shape ("... $25-35 Annual
    continuing education...") turned out to be closer than a genuine "before" hint in one real
    case.
- `tests/test_salary.py`: 8 new tests, verified via `grep -c "^def test_"` (57→65) — bare
  hourly/daily (with and without a label), the ambiguous-multiple-rates case, the shift-differential
  exclusion (and its "must not over-exclude a genuine nearby base rate" companion), the
  closest-period-hint fix, its sentence-boundary regression fix, and the ALL-CAPS-must-still-work
  companion.
- `tests/test_scrapers.py`: 2 new tests for `_compensation_custom_fields` — appended when a
  matching label exists, description left unchanged when it doesn't. All 8 pre-existing
  smartrecruiters scraper tests still pass unmodified.

**Code-review fix-up (2026-08-22, both axes):** no hard Standards or Spec violations found.
Applied:

- `_period_from_window`'s nested `_distance` closure — the only nested closure in the module,
  breaking the file's own convention of flat top-level single-call-site helpers
  (`_guess_currency`, `_mutually_consistent`) — extracted to a top-level function taking
  `rel_start`/`rel_end` explicitly.
- The `1000`-point new-sentence penalty, previously an unnamed magic number, named as
  `_NEW_SENTENCE_PENALTY` with a comment tying it to the window size, matching the file's existing
  convention for other tunables (`_CONTEXT_WINDOW`, `_HOURLY_TO_ANNUAL`).
- Added a top-level docstring to `_period_from_window` stating its two-stage shape (closest-hint
  selection, then the comma-gap veto) and how they compose — a third-iteration function with no
  synthesis comment was flagged as heading toward becoming hard to reason about safely, even
  though each individual stage was already well-commented.
- The doc's own framing was corrected in two places above (Methods tried, self-assessment): the
  shift-differential fix had been described alongside the period-hint fix as "two general bugs
  affecting every ATS," which overstated it — the differential issue only ever lived in this
  pass's own new pattern and was caught before it shipped, unlike the period-hint bug, which was
  genuinely pre-existing in already-merged code. The `_span_from_match`-refactor precedent from
  greenhouse's own fix-up round (fixing a doc inaccuracy rather than letting it stand) applies
  here too.

## Post-merge correction (2026-08-25): the "no compensation field" premise was wrong

**This pass's own "jobs with a structured `salary` field | 0 (0.0%) — no compensation field in
the raw payload" line (Coverage table above) was never actually true.** A separate location-field
audit (`experiment/location-audit-2026-08-25/smartrecruiters.md`) found the posting-**detail**
response (the same one this pass's own `_extract_description` already parses for the description)
carries a native `compensation.{min,max,currency,period}` block on 10.48% of a 2,500-detail
sample — this pass's own methods section never checked the detail payload for it, only reasoned
from the listing. Fixed in a follow-up PR: `smartrecruiters.py` gained a `_salary()` helper
(the same "MIN-MAX CODE INTERVAL" shape lever/recruitee/teamtailor/ashby/personio/rippling
already produce) and `smartrecruiters` was registered in `salary._FIELD_PARSERS`.

**Live re-verified from a fresh session** (350 boards, seed 20260825, ≤25 postings/board to
bound cost; 347/350 boards had a nonempty listing, 0 listing errors; 2,816 postings, 2,812
detail fetches ok, 4 errors — 0.14%, real-world noise, not systematic):

| metric | value |
|---|---|
| native `compensation` populated | 257 / 2,812 (9.14%) |
| BEFORE (Tier 2 only — today's shipped behavior) | 314 / 2,812 (11.17%) |
| AFTER (Tier 1 native + Tier 2 — this fix) | 466 / 2,812 (16.57%) |
| gained (found only after the fix) | 152 |
| lost (found only before the fix) | 0 |

The relative jump is smaller here (~1.48x) than the original 1,500-posting comparison's 7.40%→
16.33% (~2.2x) — this sample's Tier-2-only baseline happened to be higher (11.17% vs. 7.40%,
board-mix variance), but the AFTER number lands within half a point of that comparison's
projection (16.57% vs. 16.33%), and the zero-regression (0 lost) result held across both.
Zero extra request cost confirmed: every detail fetch above is the scraper's own `_job_detail()`,
one GET per posting id — the identical request the shipped code already makes for `description`
alone.

**A real, pre-existing, out-of-scope gap this measurement surfaced**: `salary.py`'s
`_CURRENCY_CODES` table (`USD|EUR|GBP|INR|CAD|AUD|HKD|SEK|PLN|CHF|AED`) does not include CNY,
NZD, GTQ, or TZS — all four observed on smartrecruiters' native `compensation.currency` in the
original audit. A figure in one of those currencies still parses (the span and period are read
correctly), but `currency` comes back `None` and `_bounded`'s USD-shaped fallback bounds decide
plausibility instead of a currency-specific one — the same fallback every other ATS's Tier 1
parser already relies on for an unrecognized code, not something this fix introduced. Left
unfixed here (a shared-table change is broader than one ATS's scraper fix); flagged for the
separate salary-corpus-audit follow-up. Not exhaustive: a smaller code-review sample (60 boards,
348 postings, 2026-08-26) turned up HUF, RON, and MXN as further currencies missing from the same
table — the gap is broader than the four currencies this pass happened to sample.

## Follow-up (2026-08-26, code review): max-only compensation must decline, not misreport as floor

The `_salary()` helper above formatted a `max`-only block (`min` absent) as a bare single value,
which `_field_range_currency_interval`'s single-value path always reads as a **floor** with no
ceiling. Live-verified real (not just the `max: 0` junk case already documented): 60 live boards,
348 postings, 19 populated `compensation` blocks — 1 of them max-only, `{"max": 12150, "currency":
"MXN", "period": "MONTHLY"}`, which annualizes to 145,800 and clears `_bounded`'s USD-fallback
plausibility bounds cleanly, so it would have shipped as a confident, wrong "$145,800+/year, no
ceiling" instead of the true "up to ~$12,150 MXN/month, no stated floor". Unlike the `max: 0` junk
case (still correctly caught downstream since 0 is below every currency's floor), this one is not
caught anywhere — a silent corruption, not a safe decline. Fixed: `_salary()` now declines
(`None`) outright whenever `min` is absent, regardless of `max`. `min`-only (a genuine "$X+, no
stated ceiling") is unaffected — that direction was already correct.

This mirrors ashby's own `_salary()` docstring, which checked the identical mirror shape directly
against live data, found 0/820 real occurrences, and left it on the same single-value path
deliberately. SmartRecruiters' rate is small (1/19 in this sample) but nonzero, so the same
latitude does not apply here. The 152-gained / 16.57%-after figures above predate this refinement
and were not re-measured against it — the corrected count is somewhat lower, by however many of
those 152 were max-only.

## Known gaps, left honestly unresolved rather than guessed at

- **"Target Salary Range Max"-only custom fields** — a real, measured shape (part of the 9.8%
  customField-carrying jobs) deliberately not extracted; forcing a ceiling-only figure into the
  min-required schema would misrepresent it as a floor. No schema change was judged justified for
  this alone.
- **A ~0.03% (2-job, single-company) "target figure falls within an adjacent range" ambiguity**
  (`"Target Salary: $42,356 Salary Range: $39,863 - $49,830"`) — the bare figure and the range
  aren't actually contradictory (42,356 falls inside the range), but the shared `_resolve()`
  ambiguity logic doesn't currently know that, and extending it for a 2-job yield (both from one
  company) wasn't judged worth the risk to logic every other ATS also depends on.
- **Small-yield patterns found but not built** (see Instruction-adherence self-assessment): day/
  hourly-rate label words (9 occurrences), a weekly period marker (5), currency-code support for
  `_BARE_BETWEEN` (1), a bare "paid $X" connector (4).
- **Non-English customField values exist** (not separately measured this pass, but implied by the
  Dutch/French postings already documented on greenhouse's pass) — out of scope per this repo's
  English-only search-index policy.

## Carried forward from workable, workday, and greenhouse

- **Applied**: check the raw payload directly for an undiscovered field before concluding Tier 1
  is a dead end (workday, greenhouse) — this is exactly what surfaced the customField discovery,
  the highest-value finding of this pass.
- **Applied, and validated again**: the "diff the full corpus, not just the aggregate" discipline
  — caught two near-miss regressions this pass (the differential guard, the period-hint
  proximity fix's own regression) that unit tests alone did not catch, mirroring greenhouse's own
  ~150-match near-miss from the same root cause (a plausible-looking fix that wasn't checked
  against real data before being judged correct).
- **Applied**: fix ambiguity at the source (the scraper) when the scraper has more context than
  the description string retains — the plan's own stated principle, applied here to
  `customField` exactly as darwinbox's `salary_timeframe` fix applied it during the pilot.
  Extended: a full cross-ATS regression diff is now standing practice for any change to shared
  `salary.py` code, not just a one-off exercise — this pass needed it twice, for two independent
  bugs found well after the "main" smartrecruiters-specific work was otherwise done.
- **New lesson for future ATSes**: a company-configurable custom-field mechanism (greenhouse's
  `metadata`, smartrecruiters' `customField`) is worth checking on every remaining detail-pass ATS
  during its own research pass — not assumed present, and not assumed absent just because the
  standard description sections don't carry it.
