# The title decides, and the titles the department was carrying — 2026-09-17

`TECH_FILTER_VERSION` 1 → 2. Measured over the **489,661-posting pre-filter corpus** of run
`35193130454` (its `scrape-fragment-{0,1,2,4}` artifacts — `data/jobs/` is ephemeral and reaches
neither git nor HF, so those artifacts are the only source).

## Why

Extending the ADR-0166 pre-detail gate to oracle, zoho, icims, bamboohr and jobvite needs the
**title** to carry the verdict, because those scrapers only learn `department` from the detail
page — the page the gate exists to skip. So the question was: what is the department doing that
the title cannot?

The answer, read off the corpus rather than assumed: across those five ATSes, 12,321 postings were
kept *only* by their department. Reading them split three ways.

| what kept it | count | what it actually was |
| --- | ---: | --- |
| `tech-department` (rule 4) | 6,539 | a mix — real software titles the strong list missed, and admin/sales roles sitting in an IT org |
| `strong-software-signal` | 3,494 | **rules 1-2 reading the department**: "Content Creator" in "Software development" matched `software dev` |
| `generic-tech-token` | 2,288 | same mechanism: "Plumber" in "Engineering & Facilities" matched `engineering` |

That third of the corpus was not rule 4 at all. `classify` built `text = f"{title} {department}"`
and ran rules 1 and 2 over it, so a department could decide a *title* question — and could do so
while bypassing rule 4's own guards (`_HIRING_DEPT`, ADR-0087). The department was counted twice.

## What changed

**1. Rules 1 and 2 read the title.** The department keeps exactly one job, rule 4, where its
guards live.

**2. Rule 4 no longer promotes a title that names a different profession** (`_NON_TECH_ROLE`).
This is the product ruling this change was built on: an Administrative Assistant in a Software
Engineering department is not a software job. Deliberately a list of *clear* non-software roles,
not vague ones — the gate stays recall-biased, so `Analyst`, `Associate` and `Intern` in a
Software Engineering department are all still kept.

**3. A department that says nothing about software is blanked** (`_NOT_TECH_DEPT`): "Security
Officers", "Engineering & Facilities", "Data Entry". Blanked rather than vetoed, so a genuine
software title inside a facilities org still passes on its own signal — `Software Engineer,
Facilities Systems` is unaffected.

**4. The strong list gained the roles the department was covering for.** Every entry was found by
reading rule 4's rescues, not invented: the architect family (`solutions architect` was plural-only,
so `Solution Architect`, `Cloud Solution Architect`, `Java Architect`, `AI/ML Architect`,
`Network Architect` all fell through), `System Administrator` (`systems` was plural-only too),
penetration/software testers, Scrum Master, the analyst family, the `IT <role>` family, help
desk/desktop support, Power BI/Tableau, the SAP/Salesforce/ServiceNow/ABAP consultant family, and
DBA.

## What it moved

**+1,374 postings, +1.3%** — 109,058 indexed against 107,684. Near-neutral in size, a long way in
composition: **+6,414 in, −5,290 out.**

| in | out |
| --- | --- |
| Data Analyst, Technical Writer, Solution Architect, Technical Project/Program Manager, Scrum Master, Information Systems Manager, System Administrator, IT Support Technician | Security Officer, Loss Prevention Officer, Plumber, Painter, Carpenter, Electrician, Administrative Assistant, and a large block of "Remote Data Entry / Work From Home" listings |

The biggest proportional move is **freshteam, −54%** — and all 736 of its removals are one thing:
`Administrative Assistant / Data Entry Clerk (Work From Home)`-class listings in a department
called **"Data Entry"**, which matched `\bdata\b` in `_TECH_DEPT`. freshteam reading 80.3% tech was
that artefact, not a real signal.

Read the changed values, not the total (ADR-0066's discipline). One genuine loss surfaced that way
and was fixed rather than accepted: jobvite was dropping `IT System Administrator (TS/SCI with
Polygraph)`, a cleared sysadmin role, because the existing administrator pattern was `systems`
plural-only.

## What it does **not** do

It does not unlock the gate for the five ATSes it was aimed at. A title-only gate's recall loss:

| ATS | before | after |
| --- | ---: | ---: |
| oracle | 46.0% | **34.8%** |
| zoho | 47.4% | **35.7%** |
| icims | 25.8% | 19.6% |
| jobvite | 40.6% | 13.4% |
| bamboohr | 13.6% | 10.2% |
| *corpus-wide* | 20.7% | 14.9% |

jobvite and bamboohr are now in a range worth re-examining; oracle and zoho are not. The residual
there is not a pattern gap that more regexes would close — it is genuinely vague titles
(`Analyst`, `Associate`, `Specialist`, `Consultant`) that only the department distinguishes, and
the gate is deliberately keeping those. Closing that last third would mean giving up the recall
bias itself, which is a different decision from the one made here.

## Reproducing

Download a few `scrape-fragment-*` artifacts from a recent successful `pipeline.yml` run, then
compare `is_tech(title, department)` against `is_tech(title)` per row, bucketed by
`classify(...).reason`. The reason field is what separates the three mechanisms above; a bare
true/false count conflates them and was why the first pass at this misread the problem as a
missing-patterns one.
