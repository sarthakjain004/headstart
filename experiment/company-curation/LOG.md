# Company curation — measuring the company population behind the index

**2026-09-21** — First pass, in answer to "trim the company set down to the ones a user cares
about". Measured the supply side from a freshly pulled `board_priority.csv` and the served side
from the local `data/lancedb` snapshot (2026-09-15, 459,291 rows — shape claims only).

- Script: `measure_company_shape.py`
- Raw output: `artifacts/2026-09-21_company-shape.txt`
- Findings and the design options they support:
  `docs/company-curation/2026-09-21_trimming-the-served-company-set.md`

Headlines: 40,004 hiring Boards, median 3 tech jobs each; one `backend` query spans 2,807
companies at a median of 1 job apiece; only 19.7% of served rows can name their company; 335
board_key groups are one employer split across two casings (43,067 rows); 5.1% of rows sit on
Boards that repeat a title 3x or more; a domain to join firmographics on is reachable for 13.7%.

**2026-09-21 (second pass)** — Prototyped a "Hot / actively hiring companies" ranking off the
ADR-0143 Board-delta ledger (pulled from HF: 33,480 Boards, 207 ticks, 2026-09-13 → 2026-09-21).

- Script: `rank_actively_hiring.py`
- Raw output: `artifacts/2026-09-21_actively-hiring-ranking.txt`
- Findings: `docs/company-curation/2026-09-21_hot-companies-actively-hiring.md`

Headline: the naive ranking is an agency leaderboard — `lever:jobgether` (an aggregator) leads on
new roles, AgileEngine runs 96% churn, eworgmbh 99% on two ATSes at once. The employer-type gate is
a prerequisite for this tab, not a follow-up. Churn ratio (7d-new ÷ stock) both ranks the tab and
detects the agencies. Traps handled in code: tick 1 is a baseline dump (196,824 rows vs 127),
`watch:` families double-count, `new` is a level not a flow, and 6,996 Boards were newly discovered
rather than newly hiring. Acceleration ("recently started hiring") needs history the ledger does
not have yet — 8 days old.

**2026-09-21 (third pass)** — Tested the churn-based employer-type classifier on a stratified,
hand-labelled sample of 114 Boards (of the 2,916 with >=25 open roles).

- Ground truth: `artifacts/2026-09-21_hand-labels-114-boards.txt`
- Features: regenerate with `measure_company_shape.py` — the 2.2 MB table is not
  committed, because it is derived and storage is a design constraint here
- Findings: `docs/company-curation/2026-09-21_employer-type-classifier-measurement.md`

**It does not work.** churn>=0.5 scores precision 60.9% / recall 9.9%; the best cheap combination
(name vocabulary OR churn) reaches F1 52.7 and would demote Cerebras Systems and Skylo Technologies.
Base rate of non-employers is 14.3%, and the mass sits in the 0.15-0.30 churn band (25% of 781
Boards), not at the top. IT services firms churn at ordinary rates and have ordinary names.
What does work: a 45-name curated denylist removes 73% of net growth from the top 20 Expansion
rows. Reframing: adjudicate the ~200 Boards actually displayed, not 33,480.
