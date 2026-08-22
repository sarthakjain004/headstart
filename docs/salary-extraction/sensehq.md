# sensehq

## Methods tried

- **Sampled the full candidate population directly — no liveness ledger exists for this ATS**,
  unlike every other pass in this initiative: `data/validate/liveness/sensehq.csv` does not exist
  (matching the plan's own "no liveness CSV — named single-company unlocks" note for oracle/
  sensehq), so `config.load_active_companies()` had nothing to read. Sampled directly from
  `data/ats-tenants-merged/sensehq.csv` (58 rows including header, from `cc2026`/`fingerprint`
  discovery) instead: probed every one of the 57 candidate tenants through the real registered
  scraper's own `fetch()`, since `has_detail_pass = False` (confirmed — no override in
  `sensehq.py`) and `fetch_raw()` already pages through the entire board with
  `description_external` inline (no separate detail fetch, so no bounded sampling adapter is
  needed — matching workable/greenhouse/oracle's own listing-only shape).
- **Live 18 of 57, dead/error 39** — the dead/error set is dominated by a consistent HTTP 500 (34
  of 39), not a mix of shapes, suggesting most of the mined `cc2026` candidates are tenants whose
  boards genuinely no longer exist on this host rather than a transient probing issue; one
  `Timeout`. Not investigated further per the plan's own explicit "do not over-invest here"
  guidance for this ATS.
- **One demo/test tenant found and excluded**: `trm-dev` (204 postings) — the large majority
  QA/testing-tool placeholder titles ("Cypress 1" ×41, "QA test" ×13, "TESTING" ×19, "sdaa" ×9,
  template stand-ins "Job template"/"Crm template"/"Crm job" ×6/×3/×3) plus real-looking titles
  duplicated with " copy" appended ("Sales development Representative" / "Sales development
  Representative copy"), a clear feature-testing sandbox. Excluded via
  `EXCLUDED_BOARDS` (`config.py`), per this initiative's own established discipline (found on
  lever/keka/darwinbox/trakstar before this).
- **17 real, legitimate boards, 618 jobs** — real Indian and international companies (LTIMindtree
  subsidiary LTTS, Zee Entertainment, Fresenius Kabi, Zycus, Zetwerk, WebEngage, Nova Benefits,
  Esper, Bata, Embitel, GameberryLabs, CallHub, Tiger Analytics, and more), consistent with
  SenseHQ's own real market position (a staffing/RPO-adjacent ATS with an Indian-market-heavy
  customer base, several healthcare-staffing agencies among the 39 dead — `addisongroupsf`,
  `armstaffing`, `axismedicalstaffing`, `elitemedicalstaffing`, `pulsehealthcareservices`).
- **Tier 1 is genuinely absent**: the scraper's own `parse()` never sets `Job.salary` from any
  field — confirmed directly by reading `sensehq.py` (no `salary=` kwarg anywhere in the `Job(...)`
  construction) and by measurement (0/618 jobs with a non-null raw `salary` field).
- **Real Tier-2 coverage is the lowest since ripplehire's**: 1.78% (11/618) — dominated by
  intern/entry-level stipends at two companies (zycus: ₹180,000-360,000/yr, i.e. ₹15-30k/month;
  nova-benefits: a mix of intern stipends and two full real-salary roles at ₹400,000-1,500,000/yr)
  plus two USD roles at `esper` ($125,000-400,000/yr).
- **Audited the full no-signal bucket for currency-shaped content** (22 of 607 no-signal jobs,
  the entire set given the small corpus — not a sample): every one read in full context and
  correctly declining, no real gap found:
  - Vague qualitative compensation phrasing with no actual figure — "Attractive Compensation,"
    "competitive compensation," "Competitive Rewards," "Competitive salary and incentive
    structure" (zycus ×4, callhub) — correctly nothing to extract.
  - "CTC: Candidate to mention Current and expected CTC" (`ltts`) — asking the CANDIDATE to state
    their OWN current/expected pay, not the job's own offer — correctly no-signal.
  - "CTC - Best in Industry" / "CTC: Best in Industry" / "CTC As per Norm" (`nova-benefits` ×2,
    `bata`) — a vague qualitative claim, no number — correctly no-signal.
  - Company revenue/funding/scale boilerplate repeated across many postings from the same company
    — `zetwerk`'s "$300M+ in revenues... $200M orderbook," `webengage`'s "$20M ARR,"
    `nova-benefits`'s "$20mn limit of liabilities," `gameberrylabs`'s "$150M+ lifetime revenue...
    250M+ downloads" (repeated verbatim across 7 of its own postings) — the established,
    already-correctly-excluded funding/valuation guard working as intended.
  - One acronym-collision false positive: `zetwerk`'s "layered process audits (LPA)" is a real
    manufacturing QA term, not Lakhs Per Annum — correctly not extracted (no number adjacent to
    the acronym in a salary-shaped position), consistent with this initiative's own established
    CAD/GBP-collision precedent (never a real risk once the actual match context is read).
- **No `salary.py` changes ship this pass** — the 11 real hits already extract cleanly via the
  existing shared cascade (standard ₹-per-annum labeled ranges, standard $-per-year labeled
  ranges), and the no-signal audit found zero real Tier-2 candidates worth building. The mandatory
  full cross-ATS diff is correctly N/A, matching lever's/ripplehire's/successfactors'/freshteam's/
  eightfold's own precedent.

## Instruction-adherence self-assessment

