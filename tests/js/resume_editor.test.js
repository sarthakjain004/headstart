/* The Résumé tab's editor, run against the REAL
 * src/headstart/ui/static/resume/resume_editor.js — same harness shape as app_search.test.js:
 * it is a browser script, evaluated in a vm context over a stub DOM, so these tests exercise the
 * shipped file rather than a copy of its logic.
 *
 * This file exists because the editor was the one module here with no tests at all, and it is
 * where every serious defect a browser pass found actually lived. The DOM stub is deliberately
 * shallow — element identity, attributes, listeners and innerHTML, and nothing that measures —
 * because everything the editor decides is decided from those four. What needs real geometry
 * (page breaks, drag targets, pixels per inch) is measured in a real browser instead, and is
 * not attempted here.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { DIR, ALL } = require('./resume_harness.js');

const TABS = ['content', 'design', 'checks', 'keywords'];

/** One stub element. Listeners are RECORDED and replayed by `fire`, which is the only way to
 *  test that a control is wired to the right thing — a no-op addEventListener would make the
 *  whole of `wire()` untestable. */
function fakeEl(id) {
  const classes = new Set();
  const handlers = {};
  const el = {
    id: id || '', innerHTML: '', textContent: '', value: '', hidden: false, disabled: false,
    checked: false, tabIndex: 0, dataset: {}, children: [], _attrs: {},
    style: { setProperty(k, v) { this[k] = String(v); }, removeProperty(k) { delete this[k]; } },
    offsetWidth: 816, offsetHeight: 1056, offsetTop: 0, clientWidth: 900,
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 816, height: 1056, bottom: 1056, right: 816 }),
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild(child) { el.children.push(child); return child; },
    remove() {},
    focus() { el._focused = true; },
    setAttribute(k, v) { el._attrs[k] = String(v); },
    getAttribute(k) { return k in el._attrs ? el._attrs[k] : null; },
    closest(sel) { return matches(el, sel) ? el : null; },
    _inside: new Set(),
    contains(node) { return node === el || el._inside.has(node); },
    matches(sel) { return matches(el, sel); },
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    fire(type, event) { (handlers[type] || []).forEach(fn => fn.call(el, event || { type })); },
    listeners: type => (handlers[type] || []).length,
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle(c, on) { const want = on === undefined ? !classes.has(c) : !!on;
        if (want) classes.add(c); else classes.delete(c); return want; } },
  };
  el.parentElement = el.parentElement || null;
  return el;
}

/* Only the selector shapes the editor actually uses. Anything else is a test bug, not a
   feature request, so it throws rather than quietly answering "no". */
function matches(el, sel) {
  if (sel === '[data-pane]') return el.dataset.pane != null;
  if (sel === '[data-select]') return el.dataset.select != null;
  if (sel === '[data-add]') return el.dataset.add != null;
  if (sel === '[data-act]') return el.dataset.act != null;
  if (sel === '[data-fmt]') return el.dataset.fmt != null;
  if (sel === '[data-open]') return el.dataset.open != null;
  if (sel === '[data-drop]') return el.dataset.drop != null;
  if (sel === 'input:not([type="checkbox"]), textarea, select') return !!el._caret;
  if (sel === '.rb-h') return el.dataset.handle != null;
  if (sel === '[data-node]') return el.dataset.node != null;
  throw new Error('the DOM stub was asked about an unknown selector: ' + sel);
}

/** A fresh editor over a fresh DOM. Returns the vm context plus the element table, so a test
 *  can read what was painted and fire what was wired. */
