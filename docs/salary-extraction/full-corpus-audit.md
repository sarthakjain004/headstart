# Full-corpus salary recall audit — first pass

This is the first entry in a new, ongoing initiative distinct from the per-ATS passes above
(`workable.md` through `sensehq.md`): rather than sampling a bounded population per ATS, fetch
essentially every job description in the HF-hosted description store (ADR-0050) and measure
`headstart.salary.extract()`'s real recall against the full, current production corpus at once —
the "does the module actually see everything a human would" check the per-ATS passes' own bounded
samples can't answer on their own. See `salary-corpus-audit-task` in the assistant's own memory for
the full brief; this doc covers only what this first pass found and fixed.

## Method

1. **Fetched the full description store** (`data/descriptions/`) for all 18 already-merged ATSes
   from the `imPoseidon/headstart-index` HF dataset — 391,134 jobs total, ~357MB compressed. Not a
   sample: every `{id, description}` record any ATS has ever had scraped and stored, read via the
   real production `update_descriptions.read_store()` (same fragment-precedence logic the pipeline
   itself uses, not a reimplementation).
2. **Gated on English** via the real production `doc_prep.is_english()` (the same langdetect-based
   check `embed_plan`/`embed_run` apply before a job ever reaches the index) — 24,694 jobs (6.3%)
   are non-English and correctly out of scope, matching this initiative's own established
   English-only search-corpus scope (CLAUDE.md).
3. **Ran `extract()`** over every remaining English job's description alone (no raw `salary` field
   — Tier 1 coverage is already separately measured per ATS in each pass's own doc; this audit is
   about Tier 2 recall specifically). 90,731 hits (24.8% of the English corpus).
4. **A language-independent numeric sweep**, run only where `extract()` found nothing: currency
   symbols (`$€£₹¥₩₽₴₦₪₫`) or a broad ISO-4217 code list (deliberately wider than `salary.py`'s
   own supported set — finding an unsupported real currency is exactly the point) directly
   adjacent to a digit sequence, or a label word (LPA/CTC/lakh/crore/"per annum"/"per hour"/etc.)
   within 40 characters of a digit. **Two design mistakes, found and fixed before trusting any
   count**: v1 used a generic "any 3 capital letters adjacent to a digit" heuristic for currency
   codes, which collided constantly with ISO/GDPR/PCI-DSS/NIS2 standards references and URL-slug
   digit fragments (124,264 false "misses" on a 5,000-row spot-check); v1 also allowed bare label
   words with no digit anywhere nearby, catching an ML term ("CTC" = Connectionist Temporal
   Classification), a French word ("lac" = lake, not lakh), and travel-frequency phrasing ("1-2
   times per year"). v2 (a curated real currency-code list, and a required digit within a tight
   window for every label word) cut this to 50,014 — still not directly readable at scale.
5. **Auto-triage** on the 50,014, using categories every prior ATS pass's own no-signal audit has
   independently, repeatedly confirmed as correctly-declining (funding/valuation/revenue
   boilerplate, benefit/perk amounts, candidate-facing "state your own CTC" asks, company-scale
   metrics, legal/compliance figures): 30,164 auto-declined, 19,850 residual.