- Sampled up to 3000 or the full live-CSV count: **yes, adapted for this ATS's own shape** — no
  liveness CSV exists at all for sensehq, so the full 57-candidate discovery list was probed
  directly instead (18 live, 1 excluded as a demo tenant, 17 real boards sampled in full).
- Measured both required percentages: **yes** — 0.00% field, 1.78% overall (Tier1+Tier2), against
  618 jobs.
- Live-verified after code changes: **N/A, correctly** — no `salary.py` change ships this pass, so
  there's nothing for a fresh sample to have caught; a fresh, differently-seeded 8-board reseed
  (random.Random(919)) was still run and confirms the (unmodified) sampling path works correctly
  against real current boards (0 errors, 422 jobs, consistent low-coverage shape).
- **Audited the no-signal bucket for language-independent currency-shaped content before
  trusting the coverage number as a ceiling**: yes — all 22 currency-adjacent no-signal jobs read
  directly (the full set, not a sample, given the small corpus size), traced to specific reasons.
- Went beyond the ask: did a real content-level demo/test-tenant check while sampling (not just a
  slug-shape check) and found and excluded `trm-dev` — this is the sixth ATS in this initiative
  where that discipline found a real fabricated-postings tenant (after lever/keka/darwinbox/
  trakstar), and the first time a `-dev`-suffixed slug was the one confirmed real rather than a
  false positive the way trakstar's own `zutest` was kept.

## Live-verification review

Fresh, differently-seeded 8-board sample (`random.Random(919)`, distinct from the full-population
read above) against real current sensehq hosts: 8/8 succeeded (0 errored), 422 jobs seen, 2 hits
(0.47%) — consistent with the full 17-board population's own 1.78% rate (this subset happened to
draw only `esper`'s own 2 USD hits, not `zycus`'s/`nova-benefits`'s intern-stipend hits, which is
ordinary small-sample composition variance given only 11 total hits exist across the whole
population, not a discrepancy). Confirms the (unmodified) listing-only sampling path still works
against real, current boards.

## Patterns found

Real, worked examples the existing shared cascade already extracts, unmodified:

- `"Compensation: ₹1,80,000 – ₹3,60,000 per annum"`-shaped labeled INR ranges (intern stipends)
  extract cleanly via `_LABELED`.
- `"$125,000 - $225,000"`-shaped labeled USD ranges extract cleanly.

Declined, with the real mechanism traced (not extraction gaps once understood — see Methods tried
for the full account): vague qualitative compensation phrasing with no stated figure ("Attractive
Compensation," "CTC - Best in Industry"); a candidate-facing "state your own CTC" ask, not an
offer; company revenue/funding/scale boilerplate; a real "LPA" acronym collision (Layered Process
Audit, not Lakhs Per Annum) correctly not matched since no number sits adjacent to it.

## Coverage

| metric | value |
|---|---:|
| candidate tenants (Common Crawl + fingerprint discovery, no liveness ledger) | 57 |
| live / dead-or-error | 18 / 39 |
| demo/test tenant excluded | 1 (`trm-dev`, 204 postings) |
| boards sampled (real, full population) | 17 |
| jobs seen | 618 |
| structured field (Tier 1) | 0 (0.00%) |
| description mining (Tier 2, no usable field) | 11 (1.78%) |
| **overall Tier1+Tier2 coverage** | **11 (1.78%)** |
| boards with ≥1 job showing either (loose sampling-stage signal) | 3/17 (17.6%) |

## What changed in code, and why

Nothing in `salary.py`. Tier 1 has no field to wire (the scraper's own `parse()` never sets
`Job.salary`). The no-signal audit found zero real Tier-2 candidates — every currency-adjacent
no-signal job traces to a genuinely non-numeric statement (vague "competitive"/"best in industry"
phrasing, a candidate-facing CTC ask, or funding/revenue boilerplate) or a correctly-declined
acronym collision, not a missed pattern. "No new pattern needed" is a legitimate, evidence-backed
outcome here (lesson 42): 1.78% is a real, low ceiling for this ATS's own company mix (staffing/
RPO-adjacent, several healthcare-staffing agencies, limited pay transparency), not a measurement
gap.

`config.py` gained one `EXCLUDED_BOARDS` entry (`sensehq:trm-dev`) for the demo/test tenant found
while sampling — a real, evidenced fix, same class as lever's/keka's/darwinbox's/trakstar's own
exclusions, kept separate from the (nonexistent) `salary.py` change in this same PR since it's an
independent data-quality finding, not a salary-extraction one.

## Carried forward

- **Lesson 40** (check `BaseScraper`/the scraper's own smaller primitives before writing a new
  sampling adapter) applied trivially: `has_detail_pass = False` and an already-inline description
  field meant the existing listing-only sampling shape needed zero new code.
- **Lesson 43** (do a real content-level demo/test-tenant check while sampling, not a slug-shape
  check alone) applied directly and found a real tenant this pass — the sixth time this specific
  discipline has paid off in this initiative (lever, keka, darwinbox, trakstar, now sensehq), and
  the first time a "-dev"-suffixed slug turned out to be the real positive rather than the false
  positive trakstar's own `zutest` was.
- **New**: when an ATS has no liveness ledger at all (a genuine architectural gap, not a stale
  count — see the eightfold pass's own distinction between ledger staleness and discovery-
  coverage completeness), the candidate-tenant discovery file (`data/ats-tenants-merged/<ats>.csv`)
  is the right fallback population to sample directly, probed one-by-one through the real
  registered scraper rather than through `config.load_active_companies()` (which has nothing to
  read without a liveness CSV). Worth remembering for any future ATS reached before its own
  liveness ledger exists.
