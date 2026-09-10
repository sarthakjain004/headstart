/* The Résumé tab's static markup, where it carries a contract the stylesheet depends on.
 *
 * The DOM stub in resume_editor.test.js answers by element id and knows nothing about nesting,
 * and nothing here measures — so a positioning bug that is entirely about which element contains
 * which is invisible to every other test in this directory. This file reads the shipped template
 * and asserts the containment the CSS is written against.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const UI = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui');
const TEMPLATE = path.join(UI, 'templates', 'resume.html');
const CSS = path.join(UI, 'static', 'resume.css');

/** The class names on every `<div>` still open where `needle` appears, outermost first — the
 *  element carrying the needle excluded, since nothing contains itself.
 *
 *  Two things this deliberately does rather than trusting:
 *  - Jinja comments are stripped first. This template carries eleven of them, and one that merely
 *    QUOTED an opening tag would otherwise be counted as real markup — which would make these
 *    tests pass on exactly the regression they exist to catch.
 *  - An unbalanced close throws. Popping an empty stack would silently return a short answer,
 *    and a containment test that fails open is worse than no test.
 *
 *  It reads the three elements this template nests through — `<section>`, `<div>`, `<aside>` —
 *  and it is a walk over one known file rather than a parser. `<div>` alone was enough until the
 *  Edit segment's column arrived as an `<aside>` of `<section>` cards (ADR-0128), at which point
 *  a walk that skipped them answered about the wrong ancestors while still looking right. If the
 *  template grows nesting through a fourth element, the anchor test below is what says so. */
