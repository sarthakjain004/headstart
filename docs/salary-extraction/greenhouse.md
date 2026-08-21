# Greenhouse — salary extraction findings

Third ATS in the salary-extraction initiative. See `README.md` for the overall process and the
two prior passes: `workable.md` (pilot), `workday.md` (first detail-pass ATS, two code-review
rounds).

## Methods tried

- **Sampled 3,000 of 7,503 live boards** (`config.load_active_companies` — freshly measured
  2026-08-22; the README's earlier "9,152" figure was from an older liveness snapshot and is
  superseded by this measurement, per CLAUDE.md's freshest-data rule), `--seed 7 --workers 32`.
  2,969/3,000 boards succeeded (31 board-level errors — mostly 404s on since-removed boards, 1.0%,
  not systemic). 80,434 jobs — greenhouse is listing-only (`has_detail_pass = False`, confirmed:
  `?content=true` inlines the full description in the single listing request, no per-job detail
  fetch needed), so every job on every sampled board is in the sample, not capped at ≤3/board like
  workday's detail-pass adapter — this is *why* the job count is ~10x workday's despite fewer
  boards sampled.
- **Checked the plan's specific flag**: does greenhouse's `?content=true` payload carry an
  undiscovered compensation field? Verified directly against live boards (stripe, airbnb,
  robinhood, affirm, figma, brex) — no top-level compensation field; a per-company custom
  `metadata` array exists (Greenhouse's admin-configurable custom fields) but sampled real values
  only ever held things like "Workplace Type", never a pay figure. **Negative result, first-class
  finding**: there is no structured salary field to read on this ATS — `parse()` correctly never
  sets one, and Tier 1 (`from_field`) is a confirmed dead end here, same conclusion as workday's
  own pass but confirmed independently via direct API inspection rather than inferred from
  `parse()`'s source alone.
- **Two gap-analysis rounds**, real snippets read each time (not just the coarse-hint numbers):
  round 1 (12 samples) found the em-dash separator, the "between $X and $Y" phrasing, and the
  SpaceX-specific leveled-bands template; round 2 (15 samples, different seed) confirmed no other
  large pattern family remained and surfaced two general (non-greenhouse-specific) bugs in
  `_period_from_window` and the number-capture regex — see "What changed in code" below.
- **Measured real prevalence before building anything**, per the project's own established
  discipline: em-dash range separator (13,721 raw occurrences, 5,906 missed before the fix — the
  single highest-value pattern found on any ATS pass so far), "between $X and $Y" (653 occurrences,
  622 missed), leveled compensation bands (693 occurrences, 495 missed, 494/495 traced to one
  company — SpaceX). A fourth candidate (bare "pays $X", 22 occurrences) was checked and correctly
  **not** built — too rare to justify a dedicated pattern.
- **Went looking for false-positive risk before shipping, not after**: tested the new
  `_BARE_BETWEEN` pattern against contrived adversarial cases (unrelated RSU/bonus dollar
  mentions, an equity-grant framing) before accepting it. One contrived case ("equity grant value
  between $X and $Y") did extract — checked against the real corpus (grep for "equity" near a
  between-range) and found exactly one real occurrence, which was a genuine salary with equity
  mentioned as a separate benefit in the same sentence, not a false positive. Correctly declined to
  add a speculative equity guard on n=1 evidence that didn't actually support one.
- **Two real, general bugs found and fixed** (not greenhouse-specific — affect every ATS): a
  trailing-comma-in-number-capture bug (`\d[\d,]*` swept a sentence's own trailing comma into the
  `hi` capture) and a period-hint bleed-through bug (`_period_from_window` could pick up an
  unrelated period word describing something else nearby, e.g. reading "monthly bonus
  opportunities" as the SALARY's period and multiplying by 12). See "What changed in code" below
  for the full story, including a **caught-before-ship regression**: an intermediate, overly broad
  version of the fix broke ~150 genuine matches across the two already-merged ATSes before a full
  corpus diff caught it.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 3,000 (7,503 live, well above the cap).
- Measured both required percentages: **yes** — 0.0% structured-field, 36.1% overall description
  coverage (29,028/80,434), 57.8% coarse-hint rate reported alongside for calibration.
- Live-verified after code changes: **yes** — one fresh, differently-seeded round run after all of
  this pass's fixes were in (not after each individual fix; see Live-verification review) — plus a
  full per-job diff against both already-merged ATSes
  (workable, workday), which is *not* the standard per-ATS process but was necessary here since
  two of this pass's fixes (the number-capture and period-hint bugs) live in shared `salary.py`
  code, not anything greenhouse-specific.
- Went beyond the ask: found and fixed two real, general precision bugs that could have silently
  produced *wrong* (not just missing) salary values on any ATS, not just greenhouse — the kind of
  bug this whole initiative treats as highest-priority. Verified their fix cost zero coverage on
  both already-shipped ATSes via a full per-job diff (not just re-running their aggregate
  percentage) before considering the fix safe. Declined a speculative equity guard after checking
  it against real data rather than shipping it on a contrived example alone.
- Did not: chase the "$X/hr - $Y/hr" per-side period-marker-trailing-each-number shape (256
  occurrences, 107 still missed after all other fixes) — real, but a smaller yield than the
  patterns actually built, and workday.md already establishes the precedent that not every
  evidenced gap needs its own pattern. Did not chase Dutch-language salary phrasing (real,
  observed at `newtone`) — out of scope given this repo's English-only search-index policy
  (CLAUDE.md), so the value of extracting a non-English board's salary is much lower even where
  technically possible.
- A caught mistake, reported honestly rather than smoothed over: an intermediate version of the
  period-hint-bleed-through fix used an overly broad guard ("any letters in the gap between the
  number and the period word") that looked correct against the one bug case it was built for, but
  broke ~150 genuine matches across workable and workday when checked against the full corpus —
  including common phrasing like "Competitive hourly rate of 19-21 USD" (real words legitimately
  sit between the period word and the number when the word comes *before* the number). Caught
  entirely by the full corpus diff, not by unit tests (which all still passed the broken version,
  since none of the 45 existing tests happened to cover that exact shape) — a second, concrete
  demonstration of why this project's "diff the full corpus, not just the delta" rule exists.

## Live-verification review

One round, against real current `boards-api.greenhouse.io` hosts, after all fixes were applied and
the full-corpus regression check against workable+workday passed clean:

- **40 fresh boards, seed=314** (distinct from the main sample's seed=7), `--workers 20`. 40/40
  succeeded, 0 errors, 836 jobs, 445 real extractions (53.2% — well above the frozen sample's
  36.1%, expected board-mix variance: this seed happened to draw several large tech-company boards
  with strong pay-transparency compliance, e.g. `appian` at 176 jobs). Spot-checked 15 extractions
  for plausibility across a range of roles (Associate Veterinarian $135k-$165k, Store Manager
  $47.8k-$56.2k, Team Member $31.2k-$33.3k, DevRel $180k-$250k) — every figure sane for its role.
  Specifically checked all 4 CAD-currency extractions in this sample by hand (given the `\b`
  boundary bug fixed on workday's pass was exactly a currency-mislabeling bug) — all 4 genuine, no
  mislabeling. One wide range (`exodus54`'s "General Application", $50,000–$200,000) verified
  against the real source text — genuinely stated that way for a role-agnostic posting, not a
  parsing artifact.

## Patterns found

- **`"... Range $X — $Y USD"` (em-dash, not hyphen)** is a dominant, templated pattern spanning
  many unrelated companies verbatim (`sunnyside`, `greenthumbindustries`, `luminishealth`,
  `westernspecialtycontractors`, `powerx`, `blackbirdhealth`, and more) — almost certainly a shared
  compliance/HR tool many greenhouse customers plug in, not one company's own phrasing. By far the
  single highest-value fix of this pass: 5,906 jobs recovered.
- **`"between $X and $Y"`**, anchored on the literal word "between" (real: `charliehealth`'s "will
  be between $X and $Y per year" boilerplate repeated across dozens of postings,
  `zetacharterschools`'s "is between $X and $Y"). Deliberately given the *lowest* priority among
  the bare patterns — see "What changed in code" — since a description can state both a full
  labeled range and a narrower "between" sub-detail, and the fuller range is the more
  representative headline figure.
- **Leveled compensation bands** ("Level 1: $X - $Y Level 2: $A - $B ..."), 494/495 corpus
  occurrences traced to one company (SpaceX; the 495th is xAI — confirmed by its
  `job-boards.greenhouse.io/xai/` slug and URL, though the API's own `company` display field for
  that board literally reads "SpaceXAI", not "xAI" — corrected 2026-08-22, code review finding,
  PR #236, plausibly reflecting shared administrative tooling between the two companies).
  Enveloped into one min-to-max span rather than treated as ambiguous, since each band is
  explicitly part of the *same* role's stated range, not a second unrelated number.
- **The same "unmarked small-dollar range" ambiguity workday's own pass found**, independently
  re-confirmed here: `"Pay Range $17 — $17 USD"` and `"Pay Range: $19 - $25"` (no `/hr` or "per
  hour" marker, entry-level/hourly-coded roles) are genuinely indistinguishable from an implausible
  annual figure without guessing — correctly left `None`, consistent with the module's existing
  precedent (`keka`'s low-magnitude case) of declining to guess a period when nothing confirms it.
  Recurs across `sunnyside`, `greenthumbindustries`, `westernspecialtycontractors`,
  `centriaautism` (Behavior Technician, multiple postings) — common enough to be worth naming as
  its own gap class rather than several unrelated one-offs.
- **International (non-English) postings exist and are out of scope**: `newtone` (Dutch: "Een
  aantrekkelijk salaris tussen de €5.355 en €7.733") uses "tussen...en" (Dutch for "between...and")
  — not chased, consistent with this repo's English-only search-index scope.

## Coverage

| metric | value |
|---|---|
| boards sampled (of 7,503 live) | 3,000 |
| jobs seen (listing-only, no detail-fetch cap — every job on every sampled board) | 80,434 |
| jobs showing a salary at all (field OR description) | 29,028 (36.1%) |
| jobs with a structured `salary` field | 0 (0.0%) — confirmed dead end, no field exists to read |
| jobs with a description-only signal | 29,028 (36.1%) — identical to "at all" since field is always 0 here |
| overall Tier1+Tier2 coverage | 36.1% |
| coverage before this pass's fixes (baseline, existing generic patterns only) | 32.0% |
| coarse hint rate (calibration only) | 57.8% |
| boards with ≥1 job showing a signal (coarse) | 2,159/2,969 (72.7%) |

**Reconciliation note (2026-08-22, code review finding, PR #236):** the shared artifacts directory
grew further from the live-verification round after this table was measured (see the workday
pass's own reconciliation note for why — this mechanism is now expected, not a bug). A reviewer's
independent recount against the then-current directory (81,105 jobs) gave 36.3% — within rounding
of the 36.1% above and not a correctness concern, consistent with every prior reconciliation on
this initiative.

The 21.7-point gap between the coarse hint rate (57.8%) and real coverage (36.1%) is real, not a
measurement artifact — a substantial share of "mentions a currency symbol near digits" jobs turn
out, on read, to be the unmarked-small-dollar ambiguity, company revenue/valuation mentions, or
generic "competitive salary" boilerplate with no actual figure, all correctly left unresolved.

## What changed in code, and why

- `src/headstart/salary.py`:
  - **Em-dash (`—`) added as a range separator** alongside the existing `-`/`–`/`to`, in
    `_LABELED`, `_BARE_RANGE`, `_BARE_RANGE_CODE`, and `_BARE_RANGE_CODE_EACH`. Not added to `_LPA`
    or `_RANGE` (Tier 1) — no evidence either needs it.
  - **New `_BARE_BETWEEN` pattern** for `"between $X and $Y"`, anchored on the literal word
    "between" immediately before the first number (not a generic unlabeled "and", which risks
    joining two unrelated dollar mentions — e.g. "$50,000 in RSUs and $10,000 signing bonus").
    Deliberately placed **last** (lowest priority) in `from_description`'s cascade, after
    `_LABELED` and every other bare pattern: a real regression, caught by the full-corpus diff
    against workday, showed that placing it earlier let a narrower "new hires usually start
    between $A and $B" sub-detail win over a more representative "expected range is $X to $Y"
    headline figure stated elsewhere in the same description.
  - **New `_LEVEL_BAND` pattern and `_scan_level_bands()`** for the SpaceX-style leveled-band
    template — deliberately *not* run through the shared `_resolve()`/ambiguity machinery, since
    several genuinely different numbers are the expected, correct shape here (each band is real,
    stated information), not an ambiguity to reject. Runs before the generic cascade.
  - **Fixed: trailing comma absorbed into number capture.** `\d[\d,]*(?:\.\d+)?` (used in 14
    places) allowed a comma with nothing after it — real sentence punctuation, not a thousands
    separator — to be swept into a capture: `"$100,000, plus..."` matched `hi="100,000,"`,
    shifting the match's end past the comma. The number's *value* parsed correctly either way
    (`_num()` strips all commas), but the shifted match boundary broke the next fix below. Changed
    to `\d(?:[\d,]*\d)?(?:\.\d+)?`, which requires the capture to end in a real digit.
  - **Fixed: `_period_from_window` could apply an unrelated period word.** Real bug: "Earn a base
    salary of $90,000-$100,000, plus weekly and monthly bonus opportunities" read "monthly" —
    describing the separate BONUS, not the salary — as the salary's own period, multiplying by 12
    (→ $1.08M-$1.2M; only caught by luck because it happened to exceed the plausibility ceiling —
    a smaller base salary could have produced a wrong-but-plausible value silently). Fixed to
    reject a period-hint match only when there's a comma **and** real prose words in the gap
    between the number and the hint — not just any comma (a genuine trailing descriptor like
    `"$15.86 - $19.86, hourly."` must still work) and not just any letters anywhere (a genuine
    period word stated *before* the number with real words in between, like `"Competitive hourly
    rate of 19-21 USD"`, must also still work — a comma-less case an earlier, overly broad version
    of this fix wrongly broke, along with a bilingual French/English restatement that has commas
    from European number formatting but no real prose words).
  - `from_description()`'s docstring updated to describe the new tier order and why `_BARE_BETWEEN`
    runs last.
- `tests/test_salary.py`: 12 new tests, verified via `grep -c "^def test_"` (45→57) — em-dash,
  between-and (plus its unrelated-dollar-mentions guard and its lower-cascade-priority
  regression), leveled bands (with and without a period hint), the trailing-comma fix, the
  period-hint comma-boundary fix (plus its two companion tests for the cases an earlier, broader
  version wrongly broke — the bilingual restatement and the words-before-number case).
- No changes to `greenhouse.py` itself — confirmed via direct API inspection that there is no
  compensation field in the payload to read; everything found is description-only.

**Code-review fix-up (2026-08-22, both axes):** no hard Standards or Spec violations found; the
Spec reviewer independently reran the workable+workday full-corpus diff from scratch and got the
same result (0 regressions, small real gains), corroborating that claim rather than just trusting
it. Two real findings applied:

- `_scan()` and `_scan_level_bands()` duplicated the same match→SalarySpan conversion (false-
  positive guard, "k" shorthand, period multiplier, currency guess, plausibility bounds) — and
  `_scan_level_bands` was appending `_bounded()`'s return directly, which hardcodes
  `source="field"`, into its working list before rebuilding the final span with `source="regex"`
  (harmless, since only the numeric/currency fields were read from the intermediate object, but a
  wrong provenance tag on a value nothing ever inspected). Both fixed together by extracting the
  shared logic into `_span_from_match()`, which returns a correctly-tagged `"regex"` span (or
  `None`) directly.
- `_period_from_window`'s two-directions-for-the-gap logic (the period word can sit before or
  after the number) had no comment explaining why the ternary exists; added one line.
- The doc itself had a real accuracy slip, caught by direct verification, not just re-reading: the
  495th leveled-band occurrence was attributed to "xAI" without checking the API's actual
  `company` field, which reads "SpaceXAI" (the board's slug/URL still confirm it's genuinely
  xAI's board — see Patterns found). Corrected in place rather than left standing.

## Known gaps, left honestly unresolved rather than guessed at

- **Unmarked small-dollar ranges** (`"Pay Range $17 — $17 USD"` with no period marker) — the same
  class workday's pass found, independently reconfirmed here at meaningfully higher volume. Not
  fixed for the same reason: no positive signal distinguishes an hourly rate from an implausible
  annual figure, and the module's established precedent (keka) is to decline rather than guess.
- **`"$X/hr - $Y/hr"`** (a period marker trailing *each* side of a range, not the range as a
  whole) — real (256 occurrences), but only 107 still missed after the other fixes (the rest
  already resolve correctly via the normal adjacent-period-hint path), and building a dedicated
  `_BARE_RANGE_CODE_EACH`-style pattern for period markers specifically was judged not worth it at
  this yield relative to what was already built this pass.
- **Non-English postings** (confirmed: Dutch at `newtone`) — out of scope per this repo's
  English-only search-index policy; not chased.
- **Locale-aware number parsing** (European decimal-comma) — same already-documented gap from
  workday's pass (the reverted `_TRAILING_SYMBOL` pattern); still unimplemented, still real.

## Carried forward from workable and workday

- **Applied**: the "read real misses, measure prevalence before building a pattern" loop
  (workable/workday); per-currency plausibility bounds as the primary safety net rather than
  building new explicit guards speculatively (workday) — directly validated again this pass when a
  contrived "equity grant" adversarial test case turned out, on real-corpus inspection, to not
  actually be a problem; the "diff the full corpus old-vs-new per job, not just the aggregate
  percentage" discipline (workable) — this is what caught both the hummingbird cascade-ordering
  regression and the ~150-match over-broad-guard regression before either shipped.
- **Extended**: workday's pass established that a reviewer's contrived adversarial example is a
  floor, not a ceiling, for how far to test a risk category — this pass's own initiative (not
  prompted by external review) applied that same discipline to its *own* new code, twice, catching
  two real regressions before they ever reached a PR.
- **New lesson for future ATSes**: when a fix touches shared `salary.py` code (not an
  ATS-specific dispatch table entry), it can silently move already-shipped, already-merged ATSes'
  numbers — a full per-job diff against every previously-merged ATS's frozen corpus, not just the
  current one, is now part of the process for any fix that isn't obviously scoped to one ATS's own
  Tier 1 parser.
