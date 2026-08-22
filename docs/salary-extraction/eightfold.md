# eightfold

## Methods tried

- **Sampled the full live population**: 99 live boards (`config.load_active_companies`), the
  plan's stale figure was 103 — every ATS so far has had a stale plan-stage count, this one only
  slightly, and (per the freshness re-check below) closer to today's true count than any other
  ATS in this initiative has been. `fetch_raw()` bakes a full multi-sweep,
  replica-disagreement-tolerant crawl of the entire board into itself (the PCSX API's own
  `_api_search()`, built to solve a real completeness problem, #142 — not something a bounded
  sample should trigger), so sampling needed a new bounded adapter (`_fetch_eightfold`,
  `scripts/enrich/salary_sample.py`) mirroring `fetch_raw()`'s own primary/fallback branching but
  built from the scraper's own smaller primitives: one raw `_search_url(group_id, 0)` GET
  (bypassing `_api_search()`'s own completeness crawl) for the primary PCSX path, capped to
  `_DETAIL_FETCH_CAP` before calling the scraper's own `_api_records()`; a capped slice of
  `_job_urls()` (already cheap, fan-out-free) plus the scraper's own `_jsonld()` per-job fetch for
  the sitemap fallback. Both branches verified directly against real boards before running the
  full sample (a primary-path board — qualcomm — and three fallback-path boards the scraper's own
  docstring names — bayer/hsbc/libertymutual — though all three resolved via the primary path on
  this pass's own run, suggesting the API's ~20%-tenant-403 rate the docstring documents may have
  shifted since it was last measured, 2026-07-21; not re-verified at the aggregate level, since
  the bounded sample doesn't need to know which path it took to be correct). Re-verified again
  during this PR's own code review (2026-08-22): a fresh probe of live boards found both branches
  still work correctly, and this second run found several genuine 403s on the primary path
  (`lgcns`, `bayer`, `insight`, `stmicroelectronics`, `faurecia`) that correctly fell through to
  the sitemap fallback — consistent with the scraper's own `egress_fallback_on = {403, 405}`
  wiring and its ADR-0063 `marks_wall=False` semantics for this specific probe (a 403 here can
  legitimately mean "this tenant has no API," not "our IP is blocked," so it must not itself
  trigger a wall) — no adapter change needed, the fallback path is exercised on real data either
  way.
- **A direct challenge to the 99-board count, investigated rather than dismissed**: mid-review, a
  concern was raised that eightfold should have far more live boards than 99. Investigated by
  checking, in order: (a) local git sync — current, not stale; (b) the liveness ledger's own TTL —
  every eightfold row was past its 7-day live TTL; (c) a real re-probe via `check_liveness.py`
  over the full known 745-board candidate pool, not a re-read of the existing CSV. Result: live
  103→102 raw boards, 99→100 via `load_active_companies()`'s own dedup/exclusion filtering — the
  ledger genuinely was stale by TTL, but the fresh count is barely different from what this pass
  already sampled, so ledger staleness explains almost none of the gap the concern anticipated.
  That splits into two separate questions: (1) is the ledger fresh? — yes, confirmed by a real
  re-probe, merged separately as PR #260 (`a885b53`); (2) is the known candidate pool itself
  complete? — genuinely open, and **not** answered by this pass. Eightfold tenant discovery
  (`scripts/discover/eightfold_dns_sweep.py`) is wordlist-bounded — it only finds tenants whose
  subdomain matches a known company-name pattern, so a real eightfold customer using a name the
  wordlist doesn't cover is invisible to the candidate pool regardless of how fresh the liveness
  check is. Decision: keep this pass's salary sample on the current ~99/100-board population
  rather than block on expanding discovery — the sample is representative of the *known*
  population, which is what a coverage measurement needs; widening eightfold's own tenant
  discovery is separate, future work, out of scope for a salary-extraction pass.
- **Contrary to the plan's own "historically fragile" flag, this pass hit zero fetch errors**:
  99/99 boards succeeded on the full sample, 20/20 on the live-verification reseed. The bounded,
  small-per-board sampling shape (one search page, 3 detail fetches) likely avoids whatever
  instability affects bulk/production-scale scraping of this ATS — worth noting for whoever next
  works on eightfold's own production scrape reliability, not something this pass needed to fix.
- **Tier 1 is genuinely absent**: no salary-related field anywhere in the PCSX API's own position
  schema (`name`/`department`/`locations`/`postedTs`/`workLocationOption`/`positionUrl` — the
  scraper's own `_api_records()` maps every one of these, none salary-shaped) or the sitemap
  fallback's JSON-LD. 0.00% field coverage confirmed, not assumed.
- **Real Tier-2 coverage without any new pattern is the strongest since ashby's**: 33.79%
  overall, entirely via the existing shared cascade — driven by eightfold's own real company mix,
  almost exclusively large, well-known US/EU enterprises (qualcomm, nvidia, microsoft, netflix,
  twilio, ford, hsbc) heavily represented in pay-transparency jurisdictions.
- **Every promising-looking no-signal candidate traced to its real mechanism turned out to be
  correctly-declining, not a gap** — a genuinely clean audit, no new pattern candidate cleared
  even a single-company evidence floor worth chasing except one, declined below the bar:
  - Multiple companies (`twilio`, `ericsson`, `ford`, `ngc`) state SEVERAL real, genuinely
    different salary ranges in one description — one per US state/region, or one per job
    grade/level (a well-known large-enterprise pay-transparency pattern: "Illinois, Maryland...:
    $106,320-$132,900. Based in New York...: $87,840-$109,800"). `_resolve()`'s existing
    mutual-consistency check correctly declines rather than guessing which applies, the same
    no-fabrication principle already established for multi-level postings on earlier ATSes.
  - `microsoft`'s own posting has the identical multi-region shape (a US-wide range, then a
    separate, different range for SF Bay/NYC) — confirmed by reading the FULL description, not
    just a truncated snippet (a snippet-only test of the same text misleadingly appeared to
    extract cleanly, since it happened to exclude the second, conflicting mention).
  - `tailoredbrands`'s "Pay range: $16.00 - $20.00" and `omnicell`'s "Base Compensation: $13.83
    to $25.68" are both genuinely period-ambiguous — no hr/day/mo/yr marker anywhere near the
    match — correctly declining per the no-fabrication principle, even though both READ as very
    likely hourly to a human given the role/figure shape. `omnicell` is the clearest illustration
    in this initiative yet that the SAME company's own template can correctly extract for one
    posting (`"$96,600.00 to $179,400.00"`, plausible as annual) and correctly decline for
    another using the identical template (`"$13.83 to $25.68"`, implausible as annual, no marker
    to say otherwise) — not an inconsistency, the no-fabrication principle applied uniformly to
    genuinely different source shapes.
  - `fcx`'s "$75,00-$104,000" is a genuine SOURCE-DATA TYPO (missing a digit on the floor),
    correctly rejected by `_num()`'s own intentional locale-decimal disambiguation (lesson 36) —
    the same mechanism already found on freshteam's own pass, now confirmed a second time on a
    different ATS.
  - `jhu`'s "$21.80 - $37.80 HRLY" (and one more, same company) is a real, clean, low-risk gap —
    "HRLY" is an unambiguous all-caps hourly abbreviation not currently in `_PERIOD_HINT` — but
    evidenced at exactly 1 company (2 postings), below this initiative's multi-company bar.
    Declined.
  - Non-English disclosures (`mm-group`, `johndeere`, both German; `puertoricogov`, Spanish) are
    correctly out of scope per the established English-only search-corpus scope (CLAUDE.md) —
    those postings never reach the served index regardless of what could be extracted.
  - The remainder of the no-signal bucket is company revenue/AUM/funding boilerplate
    (`amdocs`, `insight`, `whirlpool`, `mlp`, `qlik`), sign-on/adoption bonuses (`mc`, `ftr`),
    sales quotas (`citi`, `qlik`), and unrelated legal/fine references (`libertymutual`, `slb`) —
    all correctly excluded by the existing funding/valuation and false-positive-context guards.
- **No `salary.py` changes ship this pass** — Tier 1 has nothing to wire, and the one real
  Tier-2 candidate found (the "HRLY" abbreviation) is below the multi-company bar. The mandatory
  full cross-ATS diff is correctly N/A, matching lever's/ripplehire's/successfactors'/freshteam's
  own precedent.
- **No demo/QA vendor tenants found**: a slug-shape check found zero suspicious candidates at
  all (unlike lever/keka/darwinbox/trakstar) — consistent with eightfold's own real company mix
  being almost exclusively large, well-known enterprises rather than a platform with a large
  self-serve SMB base prone to vendor-side test accounts.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes** — 99, the full live population (99/99
  boards succeeded, 0 errored).
- Measured both required percentages: **yes** — 0.00% field, 33.79% overall (Tier1+Tier2),
  against 293 jobs.
- Live-verified after code changes: **yes, on the second attempt** — the only code change here is
  the sampling adapter itself (`salary.py` ships unchanged). The first reseed (seed=313) was run
  before code review found two real bugs in that adapter and was wrongly presented as a post-fix
  check in an earlier draft of this doc; a genuinely fresh reseed (seed=811), run after the fix
  commit and confirmed to exercise both the primary and fallback paths on real current boards
  (two of its 20 boards independently confirmed to 403 and fall through), is what actually
  verifies the fix — see Live-verification review.
- Checked the coverage claim against a more durable source than the frozen sample, per the
  plan's own verification checklist: **partially, same tradeoff ashby.md first named and
  recruitee.md carried forward, not silently repeated here either**. The served table's
  `/search` HTTP endpoint is still behind ADR-0042's sign-in wall, and a full LanceDB
  `snapshot_download` is still ~1.87 GB against CLAUDE.md's documented storage-cost constraint.
  The seed=811 reseed above (run for live-verification, not built for this purpose) fills the
  same role a fresh live resample serves in ashby's/recruitee's own passes — called out
  explicitly here rather than left for a reader to notice its absence.
- **Audited the no-signal bucket for language-independent currency-shaped content before
  trusting the coverage number as a ceiling**: yes — all 37 currency-adjacent no-signal jobs
  read directly (not just a sample — the full set, given the smaller corpus size), traced to
  specific reasons (see Methods tried and Patterns found).
- Went beyond the ask: for every promising-looking no-signal case, verified the mechanism against
  the job's FULL captured description, not a truncated snippet — this specifically caught that
  `microsoft`'s own apparent gap was an artifact of snippet truncation (the full text has a
  second, genuinely conflicting region-specific range the snippet had cut off), not a real
  extraction failure. Also directly live-tested both the primary (PCSX API) and fallback
  (sitemap) code paths against real boards named in the scraper's own docstring, rather than
  assuming the adapter's fallback branch was correct without exercising it.

## Live-verification review

**First reseed (seed=313, run before the adapter fix-up below) predates the two bug fixes and
does not verify them** — an earlier draft of this section presented it as a post-fix check, which
it was not (caught in this PR's own code review). For the record: 20/20 succeeded (0 errored),
60 jobs seen, 0% field, 26.7% description-hint — consistent shape with the full 99-board sample's
own description-hint rate of 47.8% (140/293; a *different* metric from the 33.79% Tier1+Tier2
figure reported elsewhere in this doc — description-hint is the sampling script's coarse regex
signal, not real `headstart.salary` extraction — the smaller reseed's lower rate reflects normal
small-sample variance across a tiny, 99-board total population, not a discrepancy).

**Second reseed (seed=811), run after the fix-up commit, is the one that actually verifies it**:
20/20 succeeded (0 errored), 59 jobs seen, 0% field, 59.3% description-hint (35/59). Two of the
20 boards (`faurecia.eightfold.ai`, `coca-colafemsa.eightfold.ai`) confirmed via a direct status
check to genuinely 403 on the primary PCSX path and fall through to the sitemap adapter — real
exercise of the exact branch the `detail_fetched` fix touched, on real current boards, not a
replay. No new patterns shipped this pass, so there was nothing for the salary-extraction logic
itself to have silently broken; this run confirms the sampling adapter — both its primary and
fallback paths, and the two fixed edge cases — works correctly against real, current boards.

## Patterns found

Real, worked examples the existing shared cascade already extracts, unmodified:

- `"Base Compensation: $96,600.00 to $179,400.00"` — a clean labeled range, no period marker
  needed since the bare figures already read as plausible annual amounts.
- Standard US pay-transparency disclosures with a single, unambiguous range extract cleanly
  wherever a job states only one real figure.

Declined, with the real mechanism traced (not extraction gaps once understood — see Methods
tried for the full account): multi-region/multi-grade postings stating several genuinely
different real ranges (`twilio`, `ericsson`, `ford`, `ngc`, `microsoft`); genuinely
period-ambiguous bare ranges with no marker anywhere nearby (`tailoredbrands`, `omnicell`'s
hourly-shaped posting); a source-data typo interacting correctly with existing locale-decimal
logic (`fcx`); an "HRLY" abbreviation evidenced at only 1 company (`jhu`).

## Coverage

| metric | value |
|---|---:|
| boards sampled (full live population) | 99 |
| boards succeeded / errored | 99 / 0 |
| jobs seen | 293 |
| structured field (Tier 1) | 0 (0.00%) |
| description mining (Tier 2, no usable field) | 99 (33.79%) |
| **overall Tier1+Tier2 coverage** | **99 (33.79%)** |
| boards with ≥1 job showing either (loose sampling-stage signal) | 66/99 (66.7%) |

## What changed in code, and why

Nothing in `salary.py`. Tier 1 has no field to wire (confirmed absent in both the PCSX API and
sitemap-fallback schemas). The one real Tier-2 candidate found this pass (the "HRLY" hourly
abbreviation) is evidenced at exactly 1 company, below the multi-company bar — declined. "No new
pattern needed" is a legitimate, evidence-backed outcome here (lesson 42): the existing shared
cascade already delivers 33.79% coverage on eightfold, the second-strongest result in this
initiative after ashby's 49.7%, without any ATS-specific extension — driven entirely by
eightfold's own large-enterprise, pay-transparency-jurisdiction-heavy company mix.

`scripts/enrich/salary_sample.py` gained `_fetch_eightfold`, a new bounded sampling adapter
mirroring both of `fetch_raw()`'s own primary/fallback paths (no `salary.py` involvement — pure
sampling infrastructure), plus one new import (`_sitemap_position_id` from `eightfold.py`, a
pre-existing module-level helper, reused rather than reimplemented).

**Code review found and fixed two real bugs in that adapter, both about correctly distinguishing
"nothing to report" from "didn't check"**: (1) the primary-path guard originally read
`if positions:` (truthy), collapsing "API unavailable" and "API works, board genuinely has zero
open positions" into the same branch and silently skipping straight to the sitemap fallback for
a genuinely-empty-but-working board; fixed to a `None`-sentinel (`positions = None` unless a 200
response parses to a real, possibly-empty, list), matching `_api_search()`'s own production
semantics exactly. (2) the sitemap-fallback branch hardcoded `"detail_fetched": True` regardless
of whether `_jsonld()` actually returned fields, violating ADR-0050's two-state description rule;
fixed to `"detail_fetched": fields is not None`, computed once and reused for both dict entries
(avoiding a double fetch), matching `_sitemap_records()`'s own correct pattern. Both fixes
re-verified live against real boards after the change (see Live-verification review). Neither
bug affected this pass's own reported coverage numbers, for two different reasons: bug (1)
changes control flow (which path a board takes), and directly checking the one 0-job board in
the original 99-board sample (`10xgenomics.eightfold.ai`) confirmed it has no `group_id` at all —
it never reaches the `positions` branch either way, and no other sampled board returned an empty
list there; bug (2) never could have mattered regardless of sampling, since `detail_fetched` is
metadata this script's own coverage counting never reads (`jobs_with_salary_field`/
`jobs_with_desc_hint` come from `job.description`/`job.salary`, set from `fields` itself, not
from the flag describing whether fetching it succeeded). Both bugs would still silently corrupt a
future re-run or the ADR-0050 description-store pipeline downstream, which does read
`detail_fetched` — worth fixing regardless of whether this pass's own numbers moved.

## Carried forward

- **Lesson 40** (check `BaseScraper` and the scraper's own smaller primitives for a reusable
  fetch primitive before writing a new adapter) applied directly, and needed more care than usual:
  `fetch_raw()`'s own top-level structure (primary API + sitemap fallback) had to be mirrored at
  the ADAPTER level too, not just the detail-fetch cap — a single-path adapter would have silently
  under-sampled the ~20% of tenants (per the scraper's own docstring) that only work via the
  fallback.
- **Lesson 42** (measure every Tier-2 candidate at full-corpus scale before building or
  declining) applied to "HRLY": confirmed at exactly 1 company via a full-corpus regex check
  before declining, not assumed from the single occurrence already read.
- **New, extending lesson 55's own discipline**: when reading a no-signal snippet to judge
  whether it's a real gap, verify against the job's FULL captured description before concluding
  either way, not just the ~150-char snippet a diagnostic script prints — a snippet can
  misleadingly exclude a second, conflicting mention elsewhere in the same description
  (`microsoft`'s own case: the snippet alone looked like a clean, single-range match; the full
  text has a second, genuinely different region-specific range that correctly makes the whole
  thing ambiguous). A future pass should re-fetch the full text before either building a pattern
  from a snippet or writing "this looks like a gap" into a doc.
- **New**: a scraper's own docstring-documented fallback-rate ("~20% of tenants 403 the API") is
  a claim from when it was written, not necessarily still true — this pass's own live checks
  against three of the docstring's own named fallback-tenant examples (bayer, hsbc,
  libertymutual) all resolved via the PRIMARY path instead, unprompted. Not re-measured at the
  aggregate level (out of scope for a salary pass), but worth a note for whoever next touches
  `eightfold.py`'s own production scraping: the documented 403 rate may be stale.
