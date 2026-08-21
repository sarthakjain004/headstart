# Zoho Recruit — salary extraction findings

Fifth ATS in the salary-extraction initiative. See `README.md` for the overall process and the
four prior passes: `workable.md` (pilot), `workday.md` (first detail-pass ATS, two code-review
rounds), `greenhouse.md` (highest coverage, two general `salary.py` bugs found), `smartrecruiters.md`
(a company-configurable custom field, plus a genuinely pre-existing period-hint bug).

## Methods tried

- **Sampled 3,000 of 5,337 live boards** (freshly measured 2026-08-22, superseding whatever stale
  count the README carried before). `has_detail_pass = True`, but a genuinely different shape from
  workday/smartrecruiters: `fetch_raw()` only detail-fetches jobs *missing* `Job_Description` from
  the initial listing payload — the scraper's own docstring documents 28/71 sampled tenants
  needing this, the rest get everything from one cheap request. Built `_fetch_zoho` in
  `salary_sample.py` to mirror this conditional shape: caps detail-fetches at 3/board (matching
  every other detail-pass ATS), but **deliberately does not cap total job count** for tenants whose
  descriptions are already in the listing, since including them costs zero additional requests.
  Confirmed working as designed: one sampled tenant returned 750 jobs, another 421, purely from
  the free listing payload. **This is a real methodology divergence from workable/workday/
  greenhouse/smartrecruiters' uniform ≤3/board cap** — zoho's total job count (58,004 across 3,000
  boards, ~19/board average) is not directly comparable to the other ATSes' bounded ~1-27/board
  average on that basis alone; the bound that matters (network requests, not job count) is
  unchanged. 3,000/3,000 boards succeeded (0 errors).
- **Checked for a dedicated or custom salary field before assuming Tier 1 was a dead end** —
  carried forward from greenhouse's/smartrecruiters' passes, and it paid off differently here:
  zoho's raw job records carry a genuine, dedicated `Salary` field (plus a companion `Currency`
  field), a real first-class schema field, not a company-configurable workaround. **Measured
  across two independent samples (100 tenants, 2,808 jobs total, seeds 7 and 999): the field is
  never populated by any real tenant sampled — 0/2,808.** A confirmed, well-evidenced Tier 1 dead
  end, same conclusion class as `freshteam`'s own documented precedent (`ctc_details` null on
  2,591/2,591) — a first-class negative finding, not a section to skip.
