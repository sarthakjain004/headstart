# ADR-0126: The picker carries the layouts people actually use, and says where each one is a bad idea

**Status:** accepted · **Date:** 2026-09-10 · **Extends ADR-0123 (a résumé is three layers). Does not supersede it: this is the extension point being used for the first time, and the report of what that cost.**

## Context

ADR-0123 shipped three Layouts and was explicit about why: "a build with only flow layouts could
assert the capability contract and never be tested on it". Two of the three exist to falsify a
claim rather than to be sent to an employer — the two-column CV says so in its own blurb, and the
free canvas says the guide would tell you not to use it. That leaves exactly **one** Layout a user
should actually apply with, and it is one guru's template.

The brief was to add the four layouts people actually use. "Actually" is doing work there: this is
a question with evidence, not a matter of taste, and the product it sits in is global and indexes
software-engineering roles — which changes the answer.

## Decision

**1. Four layouts, each picked on evidence of use, each in its own file.**

| Layout | Evidence | What it adds structurally |
|---|---|---|
| `jakes-resume` | `github.com/jakegut/resume`, 2.8k stars / 697 forks, MIT, mirrored in the Overleaf gallery; the default answer on r/EngineeringResumes | Four-corner entries, a categorised skills block, Education → Experience → Projects → Skills |
| `harvard-classic` | Reverse-chronological is what recruiters say they prefer by a wide margin, and the Harvard College guide is the template careers offices hand out; it is also the standard this repo's own `resume-builder` skill packages | The same standard the Headless Headhunter layout deliberately departs from — and the rules to match |
| `modern-sidebar` | The shape every consumer builder (Canva, Zety, Novoresume) opens with | A coloured band, and the only layout here whose own risk is a reason to warn the user |
| `europass` | 10.2M accounts, ~1.5M new a year, 8.3M CVs downloaded in 2024, 31 languages; asked for by name across EU public-sector hiring | A label gutter — a form, not a template — and A4 |

Rejected: an **executive** format (summary plus core competencies) — the popularity evidence is
vendor marketing, and structurally it is a third single column; and an **academic CV** with
publications — real, but for a corpus of software-engineering roles it serves almost nobody here.
Both remain cheap to add later, which is the whole point of the extension point.

**2. A layout may declare its own sheet; the document still overrides it.** `resume_layouts.js`
argued that the sheet is the document's choice alone and "a Layout that declared A4 would be a
second copy of itself". That is true of a *template* and false of a *form*: Europass is an A4
document the way a passport is a passport-sized one. So `europass` declares A4, everything else
declares US Letter, and `pageFor` still lets a document name either. Verified by printing: the
Europass example prints 8.26 × 11.69in, and prints 8.5 × 11in when the document asks for Letter.

**3. ATS risk is stated, not designed around.** The coloured sidebar is the most-used consumer
shape and parses worst; several published studies of column parsing are vendor marketing, but the
mechanism — a parser reading straight across two columns and interleaving them — is real and its
failure is silent. The layout ships, its blurb names the risk and names the two safe layouts
instead, its wide column is declared first so the dated history is what a parser reaches, and a
rule fires if a job history is dragged into the band. No photo component exists and none was added
for it.

**4. Four new Component Types, and a rule about naming their fields.** `professional_summary`,
`degree_entry`, `tech_project`, `language_line` — each required by a layout that ships, none
speculative. Certifications were *not* added: `education_entry` is already "Education or
certificate". The rule that came out of this: **name a Component Type's fields for what they are**,
never for what a generic renderer happens to read. Getting that wrong is invisible — see below.

## Options considered

| | | for | against |
|---|---|---|---|
| **A (taken)** | Four layouts on evidence of use, one file each | the picker finally holds formats a user recognises; the extension point is exercised by four independent authors' shapes rather than by two straw men | two of the seven are now single-column and differ mostly in convention — the difference is real (see `harvard-classic`'s header) but has to be explained |
| B | The four the brief listed, including an executive format | matches the brief exactly | the popularity evidence for it is vendor copy, and it would be a third single column with no new structure |
| C | One configurable "modern" layout with a colour and a column toggle | one file instead of four | every template's identity is in its details — the transposed education block, the CEFR gutter, the pipe on a project line — and a toggle carries none of them |
| D | Refuse the coloured sidebar on ATS grounds | no pretty trap | it is the shape most job seekers reach for; refusing it sends them to Canva, where nothing warns them at all |

## Consequences

The extensibility claim in ADR-0123 held **for the layouts and not for the components**. Four
layouts were added as four files plus four `<script>` tags, and no existing layout needed a
rendering change to accommodate any of them. But every new Component Type of a shape whose fallback
read fields *by name* rendered blank in the layouts that predated it, and the test that was meant to
catch that had itself picked the two field names the fallbacks hardcoded — so it passed while the
guarantee was false. That was found while designing these components and fixed **separately, in the
commit this branch is built on** (`ctx.fields()`, `ctx.textOf()`, `L.headAndRest` are that commit's,
not this one's), because it touched all three predating layouts and is not a layout change. The rule
it leaves behind is what matters here: **a `byShape` renderer may never name a field; a `byType`
renderer may**, and every renderer in these four files obeys it.

Two further losses were measured while adding these components, and are fixed in this change: the
plain-text export truncated any entry type that owned a `name` *plus* other fields to just the name
— so a `tech_project`'s stack never reached the copy an ATS reads — and the two-column layout's
header renderer never rendered the header's `languages` field at all. Both are outside the four new
files, and both are here rather than deferred because each is a word of a user's résumé going
missing with nothing on screen to say so.

One thing a layout still cannot express: `css(theme, scope)` is not given the page. Rules get
`api.page`; a stylesheet does not, so the sidebar's coloured band stops at the content rather than
running to the foot of an under-filled sheet. Passing the page as a third argument is the obvious
fix and is not made here.

And one deliberate deviation from a source, because it is the kind that gets "corrected" back:
Jake's Resume sets its name and headings in small caps, and `font-variant: small-caps` in a browser
with no small-caps font **synthesises** them — the printed PDF then extracts as `A NANYA R AO` and
`E DUCATION`. Real capitals are within a hair of the same page and extract as themselves, so that is
what the layout emits.
