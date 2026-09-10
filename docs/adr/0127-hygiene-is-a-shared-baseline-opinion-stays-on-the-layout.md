# ADR-0127: Hygiene is a shared baseline; opinion stays on the Layout

**Status:** accepted · **Date:** 2026-09-10 · **Amends ADR-0123 (a résumé is three layers), whose "a Rule belongs to a Layout" is narrowed here rather than reversed. Extends ADR-0126 (the picker carries the layouts people actually use), which is where the silence became visible.**

## Context

ADR-0123 put every Rule on the Layout, and gave a reason: "the same bullet is fine under one
Layout and short under another". Rules are its method's opinions, so they belong to the method.

That is true of some rules. It is plainly false of others, and ADR-0126 is what made the
difference matter — it took the builder from three Layouts to seven, and the Rules did not come
with them. Counted: `headless-headhunter` states **14**, `harvard-classic` 4, `jakes-resume` 3,
`europass` 3, `modern-sidebar` 2, `two-column` 1, `free-canvas` 1.

One deliberately terrible document — no name and no way to answer, a job with no dates, two more
in the wrong order, twelve bullets on one of them, every one opening "Responsible for" and none
carrying a number, a result or a reason — measured through all seven:

| Layout | Findings before | Findings after |
|---|---|---|
| `headless-headhunter` | 37 | 37 |
| `harvard-classic` | 14 | 35 |
| `jakes-resume` | **0** | 21 |
| `two-column` | **0** | 21 |
| `modern-sidebar` | **0** | 21 |
| `europass` | **0** | 21 |
| `free-canvas` | **0** | 21 |

On Jake's Resume — which ADR-0126's own table calls "the default answer on r/EngineeringResumes",
and which is therefore the Layout a software candidate is most likely to pick — the Checks panel
read *"Nothing to flag. Every rule this layout states is met."* That sentence was true and the
advice was wrong, which is worse than shipping no checks at all: a job-seeker was told an
unsendable résumé was finished.

Each of those four layouts got there honestly. `jakes-resume` says in its own source that "this
template is a .tex file, not a method, so it states nothing about verbs, tense or metrics";
`europass` says "Europass has no opinion about how a bullet is written and inventing one would be
putting words in the Commission's mouth"; `harvard-classic` says "nothing about tense or strong
verbs is repeated here: **that layout already checks it**". The last of those is the bug stated
out loud — a Rule reaches only its own Layout's users, so "already checked" was checked for
somebody else.

## Decision

**1. `ResumeLayouts.COMMON_RULES` — seven Rules every Layout gets, merged in `define()`.**
`contact` (a name, and an email or a phone), `dates` (month and year on every job),
`bullet-count` (no more than eight), `three-lines`, `reverse-chronological`, `opening-verb`,
`result`. ADR-0123's sentence is narrowed to: **a Rule belongs to a Layout, and a Layout may
inherit one it did not write.** After `define`, `layout.rules` is the whole set, so the panel's
"checked against this layout's own rules" stays true — a Layout states these, it just does not
spell them out.

**2. The line is hygiene against opinion, and it was drawn against a source, not by taste.**
The adjudicator is this machine's own `resume-builder` skill, which packages Harvard Career
Services and the r/EngineeringResumes wiki. A Rule is baseline only where those agree with each
other *and* with the Headless Headhunter guide. Four candidates were considered and **refused**,
each because the sources disagree:

- **`twelve-years`** — every source says drop the ancient jobs; none states a number. Twelve is
  the guide's, and Europass is a full-history form by design. It stays with the guide.
- **`past-tense`** — the general standard permits the present tense for a job you still hold
  ("pick one, be consistent"); the guide's words are "even if you are still employed".
- **`one-sentence` / `no-terminal-period`** — a bullet takes exactly one period, or none. Flatly
  opposite, one per Layout, already written down that way.
- **`pronouns`** — the standard forbids I / my / we; the Headless Headhunter's own worked example
  contains "while I prepared their food", so that layout would fire on the document it is
  calibrated against.

**3. A Layout opts out by declaring a Rule with that id and marking it `overridesBaseline`.**
The declaration is a better lever than a flag on the Layout, because it makes a disagreeing
Layout say what it *does* believe rather than only what it refuses, and it puts the disagreement
on the rule, where its author is already looking. The **flag** is what makes it loud:
`define()` refuses a rule that collides with a baseline id without it, and refuses the flag on an
id no baseline rule has. Silence here would be the very failure this ADR exists to prevent — an
author naming a rule `dates` for their own reasons drops the baseline's without a word, and
rediscovers it months later as a Checks panel that says nothing.

This is what preserves the two conflicts the change could have flattened: `harvard-classic` keeps
`present-not-current` (the baseline's `dates` accepts any still-here word, and leaves which word
is right to the Layout), and `headless-headhunter` keeps its own `opening-verb`, written as the
shared list **minus `handle`** — the guide's example opens a bullet with "Handled a large lunch
rush line", so the subtraction *is* the disagreement, in code, and a verb added to the baseline
still reaches it.

All seven baseline ids were already declared by `headless-headhunter`, so no baseline Rule is
**added** to it. That is not the same as nothing changing for it, and the difference is worth
stating precisely because it is easy to overclaim: on the fixture above its findings are
unchanged id for id, 37 before and 37 after — but §4 widens the shared `SCALE`, and that layout's
own `result` reads `SCALE` from here, so a bullet reading "brought four engineers through
onboarding in six months" earned a note there before and does not now. The rule set genuinely
moved; the fixture simply does not contain that sentence.

