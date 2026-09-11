# ADR-0130: Two more arrangements, and the blocks a résumé carries that a CV does not

**Status:** accepted · **Date:** 2026-09-11 · **Extends ADR-0126 (the picker carries the layouts people actually use) and corrects one claim in its Consequences. Uses ADR-0123's extension point for the second time, and reports what it cost this time.**

## Context

ADR-0126 took the picker from three Layouts to seven and set the bar for the next one: a Layout
earns its place by being a different **arrangement**, not a different font. The brief here was to
add more of the layouts tech job-seekers actually use — and, separately and more interestingly,
the **Components** those layouts carry that this product has no type for.

Twenty templates were surveyed on 2026-09-10, every star, fork, licence and last-commit figure
fetched that day from `api.github.com` and every structural claim read out of the template's own
`.tex` / `.cls` / `.yaml` rather than inferred. The survey's own headline is the reason this ADR
adds two Layouts and not six: **there are only six genuinely distinct arrangements in the whole
set**, and the picker already held four of them.

## Decision

**1. Two Layouts, each a shape the picker did not have.**

| Layout | Evidence, counted 2026-09-10 | The arrangement, and why nothing here had it |
|---|---|---|
| `deedy-resume` | `deedy/Deedy-Resume`, **5,055 stars / 1,288 forks**, Apache-2.0, last commit 2015-12-02; carried in the Overleaf gallery, whose own source header reads `% Deedy - One Page Two Column Resume` | Asymmetric two-column, **narrow column first** at 0.33 against 0.66, sections column-scoped — and an entry with **no right-aligned cell at all**: organisation, then title, then `date | place` on a third line. Both existing two-column Layouts put the wide column first and align something to the right margin |
| `mcdowell-cv` | `dnl-blkv/mcdowell-cv`, **2,707 stars / 848 forks**, MIT, last commit 2023-08-30. The repository is one author's LaTeX implementation; the FORMAT is Gayle Laakmann McDowell's, from *Cracking the Coding Interview* | **One bold line an entry**, with three things on it — title left, **organisation centred**, dates right, from a three-cell `tabu` row. Of the twenty templates surveyed it is the only one that centres the organisation, and the saving is structural: an entry costs one row instead of two, which is what gets a sixth job onto one page |

**2. What was rejected, and why — the restyles.** The bar is arrangement, so star count alone
does not carry a template in.

- **Awesome-CV** (`posquit0/Awesome-CV`, **28,470 stars / 5,292 forks** — by far the most-starred
  thing in the survey, and 9,680 `.tex` files use its `\cventry` by code-search proxy). Refused as
  a Layout anyway: `\cventry` is the **four-corner two-row table**, which `jakes-resume` and
  `harvard-classic` are already two spellings of. Its contribution here is to the *catalogue* —
  it is the richest source in the survey for the blocks below, shipping `Honors & Awards`
  (`\cvhonor`, 1,468 files) and `Certificates` as first-class sections.
- **AltaCV** (1,575 / 389). Its `paracol` sidebar is `modern-sidebar`'s shape, and what is left
  over — `\cvskill{English}{5}` rating dots and `\cvtag` pills — is the one thing this repo's own
  `resume-builder` skill forbids outright: *"Rating skills with stars/bars/percentages — Never —
  subjective and meaningless."* A layout whose distinguishing feature we would refuse to render is
  not a layout to add.
- **sb2nov** (6,974 / 1,881) and **billryan** (11,414 / 2,870). `jakegut/resume` says in its own
  README that it is based on sb2nov, and the two share the `\resumeSubheading` macro outright, so
  `jakes-resume` already is this. billryan glues the location to the organisation on the left and
  keeps only the date on the right, which is a variant of four-corner rather than a new shape, and
  its section headings carry FontAwesome icons.
- **RenderCV** (17,548 / 1,350, MIT, and the best-maintained thing in the survey). It ships
  **nine** themes and its own source says they are *"defined as YAML files with field overrides"*
  over one Typst `regular-entry` primitive — nine restyles of one engine, not nine templates. Its
  `engineeringresumes` theme, which encodes the r/EngineeringResumes wiki and collapses an entry
  to a single line, is the one genuinely distinct shape in the set and is **considered and not
  chosen**: it would be a fourth single column, and ADR-0126's own Options table already records
  that criticism against the third. Cheap to add later; the evidence is here.
- **FAANGPath** has no canonical repository — its home is Overleaf and its popularity is not
  star-measurable — and it is built on `res.cls` with an `OBJECTIVE` section, which every standard
  this builder is calibrated against tells people to drop.