function loadEditor(options) {
  const opts = options || {};
  const nodes = {};
  const get = id => (nodes[id] ||= fakeEl(id));
  const rail = fakeEl('rail');
  const tabs = get('rb-rail-tabs');
  tabs.children = TABS.map(name => {
    const b = fakeEl('rb-tab-' + name);
    b.dataset.pane = name;
    return b;
  });
  // The paper sits inside a scrolling wrapper; `paint` reads the wrapper to reserve height.
  get('rb-paper').parentElement = fakeEl('rb-paper-wrap');

  const ctx = {
    console, setTimeout, clearTimeout, Date, Math, JSON, Set, Map, Object, Array, String, Number,
    isFinite, RegExp,
    document: {
      getElementById: id => (id in nodes ? nodes[id] : (opts.missing || []).includes(id) ? null : get(id)),
      createElement: tag => fakeEl(tag),
      querySelector: sel => (sel === '.rb-rail' ? rail : null),
      querySelectorAll: () => [],
      addEventListener(type, fn) { (ctx._docHandlers[type] ||= []).push(fn); },
      head: fakeEl('head'), body: fakeEl('body'),
      activeElement: null,
      visibilityState: 'visible',
    },
    window: {
      addEventListener() {},
      /* Null by default -> MemoryRepository, so no test touches a real store. A test that needs
         the localStorage path — "a résumé that was already in this browser" — passes one in. */
      localStorage: opts.storage || null,
      prompt: () => (opts.prompt === undefined ? 'A version' : opts.prompt),
      confirm: () => !!opts.confirm,
      alert: () => {},
    },
    _docHandlers: {},
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  for (const name of ALL.concat(['resume_editor'])) {
    const file = path.join(DIR, name + '.js');
    vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
  }
  ctx.ResumeEditor.boot();
  get('rb-paper')._docKeydown = ctx._docHandlers.keydown || [];
  return { ctx, nodes, rail, tabs, el: get };
}

/** A localStorage stand-in, pre-loaded with documents. `ResumeRepository.detect` probes it with
 *  a real write, so this has to behave rather than merely exist. */
function fakeStorage(docs) {
  const map = new Map();
  const s = {
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: k => map.delete(k),
  };
  const index = (docs || []).map(d => ({ id: d.id, name: d.name, layoutId: d.layoutId, updatedAt: d.updatedAt }));
  if (index.length) {
    s.setItem('headstart.resumes.index', JSON.stringify(index));
    s.setItem('headstart.resumes.last', index[0].id);
    for (const d of docs) s.setItem('headstart.resume.' + d.id, JSON.stringify(d));
  }
  return s;
}

/** A hand-built event target: the rail's listeners are delegated, so what they receive is an
 *  element carrying a data attribute and nothing else. */
function target(dataset, extra) {
  const el = fakeEl('');
  Object.assign(el.dataset, dataset);
  return Object.assign(el, extra || {});
}

/** Replay a document-level keydown. The editor binds its shortcuts on `document`, and gates
 *  them on the Résumé panel being the one on screen. */
function fireKey(el, event) {
  el('rb-paper')._docKeydown.forEach(fn => fn(Object.assign({ preventDefault() {} }, event)));
}

/** Put `node` inside `pane` and give it the focus, the way a browser does after a click or a
 *  keystroke. `caret` says whether it is a field with a text cursor in it or merely a button. */
function focusInside(ctx, pane, node, caret) {
  pane._inside.add(node);
  node._caret = !!caret;
  ctx.document.activeElement = node;
  return node;
}

/* ---- booting ---- */

test('booting opens a document and paints the page and the rail from it', () => {
  const { ctx, el } = loadEditor();
  const doc = ctx.ResumeEditor.current();
  assert.ok(doc && doc.root, 'no document was opened');
  assert.equal(doc.layoutId, 'headless-headhunter', 'the tab opens on the template it ships');
  assert.ok(el('rb-paper').innerHTML.includes('data-node='), 'the page was not rendered');
  assert.ok(el('rb-pane-content').innerHTML.includes('Outline'), 'the rail was not painted');
});

/* ---- the rail's repaint guard ----------------------------------------------------------
   The pane the user is typing in must not be rebuilt under the caret; every other pane must be
   rebuilt on every change. Getting that distinction wrong in either direction is a P0, and it
   has been wrong in both: first the whole rail froze on any change from the rail, so the Checks
   panel sat on a stale list while the badge beside it counted the new one; then the guard fired
   on any focused descendant, so clicking a button in the pane froze the pane that button lives
   in — which is the primary editing loop. */

test('a focused BUTTON does not freeze the pane it lives in', () => {
  const { ctx, el, rail } = loadEditor();
  const bullets = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet');
  const pane = el('rb-pane-content');

  ctx.ResumeEditor.select(bullets[0].id);
  assert.ok(pane.innerHTML.includes('data-node="' + bullets[0].id + '"'), 'the first block is shown');

  /* An Outline row: a button, inside the pane, still focused after its own click — exactly
     where the browser leaves focus. */
  const row = target({ select: bullets[1].id });
  focusInside(ctx, pane, row, false);
  rail.fire('click', { target: row });

  assert.ok(pane.innerHTML.includes('data-node="' + bullets[1].id + '"'),
    'the pane stayed on the previous selection: selecting a block showed nothing beside it');
});

test('a focused TEXT FIELD does freeze its own pane, and only its own', () => {
  const { ctx, el, rail } = loadEditor();
  /* The SECOND bullet: the first is a job's opening summary, which two rules exempt, so a
     change to it moves no finding and the "every other pane repaints" half of this test would
     pass whatever the guard did. */
  const bullet = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet')[1];
  const pane = el('rb-pane-content');
  const checks = el('rb-pane-checks');

  ctx.ResumeEditor.select(bullet.id);
  const before = pane.innerHTML;
  const checksBefore = checks.innerHTML;

  const field = target({ node: bullet.id, field: 'text' },
    { type: 'textarea', value: 'Manage the till' });
  focusInside(ctx, pane, field, true);
  rail.fire('input', { target: field });

  assert.equal(pane.innerHTML, before,
    'the pane was rebuilt under the caret, which loses the caret and its position');
  assert.equal(ctx.ResumeDocument.contentOf(ctx.ResumeEditor.current(), bullet.id).text,
    'Manage the till', 'the keystroke still reached the document');
  assert.notEqual(checks.innerHTML, checksBefore,
    'every OTHER pane must still repaint — a frozen Checks panel is how this was wrong before');
});

test('the caret guard yields the moment the pane would show something else', () => {
  const { ctx, el, rail } = loadEditor();
  const nodes = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current());
  const bullet = nodes.filter(n => n.type === 'bullet')[1];
  const other = nodes.filter(n => n.type === 'work_entry')[0];
  const pane = el('rb-pane-content');

  /* Type in one block's field, leaving the caret in the pane... */
  ctx.ResumeEditor.select(bullet.id);
  const field = target({ node: bullet.id, field: 'text' },
    { type: 'textarea', value: 'Ran the till' });
  focusInside(ctx, pane, field, true);
  rail.fire('input', { target: field });
  const frozen = pane.innerHTML;

  /* ...then select a different block WITHOUT the caret moving first. The guard protects a caret
     against a rebuild of the same thing; it must not survive a change of subject. Left alone, the
     pane kept the bullet's single text box while the page showed the job selected, and the box was
     still wired to the bullet — so the next keystroke edited a block the user was not looking at. */
  ctx.ResumeEditor.select(other.id);

  assert.notEqual(pane.innerHTML, frozen, 'the pane stayed on the previous block');
  const fields = (pane.innerHTML.match(/data-field="([^"]+)"/g) || []).join(',');
  assert.ok(fields.includes('company'), 'it is not showing the newly selected block’s fields');
  assert.ok(pane.innerHTML.includes(other.id), 'the controls are still wired to the old block');
});

