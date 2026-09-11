# Résumé builder: measured open defects at the close of review round 6

**Status: open.** Nothing in this document is fixed. It is the measured output of the last
review round before the feature was wrapped, written down so the measurements are not lost and
so nobody has to re-derive them.

**What was reviewed:** `origin/main` at `aaece680`, covering the résumé builder shipped across
PRs #402–#432 and #436 — `src/headstart/ui/static/resume/` (17 modules), `resume.css`, the
templates, the read-only MCP server, and `tests/js/` (303 passing).

**Two axes reported; a third did not finish.** A résumé-domain axis (via the `resume-builder`
skill) and a code-and-standards axis both scored the feature **7/10**, for disjoint reasons. A
browser-rendering axis was stopped mid-run and produced nothing — see §4.

---

## 1. Why these findings exist at all

This feature shipped **four tests that passed either way** — they stayed green with the fix they
guarded reverted. One was written during this review loop, not before it. That is the failure
mode this round was built to hunt, and both axes used the same discipline in response: *measure
the rendered artefact, and prove a guard by breaking what it guards.*

The code axis built a mutation harness — apply a patch, run the suite, restore — and ran **75
mutations** across the feature's JS, its MCP Python, and the templates. **60 died, 15 survived.**
Every code finding below is a survivor. The domain axis rendered all nine layouts through the
real export path and read the output back with two independent text engines (`pdftotext` and
`textutil`), rather than reasoning from source.

Both are samples, not proofs. 75 mutations over ~5,600 lines leaves most of the rule bodies and
the component catalogue unprobed.

---

## 2. Open defects, worst first

### 2.1 Two layouts put the candidate's name at the END of the Word/HTML export

`renderDocument` emits slots in declaration order. Deedy and Modern Sidebar both hoist their
header *visually* — Deedy through `grid-template-areas`, the sidebar through
`flex-direction: row-reverse` — while declaring the header's slot late. Anything that does not
run CSS layout therefore reads the name last.

Measured on the shipped worked examples, agreeing across two engines: Deedy puts
`Priya Venkatesh` on **line 39 of 40**, opening the file with `EXPERIENCE / Freshworks`. Modern
Sidebar puts `Tomás Herrera` on **line 19 of 39**, after the whole work history and education.

ADR-0126 §3 justifies the sidebar's slot order on the grounds that the dated history is what a
parser reaches first. That trade bought the job history and sold the name, and only the first
half was measured when it was made.

The plain-text export is **not** affected — it walks `doc.root.children`, so the name is line 1
on all nine layouts. This is the HTML-shaped path only.

- `resume_layouts.js:860-875` (the slot loop), `resume_layout_deedy.js:217,430`,
  `resume_layout_sidebar.js:120,171,312`
- Fix: medium. A layout needs a *reading order* for slots distinct from its *visual order*, or
  the `header`-shaped node must always be emitted first. The missing distinction is real enough
  to deserve an ADR.

### 2.2 The property that defines a Variant is guarded by nothing

CONTEXT.md §Résumé builder defines a **Variant** as partial — it carries only the fields that
differ from the master's Content — and explicitly rejects the word *override* because that would
imply replacing the whole record. Both merge sites can be changed to a whole-record replace and
the suite stays green:

```
doc/contentOf-replaces-base      SURVIVED   303/303 pass
doc/resolve-merge-replaces-base  SURVIVED   303/303 pass
```

The cause is a sampling artefact: **all 16 `setContentFor(..., tailoringId)` calls in the suite
tailor a `bullet`**, and `bullet` declares exactly one field. The merge that makes a variant
partial is never exercised on a multi-field block. On a `work_entry`, tailoring the role alone
should leave employer, place and both dates intact; under the surviving mutation they vanish
from the tailored preview, the print, the plain-text export and the MCP reading.

- `resume_document.js:227,238`
- Fix: one test, ~15 lines — tailor one field of a `work_entry`, assert the siblings survive.

### 2.3 `'main'` is not inert — round 5 recorded this wrongly

Round 5 filed the magic slot id `'main'` as inert. Measurement contradicts that. Renaming a
single layout's first slot to `'column'` — in a file that never mentions the editor — makes
every drag inside that layout's only column leave a second, no-op undo entry, so the user must
press Undo twice:

```
main/headhunter-slot-renamed   [node]     SURVIVED   303/303
main/headhunter-slot-renamed   [browser]  RED
```

