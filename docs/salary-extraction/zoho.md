# Zoho Recruit — salary extraction findings

Fifth ATS in the salary-extraction initiative. See `README.md` for the overall process and the
four prior passes: `workable.md` (pilot), `workday.md` (first detail-pass ATS, two code-review
rounds), `greenhouse.md` (highest coverage, two general `salary.py` bugs found), `smartrecruiters.md`
(a company-configurable custom field, plus a genuinely pre-existing period-hint bug).

**This pass needed three rounds of correction before its own numbers were right** — the first
draft of this document was wrong on two separate points, both caught by code review before merge,
not after. That history is kept in the sections below rather than smoothed over, because the
lessons are as valuable as the final numbers: check which page a field actually lives on before
calling it a dead end, and check what a bare connector word semantically means (floor vs. ceiling)
before trusting a schema's default assignment.

## Methods tried

- **Sampled 3,000 of 5,337 live boards** (freshly measured 2026-08-22). `has_detail_pass = True`,
  with a shape different from workday/smartrecruiters: `fetch_raw()` only detail-fetches jobs
  *missing* `Job_Description` from the initial listing payload — most tenants already have it
  inline (43/71 in the scraper's own docstring sample), the rest need a per-job detail pass. Built
  `_fetch_zoho` in `salary_sample.py` to mirror this: capped detail-fetches, uncapped total job
  count for tenants whose descriptions are already free in the listing.
- **First code-review round (Standards axis) found a real measurement bug in that adapter**: it
  capped which records got a detail-fetch *attempt*, but then called `parse()` against the full,
  uncapped page — so records past the cap that lacked an inline description came back as Jobs with
  an *empty* description, silently diluting the coverage denominator with jobs nobody ever
  actually read. Verified directly against `zoho.py`'s `parse()` and the captured artifacts: 21,358
  of 58,004 jobs (36.8%) were these phantom empty-description records, across 1,336 boards with
  more than 3 postings. **Fixed**: `_fetch_zoho` now selects detail-fetch candidates from records
  genuinely missing a description (not just "first 3 regardless"), and drops any record that still
  has no description at all — inline or fetched — before returning, exactly matching the principle
  `_DETAIL_FETCH_CAP`'s own docstring states ("an undetailed posting has no description to mine").
  Re-sampling after the fix: 36,624 jobs (down from the phantom-inflated 58,004), coarse hint rate
  up from 11.1% to 18.1% — the same real signal, over a denominator that no longer includes jobs
  that were never given a chance.
- **While independently re-verifying that fix, found the Tier-1 "confirmed dead end" claim in this
  document's own first draft was itself wrong.** The original check queried zoho's *listing* page
  records for a `Salary` field and found none — true, but the wrong page: the listing genuinely
  carries no such key (`['City', 'Country', 'Industry', 'Is_Locked', 'Job_Opening_Name',
  'Job_Type', ..., 'id']`), but a job's *detail* page does (`[..., 'Salary', 'State',
  'Work_Experience', 'Zip_Code', ...]`), and it's a real, sometimes-populated field. A fresh
  150-tenant probe fetching detail pages directly (independent of production scrape scope)
  measured **48.6% of jobs whose detail page was fetched had `Salary` and/or `Currency`
  populated** — roughly equally whether or not the listing already carried `Job_Description`
  inline (51.5% vs. 46.2%). **Fixed**: `zoho.py`'s `_description_of` now appends `"Salary: X
  Currency: Y"` onto the description text whenever a detail page is fetched — free-text,
  per-tenant strings (`"5-10 Lakhs"`, `"DOE"`, `"$35.00 per hour"`), not a clean structured field,
  so they ride along for Tier-2 mining rather than getting a bespoke Tier-1 parser, mirroring
  smartrecruiters' `customField` treatment exactly. Verified against 17 real sampled values: 12
  extract cleanly via the *existing* Tier-2 cascade with zero new patterns needed, and the 5
  non-extractions are all principled (ambiguous currency, "DOE"/"Base + commission" aren't
  numbers, `R$` isn't a recognized currency symbol) — not bugs. Zero production request-volume
  cost: this rides on detail fetches the scraper already makes for description-backfill reasons.
- **Deliberately NOT built, and not going to be built without sign-off**: expanding
  `fetch_raw()`'s detail-fetch trigger to *also* fetch every job's detail page even when
  `Job_Description` is already inline (to reach the other ~46% of the population-rate signal
  currently invisible to production scraping, since the rate was roughly equal in both groups).
  This would substantially increase production request volume — roughly doubling it for the ~61%
  of tenants that currently make zero detail requests, recurring every pipeline run, against live
  third-party career-site infrastructure — squarely the kind of architectural cost/benefit
  tradeoff CLAUDE.md's "Weigh Design Choices on Big Work" rule asks to be presented, not silently
  decided. Left for the founder to weigh with the real numbers above.
- **A third, independent, more serious bug found while re-verifying the second fix's cross-ATS
  diff**: `"up to $X"` (a stated *ceiling*) was being extracted as `min_annual=X` — the semantic
  opposite of what it means, since every connector word in `_LABELED`'s bare-single-value branch
  ("starting at", "from", "is", "of", *and* "up to") was uniformly assigned to `min_annual`
  regardless of which direction it actually points. `SalarySpan.min_annual` is a required `int`
  with no way to represent "ceiling known, floor unknown", so this wasn't a narrow miss — it was
  silently inverting a real, correctly-read number. Found from a real zoho example ("Salary: Up to
  ₹28 LPA" → `min_annual=2,800,000`), but the bug lives in shared `salary.py` code and was already
  shipping in all four merged ATSes. Precisely measured (an "up to"/"upto" immediately adjacent to
  the matched number, not merely present anywhere in the text — an earlier, looser measurement
  attempt was thrown out for false-positiving on unrelated idioms like "up to 55 lbs" and "up to
  10 hours weekly"): workable 63, workday 47, greenhouse 777, smartrecruiters 40, zoho 712 real
  cases across `_LABELED`, `_BARE_HOURLY_OR_DAILY`, and `_LPA` combined. **Fixed**: a bare single
  value (no captured range) preceded by "up to"/"upto" now correctly declines rather than
  misassigns, via a shared `_states_a_ceiling_only()` check wired into `_span_from_match` (covers
  `_LABELED` and `_BARE_HOURLY_OR_DAILY`) and `_scan_lpa`. An actual stated range ("up to
  $50,000-$60,000") is unaffected — both bounds are already known regardless of the connector.
- **Verified the fix's real impact with the mandatory full cross-ATS diff** (main's frozen
  `salary.py` vs. the fixed working tree, across all 5 ATSes' frozen corpora) — and caught a
  second bug along the way, this time in the diff script itself: comparing `SalarySpan` instances
  loaded from two independently-`exec`'d module namespaces via `==` always returns `False`
  regardless of field values, since dataclass's generated `__eq__` checks `other.__class__ is
  self.__class__` and the two modules define distinct class objects. Confirmed directly
  (`repr(o) == repr(n)` was `True`, `o == n` was `False`). The first diff run's `lost`/`gained`
  counts were unaffected (those only ever check `is None`), but its `both_same`/`value_changed`
  split was completely wrong — every genuinely-unchanged match was misclassified as changed.
  Fixed by comparing `repr()` instead. The corrected numbers: see Coverage and What changed below.
  Read a sample of the real `value_changed` cases by hand rather than trusting the count alone
  (ADR-0066 discipline) — every one checked was a genuine improvement: the wrongly-parsed ceiling
  had been *shadowing* a real, fuller range or figure stated elsewhere in the same description,
  because `from_description`'s cascade returns as soon as one tier's `_resolve()` succeeds — e.g.
  a real Expedia posting states "the total cash range... is $146,000.00 to $204,500.00" *and*
  "pay up to $233,500.00" (a conditional ceiling); before the fix, `_LABELED` matched only the
  wrong "$233,500" and returned immediately, never reaching `_BARE_RANGE`'s correct match on the
  real range; after the fix, the ceiling match declines, the tier finds nothing, and the cascade
  correctly falls through to recover the real $146K–$204.5K range. Same story for a real
  mascmedical posting (a $50,000 *bonus* ceiling was shadowing the real "$80K–$105K base salary"
  stated a few words earlier) and a christiansonco posting (a $65/hr *commission* ceiling was
  shadowing the real "Hourly Rate: $22–$45/hour"). One case was murkier: with the wrong "up to
  $17.25" ceiling removed, a Dutch Bros posting's fallback tier picked up "$7.25 per hour" instead
  — which turned out to be the *average tip* component, not the wage, from "Compensation: Up to
  $17.25 per hour. Number includes an average tip of $7.25 per hour." Measured prevalence of this
  specific shape (a bare hourly figure extracted near the word "tip") across the full 5-ATS corpus
  before deciding whether to guard it: 43 cases total, but 42 of those are jobs where the extracted
  figure is the genuine base wage and "tips" is just a mentioned benefit nearby ("Pay: $16 per
  hour Supplemental pay types: Commission, Tips") — only the one Dutch Bros case is a real
  mismatch, where the number itself is introduced by "tip of" rather than being the wage. Left
  unguarded — a single real occurrence across five ATSes' combined corpora doesn't meet this
  initiative's established yield bar for a dedicated pattern.
- **One gap-analysis round** (before the above three fixes were found), reading real misses at the
  coarse-hint rate (11.1% at the time, the lowest seen so far). Found five real, distinct,
  evidence-measured gaps: British informal "p/h" hourly shorthand (69 occurrences), a "paying"
  verb-conjugation variant of the "pay" label (210 occurrences, 175 missed), "a year" as a bare
  period marker (50 occurrences), and two compound connector phrases ("of up to", "is up to",
  ~13–16 occurrences each). All four fixed; see What changed in code.
- **A guard candidate checked and deliberately not added**: space-separated "sign on bonus" (150
  occurrences, unlike the already-guarded hyphenated "sign-on bonus"). Verified zero genuine false
  positives across the full corpus — every nearby extracted figure traced to a real, separately-
  stated base salary, never the bonus amount. Not fixed, same evidence-based reasoning as
  greenhouse's declined "equity" guard.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 3,000 (5,337 live, above the cap).
- Measured both required percentages: **yes** — 0.0% structured-field (the field exists, is real,
  but is folded into Tier 2 by design rather than surfaced as a separate Tier-1 percentage — see
  Coverage), 9.2% overall description-mining coverage (3,358/36,624), 18.1% coarse-hint rate
  reported alongside for calibration.
- Live-verified after code changes: **yes, three times** — once after the phantom-job fix, once
  more (implicitly, via direct value testing) after the Salary/Currency wiring, and a final fresh,
  differently-seeded round (40 boards, seed=2026) after every fix including the ceiling-vs-floor
  correction. Two earlier live-verification rounds (seed=619, then an intermediate one) were
  invalidated by subsequent fixes and are not reported below — only the final one is, since citing
  a superseded number would misrepresent the shipped code's actual behavior.
- Went beyond the ask, and by a wide margin this pass: independently re-verified a claim this same
  document had already made (the Tier-1 dead end) rather than accepting it at face value, and
  caught it was wrong. Found and fixed a bug in shared code that had already shipped in four
  merged ATSes, not just an issue local to zoho. Found and fixed a bug in the diff script used to
  verify the fix. Measured a guard candidate exposed by the fix (the "tip" shape) before declining
  it rather than either reflexively building or reflexively ignoring it.
- Did not: build the expensive detail-fetch-every-job option (Option B) that would roughly double
  zoho's production request volume for a large additional coverage gain — flagged prominently
  instead of decided unilaterally, per CLAUDE.md's big-design-choice rule. Did not build a guard
  for the single-occurrence "tip" mismatch shape. Did not chase the parenthesis-before-amount or
  dash-before-connector punctuation gaps found during the original gap-analysis round (8 and 10
  occurrences respectively) — below this initiative's established per-pattern yield bar.

## Live-verification review

The authoritative round (fresh, 40 boards, seed=2026, `--workers 20`), run against the fully-fixed
code (phantom-job fix + Salary/Currency wiring + ceiling-vs-floor fix, all together) — two earlier
rounds against intermediate, since-superseded code are not reported, per the self-assessment above.

40/40 boards succeeded, 0 errors, 465 jobs, 44 real Tier1+Tier2 extractions (9.5% — consistent with
the 3,000-board sample's 9.2%, a good stability check). The sample happened to draw
`bladerecruitment.zohorecruit.com`, a single high-volume UK trades/cleaning recruitment agency
that states a clear salary on nearly every posting, contributing 39 of the 44 extractions — real
board-mix variance in a 40-board draw, not a bug (the coarse hint rate for this specific sample,
35.1%, is correspondingly higher than the 3,000-board average of 18.1% for the same reason). All
44 extractions spot-checked for plausibility: the bladerecruitment figures are sensible UK
trade-wage ranges (£18,720–£50,000, cleaners/electricians/painters/handymen), plus a Spanish-
language Canadian farm-technician posting correctly resolving to CAD, a Social Media Assistant at
$31,200–$41,600 USD, two allied-health assistant roles at $55,000–$72,000 (currency unresolved,
correctly, since neither symbol nor code was present), and a Performance Marketing Manager at
$96,000–$120,000 USD — all genuine.

## Patterns found

- **A genuine `Salary`+`Currency` field that lives only on the per-job detail page** — the
  strongest Tier-1-shaped signal found in any ATS pass so far (48.6% population rate among jobs
  whose detail page is fetched), folded into Tier 2 via description concatenation rather than a
  bespoke parser, since the raw values are free text ("5-10 Lakhs", "DOE", "$35.00 per hour"), not
  a clean structured shape.
- **"up to $X" as a ceiling, not a floor** — a real, common, and previously mis-handled connector
  shape across every ATS sampled so far, not specific to zoho. See Methods tried and What changed.
- **"p/h" as British informal hourly shorthand**, almost always glued directly onto the number
  with no separator (`"£21.50p/h"`).
- **Verb-conjugated label words** (`"paying"` for `"pay"`).
- **Compound connector phrases** (`"of up to"`, `"is up to"`) not recognized as two-word
  sequences even though each word was recognized alone.
- **Regional job-board duplication**: one UK company (`towertrophies`) posts the same role
  separately per region (6 near-identical postings sampled), each stating `"£5,000 - £20,000 a
  year COMMISSION ONLY"` — correctly declined by the plausibility floor (£5,000 fails GBP's £8,000
  minimum), a genuine commission-heavy role, not a bug.

## Coverage

**Corrected 2026-08-22 (PR #242) — see "Post-merge correction" below.** The table reflects the
current, post-correction numbers; the narrative below it (Append/pure-prose split onward) is kept
as the honest historical record of this pass's own original findings and is not rewritten in
place, per this doc's own established practice of documenting a correction rather than erasing it.

| metric | value |
|---|---:|
| boards sampled (of 5,337 live) | 3,000 |
| jobs seen (uncapped detail-fetch; up from 36,624 pre-correction, +25.2%) | 45,855 |
| jobs with a structured `salary` field (`Job.salary`) | 0 (0.0%) — by design; see below |
| **overall Tier1+Tier2 coverage** | **4,596 (10.0%)** |
| coarse hint rate (calibration only) | 19.3% |
| boards with ≥1 job showing a real signal | 515/3,000 (17.2%) |

The boards-with-signal denominator is now the full 3,000 (0 errored boards this run); the original
609/2,865 figure excluded some boards from its own denominator for reasons this correction didn't
re-derive, so the two ratios aren't a clean like-for-like comparison — reported as measured rather
than reconciled further.

**Append/pure-prose split** — measured via a separate, instrumented re-fetch of the same seed=7
board draw (36,610 jobs, 14 fewer than the primary sample above — natural variance from re-fetching
live boards at a different moment, not a mechanism difference; extracted count agrees to 3,361 vs.
3,358, both round to 9.2%): 1,466 jobs (4.0%) got a genuine detail-page append, of which 496
(33.8%) successfully extracted; the remaining 2,865 extractions came from pure prose, including
organic "Salary:"-labeled text that was already part of some descriptions before any append.

`Job.salary` stays at 0% deliberately: the detail-page `Salary`/`Currency` field is real (see
Patterns found) but is appended to `description` for Tier-2 mining rather than parsed as a
structured Tier-1 field, matching smartrecruiters' `customField` precedent exactly — so its
contribution shows up in the Tier-2 numbers above, not as a separate Tier-1 percentage.

**The append/pure-prose breakdown above needed its own correction, caught by the Spec-axis
re-review.** The first version of this table (2,824 appended / 1,219 extracted from that subset)
was computed by searching the *final*, already-concatenated description text for `"Salary:"`/
`"Currency:"` substrings — which also matches organic, pre-existing mentions of those exact words
inside a real `Job_Description` (confirmed: one real posting genuinely contains "Salary: $15k -
$25k per month..." as part of its own prose, unrelated to the appended field). A first fix
attempt anchored the regex at the string's end, which still failed for a related reason: an
unbounded lazy quantifier before the anchor was forced to swallow all the *unrelated* prose after
an organic "Salary:" mention just to reach the true end of the string, misclassifying it as an
append. Two regex attempts against the same opaque, already-transformed string both failed for
different reasons — the reliable fix was to stop trying to reconstruct the answer after the fact
and instead tag each job *at the moment of concatenation*, inside the fetch logic itself, where
whether `Salary`/`Currency` was genuinely present is unambiguous. The numbers above are from that
instrumented re-fetch. Lesson for future ATSes: if a coverage breakdown attributes extractions to
a specific mechanism (a custom field, an appended signal), track it at the source during the
fetch/measurement pass — never reconstruct it after the fact from a string that no longer
distinguishes where the mechanism's output ends and organic content begins.

Coverage moved twice during this pass, in opposite directions, before landing at its then-final
value: the phantom-job fix alone would have raised the raw percentage (a smaller, more honest
denominator); the Salary/Currency wiring added real signal on top of that; the ceiling-vs-floor
fix then *removed* net signal (more wrongly-extracted ceilings correctly declined than the
now-unblocked genuine figures it recovered). **The 9.2% figure below was this pass's own final
number, and is now superseded — see "Post-merge correction" for the current 10.0%.** At 9.2% it
was the lowest of the five ATSes done at the time (workable 15.4%, workday 27.6%, greenhouse
36.1%, smartrecruiters 10.0%, zoho 9.2%) — a genuinely different, more international/agency-heavy
company mix (confirmed reading real misses: Italian, Dutch, and French postings; UK recruitment
agencies; boilerplate "competitive salary" text with no stated figure), not a weaker extraction
pass; if anything, this pass's
extraction logic is now measurably *more* correct than every other ATS's, since the ceiling fix
benefits all of them once merged.

## What changed in code, and why

- `scripts/enrich/salary_sample.py`:
  - New `_fetch_zoho()` bounded sampling adapter (registered in `_DETAIL_ADAPTERS`).
  - **Fixed** (code-review finding): candidate selection for detail-fetching now comes from
    records genuinely missing a description (`missing_desc`), not "first `_DETAIL_FETCH_CAP`
    eligible records regardless" — and the returned job list is now filtered to drop any record
    that ended up with no description at all (inline or fetched), matching every other detail-pass
    adapter's implicit guarantee that a returned job was actually given a chance to show a signal.
- `src/headstart/scrapers/zoho.py`:
  - **`_description_of`**: now appends `"Salary: X Currency: Y"` onto the description whenever a
    detail page is fetched and either field is populated — zero new requests, since this rides on
    detail fetches already made for description-backfill reasons. `report_detail_gaps`'s "missing"
    count only checks for `None` (confirmed by reading its source before making this change), so a
    job that yields compensation info but no prose description is correctly no longer counted as a
    gap — a small, deliberate, correct shift in what that specific metric means.
- `src/headstart/salary.py` (shared code — affects every ATS, not just zoho):
  - **`_PERIOD_HINT`**: added `p\s*/\s*h\b` (no leading `\b`, since "21.50p/h" has no word/non-word
    transition to anchor on) and `\ba\s+year\b`; downstream multiplier logic updated to recognize
    "p/h" as hourly.
  - **`_LABELED`**: `pay` → `pay(?:ing)?`; added `of\s+up\s+to`/`is\s+up\s+to` connector
    alternatives before the bare `of`/`is` alternatives so the longer match wins.
  - **`_states_a_ceiling_only()` + `_UP_TO_CONNECTOR`** (new, moved up next to `_num()` so both
    tiers can share it): a bare single value (no captured range) immediately preceded by "up to"
    (any internal whitespace, including none) now declines rather than being misassigned to
    `min_annual`. Wired into Tier 2's `_span_from_match` (covers `_LABELED` and
    `_BARE_HOURLY_OR_DAILY`, every pattern routed through `_scan`) and `_scan_lpa`.
    `_LEVEL_BAND`/`_scan_level_bands` always captures both bounds by construction, so it's
    structurally unaffected. **Also wired into Tier 1's `_field_generic`** (code-review finding,
    second round): this fallback parser has the identical bare-single-value shape, and it's
    live-reachable, not theoretical — ashby and personio pass an HR system's raw free-text field
    straight into `Job.salary` with no scraper-side normalization (confirmed by reading both
    scrapers directly), unlike lever/recruitee/teamtailor/keka/darwinbox's calibrated `_field_*`
    parsers for shapes this codebase itself formats. The module's own Tier-1 section comment,
    which claimed every Tier-1 input was "our own output shape, not organic free text", was wrong
    for exactly this reason and has been corrected. The whitespace tolerance was also tightened in
    the same pass: the original `up\s?to` (0–1 space) missed a double-space "up  to", which
    `_LABELED`'s own connector (`up\s+to`) would still match — simplified to `up\s*to` (0 or more),
    which also makes the separate "upto" literal redundant.
- `tests/test_scrapers.py`: 1 parametrized test function (7 cases, 2 added in the second review
  round) — `test_zoho_detail_description_appends_salary_and_currency` — covering description+both
  fields, description+salary-only, salary-only+no-description, description+currency-only,
  currency-only+no-description, description-only+no-fields, and the
  fully-empty regression case.
- `tests/test_salary.py`: 74 tests total (up from 65 at smartrecruiters' merge). Net across this
  whole pass: the original 7 zoho-specific pattern tests (p/h ×2, paying, a year, of-up-to,
  is-up-to, the ambiguity-exposure case), plus 2 new tests for the ceiling-vs-floor fix
  (`test_description_up_to_states_a_ceiling_not_a_floor` for Tier 2,
  `test_field_generic_up_to_states_a_ceiling_not_a_floor` for Tier 1, the latter added in the
  second review round once the Tier-1 gap was found), with 6 of the original 7 subsequently
  *revised* rather than left stale: `test_description_labeled_single_gbp` (the module's own
  pilot-era "up to" example, now correctly asserting `None`), the two p/h tests and the paying
  test (connector swapped from "up to" to a neutral word so they keep isolating the mechanism they
  were built to cover, rather than being silently masked by the later, unrelated ceiling fix), the
  two compound-connector tests (reframed from "prove the connector is recognized via successful
  extraction" to "prove the ceiling-guard applies to the two-word connector form too", plus a new
  assertion using an actual range to keep connector-recognition itself provable), and the
  ambiguity-exposure test (renamed `test_description_up_to_no_longer_creates_false_ambiguity`,
  now asserting the correctly-recovered $84,000 base salary instead of `None`).

### Cross-ATS impact of the shared `salary.py` fixes

Verified via a full per-job diff against every ATS's frozen corpus (main's `salary.py` vs. the
final, fully-fixed working tree) — not just the aggregate percentage, and not just the p/h/paying/
a-year/connector fixes but the ceiling-vs-floor fix too, since all of it lives in shared code:

| ATS | both_none | both_same | lost | gained | value_changed |
|---|---:|---:|---:|---:|---:|
| workable | 4,294 | 816 | 57 | 0 | 0 |
| workday | 5,836 | 2,428 | 19 | 1 | 2 |
| greenhouse | 51,180 | 29,588 | 303 | 15 | 19 |
| smartrecruiters | 6,727 | 715 | 19 | 5 | 0 |
| zoho | 32,914 | 3,330 | 352 | 23 | 5 |

`lost` = a real "up to $X" case that used to wrongly extract the ceiling as a floor and now
correctly declines. `gained` = a case where declining the wrong ceiling match let a genuine,
previously-masked figure extract instead (the `_resolve()`/ambiguity mechanism, or cascade
short-circuiting, hiding a real number behind a wrong one). `value_changed` — every example
checked by hand across all five ATSes (see Methods tried) was the same story as `gained`: a real,
fuller, more correct figure recovered from later in the same description, with exactly one
narrower exception (a tip amount briefly surfacing in place of a declined wage ceiling — measured,
found rare, left unguarded). This fix will correct the four already-merged ATSes' data too, once
this PR merges and the pipeline's derived-field refresh next runs — no need to reopen those PRs.

## Post-merge correction (PR #242, 2026-08-22): the cap itself, not just its phantom-job symptom

This pass's own `keep_ids` fix (above) was a real, correct fix for a real bug — it stopped a
record that was never given a chance to show a signal from being silently scored as "no signal."
But it fixed the SYMPTOM (phantom jobs polluting the denominator) without touching the CAUSE (the
adapter still only ever detail-fetched the first `_DETAIL_FETCH_CAP` (3) records missing a
description, no matter how many a board actually had). `keep_ids` correctly excluded the rest from
the denominator instead of mis-scoring them — but excluding a real, uncheckable record from the
count is not the same as reading it. A board with 62 real missing-description postings still only
ever had 3 of them genuinely read; the other 59 were invisible to every coverage measurement this
pass reported, cap-and-all — real signal permanently out of reach of the 3,000-board sample no
matter how many times it was re-run, not a rare failure mode.

**Fixed**: `_fetch_zoho` no longer caps detail fetches at all. It now calls the scraper's own
`fetch_raw()` directly — zoho's listing never paginates (unlike workday/smartrecruiters, where
`fetch_raw()` is genuinely expensive to call from a sampling script), so this costs nothing beyond
the detail fetches it already needed, and reuses the scraper's real concurrent fan-out
(`fan_out`/`fan_out_async`, spare-egress-aware) instead of a serial 3-request loop. The `keep_ids`
post-filter stays — it still correctly excludes a record whose detail fetch was attempted and
genuinely failed (network error, 404), which `parse()` doesn't drop on its own — but it now runs
over the complete set of attempts, not one truncated at 3.

**Independently re-measured, not assumed**: a fresh full 3,000-board sample, same seed (7) as the
original, 0 errors. Jobs seen: 36,624 → 45,855 (+9,231, +25.2% — real postings that were
previously invisible to the sample, not new boards or a different draw). Overall Tier1+Tier2
coverage: 9.2% (3,358) → **10.0% (4,596)**. The percentage-point move is modest relative to the
denominator's growth because most of the newly-visible jobs came from boards this pass's own
company-mix finding already described (international/agency-heavy postings, several non-English)
— reading the newly-recovered descriptions directly confirms the same pattern the original pass
found in its smaller sample, just at greater scale, not a different population. `tsaaro.
zohorecruit.in` (62/62 eligible postings missing an inline description, all previously capped to 3)
is a representative single-board example: all 62 now genuinely read in 2.9 seconds via the
concurrent fetch, none of them stating a salary — a real board this initiative's own numbers were
blind to before, not diluting the corrected total, just no longer silently excluded from it.

This correction does not touch `src/headstart/salary.py` — no cross-ATS diff applies, no shared
extraction code changed. It does add a documentation-only comment to `src/headstart/scrapers/
zoho.py` (see the next subsection); no `verify-search-filters` re-run applies since `url()` and
every other function's behavior is byte-for-byte unchanged. The comparison tables in `teamtailor.
md`, `ashby.md`, and `recruitee.md` still cite zoho's pre-correction 9.2% figure as of their own
respective merge dates — deliberately not rewritten here, consistent with treating each pass's doc
as a point-in-time snapshot rather than a living document; this section is the correction's
permanent record. Zoho's corrected 10.0% is now roughly tied with smartrecruiters' 10.0% rather
than strictly the lowest of the passes done so far.

### A second, larger finding from the same investigation: a hard ~750-job listing ceiling

Removing the detail-fetch cap prompted a direct check of an assumption this whole correction had
been resting on — that "zoho's listing never paginates" — rather than continuing to assume it.
**It was wrong in the way that matters for large boards, though right in the narrower sense the
original claim needed**: a single `_get()` genuinely is the whole request (no follow-up call is
needed to walk pages), but the *response itself* is not guaranteed to contain a board's true full
population. Direct investigation (hitting the real API, not reasoning about it, per this repo's
own standing rule) found a hard ceiling: **the public, unauthenticated career-site widget embeds
at most ~750 jobs into that one response.**

Evidence, not assertion:
- **3 independent tenants** in this pass's 3,000-board sample (`maydaydentalstaffing`, `kgoci`,
  `harrisonconsultingsolutions`) each landed on **exactly 750** — the sample-wide maximum job
  count across all 3,000 boards; nothing observed exceeds it.
- **URL query-string variants never changed the response**: `?page=2`, `?start=751`,
  `?fromIndex=751`, `?offset=750`, `?pageIndex=2`, all tested directly against a 750-job board —
  identical 750 records, identical first record ID, every time.
- **The front-end JS makes no follow-up request.** `career-website-common.js`'s
  `initializeJobList()` reads `jobs=JSON.parse($L("#jobs").val())` — exclusively from the same
  server-embedded blob this scraper already parses — and `renderJobListing()` only groups/sorts/
  facets that in-memory array client-side (for display: grouping by job type, layout options).
  No `fetch`/`XMLHttpRequest`/`.ajax()` call anywhere in that file loads additional records.
- **No field anywhere reveals a true total.** Checked every hidden-input config blob on the page
  (`#jobs`, `#meta`, `#pageJson`, `#moduleMeta`) for a count/total field distinct from the
  embedded array's own length — none exists. A board with exactly 750 real openings and one with
  5,000 (750 shown) are indistinguishable from the outside.
- **Real pagination exists, but not here.** Zoho Recruit's own public documentation confirms
  `fromIndex`/`toIndex` pagination on the authenticated private API's `getRecords` method
  (`recruit.zoho.com/recruit/private/xml/...` or the newer `/recruit/v2/...`) — but that requires
  a per-tenant OAuth token, which this scraper has no path to obtaining for the thousands of
  unaffiliated companies it reads without any relationship to them.

**Scale of impact, honestly bounded, not exaggerated**: only 3/3,000 sampled boards (0.1%) hit the
ceiling exactly. This is not a general-population problem — it affects a small minority of very
high-volume boards (the three identified are staffing/recruiting agencies, consistent with this
pass's own earlier finding that zoho's population skews international/agency-heavy). But for an
affected board, an *unknown* number of real jobs beyond 750 are silently invisible — not just to
this sampling script, but to `zoho.py`'s own production `fetch_raw()`, which makes the identical
single request. There is no way, from outside Zoho's authenticated API, to even measure how many
real jobs are being missed on any given board — the ceiling is silent, with no truncation signal.

**Not fixed here, and not decided unilaterally**: closing this gap would need either (a) a
headless browser driving the widget's real UI (a fundamentally different scraping architecture
from every other scraper in this codebase, all of which are lightweight HTTP/`curl_cffi`-based),
or (b) per-tenant authenticated API access (impractical at this scale — thousands of unaffiliated
companies, no existing relationship with any of them to request OAuth grants from). Both are real,
substantial architectural decisions squarely inside CLAUDE.md's "Weigh Design Choices on Big Work"
rule — flagged here with the evidence behind it, not built without sign-off. `src/headstart/
scrapers/zoho.py`'s own module docstring now documents this limit directly, so a future reader
investigating a large zoho board's missing postings finds the explanation immediately rather than
re-discovering it.

## Known gaps, left honestly unresolved rather than guessed at

- **Non-English postings** (confirmed: Italian, Dutch, French) — out of scope per this repo's
  English-only search-index policy, consistent with every prior pass's finding of the same.
- **A bare hourly figure occasionally extracted from a "tip" sub-clause instead of the wage** — one
  confirmed real occurrence across the full 5-ATS corpus (see Methods tried); below this
  initiative's yield bar for a dedicated guard.
- **Parenthesis or em-dash between a label and its connector**, and **expanding the detail-fetch
  pass to every job** (Option B, see Methods tried) — both real, both measured, both deliberately
  left for a human call rather than decided silently.
- **A hard ~750-job ceiling on the public career-site widget's single listing response** (see
  "Post-merge correction" above for the full investigation) — affects 3/3,000 sampled boards
  (0.1%), all high-volume staffing agencies, silently and unmeasurably beyond the ceiling. No fix
  available inside this scraper's current unauthenticated-HTTP architecture; closing it would need
  a headless browser or per-tenant authenticated API access, both real architectural decisions
  flagged for a human call rather than built unilaterally.