/* ---- selection ---- */

test('clicking a block on the page selects it, and Escape lets it go', () => {
  const { ctx, el } = loadEditor();
  const paper = el('rb-paper');
  const entry = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .find(n => n.type === 'work_entry');

  /* The real path: a pointerdown on the page, not the exported `select`. A press with no handle
     under it selects; the handle is what starts a drag instead. */
  paper.fire('pointerdown', { target: target({ node: entry.id }) });
  assert.ok(el('rb-pane-content').innerHTML.includes('Work entry'),
    'the rail did not follow the click on the page');
  assert.ok(el('rb-pane-content').innerHTML.includes('data-node="' + entry.id + '"'));

  fireKey(el, { key: 'Escape' });
  assert.ok(el('rb-pane-content').innerHTML.includes('Click a block on the page'),
    'Escape left the block selected');
});

test('a click on a block that is not in the document changes nothing', () => {
  const { ctx, el } = loadEditor();
  const before = el('rb-pane-content').innerHTML;
  el('rb-paper').fire('pointerdown', { target: target({ node: 'n-never-existed' }) });
  assert.equal(el('rb-pane-content').innerHTML, before);
});

/* ---- adding, duplicating and deleting through the rail ---- */

test('the rail adds a block, selects what it added, and can add inside a container', () => {
  const { ctx, el, rail } = loadEditor();
  const D = ctx.ResumeDocument;
  const before = ctx.ResumeEditor.current().root.children.length;
  rail.fire('click', { target: target({ add: 'section', into: '' }) });
  const after = ctx.ResumeEditor.current().root.children;
  assert.equal(after.length, before + 1, 'nothing was added to the page');
  assert.equal(after[after.length - 1].type, 'section');
  assert.ok(el('rb-pane-content').innerHTML.includes('data-node="' + after[after.length - 1].id + '"'),
    'the rail must land on the block it just made, or the user has to go and find it');

  const entry = D.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'work_entry');
  const bullets = entry.children.filter(c => c.type === 'bullet').length;
  rail.fire('click', { target: target({ add: 'bullet', into: entry.id }) });
  assert.equal(D.find(ctx.ResumeEditor.current(), entry.id).children.filter(c => c.type === 'bullet').length,
    bullets + 1, 'the bullet did not land inside the job it was added to');
});

