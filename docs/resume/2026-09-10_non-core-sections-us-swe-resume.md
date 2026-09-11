# Non-core sections on a US software-engineering résumé

**What this answers:** which blocks beyond Contact / Skills / Experience / Projects / Education
belong on a résumé for a US software-engineering role — and, for each one that does not, *why not*,
and where it goes instead.

**Why this repo cares.** The résumé builder's Layer 1 is a catalogue of **Component** types
(`src/headstart/ui/static/resume/resume_components.js`). Adding a Component is cheap and adding it
wrongly is expensive: every registered **Layout** inherits it, every worked example has to stay
consistent with it, and a block that does not belong on an industry résumé makes the product worse
at the one thing it exists to do. So "does a tech résumé really carry a Publications section?" is a
catalogue question, not a styling question, and it needs an answer from outside our own taste.

This document is the evidence base for that decision. It is deliberately about **what mainstream
career services endorse**, not about what looks good.

---

## 1. The load-bearing distinction: a résumé is not a CV

Almost every disagreement about non-core sections dissolves once this is stated, and every
university career service publishes it explicitly. It is the single most useful fact in this
document.

### UNC Office of Postdoctoral Affairs

*The Difference Between a Resume and an Academic CV* (February 2018) —
<https://research.unc.edu/wp-content/uploads/2018/02/Handout-Difference-Between-a-Resume-and-an-Academic-CV-Feb-2018.pdf>

The handout is the cleanest statement of the split found anywhere. Paraphrasing its two halves:

- **The résumé** is what industry — meaning non-university — positions ask for. One to two pages,
  built around education, work and research experience, and relevant skills. It may also carry
  community service and leadership. The handout is explicit that brevity is not a stylistic
  preference but a consequence of how many applications a recruiter reads. Crucially, it names the
  material that does *not* go on it — publications, presentations, professional development, grant
  writing, teaching — and says that this can be "made available on request",
  typically through LinkedIn, ResearchGate, PubMed, Google Scholar, or a personal site.

- **The CV** is the academic document, and the handout lists what it carries: teaching and research
  experience, publications, presentations, professional development, professional credentials,
  grants, patents, awards, honours, and professional affiliations.

Read those two lists side by side and the answer to most "should I include X?" questions is already
written down. Nearly every block people are unsure about — publications, presentations, patents,
affiliations, honours — appears in the **CV** list and is named in the résumé paragraph only as
something to *defer*.

### Harvard Office of Career Services

*Resumes and Cover Letters for PhD Students* —
<https://apsanet.org/Portals/54/web/Handout%204%20-%20Harvard%20Resume%20Examples.pdf>

Harvard draws the same line and adds the emphasis dimension: a résumé is the shorter document, one
to two pages at the outside, and it spends its space on experience while giving less room to
academic material.

That second half matters as much as the length limit. The constraint is not only *how many pages*
but *what the pages are spent on*. A one-page document that spends a third of itself on conference
presentations has obeyed the length rule and broken the actual one.

---

## 2. The mechanism that resolves the hard cases: deferral, not deletion

The most common mistake in reading the above is to treat it as a ban. It is not. The UNC handout's
own framing is that CV-material is **relocated**, not forbidden — it lives on LinkedIn, Google
Scholar, ResearchGate, or a personal site, and the résumé carries a link.

This is why the honest answer to "can I put my publications on my résumé?" is usually *"put the
link to them on your résumé"*. It also explains why a **Links / profiles** line is a genuine
industry-résumé element while a **Publications** block generally is not: the line is the mechanism
the mainstream guidance actually prescribes for that material.

Two consequences worth stating plainly:

1. **A section is not disqualified by being impressive.** It is disqualified by not paying for the
   space it takes, given a reader spending seconds on the page.
2. **Seniority moves the line, and so does the role.** A research-scientist or ML-research posting
   at an industrial lab sits closer to the academic end than a backend-engineering posting at the
   same company. This is the main reason the rule cannot simply be hardcoded per layout.

---

## 3. Section-by-section verdicts

Grounded in §1 where the sources speak directly; marked **inferred** where the verdict follows from
the résumé/CV distinction rather than from an explicit statement.

