# freshteam

## Methods tried

- **Sampled the full live population**: 989 live boards (`config.load_active_companies`), the
  plan's stale figure was 1,412 — every ATS so far has had a stale plan-stage count, this one no
  exception. `has_detail_pass = False` (confirmed: not overridden from `BaseScraper`'s own
  default, and the scraper's own module docstring says so directly — "No second pass: each job
  carries its HTML `description` inline"), so no bounded sampling adapter was needed at all:
  `salary_sample.py`'s existing listing-only path (one `fetch_raw()` call per board) is the whole
  sample, same shape as workable/greenhouse/oracle/sensehq.
- **Tier 1 is a pre-documented dead end, confirmed rather than re-discovered**: the scraper's own
  module docstring already states `ctc_details` was null on every one of 2,591 scanned jobs
  (2026-07-21). 0.00% field coverage confirmed on this pass's own 8,074-job sample too — the same
  conclusion, independently reconfirmed on a fresh, much larger corpus, not just inherited.
- **Real Tier-2 coverage without any new pattern is solid**: 14.73% overall, entirely via the
  existing shared cascade — comparable to zoho/smartrecruiters (10.0%), below teamtailor (14.1%)
  by a hair less, well below ashby/rippling (49.7%/46.4%). Freshteam's own company mix (SMB,
  largely Indian tech/services companies, plus a real long tail of US remote-first startups) skews
  toward INR-denominated postings and un-labeled figures more than the higher-coverage ATSes do.