It is inert *today* only because all nine layouts happen to name their first slot `'main'`, and
that invariant is pinned by exactly one assertion for one layout (`resume_layouts.test.js:301`).
The string is spread across `resume_document.js:90,386`, `resume_layouts.js:599` and all nine
layout files — Primitive Obsession with a Shotgun Surgery cost.

- `resume_editor.js:386`
- Fix: small. Either read `lay.slots[0].id`, or add a derived test asserting every registered
  layout's first slot is `main`.

### 2.4 `tests/js/resume_export.test.js` is a binary file to git and to grep

Two literal NUL bytes at offsets 2126 and 2210, inside a string literal and a regex class.
Measured consequence on a two-line edit:

```
current file:  tests/js/resume_export.test.js | Bin 6898 -> 6907 bytes
               1 file changed, 0 insertions(+), 0 deletions(-)
NUL-free:      2 ++
               1 file changed, 2 insertions(+)

$ grep -n "filename" tests/js/resume_export.test.js   ->   (no output, exit 1)
```

A PR touching this file shows its reviewer a byte count and no diff, in a repo whose conventions
require a review on every code-changing PR. `git blame` and `git log -p` are equally blind, and
any grep-based audit of the test suite skips it silently.

- Fix: two characters — replace each raw byte with its JavaScript escape. Verified
  behaviour-preserving; the file's nine tests still pass and git then diffs it as text.

### 2.5 The `three-lines` estimator is systematically biased, and the C9 xfail is misdiagnosed

`charsIn` assumes an average character advance of `size * 0.5`. Measured against real printed
line boxes it is wrong in the same direction on every layout — the true advance is **0.41–0.45**.

| layout | estimate | measured | real lines | rule says |
|---|---|---|---|---|
| headless-headhunter | 85 | 98, 94 | 3 | "About 4 lines long" — false |
| harvard-classic | 93 | 109, 97 | 3 | silent |
| jakes-resume | 104 | 124, 114 | 3 | silent |
| free-canvas | 95 | ~108 | 3 | "About 4 lines" — false |
| two-column | 64 | ~72 | 4 | "About 5" |
| modern-sidebar | 69 | ~77 | 4 | "About 5" |
| europass | 66 | ~81 | 4 | "About 5" |
| deedy-resume | 68 | ~81 | 4 | "About 5" |

Two consequences. The false "split this bullet" band on the flagship layout starts at about
**256** characters — a good two-line bullet — and reproduces at 264, not only at the 281 the
xfail names. And the xfail's stated reason, that raising the cap would re-break the column
layouts, does not survive measurement: the column layouts under-report by the *same* proportion,
so a uniform recalibration to ~0.44 fixes headhunter and canvas while the four column layouts
still correctly fire on a genuine four-line bullet.

The comment at `resume_layouts.js:173` claiming the constant lands within 1% on
`headless-headhunter` (86 true against 85) disagrees with the measured 94–98.

- `resume_layouts.js:152-155,173`; `tests/test_resume_editor_browser.py:187-198`
- Fix: small to recalibrate; medium to measure real line boxes off the preview DOM, which is
  already dimensionally the printed page.

### 2.6 The ATS-friendly export writes the one word the standard forbids

`plainText` calls `Layouts.dateRange(content)` with no style, so it emits the Headless
Headhunter's spelling regardless of layout. All nine `.txt` exports contain **`to Current`**,
while the same documents print `– Present` on the page on 8 of 9. The product's own
`present-not-current` rule tells the user to tick "Still here" and let the layout print it for
them — and doing so produces the forbidden spelling in the file the module docstring calls the
version an ATS parses cleanly. Mainstream guidance asks for `Present`, and for an en dash rather
than the word "to".

- `resume_export.js:61,93`; `resume_layout_harvard.js:263`
- Fix: small-to-medium — put the date style on the layout spec and have `plainText` read
  `Layouts.get(doc.layoutId)`.

### 2.7 The generic `entry` fallback emits bare list items outside any list

`plainStrategies().entry` concatenates children raw, so a layout with no `byType` strategy for
`tech_project` or `degree_entry` prints their bullets as orphan `li` elements. Round 5 recorded
this as two-column-only and `tech_project`-only; rendering one node of every registered type
across all nine layouts shows it is **three layouts** — `two-column`, `headless-headhunter`,
`free-canvas` — and **both** component types, three each.

It is visible in print, not only in markup: in the two-column PDF those bullets print flush-left
with no marker and no indent, indistinguishable from the entry header above them. The text
survives extraction, so this is a segmentation and formatting defect rather than data loss.

