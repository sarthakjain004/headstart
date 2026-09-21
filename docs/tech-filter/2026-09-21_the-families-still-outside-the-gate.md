# The families still outside the gate — 2026-09-21

`TECH_FILTER_VERSION` 2 → 3. Measured over the **332,383-posting pre-filter snapshot** in
`data/jobs/` (16 ATSes, scraped 2026-08-28 on this machine — `data/jobs/` is ephemeral and reaches
neither git nor HF, so it is a local snapshot, not a run artifact; it is the same corpus ADR-0068
was measured on).

## Why

The question that started this was narrow: does the gate keep "Forward Deployed Engineer" and
"Member of Technical Staff"? Neither is exotic — the first is the Palantir-lineage role every AI
infrastructure company now hires for, the second is the standard IC title at the AI labs. The
answer was that FDE passed 90% of the time and MTS 79%, and in both cases what decided it was the
*department*, not the title.

Version 2 (`2026-09-17_the-title-decides.md`) had just taken the department away from rules 1-2,
for good reasons. That made this class strictly worse: a title naming no discipline of its own now
has nothing to fall back on. "Member of Technical Staff, Post-Training" under a `Modeling`
department is not a vague title — it is a precise one, in a vocabulary the strong list had never
been taught.

So rather than patch the two titles asked about, the whole drop pile was read: 264,877 dropped
rows, 137,031 distinct titles, aggregated by frequency and then probed family by family.

## What was actually missing

The head of the drop pile is correctly non-tech (personal trainer, sales representative, teaching
jobs, veterinarian). Everything below is tail. Counts are rows version 2 dropped.

| family | rows | why version 2 missed it |
| --- | ---: | --- |
| enterprise platform | 589 | the arm required the product and the role word to be **adjacent**; the module name almost always sits between them |
| Member of Technical Staff | 124 | names no discipline; nothing in the strong list covered it |
| QA/test role words | 66 | `… tester` and `… analyst` arms carried neither `qa lead` nor `test analyst` nor `manual tester` |
| silicon design (VLSI/FPGA/ASIC/RTL/DV/PD) | 61 | never represented |
| security operations | 44 | the `… analyst` arm covers `soc analyst`, not `SOC Specialist`, `SOC Manager` or `Security Operations Lead` |
| business intelligence | 43 | `(power bi\|tableau\|looker\|qlik) …` was there; `business intelligence analyst` and `BI analyst` were not |
| Forward Deployed Engineer | 28 | passes rule 3 on `engineer` alone, so a Sales/GTM department vetoed it at rule 2 |
| AI/ML research scientist | 20 | the `\b(ai\|ml)[\s/&,-]*(…\|scientist)` arm needs the role word **adjacent** to `ai`/`ml` — "AI Research Scientist" has a word in between |
| applied scientist | 17 | never represented |
| bioinformatics / computational biology | 10 | never represented |

Four families that looked missing at first turned out to be already covered by version 2 and were
dropped from the change: data analyst, technical program/project/product manager, solution
architect, system administrator, network architect and DBA all measured **0 still-dropped rows**.
That is the whole value of rebaselining against the current classifier rather than the one in the
working tree — a first pass built against a two-commit-stale base would have re-added all six.

## Three patterns narrowed because the measurement caught them misfiring

Each is now a named test, because each looked obviously correct before it was run against real
titles.

- **`\brtl\b` matches a broadcaster.** 2 of the 46 distinct titles it hit were "RTL Nieuws" media
  roles ("Media Consultant RTL / Veltins"). Shipped as `rtl (design|verification)`.
- **`security specialist` matches physical security.** "EHS and Security Specialist" is
  environment/health/safety. Bare `security` takes only `operations`; `specialist` is allowed only
  behind `soc`/`infosec`/`cyber security`.
- **`\btpm\b` is not a technical program manager.** It is Total Productive Maintenance in
  "Assistant Manager - Plant Management (TPM)" and Trade Promotion Management in "Senior Business
  Analyst - with TPM, CPG, and retail" — 4 of its 10 occurrences. Excluded entirely; the
  spelled-out form (already in version 2) covers everything real.

## A recall regression the count alone would have hidden

Widening the enterprise arm meant rewriting it from `(product) (role)` to
`\b(product)\b.{0,24}\b(role)\b`. Adding the closing `\b` **lost two rows** — "ServiceNow
Developer**s**" and "Higher Education Workday Consultant**s**" — because the form being replaced
had no closing boundary at all and matched the plural inside the singular. That run still gained 1,039 rows, which buried
it completely; only a row-level old-vs-new diff surfaced it. Shipped as `s?\b`, and pinned by
`test_the_platform_arm_still_matches_a_plural_role_word`.

This is the same lesson ADR-0066 records for `experience.py`: measure **changed values**, not only
coverage.

## Net effect

| | rows | share of corpus |
| --- | ---: | ---: |
| version 2 keeps | 67,506 | 20.31% |
| version 3 keeps | 68,548 | 20.62% |

**+1,042 in, 0 out** — purely additive, so unlike version 2 the composition moves exactly as far as
the total. `verify_tech.py`'s self-consistency check (no dropped job may match a strong signal)
stays at 0.

## Deliberately not included

Three families were measured, considered and left out, so the next reader does not re-derive them:

- **Bare "Product Manager"** — 2,291 rows. Not an engineering role, and an order of magnitude
  larger than the technical form version 2 already keeps. A separate product call, not a gap.
- **IT support / service desk / desktop support** — version 2 already carries `help[\s-]?desk`,
  `desktop support` and the `\b(it|ict)[\s/-]+(support|…)` arm, so the remaining rows are BPO
  call-centre listings phrased as "helpdesk agent", which are not tech roles.
- **"Research Scientist" unqualified** — 152 rows, but at a biotech it is a wet-lab job. Only the
  `ai`/`ml`-qualified form ships.