## Carried forward from workable, workday, greenhouse, and smartrecruiters — and new lessons for future ATSes

- **Applied**: check for a dedicated or custom-configurable salary field via direct API inspection
  before assuming Tier 1 is a dead end. Applied twice, in fact — the first check (against the
  listing page) genuinely found nothing, and it took a second, independent look (prompted by an
  unrelated code-review finding, not a fresh instruction) to discover the check itself had queried
  the wrong page shape.
- **Applied**: verify a guard candidate against real data before adding it, even when a sibling
  pattern already needed the same class of fix. Applied to both the "sign on bonus" and "tip"
  candidates this pass.
- **New, the headline lesson of this whole pass**: a detail-pass ATS can have real fields that live
  *only* on the per-job detail page, invisible to any check run against the listing alone. Before
  writing "confirmed dead end" for any structured field, confirm which page/response shape was
  actually probed, not just that the field was probed.
- **New**: before trusting that a bare single-value connector word (any of "starting at", "from",
  "is", "up to", "at least", "no more than", ...) can be uniformly assigned to a schema's one
  numeric slot, check what each one *means* — some state a floor, some a ceiling, and a schema
  with only a `min`-shaped required field has no safe way to represent the latter except declining
  to guess. This exact confusion had already shipped, undetected, in four merged ATSes.