test('duplicate copies the selected block, delete removes it and clears the selection', () => {
  const { ctx, el, rail } = loadEditor();
  const D = ctx.ResumeDocument;
  const entry = D.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'work_entry');
  const parent = D.parentOf(ctx.ResumeEditor.current(), entry.id);
  const before = D.find(ctx.ResumeEditor.current(), parent.id).children.length;

  ctx.ResumeEditor.select(entry.id);
  rail.fire('click', { target: target({ act: 'duplicate' }) });
  assert.equal(D.find(ctx.ResumeEditor.current(), parent.id).children.length, before + 1);

  ctx.ResumeEditor.select(entry.id);
  rail.fire('click', { target: target({ act: 'remove' }) });
  assert.equal(D.find(ctx.ResumeEditor.current(), entry.id), null, 'the block is still there');
  assert.ok(el('rb-pane-content').innerHTML.includes('Click a block on the page'),
    'the rail kept offering Delete and Duplicate for a block that no longer exists');
});

test('an action with nothing selected does nothing at all', () => {
  const { ctx, rail } = loadEditor();
  const before = JSON.stringify(ctx.ResumeEditor.current().root);
  for (const act of ['remove', 'duplicate', 'unfork', 'hide']) {
    rail.fire('click', { target: target({ act }) });
  }
  assert.equal(JSON.stringify(ctx.ResumeEditor.current().root), before);
});

/* ---- keyword coverage -------------------------------------------------------------------
   The guide's own headline instruction: get three quarters of the job title's qualifications
   into the FIRST HALF OF THE FIRST PAGE. "First half of the first page" is measured on the
   rendered page in inches — an earlier version measured it as a share of the whole text and
   scored a hit halfway down page three as "near the top", which is the opposite of the answer. */

/** Give the paper the geometry the coverage check reads: an origin, and blocks at known heights
 *  down the page. 96px to the inch, so 4.5in — half of an 11in page less its two 1in margins —
 *  is 432px. */
function layPaperOut(el, blocks) {
  const paper = el('rb-paper');
  const origin = fakeEl('doc');
  origin.getBoundingClientRect = () => ({ top: 0, height: 1056 });
  paper.querySelector = sel => (sel === '.rb-doc' ? origin : null);
  paper.querySelectorAll = sel => (sel !== '[data-node]' ? [] : blocks.map(b => {
    const node = fakeEl('');
    node.textContent = b.text;
    node.getBoundingClientRect = () => ({ top: b.top, height: 20 });
    return node;
  }));
}

