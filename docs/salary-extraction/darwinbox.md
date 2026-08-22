# darwinbox

## Methods tried

- **Live board count re-measured, not assumed**: the plan's 446 figure was stale (as every ATS
  measured so far has been). Current, properly deduplicated count: **281 live boards** (290 before
  this pass's own `EXCLUDED_BOARDS` fix — see below), below the 3,000 sampling cap. This pass
  sampled the full live population (13,658 jobs, 290 boards, 1 board errored).
- **Listing-only, no adapter needed**: `darwinbox.has_detail_pass` is unset (`False`,
  `BaseScraper`'s default) — the two-step fetch (`careerportalinfo`-equivalent tenant/TLD
  resolution, then one paginated `alljobs` POST per board) already returns the full posting,
  description included, in the listing pass. `scraper.fetch()` is the whole bounded sample; no
  `_DETAIL_ADAPTERS` entry was needed. A short 5-board dry-run cleared cleanly (0 errors,
  sub-6s/board) before sampling the full population, including confirming Cloudflare's documented
  edge wall (ADR-0056, `docs/darwinbox/cloudflare-wall.md`) wasn't actively blocking during this
  pass's sampling window.
- **Checked for structure one level deeper** (asked a seventh time now — ashby: hit, recruitee:
  confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever: confirmed-flat-miss,
  keka: confirmed undecodable): the raw `alljobs` response carries exactly two salary-related
  fields, `salary_range` and `salary_timeframe` — both already read by the scraper, both already
  wired into the Tier-1 parser. **Confirmed-flat-hit-with-nothing-more**: no third field, no nested
  compensation object, nothing left unread.
- **A real, major bug found and fixed — the actual center of this pass**: `_field_darwinbox`
  (pre-existing, built during the plan's own pilot phase from ADR-0019's single documented example,
  `"INR 3 - 5 (Annual)"`) unconditionally multiplied every parsed number by 100,000, assuming
  lakhs-shorthand universally. Measured against a real 72-company, 1,737-value sample (fake tenants
  excluded): genuine lakhs-shorthand is the **minority** shape (30 values, 14 companies, topping
  out at 19) — the overwhelming majority (1,707 values, 64 companies) state **already-absolute
  rupees** (`"INR 600000 - 1000000 (Annual)"`, `"INR 20000 - 25000 (Monthly)"`), which the blanket
  ×100,000 turned into a nonsensical billions-of-rupees figure, correctly-but-uselessly rejected by
  the plausibility bounds every time. Real gap: 20 to 4,999, zero observed real values — comfortable
  room either side of a magnitude threshold. Fixed with `_DARWINBOX_LAKHS_THRESHOLD = 1_000`,
  applied to `max(raw_lo, raw_hi)` before choosing the ×100,000-vs-×1 multiplier. Recovered field
  coverage from 0.2% to 13.1% (+1,691 jobs) on the sampled corpus — even larger than keka's own
  scientific-notation fix the previous pass.
- **A second, smaller, incidental scraper bug found and fixed while investigating the first**:
  `salary_range` already carries its own `"(Annual)"`/`"(Monthly)"` suffix whenever one exists —
  confirmed against every suffixed value in the sampled corpus (1,874/1,874) — so the scraper's own
  `parse()` appending `salary_timeframe` again was pure duplication (`"INR 3 - 5 (Annual)
  (Annual)"`, ADR-0019's own documented example — the doubling was baked into the very first
  reference example anyone wrote down, never noticed as a defect until this pass). Purely cosmetic
  (the doubled suffix doesn't confuse `_period_multiplier`'s substring check), fixed anyway since it
  was cheap, well-evidenced, and directly in the field this pass was already deep in.
- **A demo/QA-tenant sweep, broader than any prior pass**: 9 confirmed-fake tenants found by
  reading real content (never slug shape alone) — `minion`, `southuat`, `darwinboxdemo`, `spoc`,
  `treebotest`, `homecredituat`, `partnerdemodeloitte`, `training2`, `training14` (joining the
  single pre-existing `darwinbox:training` exclusion from an earlier, unrelated pass). Found via
  three independent signals, not one: explicit test/dummy-shaped titles ("Test Pre Offer", "Gulf
  Dummy", "SUPERADMINSUPERADMINSUPERADMIN SUPERADMIN"), a title/description content mismatch
  ("Customer Success Manager" rendering a "Chemistry Teacher" description), and — the most
  systematic — a numeric template ID contaminating BOTH the title AND location fields identically
  and repeatedly (`"3891_Manager"` at `"3891_Singapore, Singapore, Singapore, Singapore"`). Also
  recalibrated a false start: description-emptiness rate ALONE is **not** a reliable fake-tenant
  signal — real, well-known companies (Unacademy, Lulu India, Dixon Technologies, Airtel,
  ManipalCigna) show 30-100% empty-description rates as a genuine, real integration choice, with
  completely legitimate, specific, geographically-real job titles. One board (`banyanhcmuat`) was
  checked and deliberately kept despite a superficially suspicious "uat"-shaped slug — its content
  is genuinely realistic hospitality-industry postings with no test signal anywhere, mirroring
  lever's own `sandboxvr` precedent. 516 fabricated postings excluded, shipped as its own separate,
  standalone PR (#250) per this initiative's established scope-cleanliness convention.
- **Read real no-signal misses, then quantified the currency-shaped subset** (the mandatory audit):
  of 11,411 no-signal jobs (86.6% of the corpus, fake tenants excluded), 467 (4.1% of no-signal)
  have currency-shaped content that wasn't extracted. Read directly — see Patterns found for the
  breakdown, which concluded no new Tier-2 pattern was warranted.
- **Cross-ATS impact measured and confirmed zero** — expected and verified, not just assumed:
  `_field_darwinbox` is private, ATS-specific dispatch (`_FIELD_PARSERS["darwinbox"]`), reachable
  by no other ATS's Tier-1 path. The mandatory full diff against all 12 previously-merged ATSes
  (workable through keka) confirmed exactly that: `lost=0 gained=0 value_changed=0 crashed=0` on
  every single one.
- **Live-verified twice**: a 20-board fresh, differently-seeded sample (seed=313, fake tenants
  excluded from the sampling pool) against real current `{slug}.darwinbox.in`/`.com` hosts, zero
  errors, zero Cloudflare-wall escalations; large absolute-rupee values spot-checked directly and
  confirmed correctly parsed with the doubled-suffix bug also confirmed fixed on live output.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 290 sampled (281 post-exclusion), the
  full live population, comfortably below the cap.
- Measured both required percentages: **yes** — 13.0% field, 13.4% overall (Tier1+Tier2), against
  the corrected, fake-tenant-excluded population.
- Live-verified after code changes: **yes**, twice — the 20-board fresh sample after both the
  magnitude fix and the redundant-suffix fix, confirming both work correctly on live data.
- **Audited the no-signal bucket for language-independent currency-shaped content before trusting
  the coverage number as a ceiling**: 86.6% no-signal; 4.1% of those (467 jobs, 3.5% of all)
  currency-shaped, read directly and traced to specific reasons (see Patterns found) — none a real,
  buildable gap.
- Went beyond the ask: found and fixed a real, major, pre-existing Tier-1 correctness bug (the
  lakhs-vs-absolute magnitude assumption) worth +1,691 jobs — the largest single-pass field-
  coverage recovery in this initiative to date; found and fixed a second, smaller scraper bug
  (the doubled-suffix duplication) discovered while investigating the first; found and excluded 9
  demo/QA tenants (more than any prior single pass), explicitly correcting a false start along the
  way (description-emptiness rate is not a reliable fake-tenant signal on its own).
- Did not: build a new Tier-2 pattern — measured and found the existing shared cascade already
  correctly handles every real currency-shaped mention in the no-signal bucket (see Patterns
  found); the "Salary Range: INR to (Annual)" description-embedded template mirror is either
  genuinely empty (non-disclosure) or already extracting correctly whenever populated — verified
  directly, not assumed.

## Live-verification review

Two rounds, against real current `{slug}.darwinbox.in`/`.com` hosts each time, never a replay of
the frozen capture:

1. Dry-run before the full sample: 5 boards, 0 errors, sub-6s/board, no Cloudflare-wall escalation
   observed.
2. After both code fixes landed: 20 fresh boards, seed=313, fake tenants excluded from the sampling
   pool, 0 errors (one board, `mywio`, genuinely has 0 open jobs — not an error). Directly inspected
   15 extracted values from this fresh sample, including several large absolute-rupee ranges
   (`"INR 1400000 - 1500000 (Annual)"` → correctly 1,400,000-1,500,000) and confirmed the suffix no
   longer doubles in the live-fetched `Job.salary` string.

## Patterns found

- **The lakhs-vs-absolute magnitude bug dominates this pass's own gain** — not a description-mining
  pattern at all, but a Tier-1 correctness bug that had silently discarded the overwhelming
  majority of real darwinbox salary data since the very first pass that registered
  `_field_darwinbox`. Fixing it alone moved field coverage from 0.2% to 13.1% (+1,691 jobs).
- **The currency-shaped no-signal audit (467 jobs) broke down as**:
  - **A recurring description-embedded template mirror**: `"Salary Range: INR to (Annual)"` (or
    without the timeframe suffix) appears verbatim across many different companies
    (`sakraworldhospital`, `annalectindia`, `rohanbuilders`, `nectar`, `aragen`, `disha`, and
    others) — a description-text rendering of the SAME `salary_range`/`salary_timeframe` fields
    already read by Tier 1. Measured precisely: of every occurrence of this template in the
    corpus, the non-empty ones (real digits between "INR" and "to") **already extract correctly**
    (5/5 checked, via a mix of `field` and the shared Tier-2 cascade); the empty ones (`"INR to"`
    with nothing between) are genuine non-disclosure — the template renders regardless of whether
    a real figure was ever entered. No gap here, just a description-text mirror of already-handled
    data.
  - **"CAD" acronym collisions** (Computer-Aided Design — heavy in darwinbox's real-estate,
    manufacturing, and engineering postings: "Auto CAD", "CAD /Tekla software", "3D models... using
    CAD software") — the same false-alarm class keka's pass found and confirmed harmless. Not
    independently re-verified against a live extraction here (keka's pass already established the
    mechanism: `_CURRENCY_CODES`-matching requires a specific adjacent-number shape a bare "CAD"
    mention in a skills list never produces), consistent with every sampled instance staying
    correctly unextracted.
  - **Correctly-guarded non-salary currency mentions**: company/project revenue and valuation
    ("EPC projects with project values exceeding USD 30 million", "a diverse portfolio valued at
    US$5 billion" — repeated across several `herovired` postings, "$30bn revenue and $10bn in
    profit" for Vedanta), and one genuine new false-positive class this pass found: **product
    pricing embedded in a job description** — CaratLane (a jewelry retailer) describes its own
    product line ("a ₹80,000 solitaire on their phone") inside a design-role posting. Correctly
    stays unextracted (no recognized compensation label anywhere nearby), same mechanism as
    keka's own `bosswallah` product-pricing finding.
  - **A rendering artifact, not a currency mention at all**: `panaceabiotec`'s postings use `$>50$`
    as LaTeX-style math notation ("facility birth rates ($>50$)" meaning "greater than 50") that
    leaked through as literal text — flagged by this audit's crude symbol-detector as
    currency-shaped, but not a real dollar mention in any sense. Correctly stays unextracted.
- **No new Tier-2 pattern was warranted** — every real currency-shaped no-signal mention traced to
  one of: an already-correctly-extracting template mirror, an already-confirmed-harmless acronym
  collision, an already-guarded non-salary business mention, or a genuinely novel-but-still-
  correctly-declined false positive (product pricing, a math-notation artifact). The tenth prior
  ATS pass (lever) reached this same "mature shared cascade already covers it" conclusion; darwinbox
  reaches it independently, on a very different real-world corpus (India-heavy, non-tech-skewed
  company mix — hospitality, manufacturing, retail, pharma — not primarily software).

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 281 live, post-`EXCLUDED_BOARDS` fix) | 290 attempted (pre-fix count), 289 clean |
| jobs seen (fake-tenant postings excluded from this count and every figure below) | 13,142 |
| jobs with a structured `salary_range` field (`Job.salary`) | ~2,077 (coarse count before bounds checking) |
| of those, extracted via Tier 1 | 1,713 (13.0% of all jobs) |
| extracted via Tier 2 (description, no usable field) | 48 (0.4%) |
| **overall Tier1+Tier2 coverage** | **1,761 (13.4%)** |

Mid-to-lower pack for this initiative (workable 15.4%, workday 27.6%, greenhouse 36.1%,
smartrecruiters 10.0%, zoho 10.0%, teamtailor 14.1%, ashby 49.7%, recruitee 38.2%, personio 10.5%,
rippling 46.4%, lever 41.3%, keka 29.1%, **darwinbox 13.4%**). Unlike most prior passes, darwinbox's
number is **not** primarily a disclosure-rate story — the underlying disclosure rate (jobs with a
non-empty `salary_range`) is closer to 15.8%, and Tier 1 now correctly captures the overwhelming
majority of it (13.0% of 15.8%, roughly 82% conversion). The gap to a higher headline number is
darwinbox's real company mix skewing away from tech (hospitality, manufacturing, retail, pharma,
logistics), not a coverage defect — a lower ceiling correctly measured, not a lower conversion
rate incorrectly measured.

## What changed in code, and why

- **`src/headstart/salary.py`**:
  - `_field_darwinbox` rewritten: parses the raw number(s) first, then chooses a ×100,000
    (lakhs) or ×1 (already-absolute) multiplier based on `max(raw_lo, raw_hi)` against a new
    `_DARWINBOX_LAKHS_THRESHOLD = 1_000` constant, before applying the existing period multiplier
    on top. The threshold sits in a wide, empirically-confirmed gap (real lakhs values: 1-19; real
    absolute values: 5,000+) with room either side, not tuned to either boundary.
  - No other `salary.py` function touched — `_field_darwinbox` is private, per-ATS dispatch with
    zero shared-code blast radius, confirmed by the mandatory cross-ATS diff (see below).
- **`src/headstart/scrapers/darwinbox.py`**: `parse()`'s redundant `salary_timeframe` append
  removed — `salary_range` already carries its own suffix whenever one exists (confirmed:
  1,874/1,874 real cases). Purely cosmetic (doesn't affect extraction correctness either way), kept
  in scope since it was cheap, well-evidenced, and directly in the field already under
  investigation.
- **`src/headstart/config.py`**: `EXCLUDED_BOARDS` gained 9 darwinbox entries — landed as its own
  separate PR (#250), not this pass's own commits, per the scope-cleanliness convention lever's PR
  #246 and keka's PR #248 established. Two rounds of code review on that PR caught and fixed a
  real ordering-convention violation (numeric vs. lexicographic sort), a factual citation
  misattribution, and strengthened one entry's evidence from a weaker to a more systematic
  real-data signal — see that PR for its own full record.

### Cross-ATS impact

**Confirmed zero, not just assumed.** `_field_darwinbox` is reachable only via
`_FIELD_PARSERS["darwinbox"]` — no other ATS's Tier-1 path can invoke it, and no other `salary.py`
function was touched. The mandatory full cross-ATS diff against all 12 previously-merged ATSes
(workable through keka) confirmed exactly that expectation empirically: `lost=0 gained=0
value_changed=0 crashed=0` on every single one — a clean, complete non-event, verified rather than
skipped on the reasoning alone.

## Known gaps, left honestly unresolved rather than guessed at

- **The confirmed-flat-hit conclusion for "structure one level deeper"** — `salary_range` and
  `salary_timeframe` are genuinely the only two salary-adjacent fields in the raw payload; nothing
  is being left unread. Not a gap, but worth stating explicitly since this is the first pass in
  this initiative to reach "hit, and there is nothing more" as opposed to "hit, extract the deeper
  field" (ashby, personio) or "miss, nothing deeper exists" (recruitee, rippling, lever).
- **The CAD acronym-collision check was not independently re-verified for darwinbox specifically**
  — carried forward from keka's own established-safe finding rather than re-derived from scratch,
  since every sampled darwinbox instance of "CAD" stayed correctly unextracted, consistent with
  (not contradicting) keka's mechanism-level explanation.

## Carried forward from workable through keka — and new lessons

- **Applied**: the "check for structure one level deeper" question, asked a seventh time (ashby:
  hit, recruitee: confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss, lever:
  confirmed-flat-miss, keka: confirmed undecodable, **darwinbox: confirmed flat-hit-with-nothing-
  more** — a fourth distinct outcome shape, joining hit/flat-miss/undecodable).
- **Applied**: the mandatory "audit the no-signal bucket" methodology (personio's lesson,
  `docs/salary-extraction/README.md` step 3) — 86.6% no-signal, 4.1% of those currency-shaped, each
  category read and traced to a specific real reason.
- **Applied**: measuring every candidate pattern at full-corpus scale before building or declining
  it (lever's lesson) — the description-embedded "Salary Range: INR to" template was checked for
  its non-empty variant specifically (5/5 already correctly extracting) before concluding no gap
  existed, rather than assuming from the empty-variant's prevalence alone.
- **Applied**: demo/QA tenants confirmed only by reading real content, never slug shape alone
  (lever's/keka's lesson) — 9 found, one superficially-suspicious-named board (`banyanhcmuat`)
  correctly kept on the same evidentiary standard.
- **Applied**: an acronym-collision check (established for AED/401(k)/CAD/GBP/CTC on keka's pass)
  carries forward as a standing discipline — CAD collisions confirmed present but harmless here
  too, consistent with keka's own mechanism-level finding rather than a coincidence needing
  separate re-derivation.
- **New**: a Tier-1 parser's own core assumption — built from a single documented example during
  the initiative's pilot phase, before the "measure before building" discipline had fully
  hardened — can go unchallenged for many passes until the ATS that actually owns it gets its own
  dedicated research pass. `_field_darwinbox`'s blanket lakhs multiplier was never wrong in a way
  that crashed or corrupted anything (the plausibility bounds caught every misfire and safely
  returned `None`), which is exactly why it went unnoticed for so long — a "safely declined"
  failure mode is just as worth auditing as a "silently wrong" one, especially for any other
  early-built Tier-1 parser this initiative hasn't yet given its own dedicated pass.
- **New**: description-text emptiness rate, alone, is not a valid fake-tenant signal — it can
  reflect a completely legitimate, real company's own integration choice (confirmed on 6 large,
  well-known real companies at 30-100% empty-description rates). The reliable signals are content-
  level: explicit test/dummy-shaped titles, a title/description mismatch, or — the most
  systematic and repeatable — a template ID contaminating more than one structurally-independent
  field (e.g. both title AND location) identically. Worth checking specifically for this last
  signal on any future ATS's own demo-tenant sweep, not just the two signal types found before it.
- **New**: a scraper-level fix motivated by a functional bug (the lakhs/absolute magnitude
  ambiguity) can surface a second, purely cosmetic defect worth fixing in the same pass (the
  doubled timeframe suffix) even though it was never actually causing wrong extraction — the
  investigation that finds one real bug is a good moment to look for adjacent ones in the same
  code, not a reason to stop once the functional fix is confirmed.