- **New**: one code-review finding is worth re-reading the surrounding code for siblings, not just
  fixing the one flagged issue — this entire chain (three real bugs, two of them far larger than
  the one actually flagged) started from a single Standards-review comment about phantom jobs in a
  sampling script. Confirmed a second time within this same pass: fixing Tier 2's ceiling-vs-floor
  bug should have prompted a check for the identical bare-single-value shape in Tier 1 immediately
  — it wasn't done proactively, and a second review round caught the sibling (`_field_generic`,
  live-reachable via ashby/personio) instead. Re-reading for siblings is a step to take *before*
  sending a fix for review, not just a thing reviewers happen to catch.
- **New**: when diffing dataclass instances loaded from two independently-`exec`'d or -imported
  module namespaces (the established old-vs-new verification pattern this initiative uses), compare
  via `repr()` or a field tuple, never bare `==` — the generated `__eq__` checks class identity
  first, and two structurally-identical instances from two different module executions will never
  be `==`, silently corrupting a diff's "unchanged" bucket into "changed" with no exception raised.
- **New**: a scraper's bounded sampling adapter doesn't have to mirror a uniform per-board cap
  exactly — when some boards cost nothing extra to sample in full, capping them anyway throws away
  free signal — but the candidate-selection logic still has to match production's real selection
  criteria precisely, not an approximation of it, or the measurement quietly dilutes itself.
