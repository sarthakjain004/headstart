# Tech filter version 4: spellings the gate could not see, and the trades it let in

2026-09-23. `TECH_FILTER_VERSION` 3 → 4. Decision record: [ADR-0179](../adr/0179-the-tech-filter-stays-english-and-its-trade-vetoes-stand-down-for-infrastructure.md).

Version 4 went through two critiques and three code reviews before landing. A critique rated
version 3 at 5/10 and the first cut of version 4 at 6/10. The figures below are for the final
state.

## Headline numbers

| measure | version 3 | version 4 |
|---|---|---|
| **blind hold-out recall** (English Indeed, 800 titles, row-weighted to 557,580 rows) | n/a | **84.6%** (66.1% if every "ambiguous" title is tech) |
| **blind hold-out precision** | n/a | **82.0%** |
| dev-silver recall (English Indeed rows whose description has ≥5 core dev tokens, n=45,370) | 90.28% | 92.13% |
| served table kept (v654, 514,163 rows) | 513,389 | 496,096 (+1 in, −17,294 out) |
| pre-filter snapshot kept (332,383 rows, July) | 68,600 | 69,192 (+1,852 in, −1,260 out) |
| English + non-English Indeed kept (679,687 rows) | 160,294 | 163,569 (+7,830 in, −4,555 out) |
| speed (150k titles, best of two) | 32.4k rows/s | 29.9k rows/s |

The hold-out is the only unbiased number here. The others either come from a corpus with
survivorship bias (the served table only holds what v3 kept, so it can show losses but never
misses) or from a proxy (dev-silver covers core developer jobs only).

## How losses were checked

The owner's rule was that no real tech job may be lost. **Every served row that version 4 drops
was read by hand.** That is 7,263 unique titles, read in four passes:
- **1,553 titles, read by me:** the first cut's families.
- **5,249 titles, read line by line by a reviewer agent** after the critique round's vetoes. It
  checked ambiguous rows against their descriptions and found 28 real tech jobs and 24 plausible
  ones. All 28 and 15 of the 24 are kept now.
- **The remaining rows, read by me:** the speed change's residue and the building/construction
  department veto.
- **The second review round, read by me:** physical-security titles under a bare "Security"
  department, and the rows `_INFRA_CONTEXT` stops sparing once `technology`/`technical`/
  `infrastructure` leave it (water infrastructure, MEP building technology, supplier quality).

A script then confirmed that no current loss falls outside the read sets.

Real tech jobs these reads caught, each now kept and covered by a test:
- **Site roles and data-center roles:** Oracle's `DC Ops` site engineers; IP-network, SCADA,
  telecom and rail-communications site engineers.
- **Frameworks and platforms:** Ruby on Rails developers (and "Ruby on Rail UI Engineer"); JD
  Edwards CNC administrators; Epic EDI developers; PLM/Teamcenter admins.
- **Security and presales:** a cybersecurity platform manager; "Information Protection Manager"
  under a Security department; software presales solutions consultants in "Sales Engineering"
  departments; "Pre-Sales Consultant — Enterprise Cybersecurity".
- **AI:** Canon's "Assoc Analyst, Ai" (it evaluates AI/ML models).
- **Front-end roles:** RakutenTV's "Frontend Manager"; a React "Front End Team Member".
- **Titles carrying a code word:** "LabVIEW Quality Engineer", "SW Design" roles, "AI/ML
  Robotics Engineer – Environmental Perception".
- **Glued and compound spellings:** "SeniorSolution Architect", "GPU Memory Subsystem
  Architect", "CIAM Architect".

## What changed

- **Spellings (strong list):**
  - `_` used as a separator, and glued levels ("SDE3", "SRE2").
  - Plurals, and Engr/Engg/Eng/Dev when tied to a discipline word.
  - DevSecOps, and IT roles with a word before the role or IT after it.
  - SOC/DFIR/IAM/threat/vulnerability families; the MTS spellings; "Chief Technology Officer".
  - Reinforcement learning, JD Edwards, OutSystems/Mendix/Appian.
- **Recall groups from the Indeed harvest's English rejects:**
  - A stack word beside a non-developer role ("Kubernetes L3 Lead", "Snowflake Admin").
  - Support tiers ("L2 Application Support").
  - Cloud and admin roles.
  - AI/ML directly before lead/architect/intern.
  - "Application Development"; "Technical Delivery Manager".
- **Trade vetoes (title only):**
  - The vetoed families are site, MEP, QA/QC, highway and its siblings (bridge, traffic,
    water/wastewater, transportation, environmental, substation, rail, drainage, facilities),
    supplier quality, and business developers.
  - Each stands down when the title or department names IT, network, telecom, data-center,
    software, data, AI/ML, an enterprise platform, integration, communications or cyber work
    (`_INFRA_CONTEXT`).
  - "Rails" and "air traffic" are exempt.