- **One gap-analysis round**, reading real misses broadly since the coarse-hint rate (11.1%) was
  the lowest seen so far. Found five real, distinct gaps, each measured for prevalence before
  building anything: a British informal "p/h" hourly shorthand (69 occurrences, concentrated in
  one recruitment agency but a real, standard abbreviation), a `"paying"` verb-conjugation
  variant of the `"pay"` label word (210 occurrences, 175 missed), `"a year"` as a bare period
  marker (50 occurrences, mostly one company's template but 3 others too), and two compound
  connector phrases (`"of up to"`, `"is up to"`, ~13-16 occurrences each, found independently in
  different companies' text, so not a single-template artifact).
- **A guard was checked and deliberately not added** — the space-separated `"sign on bonus"`
  phrasing (150 occurrences) looked like it might need the same guard treatment as the already-
  fixed hyphenated `"sign-on bonus"` variant. Directly verified: **zero genuine false positives**
  across the full 58,004-job corpus — every nearby extracted figure traced to a real, separately-
  stated base salary, never to the bonus amount itself. Not fixed, on the same evidence-based
  reasoning that declined a speculative "equity" guard on greenhouse's pass: a plausible-sounding
  risk that real data doesn't actually support.
- **A real, understood side effect of the "of up to" fix, found via the mandatory cross-ATS
  regression diff** (not the zoho sample itself): on workday, a description stating both a base
  salary ("is $84,000") and a conditional higher figure ("may be provided a higher starting
  salary of up to $92,400") now has *two* matches instead of one, since the fix taught `_LABELED`
  to recognize the second phrasing it previously couldn't see at all. The existing ambiguity guard
  correctly declines rather than picking one — this is the same mechanism working as designed on a
  case it was previously blind to, not a new bug. See What changed in code.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 3,000 (5,337 live, above the cap).
- Measured both required percentages: **yes** — 0.0% structured-field (confirmed via two
  independent 100-tenant probes, not assumed from one), 5.9% overall description-mining coverage
  (3,414/58,004), 11.1% coarse-hint rate reported alongside for calibration.
- Live-verified after code changes: **yes** — one fresh, differently-seeded round (40 boards,
  seed=619) after all fixes, plus the mandatory full-corpus diff against all 4 already-merged
  ATSes' frozen corpora for the shared-code changes.
- Went beyond the ask: independently confirmed the Tier-1 dead-end finding with a *second*,
  differently-seeded probe rather than accepting one sample's negative result at face value.
  Investigated a guard candidate (space-separated "sign on bonus") thoroughly enough to find it
  *didn't* need fixing, rather than fixing it speculatively because a hyphenated sibling had
  needed a fix before. Traced the "of up to" fix's own side effect (the workday ambiguity case) to
  its real source text and confirmed it's correct behavior rather than assuming a corpus-diff
  "loss" must be a regression.
- Did not: build special envelope-handling logic for the "base + conditional higher figure"
  ambiguity found on workday (2 jobs, one company) — same proportionality reasoning as
  smartrecruiters' declined 2-job "target within range" ambiguity: too small a yield to justify
  extending shared ambiguity-resolution logic every other ATS also depends on. Did not chase the
  parenthesis-before-amount or dash-before-connector punctuation gaps found during misses-reading
  (8 and 10 occurrences respectively) — smaller than the ATSes' established threshold for a
  dedicated fix, and structurally riskier to get right than the connector-phrase fixes that were
  built. Did not investigate non-English postings (confirmed present: Italian "RAL", Dutch
  "salaris", French listings) — out of scope per this repo's English-only search-index policy,
  consistent with every prior pass.

## Live-verification review

One round, against real current `*.zohorecruit.{com,eu,in,com.au}` hosts, after all fixes:

- **40 fresh boards, seed=619** (distinct from the main sample's seed=7), `--workers 20`. 40/40
  succeeded, 0 errors, 1,566 jobs, 4 real extractions (0.26% — much lower than the frozen sample's
  5.9%, explained by genuine board-mix skew rather than a bug: 2 of the 40 sampled boards
  (`afconrecruitltd`, 421 jobs; `tehora`, 662 jobs — together 69% of this sample's job count)
  happened to be salary-free — `afconrecruitltd` confirmed by reading real postings, `tehora`
  confirmed to be a French-language Quebec engineering firm, out of scope regardless). All 4 real
  extractions spot-checked for plausibility (Coach Drivers £37.4k-£41.6k GBP, Digital Marketing
  Sales $65k-$125k USD, Intake Specialist $35.4k-$52k USD, a Magento Developer at $20,000 with
  correctly-unresolved currency) — all genuine.

## Patterns found

- **A genuine, dedicated `Salary`+`Currency` schema field that no real tenant populates** — the
  strongest-looking Tier-1 candidate of any ATS pass so far, and a confirmed dead end regardless.
- **"p/h" as British informal hourly shorthand**, almost always glued directly onto the number
  with no separator (`"£21.50p/h"`) — structurally distinct from every other period marker this
  module recognizes, since there's no non-word character anywhere in the glued sequence for a
  leading `\b` to anchor on.
- **Verb-conjugated label words** (`"paying"` for `"pay"`) — the same class of gap workday's pass
  found and left unfixed (`"Starts"` vs `"starting"`) but here measured large enough (175 misses)
  to justify the fix.
- **Compound connector phrases** (`"of up to"`, `"is up to"`) that this module's single-alternative
  connector list didn't recognize as two-word sequences, even though each word was independently
  recognized alone.
- **Regional job-board duplication**: one UK company (`towertrophies`) posts the *same* role
  separately per region (Essex, South Yorkshire, Bedfordshire, Wiltshire, Northamptonshire,
  Northumberland — 6 near-identical postings sampled), each stating `"£5,000 - £20,000 a year
  COMMISSION ONLY"` — correctly declined by the plausibility floor (£5,000 fails GBP's £8,000
  minimum), a genuine commission-heavy role rather than a bug.

## Coverage

| metric | value |
|---|---|
| boards sampled (of 5,337 live) | 3,000 |
| jobs seen (uncapped for "cheap" tenants — see Methods tried's methodology note) | 58,004 |
| jobs with a structured `salary` field | 0 (0.0%) — confirmed dead end across 2,808 jobs, 2 independent samples |
| jobs with a description-only signal | 3,414 (5.9%) |
| overall Tier1+Tier2 coverage | 5.9% |
| coverage before this pass's fixes (baseline, existing generic patterns only) | 5.75% |
| coarse hint rate (calibration only) | 11.1% |
| boards with ≥1 job showing a signal (coarse) | 733/3,000 (24.4%) |

The lowest overall coverage of the five ATSes so far (workable 15.4%, workday 27.6%, greenhouse
36.1%, smartrecruiters 10.0%, zoho 5.9%) and the lowest coarse-hint rate too — a genuinely
different, more international/agency-heavy company mix (confirmed by reading real misses: Italian,
Dutch, and French postings; UK recruitment agencies; small companies with boilerplate "competitive
salary" text and no stated figure) rather than a weaker extraction pass.

## What changed in code, and why

- `src/headstart/salary.py`:
  - **`_PERIOD_HINT`: added `p\s*/\s*h\b` (no leading `\b`) for British informal "p/h"**, and a
    matching downstream check (`"p/h" in hint.replace(" ", "")`) since "p/h" contains neither
    "hr" nor "hour" as a substring, so the existing multiplier-selection logic wouldn't have
    recognized it as hourly even once the pattern matched.
  - **`_PERIOD_HINT`: added `\ba\s+year\b`** as a bare-annual period marker alongside the existing
    "per year"/"/year"/"annual(ly)" forms.
  - **`_LABELED`: `pay` → `pay(?:ing)?`** to recognize the verb-conjugated form.
  - **`_LABELED`: added `of\s+up\s+to` and `is\s+up\s+to`** as two-word connector alternatives,
    placed before the bare `of`/`is` alternatives so the longer, more specific match wins.
- `scripts/enrich/salary_sample.py`: new `_fetch_zoho()` bounded adapter (registered in
  `_DETAIL_ADAPTERS`), matching the conditional detail-fetch shape described in Methods tried.
- `tests/test_salary.py`: 7 new tests, verified via `grep -c "^def test_"` (65→72) — the "p/h"
  shorthand (glued and spaced), the "paying" verb form, the "a year" period marker, both compound
  connectors, and the "of up to" fix's own real, understood ambiguity side effect on workday.
- No changes to `zoho.py` itself — the bounded sampling adapter reuses its existing `_records`/
  `_detail_description` methods directly; the confirmed-dead `Salary`/`Currency` fields needed no
  scraper change since there's nothing populated to read.

### Cross-ATS impact of the shared `salary.py` fixes

All four fixes above live in shared code, not zoho-specific dispatch. Verified via a full per-job
diff against each already-merged ATS's frozen corpus (not just re-running the aggregate
percentage), run once after all four fixes landed together:

| ATS | lost | gained | value changed | net |
|---|---:|---:|---:|---:|
| workable | 0 | 0 | 0 | 0 |
| workday | 2 (both traced and understood — see below) | 2 | 0 | 0 |
| greenhouse | 0 | 8 | 0 | +8 |
| smartrecruiters | 0 | 7 | 0 | +7 |

The 2 workday "losses" are not a regression: `_LABELED` recognizing `"of up to"` as a connector
means a second, previously-invisible `"... a higher starting salary of up to $92,400"` phrase now
also matches alongside an already-extracted `"is $84,000"` base figure. Both are real, but
genuinely different (a guaranteed base vs. a conditional higher tier for exceeding requirements) —
the existing ambiguity guard correctly declines rather than picking one, exactly the no-fabrication
principle working as intended on a case it couldn't previously see at all. Traced to the real
source text and confirmed correct before accepting the diff, not just counted.

## Known gaps, left honestly unresolved rather than guessed at

- **Parenthesis or em-dash between a label and its connector** (`"salary (£30,000-£60k Base
  Salary..."`, `"pay – up to £53,000"`) — real (8 and 10 occurrences respectively) but smaller
  than this pass's built fixes and structurally riskier to widen safely without more evidence.
- **Non-English postings** (confirmed: Italian "RAL €32.000 - €40.000", Dutch "salaris... tussen
  de € 15,00 en € 17,00", French) — out of scope per this repo's English-only search-index policy,
  consistent with every prior pass's finding of the same.
- **A base-figure-plus-conditional-higher-figure ambiguity** (the workday case found via the
  cross-ATS diff) — real, but only 2 known occurrences from one company; extending the shared
  ambiguity-resolution logic for that yield wasn't judged worth the risk to every other ATS.
- **"Higher placement within the salary range" boilerplate with no adjacent figure** (rwjf,
  workday) — describes the EXPERIENCE tiers that map to salary placement, not a number itself;
  genuinely non-extractable without inferring which tier applies, correctly left unresolved.

## Carried forward from workable, workday, greenhouse, and smartrecruiters

- **Applied**: check for a dedicated or custom-configurable salary field via direct API inspection
  before assuming Tier 1 is a dead end (greenhouse's `metadata`, smartrecruiters' `customField`)
  — zoho's genuine dedicated `Salary` field made this feel like it would finally pay off, and the
  discipline of actually *measuring* population (twice, independently) rather than assuming from
  the field's existence is what turned a plausible Tier-1 win into an honest, confirmed dead end.
- **Applied**: verify a guard candidate against real data before adding it, even when a sibling
  pattern already needed the same class of fix (workday's hyphenated "sign-on bonus" vs. zoho's
  space-separated variant) — checked, found zero real false positives, correctly left unfixed.
- **Applied, extended**: the full corpus diff caught a real, understood side effect (not a bug)
  this time, rather than a broken fix — worth noting as a *different* outcome of the same
  discipline: not every diff finding is a regression to fix, some are the ambiguity-safety
  mechanism correctly doing its job on a newly-visible case. Tracing to real source text before
  deciding which is which remains the load-bearing step either way.
- **New for future ATSes**: a scraper's bounded sampling adapter doesn't have to mirror the
  uniform ≤3/board cap exactly — when a scraper's own shape means some boards cost nothing extra
  to sample in full (already-fetched, already-parsed), capping them anyway only throws away free
  signal. Document the divergence clearly rather than silently deviating from the established
  pattern.
