# trakstar

## Methods tried

- **Sampled the full live population**: 968 live boards (`config.load_active_companies`), the
  plan's stale figure was 1,632 — every ATS so far has had a stale plan-stage count, this one no
  exception. `fetch_raw()` bakes a full per-posting JSON-LD detail fan-out into itself (same
  shape as workday/rippling/smartrecruiters), so sampling needed a new bounded adapter
  (`_fetch_trakstar`, `scripts/enrich/salary_sample.py`) — built by checking `BaseScraper` for a
  reusable primitive first (lesson 40): the scraper's own inherited `_get()` covers the listing,
  and its own small `_job_posting(code)` covers a capped detail fetch, so no new `http.fetch`
  call was needed at all. `parse()` walks every job card found in the full listing HTML
  regardless of which postings were detail-fetched, unlike workday's/rippling's own capped
  adapters (which slice the raw item list before calling `parse()`), so the result is
  post-filtered down to just the `_DETAIL_FETCH_CAP` codes actually fetched — the same
  "don't count a never-read job as a no-signal one" discipline zoho's adapter established via
  `keep_ids`.
- **Tier 1 is genuinely absent, not just unwired**: the scraper's own `parse()` never sets
  `Job.salary` at all — no structured salary field exists anywhere in trakstar's data (the
  careers-page HTML card, or the per-job detail page's schema.org JSON-LD `JobPosting`, checked
  directly: `baseSalary`, a well-known standard property of that type, is absent on every real
  page read). 0.00% field coverage confirmed, not assumed.