**4. Two of the seven were calibrated down by the shipped examples, not up.** Every Layout's own
worked example is now run against the baseline, and three findings came back:

- `bullet-count` was going to state a **floor** of two as well. `harvard-classic`'s example gives
  a Leadership entry one bullet and `europass`'s gives a compressed first job one — both
  deliberate, both by authors following their own standard. The floor is house style wearing a
  baseline's clothes; only the ceiling shipped.
- `result` read `modern-sidebar`'s "brought **four** engineers through onboarding in **six**
  months" as a bullet with no number in it, because the test wanted digits. Preferring digits is
  advice; pretending a spelled number is not a number is a wrong finding, so `SCALE` counts both.
- The remaining two were `jakes-resume`'s, and there the example was the thing at fault: two
  bullets carried no number, no result and no reason among peers that all did. The example got
  the number, because the worked example is what teaches a user what a good bullet looks like.

## Options considered

| | | for | against |
|---|---|---|---|
| **A (taken)** | A shared baseline merged at `define`, opted out of by re-declaring the id | one place to state hygiene; a Layout that disagrees must say what it believes; layout #8 is covered before it is written | ADR-0123's sentence needs amending, and "which half is this?" is a judgement someone must make per Rule |
| B | Copy the missing Rules into each of the six thin Layouts | no new mechanism; each Layout stays literally self-describing | six copies of "a job needs a month and a year" that drift, and layout #8 still ships silent — the failure being fixed |
| C | Leave it; let each Layout's author state what they believe | purest reading of ADR-0123 | measured: four of seven authors stated nothing, and the panel called a bad résumé clean. An architecture whose correct use is optional is not being used |
| D | One universal rule set, Layouts state nothing | simplest of all | it flattens `present-not-current` against "to Current", and pronouns against the guide's own example — a Layout's considered opposite position silently overridden is a worse defect than the silence |

## Consequences

A Layout is now cheaper to add and harder to get wrong: an author writes only what is *theirs*,
and the résumé-hygiene floor arrives with `define`. `tests/js/resume_layouts.test.js` holds the
gate — the bad-résumé fixture must trip **every** baseline id under **every** Layout in
`Layouts.all()`, iterating the registry rather than a list written in the test, so layout number
eight cannot ship with a silent Checks panel.

The vocabulary the Rules are written in — `parseMonth`, `charsPerLine`, `opener`, `anyStemIn`,
`WEAK_OPENERS`, `SUPERFLUOUS`, `SCALE`, `OUTCOME` — moved from `resume_layout_headhunter.js` to
`resume_layouts.js`, because the baseline and that layout both read it and two answers to "what
counts as a month" would drift. `ResumeHeadhunter` now exports only what is still that
template's: its numbers, its colours, its sentence count, its one-sided tense test.

Two things this does **not** do. It does not make the baseline authoritative about wording — the
messages say "every standard here", never "this template", and a Layout that disagrees says so by
writing its own. And it does not touch the levels: the badge still counts everything above a
note, so the two baseline Rules that are advice rather than defect (`result`, and `on-template`'s
neighbours) stay quiet in the count.

**The cost of calibrating against the examples, named so the next person sees it coming.**
§4 resolves two of its three findings by *weakening* a baseline Rule to fit documents that
already shipped — the `bullet-count` floor was dropped and `SCALE` was widened. That is the right
call when the documents are exemplary and independently authored, which these are, and the wrong
one if they are merely what happened to be written. The pre-existing invariant
`"${id}'s example trips its own rules"` now applies to seven shipped documents rather than to
each layout's own handful, so **every future baseline Rule must pass all seven before it can
ship**. That is a real gate on adding one, and it can be satisfied the wrong way — by softening a
correct rule instead of fixing a weak example, which is the opposite of what happened here for
`jakes-resume`. Anyone adding a baseline Rule should say, in the ADR that adds it, which of the
two they did and why.

**The module keeps its name, and that was checked rather than assumed** (CLAUDE.md Rule 3).
`resume_layouts.js` grows by the baseline and by the vocabulary the Rules are written in — a
stemmer, two verb lists, two prose patterns — and a review asked whether `resume_rules.js` is now
the honest name. It is not: ADR-0123 already put "the rules it wants checked" on the Layout, so
Rules are Layer 2 by that ADR's own definition, and the vocabulary is what those Rules are made
of. Splitting them out would buy a shorter file at the cost of a fourth registry, a second
`<script>` tag and a layer boundary ADR-0123 does not draw.

The question is nevertheless recorded rather than closed, because the file is now the registry
*and* the rulebook at +278 lines while every other rule set lives in its own
`resume_layout_*.js`, and that asymmetry is a fair thing to dislike. **The answer for now is no,
and the trigger for yes is named:** split `COMMON_RULES` and the prose vocabulary into
`resume_rules.js` when either grows a consumer that is not a Rule, or when the baseline outgrows
the seven Rules here — whichever comes first. Doing it in the change that introduced the baseline
would have moved a file and added a layer boundary in the same commit that argues for the
boundary not existing.

One inherited limit is now more visible rather than less: a Rule that would genuinely apply to
one Layout and be *wrong* on another still has to be written twice, once per Layout, because the
opt-out is per-id and there is no "everyone except" form. Nothing in the seven needed one, and
inventing the mechanism before a Rule needs it would be the speculation this repo's own rules
forbid.