- Three of the highest-starred search results — `geekcompany/ResumeSample` (28,290),
  `JordanSchuetz/LearnCS8-Resume` (11,203), `resumejob/awesome-resume` (7,418) — have **no LICENSE
  file at all**, and the first is Chinese-only Markdown with no renderer while the second is
  abandoned course material. Recorded because star count reads like a quality signal here and is
  not one.

**3. Three Component Types, and the line was drawn by the résumé/CV split rather than by taste.**

The adjudicator is this machine's own `resume-builder` skill (Harvard Career Services, the
r/EngineeringResumes wiki) together with what university career services publish about the
difference between a résumé and a CV. That distinction settles nearly every hard case: UNC's
handout names what a **résumé** carries — education, work and research experience, relevant
skills, community service and leadership — and separately lists what belongs on a **CV**:
publications, presentations, professional development, credentials, grants, patents, awards,
honours, affiliations.

That split, and a verdict for each candidate block, is written up separately in
`docs/resume/2026-09-10_non-core-sections-us-swe-resume.md` — **which is not on this branch**: it
is PR #414, docs-only, and this is a forward reference that dangles until that lands. The catalogue
below was decided independently and agrees with it row for row, which is worth saying because the
two were not copied from each other: certifications *yes, when relevant*; awards *sparingly*;
open-source *yes, but better as a project entry or a link*; leadership *yes — the one non-core
category the source explicitly places on the résumé*; publications and talks *no, link instead*;
patents *rarely*; references *no*. Where this ADR differs from that document it is on
**mechanism, not verdict** — "yes" for leadership and open-source is satisfied by `work_entry`
and `tech_project`, which already exist, and "link instead" is satisfied by `profile_line`, which
did not.

| Added | Why it is a résumé block |
|---|---|
| `certification` | Awesome-CV ships `\cvsection{Certificates}`; the skill's India reference lists certifications; `common-mistakes` cuts only the *expired or irrelevant* ones. Narrow but real, and role-dependent — a hiring manager on record calls it *"a tie breaker"* for infrastructure roles while a CTO screens out cert lists. The blurb says so |
| `award_entry` | Deedy ships `\section{Awards}` as a year/rank/award table, Awesome-CV as `\cvhonor{award}{event}{location}{date}`, billryan and mcdowell as their own sections. Harvard folds honours into Education; the wiki demotes them (*"unless they're extremely impressive"*). Real, and the blurb says to name the field you beat |
| `profile_line` | **The deferral mechanism, and the reason it is a Component and not a header field.** The guidance for someone who has CV material is not "delete it" but "keep it where it lives and link to it". A header with one `link` field cannot do that, and a second field on `header` would have been printed by exactly one of the nine Layouts — see §5 |

| Refused | Why |
|---|---|
| **Publications** | The clearest case of academia leaking in. It is on every career service's CV side of the line; the r/EngineeringResumes wiki never mentions it; **zero of five** top-school CS/engineering exemplars carry one. The three templates that ship it are a LaTeX-academic lineage — AltaCV's is literally `biblatex`, and Deedy wrote his as a Cornell researcher. Someone who genuinely has publications is served by `profile_line` pointing at Google Scholar, which is what the guidance actually prescribes |
| **Patents, talks and presentations** | Same side of the same line. Of twenty templates, patents appear in exactly one (RenderCV) and a presentations *list* is a CV block by Harvard's own contrast. A one-off talk is a `work_entry` or a bullet |
| **Relevant coursework** | Real, and well supported for new grads specifically — but it is a labelled list, which `skills_line` already is. `mcdowell-cv` writes it as `Undergraduate Coursework: …` inside Education and `deedy-resume` gives it a section; both use `skills_line`. A second type here would be a near-synonym, which CLAUDE.md Rule 3 names as a failure mode |
| **Leadership, volunteering, extracurricular** | Explicitly *on* the résumé side, and already buildable: a leadership entry is a role, an organisation, a place, dates and bullets, which is `work_entry` exactly. `harvard-classic` has shipped a "Leadership & Activities" section built out of it since ADR-0126. The counter-argument is real and recorded — the wiki says *"Only include PAID work experience"* in Work Experience, so unpaid work is arguably a different kind of thing — but a second entry type with identical fields buys a heading, not a structure |
| **Open-source contributions** | Endorsed as evidence, never as a heading: it lives in Projects, or as the GitHub URL in the contact line. `tech_project` carries name, stack, dates and bullets already, and `profile_line` carries the profile |
| **References, affiliations, grants, teaching** | CV-side, and references specifically are advised against near-unanimously — *"Do not include a references section"* |
| **Skill rating bars** | Refused outright, and it is why AltaCV is not here |

