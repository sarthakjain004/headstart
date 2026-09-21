# The churn-based employer-type classifier does not work

*Measurement date: 2026-09-21. This tests the approach recommended in
[the Hot-companies research](2026-09-21_hot-companies-actively-hiring.md) — that a Board's churn
ratio (7-day new roles ÷ open roles) would both rank actively-hiring companies and detect staffing
agencies. **The ranking half holds. The detection half fails**, and this records the measurement
that killed it so nobody proposes it again from the same reasoning.*

Artifacts: `experiment/company-curation/artifacts/2026-09-21_hand-labels-114-boards.txt` (the
ground truth) and `2026-09-21_board_features.csv` (the feature table).

## Method

Features per Board from data already held: churn (fresh Board-count ledger, 2026-09-21), title
repetition, unique titles, distinct locations per unique title, dominant employment type, and
company name — the last two from a `data/lancedb` snapshot of 2026-09-15, which is structural
information that six days does not move.

Population: the **2,916 Boards with at least 25 open roles**, which is the population a Hot tab
would ever display. A stratified random sample of **114** was drawn across six churn bands, and
each was hand-labelled **direct employer / staffing-services / aggregator** from its name, slug,
title mix, location spread and employment type. Population-weighted metrics use each band's
sampling fraction.

Two honest limits on the ground truth. The labels are one person's judgement from listing evidence,
not verified against each company's own description of itself; and the employer/services boundary
is genuinely fuzzy — Capgemini, CI&T, Version 1 and Schuberg Philis all employ their own engineers
and staff them onto client work, so calling them "not an employer" is a product choice, not a fact.

## Results

The base rate: **14.3% of Boards with ≥25 open roles are not direct employers** — about 418 Boards.
They are not concentrated where the hypothesis said they would be:

| churn band | sampled | employer | staffing | aggregator | population | not-employer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.00–0.05 | 20 | 18 | 1 | 1 | 826 | 10% |
| 0.05–0.15 | 20 | 19 | 1 | 0 | 995 | 5% |
| 0.15–0.30 | 20 | 15 | 5 | 0 | 781 | **25%** |
| 0.30–0.50 | 20 | 16 | 4 | 0 | 246 | 20% |
| 0.50–0.75 | 20 | 8 | 12 | 0 | 54 | 60% |
| 0.75–1.01 | 14 | 5 | 8 | 1 | 14 | 64% |

High churn *is* enriched for agencies — 60–64% against a 14.3% base rate. But the high-churn bands
hold only **68 of 2,916 Boards**, while a quarter of the 781-Board 0.15–0.30 band are agencies. The
mass is in the middle, where churn says nothing.

Population-weighted, against a 14.3% base rate:

| rule | precision | recall | F1 |
| --- | ---: | ---: | ---: |
| churn ≥ 0.5 | 60.9% | **9.9%** | 17.0 |
| title repetition ≥ 2.0 | 26.3% | 11.4% | 15.9 |
| locations per title ≥ 1.0 | 29.8% | 10.8% | 15.8 |
| company-name vocabulary | 52.0% | 46.0% | 48.8 |
| name **or** churn ≥ 0.5 | 52.2% | 53.3% | **52.7** |

**The best cheap rule is a coin flip.** At 52% precision, half of everything demoted would be a real
employer — the name-vocabulary rule flags **Cerebras Systems**, **Skylo Technologies**, **Freedom
Technology Solutions Group** and **Julius Baer** purely for containing "Systems", "Technologies" or
"Solutions". That is not shippable even for a demote-only action.

## Why it fails, stated plainly

Read what churn ≥ 0.5 misses: Capgemini (0.25), CI&T (0.29), Version 1 (0.39), Sutherland (0.34),
Foundever (0.20), Avanade (0.54 but no name hit), Coforge, Conneqt, Qualysoft, and Jobs for Lebanon —
an actual job board — at **0.04**.

**IT services firms hire at ordinary rates and have ordinary names.** They are the dominant source
of the pollution by volume and they look, on every cheap surface feature available, exactly like
employers. The thing that separates them is what the company *does*, which none of these features
observes. I labelled them correctly only because I knew the companies.

## What does work

A hand-written denylist of ~45 known IT-services, staffing and aggregator operators, applied to the
Expansion ranking:

| | boards removed | net growth removed |
| --- | ---: | ---: |
| top 20 | 11 (55%) | **73%** |
| top 50 | 13 (26%) | 52% |
| top 100 | 14 (14%) | 40% |

Forty-five names remove three-quarters of the net growth from the top 20, and what surfaces
underneath is Lockheed Martin, Microsoft, Apple, Google, NVIDIA, RTX, Booz Allen, CACI, GDIT and
Deloitte — employers. But the tail is endless: the cleaned top 20 still carries Artech Information
Systems, USM, ProSidian, KRG Technology, Integrated Resources, Infojini and VTech Solution, all US
IT staffing firms with unremarkable names. A denylist covers the head and never finishes.

## The reframing this suggests

**The Hot tab does not need a classifier over 33,480 Boards. It needs the top ~200 of one ranked
list to be clean.** That is a bounded problem with a bounded cost: adjudicate the head of each lens
once, cache the verdict per Board, and re-adjudicate only new entrants — a few per day. At that
scale the judgement can be an LLM call through the router (the repo's ADR-0011 judge pattern, with
a hand-labelled validation set exactly like the 114 here), or simply a human review queue, because
the volume is a handful of Boards a day rather than a corpus sweep.

And a second observation worth weighing before any classifier is built: the user-facing complaint
that started this — "the same three IT services firms fill my page" — is a **diversity** problem,
and per-company result capping solves it without judging anybody. Capping needs no labels, no
denylist and no model. The classifier is only genuinely required for Boards that are *not employers
at all* (Jobgether, Jobs for Lebanon), which are rare enough to curate by hand.

## Recommendation, revised

1. **Per-company capping** in the ranked page — no judgement required, solves the loudest complaint.
2. **A curated denylist** for the head of the Hot tab — 45 names, 73% of the top-20 pollution, an
   afternoon's work, and honest about being incomplete.
3. **Adjudicate only what is displayed** — the top ~200 per lens, cached, LLM or human, validated
   against a hand-labelled set.
4. **Do not build a corpus-wide cheap-feature classifier.** It was measured at F1 52.7 and it would
   demote Cerebras.

Churn keeps its other job: it is a sound *ranking* signal for the Hot tab's Rate lens, and it does
flag the most flagrant reposters at 61% precision. It simply cannot carry the classification.