- **New, from the post-merge correction**: a filter that correctly excludes truncated data from a
  coverage denominator is not the same fix as removing the truncation — it hides the cap's cost
  from the reported percentage without recovering the signal the cap threw away. `keep_ids` (this
  pass's own fix, above) was the right response to "phantom jobs are silently scored as no
  signal," but the deeper question — "is a 3/board cap the right design for an ATS whose *entire*
  coverage comes from Tier 2, where every uncapped detail fetch is potential real signal, not
  just noise-reduction?" — went unasked until directly raised after merge. When a sampling cap and
  the ATS's own production `fetch_raw()` compute the identical eligibility set (confirmed here:
  `_fetch_zoho`'s `missing_desc` and `fetch_raw()`'s `empty` used the same four conditions), check
  whether the cap is still earning its cost, not just whether its symptom is measured correctly.
- **New, the second post-merge finding**: an existing code comment asserting a scraper's own
  endpoint behavior ("the listing never paginates") is a claim someone made once, not a fact
  re-verified since — treat it the same as any other unverified assumption CLAUDE.md's own rules
  warn about, especially when a fix's whole justification leans on it. This pass's original PR
  correctly avoided calling `fetch_raw()` for workday/smartrecruiters because THEIR listings
  genuinely do paginate expensively — but the inverse claim about zoho ("costs nothing extra")
  was never independently hit-the-API-and-checked until directly asked to. It turned out to be
  right in the sense the fix needed (no extra *request* is required to walk pages) but wrong in a
  more consequential sense nobody had checked (the *response itself* silently truncates at ~750).
  A claim can be true enough to justify the immediate fix and still be hiding a bigger, unasked
  question — check the actual API behavior directly rather than trusting an inherited comment,
  even one that sounds authoritative and even when the narrower claim you need turns out correct.