- `resume_layouts.js:1037`
- Fix: small — one line, `groupChildren(ctx)` instead of `ctx.children.join('')`.

### 2.8 Rule coverage is uneven across layouts, and one gap has a clean answer

An **undated degree** is flagged on 1 of 9 layouts. The round-5 question of whether that should
be all nine or none now has an evidence-backed answer: **all nine, at `warn`**. A document with
an empty-dated `degree_entry` is silent on 8 of 9; only `deedy-resume` speaks, and its note is
about the template's third line rather than the standard. Separately, **9 of 9 shipped worked
examples date their education entry** — unanimity among the layout authors, which is precisely
ADR-0127's bar for promoting a rule to baseline.

An implementor must accept a non-empty `status` on `education_entry` (that field *is* the
compressed date) and a bare `end` with no `start`, which `dateRange` already reads as the
graduation date.

Two sibling gaps, same shape: **tense** is checked on 1 of 9 and misses the third-person `-s`
form everywhere, including on the layout that has the rule; **standard section headings** are
checked on 1 of 9, and promoting that rule needs the heading set widened first, because
`headless-headhunter`'s own starter names its section "Work History", which is not in
`STANDARD_HEADINGS`.

- `resume_layouts.js:348,283-297`; `resume_layout_deedy.js:364`;
  `resume_layout_headhunter.js:96-116,326`; `resume_layout_harvard.js:217-227,279`
- Fix: small each.

### 2.9 `two-column` ships with no ATS caution despite measuring as the worst offender

Four layouts scramble under PDF column linearisation. Extraction from `two-column` opens
`SKILLS / Katarzyna Wójcik / … / EXPERIENCE / April 2022 to Current / …` — a section heading
before the name, and each date range before the role it belongs to. `deedy`, `sidebar` and
`canvas` each name the risk in their picker copy and point at a safe alternative, which is
ADR-0126 §3 working as designed. `two-column`'s blurb is a developer note about sharing no code
with the first layout: no audience, no caution, no alternative.

- `resume_layout_twocolumn.js:178-180`
- Fix: small — write the blurb.

### 2.10 Contracts that are stated in a comment and enforced by nothing

Four in this class, all mutation survivors.

`tests/js/resume_harness.js:29-32` declares that its layout order is kept in lockstep with
`base.html`, and says outright that a test exercising a different order is a test of nothing.
Swapping two script tags in `base.html` is invisible to both suites — yet load order *is*
`Layouts.all()` order, which drives the gallery and `Layouts.all()[0]`, the fallback layout for
a new document and for an unknown `layoutId`. For the same reason, a layout can be dropped from
the page entirely and CI stays green: the harness loads layouts from disk, not from the template.

`resume_layouts.js:848-849` promises that a node whose stored slot no longer exists lands in the
first slot rather than disappearing. All three implementations of that fallback can be removed
without a test failing — and the trigger is routine, since the Deedy worked example puts 6 of 9
top-level blocks in slots Headhunter does not have.

Three documented fixes have no guard at all: the Word BOM (whose comment states that Word
mangles non-ASCII without it), the sync re-arm at `resume_sync.js:267` (whose comment says the
line is easy to miss and that a document edited during a push would otherwise sit unpushed), and
the filename length cap. The BOM and the re-arm are cheaply testable in node, since `download`
takes an injected `win` and `Sync` takes injected `schedule`/`cancel`. The Firefox revoke delay
is genuinely browser-only.

- Fix: small each; roughly 40 lines of test in total.

### 2.11 Smaller items

Keyword coverage reports a false miss on singular-versus-plural: a JD term `API` reads as
missing against a résumé saying `APIs` (`resume_decorators.js:64,71`). Synonym gaps such as
`k8s` are correctly out of scope, and the matcher is otherwise the strongest component in the
feature — it measures first-appearance depth in inches against the rendered page.

Deedy's `metaSize` of 8.5pt sits below the 10pt floor every packaged source states, and it
carries dates. The name sizes and margins that exceed the stated ceilings on five layouts are
**not** defects — ADR-0126 chose template authenticity deliberately.

`SCHEMA` (`resume_document.js:674`) and `startDocument` (`resume_editor.js:2592`) are exported
but read only inside their defining modules. Per CLAUDE.md §4 they are mentioned, not deleted.

`resume_markup.test.js` names a module that does not exist; it tests `resume.html` and
`resume.css`.

`resume_editor.js` is 2,615 lines with 15 responsibilities named in its own banner comments, and
**14 of the feature's 31 commits (45%) touch it** — 2.3× the next file. The honest
counter-argument is that it is the composition root. Keyword coverage and the miniature are
self-contained enough to extract; the rest is not worth disturbing.