const NESTS = 'section|div|aside';
function openAt(html, needle) {
  const source = html.replace(/\{#[\s\S]*?#\}/g, '');
  const at = source.indexOf(needle);
  assert.notEqual(at, -1, 'the template no longer contains ' + needle);
  const open = [];
  const tag = new RegExp('<(?:' + NESTS + ')\\b([^>]*)>|</(?:' + NESTS + ')>', 'g');
  let m;
  while ((m = tag.exec(source)) && m.index < at) {
    if (m.index + m[0].length > at) break;   // the needle's own tag: a div does not contain itself
    if (m[0].charAt(1) === '/') {
      assert.ok(open.length, 'the template closes a tag that was never opened — this walk is lost');
      open.pop();
    } else {
      const classes = (/class="([^"]*)"/.exec(m[1]) || [, ''])[1];
      open.push(classes.split(/\s+/).filter(Boolean));
    }
  }
  return open;
}

test('the containment walk reads the template the way the browser does', () => {
  /* An anchor for `openAt` itself. The paper's nesting is stated in the template and in the CSS
     both, so if this stops holding the walk is broken rather than the markup. `rb` is the
     wrapping <section>, which the walk now reads: the Edit column is an <aside>, so a walk that
     saw only <div> would have started skipping ancestors.

     `rb-work` in place of `rb-body` is ADR-0128: the two-column body became two workspaces, and
     the paper is inside the Preview one. */
  const inside = openAt(fs.readFileSync(TEMPLATE, 'utf8'), 'id="rb-paper"').flat();
  assert.deepEqual(inside, ['rb', 'rb-work', 'rb-stage', 'rb-paper-wrap'],
    'the walk no longer agrees with how the template nests the paper');
});

/* ---- the split (ADR-0128) ----

   Everything the editor measures sits inside a segment that can be hidden, and a hidden element
   measures zero. Which segment each of the two measured things is in is therefore a contract, not
   a detail: it is what decides which one is wrong on the way in and has to be painted again. The
   DOM stub in resume_editor.test.js knows nothing about nesting, so only this file can say it. */

test('the paper is inside the Preview segment and the form inside Edit', () => {
  const html = fs.readFileSync(TEMPLATE, 'utf8');
  const at = id => openAt(html, 'id="' + id + '"').flat();
  assert.ok(at('rb-paper').includes('rb-work'), 'the paper is not inside a workspace at all');
  assert.ok(at('rb-mini-sheet').includes('rb-aside'),
    'the miniature is not in the column beside the form, so nothing softens the lost live preview');
  assert.ok(at('rb-pane-checks').includes('rb-aside'),
    'Checks is not beside the form, so a finding names a block in the segment you are not in');

  /* Both measured sheets, each in its own segment: the paper in Preview, the miniature in Edit.
     If they ever land in the same one, `measureShown` is repainting for a reason that no longer
     exists — and the other segment is showing something nobody measured. */
  const panes = html.replace(/\{#[\s\S]*?#\}/g, '');
  const editAt = panes.indexOf('id="rb-pane-edit"');
  const previewAt = panes.indexOf('id="rb-pane-preview"');
  assert.ok(editAt > -1 && previewAt > editAt, 'the two workspaces are not both present, in order');
  assert.ok(panes.indexOf('id="rb-mini-sheet"') > editAt &&
    panes.indexOf('id="rb-mini-sheet"') < previewAt, 'the miniature is not in the Edit segment');
  assert.ok(panes.indexOf('id="rb-paper"') > previewAt, 'the paper is not in the Preview segment');

  /* Keywords is a MEASURING panel — it asks how far down the rendered sheet each term falls — so
     it has to sit in the segment that has the sheet on screen. Measured on a three-page résumé: a
     block 18.91in down the document reports 0in from the top while the paper is hidden, which
     scores every term as "near the top" whatever the truth is. */
  assert.ok(panes.indexOf('id="rb-pane-keywords"') > previewAt,
    'Keywords is not in the Preview segment, so it measures a sheet that is not on screen');
});

test('Edit is the segment the tab opens on, and Preview ships hidden', () => {
  const html = fs.readFileSync(TEMPLATE, 'utf8').replace(/\{#[\s\S]*?#\}/g, '');
  /* The static markup IS the initial state — the skeleton is legible before the scripts run, and
     the editor only ever moves the selection from where the template put it. A template that
     shipped both panels visible would draw the page under the form until the first click. */
  const edit = /<div class="rb-work" id="rb-pane-edit"[^>]*>/.exec(html);
  const preview = /<div class="rb-work" id="rb-pane-preview"[^>]*>/.exec(html);
  assert.ok(edit && preview, 'one of the two workspaces is no longer a .rb-work with that id');
  assert.ok(!/\bhidden\b/.test(edit[0]), 'Edit ships hidden, so the tab opens on nothing');
  assert.ok(/\bhidden\b/.test(preview[0]), 'Preview ships visible, so both workspaces draw at once');
  assert.match(html, /id="rb-seg-edit"[^>]*aria-selected="true"/,
    'the strip does not agree with the panels about which segment is open');
});

/* ---- the popovers ----

   `.rb-pop` is `position: absolute; top: 100%`, which reads as "just below the bar" only while
   the bar is its nearest positioned ancestor. As siblings of `.rb-bar` they resolved against the
   page shell instead: the Download menu opened 1,004px below its own button, off the bottom of a
   900px viewport, and the only sign the button had worked was the page growing a scrollbar. */

for (const id of ['rb-pop-open', 'rb-pop-download']) {
  test(`the ${id} popover is inside the bar it opens under`, () => {
    const open = openAt(fs.readFileSync(TEMPLATE, 'utf8'), 'id="' + id + '"');
    assert.ok(open.some(classes => classes.includes('rb-bar')),
      `${id} is not inside .rb-bar, so its top: 100% is measured against something else`);
  });
}

test('the bar is still the positioned ancestor the popovers are written against', () => {
  const bar = /\.rb-bar\s*\{[^}]*\}/.exec(fs.readFileSync(CSS, 'utf8'));
  assert.ok(bar, 'the .rb-bar rule is gone');
  assert.match(bar[0], /position:\s*relative/,
    'the popovers are nested inside .rb-bar for its position: relative, which has been removed');
});

/* ---- the print safety net ----

   Printing the tab is not how the PDF is made — `resume_export.js` prints an iframe holding only
   the document — but the stylesheet has always carried a safety net for anyone who reaches for
   Ctrl+P anyway, and the split broke it: the sheet now lives in a workspace that is `hidden`
   whenever Edit is the segment on screen, which is the default. Measured in Chromium: printing
   from Edit produced an **860-byte, one-page blank** PDF against the 88,968-byte two-page document
   printing from Preview produced. With the rule, both segments print byte-identical PDFs. */

test('the print rules bring the hidden workspace back, or Ctrl+P prints a blank page', () => {
  const css = fs.readFileSync(CSS, 'utf8');
  const block = /@media print \{([\s\S]*?)\n\}/.exec(css);
  assert.ok(block, 'the @media print block is gone');
  assert.match(block[1], /\.rb-work\[hidden\]\s*\{[^}]*display:\s*block\s*!important/,
    'the Preview workspace stays hidden in print, so printing from Edit gets a blank sheet');
  /* And the wrapper's screen-only caps go with it: it carries an inline height from the last
     paint, scaled to the zoom, and a max-height sized to the viewport. */
  assert.match(block[1], /\.rb-paper-wrap\s*\{[^}]*height:\s*auto\s*!important/);
  assert.match(block[1], /\.rb-paper-wrap\s*\{[^}]*max-height:\s*none\s*!important/);
});
