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
 *  It reads `<div>` only, which is every element this template nests, and it is a walk over one
 *  known file rather than a parser. If the template grows nesting through another element, this
 *  starts answering about the wrong ancestors — so the anchor test below pins the walk itself. */
function openAt(html, needle) {
  const source = html.replace(/\{#[\s\S]*?#\}/g, '');
  const at = source.indexOf(needle);
  assert.notEqual(at, -1, 'the template no longer contains ' + needle);
  const open = [];
  const tag = /<div\b([^>]*)>|<\/div>/g;
  let m;
  while ((m = tag.exec(source)) && m.index < at) {
    if (m.index + m[0].length > at) break;   // the needle's own tag: a div does not contain itself
    if (m[0] === '</div>') {
      assert.ok(open.length, 'the template closes a <div> that was never opened — this walk is lost');
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
     both, so if this stops holding the walk is broken rather than the markup. `.rb` is absent on
     purpose: it is the wrapping <section>, and this reads <div> only. */
  const inside = openAt(fs.readFileSync(TEMPLATE, 'utf8'), 'id="rb-paper"').flat();
  assert.deepEqual(inside, ['rb-body', 'rb-stage', 'rb-paper-wrap'],
    'the walk no longer agrees with how the template nests the paper');
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
