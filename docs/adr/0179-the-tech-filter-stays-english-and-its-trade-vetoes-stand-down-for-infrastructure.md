# ADR-0179: The tech filter stays English, and its trade vetoes stand down for infrastructure

**Status:** accepted · **Date:** 2026-09-23 · **Amends:** [ADR-0017](0017-tech-role-filter.md)

## Context

A critique of `headstart.tech_filter` (TECH_FILTER_VERSION 3) measured two separate kinds of
failure. **Recall:** real English tech titles the gate could not see — `_` used as a separator
("Application Developer_5"), a level glued to an acronym ("SDE3"), plurals ("PHP Developers"),
abbreviations ("Software Engr II", "Java Lead"), and whole families (DevSecOps, "IT Project
Manager", cyber/IAM roles). **Precision:** the bare "…engineer" rule admitted the construction
trades in bulk (Site, MEP, QA/QC and Highway Engineers), plus "Business Developer", the retail
"Front End Manager", "CNC Programmer" and "Mechanical Engineering Manager".

It also measured the gate as **English-only**. Non-English tech titles fell through: 14.5% of
tech-described Japanese jobs were kept, against 92% of US ones. The critique recommended adding
a multilingual role vocabulary.

## Decision

1. **The gate stays English.** No non-English role vocabulary is added. The product's search
   corpus is English (CLAUDE.md), and the owner confirmed the gate follows it. A non-English
   title still passes when it carries an English signal ("Senior Java Developer (m/w/d)").
2. **Recall fixes are English spellings and families only**, each tied to a discipline word
   where the bare form is ambiguous: `eng`/`dev` never count alone, because "ENG/SPA" is a
   language pair and "Business Dev" is sales.
3. **The new trade vetoes stand down for infrastructure.** Site, MEP, QA/QC, highway and
   business-developer titles are refused only when neither the title nor the department names
   IT, network, telecom, data-center, SCADA, software, automation or test work. Measured over the
   served table, every real tech job these vetoes would otherwise have dropped carried one of
   those words: Oracle's "Site Engineer II" in `DC Ops`, "Site Engineer - IP Network",
   "Software QA/QC Engineer", and a "QA/QC Engineer" in `IT`.
4. **A phrase that trips a strong signal while naming another trade is set aside before the
   signal is read** (`_STRONG_NOT`, rule 0). A real signal elsewhere in the title still counts,
   and so does software work named beside the trade ("Industrial Engineering Manager - MES").
   A title with neither goes on to rule 4, whose own guards keep the trades out. Joint titles
   are exempt ("Firmware & Electrical Engineering Manager").

   The first cut refused such a title outright. Review measured the cost: that dropped
   RakutenTV's "Frontend Manager", a software role at a streaming platform, and "JD Edwards CNC
   Administrator", where CNC is the ERP's own admin layer. Rule 4's guards do the same job
   without those losses.
5. **Project, service and process engineers are not vetoed,** although the critique asked for
   it. Measured on the served table, each veto would drop 3,500–6,000 rows, including about 140
   unique titles that name systems, software or digital work ("Security Service Engineer",
   "Control Systems Project Engineer", "Digital Process Engineer"). That is too much recall risk
   for a recall-first gate.

## Consequences

- On the served table (v654, 514,163 rows) the change removes 2,525 rows and adds 1. All 1,553
  lost titles were read by hand, and none is a software or IT role. On the 332,383-posting
  pre-filter snapshot it adds 1,163 and removes 195. Detail:
  `docs/tech-filter/2026-09-23_spellings-and-trades.md`.
- Non-English tech jobs stay out of the feed and trends as well as the index. If multilingual
  retrieval is ever added, this gate has to be widened in the same change, or those jobs will
  never reach the index at all.
- Crowdwork "AI Trainer" gig titles are unchanged: kept where they were kept before. Whether they
  belong in the index is a separate decision (ADR-0087 covers only the department booster).
