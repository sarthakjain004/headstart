# ripplehire

## Methods tried

- **Live board count re-measured, not assumed**: the plan's 79 figure was stale (as every ATS
  measured so far has been). Current, properly deduplicated count: **55 live boards**, well below
  the 3,000 sampling cap. This pass sampled the full live population.
- **Detail-pass, fan-out baked into `fetch_raw()` — needed a new bounded adapter**: like workday/
  smartrecruiters/rippling before it, `ripplehire.py`'s own `fetch_raw()` loops the search POST
  across every page AND fans out a per-posting detail fetch (`jobDesc`, never on the listing) for
  every job found — unsafe to call directly for a bounded sample. `BaseScraper` was checked first
  for a reusable primitive (lesson 40): its `_get()` returns only response text, but the token
  handshake ripplehire's `fetch_raw()` does first needs the redirected response's own `.url` to
  extract a token from — `_get()` structurally can't supply that, so the token step correctly
  mirrors production's own direct `http.fetch()` call rather than missing a primitive. Built
  `_fetch_ripplehire` in `scripts/enrich/salary_sample.py`, registered in `_DETAIL_ADAPTERS`.
- **A real design bug caught and fixed before trusting any coverage number**: the adapter's first
  version requested only `_DETAIL_FETCH_CAP` (3) listing rows via the search API's own `pagesize`
  parameter — silently starving *field* coverage to 3 jobs/board even though the compensation
  fields are already on the listing response and cost nothing extra to read (unlike the detail
  fetch, which genuinely does need bounding). On a board documented at ~937 jobs (LTIMindtree,
  `ripplehire.py`'s own module docstring), that meant judging the whole board's field-disclosure
  rate from 0.3% of it. Fixed: the listing now requests a full page (`_PAGE_SIZE`, matching
  production), so field coverage is measured across every job returned (up to 100/board); only the
  actual per-job detail fetch — the expensive part — stays capped at 3. Documented explicitly in
  the adapter's own docstring: this makes the coarser Tier-2/description-hint measurement a
  conservative undercount on any board with more than 3 jobs (rows past the cap correctly show "no
  hint" rather than an unread guess), while making the far more important Tier-1 field measurement
  accurate. Caught by directly inspecting a real board's job count against the pre-fix sample size,
  not assumed correct because the script ran without error.
- **Checked for structure one level deeper** (asked an eighth time now — ashby: hit, recruitee:
  confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever: confirmed-flat-miss,
  keka: confirmed undecodable, darwinbox: confirmed flat-hit-with-nothing-more, **ripplehire:
  confirmed flat-dead**): the raw listing carries `compensationRange` and `compensationInfo` (both
  read by the existing scraper), plus `companyCurrencySymbol`/`companySeq` and an unrelated
  employee-referral-reward cluster (`jobReward`/`rewardType`/`jobRewardCurrency`/
  `jobRewardPoints`/`currencyCoins`) that was checked and confirmed to be about referral bonus
  points, not compensation. Every one of these fields was `None`/empty on every real job inspected
  — 2,651 jobs checked via the fixed adapter (0 hits) plus 40 more spot-checked directly against
  the raw JSON across 4 different companies (0 hits). **Confirmed-flat-dead**, a fourth distinct
  outcome shape for this recurring question, joining hit/flat-miss/undecodable/flat-hit — real
  structure exists, the scraper already reads it correctly, and it is simply never populated by
  any real tenant sampled.
- **Read the 2 real Tier-2 hits and audited the no-signal bucket, restricted to genuinely-read
  jobs** (the mandatory audit, done carefully this time to avoid diluting it with jobs never
  detail-fetched — a real pitfall this pass's own adapter fix made newly visible): of 160 jobs
  with an actual fetched description (not the raw 2,651 field-checked total, most of which were
  never detail-fetched), 158 show no signal (98.8%); only 1 has currency-shaped content, a
  correctly-guarded company revenue mention ("Cimpress generated $3.5B in revenue"). The existing
  shared Tier-2 cascade already correctly extracts both real hits found (`usource`: "$88,000 -
  $132,000"; `tredence`: "$174,304") — no new pattern needed.
- **No `salary.py` changes this pass** — mirrors lever's own precedent (the first pass to make
  zero shared-code changes): with Tier 1 confirmed dead and no new Tier-2 pattern warranted, the
  mandatory full cross-ATS diff is correctly N/A, not skipped.
- **Live-verified twice**: the full 55-board sample itself (using the corrected adapter), plus a
  fresh, differently-seeded 12-board re-sample (seed=919) against real current
  `{slug}.ripplehire.com` hosts, zero errors, zero field hits — consistent with the full sample.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 55, the full live population.
- Measured both required percentages: **yes** — 0.00% field, 0.08% overall (Tier1+Tier2), against
  2,651 jobs (field) and 160 genuinely-read jobs (description).
- Live-verified after code changes: **yes**, twice — the full-sample run with the corrected
  adapter, plus a fresh 12-board reseed after.
- **Audited the no-signal bucket for language-independent currency-shaped content before trusting
  the coverage number as a ceiling**: restricted to the 160 jobs actually read (not the 2,651
  field-checked total, which would have diluted the audit with never-fetched descriptions) — 98.8%
  no-signal, 0.6% of those currency-shaped (1 job), read directly and confirmed correctly guarded.
- Went beyond the ask: found and fixed a real design bug in the sampling adapter itself before it
  could produce a misleading "0% coverage" conclusion from an artificially tiny, biased slice;
  checked the referral-reward field cluster specifically to rule out a false negative (confirming
  those fields are about something else entirely, not a missed compensation signal); spot-checked
  the raw JSON directly across 4 companies, not just trusting the formatted `Job.salary` output.
- Did not: register a Tier-1 parser (the field is confirmed dead, not merely unregistered) or
  build a new Tier-2 pattern (the 2 real hits found already extract correctly via the existing
  shared cascade). Did not touch `salary.py` at all this pass.

## Live-verification review

Two rounds, against real current `{slug}.ripplehire.com` hosts each time, never a replay of the
frozen capture:

1. The full 55-board sample itself, using the corrected (full-page-listing) adapter — 0 errored,
   2,651 jobs field-checked, 160 genuinely detail-fetched for description.
2. A fresh, differently-seeded 12-board re-sample (seed=919): `stltech`, `ltimindtree`,
   `tatasteel`, `drreddys`, `eclerx`, `cimpress`, `mphasis`, `tenant1-mph`, `bajajgeneral`,
   `axistrustee`, `ltts`, `tresvista` — 0 errors, 604 jobs, 0 field hits, consistent with the full
   sample's near-total absence of disclosure.

## Patterns found

- **No structured Tier-1 signal exists to find** — the defining, negative finding of this pass.
  `compensationRange`/`compensationInfo` are read by the scraper, present in the schema, and
  confirmed empty on every one of 2,651+ real jobs checked directly (not detail-fetch-limited).
- **The 2 genuine Tier-2 hits are ordinary, already-covered label+range shapes** — "Program
  Manager... $88,000 - $132,000" and an "Associate Manager" posting stating "$174,304" — nothing
  ripplehire-specific about either; the existing shared `_LABELED`/`_BARE_RANGE` cascade already
  handles them.
- **The single currency-shaped no-signal mention is a correctly-guarded revenue figure**
  ("Cimpress generated $3.5B in revenue through customized print products...") — the existing
  revenue/funding false-positive guard already excludes it.
- **No new Tier-2 pattern was warranted** — with only 160 genuinely-read descriptions and 2 real
  hits already covered, there simply isn't enough signal in this ATS's real corpus to find a new
  pattern worth building, distinct from lever's/darwinbox's "mature cascade already covers a large
  corpus well" conclusion — here the corpus itself is small and overwhelmingly non-disclosing.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 55 live) | 55 (full population) |
| jobs seen (field-checked: every job on each board's first listing page, up to 100/board) | 2,651 |
| jobs with a real fetched description (detail-fetch capped at 3/board) | 160 |
| jobs with a structured `salary` field (`Job.salary`) | 0 |
| extracted via Tier 1 | 0 (0.00% of all jobs) |
| extracted via Tier 2 (description, no usable field) | 2 (0.08%) |
| **overall Tier1+Tier2 coverage** | **2 (0.08%)** |

By far the lowest coverage of any ATS in this initiative (previous low: smartrecruiters/zoho at
10.0%). Consistent with ripplehire's own real company mix — per `ripplehire.py`'s own module
docstring, "enterprise/IT-heavy" (LTIMindtree, Mphasis, UST, Tata Steel) — large Indian IT-
services/enterprise employers are widely known for not disclosing compensation in job postings at
all, a genuine industry-segment characteristic rather than a measurement gap: field coverage was
checked across the full listing page of every board (not detail-fetch-limited), and came back
zero with very high confidence, not merely "not yet found."

## What changed in code, and why

- **`src/headstart/salary.py`: no changes.** Tier 1 is confirmed dead (nothing to register); no
  Tier-2 pattern cleared the evidence bar (only 2 real hits in the whole sample, both already
  covered by the existing cascade). The second pass in this initiative to conclude "nothing to
  extend" (after lever) — here because the real corpus has almost no signal at all, not because an
  already-mature cascade already covers it well.
- **`scripts/enrich/salary_sample.py`**: added `_fetch_ripplehire`, a bounded sampling adapter
  (`_DETAIL_ADAPTERS["ripplehire"]`) — the actual code change this pass produced. Requests a full
  listing page (`_PAGE_SIZE`, matching production) for accurate field coverage, detail-fetches only
  the first `_DETAIL_FETCH_CAP` postings for description text, documented as deliberately
  asymmetric (unlike every other capped adapter here) with the reasoning in its own docstring.

### Cross-ATS impact

**Not applicable — `salary.py` was not touched this pass**, so the mandatory full cross-ATS diff
doesn't apply here, matching lever's own precedent. This is a deliberate, evidence-based
non-event: Tier 1 is confirmed dead and no Tier-2 pattern was built, so there is no shared-code
change that could have moved any other ATS's numbers.

## Known gaps, left honestly unresolved rather than guessed at

- **Boards with more than 100 jobs on their first listing page** (e.g. LTIMindtree, ~937 jobs
  documented) still only had their first page's worth checked for the field, and only 3 of those
  detail-fetched for description — consistent with every other capped adapter's own bound, not a
  gap specific to this pass, but worth naming: a genuinely non-zero field-disclosure rate hiding
  entirely on page 2+ of a large board cannot be ruled out by this sample, though the 0/2,651
  result across every board sampled (including several 100-job first pages) makes that
  increasingly unlikely the more boards agree.
- **The description-hint/no-signal audit's 160-job denominator is small** — a genuine consequence
  of ripplehire's own small board count (55) and the standard 3/board detail-fetch cap, not a
  shortfall in this pass's own diligence. The 98.8% no-signal rate and single correctly-guarded
  miss are consistent with (not contradicted by) the much larger, near-total absence of field
  disclosure across the full 2,651-job sample.

## Carried forward from workable through darwinbox — and new lessons

- **Applied**: the "check for structure one level deeper" question, asked an eighth time (ashby:
  hit, recruitee: confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever:
  confirmed-flat-miss, keka: confirmed undecodable, darwinbox: confirmed flat-hit-with-nothing-
  more, **ripplehire: confirmed flat-dead** — a fourth distinct outcome shape).
- **Applied**: check `BaseScraper` for a reusable primitive before writing a new `http.fetch` call
  (lesson 40) — found a genuine, structural reason (`_get()` can't expose the redirected `.url` a
  token-extraction step needs) rather than a missed primitive, and documented that reasoning
  explicitly rather than silently deviating from the established pattern.
- **Applied**: the mandatory "audit the no-signal bucket" methodology (personio's lesson) —
  restricted to genuinely-read jobs specifically, avoiding dilution by jobs never detail-fetched.
- **Applied**: description-text emptiness/field-coverage accuracy requires checking what's
  genuinely free to read (the field, on the listing) versus what's genuinely bounded for cost (the
  detail fetch, description) — darwinbox's pass established the description-emptiness caution for
  fake-tenant detection; this pass applies the same "don't confuse cheap and expensive data" instinct
  to a sampling adapter's own design, catching a real self-inflicted measurement bug before it
  shipped a misleading number.
- **New**: when a detail-pass ATS's Tier-1 field lives on the LISTING response (not behind the
  detail fetch), a bounded sampling adapter must not let the same cap that bounds the (expensive)
  detail fetch also starve the (free) field check — request the full listing page for field
  coverage, and cap only the actual per-job detail fetch. Check this explicitly for every future
  detail-pass ATS's own adapter, not just assume the established capped-adapter shape from
  workday/smartrecruiters/rippling transfers safely — it does for THEM (their salary data comes
  from the SAME per-job detail response, so capping detail-fetches correctly caps field-checking
  too), but doesn't generalize to an ATS where the two data sources are structurally different.
- **New**: "confirmed dead" (Tier 1) and "no new pattern needed" (Tier 2) can co-occur on the same
  ATS, for two DIFFERENT reasons that shouldn't be conflated in the writeup — Tier 1 is dead
  because the field is never populated (evidenced by reading real data at scale); Tier 2 has no
  new pattern because the tiny amount of real signal that DOES exist is already covered by the
  mature shared cascade (evidenced by testing the 2 real hits found). State both reasons
  separately and explicitly, the same discipline lesson 7 established for distinguishing
  pre-existing bugs from same-PR ones — precision here is what makes the finding trustworthy
  rather than a single blurred "nothing here" conclusion.