test('keyword coverage counts what the résumé says, and where on the page it says it', () => {
  const { ctx, el, rail } = loadEditor();
  /* An empty sheet, so the only words on the page are the two this test writes — the tab opens
     on the guide's worked example, whose own words would decide where the halfway mark falls. */
  el('rb-new').fire('click');
  const bullets = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet');
  const write = (node, text) => rail.fire('input',
    { target: target({ node: node.id, field: 'text' }, { type: 'textarea', value: text }) });

  write(bullets[0], 'Operated the Point of Sale by counting cash');
  write(bullets[1], 'Ran a Kubernetes cluster to serve the shop app');

  /* The page deliberately disagrees with the text. Both terms sit in the top half of page one
     (4.5in = 432px), but the second one is in the back half of the document's WORDS — which is
     what an earlier version of this measured, and why it once scored a hit halfway down page
     three as "near the top". If the two ever agree, this test cannot tell them apart. */
  layPaperOut(el, [
    { text: 'operated the point of sale by counting cash', top: 100 },    // 1.04in
    { text: 'ran a kubernetes cluster to serve the shop app', top: 200 }, // 2.08in
  ]);
  const words = ctx.ResumeExport.plainText(ctx.ResumeEditor.current()).toLowerCase();
  assert.ok(words.indexOf('kubernetes') > words.length / 2,
    'the fixture no longer separates "far down the page" from "far through the text"');

  el('rb-kw').value = 'Point of Sale\nKubernetes\nForklift';
  ctx.ResumeEditor.keywordCheck();

  const summary = el('rb-kw-summary').textContent;
  assert.match(summary, /^2 of 3 present/, 'wrong count of terms the résumé actually contains');
  assert.match(summary, /67% in the top half of page one/,
    'the depth of a hit must be measured on the page, in inches, not as a share of the text');

  const rows = el('rb-kw-out').innerHTML.split('rb-kwrow');
  assert.ok(rows[1].includes('top half, page one'), 'the first hit was not marked early');
  assert.ok(rows[2].includes('top half, page one'), 'a hit high on the page was called late');
  assert.ok(rows[3].includes('missing'), 'the absent term was not marked');

  /* And a term genuinely down on page two reads as present, further down. */
  layPaperOut(el, [{ text: 'ran a kubernetes cluster to serve the shop app', top: 700 }]);
  el('rb-kw').value = 'Kubernetes';
  ctx.ResumeEditor.keywordCheck();
  assert.match(el('rb-kw-summary').textContent, /^1 of 1 present · 0%/);
  assert.ok(el('rb-kw-out').innerHTML.includes('present, further down'));
});

test('an empty keyword box clears the panel rather than dividing by nothing', () => {
  const { ctx, el } = loadEditor();
  el('rb-kw').value = '  \n , ; \n ';
  ctx.ResumeEditor.keywordCheck();
  assert.equal(el('rb-kw-summary').textContent, '');
  assert.equal(el('rb-kw-out').innerHTML, '');
});

/* ---- the download menu ------------------------------------------------------------------
   Every format gets the version on screen — that is the document the employer will read — with
   ONE exception: the JSON backup is the whole thing, every version and every variant, because
   it is the only copy that exists off this browser. Getting that backwards either sends the
   master to an employer you tailored for, or hands someone a "backup" missing every version
   they wrote. */

/** Swap the export functions for recorders. They are looked up on `ResumeExport` at click time,
 *  so this is the module seam, not a reach inside the editor. */
function recordExports(ctx) {
  const calls = [];
  for (const name of ['print', 'asWord', 'asText', 'asHtml', 'asJson']) {
    ctx.ResumeExport[name] = (win, payload) => calls.push({ name, payload });
  }
  return calls;
}