- **Rule 0 (`_STRONG_NOT`):**
  - It sets aside a phrase that trips a strong signal while naming another trade: the spaced
    retail "Front End Manager/Clerk/…", "CNC Programmer", "JD/LLM", and discipline engineering
    managers.
  - A remainder that names code is kept (`_CODE_WORK`); anything else goes to rule 4.
- **Rule 4:**
  - It no longer promotes a vague title from a department naming a non-software discipline or
    a building/construction trade.
  - It still does when the department or title names software or IT, or when the department's
    only such word is `sales` or `bridge`.
  - A department of just "Security" is not vetoed, because it holds infosec teams, but
    physical-security titles under it (officer, patrol, correctional, event security, CCTV…)
    are refused unless the title names information, technical, cyber, privacy, data or IT work.
- **Strong arms respect vetoes:** "Sales Engineer – AI", "Cyber Sales Manager", "Sales Engineer
  – Cybersecurity" and "Cyber Claims Specialist" no longer override them. Pre-sales is exempt.
- **A developer is software whatever the sector:** the trade vetoes stand down for "Rail Systems
  Developer" and "Traffic Simulation Developer", but not for "Business Developer".
- **"Quality engineer" is generic** unless it sits beside software, data, analytics, systems or
  test work.
- **Speed:**
  - The strong list is tried only where a word starts. That includes a lower/upper join, so glued
    words are still read.
  - v4's longer list had cost 2.4x; this brings it back to within about 8% of v3 (29.9k against
    32.4k rows/s; the rest is the longer list itself).
  - It also retires accidental v3 substring hits: "Geotechnical Project Manager", "Assoc Analyst
    Procurement", "Automobile Engineer" (matched as "mobile engineer").

## Evaluation sets

- **`tests/fixtures/tech_filter_eval.tsv`:** 971 titles from our own data, labelled blind by two
  labellers (973 of 1,000 agreed before non-English rows were dropped).
  - It is a **regression set, not a recall measure**. 300 rows came from titles version 4
    changed, and patterns were added for its misses.
  - Version 4 keeps 344/345 tech titles and 85/540 non-tech ones.
- **`tests/fixtures/tech_filter_holdout.tsv`:** the blind hold-out behind the headline numbers.
  - 800 English Indeed titles, drawn at random after the code was final: 500 dropped, 300 kept.
  - `scripts/eval/tech_filter_holdout.py draw` replays the draw from the harvest and reproduces
    it exactly; `estimate` recomputes the figures for the current filter.
  - **Weighted by row, not title.** The draw sampled postings; each title's `rows` column counts
    how many drawn postings it stands for, and the estimate weights by that. (An earlier figure,
    84.7%, weighted each title once and so under-weighted common titles.)
  - **Ambiguous titles (72 of 800) are left out of the headline figure.** They are mostly bare
    "Product Manager"/"Analyst"-type titles. Counting all of them as tech drops recall to 66.1%,
    so the true figure depends heavily on whether such roles count as tech jobs.
  - The design strata are the draw-time filter's (commit `6a1b3630`), so the same labels
    estimate any later version fairly.
  - Two labellers labelled it, with every output line echoing its title. A first pass without
    the echo had drifted a row out of alignment in places; this pass verified 800/800 and agreed
    on 783.
  - Nothing was tuned on it. Its CI tests only stop the miss and false-positive counts rising
    (22 of 241 tech titles dropped, 51 of 487 non-tech kept).
  - It comes from the same English Indeed pool whose rejects were mined for recall patterns, so
    although nothing was tuned on its titles, the recall figure is probably somewhat optimistic.

## Still open

- **The hold-out's 22 misses:** mostly IT and business-systems titles ("Manager - IT", ("ICT Business Analyst",
  "Assistant Manager IT", "Staff Administrator (Database)", "SharePoint Platform SME"). Fixing
  them from the hold-out would spend it. A further round should mine such titles from the
  Indeed rejects, and then draw a fresh hold-out.
- **A title ceiling:** the critique found about 55% of dev-silver misses have no title pattern
  that could catch them ("ERP Advisor", "AI/ML Computational Science Associate"). Getting past
  that needs a description fallback, which ADR-0166's listing-time gate blocks on six ATSes.
- **Deferred with measurements:**
  - Project, service and process engineer vetoes: each would drop 3,500–6,000 served rows,
    about 140 of them naming systems or software work.
  - Bare QA/tester: mostly food, pharma and lab QA.
  - AI strategy/consulting/PM titles.
  - PLC programmer (controls code).
  - Crowdwork "AI Trainer" titles.
- **Precision still tolerated:** the bare "…engineer" rule. The critique estimated about 68k
  non-tech served rows under it.
- **Judgement calls left as dropped** (the reviewer's "maybe"s): rail operations modelling,
  quantum-chemistry modelling, facility simulation, and similar.
- **Non-English:** out of scope by decision (ADR-0179).
