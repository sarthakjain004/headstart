# ADR-0128: The Résumé tab is two segments — Edit and Preview

**Status:** accepted · **Date:** 2026-09-10 · **Narrows ADR-0125.** Its points 1, 5 and 6 stand
unchanged; its point 2 (Design / Checks / Keywords demoted into a `Polish` segment) and its
side-by-side arrangement are superseded here. No model layer moves: Components, Layouts, Content,
Rules and Variants are untouched. One Layer-3 command changes — `Cmd.setHidden` — and it gains a
layer rather than a meaning.

## Context

ADR-0125 chose **a form with a live preview beside it**, after research over four builders
(`docs/resume/2026-09-10_resume-builder-editor-patterns.md`) found that every product examined
edits in a rail and previews on the right. The user has since asked for the opposite, in these
words:

> "have a seprate tab to put the data in for the resume, and separate where people can view the
> resume they build"

and, against the same tab:

> "right now the tab looks very messy with everything bundled together in compact space, its not
> easy for a new user to get a hang of it"

> "also make the data put entry of resume data place bigger and more comfortable for the users,
> give them proper options, they can add multiple projects and choose which one they want to show
> in the actual resume"

**The measurement agrees with the complaint.** At 1280×800 on the guide's worked example, with one
job open — the state somebody writing a résumé is in for most of the session:

| | before | after |
|---|---|---|
| A bullet's own box (bullets are paragraphs) | **319 × 80 px** | **731 × 106 px** |
| A text field's width | **352 px** | **764 px** |
| Rows taken by a Work entry's first five fields | **5** | **3** |
| Tablists on the tab | **2, nested** | **1** |
| Tabs to traverse | **5** | **2** |
| Controls in the DOM / visible, at rest | 52 / 20 | 53 / 23 |

Note the last row: **this change does not remove controls.** The count is flat and the visible
count is three higher, because a wider form fits more of itself on one screen. What it removes is
*nesting* and *cramping*. Saying otherwise would be claiming a win the numbers do not support.

**What the research says about this exact move, honestly.** The doc files a Write/Preview mode
split under *Patterns to reject*: Standard Resume's, on the grounds that it "forces a mode-switch
to see the effect of a content edit, which is exactly the friction a live preview exists to
remove," and that "Reactive Resume and Teal both keep content and preview visible together." Two
things temper it. The doc flags that finding as **its own weakest-evidence claim** — Standard
Resume's editor is behind a signup gate, the split is inferred from one screenshot, and the note
says so and asks for a live check before treating it as settled. And live *field-edit* sync is
confirmed for exactly **one** of the four products (Reactive Resume, read from source); FlowCV's
"real-time preview" is explicitly excluded as evidence, Teal's liveness is confirmed only for
template changes, and Standard Resume's is unknown.

So the honest position is: the user asked for a split, the argument against it is real but thin,
and the cost is specific and nameable. This ADR names it and pays it down rather than pretending
it is not there.

## Options

1. **Two segments stacked on the existing rail** — `Edit | Preview`, and inside Edit the current
   `Document | Polish` segment with its `Design / Checks / Keywords` tablist. Smallest diff.
   Rejected: three levels of tabs. It makes the complaint that prompted this worse, and the layout
   picker would be four clicks from the page it changes.
2. **A responsive split** — side-by-side when the window is wide, segmented when it is narrow.
   Rejected: it answers a question the user did not ask. They asked for two places, on the machine
   they use, and a rule that gives them one place at 1280 and two at 900 is a rule they have to
   learn before they can predict the product.
3. **Two segments that absorb the existing strips** (chosen). Design goes with the page; Checks
   and Keywords go with the form; the two nested tablists collapse into one strip of two.
4. **Two top-level nav tabs** — `Résumé` and `Preview` beside Search and Trends. Rejected by the
   user when the three readings were put to them, and rightly: the document bar (name, Open, New,
   version, Undo, Download) would have to be duplicated or abandoned, and Download is the
   highest-intent control on the tab.

## Decision

**The Résumé tab is one document bar over two segments, and each segment is a main column with a
column of what helps beside it.**

```
Untitled résumé   Résumés │ Version ▾  Tailor for a job │ ↶ ↷  Download
[ Edit ] [ Preview ]
┌── Edit ──────────────────┬──────────────┐   ┌── Preview ───────────┬──────────┐
│ the form: sections,      │ miniature    │   │ the page             │ Design   │
│ entries, fields, bullets │ Checks       │   │ template chip · zoom │ Keywords▸│
│                          │              │   │ template gallery     │          │
└──────────────────────────┴──────────────┘   └──────────────────────┴──────────┘
```

1. **The bar stays above both segments.** Switching never hides the name, the version picker, Undo,
   or Download. The research's one piece of structural guidance is a warning against exactly the
   opposite: a high-intent, end-of-flow control that is hardest to reach at the moment it is most
   wanted.