**4. The `three-lines` Rule measures the column, not the page.** ADR-0127 promoted `three-lines`
into the shared baseline, so it now runs on every Layout — and it was handed `api.page` where the
text has a column. Measured in Chromium on 2026-09-10 against one realistic 281-character bullet:
`two-column` really prints **five** lines and `modern-sidebar` and `europass` **four**, and the
rule said **nothing** about any of them, because it believed a line held 24–39% more characters
than it does. A Layout now answers `api.measure(node)` — the slot's share of the measure by
`grow`, the block's own width where the Layout positions freely, or the Layout's own
`measure(slotId, page, theme)` where its geometry is neither. Europass declares one, because it
has a single slot and still gives a quarter of every row to a label gutter, so no share of `grow`
could have described it.

The `0.5` average-advance constant was **not** touched, and that is the interesting half. An
earlier diagnosis called this a uniform ~7.5% bias in the constant; it is not, and tuning it would
have been the wrong fix arrived at confidently — it lands within 1% on `headless-headhunter` (86
true characters against 85 assumed) and would have had to be wrong by 39% somewhere else to fix
`modern-sidebar` by that route. The input width was the fault. Rendered widths after the change,
measured against Chromium's own boxes: within about 5% on every flow Layout.

**5. The answer to "is a new Component really free?" — measured, and it is half free.**

ADR-0123 promises that `shape` dispatch makes a Component added after a Layout shipped render in
it anyway. Measured across all nine registered Layouts on 2026-09-10, by adding a field and by
adding a type:

- A **new Component Type** with field names no renderer has ever seen renders in **9 of 9**
  Layouts and in the plain-text export. The promise holds, and it is what made the three types
  above cost no change to any existing Layout's rendering.
- A **new field on an existing Type** renders in **0 to 9 of 9, depending on the type** — it is
  not one number, and the "0 of 9" this bullet originally stated is right only for the four types
  it goes on to name. What decides it is how many Layouts claim that type `byType`: a `byType`
  strategy names the fields of a type it knows, so a field added to one is printed by nobody,
  while a `byShape` fallback prints every string field a node owns. Re-measured 2026-09-11 across
  the nine registered Layouts:

  | renders a new field in | Component Types |
  |---|---|
  | **0 of 9** | `work_entry`, `education_entry`, `project_entry` |
  | **1 of 9** | `header` |
  | **3 of 9** | `degree_entry`, `tech_project` |
  | **7 of 9** | `certification`, `award_entry`, `language_line`, `professional_summary` |
  | **9 of 9** | `section`, `bullet`, `skills_line`, `profile_line`, `summary` |

  Worth saying which end this ADR's own three additions landed on: `certification`, `award_entry`
  and `profile_line` are at 7, 7 and 9 of 9, so widening one of them later is cheap — the opposite
  of the case this bullet was written to warn about. It is still worse than the source comment in
  `resume_components.js` claims for the types at the top of the table: for `work_entry` the
  **plain-text export drops a new field too**, because that visitor is hardcoded to `roleLine` and
  `dateRange`. For `project_entry` the export does print it, which makes the failure inconsistent
  as well as silent.

So ADR-0123's extensibility guarantee is about **types, not fields**, and the honest statement of
it is: *a `byShape` renderer may never name a field, so a new type is cheap; a `byType` renderer
must name fields, so a new field on a claimed type is a pass over every Layout that claims it.*
That is why `profile_line` is a Component rather than a second link on `header`.

## Options considered

| | | for | against |
|---|---|---|---|
| **A (taken)** | Two arrangements the picker lacked, three Components, `byShape` only | every addition is a shape or a block that was genuinely missing; no existing Layout needed a rendering change, and this time that is measured rather than asserted | two is a modest answer to "add more layouts", and the most-starred template in the survey is refused |