test('the download menu sends the version on screen — except the JSON backup', () => {
  const { ctx, el, rail } = loadEditor();
  const D = ctx.ResumeDocument;
  const bullet = D.flatten(ctx.ResumeEditor.current()).filter(n => n.type === 'bullet')[1];
  const write = text => rail.fire('input',
    { target: target({ node: bullet.id, field: 'text' }, { type: 'textarea', value: text }) });

  write('Gave customers correct change by adding cash');
  const masterName = ctx.ResumeEditor.current().name;

  el('rb-version-new').fire('click');                 // window.prompt answers "A version"
  assert.equal((ctx.ResumeEditor.current().tailorings || []).length, 1, 'no version was created');
  write('Tailored for Acme');

  const calls = recordExports(ctx);
  el('rb-pop-download').fire('click', { target: target({ fmt: 'pdf' }) });
  el('rb-pop-download').fire('click', { target: target({ fmt: 'json' }) });
  assert.deepEqual(calls.map(c => c.name), ['print', 'asJson']);

  const printed = calls[0].payload;
  assert.equal(printed.content[bullet.id].text, 'Tailored for Acme',
    'the PDF carried the master’s words while a version was on screen');
  assert.equal(printed.name, masterName + ' — A version',
    'three tailored PDFs under one filename is how the wrong one gets sent');

  const backup = calls[1].payload;
  assert.equal(backup.content[bullet.id].text, 'Gave customers correct change by adding cash',
    'the backup was resolved, so it no longer holds the master’s own words');
  assert.equal(backup.name, masterName);
  assert.equal(backup.tailorings.length, 1, 'the backup lost the version it was meant to keep');
});

test('a version that leaves a block out prints without it, and backs up with it', () => {
  const { ctx, el, rail } = loadEditor();
  const D = ctx.ResumeDocument;
  const bullet = D.flatten(ctx.ResumeEditor.current()).filter(n => n.type === 'bullet')[1];

  el('rb-version-new').fire('click');
  ctx.ResumeEditor.select(bullet.id);
  rail.fire('click', { target: target({ act: 'hide' }) });

  const calls = recordExports(ctx);
  el('rb-pop-download').fire('click', { target: target({ fmt: 'text' }) });
  el('rb-pop-download').fire('click', { target: target({ fmt: 'json' }) });
  assert.equal(D.find(calls[0].payload, bullet.id), null, 'the left-out block was still printed');
  assert.ok(D.find(calls[1].payload, bullet.id), 'the backup dropped a block the résumé still has');
});

test('a format the menu does not offer is ignored rather than throwing', () => {
  const { ctx, el } = loadEditor();
  const calls = recordExports(ctx);
  el('rb-pop-download').fire('click', { target: target({ fmt: 'ps' }) });
  el('rb-pop-download').fire('click', { target: fakeEl('') });   // a click on the menu's padding
  assert.deepEqual(calls, []);
});

/* ---- the tab strip ---- */

test('the rail strip keeps one tab stop and moves the selection with the arrows', () => {
  const { ctx, el, tabs } = loadEditor();
  const state = () => tabs.children.map(b => b.getAttribute('aria-selected') + '/' + b.tabIndex);

  tabs.fire('click', { target: tabs.children[2] });   // Checks
  assert.deepEqual(state(), ['false/-1', 'false/-1', 'true/0', 'false/-1'],
    'a tablist is ONE tab stop; four tabIndex-0 buttons is four');
  assert.deepEqual(TABS.filter(n => !el('rb-pane-' + n).hidden), ['checks']);

  ctx.document.activeElement = tabs.children[2];
  tabs.fire('keydown', { key: 'ArrowRight', preventDefault() {} });
  assert.equal(tabs.children[3].getAttribute('aria-selected'), 'true');
  assert.ok(tabs.children[3]._focused, 'the arrow moved the selection but not the focus');
  assert.deepEqual(TABS.filter(n => !el('rb-pane-' + n).hidden), ['keywords'],
    'the panel must follow the selection, as it does for a mouse');

  /* Wrapping, and Home/End, are what stops the last tab being a dead end. */
  ctx.document.activeElement = tabs.children[3];
  tabs.fire('keydown', { key: 'ArrowRight', preventDefault() {} });
  assert.equal(tabs.children[0].getAttribute('aria-selected'), 'true');
  ctx.document.activeElement = tabs.children[0];
  tabs.fire('keydown', { key: 'End', preventDefault() {} });
  assert.equal(tabs.children[3].getAttribute('aria-selected'), 'true');
});

