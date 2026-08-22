# rippling

## Methods tried

- **Live board count re-measured, not assumed**: the plan's 2,941 figure was stale. Current,
  properly deduplicated (`config.load_active_companies`, which applies the liveness-ledger dedup
  CLAUDE.md warns about) count: **2,062 live boards**, below the 3,000 sampling cap — this pass
  sampled the full live population, not a subset.
- **No bounded adapter existed yet**: `rippling.has_detail_pass = True`, and `fetch_raw()` bakes
  the full per-posting detail fan-out into the listing call itself (every job on the board, not a
  capped subset) — the sampling script's own safety check (`has_detail_pass and adapter is None`)
  correctly refused to run rather than risk an unbounded fan-out across 2,062 boards. Built
  `_fetch_rippling` in `scripts/enrich/salary_sample.py`, mirroring workday's/smartrecruiters'
  shape (one direct listing GET, cap detail fetches at `_DETAIL_FETCH_CAP`=3/board via the
  scraper's own `_detail()`) rather than zoho's uncapped shape — rippling's listing is cheap like
  zoho's, but its `fetch_raw()`'s *fan-out*, not its listing, is what's unbounded, so the
  workday/smartrecruiters cap-the-detail-calls pattern is the right fit, not zoho's.
- **Dry-run first**: 40 boards, seed=1, 32 workers, 1/40 errored — no rate-limit pattern, cleared
  to sample the full population at the standing default concurrency.
- **Checked for structure one level deeper** (asked of every already-populated ATS per this
  initiative's own recurring question — a hit on ashby, a confirmed-flat-miss on recruitee, a hit
  again on personio): rippling's raw `payRangeDetails` is itself a list, each entry carrying its
  own `location`, and `_pay_range()` (rippling.py) only ever reads the FIRST entry
  (`(ranges or [{}])[0]`) — the same general risk shape as ashby's/personio's real hits. Checked
  directly against live data (105 real jobs sampled across 60 boards, not assumed): only 1/105 had
  more than one `payRangeDetails` entry, and that one case (`threeone`) had *identical*
  `rangeStart`/`rangeEnd` across both its "Main Office" and "Remote (United States)" entries — no
  actual value lost by reading only the first. **Confirmed-flat-miss, joining recruitee's** — real
  structure exists, but reading only the first entry doesn't lose real information here.
- **Tested the raw unmapped value through `from_field()` before building anything** (this
  initiative's own "test before building a translation layer" lesson, learned the hard way on
  personio's own pass): rippling's real `_pay_range()` output ("62000-70000 USD YEAR",
  "25-25 USD HOUR") already parses correctly end-to-end via the existing
  `_field_range_currency_interval` — EXCEPT the HOUR case, which `_field_generic` (rippling's
  current, unmapped fallback) doesn't recognize as a period marker and returns `None` for, while
  the calibrated parser correctly annualizes it. Registering rippling in `_FIELD_PARSERS` is the
  entire scraper-adjacent fix — zero changes to `rippling.py` itself, zero changes to the shared
  parser's own logic.
- **Read real misses** from the full sample's "currency-shaped but not extracted" bucket (see
  Coverage below) — 30 examples read directly, categorized by hand. Found one real, well-evidenced
  Tier-2 gap ("an hour" as a bare period marker — see Patterns found) and confirmed several
  real, correctly-declined non-findings (see below).
- **Checked "a day"/"a month" as siblings of the same bare-indefinite-article shape "an hour"
  and "a year" (already supported) use**, before assuming the same fix generalizes: real, negative
  result. All 7 "a day" and the 1 "a month" occurrence found near a digit in a no-signal
  description were false positives relative to salary — work-pace/volume counts ("100+ dials a
  day", "16-20 patients a day", "1,000 net-new ads a month"), not wages. Not added.
- **Live-verified twice**: the full 2,062-board sample itself (all live fetches), plus a
  fresh, differently-seeded 50-board re-sample (seed=909) after the code changes landed.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 2,062 (the full live population, below
  the cap).
- Measured both required percentages: **yes** — 24.3% field, 46.4% overall (Tier1+Tier2).
- Live-verified after code changes: **yes**, twice (see Live-verification review).
- **Audited the no-signal bucket for language-independent currency-shaped content before trusting
  the coverage number as a ceiling** — the standing methodology this initiative adopted after
  personio's own post-merge audit (`docs/salary-extraction/README.md` step 3, `salary-extraction-
  progress.md` lesson 39): 87.6% of no-signal jobs have no currency-shaped content anywhere in the
  description at all (genuinely undisclosed); 12.4% (348 jobs, 6.7% of all jobs) had currency-
  shaped content that wasn't extracted — read in a 30-job sample, not assumed clean.
- Went beyond the ask: built the sampling script's `_fetch_rippling` adapter (needed once, now
  reusable); measured the "structure one level deeper" question against 105 live jobs rather than
  a handful; checked two candidate period-marker siblings ("a day"/"a month") the read surfaced as
  plausible before assuming either generalized, and found real evidence against both.
- Did not: chase the 5 remaining "an hour" misses left after the fix landed (each its own distinct,
  single-example connector-phrasing gap — "Hourly Rate:" as an unrecognized label, "starts at" vs.
  the recognized "starting at", "offers" as an unrecognized connector, a fully bare mention with no
  label at all, and one genuine "up to $X" ceiling-only single value the schema can't represent by
  design) — below this initiative's own multi-example bar for each individually.

## Live-verification review

Two rounds, against real current `ats.rippling.com`/`api.rippling.com` hosts each time, never a
replay of the frozen capture:

1. Dry-run before the full sample: 40 boards, seed=1, 32 workers, 1/40 errored (a single board
   fetch failure, not a pattern) — cleared to sample at the standing default concurrency.
2. After the field-parser registration and the "an hour" fix: 50 fresh boards, seed=909, 32
   workers, 0 errors, coverage (53.8% either-tier, coarse hint) consistent with the full sample's
   own 55.0% — confirms the code works against live data, not just the frozen capture.

## Patterns found

- **`_field_range_currency_interval` registration (Tier 1 fix)**: rippling's own `_pay_range()`
  already assembles "RANGE + CODE + optional period" ("62000-70000 USD YEAR",
  "25-25 USD HOUR") — the same shape lever/recruitee/teamtailor/ashby/personio already share. No
  scraper change needed; the raw format was already correct, only the dispatch registration was
  missing.
- **"$X an hour" (Tier 2 fix)**: a bare indefinite-article period marker, structurally identical
  to the already-supported "$X a year" (zoho's pass) but for hours instead — 26 real occurrences
  across 19 distinct companies in the no-signal bucket, e.g. "$16 an hour", "Starting pay is
  $18.50 an hour", "between $24.00 and $29.00 an hour". Added to `_PERIOD_HINT`; no change needed
  to the hourly/annual classification logic in `_period_from_window`, since "an hour" already
  contains "hour" as a substring and is caught by the existing check.
- **Genuine multi-value ambiguity, correctly surfaced, not a bug**: the mandatory cross-ATS diff
  (below) found 4 cases across 3 other already-merged ATSes where the "an hour" fix made a
  previously-invisible hourly mention visible, and that mention genuinely disagreed with another
  real figure already in the same description (a second "between $X and $Y an hour" range at a
  different rate; a company's own precomputed "($45,000 to $50,000 if annualized)" parenthetical
  next to its hourly rate; a stated annual figure for an explicitly part-time role that doesn't
  match a standard 40hr/week FTE annualization of its own hourly rate; an "on-target" wage
  figure next to a separately-stated base hourly rate). Each hand-traced to its real source text,
  not just counted — every one is the same pattern this initiative's personio pass already
  established: a previously-masked genuine second figure, not new fabrication or a wrong number.

## Coverage

| metric | value |
|---|---:|
| boards sampled (of 2,062 live) | 2,062 attempted, 2,058 clean (99.8%) |
| jobs seen | 5,194 |
| jobs with a structured `salary` field (`Job.salary`) | ~1,272 (24.5%, coarse count) |
| of those, extracted via Tier 1 | 1,260 (24.3% of all jobs) |
| extracted via Tier 2 (description, no usable field) | 1,149 (22.1%) |
| **overall Tier1+Tier2 coverage** | **2,409 (46.4%)** |
| boards with ≥1 job showing a real signal | 1,307/2,058 (63.5%, coarse hint) |

**Second-highest coverage of any ATS in this initiative so far** (workable 15.4%, workday 27.6%,
greenhouse 36.1%, smartrecruiters 10.0%, zoho 10.0%, teamtailor 14.1%, ashby 49.7%, recruitee
38.2%, personio 10.5%, **rippling 46.4%**) — behind only ashby. Unlike ashby (where nearly the
entire number is Tier 1), rippling splits close to evenly between Tier 1 (24.3 points) and Tier 2
(22.1 points) — both a well-populated structured field on a meaningful share of boards, and a
description corpus that already skews toward the familiar English phrasings this initiative's
prior passes have already built patterns for, needing only the one narrow "an hour" addition to
reach this level.

## What changed in code, and why

- `src/headstart/salary.py`: registered `"rippling"` in `_FIELD_PARSERS` → the existing
  `_field_range_currency_interval` (now a 6th caller); docstring updated with rippling's own real
  shape. Added `\ban\s+hour\b` to `_PERIOD_HINT`'s hourly alternatives — no other logic changed;
  "an hour" already contains "hour" as a substring, so `_period_from_window`'s existing
  classification (`"hour" in hint`) needed no change.
- `scripts/enrich/salary_sample.py`: new `_fetch_rippling(scraper)` adapter (bounded at
  `_DETAIL_FETCH_CAP`, mirroring workday's/smartrecruiters' shape), registered in
  `_DETAIL_ADAPTERS`; imports `headstart.http` and `USER_AGENT` (previously only `BaseScraper` was
  imported from `headstart.scrapers.base`).
- `tests/test_salary.py`: 2 new tests —
  `test_field_range_currency_interval_rippling_structured_tier` (the two real raw formats) and
  `test_description_an_hour_period_marker` (the real Tier-2 phrasing).

### Cross-ATS impact

Mandatory full cross-ATS diff (main's frozen `salary.py`, `a7f6750` — post-personio-correction —
vs. this working tree, across all 9 previously-merged ATSes' frozen corpora): real, non-zero, and
every difference hand-traced to its exact source text, not just counted. Net: workable +6/−0,
workday +28/−0, greenhouse +163/−2 (1 changed), smartrecruiters +13/−0, zoho +12/−1, teamtailor
+11/−0, ashby +14/−0 (1 changed), recruitee +3/−0, personio +0/−0/0 changed (personio's own German
corpus has no "an hour"-shaped English mentions, and isn't affected by a `rippling`-keyed field
registration). All 4 losses/changes trace to the same mechanism described in Patterns found — a
newly-visible genuine second figure, not a wrong extraction. A ~240-job net gain across the
initiative's already-shipped ATSes from one narrow, well-evidenced period-marker addition.

## Known gaps, left honestly unresolved rather than guessed at

- **5 remaining "an hour" misses**, each its own distinct single-example connector-phrasing gap
  (see Instruction-adherence above) — below this initiative's own multi-example bar individually;
  chasing each would mean writing 4-5 narrow, single-purpose regex branches for one example each.
- **"a day"/"a month" as bare period markers** — deliberately NOT added. Real, measured evidence
  (8 occurrences total) shows this specific shape is dominated by work-pace/volume counts near an
  unrelated number, not wages — the opposite conclusion from "an hour"'s own evidence.
- **The part-time-hours/standard-FTE annualization mismatch** (the redliontruckstop case in Patterns
  found) — a real, but structural and pre-existing limitation: this module always annualizes an
  hourly rate at the standard 40hr/week convention, with no way to know a role is actually
  part-time from the description alone. Correctly resolves to `None` (declines rather than picks a
  disagreeing figure) when a company's own stated annual total contradicts the standard-FTE
  annualization of its stated hourly rate — the safe, no-fabrication outcome, not a fix for the
  underlying limitation itself.

## Carried forward from workable through personio — and new lessons

- **Applied**: the "check for structure one level deeper" question, asked a fifth time now (ashby:
  hit, recruitee: confirmed-flat-miss, personio: hit, rippling: confirmed-flat-miss) — checked
  against 105 real live jobs, not assumed from the shape of the JSON alone.
- **Applied**: "test the raw unmapped value through `from_field()` before building any translation
  layer" (personio's own hard-won lesson) — found registering rippling needed zero scraper changes
  and zero new parser logic, only a one-line dispatch registration, because this check was run
  first instead of assumed.
- **Applied**: the mandatory full cross-ATS diff, now against personio too (9 ATSes, not 8) since
  personio's post-merge correction is part of `main`. Every difference hand-traced to real source
  text, per the discipline this initiative adopted after finding a real crash and a real
  regression the same way on personio's own correction pass.
- **New**: a bare indefinite-article period marker ("a year", now "an hour") generalizes safely
  ONLY when checked per-period-unit against real evidence, not assumed to extend uniformly — "a
  day"/"a month" looked like the obvious next siblings but real data showed the opposite signal
  (dominated by false positives). Measure each specific phrasing's own evidence before adding it,
  even within what looks like one shape.
- **New**: a detail-pass ATS whose `fetch_raw()` bakes in an uncapped fan-out isn't automatically
  a "build a zoho-style uncapped adapter" case — check whether it's the *listing* or the *fan-out*
  that's unbounded before choosing which existing adapter shape to mirror. Rippling's listing is
  cheap (like zoho's) but its fan-out is what's unbounded (like workday's/smartrecruiters'
  problem, for a different reason) — the right adapter shape follows the fan-out's cost, not the
  listing's.
