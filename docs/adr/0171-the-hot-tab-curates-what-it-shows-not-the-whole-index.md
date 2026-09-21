# ADR-0171: The Hot tab curates what it shows, not the whole index

**Status:** accepted · **Date:** 2026-09-21 · **Extends:** [ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md), [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md)

## Context

A search for `backend` spans 2,807 distinct companies at a median of one job each, so the index
offers no company-level unit at all. A "who is hiring right now" surface would give one, and
ADR-0143's Board-delta ledger already holds exactly the measurements it needs — per-Board `stock`
and a rolling 7-day `new` level, ticking every ~40 minutes. No new collection is required.

Ranking that data naively does not work. Measured on 2026-09-21, the top of every lens is
dominated by Boards that are not employers: `lever:jobgether`, an aggregator that re-posts other
companies' jobs, leads on new roles; `careers.hcltech.com` and `careers.wipro.com` lead on growth;
`agileengine.zohorecruit.com` runs 96% churn. Of the twelve highest-rate Boards, about four hire
for themselves. **A Hot tab without an employer filter ships an agency leaderboard.**

The obvious fix — classify every Board — was built and measured against a stratified sample of 114
hand-labelled Boards drawn from the 2,916 with at least 25 open roles. It fails:

| rule | precision | recall |
| --- | ---: | ---: |
| churn ≥ 0.5 | 60.9% | 9.9% |
| company-name vocabulary | 52.0% | 46.0% |
| name **or** churn ≥ 0.5 | 52.2% | 53.3% |

At 52% precision half of everything demoted is a real employer; the name rule flags Cerebras
Systems, Skylo Technologies and Julius Baer for containing "Systems", "Technologies" and
"Solutions". IT services firms hire at ordinary rates and have ordinary names — Capgemini churns at
0.25, Foundever at 0.20, and Jobs for Lebanon, an actual job board, at 0.04. The separating fact is
what the company *does*, which no cheap feature observes.

## Decision

**Curate what is displayed, not the corpus.** A hand-written list of operator names
(`ingest/board_operator.py`) labels each ranked Board `employer`, `services` or `aggregator`, and
the tab shows employers by default with a visible count of what it filtered and a toggle to reveal
it. Nothing is evicted, demoted in search, or removed from the index: ADR-0053 and ADR-0083 keep
sole ownership of eviction, and a label here changes only what one tab lists first.

The scope is bounded to the head of one ranked list, where the cost of a wrong answer is highest
and the volume is smallest. Measured against the Expansion lens over its 7-day window, the entries
that ship flag 79% of net growth in the top 20, 62% of the top 50 and 48% of the top 100 —
decreasing, because the tail is endless. (The research doc's 73/52/40 describes an earlier
45-entry draft; the figures move with the list.) Going deeper means adjudicating the Boards that
actually reach the tab, a few new entrants per run, not scoring 33,480.

**Both lenses that carry a time span use the same one.** Expansion sums stock deltas over a
trailing seven days, matching the rolling window `new` already uses (ADR-0051), because a row
prints the two side by side. An unbounded sum — the first implementation — would have grown by one
run every run, so "net roles" and "opened this week" would have described different lengths of time
under one heading within a day.

**Company identity on this tab is display-level and deliberately shallow.** A curated alias map
plus slug tidying gives each row a readable name, and rows sharing a name collapse to the
best-ranked Board. This exists because Lockheed Martin reaches Expansion on both Eightfold and
SuccessFactors and ranked first and second as two companies. It is **not** cross-ATS Board
identity, which the trimming research calls an ADR-sized decision in its own right: an unlisted
pair still shows twice, and nothing downstream of this tab consumes these names.

**Matching is exact against normalized forms, never substring or prefix.** Both looser rules were
run over the real population and both demoted real companies: substring matching labels every
"…Manufacturing" board (it contains "turing"), and prefix matching labels Ibex Medical Analytics,
TopTalents and Zensark Tecnologies as three operators they are not. A Board's *tenant* is read
rather than its whole key, because Hyatt's Taleo section is named `infosys_intl`.

**"Actively hiring" is served as three lenses, defaulting to Expansion** (net change), with Volume
(roles opened) and Rate (opened as a share of size). They produce barely-overlapping lists and only
Expansion separates growth from churn — over the 7 days to 2026-09-21 Amazon opened 1,396 roles at
a net change of +20, a near-constant size. **Acceleration is not offered**: "started hiring recently" needs a before and an
after, and the ledger began on 2026-09-13.

**Ranking happens in the pipeline; the Space serves a static artifact** (`data/state/hot_boards.json`,
a few tens of KB). The ledgers behind it are tens of megabytes and the answer only changes when a
run does.

## Rejected alternatives

- **A churn-based classifier over the whole index.** The measurement above: F1 52.7, and it demotes
  Cerebras. Recorded in full in `docs/company-curation/2026-09-21_employer-type-classifier-measurement.md`
  so it is not re-proposed from the same reasoning.
- **Evicting aggregators and staffing Boards from the index.** Clean lists, but it deletes real
  jobs — a staffing firm is genuinely the only route into some roles — and it puts an eviction
  decision outside the two mechanisms that own eviction.
- **An LLM pass over every Board.** Affordable only against the ~200 Boards displayed, which is the
  stated way to extend this; against 33,480 every run it is not, and the router is a single private
  deployment this pipeline must not depend on for a tab to render.
- **Ranking at query time from the ledgers.** Re-derives an answer that changes once per run, and
  costs the Space memory it does not have.
- **Showing every Board and letting the user filter.** Tested by building it: the first screen is
  HCLTech, Wipro, Bluelight Consulting and Jobgether, which reads as a broken product before any
  filter is found.

## Deferred, and said so here rather than left implied

Two parts of the plan this tab belongs to are **not** in it: per-company capping in
`JobSearch.run`, and per-account follow/hide. Both are self-contained changes that touch the
search path rather than this one, and the research recommends doing them — they are sequenced
after this tab because it is the front door that makes a follow list worth having, not dropped.

One change here falls outside the tab: the site footer claimed "no reposts and no agencies", which
this work disproves — an aggregator and 111 services Boards sit in the index. Shipping the evidence
while leaving the claim would be worse than the out-of-scope edit.
