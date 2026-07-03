# ADR-0018: Experience extraction — widened description patterns + a data-calibrated seniority fallback

- Status: Accepted
- Date: 2026-07-03
- Extends [ADR-0009](0009-experience-extraction.md) (adds the deferred seniority tier)

## Context

Run over the real tech corpus (`data/jobs/tech/`, 49,725 jobs), the ADR-0009 extractor covered only
**45%**. Analysing how each ATS actually states required experience (via
`scripts/enrich/experience_coverage.py`) showed two structural gaps:

1. **Seniority-label fields, not numbers.** recruitee (`entry_level`, `experienced`, `mid_level`),
   personio (`experienced`), smartrecruiters (`Mid-Senior Level`, `Associate`), and workable
   (`Mid-Senior level`) express experience as a *label*, not a digit — so `from_field` returns None.
   **recruitee sat at 2%.** This is exactly the "seniority inference" tier ADR-0009 deferred.
2. **Description phrasings the regex missed.** Reading the missed descriptions by hand surfaced:
   `N years of/in/as <role>` with no "experience" word (`5+ years in software testing`), reversed
   ranges (`Experience: 8 – 12 Years`), `N plus years`, and `.`/`·` breaking the gap (`Min. 10 Years`).
   The genuine misses in greenhouse/lever turned out to be mostly experience-*less* (credential-based
   gigs, skills-only requirements, "curiosity > CV") — not regex gaps.

## Decision

**Concrete numbers always win; a seniority label is a fallback only when no number is stated** (the
cascade is `from_field` → `from_description` → `from_seniority`; `extract` gains an optional `title`).

1. **Widen `from_description`** — add the four phrasings above, allow `.`/`·`/`•` in the gap, and add a
   pattern for `N years of/in/as <work word>` that needs no "experience" word but stays anchored to
   `of/in/as` + a work token so `per year` / `N years ago` don't match.
2. **Add `from_seniority` (Tier 3, fallback)** — map a seniority label (the source's field, else the
   title, e.g. `Senior Engineer`) or a numeric/roman **level suffix** (`Software Engineer 1`,
   `Data Scientist III`) to a floor-years estimate. It runs only when neither number tier fired.

**The seniority→years mapping is calibrated to the data**, not guessed: for jobs carrying *both* a
label and a concrete number in their description, the median description-`min_years` per tier is —

| tier | median actual `min_years` | mapped |
| --- | --- | --- |
| entry / junior / intern | ~1 | 0 |
| associate / mid-level | 3 | 3 |
| senior / mid-senior / experienced / (smartrecruiters) executive | 5 | 5 |
| lead / staff | — | 7 |
| director / chief | 10 | 10 |

The calibration corrected two guesses: `experienced` is a 5 (not 3), and smartrecruiters' `Executive`
is a *level* worth ~5 (not C-suite 10).

## Result

Coverage **45% → 79%** (`scripts/enrich/experience_coverage.py`): recruitee 2%→100%, personio
17%→100%, smartrecruiters 16%→95%, workable 58%→92%, ashby 50%→79%, greenhouse 44%→70%, lever
9%→58%. The description patterns added ~2,200 numeric hits; the seniority fallback added ~15,100. The
residual `none` (~10k) is dominated by jobs that genuinely state no experience.

## Rejected alternatives

- **An LLM classifier per job** — far too expensive at this scale; the deterministic tiers reach 79%.
  An LLM tier stays deferred (it would chase the genuinely-unstated residual).
- **Guessing the seniority years** — calibrated against real numbers instead, so the floors reflect
  what each label actually means in the corpus.
- **Tightening the direction-agnostic anchor** — still deferred (the ADR-0013 strict-xfail stands);
  it needs the corpus to re-verify recall, which the coverage tool now supplies for a future pass.

## Consequences

The `seniority` source is an *estimate* (a floor for the "≤ N years" filter), not a stated
requirement — callers can treat it as lower-confidence than `field`/`regex` via the `source` tag. The
`N years of/in/as` pattern can admit an occasional non-requirement number, but it's anchored and the
`experience`-anchored patterns take precedence. `experience_coverage.py` is the standing
read-then-widen verification loop (noted in CLAUDE.md).