---

## 3. What was attacked and held

Recorded so nobody spends the effort twice.

The **MCP single-account guarantee is structurally enforced**, not merely checked. Five attacks
all went red: opening the input schemas, ignoring extra arguments, adding an `email` parameter to
`Account.documents`, adding an `account` parameter to `Account.document`, and registering a
write-shaped tool. The disclosure obligations — the freshness note, the sync note, and the
unreadable-versus-missing split — go red when dropped.

**ADR numbering is clean and the race is now guarded.** At `aaece680`: 132 files, max 0137, no
duplicate prefixes, every one indexed in order, and no reference to a nonexistent ADR anywhere in
the repo. (0138 was claimed by a concurrent branch hours later — the race is live, which is the
point of the guard below.)
`test_adr_index.py::test_adr_numbers_are_unique_across_files` was confirmed by planting a
synthetic collision. It needs no heavy dependencies, so it really runs in CI. It detects after
merge rather than preventing, but on a PR's merge ref that is before merge.

**The `shape` dispatch holds.** 68 of 71 strategies are absorbed by `plainStrategies()`; the
three that leaked back would be absorbed by letting it take a class prefix. No type-switch
leakage. A `DATES` constant is duplicated verbatim in six files — a small Data Clump.

`resume_markup.test.js` (6 of 6 mutations red), `resume_sync.test.js` (11 of 12, including the
whole conflict path and the OFF-before-confirmed ordering), `inspect_document.js` (6 of 6) and
`resume_export.js`'s import hardening (10 of 13) all earn their keep. No orphaned CSS: every
`rb-` class that looks unused is built by concatenation.

On the domain side: **the component catalogue gaps are closed** — `profile_line`,
`certification`, `award_entry`, `professional_summary` and `language_line` are all registered,
and leadership is deliberately a `work_entry` inside a named section with the counter-argument
recorded in ADR-0130. Do not re-raise it. **The rule checks catch what they claim**: across 19
probed bullets, weak openers, superfluous verbs, pronouns, terminal periods, unquantified
bullets and out-of-order jobs all fire correctly, and no rule flagged a correct résumé except
through §2.5 and §2.8. **The plain-text export's ordering is correct on all nine layouts**,
column layouts included.

---

## 4. Limits of this review

- **The browser-rendering axis produced nothing.** It was stopped mid-run when the feature was
  wrapped. Pixel-level rendering, the Checks panel's own DOM, and the Edit/Preview and
  mini-preview interactions at real viewports are therefore unreviewed in this round. Given that
  every user-reported defect in this feature was invisible to a green suite, this is the largest
  gap here.
- **No real ATS parser was involved.** `pdftotext` and `textutil` are proxies; Greenhouse,
  Workday and iCIMS résumé ingestion is closed-source and nothing was submitted to one.
- **No Word or LibreOffice on the review machine.** "Word renders in source order" is judgement
  supported by two independent text engines that do exactly that. The `.doc`-is-actually-HTML
  choice carries a parser-sniffing risk that was not measured either way.
- **The browser suite never runs in CI.** Playwright is absent from `pyproject.toml`'s `dev`
  extra and from every workflow, so all 13 tests skip, always — while `pyproject.toml`'s own
  comment three lines above that list states the opposite principle for the same situation. It
  is the only guard for at least three fixes. It passes locally: 12 passed, 1 xfailed, with the
  xfail correctly `strict=True` and narrowly `raises`-scoped.
- **Mutation coverage is a sample.** 75 mutations, biased toward comment-documented fixes and
  ADR claims. `resume_components.js`'s catalogue, most rule bodies and about two-thirds of
  `resume_editor.js` were not probed.
- **Node version drift.** CI pins Node 22; these measurements were taken on Node 26.
- Whether the four previously-known "passed either way" tests are among the 15 survivors is
  **unknown** — the reviewer was not told which four. §2.2 and §2.10 are the strongest
  candidates.

## Related

- `docs/resume/2026-09-10_non-core-sections-us-swe-resume.md` — which sections belong on a US
  SWE résumé, and the evidence for it.
- `docs/resume/2026-09-10_resume-builder-editor-patterns.md` — how competing builders arrange
  the editor.
- ADR-0123 (the three layers), ADR-0126 (which layouts ship, and the evidence standard),
  ADR-0127 (the baseline Rule set and its promotion bar), ADR-0130, ADR-0137 (the MCP server).
