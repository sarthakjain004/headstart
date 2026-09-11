# ADR-0134: The shape says which blocks the printer may not split

**Status:** accepted · **Date:** 2026-09-11 · **Extends ADR-0123 (three layers), whose shape-dispatch contract this puts one more fact behind.**

## Context

A résumé entry is **atomic**: the printer moves a whole job or degree onto the next sheet rather
than cutting it in half. Two parties act on that promise, and until now each made it in its own
vocabulary.

- The editor's page-break sweep (`resume_editor.js`, `pageBreaks`) finds the atoms by **shape** —
  `[data-shape="entry"]`, which `renderNode` stamps on every node any Layout draws — and moves a
  drawn cut to the top of any atom that would straddle it.
- The **stylesheets** answered in each Layout's own entry **class**: `.hh-entry`, `.jr-entry`,
  `.hv-entry`, `.tc-entry`, `.sb-entry`, `.ep-entry`, `.mc-entry`, `.dy-entry`. `free-canvas`
  declared none at all, and the `byShape.entry` fallback emits `rb-entry`, which no Layout marked.

The shape selector was itself a fix: it replaced a hardcoded four-class list that was wrong for
four of the seven then-registered Layouts. That fix changed one side and left the other speaking
classes, so the two have disagreed ever since. Measured 2026-09-11 in Chromium, with one node of
every entry-shaped type added to the first section:

| Layout | entries | `break-inside: avoid` | treated as atomic but splittable |
|---|---|---|---|
| `headless-headhunter`, `two-column` | 7 | 2 | 5 |
| `jakes-resume`, `harvard-classic`, `modern-sidebar`, `europass` | 7 | 5 | 2 — `certification`, `award_entry` |
| `mcdowell-cv`, `deedy-resume` | 7 | 7 | 0 |
| `free-canvas` | 7 | 0 | 7 |

Both components ADR-0130 added are in that set on seven of the nine. It is not theoretical: with
six four-bullet `tech_project` entries the sweep *moved* a cut to the top of a block Chromium
computes as `break-inside: normal`, so the preview told the user a page ended where it does not.

The existing browser check asserts the other direction — that no drawn cut falls *inside* an avoid
box — which is green whichever way this disagreement runs. That is how it survived.

## Decision

**The shape owns the answer, and the stylesheet is derived from it.** `ResumeLayouts.define`
appends one rule to every Layout's emitted CSS:

```
{scope} [data-shape="entry"] { page-break-inside: avoid; break-inside: avoid; }
```

It is appended, so it lands after the Layout's own rules at equal specificity, and it goes through
the same `css(theme, scope)` that the preview, the miniature, the gallery card, the print dialog
and the HTML download all already call — one stylesheet, five surfaces, as ADR-0123 requires. The
eight hand-written declarations are deleted; every `*-entry` class was applied through `ctx.el` on
an entry-shaped node, so the generated rule is a strict superset of what they said.

## Options considered

| | | for | against |
|---|---|---|---|
| **A (taken)** | the shape owns it; `define` emits the rule | one statement of the fact, in the vocabulary the sweep already reads; a tenth Layout inherits it without knowing; assertable in `node --test`, which CI can actually run | takes a decision away from a Layout that might one day want a splittable entry |
| **B** | the stylesheet owns it; the sweep reads `getComputedStyle(el).breakInside` | the sweep's job is to predict the printer, and the printer reads computed style — so this is true by construction | a forced style read per node on every keystroke; and it leaves the fact spread across nine stylesheets, so a tenth Layout that forgets silently stops being predicted — the exact failure the class list already caused once |
| **C** | keep both, add a test that they agree | smallest diff | keeps the duplication whose drift is the defect, and buys only a red run instead of a fix |

**A.** There is no seam here, only a duplicated fact: eight of nine Layouts had already declared
this by hand, unanimously, and the ninth had simply never written entry CSS — not a different
opinion. *One adapter means a hypothetical seam; two means a real one*, and there is no second
adapter. A Layout that one day wants a breakable entry has to change the line in `define` — which
is also the line the sweep reads, so the two cannot go out of step again without somebody noticing.

**B** is what the sweep's job argues for and it remains the honest check, so it is kept as exactly
that: `tests/test_resume_editor_browser.py` asserts, on every registered Layout, that every
`[data-shape="entry"]` node computes to `break-inside: avoid`. It measures the consequence instead
of computing it on every repaint.

## Consequences

- `free-canvas` entries become unbreakable in print, which they were not. That is the change with
  the most behaviour behind it and it is the correct direction: the sweep was already drawing its
  cuts as though they were.
- The `certification` and `award_entry` blocks stop being split mid-record on six Layouts.
- A tenth Layout gets this for free and cannot forget it, which is what the shape contract already
  promises for rendering.
- The claim is checked twice on purpose: `tests/js/resume_layouts.test.js` asserts the declaration
  is emitted and scoped (CI can run this), and the browser test asserts what it computes to (CI
  skips this — there is no Chromium there).
