# Does `tech_filter` miss real tech jobs by not reading the description?

**Date:** 2026-09-16. **Question:** `headstart.tech_filter.classify` reads only `title` + `department`.
Descriptions are never consulted. How many tech jobs, if any, does that actually cost — measured by
manually reading real postings, not by keyword-scanning descriptions (which over-reports badly: a
salesperson "partners with engineering," a marketer runs "our platform").

**Verdict: very few, and almost none of them need the description.** Of 450 hand-read postings the
filter classified non-tech, **10 (2.2%) were actually tech**. Of those 10, **8 were decidable from
the title alone** — plain regex gaps, not description-only misses. Only **2 of 450 (0.44%)**
genuinely needed the description to be caught. The filter's recall-biased design is working close
to as intended; the fix that matters is a handful of title patterns, not reading descriptions.

## Method

1. **Sample.** 40 random live Greenhouse boards (chosen because Greenhouse serves full descriptions
   inline via `?content=true`, so no extra detail-fetch was needed), 2,589 real jobs. 1,878 (72.5%)
   were classified non-tech by `tech_filter.is_tech(title, department)`.
2. **A cheap high-precision pre-filter, read manually — never trusted alone.** Two-or-more of five
   description signals (named languages, an engineering stack, a CS-degree requirement, "writes
   code," years-of-software-experience) flagged 24 of 1,878 (1.3%). Reading these confirmed the
   trap: company-boilerplate paragraphs ("we partner with employers to redesign healthcare...")
   dominated the raw text, so the pre-filter is a candidate generator, not a verdict.
3. **The real measurement: 450 postings read in full, by a human, against the repo's own tech
   definition** (`scripts/filter/verify_tech.py`'s: SWE/ML/data/devops/security/mobile/embedded/
   eng-manager; explicitly NOT PMs, analysts, pre-sales, consultants-who-advise-rather-than-build,
   unless clearly hands-on). Three independent batches of 150, drawn without replacement from the
   1,878 non-tech pool (24% coverage), each classified `yes` / `borderline` / `no`, plus whether the
   **title alone** (no description) would have been enough to call it tech.

## Results

| | n | share of 450 |
| --- | ---: | ---: |
| `yes` (genuinely tech) | 10 | 2.2% |
| `borderline` (PM/analyst/pre-sales/consultant judgement calls) | 53 | 11.8% |
| `no` (genuinely not tech) | 387 | 86.0% |

Of the 63 `yes`+`borderline`: **44 were decidable from the title alone, 19 were not.**
Of the 10 strict `yes`: **8 title-decidable, 2 description-only.**

The 2 genuine description-only misses:
- **CLM Consultant** — a contract-lifecycle-management consulting title that turned out to mean
  hands-on Ironclad/Agiloft integration building. Nothing in the title signals "builds software."
- **Senior QA Analyst** — reads as manual QA from the title; the description showed the person
  owns Playwright/TypeScript automation frameworks.

## The 8 title-decidable misses — cheap regex fixes, not description reading

Every reviewer independently converged on the same handful of patterns, cross-checked against all
450 titles for collateral damage before recommending them:

1. **`applied scientist` / `research scientist`** — 3 of 10 misses, one missing term. `_STRONG_TERMS`
   already has `data (engineer|scientist)` but nothing for "applied" or "research" scientist, and
   `_TECH_DEPT` has `r&d` but not `research`. Zero collateral checked against the batch.
2. **`application architect`** — the `architect` alternation requires an adjacent qualifier
   (`software|systems|solutions|technical|cloud|data|security|platform|enterprise|integration`) and
   "Enterprise Cloud / VAEC Architect" breaks the adjacency requirement entirely. A separator-tolerant
   form is safer than a bare `\barchitect\b`, which would catch "Information Architect" and
   "Art Director"-adjacent titles.
3. **Leadership titles using a comma, not "of."** `(director|vp|...) of (engineering|...)` doesn't
   match "VP, Data Platform" or "Director, Engineering." Needs live-checking against
   "Director, Data Governance"-shaped false positives before shipping.
4. **`performance test(ing|er)`** — the QA pattern requires `(qa|test) (engineer|automation)`, which
   misses the "…Testing"/"…Tester" title shape entirely.
5. **`decision scientist`** — not in `_STRONG_TERMS` alongside `data scientist`. Low frequency.

None of these were implemented as part of this investigation — they are recommendations, sized and
verified against this sample, for whoever owns `tech_filter.py` to weigh and ship.

## What this does NOT show

- **Not corpus-wide.** This is 40 Greenhouse boards. Other ATSes (Workday, SuccessFactors, Oracle)
  have a very different non-tech mix — far more admin, manufacturing-test, and support roles per
  the five-run pipeline review's own board-level reading — and Greenhouse skews toward startups
  with denser technical-adjacent titles (PM, sales engineering) than the corpus as a whole. **Do
  not scale this 2.2%/0.44% to a corpus-wide job count** — a Workday- or SuccessFactors-only sample
  would very plausibly read differently in either direction.
- **Heavy per-board duplication in the `no` bucket** (6 identical HVAC postings from one employer,
  23 near-identical creative-agency roles, 11 teaching postings) means the 450 are not 450
  independent draws. This likely does not move the `yes` count (none of the 10 misses were
  duplicates — verified per-batch), but it does mean the denominator overstates how many distinct
  employers were actually sampled.
- **The PM/analyst/pre-sales borderline class (53 of 450, 11.8%) is a policy question, not a bug.**
  Several — "Technical Product Manager," "Business Analyst," "Solutions Engineer (Pre-Sales)" — are
  trivially title-detectable if the project ever decides those roles belong in the tech corpus.
  That decision was explicitly out of scope for this measurement (per the repo's own definition of
  "tech" excluding them by default) and is not recommended here.

## Recommendation

**Do not add description-reading to `tech_filter`.** The description-only miss rate measured here
(0.44%) does not justify reading ~2M descriptions/run through the gate, and CLAUDE.md's own
Simplicity First rule cuts against it directly. The regex widenings above would recover most of the
measured miss (8 of 10) at effectively zero cost and zero added complexity — that is the fix worth
making, if the false-positive risk on each pattern is checked live against a larger sample first,
per this repo's "verify against the live API" convention.

## Artifacts

`artifacts/greenhouse_sample.jsonl` (2,589 jobs, full descriptions), `artifacts/flagged.json` (the
24-candidate pre-filter output, kept for reference only), `artifacts/review_batch_{0,1,2}.json`
(the 450 postings actually read) and `artifacts/verdicts_{0,1,2}.json` (the verdicts).

## Reproducing

`greenhouse_sample.jsonl` (15 MB, full raw descriptions) is not committed — it is a live pull,
reproducible from `data/validate/liveness/greenhouse.csv` plus each board's
`boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`, seeded with `random.seed(17)` over
40 boards sized 20-900 jobs. `review_batch_*.json` (the extracted role-text sections actually read)
and `verdicts_*.json` (the classifications) are committed and are what the LOG's numbers come from.
