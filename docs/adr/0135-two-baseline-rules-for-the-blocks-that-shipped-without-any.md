# ADR-0135: Two baseline Rules for the blocks that shipped without any

**Status:** accepted · **Date:** 2026-09-11 · **Amends ADR-0127 (hygiene is a shared baseline), whose count of seven Rules and four refusals this change makes stale. ADR-0127's decision itself stands; only its arithmetic moves.**

## Context

ADR-0130 added three Components — `certification`, `award_entry` and `profile_line`. None of them
got a Rule, from the baseline or from any Layout. Measured through all nine registered Layouts, a
deliberately bad résumé produced **zero findings** for a `certification` with an empty issuer, no
month and no credential id; an `award_entry` whose title is literally `"Winner"`; a `profile_line`
whose url is `https://github.com/ravi`; and a header `link` that is a full `https://www.…/` URL.

The advice for two of those exists — in the Add-menu blurbs (`resume_components.js`: *"'1st of 340
teams' is the award, 'Winner' is a word"*; *"Plain text, no https:// and no www"*). That is where
the user reads it once, when adding the block, and never again. The Checks panel is the surface
that repeats it.

Three candidates were considered against the standards packaged in this machine's `resume-builder`
skill (Harvard Career Services, r/EngineeringResumes), at ADR-0127's own bar: **baseline only where
the mainstream sources agree.**

## Decision

**Two Rules added, one candidate refused, and one half of a third refused.**

**1. `plain-links` (warn) — a field that holds an address and carries a scheme.**
The wiki's line is *"do NOT include `https://www.`"*, and the delivery checklist repeats it. The
`https://` half is the half nobody disagrees with: no template on the picker writes a scheme, and
measured across all eighteen shipped starters and worked examples, **not one** contains `://`.

*Which fields those are is declared by the Component Type*, as a new `holdsUrl` flag on the field
spec — `header.link`, `profile_line.url` and `certification.credential`. The first draft kept a set
of field NAMES in `resume_layouts.js` instead, and the names do not carry the fact: a
certification's address field is called `credential`, and so are `degree_entry`'s and
`education_entry`'s, which hold the name of a qualification. A list keyed on `credential` flags a
degree; one that leaves it out misses a certification's link, which is what the first draft did.
It is deliberately **not** a `KINDS` entry — a kind is a promise the editor must keep, it costs a
control, and an address is typed into the same plain text box as everything else.

**2. `award-scale` (note) — a title that is a ranking word and nothing else.**
Quantification is the most universally agreed thing in these sources, and this is its narrowest
form. It fires only when the whole `title` is a placing word, and then only when neither `awarder`
nor `place` carries a number — "Winner" beside "Smart India Hackathon, 340 teams" has already said
what was beaten. `when` is deliberately not read: a date is digits, and reading it would silence
the rule on every award that states one.

**3. REFUSED — the `www.` half of rule 1.** `headless-headhunter`'s own worked example writes
`www.linkedin.com/in/leekorelitz`, and it is the only one of the eighteen documents containing
`www.` at all. A baseline including it would fire on the document that Layout is calibrated
against — the same test that kept `pronouns` out of ADR-0127's seven.

Refusing a Rule only works if the advice lands somewhere, and for one of the three fields it did
not: `header.link` — the one address every résumé carries — had **no hint at all**, while the same
advice sat on `profile_line.url` below it, and its own placeholder wrote `www.linkedin.com`. So
every `holdsUrl` field now carries the hint, and that placeholder drops the `www.`. The
headhunter *example* is untouched: it is a transcription of that template's own document, and
changing it to suit a rule this ADR refused would be the calibration failure ADR-0127 warns about,
backwards.

**4. REFUSED — `certification-provenance`**, a certification with no issuer or no date. Defensible
advice; nobody's *stated* standard. Certifications appear **twice** in the packaged sources and
neither is a requirement: "certifications that are expired or irrelevant" (advice to drop one, not
to date it) and a bare section-order listing in the India notes. An AWS certification also names
its issuer inside its own name, so the issuer half would fire on correct entries. Absence of a
source is not agreement. `deedy-resume` and `mcdowell-cv` render `certification` by name and may
still state it as their own opinion.

The block is not left with nothing, though: its `credential` field is one of the three `holdsUrl`
fields, so a certification linking `https://credly.com/...` is flagged by rule 1.

Both refusals are written into the `COMMON_RULES` comment beside ADR-0127's four, so the next
reader does not re-litigate them from the sources.

## Which of the two, and why — ADR-0127's named gate

ADR-0127 requires every future baseline Rule to pass all the shipped worked examples, warns that
the gate *"can be satisfied the wrong way — by softening a correct rule instead of fixing a weak
example"*, and asks the adding ADR to say which was done.

**Both Rules here were narrowed to fit an example, and neither example was changed.** Named
plainly, because that is the path ADR-0127 says to distrust:

- `award-scale` was narrowed from "an award with no number" to "a title that is a ranking word and
  nothing else" because `mcdowell-cv`'s example writes *"Engineering Excellence Award"*, which has
  no number in it at all. **This narrowing is right**: that title is a real award with a real name,
  and a rule that flagged it would be wrong, not merely inconvenient. The example is exemplary and
  independently authored — it is the McDowell method's own — and it is the rule that was too broad.
- `plain-links` was narrowed from "`http`, `://` or `www.`" to "`://`" because
  `headless-headhunter`'s example writes `www.linkedin.com`. **This narrowing is arguable and is
  the weaker of the two.** `www.` genuinely is against the source; the example is a transcription
  of the Headless Headhunter's own template rather than something this repo chose, so "fix the
  example" would mean disagreeing with the template the Layout implements. That is a Layout's call
  to make, not a baseline's — which is the ADR-0123 line the whole baseline is drawn along — so
  the narrowing stands and the disagreement is left available to `headless-headhunter` to state.

## Consequences

- The baseline is now **nine** Rules. ADR-0127's Decision §1 ("seven Rules every Layout gets"), its
  "four candidates were considered and refused" (now six) and its "all seven baseline ids were
  already declared by `headless-headhunter`" (now seven of nine; the two added here are new to
  every Layout) are superseded by the arithmetic above. Those are counts, not decisions —
  ADR-0127's mechanism, its bar and its opt-out are untouched.
- `badResume` in `tests/js/resume_layouts.test.js` gains a schemed header link and a one-word
  award, so ADR-0127's registry-iterating gate — *every* registered Layout must flag *every*
  baseline Rule on it — still holds for the two new ids, automatically and for Layout number ten.
- All nine worked examples still trip none of their own rules.
- `holdsUrl` is the first fact a Component Type declares that only a Rule reads. It defaults false,
  so every existing type and every future one is unaffected until it says otherwise.
- **ADR-0127's `resume_rules.js` split trigger is now met, and is not discharged here.** Its
  trigger was *"when either grows a consumer that is not a Rule, or when the baseline outgrows the
  seven Rules here — whichever comes first"*; the second arm fires at nine. It is left as a named
  debt rather than taken, for two reasons: the split needs a `<script>` tag in `base.html`, which
  is outside the file lane this change was scoped to; and ADR-0127 itself argued that moving the
  file *"in the change that introduced the baseline"* would mix a file move with the argument for
  it — which is equally true of mixing it with four behaviour fixes. The first arm still has not
  fired: every consumer of `COMMON_RULES` and of the prose vocabulary is still a Rule.
