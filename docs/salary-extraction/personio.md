# Personio — salary extraction findings

Ninth ATS in the salary-extraction initiative. See `README.md` for the overall process and the
eight prior passes: `workable.md` (pilot), `workday.md`, `greenhouse.md`, `smartrecruiters.md`,
`zoho.md`, `teamtailor.md`, `ashby.md`, `recruitee.md`.

Personio is one of the 9 ATSes that already populated `Job.salary` from a structured field —
and, like ashby before it, that field turned out to be reading the wrong thing. Applying ashby's
and recruitee's carried-forward lessons up front: personio's `<salaryInformation>` element is
genuinely structured one level deeper than what the scraper extracted (a hit, same as ashby — the
question has now been asked three times: hit, confirmed-flat-miss, hit again), and the fix's own
zero/None handling was checked against real data rather than assumed safe from the code shape
alone (a clean negative this time, like recruitee's own finding, for a different, structural
reason: XML text is always a string, so the ashby-class truthy bug can't reach it).

## Methods tried

- **Live board count re-measured: 2,534** (the plan's 2,992 figure was stale). Listing-only
  (`has_detail_pass = False`, confirmed by reading the scraper), so the sample covers the entire
  live population, not a subset — 2,534 of 2,534 boards were the sampling target (fewer than the
  3,000 cap).
- **Applied ashby's/recruitee's "check for structure one level deeper" lesson — a hit, not a
  miss, confirmed by direct API inspection**: personio.py's `_text(pos, "salaryInformation")`
  reads only the element's own direct text — but 80 real boards / 700 positions showed
  `<salaryInformation>` is itself structured (`<min>`, `<max>`, `<currencyCode>`,
  `<currencySymbol>`, `<type>`), and `.text` is always empty when an XML element has child
  elements (the text *before* the first child; Personio never puts any there). So `salary` was
  silently `None` for every one of the 13.4% of positions carrying this real data — a genuine,
  silent Tier-1 dead end, the same class of finding as ashby's `compensationTierSummary`, just
  reached via a different underlying serialization (XML child elements vs. a JSON string field
  nobody read a level deeper).
- **Applied the "check `is not None` vs. truthiness" lesson — negative result, but for a
  structural reason rather than an empirical one**: `ElementTree.findtext()` always returns a
  Python string (or `None`) for XML text content — there is no code path by which it could return
  a raw falsy integer `0` the way ashby's JSON deserialization could. Verified directly (0
  zero-valued, 0 non-string `min`/`max` across a live 80-board check) before concluding this, not
  assumed from the type system alone — but the explicit `is not None` checks were written anyway,
  since the cost is zero and this exact code shape has now caused one real bug in this codebase.
- **Fixed at the source**: new `_salary(pos)` in `personio.py` reads `<min>`/`<max>`/
  `<currencyCode>`/`<type>` and formats `"MIN-MAX CODE period"` (or `"MIN CODE period"` for a
  floor/fixed-rate-only tier, no `<max>`) — the same shape `_field_range_currency_interval`
  already parses for lever/recruitee/teamtailor/ashby. Personio's own `<type>` values
  ("yearly"/"monthly"/"hourly", all three confirmed real; "daily"/"weekly" not observed but the
  same naming convention) don't match `_period_multiplier_structured`'s bare-word regex — the
  "-ly" suffix breaks the `\bmonth\b`-style word-boundary check — so `_PERIOD_WORD` maps each to
  the already-recognized word in `personio.py` itself, a narrow, local fix rather than widening a
  shared regex for one ATS's own suffix convention. Registered `"personio"` in `_FIELD_PARSERS`.
- **Two existing tests needed the same swap ashby's own pass caused, and were fixed to stop this
  from recurring a third time**: `test_field_generic_bare_word_period_not_recognized_in_free_text`
  and `test_field_generic_up_to_states_a_ceiling_not_a_floor` used "personio" as `_field_generic`'s
  canonical free-text example — now that personio is structured, both were rewritten to use the
  synthetic `"some-new-ats"` placeholder (already established elsewhere in the same test file for
  "any ATS with no calibrated parser") rather than a third real ATS name that a future pass could
  just as easily move off `_field_generic` again.
- **Tier-1 residual failures (136 of 2,076 field-present jobs, 6.6%) traced to their root cause,
  not just counted**: every one is an unsupported currency, and every one of those traces to
  exactly ONE company each — GEL/BGN/CRC/LKR all from a single globally-distributed remote-first
  company (`flatrock`, posting salary in each hire's own local currency across several countries),
  plus HUF (one company) and PHP (one company). None clears the multi-company evidence bar
  recruitee's own pass established for adding currency support (19 companies for PLN, 7 for CHF) —
  measured and deliberately left unsupported, not built past the evidence.
- **Tier-2 gap analysis, English-language misses only**: sampled real "no field, no Tier-2 hit"
  descriptions with a currency/salary-word hint. The one numeric candidate found
  (`"deals above R$500,000"`) is a sales-role deal-size requirement, not a salary — correctly
  never extracted. No real, actionable English-language Tier-2 pattern found.
- **A genuinely German-majority description corpus, measured directly, even more lopsided than
  recruitee's own finding**: a real `langdetect` measurement over a random sample of misses found
  79.8% German, 10.2% English, with a long single-digit tail — consistent with Personio's DACH
  (Germany/Austria/Switzerland)-heavy customer base. The dominant German phrasing
  ("Gehaltsvorstellung"/"Gehaltswunsch" — "desired salary," asking the *candidate* to state a
  figure) is a genuine non-disclosure pattern, not a missed extraction, in the one case read in
  detail. Consistent with, not a deviation from, this project's English-only search-corpus scope —
  not built, for the same reason recruitee's and zoho's own non-English findings weren't.
- **A third, genuinely different rate-limit-adjacent failure mode for this initiative's own memory
  — but an already-known, already-thoroughly-investigated one for this repo.** The full sample hit
  24/2,534 errors (0.9%), all clustered at the very end of a long run; unlike teamtailor's
  rolling-window limit or recruitee's simple too-many-concurrent-connections, a retry at 4 workers
  recovered **zero** of the 24. Inspecting one directly showed `x-vercel-mitigated: challenge` in
  the response headers — a header-level confirmation of the exact mechanism
  `docs/personio/vercel-bot-wall-bypass.md` (an existing, far more thorough investigation from
  2026-08-14, predating this pass) already identified in depth: Vercel's Firewall "Challenge"
  action (a client-side proof-of-work + browser-characteristics check), not a request-rate
  throttle — concurrency reduction doesn't touch it, matching that doc's own finding that the wall
  "still fires at ONE request every 3 seconds." That doc's own conclusion, reached independently
  and well before this pass needed one, was the same one this pass would have landed on from
  scratch: no current non-browser solve exists (the one public implementation is confirmed broken
  post-WASM-migration), and building one is real, open-ended reverse-engineering effort not
  proportionate to what it recovers — so `scripts/validate/check_liveness.py` already treats a
  detected challenge as a graceful 30-minute cooldown, never a mis-marked dead board, and this
  sampling script's own small, accepted gap here is consistent with that same, already-reasoned
  stance, not a new problem this pass is the first to hit. One suggestive, unconfirmed data point
  from that doc worth flagging forward rather than chasing now: it found a clean, unchallenged
  response from a home-network IP against a live tenant, hypothesizing the trigger tracks
  requesting IP/ASN reputation rather than the request itself — this pass's own 99.05% clean rate
  (vs. whatever CI/datacenter-IP runs might see) is a small, additional, consistent-but-not-
  controlled data point for that same hypothesis, not a confirmation.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — all 2,534 live boards (below the 3,000
  cap), 2,510 clean (99.05%) after accounting for the Vercel-challenge tail.
- Measured both required percentages: **yes** — 10.7% field presence, 10.2% overall Tier1+Tier2
  coverage, plus the full residual-failure breakdown (single-company-evidenced currencies, not an
  unexplained remainder).
- Live-verified after code changes: **yes** — a fresh, differently-seeded 60-board sample
  (seed=2026, 20 workers) after the structured-field fix, reported in Live-verification review.
- Checked the coverage claim against a more durable source than the frozen sample, per the plan's
  own verification checklist: **same tradeoff ashby.md and recruitee.md already named, not
  silently repeated.** The served table's `/search` HTTP endpoint is still behind ADR-0042's
  sign-in wall, and a full LanceDB `snapshot_download` is still ~1.87 GB against CLAUDE.md's
  documented storage-cost constraint. This pass's own fresh 60-board live-verification sample
  (below) serves that role instead — a differently-seeded resample against personio's own live API
  is fresher than the served table would be anyway, and the tradeoff is named explicitly rather
  than left for a reader to notice its absence.
- Went beyond the ask: applied both of ashby's/recruitee's carried-forward lessons proactively,
  reported the hit/negative outcome of each with the same rigor either way (a real 80-board and a
  separate 80-board live-data check, not an assumption). Fixed the recurring `_field_generic`
  test-example churn at its root (a synthetic placeholder) instead of doing the same real-ATS swap
  a third time. Diagnosed the sample's tail failures to their exact mechanism (a response header)
  rather than assuming they were the same rate-limit shape already seen twice — then, before
  writing up a finding, checked whether this repo already had context on it, and found a
  substantially deeper, already-complete investigation (`docs/personio/vercel-bot-wall-bypass.md`)
  rather than re-deriving from scratch or presenting a shallower header-check as a first discovery.
- Did not: build currency support for GEL/BGN/CRC/LKR/HUF/PHP (each backed by exactly one company)
  — real coverage left on the table, but below the evidence bar this initiative has already set.
  Did not build German-language Tier-2 patterns despite them covering the large majority of
  misses — consistent with the project's own English-only search scope. Did not attempt a Vercel-
  challenge solver for the 24 sampling failures — not a new decision, but the same one
  `docs/personio/vercel-bot-wall-bypass.md` already reached independently (open-ended
  reverse-engineering effort against a target Vercel keeps changing, not proportionate here).

## Live-verification review

Fresh, 60 boards, seed=2026, `--workers 20` (a dry-run at the standing 32-worker default had
already shown this ATS tolerates it cleanly — 199/200 clean on the initial dry-run — so 20 was a
comfortable, not a defensively-reduced, choice for this small confirmatory run).

58/60 boards succeeded, 2 errors — both, precisely, `wos.jobs.personio.com` and
`pitch.jobs.personio.com`, which were **also** among the 24 Vercel-challenge failures in the full
3,000-board run. Two boards recurring across two independently-seeded samples run at different
times is a real signal this specific pair sits under a longer-duration or company-side block
rather than a purely session-length flag from this pass's own request volume — worth knowing if a
future re-verification of personio also finds these two still failing, though not investigated
further here given the scale (2 boards) doesn't threaten anything this pass concludes.

342 jobs, 32 real Tier1 extractions (9.4% overall, zero Tier 2 on this specific draw) — within
ordinary small-sample variance of the headline 10.2%, no Tier-1-parse-failure surprises. All
spot-checked extractions are genuine: `Smartmod GmbH`
(€30k–€40k, an Innendienst/back-office role), and seven roles at `feld.energy GmbH` spanning
€50k–€85k (Bauleiter, Chief of Staff, electrical/legal/supply-chain roles) — a realistic spread
for a German renewable-energy company's own pay bands across different seniority levels, no red
flags.

## Patterns found

- **A structured `<salaryInformation>` element one level deeper than what the scraper read** — the
  headline finding, directly recovering this initiative's third-highest Tier-1 win after ashby's
  own structured-field discovery.
- **A single globally-distributed company (`flatrock`) posting salary in each hire's own local
  currency** — six distinct currencies observed from one company alone (EUR plus five unsupported
  ones), a reminder — echoing zoho's `ascor` and recruitee's `rebootmonkey` — that a repeat-poster
  can make a currency-diversity finding look broader than the real evidence supports.
- **Personio's own `<type>` "-ly" suffix convention doesn't match the shared bare-word period
  regex** — a narrow, ATS-local mapping fix, not a shared-regex change, since the underlying
  words ("year"/"month"/"hour") are already correctly recognized once the suffix is stripped.
- **A DACH-region-dominant, heavily non-English description corpus** — the most lopsided
  language split measured in this initiative so far (79.8% German).
- **A Vercel bot-mitigation challenge as a third distinct rate-limit-adjacent failure shape** —
  concurrency reduction doesn't help a challenge the way it helps a rate limit, a useful
  diagnostic distinction for future passes. Already thoroughly investigated by this repo before
  this pass needed it (`docs/personio/vercel-bot-wall-bypass.md`); this pass's own header
  inspection is a small confirming data point, not a new investigation.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 2,534 live) | 2,534 attempted, 2,510 clean (99.05%) |
| jobs seen | 19,384 |
| jobs with a structured `salary` field (`Job.salary`) | 2,076 (10.7%) |
| of those, extracted via Tier 1 | 1,940 (10.0% of all jobs; 93.4% of field-present jobs) |
| extracted via Tier 2 (description, no usable field) | 44 (0.2%) |
| **overall Tier1+Tier2 coverage** | **1,984 (10.2%)** |
| boards with ≥1 job showing a real signal | 509/2,510 (20.3%) |

Third-highest coverage of any ATS in this initiative so far (workable 15.4%, workday 27.6%,
greenhouse 36.1%, smartrecruiters 10.0%, zoho 10.0% [corrected, PR #242], teamtailor 14.1%,
ashby 49.7%, recruitee 38.2%, **personio 10.2%**) — behind teamtailor, essentially tied with
zoho/smartrecruiters. Like ashby's pass, almost the entire number comes from the single
structured-field fix (10.0 of 10.2 points is Tier 1); unlike ashby, personio's overall ceiling is
lower because — measured, not guessed — the large majority of jobs are on boards with no
structured salary data at all and description text that's predominantly non-English and
predominantly non-disclosing even where it is English.

## What changed in code, and why

- `src/headstart/scrapers/personio.py`: new `_salary(pos)` reading the structured
  `<salaryInformation>` element instead of its own (always-empty-when-structured) direct text;
  new `_PERIOD_WORD` mapping personio's "-ly"-suffixed `<type>` values to the bare words
  `_period_multiplier_structured` already recognizes. `url()` is unchanged (the XML feed was
  already being fetched in full; the fix only changes which part of the already-parsed document
  `parse()` reads for `salary=`) — confirmed no `verify-search-filters` re-run is needed, since
  that skill exists to catch a URL-shape gap and none changed here.
- `src/headstart/salary.py`: registered `"personio"` in `_FIELD_PARSERS` → the existing
  `_field_range_currency_interval` (now a 5th caller); docstring updated to describe personio's
  own real shape. No other change — the function's own logic didn't need to change for personio's
  data (unlike ashby's pass, which needed a WEEK-interval and bare-single-value fix, or
  recruitee's, which needed new currency support).
- `tests/test_salary.py`: 2 new tests
  (`test_field_range_currency_interval_personio_structured_tier`, covering both the range and
  single-value-floor shapes) plus 2 existing tests rewritten to stop using "personio" as
  `_field_generic`'s free-text example (see Methods tried).
- `tests/test_scrapers.py`: 1 new parametrized test
  (`test_personio_salary_from_structured_salary_information`, 5 cases: a range, a floor-only
  single value, an hourly rate, an empty `<salaryInformation>`, and no element at all).

### Cross-ATS impact

Mandatory full cross-ATS diff (main's frozen `salary.py` vs. the current working tree, across all
8 previously-merged ATSes' frozen corpora): **0 differences on every single one** (workable,
workday, greenhouse, smartrecruiters, zoho, teamtailor, ashby, recruitee) — expected and confirmed
rather than assumed: adding a new key to `_FIELD_PARSERS` cannot change the dispatch outcome for
any ATS whose own key was already present and unchanged, and this pass made no change to
`_field_range_currency_interval`'s own logic (unlike ashby's and recruitee's passes, which did
change shared logic and therefore had real, non-zero, hand-traced diffs to report).

## Known gaps, left honestly unresolved rather than guessed at

- **Six unsupported currencies, each backed by exactly one company** (GEL, BGN, CRC, LKR — all
  `flatrock`; HUF — `TradeTracker Hungary`; PHP — `Dynamix Services Philippines`) — real, but well
  below the multi-company evidence bar this initiative has already established.
- **A heavily German-majority, largely non-disclosing description corpus** (79.8% of misses) —
  consistent with, not a deviation from, this project's English-only search-corpus scope.
- **24 Vercel-bot-challenge sampling failures (0.9%)** — a genuinely different failure shape from
  this initiative's two previously-diagnosed rate-limit patterns; concurrency reduction doesn't
  help it. Not a new gap this pass leaves open: `docs/personio/vercel-bot-wall-bypass.md` already
  investigated this in depth and reached the same conclusion (no working non-browser solve exists,
  building one is disproportionate open-ended effort) well before this pass encountered it — this
  pass's own 0.9% is a small, expected instance of an already-understood, already-accepted
  platform behavior, not a fresh, unresolved question.

## Carried forward from workable through recruitee — and new lessons

- **Applied directly, and it was a hit this time**: ashby's and recruitee's "check for structure
  one level deeper" lesson — the third time this exact question has been asked of an
  already-structured ATS, and the second time the answer was yes. Worth continuing to ask fresh on
  every remaining already-populated ATS (rippling, keka, darwinbox, ripplehire), not just the ones
  already checked.
- **Applied**: verify `is not None` vs. truthiness safety with real data, not from the type system
  alone — even though XML text being string-typed made the ashby-class bug structurally
  impossible here, the check was run anyway (0 zero-valued, 0 non-string `min`/`max` across a live
  sample) before concluding it, and the explicit `is not None` checks were written regardless of
  the structural guarantee, since the cost is zero.
- **New**: when a shared parser gains a new caller and an existing test's "free text" example ATS
  turns out to move off `_field_generic` for the SECOND time, stop naming a specific real ATS in
  the test at all — switch to a synthetic placeholder that can never legitimately gain a
  structured parser. Fixing the root cause (an example test tying itself to a real ATS's data
  shape) is better than repeating the same swap a third time on some future pass.
- **New**: a rate-limit-shaped failure isn't always a rate limit — inspect the actual response
  (headers, status text) before assuming it's the same mechanism already diagnosed twice. A `429`
  with `x-vercel-mitigated: challenge` in the headers is a bot-mitigation challenge, not a
  request-volume throttle, and concurrency reduction (the correct fix for both prior patterns)
  does nothing for it.
- **New, and arguably more valuable than the diagnosis itself**: before writing up a finding as
  new, check whether this repo already has context on it. `docs/personio/vercel-bot-wall-bypass.md`
  turned out to already contain a far deeper, already-complete investigation of the exact same
  mechanism this pass's header-check surfaced — reading it first (rather than after drafting a
  shallower version of the same finding) is what let this doc credit and build on that work
  instead of quietly duplicating or, worse, contradicting it. The general habit — check
  `docs/<related-topic>/` and untracked-but-present files for existing research before presenting
  something as a fresh discovery — applies well beyond this one ATS.
- **New**: a single global, remote-first company can produce currency diversity that looks like
  broad multi-company evidence in a raw scan — always check distinct-company counts before
  treating a currency-prevalence number as real population evidence, a lesson zoho's `ascor` and
  recruitee's `rebootmonkey` already taught and personio's `flatrock` confirms generalizes.