| Section | On a US SWE résumé? | Why |
|---|---|---|
| **Links / profiles** (GitHub, LinkedIn, portfolio) | **Yes** | This is the mechanism UNC names for deferred material. It is how a résumé carries publications, side work and code without spending page space on them. |
| **Certifications** | **Yes, when relevant** | UNC lists *professional credentials* under the CV, but industry cloud and security certifications (AWS, GCP, CKA, security clearances) are read as job qualifications rather than scholarship. Treat as a qualification, not an honour. *Partly inferred.* |
| **Awards / honours** | **Sparingly** | Named in the CV list, not the résumé paragraph. Defensible when the award is a recognisable competitive signal for the role — ICPC, a major hackathon, a named company award — and weak when it is coursework-adjacent. *Inferred from the CV listing.* |
| **Open-source contributions** | **Yes** | Not academic material at all; for a software engineer it is directly evidence of the work. Often better expressed as project entries or a link than as a separate block. *Inferred.* |
| **Leadership / community service** | **Yes** | The one non-core category UNC explicitly names as belonging on the résumé: *community service and leadership activities.* This is the clearest positive in the source. |
| **Relevant coursework** | **Only as a student or new grad** | Fills space that experience should occupy once experience exists. Harvard's emphasis point is the operative one. *Inferred.* |
| **Publications** | **No — link instead** | Explicitly named in UNC's defer-to-a-profile list and in the CV list. The exception is a research-facing industry role. |
| **Presentations / talks** | **No — link instead** | Same list, same treatment. |
| **Patents** | **Rarely** | In the CV list. Occasionally justified for hardware, ML or infrastructure roles where a patent *is* the artefact. *Inferred.* |
| **Professional affiliations** | **No** | In the CV list; carries almost no signal for an industry SWE reader. |
| **Grants / teaching** | **No** | In the CV list. Reads as an academic document to an industry reader. |
| **References** | **No** | Not in either list here; the mainstream position is that they are supplied on request. *Not sourced in this document — do not cite it for this row.* |

---

## 4. What this means for the Component catalogue

Twelve Component types are registered today: `header`, `summary`, `professional_summary`,
`section`, `work_entry`, `education_entry`, `degree_entry`, `project_entry`, `tech_project`,
`bullet`, `skills_line`, `language_line`.

Reading §3 against that list:

**Genuine gaps, worth adding.** Links/profiles as a first-class line rather than something crammed
into the contact header; certifications as their own entry rather than borrowed from
`education_entry` (whose label already strains at "Education or certificate"); awards/honours; and
leadership or community involvement, which is the one non-core section the source explicitly places
on a résumé.

**Add with a stated caveat, not enthusiasm.** Publications, presentations and patents. If they are
added at all, it should be because the product serves research-facing industry roles too — and the
UI should say when they are a poor idea, exactly as the layout gallery already tells users that
Jake's template is a poor choice for a senior candidate. A Component that exists is a Component
people will use.

**Do not add.** Professional affiliations, grants, teaching, references. Nothing in the mainstream
guidance puts them on an industry résumé, and every one of them costs page space a recruiter is
spending in seconds.

### The design point that keeps this honest

ADR-0123 makes a Component **structure only** — it says nothing about appearance, and whether it
can be dragged or resized is a permission a **Layout** grants. That separation is what lets this
list be settled once rather than per template: adding `certification_entry` does not commit every
layout to showing it, and a layout aimed at new graduates can offer coursework while one aimed at
senior engineers does not.

It also means the *right* place for the seniority caveat in §2 is the Layout and its Rules, not the
Component catalogue. ADR-0127 already established the precedent: a Rule is baseline only where the
mainstream sources agree, and anything that is one school's house style stays on the layout that
believes it.

---

## 5. Limits of this evidence

Stated so nobody over-reads the document:

- **Two sources.** UNC and Harvard, both career-services handouts, both aimed partly at PhD holders
  moving into industry. They are unusually explicit about the résumé/CV split, which is why they
  carry this argument — but they are not software-engineering-specific.
- **The Harvard quotation available here was truncated mid-sentence** at "focusing less on ac…"
  (presumably "academic"). The paraphrase in §1 reflects the sentence's evident sense; anyone
  relying on the exact wording should open the PDF.
- **Rows marked *inferred*** follow from the résumé/CV distinction rather than from a sentence in
  either source. They are reasoning, not citation, and are labelled so they can be challenged.
- **Not consulted here:** the r/EngineeringResumes wiki, Google/Meta recruiting guidance, and ATS
  parsing behaviour for non-standard section headings. The last one is a genuinely separate
  question — a section can be correct to include and still parse badly — and it is not answered by
  this document.

## Sources

- UNC Office of Postdoctoral Affairs, *The Difference Between a Resume and an Academic CV*, February 2018.
  <https://research.unc.edu/wp-content/uploads/2018/02/Handout-Difference-Between-a-Resume-and-an-Academic-CV-Feb-2018.pdf>
- Harvard Office of Career Services, *Resumes and Cover Letters for PhD Students*.
  <https://apsanet.org/Portals/54/web/Handout%204%20-%20Harvard%20Resume%20Examples.pdf>

## Related

- `docs/resume/2026-09-10_resume-builder-editor-patterns.md` — how competing builders arrange the editor.
- ADR-0123 — the three layers, and why a Component is structure only.
- ADR-0126 — which layouts ship, and the evidence standard used to choose them.
- ADR-0127 — the baseline Rule set, and the rule that a Rule is universal only where the mainstream sources agree.