2. **Design moves to Preview.** Every control in it — template, body size, line spacing, colour,
   paper size — is judged by looking at the sheet, which is the one place on this tab where a live
   loop is genuinely load-bearing. Standard Resume, the one product that does split this way, puts
   its design rail inside Preview mode too.

3. **Checks moves beside the form; Keywords stays with the page.** A finding names a block you
   then fix, so that loop ends in the form. Keywords looked like the same case and is not: it is a
   **measuring** panel — it asks how far down the *rendered sheet* each term falls — and the
   highlights it draws are on that same sheet. It was put beside the form first, and the trap
   below caught it: measured on a three-page résumé, a block **18.91in** down the document reports
   **0in** from the top while the paper is off screen, so every term would have been scored "top
   half, page one" whatever the truth was — silently, by the one check that exists to answer
   exactly that question. It sits with the sheet it reads. Both are plain panels in a column, not
   tabs; Keywords is folded away, because it is a task you opt into once per application.

4. **One strip, not three levels.** `Document | Polish` and `Design | Checks | Keywords` are both
   gone. `showStrip`/`wireStrip` were generic over two strips and are now written once, inline,
   for the one that remains.

5. **A miniature in the Edit column is what softens the lost live loop** — see below.

6. **Every block carries a tick that keeps it and stops printing it**, which is what "add multiple
   projects and choose which one they want to show" asks for.

## The live loop, and what replaces it

This is what the split gives up, and it is not nothing. A live preview beside a form answers two
questions while you type: *did my words land on the page*, and *does it still fit on one page*.
The second is the one this product has already decided matters — it is why the page-break markers
exist at all (ADR-0123).

Three softenings were weighed. **Auto-switching to Preview** on some action was rejected: a mode
that changes itself is a mode you cannot predict, and there is no honest trigger — every keystroke
is an edit. **Nothing at all** was rejected because it would silently drop a decision ADR-0125 made
on evidence. What ships is **a live miniature at the top of the Edit column**: the current document
rendered through the current Layout, at the sheet's true width and scaled — the same measured
arithmetic the template gallery's cards use, so it cannot drift from the page it stands in for. It
carries the page-break lines (labels suppressed; at this scale they would be 2px tall) and states
the page count in words underneath. Clicking it opens Preview, which is also how the segment stays
discoverable.

It is honestly a weaker instrument than the full sheet — you cannot read it. It answers the two
questions above and nothing else. The cost is one extra `renderDocument` per paint, measured at
**0.08 ms**.

**Teal's picker-level liveness — "see a preview of how it would look with your experiences" — the
research's own recommended way to deliver liveness at the moment it matters, is already how the
template gallery works** (ADR-0125 §4). Nothing here weakens it.

## The trap: a hidden element measures zero

This editor converts pixels to inches by measuring a rendered element. `ResumeEditor.shown()`
exists for exactly this reason — the tab's panel is hidden until app.js opens it, so the sheet is
fitted on the way in rather than at load. **The split puts a second hidden boundary inside the tab**,
and both segments hold something that gets measured: the sheet in Preview, the miniature in Edit.

Measured in Chromium at 1280×800 on a three-page résumé, with the segment switch **not** repainting:
the first switch to Preview drew the sheet at its raw **816 px** inside a **734 px** column,
reserved **0 px** of height for it (so the page drew over whatever followed), and drew **zero** of
the two page-break markers. `fitToWidth()` had run and computed the right zoom — nothing had applied
it, because `paint()` is what writes the transform, the wrapper's reserved height and the markers.
**Fitting alone is not the fix; the paint is.**

So: **opening a segment re-measures whatever it just put on screen.** The sheet is *fitted* once —
re-fitting on every switch would throw away a zoom the user had chosen — and *painted* every time.
Verified in a browser over ten switches: identical zoom (90%), identical reserved height
(1855.8 px), and both break markers at identical offsets on the first switch and the tenth. Both
halves are asserted by the test suite, and both were shown failing with the code reverted.

## Keeping a block without printing it

`Cmd.setHidden` and `resolve()` already existed, and `resolve()` already pruned hidden nodes — but
`hidden` was a **Tailoring's** list only, so `setHidden` was a no-op with no version active. There
was no way to keep five projects on the master résumé and print two.

**One command now writes whichever layer is being edited**: the named Tailoring's list under a
version, the document's own otherwise. `resolve()` unions the two, so a block the master leaves out
is left out of every version — which is what a Tailoring being a *difference from* the master
already means. The alternative, a second command and a second control beside the first, would have
put two identical-looking ticks next to each other and left the user to work out which layer each
one reached.

