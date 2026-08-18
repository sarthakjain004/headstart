# 19-run log review — what PR #160 did *not* fix

Source: the logs of the 19 completed pipeline runs from 2026-08-16 13:50 UTC to 2026-08-18 03:39 UTC
(835 log files, 26 MB), plus three targeted investigations and live re-probes. PR #160 fixed five
things; everything below is what the same review turned up and left on the floor.

Each item is marked **measured** (a number taken from the logs or a live probe) or **inferred**
(reasoned from code, not observed). Do not quote an inferred number as fact.

---

## A. The headline: the served index is shrinking

- [ ] **A1. The index nets −388 rows over 19 runs and nobody would know.** *(measured)*
  285,304 → 284,916 rows across ~40 h. True new rows ≈ 7,935 against ≈ 8,304 evictions. The
  accounting balances in all 19 runs: `before + add − evict − upgrades = post-sync`.
  This is the thing to fix; the pipeline is treading water at ~285 k against the 4.28 M the ledger
  advertises. Root cause **not** established — the review only proved the arithmetic, not the why.
- [ ] **A2. `plan: add N, evict M` is actively misleading.** *(measured)*
  `add` silently includes upgrade-replacements, so a run that shrank by 147 rows logs
  "add 855, evict 723" and reads like +132 growth. Nothing logs the net row change.
  Cheap fix, high value: log `net`, and split `add` into new vs upgrade.

## B. Eightfold — ~11–12 k rows permanently unevictable

- [ ] **B1. `_MAX_SWEEPS = 3` is calibrated to the wrong miss rate.** *(measured, live probe)*
  `eightfold.py` assumes a ~6 % per-sweep miss; a clean sequential crawl of `caci.eightfold.ai`
  measured **13.9 %** (1,709 rows fetched, 1,471 distinct). At p=0.139 the probability a board of N
  postings comes back whole is (1−p³)^N, which predicts the observed exclusions almost exactly:
  micron (N=2,666) 19.0/19 predicted vs 19 observed; nvidia 19.0 vs 19; qualcomm 18.9 vs 19;
  caci 18.8 vs 19; hp 16.2 vs 17; bms 16.0 vs 16.
  These boards are excluded from the eviction scope on essentially every run, so their stale rows
  can never leave. ~6,700 rows on the four always-excluded boards; ~11–12 k (~4 % of the table)
  including the 15–18/19 near-permanent ones — *estimate*, since the logs print only the ledger's
  top 10 (micron 2,031 and nvidia 1,968 are directly measured).