6. **Manually read** a random, stratified 350-row sample of the residual (context windows around
   each trigger, not full descriptions — for throughput) plus, for every real candidate pattern
   the read surfaced, directly re-verified against the real code (never trusted a "this looks like
   it should work" read without running `extract()` on the exact text) and measured full-corpus
   prevalence and distinct-company counts before building or declining anything, per lesson 42's
   own established discipline.

## What the manual read found

**Two real, narrow `salary.py` bugs, fixed this pass** (see `DERIVATIONS_VERSION`'s own v6 comment
in `src/headstart/ingest/doc_prep.py` for the terse version):

- `_LABELED`'s "for X Y Z" filler cap (originally 3 words) couldn't reach real phrasing like "for
  this role across Switzerland" (4 words) — the whole match silently failed, not just the filler.
  Widened to 4 words, calibrated against the real corpus's own filler-length distribution (79.8%
  of real occurrences need only 1-3 words; the next-largest bucket, 4 words, is exactly this
  shape). **Deliberately not widened further** — a first attempt at 8 words fixed the same real
  gap but also reached distant, unrelated mentions in the same description two other ways: it
  silently mis-parsed a European trailing-currency-symbol format ("44.625 € - 65.450 €", capturing
  only the floor and losing both the ceiling and the currency, since the specialized lower-priority
  pattern that handles that shape correctly never got a chance to run — the identical
  cascade-precedence trap trakstar's own pass already found and reverted once, in a different
  guise), and it let a source-data typo (a missing separator, "$190,000 $250,000") turn a
  previously-usable single value into a total loss. Both were found via the mandatory full
  cross-ATS diff, not assumed safe from the isolated fix alone — see `salary-extraction-progress`
  memory lessons 63-64 for the blow-by-blow.
- `_BARE_BETWEEN` only ever recognized a currency SYMBOL (`$£€₹`), never a CODE — "between CAD
  82,000 and CAD 100,000" (45 real occurrences) and the rarer dash-separated hybrid "between CAD
  82,000 - CAD 100,000" (11 occurrences) fell through entirely. Fixed by widening the pattern
  itself to accept a code, and to accept a dash as well as "and" — kept inside `_BARE_BETWEEN`,
  not folded into `_LABELED`'s own connector list, for the same cascade-precedence reason above.

**One real currency-attribution bug, fixed this pass — and fixed twice, the first attempt wasn't
general**: a bare "CA$"/"C$" prefix (Canadian dollars) was silently defaulting to USD via
`_guess_currency`'s own generic "$" fallback — 30 occurrences, 10 distinct companies. A real
guidepoint posting stated the identical $105,000-$145,000 range twice, once with "CA$" and once
with a trailing "CAD" code, and the two disagreeing currency guesses made `_resolve()` decline the
whole thing as ambiguous even though the dollar amounts themselves agreed. **The first fix
(`_CA_DOLLAR`, a separate lookup against the overall matched text) only worked when an unrelated
earlier part of the SAME match happened to have already swallowed the "CA"/"C" letters as generic
filler text — real for the guidepoint phrasing that motivated it, not real for other, differently-
worded real phrasings with the identical prefix** (code review caught this directly, testing a
natural rephrasing that exposed it). Fixed properly the second time by folding the "CA$"/"C$"
prefix into `_SYM` — the ONE shared symbol fragment every pattern in the file that captures a
currency symbol now interpolates, rather than each independently spelling out `[$£€₹]` — so every
caller reliably captures the prefix as part of its own match, not by coincidence of surrounding
text. This also meant the filler exclusion added for "between" (see above) needed a matching
exclusion for "a word immediately followed by a currency symbol", for the identical reason: the
filler's own generic word-matching was swallowing "CA" before `_SYM` ever got a chance to see it.

**A large, well-evidenced body of missing-currency findings, deliberately NOT built this pass** —
see "Deferred: new currencies" below.

**Everything else read traced to an already-correctly-declining reason**, matching this
initiative's own repeated finding across every per-ATS pass: multi-region/multi-level postings
stating several genuinely different real ranges (the dominant shape in the residual sample, and
the mechanism behind every one of this fix's own "lost" cases in the full-corpus diff — see
Verification below), funding/revenue/valuation boilerplate, benefit/perk amounts, vague
"competitive"/"CTC — best in industry" phrasing with no figure, and non-English text.

## Verification — the full-corpus diff, not a sample

Every prior ATS pass's own "mandatory full cross-ATS diff" ran against a frozen, bounded sample
per ATS (up to ~3,000 boards). This pass ran the SAME diff methodology (`repr()` comparison of two
independently-loaded `salary.py` module instances, old = `main` at the fix's own parent commit,
new = the working tree) against the ENTIRE 391,134-job description store — the most complete
verification run this initiative has ever done.

