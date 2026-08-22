# lever

## Methods tried

- **Live board count re-measured, not assumed**: the plan's 2,784 figure was stale (as every ATS
  measured so far has been). Current, properly deduplicated count: **2,187 live boards**
  (2,179 after this pass's own `EXCLUDED_BOARDS` fix — see below), below the 3,000 sampling cap.
  This pass sampled the full live population.
- **Listing-only, no adapter needed**: `lever.has_detail_pass` is unset (`False`, `BaseScraper`'s
  default) — the full posting, including its description, comes back in one listing GET
  (`api.lever.co/v0/postings/{slug}?mode=json`, falling back to `api.eu.lever.co` on a 404). The
  sampling script's default path (`scraper.fetch()`) already handles this correctly; no
  `_DETAIL_ADAPTERS` entry was needed, unlike every detail-pass ATS this initiative has covered so
  far (rippling, workday, smartrecruiters, zoho).
- **Dry-run first**: 40 boards, seed=1, 32 workers, 0 errored — cleared to sample the full
  population at the standing default concurrency.
- **Checked for structure one level deeper** (asked a fifth time now — ashby: hit, recruitee:
  confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss): the raw posting object
  carries `salaryDescriptionPlain`/`salaryDescription`, a free-text field never referenced anywhere
  in `lever.py`. Read directly against real live data across several boards before measuring at
  scale: where non-empty, it's either a clarifying note about the ALREADY-captured `salaryRange`
  ("This is a range for the base salary, not including commission/OTE") or genuinely empty/non-
  disclosing filler ("To be determined based on your profile."). Measured precisely, not assumed:
  across 1,627 jobs with no `salaryRange` at all (an 80-board live sample, not the frozen capture),
  **zero** had real currency-shaped content in `salaryDescription`. **Confirmed-flat-miss**,
  joining recruitee's and rippling's — real structure exists, but it never carries a figure the
  existing `salaryRange` field doesn't already state.
- **A real, incidental finding, fixed separately**: while sampling, 8 lever slugs
  (`leverdemo` and 5 numbered variants, `levertest`, `salesdemo-jr`) turned out to be Lever's own
  vendor demo/QA tenants — 1,769 fabricated postings (template titles, `(copy)`-suffixed
  duplicates, joke entries like "Ice cream eater"), live in the liveness ledger and counted as real
  hiring boards. Confirmed by reading real content, not the slug shape (`lever:sandboxvr`, a real
  VR-entertainment company, was checked and correctly kept despite a superficially similar name).
  Fixed in `src/headstart/config.py`'s `EXCLUDED_BOARDS`, **as its own separate, standalone PR
  (#246)** — a liveness/data-quality concern, not a `salary.py` concern, kept out of this pass's
  own diff per this repo's "Divergent Change" scope-cleanliness convention. All coverage numbers
  below are measured against the corrected, fake-board-excluded population.
- **Read real misses** from the full sample's "currency-shaped but not extracted" bucket — 30
  examples read directly, spanning several distinct real companies (not just the highest-volume
  poster). Found three structurally genuine gaps (a period-first "Monthly compensation of
  approximately" phrasing, a reversed "$X annual salary" word order, and compound currency
  prefixes like "CA$") — each measured at scale before deciding, and each declined (see below).
- **Measured prevalence of every candidate pattern BEFORE building anything** — the discipline this
  initiative has followed since the pilot, applied here to conclude "don't build," not just "build
  it because it's evidenced." All three candidates found in the 30-example read turned out to be
  effectively single-company when checked against the FULL no-signal corpus (not the 30-example
  sample): "Monthly compensation of approximately" — 6 occurrences, all from one company
  (`jobgether`, a job-aggregator platform whose templated postings for many employers inflate raw
  occurrence counts without real company diversity, the same risk class zoho's `ascor` and
  personio's `flatrock` already taught); the reversed-order and compound-currency-prefix shapes —
  effectively one example each once re-measured against the full corpus, not the smaller sample
  that happened to surface them. None cleared this initiative's own multi-company evidence bar.
- **Live-verified twice**: the full 2,187-board sample itself, plus a fresh, differently-seeded
  50-board re-sample (seed=909) after the `EXCLUDED_BOARDS` fix landed — confirming the fake
  tenants are genuinely gone from what gets sampled, not just from a manual count.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 2,187 (2,179 post-fix), the full live
  population, below the cap.
- Measured both required percentages: **yes** — 30.5% field, 41.3% overall (Tier1+Tier2), both
  against the corrected, fake-board-excluded population.
- Live-verified after code changes: **yes**, twice — though note `salary.py` itself received ZERO
  changes this pass (see What changed in code); the live-verification here confirms the
  `EXCLUDED_BOARDS` fix took effect in production sampling, not a salary-pattern change.
- **Audited the no-signal bucket for language-independent currency-shaped content before trusting
  the coverage number as a ceiling** (the standing methodology from personio's own post-merge
  audit): 84.2% of no-signal jobs have no currency-shaped content anywhere in the description
  (genuinely undisclosed); 15.8% (6,740 jobs, 9.3% of all jobs) had currency-shaped content that
  wasn't extracted — read directly, not assumed clean, and traced to real, specific reasons (see
  Patterns found).
- Went beyond the ask: found and fixed a real, pre-existing data-quality issue (the 8 fake lever
  tenants) that this initiative's own liveness-dedup machinery had never caught, since nothing
  before this pass had ever read lever's real board content closely; measured three real candidate
  Tier-2 patterns at full-corpus scale specifically to confirm they DON'T clear the evidence bar,
  rather than stopping at "found in a 30-example read" and building from that alone.
- Did not: build any of the three candidate Tier-2 patterns (see Known gaps) — each is real,
  each was measured, and each stayed below this initiative's own multi-company bar. Did not chase
  `salaryDescription` further after the 80-board, 1,627-job zero-evidence result — a clean,
  confident negative, not an early stop.

## Live-verification review

Two rounds, against real current `api.lever.co`/`api.eu.lever.co` hosts each time, never a replay
of the frozen capture:

1. Dry-run before the full sample: 40 boards, seed=1, 32 workers, 0 errors.
2. After the `EXCLUDED_BOARDS` fix landed: 50 fresh boards, seed=909, 32 workers, 0 errors, none of
   the sampled boards were among the 8 now-excluded fake tenants — confirms the fix is live in
   production sampling, not just verified by a standalone count.

## Patterns found

- **No new Tier-2 pattern was warranted** — the honest, evidence-based conclusion of this pass, not
  a shortfall. Ten prior ATS passes (workable through rippling) have already built a substantial,
  general-purpose Tier-2 pattern library for English-language salary phrasing, and lever's own
  corpus is predominantly English (US/EU tech and services companies). What remained after the
  no-signal-bucket audit split cleanly into: genuine non-disclosure (84.2% of no-signal, no
  currency-shaped content at all); real, already-guarded false positives (company revenue/funding/
  ARR mentions — "$20M-$50M in annual revenue", "Backed by \$30M in funding", "$25M ARR" — all
  correctly excluded by the existing `_FALSE_POSITIVE_CONTEXT` guards); genuinely implausible
  magnitudes correctly declined by the existing plausibility bounds (`Compensation: $2,000-$3,000
  USD` reads as an annual figure and correctly fails the floor — confirmed directly: the SAME
  "Compensation:" label already extracts a plausible range correctly when tested in isolation, so
  the miss is the bounds check doing its job, not a label-recognition gap); and three narrow,
  real-but-single-company phrasing gaps (below), each measured and declined.
- **Three real gaps found, measured, and declined — not speculative, just below the evidence bar**:
  - `"Monthly compensation of approximately USD $X-$Y"` — confirmed structurally genuine (a
    clean, plausible test case in isolation returns `None`, so this isn't a bounds rejection) but
    6 occurrences, 1 company (`jobgether`) in the full no-signal corpus.
  - `"$X annual salary for the position"` (number-before-label, reversed from every currently-
    recognized "label: $X" order) — also structurally genuine in isolation, but essentially one
    example once re-measured at full-corpus scale.
  - Compound currency prefixes (`CA$`, and by the same shape `AU$`/`NZ$`/`HK$`/`US$`) — the bare
    symbol regex only recognizes single-character symbols (`$£€₹`), so "CA$102,000" isn't
    recognized as a CAD-prefixed figure at all; also essentially one example at full-corpus scale.
  - None of the three were built. This initiative's own established discipline (zoho's `ascor`,
    recruitee's `rebootmonkey`, personio's `flatrock`, this pass's `jobgether`) is explicit that a
    single company's repeat-posted phrasing isn't broad evidence, however many times it recurs
    within that one company's own postings.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 2,179 live, post-`EXCLUDED_BOARDS` fix) | 2,187 attempted (pre-fix count), 2,165 clean |
| jobs seen (fake-tenant postings excluded from this count and every figure below) | 72,794 |
| jobs with a structured `salary` field (`Job.salary`) | ~22,300 (30.6%, coarse count) |
| of those, extracted via Tier 1 | 22,191 (30.5% of all jobs) |
| extracted via Tier 2 (description, no usable field) | 7,868 (10.8%) |
| **overall Tier1+Tier2 coverage** | **30,059 (41.3%)** |

Third-highest coverage of any ATS in this initiative so far (workable 15.4%, workday 27.6%,
greenhouse 36.1%, smartrecruiters 10.0%, zoho 10.0%, teamtailor 14.1%, ashby 49.7%, recruitee
38.2%, personio 10.5%, rippling 46.4%, **lever 41.3%**) — behind only ashby and rippling. Lever's
own structured `salaryRange` field was already registered with the shared parser from this
initiative's very first ATS pass (used as `_field_range_currency_interval`'s original canonical
example), so this pass's real contribution is confirmation and measurement — proving the existing
Tier 1 registration and Tier 2 pattern library already serve lever well — plus the incidental
`EXCLUDED_BOARDS` data-quality fix, not a new extraction capability.

## What changed in code, and why

- **`src/headstart/salary.py`: no changes.** Lever's Tier 1 registration
  (`_field_range_currency_interval`) predates this pass; no Tier-2 pattern cleared the evidence bar
  (see Patterns found). This is the first ATS pass in this initiative to conclude "the existing
  shared code already covers this ATS well" rather than extend it — a legitimate outcome once a
  pattern library has matured across ten prior passes, not a shortfall in this pass's own research.
- **`src/headstart/config.py`: `EXCLUDED_BOARDS` gained 8 lever entries** — landed as its own
  separate PR (#246), not this pass's own commits, per the scope-cleanliness reasoning above. See
  that PR for its own full record.
- No test changes: no new pattern, no new field parser, nothing new to cover.

### Cross-ATS impact

**Not applicable — `salary.py` was not touched this pass**, so the mandatory full cross-ATS diff
(required whenever shared extraction code changes) doesn't apply here. This is a deliberate,
evidence-based non-event, not a skipped step.

## Known gaps, left honestly unresolved rather than guessed at

- **The three measured-and-declined Tier-2 phrasings** (Patterns found above) — each real, each
  structurally confirmed (not a bounds rejection), each below the multi-company evidence bar.
  Worth revisiting only if a FUTURE ATS's own pass finds the same shape recurring across multiple
  real companies — at that point it would be evidence-based to build, which it isn't yet from
  lever's own corpus alone.
- **`jobgether`'s own volume** (a single job-aggregator company posting on behalf of many
  employers) means some of lever's own real coverage ceiling reflects one platform's writing
  conventions more than lever's broader company population — noted for awareness, not adjusted for
  in the headline numbers above, since the postings are still genuinely different employers' real
  job listings, not fabricated content like the excluded demo tenants.

## Carried forward from workable through rippling — and new lessons

- **Applied**: the "check for structure one level deeper" question, asked a fifth time
  (ashby: hit, recruitee: confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss,
  lever: confirmed-flat-miss) — measured against 1,627 real jobs, not a handful of examples.
- **Applied**: the mandatory "audit the no-signal bucket" methodology (personio's lesson,
  `docs/salary-extraction/README.md` step 3) — 84.2%/15.8% split, read directly.
- **Applied, and reinforced**: "distinguish distinct-company evidence from raw occurrence counts"
  (zoho's `ascor`, recruitee's `rebootmonkey`, personio's `flatrock`) — this pass's own `jobgether`
  is a fourth, independent confirmation of the same risk class, this time catching THREE candidate
  patterns before they were built rather than after.
- **New**: a mature shared Tier-2 pattern library (ten prior ATS passes deep) can genuinely already
  cover a new ATS's real-world phrasing — "no new pattern needed" is a legitimate, evidence-backed
  conclusion for a pass to reach, not a sign the research was too shallow. The discipline that
  makes this trustworthy is the same one that would have caught a real gap: measure every
  candidate at full-corpus scale before deciding either way.
- **New**: a scraper's own vendor/platform can have demo or QA tenants live in the liveness ledger
  that nothing has caught yet, simply because no prior pass ever needed to read that ATS's real
  board content closely. Worth a similar quick check (a handful of demo/test/sandbox-shaped slugs,
  content-verified before excluding) on any remaining unexamined ATS, not just assumed clean because
  `EXCLUDED_BOARDS` already has entries for other ATSes.
- **New**: when a data-quality issue is found incidentally during a salary-extraction pass but
  doesn't touch `salary.py`, scope it as its own separate PR rather than bundling it — keeps each
  PR's diff traceable to one reason to change, and means a `salary.py`-focused review isn't also
  asked to judge an unrelated liveness-ledger claim.
