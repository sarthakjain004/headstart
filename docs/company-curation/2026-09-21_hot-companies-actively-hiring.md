# A "Hot companies" tab: who is actively hiring, and what the data does to that question

*Research date: 2026-09-21. Idea: a tab showing companies that are hiring a lot right now — that
recently started hiring, opened many roles lately, or simply have a lot of tech roles open. Third
in the company-curation series, after [trimming](2026-09-21_trimming-the-served-company-set.md) and
[list-building](2026-09-21_building-and-tracking-a-company-list.md).*

Prototype and captured output: `experiment/company-curation/rank_actively_hiring.py` and
`artifacts/2026-09-21_actively-hiring-ranking.txt`.

## The data for this already exists

ADR-0143's Board-delta ledger is exactly this feature's substrate, and it is live: a per-Board,
per-family, per-band, per-ATS count snapshot (`role_trend_board_counts.parquet`, 246,131 rows over
**33,480 Boards**) plus append-only per-tick deltas, currently **207 ticks** at roughly 40-minute
intervals. It carries two metrics: **`stock`** (open roles) and **`new`** (a rolling 7-day level of
roles whose `first_seen` falls inside the window, ADR-0051). Totals right now: 507,794 stock,
86,455 new, with **46.1%** of Boards showing at least one new role this week.

No new pipeline stage is needed to rank companies by hiring activity. What is needed is care, and
a gate.

## Four traps, each of which produces a plausible wrong list

**The first delta tick is a baseline dump, not a change.** The ledger began 2026-09-13 with no
prior snapshot, so tick 1 emitted every Board's entire stock as a delta — 196,824 rows against the
next tick's 127. Summing from tick 0 makes every Board on the index look newly created; it did, on
the first run of this prototype, reporting all 33,480 Boards as new.

**`watch:` families double-count** against centroid families (ADR-0051) and must be excluded from
any total. **`new` is a rolling 7-day level, not per-tick inflow** — read it, never sum it across
ticks.

**A Board whose whole stock appears after the baseline was newly *discovered*, not newly hiring.**
**6,996 Boards** — a fifth of the ledger — are in that state over this 8-day window. ADR-0143 exists
because of precisely this confound for Trends; the Hot tab inherits it and needs the same cohort
exclusion.

**And a Board nothing scraped shows no change.** A run reads only a ~20,000-Board slice, so "not
hiring" and "not read recently" look identical in this ledger. Any hot ranking must state a Board's
last-confirmed time alongside its numbers, or it will report cooling that is really absence.

## The finding that changes the plan

With every trap above handled, here is the top of the ranking by 7-day new roles:

| 7d new | open roles | Board |
| ---: | ---: | --- |
| 1,634 | 1,773 | `lever:jobgether` |
| 1,396 | 9,081 | `amazon:www.amazon.jobs` |
| 962 | 2,497 | `successfactors:careers.wipro.com` |
| 924 | 5,386 | `successfactors:careers.hcltech.com` |
| 718 | 747 | `zoho:agileengine.zohorecruit.com` |
| 496 | 1,708 | `google:careers.google.com` |
| 492 | 1,969 | `successfactors:careers.capgemini.com` |
| 408 | 632 | `smartrecruiters:AvanceConsultingServices2` |

The leader, `lever:jobgether`, is an **aggregator** — it re-posts other companies' jobs, so it
looks like the most actively hiring employer on the index while employing almost nobody. Rank by
net expansion instead and the top four are HCLTech, Wipro, `lever:bluelightconsulting` and Jobgether
again. Rank by hiring *rate* and the top of the list is `teamtailor:eworgmbh` at 99% churn,
AgileEngine at 96%, Jobgether at 92% — with `recruitee:eworgmbh` also at 91%, which is the *same
company on a second ATS*, unjoined.

**A Hot tab built on this data without the employer-type gate is an agency leaderboard.** That is
the finding. The quality gate from the trimming research is not an independent nice-to-have that
can be sequenced after this tab — it is this tab's prerequisite. Of the twelve highest-rate Boards,
roughly four (Comcast, Toloka, Side, NeuraFlash) are employers hiring for themselves.

**The consolation is that the same number does the classifying.** A Board whose 7-day new count
approaches its entire open stock is reposting, not hiring: 99%, 96%, 92% churn are not hiring
patterns. The churn ratio this tab needs for ranking is simultaneously the strongest cheap feature
the employer-type classifier needs. Build it once, use it twice.

## "Actively hiring" is three different questions

The prototype produced three defensible lists that barely overlap, and the tab has to pick or
expose all three.

**Volume** — most new roles this week. Answers "where is the most opportunity right now". Always
won by the largest employers; Amazon's 1,396 new roles come with a net stock change of **−3**,
meaning constant churn at a steady size.

**Expansion** — opening more than it closes, net. Answers "who is actually growing". HCLTech
at +1,010 and Wipro at +764 over eight days are genuinely expanding, not just churning.

**Rate** — new roles as a share of open roles. Answers "who is moving fast for their size", which
is the one that surfaces small companies a user would never otherwise find. It is also the one most
polluted by agencies, for the reason above.

**Acceleration — "recently *started* hiring" — is not yet measurable.** The delta ledger begins
2026-09-13, so there are eight days of history. A claim that a company *started* hiring needs a
before-and-after, and there is no before. This is purely a matter of waiting: the ledger accrues
every 40 minutes, and in a month the question is answerable. Do not fake it from `first_seen` in
the meantime — that measures when **we** indexed a row, not when the employer opened it.

## What this implies for the ordering

The Hot tab is cheap in data and expensive in judgement. The data is already written and only
needs reading; the judgement — which Boards are employers, which are agencies or aggregators, which
are simply new to us — is the entire feature. So:

1. **The employer-type gate comes first.** Without it the tab ships an agency leaderboard on day
   one, and the churn ratio measured here is the feature that makes the gate cheap.
2. **Then the tab**, initially on Volume and Expansion, which are the two robust lenses.
3. **Rate is gated behind the classifier**, because it is the most useful lens for discovery and
   the most corrupted without one.
4. **Acceleration ships when the ledger is old enough**, not before — roughly a month out, and it
   costs nothing to wait because the data is accruing regardless.

The tab also answers the cold-start problem the list-building research identified: a user with no
tracked companies gets a ranked, browsable set on first visit, and "＋ Track" on each row is the
mechanism that was already the recommended entry path. Hot companies is not a detour from the
company-list feature — it is its front door.

## Resolved

**Expansion is the default lens**, chosen 2026-09-21, and all three ship — they answer different
questions and picking one for everyone loses information. Built in
[ADR-0171](../adr/0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) as
`headstart.ingest.hot_boards`.

The employer gate that this document argued had to come first *did* come first, but not in the
form proposed here: the churn-based classifier was built, measured and rejected. See
[the measurement](2026-09-21_employer-type-classifier-measurement.md) — churn reaches recall
9.9%, and the best cheap rule would demote Cerebras. What shipped is a curated operator list
scoped to the head of the ranked list.
