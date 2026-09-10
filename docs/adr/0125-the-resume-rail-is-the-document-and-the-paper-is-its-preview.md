# ADR-0125: The résumé rail is the document; the paper is its preview

**Status:** accepted · **Date:** 2026-09-10 · **Rearranges the view of ADR-0123 (a résumé is three layers). Changes no model layer: Components, Layouts, Content, Tailoring and the repository are untouched.**

> **Narrowed by [ADR-0128](0128-the-resume-tab-is-two-segments-edit-and-preview.md) (2026-09-10).**
> Points 1, 5 and 6 below stand. Point 2 does not: `Design`, `Checks` and `Keywords` are no longer
> a `Polish` segment beside `Document` — Design moved beside the page and the other two beside the
> form, and the nested tablist is gone. Nor does the side-by-side arrangement: the form and the
> page are two segments of one tab, `Edit` and `Preview`. ADR-0128 states what that costs and what
> replaces the live preview. The gallery cost in *Consequences* below (*O(layouts)*, ~30 as the
> threshold) was a projection and is corrected there by measurement.

## Context

ADR-0123 built the Résumé tab as a **canvas with an inspector** — the shape Figma has. The
rendered page is the workspace, you click a block on it to select one, and a rail beside it
inspects whatever is selected. Every capability worked. The arrangement did not.

Measured at 1280×800 on the guide's own worked example, before the user types anything:

