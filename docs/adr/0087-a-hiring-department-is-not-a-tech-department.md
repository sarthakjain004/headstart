# ADR-0087: A hiring department is not a tech department

- Status: Accepted
- Date: 2026-08-25
- Amends [ADR-0017](0017-tech-role-filter.md) on what may act as the department recall booster;
  sits alongside [ADR-0068](0068-a-department-names-the-org-not-the-role.md), which corrected the
  same category error on the *disqualifier* — though by a different mechanism, see below

## Context

ADR-0017's rule 4 promotes a Job to tech when its `department` names a technical area, as a recall
booster for titles too vague to decide on their own ("Intern" under "Engineering"). The department
list matches on whole words, `data` among them.

`prolificacademicltd` files 2,459 of its 3,679 Jobs under the department **"Human Data
Recruitment"**. Prolific's business is recruiting *people* to perform AI-training and research
tasks, so that label means "the team that recruits humans to produce data". The Jobs under it are
the task listings themselves: *AI Trainer – Fluent Serbian Speaker*, *Cardiologists (Freelance -
Remote)*, *Linguist/Translator - AI Training*, *Fluent Russian Speakers - UK (Task-Based)*.

`\bdata\b` matched that label, so rule 4 promoted **2,427** of those listings into the tech index
regardless of title. They then surface against searches like "AI engineer".

The scale is what makes it a defect rather than the tolerated creep ADR-0017 explicitly accepts.
A survey of 239 live Boards / 15,549 Jobs across six ATSes found rule 4 promoting **259 Jobs total,
from 51 distinct departments** — and inspection showed those 51 are overwhelmingly genuine
(`Information Technology`, `R&D`, `Technology`, `Data`, `IT`, `Security`, `Cloud Infrastructure &
Operations`). One Board contributed roughly **ten times** the entire rest of the sample.

## Decision

A department that names a **hiring function** — `recruit*`, `staffing`, `talent acquisition` — does
not act as the rule-4 recall booster.

The reasoning is that such a label describes *who does the hiring*, not the discipline of the role,
so a technical word appearing inside one is incidental. ADR-0068 corrected the same category error
in the other direction: there, an org label was wrongly *vetoing*; here, a hiring label is wrongly
*promoting*.

**But not by the same mechanism, and the difference is forced.** ADR-0068 *strips* the org word and
lets what remains still decide, explicitly preferring that over discarding. Review proposed the same
here. It does not work: the hiring word and the technical word are different words, so stripping
`Recruitment` from "Human Data Recruitment" leaves "Human Data", which still matches `\bdata\b` and
leaves the defect entirely unfixed (verified). Stripping is only equivalent when the word being
removed is the one the rule fires on, which was true for ADR-0068 and is not true here. Discarding
the booster is therefore the only form of this fix that fixes it.

`sourcing` was considered and excluded. It is a hiring term of art, but in Department labels it
overwhelmingly means procurement: all 10 live occurrences in a 418-Board, 22,573-job survey were
supply-chain ("Category Sourcing", "Sourcing & Quality", "Global Sourcing", "Product Sourcing"), so
vetoing on it would fire on the wrong sense of the word.

**Scoped to rule 4, and deliberately not a disqualifier.** A title that names a software role still
passes on its own signal at rules 1–3. That is why 193 of the same Board's Jobs — titled *AI
Engineer/ML Engineer - Senior Developers* — are untouched. The veto only withdraws the benefit of
the doubt that a *vague* title borrows from its department; it never overrides a title that speaks
for itself.

## Consequences

Measured live, 2026-08-25:

- **On the Board in question:** tech count 2,620 → 193. The `tech-department` reason disappears
  from it entirely; every survivor is `strong-software-signal`. Those 193 survivors are *still*
  crowdwork ("AI Engineer/ML Engineer - Senior Developers - AI Training - Austin, US") — they pass
  because their titles name an engineering role, which is rule 1 behaving exactly as designed. That
  is an explanation of the mechanism, not a claim that keeping them is correct.
- **Does the veto fire when it should? Yes — measured directly.** On `prolificacademicltd` it
  fires 2,427 times, taking the Board from 2,620 tech Jobs to 193.
- **Does it fire where it should not? No, across 1,300 live Boards / 130,229 Jobs.** Replaying
  old-vs-new classification over twelve ATSes — including the formal-HR-taxonomy ones (workday,
  successfactors, eightfold) and with no Board-size cap — dropped **0** Jobs. The run was ended
  early by ATS rate limits, having been flat at zero throughout.
- **Why it is that rare.** A census of 418 Boards / 22,573 Jobs enumerated **1,678 distinct
  department labels** and found **none** carrying both a technical word and a hiring word. Hiring
  words do occur (147 Jobs: `Participant Recruitment`, `The Hospitality Recruiters`,
  `Talent Acquisition`…) but never beside a technical one.

Those first two bullets are deliberately separate, and the separation was the main thing review
corrected. "0 Jobs dropped" cannot on its own distinguish *"the veto is safe"* from *"the veto
never ran"* — and the first sweep was in fact the latter, having capped Boards at 500 Jobs when the
defect's own shape is a 3,364-Job Board. The sweep bounds the **cost**; only the direct measurement
on the Board itself establishes the **benefit**. Note `prolificacademicltd` is not in the sweep's
sample, so the two numbers are genuinely independent rather than one implying the other.

The asymmetry is why this mattered: ADR-0017 accepts creep but forbids dropping a real tech Job.
It also bounds what this buys — a fix that fires on one label in 1,678 is narrow by construction.

**What this does not fix.** This is a narrow correction to one word's worth of blast radius, not a
repair of rule 4. Three gaps are known and deliberately left, all verified live:

1. **Rules 2 and 3 bypass it entirely.** They search `f"{title} {department}"`, so a hiring
   department that *also* carries a generic role token promotes any title before rule 4 is ever
   reached: `classify("Cardiologists (Freelance - Remote)", "Engineering Recruitment")` is tech,
   by `generic-tech-token`. Prolific is caught only because "Human Data Recruitment" happens to
   contain no generic token. Closing this would mean touching the disqualifier tiers, which is a
   larger change with a real recall cost, and was not attempted here.
2. **Near-miss labels.** The veto matches the word, not the concept, so `HR Data`, `Talent - Data`,
   `People Operations - Data`, `Workforce Data` and `Human Data Operations` all still promote a
   vague title. Prolific is caught only because it spells out "Recruitment".
3. **Rule 4's single-word matching generally.** Any label containing one technical word promotes —
   `Project Finance & Infrastructure` appeared in the survey.

Narrowing `data` itself to phrases (`data engineering`, `data science`) was rejected as the general
fix: the survey shows bare `Data`, `Data analytics`, `CS- Data & Analytics` and `Data Excellence &
Strategy` are all real tech departments that would stop being recognised, which is a recall loss and
therefore the one outcome ADR-0017 forbids.

**Rejected alternatives.** Blocklisting the single Board (as `registry.DISABLED_ATS` does for the
join scraper) was rejected as not generalising to the next staffing company with the same shape.
Doing nothing was rejected on scale: 2,427 non-tech listings from one Board is not the marginal
creep ADR-0017 had in mind.
