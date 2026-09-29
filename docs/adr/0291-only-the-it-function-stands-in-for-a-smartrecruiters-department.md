# ADR-0291: Only the Information Technology function stands in for a SmartRecruiters department

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0017](0017-tech-role-filter.md) (the post-hoc tech gate),
[ADR-0068](0068-a-department-names-the-org-not-the-role.md) (the department veto),
[ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail gate reads the same
department), [ADR-0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md)
(#570's Dormant Boards) · **Issue:** #570 (options A and D)

## Context

SmartRecruiters leaves `department.label` null on most postings. #564 made `_department_of` fall
back to `function.label` whenever that happens, so that `tech_filter`'s rule 4 (a vague title in
a technical department is tech) could see something. `function` is not a company's own label. It
is SmartRecruiters' fixed taxonomy of about 35 values, and the filter reads four of them:

| function id | label | what the filter does with it |
|---|---|---|
| `information_technology` | Information Technology | rule 4 promotes a vague title |
| `engineering` | Engineering | rule 4 promotes a vague title |
| `sales` | Sales | rule 2 vetoes a generic "…Engineer" (`_NON_SOFTWARE`) |
| `manufacturing` | Manufacturing | rule 2 vetoes a generic "…Engineer" (`_NON_SOFTWARE`) |

(Tallied from 66,209 postings on 244 Boards walked live on 2026-09-29.) #570 found the
"Engineering" function promoting civil, construction and plant work. ADR-0250 has since removed
the abandoned Boards, so what is left is the fallback itself. Separately, `_NON_TECH_ROLE`, rule
4's title veto, ended in a bare `\b`, so it refused "Welder" and "Security Officer" and promoted
"Welders" and "Security Officers".

### The sample

On served table v298 (2026-09-29, 498,848 rows, read off HF), 4,536 SmartRecruiters rows are tech
only through rule 4 under a department of "Engineering" (2,965) or "Information Technology"
(1,571). A random 300 of them (`random.seed(570)`) were fetched from the live posting API one at
a time: all 300 answered 200 and `active: true`, and 285 had `department` null, so the function
was what promoted them. Two labellers read the 285 titles, one of them blind to the function.
They agreed on 263 (92%). No Engineering row had one labeller saying tech and the other saying
not tech. The three rows where they split that way were settled as not tech: two crowdwork "AI
Content Expert" postings, which ADR-0087 keeps out, and a training-course advert. A row either
labeller called borderline is counted as borderline (?).

| function | tech | ? | not tech | posted 2026 |
|---|---:|---:|---:|---:|
| Engineering (180) | **13** | 14 | **153** | 158 |
| Information Technology (105) | **63** | 16 | **26** | 79 |