- [ ] **B2. Prefer the sitemap over the paginated API (issue #150 already scopes this).**
  *(measured, live probe)* caci's sitemap yields 1,709 distinct ids = `data.count` exactly;
  qualcomm 1,970 vs 1,969. Batch-generated, so replica ordering cannot touch it, and it replaces
  171–513 API calls per board with one fetch — which also collapses B3's exposure.
  Needs the rot guard the evaluation doc already calibrated: gate on `|sitemap ids| ≈ data.count`
  **and** `max(lastmod)` within 7–14 days (hp.eightfold.ai's sitemap is ~2 years stale).
- [ ] **B3. A rate-limit wall aborts the crawl mid-sweep.** *(measured)*
  `eightfold.py:167-175` returns on the first non-200 page, abandoning all remaining sweeps.
  Aggregate over 19 runs: 3,023,846 `429-ratelimit` retries, 503,065 `405-wall`, 412,121 `403-wall`.
  caci's scraped count swings 743–1,704 against a true 1,709, and its description fetches have gone
  100 % missing. *Not recommended:* changing the `return` to `break` — under a sustained wall that
  burns budget, and marking truncated there is correct behaviour.
- [ ] **B4. `_MAX_SWEEPS` cannot be raised out of this.** *(inferred, arithmetic)*
  P(complete) > 98 % at N=2,666 needs ~6–7 sweeps ≈ ~1,800 listing requests per board per run
  against a metered edge. Recorded so nobody re-derives it.

## C. Authoritativeness is binary and board-wide (provider-agnostic)

- [ ] **C1. A 0.11 % shortfall permanently disables eviction for a whole board.** *(measured)*
  ADR-0053 authoritativeness is all-or-nothing, so a 3-of-2,607 miss protects ~1,900 rows forever.
  Not eightfold-specific: `successfactors:jobs.crh.com` is 19/19 excluded with a *fixed
  deterministic* gap — `sitemap rss -> 1811 job pages to fetch` then `1805 jobs`, identical every run.
- [ ] **C2. Proposed: N-strikes-before-evict hysteresis.** Evict a row only after it is absent from
  N consecutive scrapes; a chronically-short board can then stay in the eviction scope safely.
  This is the option that actually resolves the tension, and it fixes C1's successfactors variant
  too. **Costs a schema change** (new column → ADR + README §"The served table" in lockstep +
  `tests/test_readme_schema.py`). Bigger call — decide before building.
- [ ] **C3. Boards stuck in the collapse guard.** *(measured)* `successfactors:viacomcbs.careers`
  withheld evictions in 15/19 runs, `eightfold:portal.careers.hsbc.com` 7/19,
  `smartrecruiters:TurnerTownsend` 4/19. ADR-0055 bounded the hold; these still recur, which
  suggests the drain rate is below their churn.

## D. Personio — PR #160 fixed the scraper only

- [ ] **D1. `p_personio` in `scripts/validate/check_liveness.py:1090` has the identical
  non-normalising split.** *(measured)* **Fix this before any re-probe** or the probe will re-confirm
  the bad rows as live: all 312 live-and-malformed rows currently report `jobs=0` because the prober
  fetched the HTML job page and counted zero `<position>` entries.
  *(Note: this file has uncommitted local edits — coordinate before touching.)*
- [ ] **D2. `scripts/discover/cc_miner.py:384` is the actual source of the bad data.** *(measured)*
  For `kind: "host"` the regex captures a clean host but returns `url_hint = None`, so line 421
  stores the raw Common Crawl capture — usually a job deep link. Return the reconstructed board URL
  (`f"https://{tok.lower()}"`) instead.
- [ ] **D3. Data cleanup: 634 rows in `data/validate/liveness/personio.csv`.** *(measured)*
  The `tenant` column is already correct in 633 of 634, so this is a pure `url`-column rewrite to
  the bare host, then a re-probe with the fixed prober. Live probe of 45 normalised hosts: **73 %
  returned 200 with real postings** → extrapolated **~229 recoverable boards, ~4,800 currently
  invisible postings**. Zero of the 634 has a competing clean row, so this is pure coverage loss.
- [ ] **D4. Same latent bug in zoho.** *(measured)* `zoho.py` `slug_from` and `p_zoho` both consume
  the url verbatim; 44 pathy / 19 query rows, currently 0 live, so it isn't biting yet.
- [ ] **D5. Bad `url` data exists in other ledgers but is inert.** *(measured)* teamtailor (217 query
  rows, 420 live-pathy), smartrecruiters (202), greenhouse (118), join (73). Harmless *today* only
  because those scrapers use the base `slug_from`, which reads the clean `tenant` and ignores `url`.
  A trap for the next scraper that overrides `slug_from`.

## E. Concurrency — PR #160 changed behaviour that is not yet measured

- [ ] **E1. Workday's 25 streams is a starting point, not a settled number.** *(inferred)*
  It is Eightfold's ADR-0047 measured width; Workday carries ~2.5x the per-shard detail volume
  (~8,513/shard/run vs ~3,400). Re-measure with `scripts/bench/probe_eightfold_throttle.py`'s method.
- [ ] **E2. Four scrapers silently dropped from 100 streams to their sync bound.** *(inferred — this
  is the risk, not an observation)* join 8, rippling 8, smartrecruiters 8, successfactors 6. That is
  a 12–16x reduction in detail-fetch width with **no measurement behind it**. successfactors already
  shows 78 timeouts across the window and fetches hundreds of pages per board; if this slows it
  materially it could push shards toward the 60-minute budget. **Watch the next few runs' shard
  durations and description-miss rates**, and be ready to set an explicit `detail_streams`.
- [ ] **E3. Does Workday send `Retry-After`?** Unknown — needs a live probe. If it does, honour it.
- [ ] **E4. `fan_out` (sync) still takes `workers=_DETAIL_WORKERS` at every call site** while the
  class now also declares `detail_workers`. Two sources of truth for one number; the sync path could
  default to the class attribute the same way the async one now does.

## F. Dead boards — the loop is closed, the data is still stale

- [ ] **F1. The committed liveness ledger still says `live` for confirmed-dead boards.** *(measured)*
  All 9 boards that failed 19/19 are `live` with job counts from probes 3–6 weeks old
  (e.g. `greenhouse:hibu` live/577 jobs/2026-07-03). The new quarantine stops scraping them but
  deliberately does not write the ledger (ADR-0058). A probe run is still owed.
- [ ] **F2. Scale check, so nobody over-builds:** 287 distinct boards ever 404'd (1,347 rows), but
  only **9 failed in all 19 runs** and only ~10 of the 404 boards sit in the deterministic priority
  head — so the *waste* is ~9.5 scrapes/run, not thousands. *(measured)* The tail is large because
  the slice is 20,000 of 66,745 boards: a 6,000-board scored head plus 14,000 random, giving a tail
  board ~23 % selection chance per run.
- [ ] **F3. Consider promoting quarantine → `dead` in git** once the ledger has proven itself, via
  the two-phase design ADR-0058 rejected for now (pipeline observes, a scheduled probe demotes).

## G. Observability traps found while reading the logs

- [ ] **G1. `timeout 60m … || echo "scrape time budget reached — banking partial fragment"`.**
  *(measured — the construct is in pipeline.yml)* The `||` catches **any** non-zero exit, so an OOM,
  a segfault or a crash prints the same benign message and exits 0. A real crash is
  indistinguishable from a clean budget stop. Test `$?` for 124 specifically.
- [ ] **G2. Per-board scrape *attempts* are never logged — only failures.** *(measured)* This makes
  failure *rates* uncomputable and is exactly what blocked classifying the 1,557 boards that failed
  exactly once ("dead" vs "rarely selected"). Log the attempted set, or a per-board outcome line.
- [ ] **G3. Retry volume is invisible in aggregate.** *(measured)* 3,023,846 429-retries occurred
  across the window against only 149 fatal errors — a 1,065x ratio — and Workday's detail pass was
  missing **1,254,130 of 2,426,147 descriptions (51.7 %)** with nothing surfacing it. Per-shard
  `retries:` lines exist; no run-level rollup and no threshold alert.
- [ ] **G4. Shell command echoes poison every naive grep of these logs.** *(measured)* `##[group]Run`
  blocks echo the whole script with an ANSI `\x1b[36;1m` prefix, so `grep -c "time budget reached"`
  returns 327 when the real count is **1**. Any future log tooling must strip ANSI-prefixed lines.
  Worth a note in the runbook.
- [ ] **G5. `unzip` silently drops files whose names contain `[` `]`.** *(measured)* The run-log
  archives contain `4_Run pip install -e .[embed].txt`; `unzip` treats the brackets as a glob and
  extracted 26 of 113 files **with no error**. Use Python's `zipfile`. Also worth a runbook note.

## H. Small, cheap, unrelated to the above

- [ ] **H1. 16 shards stampede GitHub's codeload and 429 themselves.** *(measured)* The single hard
  failure in the window (run 32043354923, 3 shards lost) was `actions/download-artifact` returning
  429 → 502/503 because every shard fetched the same action at once. Pre-fetch once and pass it
  through, or stagger.
- [ ] **H2. `huggingface_hub[cli]` extra no longer exists.** *(measured)* `WARNING: huggingface-hub
  … does not provide the extra 'cli'`, 3x per run. Stale spec; drop the `[cli]`.
- [ ] **H3. ~13,400–14,000 corpus jobs never have vectors** *(measured)* — stable across all 19 runs,
  reported as "non-English, or run embed_run --resume". Confirm it really is the language gate and
  not a stuck backlog; it is ~6.8 % of the corpus.
- [ ] **H4. Priority-ledger key mismatch (pre-existing, spotted in passing).** *(inferred — verify
  before acting)* `update_ledgers priority` keys rows by `board_of(id)` (the `board_key` shape, e.g.
  `workday:company/site`) while `scrape_plan` looks scores up as `f"{c.ats}:{c.slug}"` — and a
  Workday slug is the **whole careers URL**. If that is right, no Workday board has ever matched its
  own priority score. Worth 20 minutes to confirm.
- [ ] **H5. `scripts/eval/judge_pool.py:93` still constructs `Anthropic()` directly**, bypassing the
  llm-router. Already named as a known exception in CLAUDE.md; listed here so it isn't forgotten.