It is **per node**, so this is not a projects feature: work entries, degrees, sections and
individual bullets all get it from the same mechanism. Reactive Resume calls the same affordance
*Hide/Show* and hides it in a per-row `⋮` menu; Teal toggles bullets on and off per résumé version.
This is drawn as a tick **on the closed row**, because "which of my five projects does this résumé
show" is a question answered by reading down the column, not by opening every row to look.

A block Layer 1 declares un-removable (the header) is offered no tick — a résumé with no name on it
is not a thing to offer. Under a version, a block the *master* leaves out shows an unticked,
disabled box saying so: turning it on there could only do nothing or edit the master by surprise.

## Consequences

- **The two "1,004px" popovers finally close.** The Résumés and Download menus had no dismissal at
  all — not Escape, not an outside click — which only became visible once PR #407 moved them under
  the bar where the 247px panel covers what is behind it. Escape now closes the open menu and
  returns focus to its button; a pointerdown outside closes it. The owning button is excluded from
  the outside handler on purpose: it decides by reading `pop.hidden`, so closing before its own
  click arrived would make it reopen what the user asked to close.
- **The tab now scrolls as a page** rather than scrolling a rail inside itself: 845 px of scroll
  height became 2,109 px with a job open. That is the trade for a full-width form, and it is the
  better half — a nested scroller inside a page that also scrolls is its own usability problem, and
  the aside is sticky so Checks stays reachable.
- **`.rb-body` is gone; `.rb-work` is the workspace**, and the Edit column is an `<aside>` of
  `<section>` cards. `tests/js/resume_markup.test.js`'s containment walk read `<div>` only and now
  reads `<section|div|aside>` — a walk that skipped them would have kept answering, about the wrong
  ancestors.
- **ADR-0125's gallery cost estimate was wrong and is corrected.** It called the gallery
  *O(layouts)* full document renders and named ~30 layouts as the point needing cached or
  virtualised thumbnails. Seven are registered now (ADR-0126). Measured: the seven cards open in
  **15.3 ms**, of which the seven renders are **0.6 ms**. The render is not the cost — the forced
  synchronous layout for *measuring* each sheet is, at ~2.1 ms a card. The threshold is far above
  thirty.
- **Adding a field to an existing Component Type is not free**, which is why "give them proper
  options" was answered by grouping the form rather than by widening Layer 1. `resume_components.js`
  says a `header` or `entry` "prints every string field a node owns" — that is true of the *generic
  fallback* only. A `byType` strategy is written for a type it knows and names that type's fields.
  Counted across the seven registered Layouts: **`project_entry` is overridden by all seven**, so a
  `link` added to it today would be typed by the user and printed by nothing at all; **`header` by
  six**, the exception being `free-canvas`, whose header is a field-agnostic `byShape` strategy and
  would print it. Widening an existing type means a coordinated pass over the Layouts that name it,
  and that is its own change. What this one does instead: the eleven "add" buttons a
  Section offered at equal weight are ordered — the entry types a section is *for* first, the rest
  one disclosure away — so somebody adding a second project finds "Project" and "Project with a
  stack" rather than hunting past "Situation note" and "Language". Nothing the model allows is
  narrowed.
- **Ctrl+P printed a blank sheet, and now does not.** The stylesheet has always carried a print
  safety net for anyone who reaches for Ctrl+P (the real PDF path prints an iframe holding only the
  document). The split broke it: the sheet lives in a workspace that is `hidden` whenever Edit is
  the segment on screen, which is the default. Measured in Chromium — printing from Edit produced
  an **860-byte, one-page blank** PDF against the **88,968-byte, two-page** document printing from
  Preview produced. The print rules now unhide the workspace and clear the wrapper's two
  screen-only height caps; both segments then print **byte-identical** PDFs (88,977 bytes, 2 pages).
- **The same zero-measurement trap had a second door, and a third.** The template gallery hides
  the sheet while it takes the stage over, which would leave Keywords measuring a hidden page from
  a column still on screen — so the Preview column is hidden with the sheet. `pxPerInch()`'s other
  caller is the drag path, which cannot run while the paper is off screen, so it needs nothing.
- **An imported backup would have silently un-hidden everything.** `sanitiseIds` remaps every
  identifier a `.json` import carries — the tree, the content map, the variants, each Tailoring's
  `picks` and its `hidden` — and the document's own left-out list is new, so it was not in that
  list. Missed, an import that needed sanitising at all keeps ids nothing in the tree answers to,
  and every block the résumé had switched off comes back on. `duplicateNode` needed the same
  thought and got the opposite answer: a copy of a block that is switched off is switched off too,
  because "duplicate" means another one like this and *like this* includes off.
- **No horizontal overflow at 390×844 in either segment**, and the segment strip stretches to full
  width below 560px. A fresh phone load fits the sheet at 35% on the first switch to Preview.