The Engineering rows were AECOM, Bosch, City of New York, Egis, Ramboll and CIMA+: "Senior
Construction Manager", "Transmission Lines Designer", "Boilermaker", "ACCOUNTABLE MANAGER". The 13
tech rows were "Software Manager", "Sr. Data Modeler", "Product Owner", "Senior Security
Enginneer", a Check Point "Professional Services Consultant", two Chinese-titled Bosch AI and
embedded-software roles and similar. The IT rows are mostly IT work: 26 bctechpro "Computer Field
Technician" postings, SAP and Salesforce consultants, IT business analysts. Their non-tech share
is real but smaller ("Physical Security Specialist", "Senior CAD Technician", "Junior Invoice
specialist").

### The variants

| variant | Engineering rows (of 180) | IT rows (of 105) | Sales / Manufacturing function |
|---|---|---|---|
| (iii) today: any function | keeps 13 T, 14 ?, 153 N | keeps 63 T, 16 ?, 26 N | vetoes a generic "…Engineer" |
| **(ii) Information Technology only** | drops 13 T, 14 ?, 153 N | keeps all | no veto: rule 3 keeps it |
| (i) no fallback (#564 reverted) | drops all | drops 63 T | no veto |

Variant (i) loses more tech than it removes non-tech, so it is out. Between (ii) and (iii):

* **What (ii) removes.** Each served row was joined to its Board's live listing, walked in full on
  2026-09-29 (all 196 Boards with an "Engineering" rule-4 row, 57,411 postings). **2,755 served
  rows would leave, on 170 Boards:** `aecom2` 651, `boschgroup` 488, `cityofnewyork` 232,
  `egisgroup` 174, `jobsforhumanity` 129, `ramboll3` 85, `cima2` 77, `assystem` 66, `accorhotel`
  61. 2,449 of them were posted in 2026, so these are current postings. At the sample's rate
  that is about 200 tech rows lost (up to about 400 counting every borderline as tech) against
  about 2,300 non-tech rows removed. Most of the lost tech rows sit on software firms whose titles
  the title rules should catch on their own; #913 follows that up.
* **What (ii) lets in.** A null department no longer carries a Sales or Manufacturing veto, so a
  generic "…Engineer" there is kept by rule 3, as it is on any ATS that states no department.
  That was the behaviour before #564. On the same 196 Boards, 376 postings not in the Tech subset
  today would enter, plus 1 on a separate random walk of 60 served SmartRecruiters Boards (10,280
  postings). A read of all 377 titles found about 40 software, EDA or digital-design roles that
  today's veto drops: "WPF Developer" three times, "IT Solution Developer (Semiconductor)",
  "Principal Linux Driver Engineer", "Senior SCCM/MECM Engineer", "AI Development Engineer", seven
  digital or AMS verification engineers. Most of the rest are process, product, quality and
  field-application engineers at Renesas (207) and Bosch (90). A random 60 of the 377
  (`random.seed(858)`) were then labelled one by one: **3 tech, 15 borderline, 42 not tech**, and
  57 of the 60 were posted in 2026 (2 in 2025, 1 in 2024). Scaled up, about 20 tech, 95 borderline
  and 265 non-tech postings enter. The borderline ones are hardware validation, analog and
  mixed-signal design, product test and MCU application engineers. The 377 is a floor: it counts
  only the 244 Boards walked. The random walk found 1 gain on 60 served Boards, so the other
  roughly 800 served SmartRecruiters Boards would add about 15 more. Boards with no served row were
  not walked.

## Decision

1. **`smartrecruiters._department_of` falls back to `function.label` only when `function.id` is
   `information_technology`** (variant ii). The id is the taxonomy's own key, so a relabelled or
   translated label cannot change the answer. A posting that states a department is read as
   before. The pre-detail gate reads the same function, so a posting it no longer promotes is no
   longer fetched: on the 196 Boards the gate's passes fall from 16,012 to 11,131, and on the 60
   random Boards from 1,319 to 1,264. The latest run attempted 62,708 SmartRecruiters details
   (join log of run 36482634879).
   **Every function's label is still kept, as a new `Job.job_function`**, and
   `doc_prep.stored_facts` shows it as the served row's `department` when the posting states
   none. So `department` on the served row is what it was before this change, but the tech gate
   reads `Job.department` alone. `job_function` is None on every other ATS and never reaches the
   served table as a column of its own.
2. **`_NON_TECH_ROLE` takes an optional plural `s` before its closing boundary.** On served v298
   this refuses 329 rows on 22 Boards. 311 are security officers and guards on 10 Boards, 273 of
   them one guard firm's (`phenom:careers.sunstatessecurity.com`). The other 18 are drivers,
   electricians, content creators, account executives, customer-services and instructor titles.
   Every title was read. Four are arguable: L3Harris' "Associate, IT Customer Services", two
   "Trainers/Instructors for IBM Maximo Application Suite Training" and a volunteer bootcamp
   instructor. Each has the same
   verdict as its singular already had. `technician` stays off the list in both numbers.
3. **`TECH_FILTER_VERSION` goes to 7**, so Trends reads the change as a definition change and not
   as the market moving.

## Alternatives considered

* **Keep the Engineering fallback** (variant iii). It keeps about 200 tech rows at the price of
  about 2,300 non-tech ones, nearly all posted this year. The gate is recall-biased, but at nearly 12 non-tech
  rows per tech row the fallback is not a recall booster for vague titles. It is the index
  admitting a construction company's whole board. The tech rows it held are mostly titles the
  title rules should catch on their own ("Software Manager", "Data Modeler", "Product Owner").
  Widening rules 1-3 for them is a filter change for every ATS, and needs its own measurement.
* **Keep every function except Engineering.** This removes the same 2,755 rows and keeps the
  Sales and Manufacturing vetoes, so nothing enters. It was rejected because those vetoes cost
  recall that #564 never measured, and no other ATS without a department loses it. The labelled
  sample of 60 gains makes the trade closer than the first read of the titles suggested: about 20
  tech and 95 borderline postings regained against about 265 non-tech ones admitted. That is still
  the direction ADR-0017's recall bias asks for (no tech Job dropped, some non-tech creep
  tolerated), and it is small next to the 2,300 non-tech rows the Engineering fallback removes.
* **Fall back to Engineering only on software companies**, keyed on the posting's `industry`.
  Of the 13 tech Engineering rows it would keep 5, since Bosch, Intuitive and Renesas file as
  automotive, medical-device and semiconductor companies. Another company-configured field to
  trust, for little gain.
* **Let `department` go null when the function is not Information Technology** (this ADR's first
  draft). The gate verdicts are the same as the decision's, but `department` is also what the
  served row shows (the Space's `/job` route and MCP `get_job`), and it is a fact field that a None
  overwrites. On the 244 walked Boards, 6,041 of the 12,276 postings that stay tech state no
  department and show a non-IT function (`boschgroup` 1,018, `aecom2` 639, `renesaselectronics`
  549), and each would have lost it on its next scrape. Before #564 they showed nothing, but #564
  has been live since 2026-09-22. `Job.job_function` costs one more field on every row, None except
  on SmartRecruiters.
* **Revert #564** (variant i). It also drops the IT-function rows, 63 of the 105 sampled being
  tech.
* **Pluralise `_NON_SOFTWARE` too.** Measured on v298: 134 rows would flip, mostly "Mechanics"
  and "Civils" titles, but with software or systems work among them ("Flight Mechanics and
  Simulation Engineer", a KLA systems design engineer). That is a different trade-off and is not
  part of #570's option D.

## Consequences

* **3,081 served rows leave over the next two scrapes of their Boards** (ADR-0083), 2,755 from
  the fallback and 329 from the plural veto (3 are both), on 191 Boards. About 377 postings enter:
  the Sales and Manufacturing "…Engineer" titles on these Boards, most of them not tech (the
  labelled sample above). Dropping a function can only lift a veto, never add one, so no kept row
  changes verdict because of it.
* **No served row's `department` changes.** Replaying `parse` and `stored_facts` over the 66,209
  walked postings gives the same department as before on every one, and the same tech verdict as
  the IT-only rule on every one.
* **About 200 current tech rows go with the Engineering creep**, per the sample. They are the
  known cost of this decision, and recoverable by title rules in a later filter round (#913).
* **SmartRecruiters' detail attempts fall** by about 4,900 a run, from 62,708 to about 57,800
  (-8%), if every walked Board is in the run's slice. That figure is projected from the walk, not
  measured on a run. Check the join log's `smartrecruiters detail loss events … attempted` line
  after this ships; #570 closes once that line is read.
* The blind hold-out (recall 84.6%, precision 82.0%) and the labelled set (344 of 345 tech kept,
  84 of 540 non-tech kept) are unchanged. Both are title-only or carry no SmartRecruiters
  function, so the served-table measurement is the one that counts here.