- **Five real Tier-2 candidates read and measured, all declined** (each below this initiative's
  multi-company evidence bar, or — in three cases — not a genuine pattern gap at all once traced
  to its real mechanism):
  - A "stipend"-labeled figure correctly matching `_LABELED` but failing `_bounded()`'s INR
    plausibility floor ("a stipend of ₹5,000 per month" → ₹60,000/year, below the ₹100,000 floor
    calibrated for regular salaries, not training stipends specifically) — real, evidenced, and a
    genuinely interesting architectural question (should "stipend" carry its own, lower floor?),
    but only 1 company. Declined — below the bar, and a label-dependent plausibility floor would
    be a real `_bounded()` API change (currently keyed on currency alone, never label), not a
    quick patch, disproportionate to one company's evidence.
  - "In the range of $X-$Y" as a new connector phrase — 3 companies matched the surface pattern,
    but reading each showed three DIFFERENT, non-shared blocking reasons, not one common gap: (1)
    `sunergi` has "salary" separated from its figure by a 7-word unrelated clause ("upon reaching
    a seed round of funding"), far beyond `_LABELED`'s existing 1-3-word tolerance for intervening
    text; (2) `emeraldtreecare`'s "$60,000-$100,00" is a genuine SOURCE-DATA TYPO (a missing
    trailing zero) that collides with `_num()`'s own intentional, already-correct locale
    disambiguation (a 2-digit trailing group reads as a European decimal, per lesson 36) —
    parses as $100.00, correctly rejected as implausibly low, not a pattern bug; (3) `terra`'s
    figure is stated "per 12-week cohort," an unusual, non-standard compensation unit this
    module has no annualization concept for. No single fix would help more than one of these
    three. Declined.
  - A tiered, by-years-of-experience hourly-rate table with no label word at all ("Less than 2
    years - $80.00 per hour, From 2 to 5 years - $90.00 per hour...") — a genuinely different
    shape from the existing `_scan_level_bands` (which handles job-*level* tiers, not
    *experience-year* tiers) — real, but found in only 1 company across the full corpus.
    Declined.
  - A reversed "$N base pay" order (number before label, "$15 base pay") — 1 company. Declined.
  - "paid" (past tense) as an additional label keyword, alongside the already-recognized
    `pay(?:ing)?` — 2 companies not already extracted. Declined — below the bar.
- **Audited the no-signal bucket for currency-shaped content genuinely missed** (the mandatory
  audit, lesson 39): of 382 currency-adjacent no-signal jobs (5.5% of the 6,885 no-signal jobs,
  4.7% of the whole corpus), a 30-example random read found the dominant pattern is company
  scale/funding/revenue boilerplate repeated verbatim across many postings from the same company
  ("AUM of over US$1.5B", "$2.8B of finance", "raised $15+ M from credible investors") — the
  established, already-correctly-excluded funding/valuation guard working as intended, not a gap.
  The five real candidates above account for essentially all of the rest.
- **No `salary.py` changes ship this pass** — all five real Tier-2 candidates were measured and
  declined, three of them for reasons that trace to something other than a missing pattern once
  read closely (a genuine plausibility-floor question, a source typo, an unusual compensation
  unit). The mandatory full cross-ATS diff is correctly N/A, matching lever's/ripplehire's/
  successfactors' own precedent.
- **No demo/QA vendor tenants found**: a slug-shape check flagged 4 candidates
  (`apptestify`, `innoventestech`, `primeremotestaff`, `remotestar-team`), all confirmed
  false-positive substring matches on inspection (e.g. "primeremote**st**aff" contains "test" only
  by accident, at a word boundary) — `apptestify` checked directly by content and is a genuinely
  real company (its own real job titles: "User Acceptance Tester", "Senior Test Automation
  Engineer" — a QA/testing services business, matching its own real name). Consistent with this
  list's own established rule (CLAUDE.md's `EXCLUDED_BOARDS` comment): never exclude from slug
  shape alone.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 989, the full live population (986
  boards succeeded, 3 errored — a real, very low ~0.3% failure rate).
- Measured both required percentages: **yes** — 0.00% field, 14.73% overall (Tier1+Tier2),
  against 8,074 jobs.
- Live-verified after code changes: **N/A, correctly** — no code change ships this pass, so
  there's nothing for a fresh sample to have caught; the fresh, differently-seeded 30-board
  reseed (seed=313) was still run and confirms the (unmodified) sampling path works correctly
  against real current boards (0 errors, consistent shape).
- **Audited the no-signal bucket for language-independent currency-shaped content before
  trusting the coverage number as a ceiling**: yes — 382 currency-adjacent no-signal jobs read
  and traced to specific reasons (see Methods tried and Patterns found).
- Went beyond the ask: for every candidate that looked promising on a surface regex match, traced
  the ACTUAL mechanism via direct code execution before counting it as evidence — this caught that
  `emeraldtreecare`'s "in the range of" occurrence isn't a connector gap at all but a source-data
  typo interacting correctly with an existing locale-disambiguation feature, and that the "3
  companies" surface count for that candidate was really three unrelated single-company issues,
  not one shared 3-company gap (lessons 42/53/55's own discipline, applied without needing a
  review round to catch it this time).

## Live-verification review

Fresh, differently-seeded 30-board sample (seed=313) against real current freshteam hosts: 30/30
succeeded (0 errored), 170 jobs seen, 0% field, 25.9% description-hint (the sampling script's own
coarse detector) — a single company (`cashkaro`) contributed 31 of the 44 desc-hint jobs in this
small reseed via its own repeated funding-boilerplate text, consistent with what the full sample's
own no-signal audit already found and correctly excludes. No new patterns shipped this pass, so
there is nothing for a code change to have silently broken; this re-run confirms the (unmodified)
listing-only sampling path still works against real, current boards.

## Patterns found

Real, worked examples the existing shared cascade already extracts, unmodified — freshteam's own
company mix skews toward INR figures and US remote-startup pay-transparency disclosures:

- Standard labeled ranges with an explicit period marker (e.g. "Salary range: ₹X - ₹Y per annum",
  "Compensation: $X - $Y per year") extract cleanly via `_LABELED`.

Declined, with the real mechanism traced (not extraction gaps once understood):

- `"During training, a stipend of ₹5,000 per month will be provided"` — `_LABELED` matches
  correctly; annualizes to ₹60,000, below the ₹100,000 INR plausibility floor. A real training
  stipend, correctly below what the floor expects of a stated annual salary — the no-fabrication
  principle would need a stipend-specific floor to safely recover this, not a pattern change.
- `"Competitive salary range of $60,000-$100,00 annually"` — a genuine source typo (missing a
  trailing zero on the ceiling); `_num()`'s own intentional 2-digit-trailing-group-is-a-decimal
  rule (lesson 36) correctly reads "100,00" as $100.00, correctly failing the plausibility floor
  as an implausible ceiling. Nothing to fix — the pattern is already doing the right thing with
  ambiguous, malformed source data.
- `"Less than 2 years - $80.00 per hour, From 2 to 5 years - $90.00 per hour..."` — a real, tiered
  by-experience compensation table with no salary/pay/wage/comp label word anywhere; a
  genuinely different shape from the existing job-level band pattern, evidenced at only 1 company.

## Coverage

| metric | value |
|---|---:|
| boards sampled (full live population) | 989 |
| boards succeeded / errored | 986 / 3 |
| jobs seen | 8,074 |
| structured field (Tier 1) | 0 (0.00%) |
| description mining (Tier 2, no usable field) | 1,189 (14.73%) |
| **overall Tier1+Tier2 coverage** | **1,189 (14.73%)** |
| boards with ≥1 job showing either (loose sampling-stage signal) | 200/986 (20.3%) |

## What changed in code, and why

Nothing in `salary.py`. Tier 1 was already a documented dead end before this pass and is
reconfirmed, not newly discovered. All five real Tier-2 candidates found this pass were measured
and declined — two for genuinely thin evidence (1-2 companies each), and three more that
surface-matched a shared "in the range of" regex but, once each was traced to its real mechanism,
turned out to be three unrelated, single-company issues (a distant clause, a source typo
interacting correctly with existing locale logic, and an unusual compensation unit) rather than
one common 3-company gap. "No new pattern needed" is a legitimate, evidence-backed outcome here
(lesson 42) — the existing shared cascade already delivers 14.73% coverage on freshteam without
any ATS-specific extension.

## Carried forward

- **Lesson 40** (check for a reusable primitive / the simplest correct sampling shape before
  building anything new) applied trivially here: `has_detail_pass = False` meant the existing
  listing-only sampling path needed zero changes — the fastest confirmation yet in this
  initiative that "check the simplest shape first" pays off.
- **Lessons 42/53/55** (measure every Tier-2 candidate via real code execution against the real
  corpus before trusting a surface regex-match count as evidence, and don't stop at "does the
  pattern match" — trace WHY a match still resolves to `None`) were essential for the "in the
  range of" candidate specifically: a first-pass regex found "3 companies," which read as
  borderline-but-maybe-buildable — only tracing each occurrence's own `_LABELED`/`_num`/`_bounded`
  path individually revealed they shared no common root cause at all, and that one of the three
  wasn't a pattern gap in any sense (a source typo correctly rejected by existing, intentional
  logic). A future pass should keep applying this: a shared surface regex across N companies is
  not the same claim as "one pattern would fix all N."
- **New**: a `stipend`-specific plausibility floor is a real, evidenced (if thin, 1-company-so-far)
  idea worth remembering for a future pass that reaches better evidence for it — training/intern
  stipends are legitimately, systematically lower than regular salaries in a way the current
  currency-keyed (never label-keyed) `_bounded()` API can't distinguish. Not built here
  (disproportionate to one company), but worth checking again if a later ATS's own audit surfaces
  more stipend-shaped rejections — if the evidence base grows, this would need threading a label
  signal through to `_bounded()`, a real (if narrow) API change, not a regex tweak.
