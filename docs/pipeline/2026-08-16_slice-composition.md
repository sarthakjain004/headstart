# Slice composition: selection is exonerated; the gap is content, not coverage

**Date:** 2026-08-16 · **Question** (from the session handoff): the served index holds ~290k rows
against 4,279,718 advertised jobs on the ledger's live boards (~6.8%) — do newly-added boards
actually get selected by `board_priority`, or does the slice keep re-scraping the same boards?

## Answer: selection works, completely and fast

Yardstick: boards *added to the committed liveness ledger* in a known window (git history dates
every addition), checked against the cost ledger (`data/state/board_cost.csv`, one row per board
ever scraped — pulled fresh from HF) and matched per-ATS by the key form each side actually uses.

- Boards added in the last week: 9,725. Boards added in the last **two days**: 6,327 —
  **98–100% of them already scraped** on every ATS whose keys match directly (teamtailor,
  trakstar, freshteam, workable, greenhouse, rippling, recruitee — each ≥98%).
- The apparent laggards were measurement artifacts, not gaps. Matching at host level (the
  liveness `url`'s host against hosts inside cost-ledger keys): **workday 98%, zoho 100%,
  personio 100%** of live rows have been scraped. Their raw-key numbers (1%, 1%, 0%) came from
  key-form mismatch — workday cost keys are full URLs, zoho's are hosts, while liveness `tenant`
  is a bare name (the ADR-0049 board_of-guess caveat, biting a measurement this time).
- `join`: 0 of 25,416 live rows scraped — correct, the ATS is in `DISABLED_ATS`. A quarter of
  the ledger's *board count* is deliberately unscraped, but only **1% of its job volume** (45,571
  jobs — join boards are tiny).

The mechanics explain the speed: `pick_boards` gives 30% of the 20k slice to the scored head and
**70% to uniform-random exploration** over everything else. A new (unscored) board's per-run
selection probability is ~14,000/61,000 ≈ 23%; at ~12 runs/day, P(still unscraped after a day)
≈ 4%. Observed <2-day coverage of 98–99% matches the theory.

## So where does 4.28M → 290k go?

Measured on run 31925814898's join stage (the full banked snapshot, 1,051,185 jobs):

- **Tech filter keeps 20.1%** overall — and the volume giants are the least tech-dense:
  workday 8.8% (and workday is **59% of all advertised jobs**, 2.54M), successfactors 10.3%,
  vs ashby 42%, eightfold 41%, greenhouse 33%.
- **English gate**: 13,900 of 209,592 scanned tech docs dropped (~6.6%) — English ≈ 93%.

Volume-weighting each ATS's advertised jobs by its measured tech rate projects the reachable
corpus (tech ∩ English, enabled ATSes) at roughly **~650k jobs** — an estimate, not a
measurement: the slice's tech rates are applied to whole-ledger volumes, and the liveness `jobs`
column is a point-in-time count from each board's last validation.

Against that, the 290k index is **~45% of reachable**, not 6.8% — the "6.8%" compared tech-only,
English-only stock against an all-jobs, all-languages denominator.

## The remaining open question

Why ~45% and not ~100%? Candidate explanations, none yet measured: the liveness `jobs` counts
overstate current stock (stale, or counting location-variants the scrape dedupes); per-tenant
listing caps (e.g. freshteam's widget caps at 1000/tenant); scrape misses on unstable providers
(the #142 class — eightfold alone was oscillating ~6% of its rows until PR #144); tech-filter
misses on jobs whose description arrives only via a later detail pass; and steady-state dynamics
(new-doc inflow is ~1,533/run ≈ 18k/day against a ~290k stock — if that inflow is the true
posting rate, the stock is consistent with a ~16-day mean posting lifetime, i.e. the index may be
*at* equilibrium and the projection wrong). Distinguishing these needs a per-board
advertised-vs-indexed comparison on a sample of large boards — a follow-up worth its own session.

## Method notes

- Ledger additions dated via `git rev-list --before` + CSV diffs (the liveness ledger is
  committed; the repo is authoritative for it — no HF pull needed).
- `board_priority.csv` / `board_cost.csv` pulled fresh from HF before reading (source-of-truth
  rule).
- Per-ATS breakdown first, always: both headline anomalies (47% "never scraped", the inverted
  age gradient) evaporated under the ATS split — they were mix effects of key-form mismatches
  and the disabled join ATS.