test('a keypress on the strip that is not a traversal is left alone', () => {
  const { ctx, el, tabs } = loadEditor();
  tabs.fire('click', { target: tabs.children[1] });
  const before = tabs.children.map(b => b.getAttribute('aria-selected'));
  ctx.document.activeElement = tabs.children[1];
  tabs.fire('keydown', { key: 'a', preventDefault() { throw new Error('swallowed a keystroke'); } });
  assert.deepEqual(tabs.children.map(b => b.getAttribute('aria-selected')), before);
  assert.deepEqual(TABS.filter(n => !el('rb-pane-' + n).hidden), ['design']);

  /* A keydown that reaches the strip from somewhere else — the panel below it, say — must not
     move the selection either. */
  ctx.document.activeElement = el('rb-pane-design');
  tabs.fire('keydown', { key: 'ArrowRight', preventDefault() { throw new Error('claimed a key it does not own'); } });
  assert.deepEqual(tabs.children.map(b => b.getAttribute('aria-selected')), before);
});

/* ---- what the Checks tab says out loud ---- */

test('the Checks verdict is announced when it changes, and not otherwise', () => {
  const { ctx, el, rail } = loadEditor();
  const live = el('rb-live');
  assert.equal(live.textContent, 'Checks: nothing to flag.',
    'the verdict was never announced at all');
  assert.match(el('rb-badge').innerHTML, /to fix<\/span>/,
    'the badge reads as a bare number to a screen reader');

  /* Renaming the résumé changes nothing a rule looks at. A live region that re-reads the whole
     panel on every keystroke is worse than one that says nothing. */
  live.textContent = 'CLEARED';
  el('rb-name').fire('input', { target: { value: 'Another name' } });
  assert.equal(live.textContent, 'CLEARED', 'an unchanged verdict was announced again');

  /* Breaking a rule is news, and it is news a screen reader gets no other way: the badge and
     the findings list are both silent on their own. */
  const bullet = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet')[1];
  rail.fire('input', { target: target({ node: bullet.id, field: 'text' },
    { type: 'textarea', value: 'Manage the till. And a second sentence.' }) });
  assert.match(live.textContent, /^Checks: [1-9]\d* to fix/, 'a changed verdict went unannounced');
});

/* ---- the rail's own escaping -------------------------------------------------------------
   `importJson` rewrites hostile identifiers at the boundary, and its own tests cover that. This
   is the second lock, on the sinks: a document can reach the rail WITHOUT passing the import —
   one already sitting in localStorage from before that fix shipped does exactly that — so the
   rail has to escape what it is handed rather than trust where it came from. */

test('a hostile node id cannot break out of the attributes the rail writes', () => {
  let { ctx, el, rail } = loadEditor();
  const doc = ctx.ResumeDocument.clone(ctx.ResumeEditor.current());
  const nasty = 'x" onmouseover="alert(1)" data-x="';
  const entry = ctx.ResumeDocument.flatten(doc).find(n => n.type === 'work_entry');
  doc.content[nasty] = doc.content[entry.id];
  entry.id = nasty;

  /* Re-opened from this browser's own storage, which is how a document written before that
     import fix shipped arrives: no import, so nothing has had a chance to rewrite the id. */
  const second = loadEditor({ storage: fakeStorage([doc]) });
  second.ctx.ResumeEditor.select(nasty);
  el = second.el; rail = second.rail; ctx = second.ctx;
  assert.equal(ctx.ResumeDocument.find(ctx.ResumeEditor.current(), nasty).type, 'work_entry',
    'the seeded document did not come back out of storage');

  /* The escaped form still CONTAINS the word `onmouseover` — as text inside an attribute value,
     which is inert. What must not appear is the unescaped quote that would end the attribute and
     start a new one, so that is what is asserted. */
  for (const html of [el('rb-pane-content').innerHTML, el('rb-paper').innerHTML]) {
    assert.ok(!html.includes('" onmouseover="'), 'the id closed its attribute and opened another');
    assert.ok(html.includes('&quot; onmouseover=&quot;'), 'the id is not in this markup at all, so this test proves nothing');
  }

  /* And the round trip still works: the escaped attribute is only how it is written, so the
     rail must still be able to edit the block it names. */
  rail.fire('input', { target: target({ node: nasty, field: 'role' },
    { type: 'text', value: 'Cashier' }) });
  assert.equal(ctx.ResumeDocument.contentOf(ctx.ResumeEditor.current(), nasty).role, 'Cashier');
});

