# keka

## Methods tried

- **Live board count re-measured, not assumed**: the plan's 916 figure was stale (as every ATS
  measured so far has been). Current, properly deduplicated count: **819 live boards** (after
  this pass's own `EXCLUDED_BOARDS` fix — see below), below the 3,000 sampling cap. This pass
  sampled the full live population (12,867 jobs).
- **`has_detail_pass` unset** (`False`, `BaseScraper`'s default) — keka's own two-step fetch
  (`careerportalinfo` for the tenant UUID, then one `embedjobs` GET for the full active-jobs
  array, description included) is already a single listing pass per board; no `_DETAIL_ADAPTERS`
  entry was needed.
- **Checked for structure one level deeper** (asked a sixth time now — ashby: hit, recruitee:
  confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever: confirmed-flat-miss):
  the raw payload carries a numeric `salaryPeriod` enum (0–4, confirmed real across a 150-board
  sample) with no label mapping anywhere in the payload. Resolved as **confirmed UNDECODABLE** —
  a new, third outcome distinct from "hit" and "confirmed-flat-miss": the tenant-specific JS
  bundle every keka careers page actually loads
  (`{slug}.keka.com/careers/api/embedjobs/js/{tenant_uuid}`) contains zero occurrences of the
  string "salary" anywhere in it — the public embed-jobs widget doesn't render salary at all, so
  no label mapping exists anywhere in the public product to reverse-engineer, not merely one this
  scraper hasn't found yet. Statistical inference from magnitude doesn't resolve it either: the
  same enum value spans both LPA-shorthand-scale numbers ("3-5") and absolute-rupee-scale numbers
  ("300000-500000") across different tenants — inconsistent per-company data entry, not a
  guessable convention. The period is correctly omitted rather than guessed, exactly as
  `keka.py`'s own docstring already documented before this pass.
- **A real, incidental bug found and fixed**: Python's `f"{v:g}"` formatting (the scraper's own
  predecessor to `_format_num`) silently switches to scientific notation ("1e+06") for values ≥
  1,000,000 — neither `salary.py`'s `_RANGE` regex nor `_num()` can parse an exponent, so every
  genuine keka figure at or above ₹1,000,000 was discarded before ever reaching the extraction
  cascade. Evidenced directly: 27% of a 300-job sample of *rejected* `Job.salary` field values
  showed this shape, across 19 distinct companies. Fixed with a new `_format_num()` helper
  (fixed-point, trailing-zero-stripped). Verified against real captured examples, a dedicated
  unit test, and a full coverage remeasurement — not just eyeballed.
- **The truthy-vs-`is not None` question, asked again** (lesson from ashby's real bug): keka's
  `rng.get("minimum") or None` is deliberately kept truthy, not changed to `is not None`. Checked
  against real data before keeping it — unlike ashby's bug (a genuinely *stated* `0` silently
  dropped), every real keka `0/0` pair observed is a fully-unfilled form field, and an asymmetric
  `0/X` pair reads as "only the ceiling was entered," which `SalarySpan.min_annual` (a required
  int) can't represent regardless — the existing `lo or hi` fallback already produces the correct
  bare-ceiling string for that case.
- **Two real vendor demo/QA tenants found via reading real content, not slug shape** (the same
  discipline lever's pass established): `keka:csdemo` (681 fabricated postings, org name literally
  "keka cs") and `keka:salesdemo` (153 fabricated postings, org name "Out comes Operating" linking
  to Keka's own LinkedIn). Fixed in `EXCLUDED_BOARDS` as its own separate, standalone PR (#248),
  matching lever's PR #246 precedent — a liveness/data-quality concern, not a `salary.py` concern.
- **Read real no-signal misses, then went further and quantified the currency-shaped subset**
  (the mandatory audit from personio's own lesson): of 9,134 no-signal jobs (71.0% of the corpus),
  551 (6.0% of no-signal, 4.3% of all jobs) have currency-shaped content that wasn't extracted.
  Read directly, not assumed clean — see Patterns found for the full breakdown and what it led to.
- **Two acronym-collision risks checked directly, both confirmed harmless**: "CAD" (Computer-Aided
  Design — heavy in keka's mechanical/hardware-engineering postings) and "GBP" (Google Business
  Profile, in marketing postings) both collide with real currency codes. Checked whether either
  causes a false *extraction* (not just noise in the no-signal audit): every current CAD/GBP
  currency hit in the corpus (9 CAD, 3 GBP) is `source='field'` — Tier 1, from the employer's own
  structured payload — meaning the Tier-2 regex cascade never actually matches either acronym as a
  currency in real description text (both require an adjacent number in a specific shape neither
  acronym's real usage produces).
- **Two new numeric-shorthand/label gaps found via the currency-shaped audit, both measured before
  building, both built**: an "L" (lakh, ×100,000) suffix — real, label-anchored, 5 companies — and
  a "CTC" (Cost To Company, India's standard total-compensation term) label — real, 33 companies.
  Both checked for the same acronym-collision risk as AED/401(k) before shipping (see Patterns
  found for the specific real collisions checked and confirmed safe).
- **One real gap found, measured, and explicitly deferred** (not declined as false, deferred as
  real-but-out-of-scope-for-one-pass): "Rs."/"Rs" as a rupee indicator — 32 occurrences, 16
  companies, 16 not extracted. Unlike "L" and "CTC" (which slotted into existing alternation
  groups), "Rs" doesn't fit the existing single-character `_CURRENCY_SYM` class or the 3-letter
  `_CURRENCY_CODES` alternation cleanly, and this pass had already shipped six distinct `salary.py`
  changes — drawn as the line for this pass rather than chased indefinitely. See Known gaps.
- **One real gap found, measured, and declined as too thin** — the "X lpa to Y lpa" doubled-unit
  shape (unit stated after *both* numbers, joined by "to" rather than `_LPA`'s hyphen): 38 raw
  occurrences across 9 companies, but 35 of 38 were already extracted via some other path in the
  same description; the genuine incremental yield is 3 jobs across 2 companies. Below this
  initiative's own multi-company evidence bar (see lever's `jobgether`/rippling's/personio's
  precedents) once measured at its true incremental value rather than its raw occurrence count.
- **Cross-ATS impact measured three times**, once per round of `salary.py` changes this pass (the
  AED/leading-code/stipend/401(k) round, then the L-suffix/CTC round) — see What changed in code.
- **Live-verified twice**: a 15-board fresh, differently-seeded sample (seed=99) against real
  current `{slug}.keka.com` hosts after the scientific-notation fix, zero errors, multiple
  large-value (≥₹1,000,000) jobs confirmed correctly formatted and extracted on live data (not
  just the frozen capture) — see Live-verification review.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 819, the full live population (post
  `EXCLUDED_BOARDS` fix), below the cap; 12,867 jobs.
- Measured both required percentages: **yes** — 27.8% field, 29.1% overall (Tier1+Tier2).
- Live-verified after code changes: **yes** — a fresh 15-board, seed=99 sample against real
  current hosts, after the scientific-notation fix; zero errors, and the fix's own effect (clean,
  correctly-parsed large values) directly confirmed on live data, not just the frozen sample.
- **Audited the no-signal bucket for language-independent currency-shaped content before trusting
  the coverage number as a ceiling**: 9,134 no-signal jobs (71.0%); 551 (6.0% of no-signal) had
  currency-shaped content. Read directly — not assumed clean — and traced to specific, real
  reasons: acronym noise (CAD/GBP, 141 of 551), correctly-guarded revenue/funding/AUM/budget/
  market-size mentions (the large majority of the rest), a small number of unrecoverable "per
  class"/ceiling-only mentions, and the two genuine gaps this pass fixed (L-suffix, CTC label) plus
  one deferred (Rs.) and one declined (doubled-unit LPA).
- Went beyond the ask: found and fixed a real, incidental scraper bug (the scientific-notation
  formatting defect) worth +1,549 jobs on its own — nearly doubling Tier 1 coverage — before any
  `salary.py` pattern work began; found and fixed a real, dangerous 401(k) false-positive
  regression via hand-tracing the mandatory diff, which turned out to *also* fix a pre-existing
  false positive already live on 8 previously-merged ATSes (see What changed in code); found and
  fixed 2 real vendor demo/QA tenants; found and bumped a stale `DERIVATIONS_VERSION` watermark
  that none of the 3 intermediate passes since the salary cascade's own pilot had bumped despite
  each changing `extract()`'s output (see What changed in code) — the sweep this triggers reaches
  every already-served row, not just keka's own.
- Did not: build the "Rs." currency-indicator gap (real, 16 companies, deliberately deferred — see
  Known gaps) or the doubled-unit LPA shape (real, but below the evidence bar once measured at its
  true incremental yield) — both measured, neither built without evidence-based justification.

## Live-verification review

Two rounds:

1. **Dry-run equivalent**: the scientific-notation fix was verified first against 300 captured
   *rejected* real `Job.salary` field values (not live-refetched, but real prior captures), then
   against a dedicated unit test with 6 cases spanning the exact failure threshold.
2. **Fresh live sample**: 15 boards, seed=99 (different from any prior sample's seed), fetched
   directly against real current `{slug}.keka.com` hosts — `ldipl`, `kapturecrm`,
   `cybernetyxtechnik`, `secpod`, `cognida`, `donatekart`, `entropik`, `bosswallah`, `wbg`,
   `attentiveos`, `epaylater`, `universalaiuniversity`, `ketto`, `phykon`, `teaxpress`. Zero
   fetch/parse errors across all 15 (3 returned 0 jobs — dead/empty boards, not errors). 160 jobs
   seen, 34 field hits (21.2% — within normal sampling variance of the 27.8% full-corpus figure at
   this sample size). Directly inspected several large-value hits (`kapturecrm:146418`:
   1,000,000-1,200,000 INR; `attentiveos:136345`: 4,500,000-5,500,000 INR) and confirmed clean,
   correctly-parsed decimal formatting — the scientific-notation fix confirmed working on live,
   current data, not just the frozen research capture.

## Patterns found

- **The scientific-notation bug dominates Tier 1's own gain** — not a description-mining pattern
  at all, but a scraper-level formatting defect that silently discarded every genuine keka figure
  ≥ ₹1,000,000. Fixing it alone moved field coverage from ~15.8% to 27.8% (+1,549 jobs).
- **The currency-shaped no-signal audit (551 jobs) broke down as**:
  - **141 jobs (25.6%)**: "CAD"/"GBP" acronym noise (Computer-Aided Design, Google Business
    Profile) — no real currency mention at all. Checked directly for false *extraction* risk (not
    just audit noise) — confirmed zero: every real CAD/GBP currency hit in the corpus is Tier 1
    (field-sourced), meaning the Tier-2 cascade never actually matches either acronym as currency
    in real description text.
  - **The large majority of the remaining 396** (a representative 20-example random sample found
    16/20, 80%): correctly-guarded non-salary currency mentions — company revenue ("$1B in annual
    gross bookings"), funding rounds ("$27.5M in funding", "$3.8M seed round"), AUM ("₹2,300
    crores... investment vehicles"), organizational/ad budgets ("scale budgets of ₹50 Lakhs+"),
    market-size stats ("$40 billion international logistics space"), and one literal placeholder
    ("$XM inventory reduction"). All already excluded by the existing revenue/funding guards or by
    simply never being adjacent to a recognized label.
  - **A small number of genuinely unrecoverable mentions**: "₹450 per Class" / "₹250 per class"
    (a real rate, but "per class" isn't an annualizable period this cascade recognizes, and reading
    it as a bare annual figure is implausible and correctly rejected) and "Up to ₹4.8 LPA"
    (ceiling-only, correctly declined per the no-fabrication principle — `SalarySpan.min_annual` is
    required and a ceiling alone can't set it).
  - **Two genuine gaps, both measured at full-corpus scale, both cleared the evidence bar, both
    built**:
    - **"L" (lakh, ×100,000) as a numeric suffix**, alongside the existing "k" (×1,000) — real,
      label-anchored evidence: `"Compensation: ₹30L to ₹50L"` (jupiter), `"Salary : INR 3.0L to
      4.5L"` (mub, ×2), `"CTC - 7L-8L/annum"` (nethority), `"Compensation INR 23-27L + ESOPs"`
      (evolve) — 5 distinct companies. Requires a trailing word boundary (`[lL]\b`, unlike "k",
      which has none) so it can't partially swallow "Lakhs"/"Location"/any other L-word — checked
      directly against real insurance-coverage text ("group insurance of 3 lakhs for family") that
      must stay unextracted, and it does. Deliberately scoped to `_LABELED` only, never added to
      any bare/unguarded pattern: a label is what makes "L" safe the same way it already makes "k"
      safe, and the broader "lacs"/"lakhs" *word* (also measured this pass: 205 occurrences, 39
      companies, but dominated by unrelated insurance-coverage mentions once read directly) only
      stays safe with a label anchor keeping the insurance noise out.
    - **"CTC" (Cost To Company) as a recognized label**, India's standard term for total annual
      compensation — real, 74 occurrences, 33 distinct companies, 13 not already extracted via
      some other label. Checked for the same acronym-collision risk as AED/401(k): real corpus
      text has "CTC" naming an unrelated business unit ("investigate... Freight charges for CTC
      Business units") — confirmed safe directly against that exact text, not just reasoned about,
      because `_LABELED`'s own connector is narrow (`[:\-]?` plus a small lead-in-word set) and
      demands a digit immediately after, so a bare mention with no adjacent figure never reaches
      the number groups.
- **A real 401(k) false-positive regression, found and fixed before merge** — see What changed in
  code for the full mechanism; summarized here as a *pattern*: a benefits-list mention of a
  US retirement plan ("Equity compensation - 401K program") can misread as a $401,000 figure once
  a label's connector is widened to accept a bare hyphen, because "k" (the pre-existing shorthand)
  applies to "401" the same way it would to a real number. Guarded by checking the matched text
  itself for "401k"/"401(k)", since the false positive *is* the matched number, not context around
  it — `_has_false_positive_context`'s context-window check structurally cannot catch this class.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 819 live, post-`EXCLUDED_BOARDS` fix) | 819 (full population) |
| jobs seen | 12,867 |
| jobs with a structured `salary` field (`Job.salary`, post scientific-notation fix) | ~3,700 (coarse count before bounds checking) |
| of those, extracted via Tier 1 | 3,572 (27.8% of all jobs) |
| extracted via Tier 2 (description, no usable field) | 167 (1.3%) |
| **overall Tier1+Tier2 coverage** | **3,739 (29.1%)** |

Mid-pack for this initiative (workable 15.4%, workday 27.6%, greenhouse 36.1%, smartrecruiters
10.0%, zoho 10.0%, teamtailor 14.1%, ashby 49.7%, recruitee 38.2%, personio 10.5%, rippling 46.4%,
lever 41.3%, **keka 29.1%**). Unlike lever (whose own Tier 1 registration predated this pass and
needed no changes), keka's coverage is overwhelmingly a **this-pass result**: Tier 1 alone nearly
doubled (15.8% → 27.8%) purely from a scraper-level bug fix, before any `salary.py` pattern work.

## What changed in code, and why

- **`src/headstart/scrapers/keka.py`**:
  - New `_format_num()` helper: fixed-point formatting (`f"{v:f}".rstrip("0").rstrip(".") or
    "0"`), replacing Python's `:g` format whose scientific notation above ₹1,000,000 neither
    `_RANGE` nor `_num()` can parse. This is the single largest coverage contribution of this pass.
  - `_salary()`'s docstring rewritten to document the confirmed-undecodable `salaryPeriod` finding
    (JS-bundle evidence) and the truthy-vs-`is not None` safety analysis for the `0`-handling.
- **`src/headstart/salary.py`** (all evidence-based, all measured at full-corpus scale before
  building, per this initiative's standing discipline):
  - `_CURRENCY_CODES` gained `AED` (11 companies; real values are monthly-scale so this doesn't
    change which values pass today, but resolves the currency *field* correctly and gives any
    genuinely-annual AED figure — here or on a future ATS — a properly-calibrated bound instead of
    the coarser USD-shaped fallback).
  - `_MIN_PLAUSIBLE_ANNUAL["AED"]` / `_MAX_PLAUSIBLE_ANNUAL["AED"]` added, calibrated to reject
    keka's own observed monthly-scale AED figures as implausible-if-annual (the correct, safe
    outcome given keka's period-omitted payload) while still admitting a genuinely low-but-real
    annual figure.
  - `_LABELED` gained optional **leading**-currency-code support (`AED 30,000-35,000`, not just the
    pre-existing trailing `30,000-35,000 AED`) — general, not AED-specific: `"Salary: USD
    70,000-90,000"` (an already-registered code) failed identically before this fix, confirmed by
    direct testing.
  - `_LABELED`'s label alternation gained **"stipend"** (13 companies — the primary stated pay for
    an internship/trainee role, not a side benefit) and **"ctc"** (33 companies — India's standard
    total-compensation term; checked directly against a real "CTC [as business unit name]"
    collision and confirmed safe).
  - `_LABELED`'s connector gained a bare hyphen (`[:\-]?`, was `:?`) alongside the existing colon.
  - `_LABELED`'s number-suffix groups gained an **"L" (lakh, ×100,000)** alternative alongside the
    existing "k" (×1,000), with a trailing word boundary the "k" alternative doesn't have (checked
    directly against real "lakhs"-as-a-word insurance text to confirm it can't partially swallow
    that word).
  - `_span_from_match` gained an explicit **401(k) guard**: `"401k"`/`"401(k)"` in the matched text
    itself is rejected before the "k"-shorthand multiplier applies, since `_has_false_positive_context`
    structurally cannot catch a false positive that *is* the matched number rather than context
    around it. Found via hand-tracing the mandatory cross-ATS diff — a benefits-list "401K program"
    mention, once the label+hyphen-connector fix let it reach `_LABELED` at all, misread as a
    $401,000 figure.
- **`src/headstart/ingest/doc_prep.py`**: `DERIVATIONS_VERSION` bumped 3 → 4. The comment on this
  counter is explicit — "bump this in the same change that alters what `extract` returns" — and
  none of the three intermediate passes since the salary cascade's own pilot (ashby's truthy-check
  fix, personio's numeric/period-marker fixes, rippling's period marker) had bumped it despite each
  measurably changing `salary.extract()`'s output on their own mandatory cross-ATS diffs. Since
  this is a single monotonic watermark (ADR-0061), there is no way to bump it "for keka's changes
  only" — any bump sweeps in everything since the last one. This bump is a direct, mandated
  consequence of keka's own changes to `extract()`, and its accumulated scope is documented in the
  comment itself rather than silently absorbed. The version-sweep this triggers (`update_meta`,
  ADR-0061) reaches every already-served row whose description the store holds, on the next
  regular pipeline run — no manual trigger needed.
- **`src/headstart/config.py`**: `EXCLUDED_BOARDS` gained `keka:csdemo` (681 postings) and
  `keka:salesdemo` (153 postings) — landed as its own separate PR (#248), not this pass's own
  commits, per the scope-cleanliness convention lever's PR #246 established.

### Cross-ATS impact

**Measured across all 11 previously-merged ATSes, three times** (once per round of `salary.py`
changes this pass) — the mandatory full diff whenever shared extraction code changes.

The **first two rounds** (AED currency, leading-code `_LABELED` fix, stipend label, and the
401(k) guard) surfaced a genuine regression before merge and a broader, valuable side effect:

- **A real regression found and fixed before shipping**: the label+hyphen-connector fix let
  `ashby:hiya`'s real "Equity compensation - 401K program" text reach `_LABELED` for the first
  time, where the pre-existing "k" shorthand misread "401" + "k" as $401,000 — which then
  conflicted with the job's own genuine, separately-stated $146,500–$175,000 range, and
  `_resolve()`'s ambiguity handling correctly declined the whole match rather than guess between
  them (visible as a LOST case, not a wrong-value CHANGED case). The dedicated 401(k) guard fixes
  this: `ashby:hiya` now resolves cleanly to its real range, matching `main`'s own original value
  exactly.
- **A broader, unplanned discovery**: the same guard *also* corrects an identical false positive
  that was **already live on `main`**, independent of this pass's connector-widening — real text
  like workday's `"Industry leading compensation 401K savings w/ 4% company match"` was already
  misreading as $401,000 before this pass touched anything, because `_LABELED`'s connector has
  *always* been optional (zero-width match), not newly widened. Found only because this
  initiative's hand-trace-every-line discipline caught that several LOST/CHANGED examples showed
  `main` itself, not just the working tree, producing the buggy 401000 value. Across the sampled
  corpus: **35 job-level instances fixed on 8 already-merged ATSes** (workday, greenhouse,
  smartrecruiters, zoho, teamtailor, ashby, recruitee, lever) — 28 correctly nulled (no real salary
  existed), 7 real values recovered from behind the bogus match (workday ×3, greenhouse ×3, lever
  ×1). Zero crashes, zero unexplained deltas, across all 11 ATSes in both rounds.
- Every other LOST/CHANGED line across both rounds was hand-traced to one of: a pure currency-field
  resolution (AED added — the large majority), a more-informative match correctly winning over a
  less-informative one, or genuine multi-value ambiguity now correctly surfacing and being safely
  declined (matching the established pattern from personio/rippling's own passes).

The **third round** (the "L" suffix and "ctc" label) was re-verified clean against all 11 ATSes:
zero crashes. Two new deltas beyond the second round, both hand-traced:

- **zoho gained 7 new LOST cases** (`kawenmanpower` ×5, `dudhi` ×2) — not a regression: real text
  states genuinely different figures under different labels in the same posting (`"CTC: ₹23,000
  per month"` vs `"Approx. In-hand Salary: ₹18,000 – ₹19,400 per month"`; `"Total CTC: ₹8,00,000
  per annum"` vs `"Deferred Pay: ₹2,00,000"`) — CTC (gross cost-to-company) and in-hand/net salary
  are a well-known, legitimately different pair of numbers in Indian compensation, not a
  formatting variant of the same figure. Before this pass, only the non-CTC label matched, so one
  of the two real numbers was reported with unearned confidence; now both are recognized as
  candidates and `_resolve()`'s existing ambiguity handling correctly declines rather than picking
  one arbitrarily — the same category this initiative has accepted throughout (lever's/rippling's/
  personio's own passes), confirmed directly by testing each labeled figure in isolation.
- **ashby and lever each gained exactly 1 new correct extraction** (`ashby:credo.ai`: `"expected
  base salary range for this position is ₹40–50L"` → 4,000,000-5,000,000 INR; `lever:jobgether`:
  `"Base compensation of ₹10L–₹18L"`, alongside a separate "Variable compensation" mention →
  1,000,000-1,800,000 INR, `_resolve()`'s existing — unmodified by this pass — consistency logic
  choosing the "Base" figure). A 12-example spot-check of zoho's other new gains (currencies
  spanning INR/CHF/AED/EUR) found every value plausible in magnitude, none nonsensical.

No new false-positive class, no new crash, no unexplained delta — every line in both rounds traces
to a specific, understood mechanism.

## Known gaps, left honestly unresolved rather than guessed at

- **"Rs."/"Rs" as a rupee indicator** — real, evidenced (32 occurrences, 16 distinct companies, 16
  not currently extracted: `"Salary - Rs. 35,000 - 45,000/-"`, `"Stipend: Rs.2500/- per month"`,
  `"Salary (CTC): Rs. 18,000-20,000/- per month"`). Deliberately deferred rather than built this
  pass: unlike "L" and "ctc" (which slotted into `_LABELED`'s *existing* suffix/label alternation
  groups), "Rs" doesn't fit either the single-character `_CURRENCY_SYM` class or the 3-letter
  `_CURRENCY_CODES` alternation without new structural handling, and this pass had already shipped
  six distinct `salary.py` changes. Worth building as a small, focused follow-up — the evidence
  bar is already cleared.
- **The "X lpa to Y lpa" doubled-unit shape** (unit stated after both numbers, joined by "to"
  rather than `_LPA`'s hyphen) — real, structurally distinct from the existing `_LPA` pattern, but
  its true incremental yield (measured, not assumed from the raw occurrence count) is only 3 jobs
  across 2 companies once the 35-of-38-already-covered cases are excluded. Below this initiative's
  multi-company evidence bar.
- **The broader "lacs"/"lakhs" word** (spelled out, not the "L" suffix or the strict "LPA" token) —
  205 occurrences, 39 companies, but a representative sample is dominated by unrelated
  insurance-coverage mentions ("group medical insurance of 3 lakhs for family"). Not built as a
  general pattern; the label-anchored "L" suffix captures the safe, evidenced subset of this
  same underlying convention.
- **The confirmed-undecodable `salaryPeriod` enum** — not a gap this or any future pass can close
  without new information: the label mapping doesn't exist anywhere in keka's own public product,
  confirmed by reading the actual JS the careers page loads. Left correctly omitted, not guessed.

## Carried forward from workable through lever — and new lessons

- **Applied**: the "check for structure one level deeper" question, asked a sixth time (ashby:
  hit, recruitee: confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever:
  confirmed-flat-miss, **keka: confirmed undecodable** — a new third outcome, not a repeat of
  either prior category).
- **Applied**: the mandatory "audit the no-signal bucket" methodology (personio's lesson,
  `docs/salary-extraction/README.md` step 3) — 71.0% no-signal, 6.0% of those currency-shaped,
  each category read and traced to a specific real reason, not just counted.
- **Applied**: vendor demo/QA tenants confirmed only by reading real content, never slug shape
  alone (lever's lesson) — `csdemo`/`salesdemo` fixed as their own separate PR, matching PR #246's
  precedent exactly.
- **Applied**: measuring every candidate pattern at full-corpus scale before building or declining
  it (lever's lesson) — caught the doubled-unit LPA shape's true 3-job yield behind its
  38-occurrence raw count, and confirmed the "L" suffix and "ctc" label both genuinely clear the
  bar rather than assuming from a smaller sample.
- **New**: a false-positive-guard acronym check (established for AED vs. "Automated External
  Defibrillator", 401(k) vs. the retirement-plan name) generalizes to *any* short, common business
  acronym before adding it as a recognized currency code or label — checked here for CAD (Computer-
  Aided Design), GBP (Google Business Profile), and CTC (a business-unit name) — and the check that
  actually matters is not "does this string collide with something else" (it always will) but
  "does the collision reach a real *extraction*, not just noise in an audit regex" — confirmed by
  checking the source of every current currency hit (CAD/GBP: 100% Tier 1, meaning Tier 2 never
  matches the acronym) and by testing the exact colliding real text directly (CTC) rather than
  reasoning about it.
- **New**: a numeric shorthand (like "k") is only as safe as the context that gates it — "L" needed
  both a trailing word boundary (unlike "k", which has never needed one) *and* `_LABELED`-only
  scoping to stay safe, because the broader unguarded word it's short for ("lakhs") collides
  heavily with a real false-positive class (insurance-coverage amounts) that a label anchor keeps
  out but an unguarded bare pattern would not.
- **New**: a single monotonic version watermark (`DERIVATIONS_VERSION`, ADR-0061) can silently
  stop being bumped across several passes without any test catching it, because nothing asserts it
  advances on every `salary.py`/`experience.py` change — only the comment above it says to. Worth
  a routine check on every future pass that touches either extractor: has this actually moved
  since the last bump, given what changed this time.
- **New**: a shared regex fix motivated by one narrow, newly-introduced case can also repair a
  broader, unrelated, already-shipped defect — found only by hand-tracing which side of a
  before/after diff pair (`main`'s frozen value vs. the working tree's) was actually the buggy one,
  not just counting how many lines changed. A LOST/CHANGED line where `main` itself already shows
  the bug is evidence the defect predates the current pass entirely.