| | |
|---|---|
| Controls in the DOM (excluding the paper's own drag handles) | **57** |
| Controls visible at once | **33** |
| Stacked rows of chrome above the page | **3** (the bar, plus a stage head that wrapped to two) |
| Rows in the rail's Outline | **13**, of which **7** were single bullets |

The specific failures behind those numbers:

- **The top bar held eight controls at one visual weight and in no order** — name, Open, New, a
  "Master résumé" dropdown, "+ Version", Undo, Redo, Download. "Download" and "+ Version" were the
  same size and two apart. A newcomer had nothing to read the difference from, and "+ Version"
  names a mechanism rather than a purpose.
- **A second band held four unrelated things** — a layout `<select>`, a summary string, a paper
  size and an unlabelled zoom slider — none of which is a per-session choice. A third line
  carried the method credit.
- **The rail's Outline was a developer's node tree.** Every node at one indent, including all
  seven bullets truncated mid-sentence ("Bullet Operated our Point of Sale (POS) cas…"). It is how
  a debugger draws a tree, not how a résumé reads, and it dominated the panel.
- **Four tabs — Content / Design / Checks / Keywords — competed for one 340px column**, which
  claims that "Keywords" and "the résumé" are the same kind of thing.
- **Nothing directed the first action.** The only guidance was the sentence "Click a block on the
  page to edit its words", beside a page that gave no affordance suggesting a block was clickable
  — prose standing in for an affordance.

Research into how the field actually solves this (`docs/resume/2026-09-10_resume-builder-editor-patterns.md`,
plus a parallel pass over Resume.io, Kickresume and Novoresume) found the same answer in every
product examined: **a form on one side, a live preview on the other, and no click-to-edit on the
rendered page at all.** Reactive Resume's preview is read-only, fed from the same reactive store as
its sidebar forms; Teal separates a "Builder" mode from a "Designer" mode; Resume.io, Kickresume and
Novoresume are all left-form / right-preview. Inline editing on the paper is not table stakes — it
is a thing none of them does.

## Decision

**The rail is the document. The paper is a preview of it. Direct manipulation survives as a second
path, not the main road.**

1. **The rail is an accordion of the résumé's own sections**, not a node tree. Contact, then each
   Section, each opening to its entries, each entry opening to its fields *and its bullets*,
   inline. Editing happens where you navigate, so ADR-0123's separate "Outline" and "Content"
   concepts collapse into one thing. A bullet is drawn as what it is — a field of the job it
   belongs to — rather than a sibling of "Work History". 13 rows at rest became 3.

2. **Design, Checks and Keywords are demoted one level**, into a `Polish` segment beside
   `Document`. Two segments, not four tabs. Both strips are real tablists with roving tabindex and
   arrow traversal, wired by one shared function, which is the APG's nested-tablist pattern.

3. **The chrome is one band in three hairline-separated groups** — document (name + a `Résumés`
   menu holding Open / New / Import), version, actions (undo, redo, and Download as the only
   primary). Layout, paper size and zoom leave the chrome entirely.

4. **The layout picker becomes a gallery that takes the stage over**, and every card is the
   *current document* rendered through that layout — not a stock thumbnail. Affordable because a
   Layout's render is a pure string function (ADR-0123), so it costs one render per registered
   layout — three today — and only while the gallery is open. Each card carries that layout's own
   `summary`, `blurb` (which is where an honest ATS warning about coloured sidebars or
   multi-column parsing belongs) and `credit`. The miniature's scale is **measured** from the
   rendered sheet rather than derived from `page.width × 96`: `page.unit` is whatever the layout
   declared, and a layout declaring mm would have rendered ~25× off.

5. **Checks are reported twice, deliberately.** The `Polish` segment carries a persistent count
   badge, and each finding is *also* rendered inline at the top of the block it names, with a mark
   on every collapsed ancestor so a problem three levels down is findable without opening every
   row. The full list stays in the Checks panel.

6. **The first run gets a control, not a sentence** — a "Start here" card whose primary button
   opens Name & contact and focuses the first field. It stands down the moment a word is typed.

## Consequences

Measured after, same page and viewport: **55 controls in the DOM, 22 visible** (33 → 22), one band
of chrome, 3 rail rows at rest, and one click from load to a focused field for the user's own name.
No pointer target under 24 CSS px, and no horizontal overflow at 390×844 in either theme.

Every capability is preserved: drag reorder, resize with typed equivalents, undo/redo, tailoring,
layout switching, decorators, all five exports, page-break markers, the rules panel and keyword
coverage. Verified end to end in Chromium.

That claim was false on the first cut and the two-axis review caught it, so both halves are now
asserted by the browser harness rather than argued: a **bullet** carried no rail anchor (so the
"clicking a block reveals it in the rail" tie silently no-oped on the block type edited most), and
under the free-canvas layout a bullet was given move and box handles on the page with **zero**
typed equivalents — measured 1 handle / 0 controls against the pre-fix commit, which is a WCAG 2.2
SC 2.5.7 failure visible on one layout only. Both are fixed; the harness now fails if either
returns.

**What this costs.**

- Rendering the gallery is *O(layouts)* full document renders. It is lazy and only on open, but a
  registry of thirty layouts would need thumbnails cached or virtualised.
- The rail's caret guard (ADR-0123) freezes the pane the user is typing in, which would have made
  an inline check invisible for exactly the block being edited. The checks and the ancestor marks
  are therefore patched in place while frozen, the same way the range labels already are. That is
  a second, narrower render path over the same data — a real cost, accepted because a warning that
  only appears after you stop typing is a warning about the wrong thing.
- Zoom did not follow layout and paper size into the Design pane. It is a property of the *view*,
  not of the document, and burying the most-used view control three clicks deep would have been
  the wrong trade; it sits in a status strip under the page instead, next to the name of the
  template being previewed. Reactive Resume reaches the same conclusion — its zoom lives in a
  floating dock by the preview, not in either design sidebar.
- The preview now scrolls inside a capped frame rather than making the whole tab tall. Without the
  cap, a full-height sheet pushed the strip below it — and with it the only control that opens the
  template gallery — past the bottom of a 1280×800 window.

**What is deliberately still true.** The paper keeps `flex: 0 0 auto` and its true printed width;
zoom does the fitting. A sheet that shrinks re-wraps text and stops being the page, which ADR-0123
records as a real bug — nothing here reintroduces it.