/* ---- the first thing anyone sees ---------------------------------------------------------
   A brand-new sheet breaks almost every rule this template states — no name, no phone, no dates
   — so the tab used to open on an empty page with a red badge counting five faults in a
   document the user had not started writing. The rules are advice about writing; there is
   nothing to advise about yet. */

test('a genuinely first visit opens the guide’s worked example', () => {
  const { ctx, el } = loadEditor();
  const doc = ctx.ResumeEditor.current();
  const header = ctx.ResumeDocument.flatten(doc).find(n => n.type === 'header');
  assert.ok(ctx.ResumeDocument.contentOf(doc, header.id).fullName,
    'the first visit still opens an empty sheet');
  assert.ok(el('rb-paper').innerHTML.includes('Lee Korelitz'), 'the page is blank');
  assert.equal(ctx.ResumeEditor.findings().length, 0,
    'the example must pass its own rules, or the first visit still opens on a red badge');
});

test('a visit that is not the first opens what was there, untouched', () => {
  const first = loadEditor();
  const mine = first.ctx.ResumeDocument.clone(first.ctx.ResumeEditor.current());
  mine.name = 'My own résumé';
  const { ctx } = loadEditor({ storage: fakeStorage([mine]) });
  assert.equal(ctx.ResumeEditor.current().name, 'My own résumé',
    'a returning visitor was handed the example over their own work');
});

test('an empty sheet does not count faults before anything has been typed', () => {
  const { ctx, el, rail } = loadEditor();
  el('rb-new').fire('click');            // window.confirm answers no -> an empty sheet
  assert.equal(ctx.ResumeEditor.current().name, 'Untitled résumé');

  assert.ok(el('rb-badge').hidden, 'a red badge on a document nobody has started');
  assert.ok(el('rb-pane-checks').innerHTML.includes('when you start writing'),
    'the panel listed faults in an empty page instead of saying it is waiting');

  /* And the moment there are words, the checks are back — this must not be a way to make the
     rule panel go quiet. */
  const header = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'header');
  rail.fire('input', { target: target({ node: header.id, field: 'fullName' },
    { type: 'text', value: 'Lee' }) });
  assert.ok(!el('rb-badge').hidden, 'the checks never came back');
  assert.ok(el('rb-pane-checks').innerHTML.includes('rb-finding'));
});

/* ---- paper size ---- */

test('choosing A4 re-lays the page, and it survives a reload', () => {
  const { ctx, el } = loadEditor();
  assert.equal(el('rb-paper').style.width, '8.5in', 'the tab did not open on the layout’s sheet');
  assert.deepEqual(ctx.ResumeLayouts.PAPERS.map(p => p.id), ['letter', 'a4'],
    'the picker offers whatever Layer 2 declares; this test names what it expects to find');

  el('rb-paper-size').fire('change', { target: { value: 'a4' } });
  assert.equal(el('rb-paper').style.width, '8.27in', 'the sheet on screen is still US Letter');
  assert.equal(el('rb-paper').style.minHeight, '11.69in');
  assert.equal(el('rb-paper-size').value, 'a4', 'the control does not show what is in force');

  /* It is the document's, not the session's — so it comes back with the document. */
  ctx.ResumeEditor.flush();
  const saved = ctx.ResumeDocument.clone(ctx.ResumeEditor.current());
  const back = loadEditor({ storage: fakeStorage([saved]) });
  assert.equal(back.el('rb-paper').style.width, '8.27in', 'the sheet was a session setting, not the résumé’s');
});
