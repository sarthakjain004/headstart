# ADR-0123: A résumé is three layers — structure, layout, and words

**Status:** accepted · **Date:** 2026-09-09 · **Upholds ADR-0107/0041 (HeadStart's servers never store a Résumé). Sibling of ADR-0116 (the app's own palette stops at the paper's edge)**

## Context

HeadStart finds jobs. The step immediately before applying to one — having a résumé that a
recruiter will actually read — happened entirely outside the product, and the method the Résumé tab
is built around ("How to Get a Job", the Headless Headhunter) is a set of rules that a document
editor cannot check and a person will not remember: three to eight bullets a job, one period a
bullet, month and year on every date, reverse chronological, education inside three lines, and
three quarters of the job's keywords in the first half of the first page.

A résumé builder can be built as one thing — a form that emits a styled document — and almost all
of them are. That collapses three genuinely separate concerns into one, and the cost shows up the
first time someone wants the same words in a different shape: they retype them. The brief here was
explicit that the three had to be separable, that new layouts and new components had to be cheap,
and that blocks had to be draggable and resizable.

## Decision

**1. Three layers, three registries, one document.**

- **Layer 1 — Components** (`resume_components.js`). *What a résumé is made of.* A Component Type
  declares its id, a `shape`, the content fields it owns, what it may contain, and which editor
  affordances it can support. It says nothing about type, size, colour or position. Adding one is
  a call to `define`.
- **Layer 2 — Layouts** (`resume_layouts.js` + one file per layout). *How those components are
  arranged and how they look.* A Layout owns page geometry, type tokens, one render Strategy per
  shape (and optionally per type), the rules it wants checked, and the capability contract below.
  It emits its own CSS as a string. Adding one is a file and a `<script>` tag.
- **Layer 3 — Content** (`resume_document.js`). *The words.* A flat `content` map keyed by node id,
  deliberately outside the node tree, so switching Layout cannot touch a sentence — the invariant
  that makes the split worth having, and a test rather than an intention.

**2. `shape` is the extensibility hinge.** A Layout renders the types it knows by name and
everything else by shape, and registration fails outright if any shape is unhandled. A component
added after a layout shipped therefore still renders in it. Without this, "extensible" would mean
editing every layout for every component, which is the coupling the design exists to avoid.

**3. Draggable and resizable is a permission the Layout grants, not a property a component has.**
`caps.mode` is `flow` (reorder within slots) or `free` (x/y/w/h), `caps.resize` names the geometry
keys the layout honours, and `bounds` clamps them. A component states what it *can* support; the
layout decides what is offered. This is what lets the Headless Headhunter layout be a real
drag-and-drop editor and still refuse free positioning — the single column is the template's whole
argument, so it withholds the affordance rather than granting it and then disapproving. Geometry a
layout ignores is kept, not deleted, so a trip through a strict layout and back is lossless.

**4. Patterns, where each earns its place.** Composite (the node tree), Builder (assembly, so ids
and content cannot drift apart), Registry/Factory (the two extension points), Strategy (per-shape
rendering), Repository (persistence, below), Command (a named operation vocabulary — every gesture,
including a drag, is one, which is why undo covers drags), Observer (store → views), Visitor (the
exporters and the rule runner, dispatching on shape). Undo is a bounded stack of prior documents —
a memento, not inverse operations: undoing a subtree deletion needs the subtree back, so an inverse
would have carried a snapshot anyway under a name that hid it.

**5. The words stay in the browser.** ADR-0041 and ADR-0107 already say HeadStart's servers do not
store a Résumé. The builder honours that: `localStorage`, no endpoint, no upload. The Repository
interface is the seam that makes this a choice rather than a weld, and `MemoryRepository` is a real
fallback for browsers that block storage, not only a test double — with a visible warning, because
a builder that silently forgets is worse than one that says it will.

**6. Download is the browser's own print pipeline, plus three files.** PDF is `window.print()` on an
off-screen iframe holding the layout's own stylesheet; Word (`.doc`), plain text and JSON come out
of a Blob. No PDF library, no server-side renderer. The preview, the print and the download are
literally one stylesheet, so fidelity is structural rather than a thing to keep re-testing.

**7. The template's rules are findings, never locks.** The layout ships them; the panel states them
in the guide's own words and links each to the block it is about. A user may set a 12pt body and the
page will print — the panel then says the template asks for 10.5. Locking would make the tab a
straitjacket; silence would make "Headless Headhunter" a label rather than a claim about the output.

## Options considered

| | | for | against |
|---|---|---|---|
| **A (taken)** | Three layers, shape dispatch, layout-granted affordances | the same words re-lay under any layout; a component and a layout are each one file; the template's rules are data | more moving parts than a form; the shape vocabulary is a small up-front commitment |
| B | One templating layer: each template renders the whole document | simplest to write the first template | the second template duplicates the first; adding a component edits every template; no separation to test |
| C | Free canvas only, like a design tool | maximal flexibility, one interaction model | it is the wrong default for the one method this tab exists to teach, and the guide says so plainly |
| D | Server-rendered PDF (WeasyPrint on the Space) | exact typography, no print dialog | a heavy dependency on an image already carrying torch, and the résumé would have to be uploaded — which ADR-0107 forbids |

## Consequences

The Résumé tab needs no route, no endpoint and no feature flag: it works on the Space and on the
local dev renderer alike, and it stores nothing on our side. That also fixes its limits — a résumé
lives in one browser, so the tab pushes the JSON backup rather than pretending otherwise, and
anything that needs a résumé to exist server-side (attaching one to an auto-apply run, ADR-0105) is
a decision that has not been made here.

Three layouts ship: the Headless Headhunter template, a two-column CV, and a free canvas. The
second and third exist to keep the extensibility claim falsifiable — a build with only flow layouts
could assert the capability contract and never be tested on it.

Editing is through the rail, not on the page. Direct manipulation covers position and size; words
are typed into fields beside the preview. Inline editing on the paper is the obvious next step and
is deliberately not half-built here.