- **Real Tier-2 coverage without any new pattern is already strong**: 19.78% overall, entirely
  via the existing shared cascade — higher than smartrecruiters/zoho (10.0%), workable (15.4%),
  and close to teamtailor (14.1%), without a single trakstar-specific line of code. Reading real
  hits showed rich, well-labeled US/Canada disclosure language ("Base Pay: \$18hr", "Pay Range:
  \$16.00 - \$18.00 an hour", "the salary range for this position starts at \$235,00 to
  \$250,000") the mature cascade already handles.
- **Four real Tier-2 candidates measured, three declined for thin evidence** (the same
  multi-company bar applied throughout this initiative — see `lever.md`, `personio.md`, and
  `zoho.md`'s own single-company declines):
  - A label immediately followed by a parenthetical period marker ("Pay Rate (per hour):
    \$18.75") — `_LABELED` doesn't match this shape at all (confirmed directly: `.search()`
    returns `None`), but real evidence is 1 occurrence, 1 company. Declined.
  - A glued `$Nhr`/`$Nhrs` bare suffix with no space or slash ("Base: \$25hr") — real evidence
    is 2 occurrences, 1 company (`registrarcorp`). Declined.
  - "per week"/"weekly" as a recognized period marker — an initial loose, whole-description
    co-occurrence check suggested 27 companies' worth of signal; reading a sample showed roughly
    80% of that was work-hours-commitment or meeting-cadence text ("15 hours per week", "weekly
    owners meetings") entirely unrelated to salary, sharing a description with an unrelated
    dollar figure elsewhere. Building the real candidate (`per\s+week|weekly` added to
    `_PERIOD_HINT`, plus the corresponding weekly-to-annual multiplier branch it needs) and
    diffing `from_description()` old-vs-new (`repr()`-compared, per lesson 11) across every real
    occurrence in the corpus found the true yield: 4 jobs, 2 companies. Declined — below the bar.
- **A fourth candidate — "base rate"/"hourly rate"/"starting rate" as a new label — cleared the
  evidence bar (7 companies) but was ultimately declined anyway, after exposing two separate,
  real bugs in shared resolution code along the way.** Building it and diffing the full corpus
  first surfaced a genuine, pre-existing bug in `_resolve()`'s own tie-break (its docstring
  already promised "the more informative span wins," but the code only ever checked
  currency-presence, never completeness): on a real board (`greenhouse:carvana`) the new label
  matched a second, incomplete mention of a wage a description ALSO stated completely elsewhere,
  and the old tie-break kept whichever mention came first in the text regardless of completeness
  — silently discarding hundreds of already-correct `max_annual` values the moment the new label
  made both mentions match. **Fixed — this ships in this pass, independent of the label's own
  fate** (see `_resolve()`'s own docstring for the full account; verified via the mandatory full
  cross-ATS diff: 82 jobs across 5 ATSes gained a previously-lost `max_annual`, zero
  regressions). But re-running the full diff with the fix in place found a SECOND, separate,
  deeper issue the tie-break fix can't touch: 8 further real cases (`lever:andersencorp`,
  `smartrecruiters:hillstonerestaurantgroup`, `workday:ucar/ucar_careers`, and 5 more) where the
  new label matched a genuinely LESS representative mention early in a description (e.g. a bare,
  incentive-framed rate — "your hourly rate is \$16... but the real reward comes from your
  incentive payments... making \$20-\$25" — where \$20-\$25 is the realistic figure a LOWER-
  priority tier used to correctly find), and because `_LABELED` is the FIRST tier tried in
  `from_description()`'s confidence-ordered cascade, the cascade stopped there — the later,
  correct tier never even ran. Fixing that safely means redesigning the cascade's own tier-
  precedence, not patching one function — disproportionate to one label's 7-company gain, and a
  materially bigger, riskier change than this pass's own scope. **Declined; reverted.** (A
  separate 19 cases surfaced by the same label, not counted against it, were legitimate NEW
  ambiguity — two genuinely different real figures, like a base hourly rate vs. a stated total
  annualized salary including commission — correctly declining rather than guessing, the same
  acceptable shape already established for the "ctc" label's own 7 cases, lesson 48.)
- **Audited the no-signal bucket for currency-shaped content genuinely missed** (the mandatory
  audit, lesson 39): of the currency-adjacent no-signal jobs read, the dominant reason is neither
  a missing pattern nor a missing label — `_LABELED` already matches most of these via its own
  pre-existing `pay(?:ing)?`/`wage`/`salary`/`compensation` keywords (unanchored `.search()`, the
  exact mechanism confirmed on successfactors's own pass this same day — see lesson 53). They
  decline because no period marker (hr/day/mo/yr) sits near the match, so `_period_from_window`
  defaults to an annual multiplier and the resulting figure (e.g. "\$33.35" read as \$33/year)
  correctly fails `_bounded()`'s plausibility floor — the no-fabrication principle working as
  designed, not a gap. A handful of correctly-declined false positives were also read: company
  funding/valuation mentions ("$100 million in Series D funding"), ceiling-only "up to \$18/hour"
  phrasing (the existing `_states_a_ceiling_only` guard), and genuinely ambiguous multi-level
  postings stating two different real figures for two different roles in one description
  (`_resolve()`'s existing mutual-consistency check correctly declining rather than guessing).
- **One `salary.py` change ships this pass, but it's a shared bug fix, not a new trakstar
  pattern**: `_resolve()`'s tie-break now correctly prefers a span with `max_annual` set over one
  without (previously it only checked currency-presence) — found while building the declined
  "rate" label above, kept regardless of that label's own fate since it's a real, independently-
  evidenced correctness fix. No new Tier-1 field (nothing to wire) and no new Tier-2 pattern
  (all four real candidates measured and declined) — trakstar's own coverage number is unchanged
  by this fix, since it only affects resolution across MULTIPLE matches, and trakstar's own
  no-new-pattern outcome means no case here ever produces more than one match. The mandatory
  full cross-ATS diff for the fix is real (82 jobs, 5 ATSes, zero regressions) even though
  trakstar's own corpus isn't among them.
- **A real, unrelated production bug was found and separately fixed while sampling**: the
  careers-page HTML this scraper reads for its production `fetch_raw()`/`parse()` path silently
  caps at 25 rendered job cards — confirmed live and measured at 5.4% of a 148-board sample
  hitting the cap, recovering 154 real jobs missed by the current path in that one run. Scoped as
  its own separate investigation and PR (#256, merged — adds `TrakstarScraper.fetch_via_feed()`,
  an alternate RSS-based fetch path, alongside the current one, not cut over to production yet),
  not bundled into this salary pass. This salary pass's own coverage numbers are measured against
  the CURRENT (HTML-capped) production path, since that's what's actually served today.
- **Three demo/QA vendor tenants found and excluded** (`bbtest`, `smoketest`, `testbass` — 27
  fabricated postings, clustered in Bangalore or content-confirmed as feature-testing sandboxes;
  `zutest` checked and deliberately kept as genuinely real) — fixed as its own standalone PR
  (#255), matching lever's/keka's/darwinbox's own precedent.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 968, the full live population after
  the demo/QA exclusion (958 boards succeeded, 10 errored — a real, low ~1.0% failure rate).
- Measured both required percentages: **yes** — 0.00% field, 19.78% overall (Tier1+Tier2),
  against 1,946 jobs.
- Live-verified after code changes: **yes**, on two levels. The sampling adapter itself: a
  fresh, differently-seeded 30-board reseed (seed=313) confirms it works correctly against real
  current boards, consistent shape with the full sample's own coarse *sampling-stage* signal (0%
  field, ~31.5% description-hint — the loose, keyword-based detector `salary_sample.py` itself
  reports while measuring, not the calibrated `salary.py` cascade rate reported under Coverage
  below; the full 968-board sample's own equivalent figure is 30.9%, see the Live-verification
  review section). The `_resolve()` tie-break fix: verified via the mandatory full cross-ATS
  diff required for any shared `salary.py` change (82 jobs, 5 ATSes, zero regressions — see
  Methods tried's fourth candidate for the full account), even though it happens not to move
  trakstar's own corpus (confirmed directly: 0/1,946 jobs change).
- **Audited the no-signal bucket for language-independent currency-shaped content before
  trusting the coverage number as a ceiling**: yes — read directly, traced to specific reasons
  (see Methods tried and Patterns found).
- Went beyond the ask: measured every candidate Tier-2 pattern at full-corpus scale via a real
  `from_description()` diff before declining any of them, not just an isolated regex-match test
  or a loose proximity heuristic (the "week" candidate specifically needed this — an initial
  loose measurement overstated its real yield by roughly 10x). Found, diagnosed precisely, and
  fixed a real, pre-existing bug in shared `_resolve()` code that a new label's own diff
  incidentally exposed — then, rather than stopping at "the diff looks clean," kept measuring
  and found a SECOND, deeper issue the first fix didn't touch, correctly declining the label that
  found it rather than shipping a fix that was necessary but not sufficient. Also found, scoped,
  and separately fixed a real, unrelated production data-completeness bug (the 25-job HTML
  truncation) rather than letting it sit unreported because it wasn't what this pass was
  measuring for.

## Live-verification review

Fresh, differently-seeded 30-board sample (seed=313) against real current trakstar hosts: 29/30
succeeded (1 errored — a real board-level failure, not an adapter bug), 54 jobs seen, 0% field,
31.5% description-hint (the sampling script's own coarse detector — `salary_sample.py`'s raw
output for this reseed: "jobs with only a description hint: 17 (31.5%)") — consistent with the
full 968-board sample's own equivalent figure (602/1,946 = 30.9%, from that same run's raw
output). This coarse figure is a sampling-stage signal, not the calibrated coverage number
(19.78%, see Coverage below); comparing it across the two runs is what confirms the sampling
adapter itself behaves consistently, not a claim about the calibrated rate. No new patterns
shipped this pass, so there is nothing for a code change to have silently broken; this re-run
mainly re-confirms the sampling adapter itself still works against real, current boards.

## Patterns found

Real, worked examples the existing shared cascade already extracts, unmodified:

- `"Base Pay: $18hr + performance bonus opportunities"` — `_LABELED`'s `pay` keyword plus the
  existing glued-hour-shorthand handling.
- `"Pay Range: $16.00 - $18.00 an hour"` — a clean labeled range with an explicit period marker.
- `"the base salary for this position is expected to be up to $50,000 per year with expected
  on-target earnings (OTE) of $70,000–$90,000"` — the existing ceiling/OTE handling.
- `"the salary range for this position starts at $235,00 to $250,000 for those who meet all
  qualifications depending on location"` — a long, prose-heavy labeled range.
- `"Wage Range: $21 - $25"` and `"Compensation and Benefits Salary range: $19 – $21 (based on
  experience)"` — genuinely declined, not extraction gaps: no period marker anywhere in the
  surrounding text, and the no-fabrication principle correctly won't guess whether a bare `$19–
  $21` means hourly or annual, even though the shape reads as very likely hourly given the role.
- `"Electrical Engineer I pay range: $64,600 - $107,667* Electrical Engineer II pay range:
  $78,729.66– $127,907.94"` — two real, different, correctly-declined figures for two different
  job levels stated in one description; `_resolve()`'s existing mutual-consistency check declines
  rather than picking one arbitrarily, the same no-fabrication principle extended to
  disambiguation.

## Coverage

| metric | value |
|---|---:|
| boards sampled (full live population) | 968 |
| boards succeeded / errored | 958 / 10 |
| jobs seen | 1,946 |
| structured field (Tier 1) | 0 (0.00%) |
| description mining (Tier 2, no usable field) | 385 (19.78%) |
| **overall Tier1+Tier2 coverage** | **385 (19.78%)** |
| boards with ≥1 job showing either | 310/958 (32.4%) |

## What changed in code, and why

One line in `salary.py`: `_resolve()`'s tie-break, extended to prefer a span with `max_annual`
set over one without (see Methods tried's fourth candidate, and `_resolve()`'s own docstring, for
the full account of how this was found and verified). No new Tier-1 field — confirmed absent,
not just unread: no `baseSalary` anywhere in the JSON-LD, no salary field in the raw payload at
all. No new Tier-2 pattern — all four real candidates found this pass were measured at
full-corpus scale; three declined as below the multi-company evidence bar (1, 1, and 2 companies
respectively), and the fourth cleared the bar but was declined anyway once it exposed a second,
deeper cascade-precedence issue disproportionate to fix in this pass. "No new pattern needed" is
a legitimate, evidence-backed outcome here (lesson 42), not a sign the research was shallow: the
existing shared cascade already delivers 19.78% coverage on trakstar without any ATS-specific
extension, driven by trakstar's own real company mix skewing toward US/Canada pay-transparency-
jurisdiction employers.

`scripts/enrich/salary_sample.py` gained `_fetch_trakstar`, a new bounded sampling adapter (no
`salary.py` involvement — pure sampling infrastructure).

Separately (own PRs, not this pass's `salary.py` scope): `src/headstart/config.py` gained 3 new
`EXCLUDED_BOARDS` entries (PR #255); `src/headstart/scrapers/trakstar.py` gained an investigative
`fetch_via_feed()` alternate fetch path plus `scripts/eval/trakstar_feed_compare.py` (PR #256) —
see Methods tried above for the full account.

## Carried forward

- **Lesson 40** (check `BaseScraper` for a reusable fetch primitive before writing a new adapter)
  applied directly: `_fetch_trakstar` needed zero new `http.fetch` calls, reusing the scraper's
  own inherited `_get()` and its own small `_job_posting()` method.
- **Lesson 42** (measure every Tier-2 candidate at full-corpus scale via a real `extract()` diff
  before building OR declining, never stop at an isolated match test or a loose proximity count)
  was essential here, not optional: the "week" candidate's real yield (4 jobs, 2 companies) was
  roughly 10x smaller than what a loose whole-description co-occurrence check suggested (27
  companies) — reading a sample of the "matches" showed most were unrelated work-hours or
  meeting-cadence text sharing a description with an unrelated dollar figure. A future pass
  measuring a period-marker or label candidate should mirror this: check real proximity to an
  actual number, not just co-occurrence anywhere in the same description, before trusting a
  candidate's evidence count at all.
- **Lesson 53** (a code-review finding's own prose explanation, and even a raw regex-match
  count, both need re-deriving via direct code execution before being trusted as evidence — from
  successfactors's own pass this same day) applied directly to the "week" candidate: the initial
  loose measurement's "27 companies" was a real number from a real query, but the query itself
  measured the wrong thing (co-occurrence, not proximity) — re-running the ACTUAL candidate
  pattern through the real cascade and diffing real output was the only way to find the true
  yield (4 jobs, 2 companies), matching the exact discipline lesson 53 names.
- **Lesson 43** (check for demo/test/sandbox-shaped board content while sampling, confirmed by
  reading, never by slug shape alone) applied and found 3 more (bbtest, smoketest, testbass) —
  four ATSes running in the "already-partially-handled"/"remaining unexamined" groups combined
  (lever, keka, darwinbox, trakstar) have now turned up vendor demo tenants; worth a standing
  check on every remaining ATS, not an occasional one.
- **New**: a real, unrelated production bug found while sampling for salary extraction (the
  25-job HTML truncation) is worth surfacing and scoping as its own investigation immediately,
  not silently worked around or silently ignored just because it isn't what the current pass is
  measuring for — see PR #256 for the full account.
- **New**: a new label/pattern can fail in TWO distinct ways once it starts matching text a
  description already states correctly elsewhere, and fixing one does not fix the other. (1) A
  *tie-break* failure — the new match and an existing match land in the SAME tier and are close
  enough to be judged "the same figure," so `_resolve()`'s own preference logic decides which
  wins; a preference that only checks one dimension of informativeness (here, currency-presence)
  can pick the less complete of two agreeing spans. This is fixable with a narrow, low-risk
  change to that one function, verified by the mandatory full cross-ATS diff. (2) A *cascade-
  precedence* failure — the new match lands in an EARLIER tier than a different, already-correct
  match that used to be found by a LATER tier, and `from_description()`'s own "stop at the first
  tier that succeeds" design means the later tier never runs at all once the earlier one
  produces anything. This is NOT fixable with a small patch — it requires redesigning tier
  precedence itself, since the cascade's confidence-ordering assumption (earlier tiers are more
  trustworthy) is exactly what breaks. Before trusting a full cross-ATS diff's "zero regressions"
  after fixing (1), specifically check for values that got LESS complete or plain wrong (not
  just those that became `None`) — that's the signature of (2), and it can hide inside a diff
  that otherwise looks clean if you only grep for `-> None`.
- **New**: don't write a specific measured number (a company count, a "confirmed via the diff"
  claim, a regression count) into a code comment or docstring before actually running the
  measurement that number describes — caught myself doing this twice in the same pass, once
  claiming a combined-diff result before running the combined diff, once claiming an ATS count
  that turned out to be one off (5, not 6) once actually counted. Write the comment AFTER the
  measurement returns, not before, even when the number feels predictable.