| metric | count |
|---|---:|
| gained (None → a value) | 366 |
| lost (a value → None) | 21 |
| changed (a value → a different value) | 153 |

**All 21 "lost" cases directly confirmed genuine, not assumed** — and confirmed against every
pattern capable of independently finding a span (`_LABELED`, `_BARE_RANGE`, `_BARE_BETWEEN`,
`_MIN_MAX_BAND`, `_LEVEL_BAND`, `_BARE_HOURLY_OR_DAILY`, `_BARE_RANGE_CODE`), not `_LABELED`
alone: a first verification pass checked only `_LABELED.finditer()` and found 8 of 21 cases
"suspicious" (zero matches) — reading one directly (a real Docker/ashby posting: "Canada:
CA$225,300 – CA$361,750 + equity United States: $160,900 – $260,700 + equity") showed the real
mechanism was `_BARE_RANGE` (a bare-symbol range with no label at all), which the CA$ fix now lets
correctly recognize BOTH regional ranges as genuinely different currencies — the identical
multi-region-ambiguity pattern, just reached through a different pattern than the filler-length
fix's own motivating examples. Checking all patterns, every one of the 21 finds 2+ distinct
`(lo, hi)` pairs — real, different stated ranges (the established multi-region/multi-level
pattern), now correctly caught where the pre-fix code was simply blind to the second mention
(either the filler was too short to reach it, or the second mention's own currency wasn't
recognized as genuinely different). Losing a wrong "confident" single answer to a correct
"ambiguous" decline is the intended outcome, not a regression — see eightfold.md's own
"twilio/ericsson/ford/ngc" precedent for the same call made the same way.

**Of the 153 "changed" cases**: 147 are pure currency corrections (identical `min_annual`/
`max_annual`, only the currency label improved — the CA$ fix, general this time; confirmed spread
across 3+ distinct companies on recruitee alone, not one repeated template). The remaining 6 are
genuine value shifts, all on `workday`, all individually read — none is a fabricated or
implausible number, every figure is genuinely present in its own job's description — but **not
all the same mechanism**, an inaccuracy an earlier draft of this section claimed (asserting one
uniform "`_mutually_consistent()` merge or tie-break" story) and Spec-axis code review caught by
independently re-tracing two of the six against the real descriptions; all six are now fully
retraced, not just the two the review sampled:

- **`teliacompany` and `blackrock` are genuine bug fixes, not multi-region cases at all.** Each
  description states only ONE range. `teliacompany`'s "3500 - 5500 Eur" (monthly) had its floor
  ("3500") swallowed by the OLD filler's unbounded `\w+` treating the number itself as a filler
  "word", truncating the match to a ceiling-only reading. `blackrock`'s "salary range for France
  is 97500 - 147500 EUR" had the SAME shape of undercapture (old only kept "147500"). The
  `[^\W\d]` filler guard (this pass's own digit-exclusion fix, built for the CHF-Switzerland case)
  independently fixes both — a real, useful side effect not specifically called out until this
  correction found it.
- **`crowdstrike` (both postings) and `ciena` share a real, deeper mechanism this pass does NOT
  fix: a genuine second regional range exists in the same description, but is found only by a
  DIFFERENT, lower-priority pattern than the one that already "succeeded", so the cascade never
  even looks at it.** `crowdstrike`: "for all U.S. candidates is $120,000 - $180,000" and "in
  Canada is $115,000 - $165,000 CAD" are both real — `_LABELED.finditer()` finds only the Canada
  one, since the U.S. mention's own filler contains a second "for" AND a period inside "U.S.",
  either alone enough to stop the filler's word-matching from reaching past it (confirmed by
  testing each condition in isolation). `ciena`: "annual pay range for this position in Canada is
  C$89,100-C$142,300" and a separate, unlabeled "$127,100-$203,000" are both real —
  `_LABELED.finditer()` finds only the Canada one (it has an actual label; the other doesn't), and
  `_BARE_RANGE` — which WOULD independently find the second, unlabeled one too — never runs at
  all, because `from_description()` stops at the first tier that returns anything. In both cases,
  `_resolve()` never reaches its own tie-break or mutual-consistency logic, because it only ever
  sees ONE span from ONE tier — which region "wins" is an accident of which tier's own pattern
  happens to reach it first, not a principled choice between two real candidates. **Carried
  forward, not fixed this pass**: this is a real, if narrow, instance of a more general
  limitation — matches across DIFFERENT tiers are never cross-checked against each other for
  mutual consistency, only matches WITHIN the same tier are. Worth a future pass's own attention
  if it recurs with real multi-company evidence, not redesigned here on 2 examples.
- **`linklogistics` is the CDN-currency-label finding already documented under "Deferred: new
  currencies"'s own note** (the word "CDN" for Canadian dollars, not "CAD" the code or "CA$" the
  symbol) — the same cross-tier blindness as the pair above (`_LABELED` finds the CDN-labeled
  "150,000-$200,000" alone; `_BARE_RANGE`'s own separate "$185,000-$225,000" match never runs),
  compounded by "CDN" itself not being a recognized currency marker, giving `currency=None` on the
  one span that IS reached. A real, if unusually layered, single example — not built into a
  currency addition on its own (1 company, well below the evidence bar the "Deferred" table's own
  candidates already clear far more convincingly).

## Deferred: new currencies

The sweep and its auto-triage surfaced real, multi-company evidence for roughly 20 currencies
`salary.py` doesn't support yet, several with evidence far exceeding any single ATS pass's own
prior currency addition:

| code | jobs | companies | | code | jobs | companies |
|---|---:|---:|---|---|---:|---:|
| PHP | 31 (after stripping the "PHP the language" false positive — see below) | 14 | | CZK | 93 | 33 |
| RON | 64 | 13 | | HUF | 72 | 23 |
| SGD | 258 | 10 | | MXN | 24 | 12 |
| JPY | 23 | 11 | | DKK | 26 | 7 |
| BRL | 17 | 8 | | SAR | 28 | 6 (needs re-verification — see below) |
| MYR | 135 | 3 | | ILS | 4 | 3 |
| NOK | 9 | 4 | | QAR | 13 | 2 |
| IDR | 1 | 1 | | THB | 3 | 2 |
| TWD | 27 | 4 | | KRW | 2 | 2 |
| NZD | 1 | 1 | | ZAR | 4 | 2 |

**None of these were built this pass. The one reason that applies to every single one of them,
including the currencies with no other blocker at all**: `_bounded()`'s plausibility floor/ceiling
is currently keyed only on the currencies `salary.py` already supports, each calibrated against
real minimum-wage/salary data at the time it was added (recruitee's own PLN/CHF precedent,
ADR-0082) — not reused wholesale from USD-shaped bounds. Adding a new `_CURRENCY_CODES` entry
without ALSO adding its own calibrated bound would either reject every real value for that
currency (an implausible-looking floor) or, worse, silently accept implausible ones — this is real
work for every currency in the table below, no exceptions, and none of it happened this pass.

**On top of that shared reason, several — not all — of these currencies have an ADDITIONAL,
currency-specific blocker, checked directly, not assumed uniform across the table**:

- **PHP and, likely, SAR have a real acronym-collision trap**: "PHP" (Philippine Peso) collides
  constantly with PHP the programming language, especially in numbered skill lists ("1. PHP
  2. MySQL 3. AWS") — a naive "PHP adjacent to any digit" sweep found 678 false "occurrences"; a
  stricter 3+-digit-amount filter cut this to 31 genuine ones. "SAR" likely has the same problem
  (one sampled match was `NOKIA 7705 SAR`, a telecom equipment model suffix, not Saudi Riyal) and
  needs the same individual re-verification before its 28/6 count is trusted.
- **CZK/HUF/RON's dominant real shape is a multi-country listing in ONE description**
  ("Czechia: 1,400,000 - 1,800,000 CZK - Greece: 50,000 - 62,000 EUR - Hungary: ..." — a real,
  common pattern for remote roles hiring across many European markets at once). This is
  architecturally NOT the same problem as "add a supported code": `SalarySpan` has no way to
  represent several genuinely different, all-correct values for different countries, and simply
  adding the currency code risks turning an honest decline into a wrong, arbitrarily-picked single
  country's figure — worth verifying `_mutually_consistent()` still correctly declines these once
  the codes ARE added, not assuming it does.
- **JPY/MXN/TWD/IDR have naturally large nominal salary figures** (weak currencies relative to
  USD) that would need their own genuinely different floor/ceiling, not a scaled-down USD
  assumption — a calibration question specifically about the SHAPE of the bound, on top of the
  baseline "needs a bound at all" reason above.
- **MYR/BRL/IDR have their own locale-format nuances**: MYR's dominant range separator is "~" not
  "-"/"–"; BRL and IDR use "." as a thousands separator (European-style), interacting with
  `_num()`'s own locale-decimal disambiguation (lesson 36) in ways that need individually
  checking, not assumed safe by analogy.

**SGD (258 jobs, 10 companies — the single strongest line in the whole table) hits none of the
four currency-specific blockers above** — its own real evidence (`"Compensation: SGD 2,800 -
SGD 3,500"`-shaped mentions, already sampled during the sweep) is clean, unambiguous, and not
multi-country-listed in what was read. It is deferred for the SAME shared reason every currency
here is — no calibrated plausibility bound exists for it yet — not a currency-specific issue.
Naming this explicitly rather than letting SGD sit undifferentiated among currencies that DO have
real, additional problems: this initiative's own established precedent (AED shipped same-pass on
keka, PLN/CHF shipped same-pass on recruitee, both with less evidence than SGD's own 10 companies)
would normally have built at least SGD by now. It wasn't, this pass, to keep this fix's own diff
reviewable and traceable to exactly what it changed — a deliberate batching choice, not a verdict
that SGD itself needs more evidence or has some problem the table's other rows don't.

This is real, substantial, well-evidenced follow-on work — likely its own PR (or several, given
SGD alone could reasonably ship on its own once calibrated) — not folded into this pass.

## Instruction-adherence self-assessment

- Fetched from HF, not a possibly-stale local copy: yes — full `snapshot_download` of
  `data/descriptions/*`, 391,134 jobs, not a sample.
- Ran the extraction over the same full corpus: yes.
- Read real misses, not just measured a percentage: yes — 350 residual rows read directly, every
  real candidate pattern individually re-verified against real code before being counted as
  evidence, matching lesson 42.
- Nothing regressed: yes — the mandatory full cross-ATS diff ran multiple times across this pass's
  own iteration (catching three real regressions before they shipped — two in the filler-length
  fix, one in the CA$ fix's own first attempt — all reverted/fixed in the same pass, not left in),
  and the final, shipped state's own 21 losses and 153 changes were each individually traced to
  their real mechanism (see Verification above), not assumed safe from the aggregate counts alone
  — including catching that the "changed" bucket's own first-drafted mechanism claim was itself
  inaccurate for at least one case (spec-axis code review), and correcting it rather than leaving
  a confident-sounding but wrong explanation in place.
- New tests: 10 regression tests added to `tests/test_salary.py`
  (`test_description_labeled_filler_reaches_a_short_geography_clause`,
  `..._still_declines_a_reachable_second_mention`, `..._does_not_reach_a_distant_second_mention`,
  `test_description_between_code_and`, `..._code_dash`, `..._symbol_still_works`,
  `test_description_ca_dollar_prefix_resolves_as_cad`,
  `..._prefix_works_without_a_swallowing_filler` [added after code review showed the first CA$
  test alone couldn't distinguish a general fix from an accidental one],
  `..._bare_range_multi_region_still_ambiguous`, `..._and_trailing_cad_code_agree`).