**The weaker of the two picks, named rather than left to be found.** `mcdowell-cv`'s *page*
geometry is a third plain single column, and its whole distinction lives one level down, at the
entry. That is the seam a sceptic should press, and it presses on `engineeringresumes` being
refused partly for being "a fourth single column". The difference claimed is that McDowell's entry
is a *different row* — three cells, one line, the organisation centred, found in one template of
twenty — where `engineeringresumes` is a denser spelling of the same two-corner row `jakes-resume`
already has. That is a real distinction and it is also a fine one; a reviewer who weighs page
geometry above entry geometry would reasonably have taken neither.
| B | Add Awesome-CV too, on its 28,470 stars | it is the most-used template in the world by that measure | its arrangement is four-corner, which the picker holds twice already. Adding it would be the restyle ADR-0126's own bar exists to refuse, and the star count is a popularity signal about a *class*, not about a shape |
| C | Give the three new Components a `byType` renderer in all nine Layouts | each would look native everywhere | 27 renderers for a benefit measured as unnecessary: the field-agnostic fallback renders every one legibly in all nine, and the two new Layouts give theirs a real row because they are new files, not because the others needed one |
| D | Add a `leadership_entry` type | says what it is; the wiki does draw a paid/unpaid line | identical fields to `work_entry`, and `harvard-classic` already ships the section built from it. A near-synonym for the same structure is the Rule 3 failure |

## Consequences

**The correction ADR-0126 owes.** Its Consequences say: *"Four layouts were added as four files
plus four `<script>` tags, and no existing layout needed a rendering change to accommodate any of
them."* That is false as written, and the falseness had a cost. The merge that landed it
(`ec8950d3`) changed the render strategies of **all three** pre-existing Layouts —
`resume_layout_headhunter.js`, `resume_layout_twocolumn.js` and `resume_layout_canvas.js` — plus
`resume_export.js` and 115 lines of `resume_layouts.js`. The ADR does describe those changes; what
it gets wrong is attributing them to a prior commit and then reporting the layout addition as
costing zero.

Believing the zero is what left `resume_editor.js`'s page-break simulation behind. `pageBreaks()`
selects `.hh-entry, .tc-entry, .cv-entry, .rb-entry` — the three pre-existing Layouts' entry
classes and the generic fallback — and the four Layouts ADR-0126 added introduced `.jr-entry`,
`.hv-entry`, `.sb-entry` and `.ep-entry`, none of them in that list. Measured on each Layout's own
example, 2026-09-10: the selector finds **0 of 5** entries on `jakes-resume`, **0 of 4** on
`harvard-classic`, **0 of 3** on `modern-sidebar` and **0 of 3** on `europass`. On those four the
preview draws its page cuts at uniform intervals as though no block were unbreakable, while the
stylesheet those same four files emit says `break-inside: avoid` on every entry — so the cut drawn
on screen sits above where the printer will really make it.

The two Layouts added here carry `rb-entry` alongside their own entry class, so their page breaks
are right from birth. **The durable fix is one line in `resume_editor.js`** — select
`[data-shape="entry"]`, which `ctx.el` already stamps on every entry in every Layout — and it is
**not made here**, because that file belongs to another change in flight. The four existing
Layouts are left alone deliberately rather than patched the same way: adding `rb-entry` to
`.hv-entry` would let harvard's generic `.rb-entry` margin rule win over `.hv-thin`, which is a
visual change to a shipped Layout in exchange for a workaround. This is recorded as owed work, not
as done.

**Two tests stopped being lists.** `"every added layout ships a worked example that passes its own
rules"` iterated a hardcoded four, so the two added here would have shipped without their examples
ever being read; it now iterates every Layout that ships an example, which is the same argument
ADR-0127 made for the baseline gate. The neighbouring `standard-headings` check deliberately
**stays** a list, because it borrows one Layout's rule and applies it to others — and three of the
nine decline it for the same reason: they implement somebody else's template and keep that
template's own section names (`headless-headhunter`'s "Work History", `mcdowell-cv`'s "Additional
Experience and Awards", `deedy-resume`'s "Links" and "Coursework"). Iterating there would assert
one Layout's opinion against Layouts entitled to disagree.

**A worked example was caught teaching the wrong thing.** `mcdowell-cv`'s own `one-line-row` Rule
checked jobs only, and the example's education row and one award row both wrapped on a real page —
the Layout's headline claim, demonstrated failing, by the document that teaches users what the
format is for. Per ADR-0127's own warning about which half to fix: **both** were fixed. The Rule
now covers every type the Layout renders as a three-cell row, and the example lost a city and four
words. Screenshots are the check that found it; no test would have.

**What this costs the picker.** Nine live miniatures now render from the user's own document on
every gallery open, up from seven. That is the recurring price of an addition here and it is why
two was the number rather than five.

**A limit that is now visible.** `free-canvas` answers `measure` with the block's own width, which
is right — but `adopt` hands every arriving block the *full* measure, so a freshly migrated canvas
document measures exactly as it did before. That is honest (the block really is that wide) and it
means the fix does nothing there until someone drags something narrower.
