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

/* ONE strip: Edit and Preview (ADR-0128). The four panes are still four panes — they are simply
   permanently visible inside whichever segment they belong to, so there is no second tablist and
   nothing left to name them by. */
const SEGMENTS = ['edit', 'preview'];

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
  if (sel === '[data-seg]') return el.dataset.seg != null;
  if (sel === '[data-show]') return el.dataset.show != null;
  if (sel === '[data-select]') return el.dataset.select != null;
  if (sel === '[data-add]') return el.dataset.add != null;
  if (sel === '[data-act]') return el.dataset.act != null;
  if (sel === '[data-row]') return el.dataset.row != null;
  if (sel === '[data-layout]') return el.dataset.layout != null;
  if (sel === '[data-act="templates"]') return el.dataset.act === 'templates';
  if (sel === '[data-fmt]') return el.dataset.fmt != null;
  if (sel === '[data-open]') return el.dataset.open != null;
  if (sel === '[data-drop]') return el.dataset.drop != null;
  if (sel === '[data-drop-yes]') return el.dataset.dropYes != null;
  if (sel === '[data-drop-no]') return el.dataset.dropNo != null;
  if (sel === '[data-pull]') return el.dataset.pull != null;
  if (sel === 'input:not([type="checkbox"]), textarea, select') return !!el._caret;
  if (sel === '.rb-h') return el.dataset.handle != null;
  if (sel === '[data-node]') return el.dataset.node != null;
  /* The popover dismissal asks "is this pointerdown inside the menu or on its own button", by
     id. The stub answers about the element itself, which is all these tests fire at. */
  if (sel.charAt(0) === '#') return el.id === sel.slice(1);
  throw new Error('the DOM stub was asked about an unknown selector: ' + sel);
}

/** A fresh editor over a fresh DOM. Returns the vm context plus the element table, so a test
 *  can read what was painted and fire what was wired. */
function loadEditor(options) {
  const opts = options || {};
  const nodes = {};
  const get = id => (nodes[id] ||= fakeEl(id));
  /* The delegated listeners are bound to the whole tab, not to a rail: the panes sit in two
     workspaces on opposite sides of the segment switch (ADR-0128). */
  const panel = get('rb');
  /* Seeded the way the TEMPLATE ships it — first tab selected, the rest at tabIndex -1 — because
     that is where the initial state genuinely lives: the strips are static markup so the panel is
     legible before the scripts run, and the editor only ever moves the selection from there. A
     stub that started every tab blank would let "the tab opens on Edit" pass by accident. */
  const segs = get('rb-seg');
  segs.children = SEGMENTS.map((name, i) => {
    const b = fakeEl('rb-seg-' + name);
    b.dataset.seg = name;
    b.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
    b.tabIndex = i === 0 ? 0 : -1;
    get('rb-pane-' + name).hidden = i !== 0;
    return b;
  });
  /* Closed, the way the template ships them. Escape asks the popovers before it deselects a
     block, so a stub that started them open made Escape a no-op on the page. */
  get('rb-pop-open').hidden = true;
  get('rb-pop-download').hidden = true;
  get('rb-pop-version').hidden = true;
  /* And the version-delete confirmation, which the template also ships closed. A stub that
     started it open would let "asking before deleting" pass without the editor asking. */
  get('rb-version-confirm').hidden = true;
  /* The paper and the miniature both sit inside a frame their painter reserves height on.
     600px of usable width against an 816px sheet, so `fitToWidth` has a real answer to give —
     at the stub's default 900 it clamps to 1 and every fit is indistinguishable from no fit. */
  get('rb-paper-wrap').clientWidth = 600;
  get('rb-paper').parentElement = get('rb-paper-wrap');
  get('rb-mini-sheet').parentElement = fakeEl('rb-mini-frame');

  const ctx = {
    console, setTimeout, clearTimeout, Date, Math, JSON, Set, Map, Object, Array, String, Number,
    isFinite, RegExp,
    document: {
      getElementById: id => (id in nodes ? nodes[id] : (opts.missing || []).includes(id) ? null : get(id)),
      createElement: tag => fakeEl(tag),
      querySelector: () => null,
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
      /* The three native dialogs, wired to THROW. Every one of them is gone from the editor —
         they are modal, unstyled, outside the page's reading order, and one of them asked the
         user to type what the app already knew. A test that merely stopped answering them would
         pass on a reintroduction; this one fails loudly. */
      prompt: () => { throw new Error('window.prompt is not a control this tab may use'); },
      confirm: () => { throw new Error('window.confirm is not a control this tab may use'); },
      alert: () => { throw new Error('window.alert is not a control this tab may use'); },
    },
    _docHandlers: {},
  };
  /* An account-configured deployment (ADR-0124). Without this the tab loads exactly as it
     always has — `resume_sync.js` is absent, `rb-account` is not rendered, and the editor holds
     no sync at all — which is the shape every other test in this file exercises and the shape a
     Space with no Subscriptions dataset really serves. */
  const wire = [];
  if (opts.account) {
    ctx.fetch = (url, init) => {
      const method = (init && init.method) || 'GET';
      wire.push({ url, method, body: init && init.body ? JSON.parse(init.body) : null });
      const answer = (opts.account[method + ' ' + url] || opts.account[url] ||
        { status: 200, body: { ok: true, rev: 1 } });
      return Promise.resolve({ status: answer.status, json: () => Promise.resolve(answer.body) });
    };
  }
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  /* The panel's own visibility, which the editor reads as it loads to decide whether the page
     already opened on this tab. Default is visible because every other test here is about a tab
     someone is looking at. */
  get('panel-resume').hidden = !!opts.panelHidden;
  for (const name of ALL.concat(opts.account ? ['resume_sync', 'resume_editor'] : ['resume_editor'])) {
    const file = path.join(DIR, name + '.js');
    vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
  }
  /* `skipBoot` leaves the editor exactly as loading it left it — the only way to see what the
     script did on its own, rather than what this harness then asked it to do. */
  if (!opts.skipBoot) ctx.ResumeEditor.boot();
  get('rb-paper')._docKeydown = ctx._docHandlers.keydown || [];
  get('rb-paper')._docPointer = ctx._docHandlers.pointerdown || [];
  get('rb-paper')._docVisibility = ctx._docHandlers.visibilitychange || [];
  return { ctx, nodes, panel, segs, el: get, wire };
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

/** Make a version, through the controls the page actually offers: the button opens a popover
 *  holding a text field, and the field's own button creates it. */
function newVersion(el, name) {
  el('rb-version-new').fire('click');
  el('rb-version-name').value = name === undefined ? 'A version' : name;
  el('rb-version-create').fire('click');
}

/** A hand-built event target: the tab's listeners are delegated, so what they receive is an
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

test('booting opens a document and paints the page and the form from it', () => {
  const { ctx, el } = loadEditor();
  const doc = ctx.ResumeEditor.current();
  assert.ok(doc && doc.root, 'no document was opened');
  assert.equal(doc.layoutId, 'headless-headhunter', 'the tab opens on the template it ships');
  assert.ok(el('rb-paper').innerHTML.includes('data-node='), 'the page was not rendered');
  assert.ok(el('rb-pane-document').innerHTML.includes('data-row='), 'the form was not painted');
});

test('the worked example a first visit opens is written, not just pointed at', () => {
  const storage = fakeStorage([]);
  const { ctx } = loadEditor({ storage });
  const id = ctx.ResumeEditor.current().id;
  /* `last` used to name a document nothing had saved: the next visit found nothing under that
     id, minted a second example, and showed the "Start here" card again — with the Résumés list
     empty however much had been typed into it. */
  assert.equal(storage.getItem('headstart.resumes.last'), id);
  assert.ok(storage.getItem('headstart.resume.' + id), 'the example was never written');
  assert.deepEqual(JSON.parse(storage.getItem('headstart.resumes.index')).map(r => r.id), [id]);
});

/* ---- the repaint guard ------------------------------------------------------------------
   The pane the user is typing in must not be rebuilt under the caret; every other pane must be
   rebuilt on every change. Getting that distinction wrong in either direction is a P0, and it
   has been wrong in both: first every pane froze on any change from the form, so the Checks
   panel sat on a stale list while the badge beside it counted the new one; then the guard fired
   on any focused descendant, so clicking a button in the pane froze the pane that button lives
   in — which is the primary editing loop. */

test('a focused BUTTON does not freeze the pane it lives in', () => {
  const { ctx, el, panel } = loadEditor();
  const bullets = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet');
  const pane = el('rb-pane-document');

  ctx.ResumeEditor.select(bullets[0].id);
  assert.ok(pane.innerHTML.includes('data-node="' + bullets[0].id + '"'), 'the first block is shown');

  /* A button inside the pane, still focused after its own click — exactly where the browser
     leaves focus. A Checks finding is one; so is every row header in the accordion. */
  const row = target({ select: bullets[1].id });
  focusInside(ctx, pane, row, false);
  panel.fire('click', { target: row });

  assert.ok(pane.innerHTML.includes('data-node="' + bullets[1].id + '"'),
    'the pane stayed on the previous selection: selecting a block showed nothing beside it');
});

test('a focused TEXT FIELD does freeze its own pane, and only its own', () => {
  const { ctx, el, panel } = loadEditor();
  /* The SECOND bullet: the first is a job's opening summary, which two rules exempt, so a
     change to it moves no finding and the "every other pane repaints" half of this test would
     pass whatever the guard did. */
  const bullet = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet')[1];
  const pane = el('rb-pane-document');
  const checks = el('rb-pane-checks');

  ctx.ResumeEditor.select(bullet.id);
  const before = pane.innerHTML;
  const checksBefore = checks.innerHTML;

  const field = target({ node: bullet.id, field: 'text' },
    { type: 'textarea', value: 'Manage the till' });
  focusInside(ctx, pane, field, true);
  panel.fire('input', { target: field });

  assert.equal(pane.innerHTML, before,
    'the pane was rebuilt under the caret, which loses the caret and its position');
  assert.equal(ctx.ResumeDocument.contentOf(ctx.ResumeEditor.current(), bullet.id).text,
    'Manage the till', 'the keystroke still reached the document');
  assert.notEqual(checks.innerHTML, checksBefore,
    'every OTHER pane must still repaint — a frozen Checks panel is how this was wrong before');
});

test('the caret guard yields the moment the pane would show something else', () => {
  const { ctx, el, panel } = loadEditor();
  const nodes = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current());
  const bullet = nodes.filter(n => n.type === 'bullet')[1];
  const other = nodes.filter(n => n.type === 'work_entry')[0];
  const pane = el('rb-pane-document');

  /* Type in one block's field, leaving the caret in the pane... */
  ctx.ResumeEditor.select(bullet.id);
  const field = target({ node: bullet.id, field: 'text' },
    { type: 'textarea', value: 'Ran the till' });
  focusInside(ctx, pane, field, true);
  panel.fire('input', { target: field });
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
  assert.ok(el('rb-pane-document').innerHTML.includes('Work entry'),
    'the form did not follow the click on the page');
  assert.ok(el('rb-pane-document').innerHTML.includes('data-node="' + entry.id + '"'));

  assert.ok(el('rb-pane-document').innerHTML.includes(' on"'), 'the row is not marked selected');
  fireKey(el, { key: 'Escape' });
  assert.ok(!el('rb-pane-document').innerHTML.includes(' on"'), 'Escape left the block selected');
  assert.ok(el('rb-pane-document').innerHTML.includes('data-node="' + entry.id + '"'),
    'Escape also collapsed the row, which would throw away where the user had navigated to');
});

test('a click on the page does not move the user out of the segment they are reading', () => {
  const { ctx, el, segs } = loadEditor();
  const paper = el('rb-paper');
  const entry = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .find(n => n.type === 'work_entry');
  const seg = name => segs.children.find(b => b.dataset.seg === name).getAttribute('aria-selected');

  el('rb-seg').fire('click', { target: target({ seg: 'preview' }) });
  assert.equal(seg('preview'), 'true', 'the test did not reach Preview');

  /* Clicking a block to look at it used to switch the segment underneath the reader — the
     auto-switching ADR-0128 rejected, arriving through the page instead of the form. The block
     is still selected and the form still opened down to it; only the segment is left alone. */
  paper.fire('pointerdown', { target: target({ node: entry.id }) });
  assert.equal(seg('preview'), 'true', 'a click on the page threw the reader out of Preview');
  assert.equal(seg('edit'), 'false');
  assert.ok(el('rb-pane-document').innerHTML.includes(' on"'), 'the block was not selected');
});

test('a click on a block that is not in the document changes nothing', () => {
  const { ctx, el } = loadEditor();
  const before = el('rb-pane-document').innerHTML;
  el('rb-paper').fire('pointerdown', { target: target({ node: 'n-never-existed' }) });
  assert.equal(el('rb-pane-document').innerHTML, before);
});

/* ---- adding, duplicating and deleting through the form ---- */

test('the form adds a block, selects what it added, and can add inside a container', () => {
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const before = ctx.ResumeEditor.current().root.children.length;
  panel.fire('click', { target: target({ add: 'section', into: '' }) });
  const after = ctx.ResumeEditor.current().root.children;
  assert.equal(after.length, before + 1, 'nothing was added to the page');
  assert.equal(after[after.length - 1].type, 'section');
  assert.ok(el('rb-pane-document').innerHTML.includes('data-node="' + after[after.length - 1].id + '"'),
    'the form must land on the block it just made, or the user has to go and find it');

  const entry = D.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'work_entry');
  const bullets = entry.children.filter(c => c.type === 'bullet').length;
  panel.fire('click', { target: target({ add: 'bullet', into: entry.id }) });
  assert.equal(D.find(ctx.ResumeEditor.current(), entry.id).children.filter(c => c.type === 'bullet').length,
    bullets + 1, 'the bullet did not land inside the job it was added to');
});

test('duplicate copies the selected block, delete removes it and clears the selection', () => {
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const entry = D.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'work_entry');
  const parent = D.parentOf(ctx.ResumeEditor.current(), entry.id);
  const before = D.find(ctx.ResumeEditor.current(), parent.id).children.length;

  ctx.ResumeEditor.select(entry.id);
  panel.fire('click', { target: target({ act: 'duplicate' }) });
  assert.equal(D.find(ctx.ResumeEditor.current(), parent.id).children.length, before + 1);

  ctx.ResumeEditor.select(entry.id);
  panel.fire('click', { target: target({ act: 'remove' }) });
  assert.equal(D.find(ctx.ResumeEditor.current(), entry.id), null, 'the block is still there');
  assert.ok(!el('rb-pane-document').innerHTML.includes('data-node="' + entry.id + '"'),
    'the form kept offering Delete and Duplicate for a block that no longer exists');
});

test('an action with nothing selected does nothing at all', () => {
  const { ctx, panel } = loadEditor();
  const before = JSON.stringify(ctx.ResumeEditor.current().root);
  for (const act of ['remove', 'duplicate', 'unfork', 'hide']) {
    panel.fire('click', { target: target({ act }) });
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
  const { ctx, el, panel } = loadEditor();
  /* An empty sheet, so the only words on the page are the two this test writes — the tab opens
     on the guide's worked example, whose own words would decide where the halfway mark falls. */
  el('rb-new-blank').fire('click');
  const bullets = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .filter(n => n.type === 'bullet');
  const write = (node, text) => panel.fire('input',
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

  /* Now the terms this is actually used with. Every word above was chosen so that no term is a
     substring of any other word in the fixture, which made the check above assert carefully and
     never fail — and a raw substring match called `R`, `C` and `AI` present in a résumé holding
     none of them, on a job board where those three ARE the corpus. */
  write(bullets[0], 'Shipped a C++ trading engine and the JavaScript tooling around it.');
  write(bullets[1], 'Coordinated the release train and ran the retail migration.');
  layPaperOut(el, [
    { text: 'shipped a c++ trading engine and the javascript tooling around it.', top: 100 },
    { text: 'coordinated the release train and ran the retail migration.', top: 200 },
  ]);
  el('rb-kw').value = 'C++, JavaScript, Java, C, R, Go, AI';
  ctx.ResumeEditor.keywordCheck();
  assert.match(el('rb-kw-summary').textContent, /^2 of 7 present/,
    'a term is matched as a term, not as a substring of a longer word');
  const kw = el('rb-kw-out').innerHTML.split('rb-kwrow');
  assert.ok(kw[1].includes('top half, page one'), '"C++" was not found at all — a naive \\b matches none of it');
  assert.ok(kw[2].includes('top half, page one'), '"JavaScript" was not found');
  for (const [i, term] of [[3, 'Java'], [4, 'C'], [5, 'R'], [6, 'Go'], [7, 'AI']]) {
    assert.ok(kw[i].includes('missing'), `"${term}" is not in this résumé, only inside longer words`);
  }
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
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const bullet = D.flatten(ctx.ResumeEditor.current()).filter(n => n.type === 'bullet')[1];
  const write = text => panel.fire('input',
    { target: target({ node: bullet.id, field: 'text' }, { type: 'textarea', value: text }) });

  write('Gave customers correct change by adding cash');
  const masterName = ctx.ResumeEditor.current().name;

  newVersion(el, 'A version');
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
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const bullet = D.flatten(ctx.ResumeEditor.current()).filter(n => n.type === 'bullet')[1];

  newVersion(el);
  ctx.ResumeEditor.select(bullet.id);
  panel.fire('change', { target: target({ show: bullet.id }, { checked: false }) });

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

/* ---- what a hidden segment costs -----------------------------------------------------------

   A HIDDEN ELEMENT MEASURES ZERO, and the sheet now starts inside a segment that is hidden. This
   is the trap the whole arrangement had to answer, and it is not theoretical: measured in
   Chromium on a three-page résumé, with the switch NOT repainting, the first switch to Preview
   showed the sheet at its raw 816px inside a 734px column, reserved 0px of height for it (so the
   page drew over whatever followed), and drew ZERO of the two page-break markers. `fitToWidth`
   had run and computed the right zoom; nothing had applied it, because `paint()` is what writes
   the transform, the wrapper's height and the markers.

   The stub cannot make an element measure zero — it knows nothing about nesting. What it can do
   is prove the two halves of the fix separately: that the switch repaints, and that it fits once
   rather than every time. */

test('opening Preview measures the sheet again, rather than trusting what it read while hidden', () => {
  const { el, segs } = loadEditor();
  const wrap = el('rb-paper-wrap');
  assert.equal(wrap.style.height, '1056px', 'the wrapper never reserved the sheet height at all');

  /* The sheet grows while nobody is looking at it — which is what typing in the Edit segment
     does. Nothing repaints the wrapper until the segment carrying it opens. */
  el('rb-paper').offsetHeight = 2000;
  assert.equal(wrap.style.height, '1056px', 'something painted the hidden segment unprompted');

  segs.fire('click', { target: segs.children[1] });
  assert.equal(wrap.style.height, '1400px',
    'the wrapper still reserves what the sheet measured while it was hidden — 2000 x 0.7');
  assert.equal(el('rb-zoom-read').textContent, '70%',
    'the sheet was never fitted to the space the Preview segment actually gives it');
});

test('the tenth switch behaves like the first, and neither throws away a chosen zoom', () => {
  const { el, segs } = loadEditor();
  const toPreview = () => segs.fire('click', { target: segs.children[1] });
  const toEdit = () => segs.fire('click', { target: segs.children[0] });

  toPreview();
  assert.equal(el('rb-zoom-read').textContent, '70%');

  /* A zoom the user chose. Re-fitting on every switch would take it away — which is why the fit
     is latched and the paint is not. */
  el('rb-zoom').value = '150';
  el('rb-zoom').fire('input', { target: el('rb-zoom') });
  assert.equal(el('rb-zoom-read').textContent, '150%');

  for (let i = 0; i < 9; i++) { toEdit(); toPreview(); }
  assert.equal(el('rb-zoom-read').textContent, '150%',
    'a later switch re-fitted the page and threw away the zoom the user had set');
  /* Still repainting, though: the height follows the sheet on the tenth switch as on the first. */
  toEdit();
  el('rb-paper').offsetHeight = 3000;
  toPreview();
  assert.equal(el('rb-paper-wrap').style.height, '4500px',
    'the tenth switch stopped repainting, so Preview shows what it measured nine switches ago');
});

/* ---- keeping an entry, and choosing whether it prints -------------------------------------- */

test('unticking a block keeps every word and takes it off the page', () => {
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const entry = D.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'work_entry');

  ctx.ResumeEditor.select(entry.id);
  assert.match(el('rb-pane-document').innerHTML, /data-show="/, 'no tick is offered at all');

  panel.fire('change', { target: target({ show: entry.id }, { checked: false }) });

  const doc = ctx.ResumeEditor.current();
  assert.ok(D.find(doc, entry.id), 'the block was DELETED — the whole point is that it is kept');
  assert.deepEqual(doc.hidden, [entry.id], 'the document does not record what it leaves out');
  assert.equal(D.find(D.resolve(doc), entry.id), null, 'the résumé still prints the block');
  assert.ok(el('rb-pane-document').innerHTML.includes('not shown'),
    'nothing on the row says the block is being kept rather than printed');

  panel.fire('change', { target: target({ show: entry.id }, { checked: true }) });
  assert.ok(D.find(D.resolve(ctx.ResumeEditor.current()), entry.id), 'ticking it back did nothing');
});

/* The header cannot be removed (Layer 1 says so), so it cannot be switched off either — a
   résumé with no name on it is not a thing to offer. */
test('a block Layer 1 says is not optional is offered no tick', () => {
  const { ctx, el } = loadEditor();
  const header = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'header');
  ctx.ResumeEditor.select(header.id);
  assert.ok(!el('rb-pane-document').innerHTML.includes('data-show="' + header.id + '"'),
    'the name and contact block can be switched off');
});

/* One control, two layers. Which list the tick writes is which layer the user is editing — and
   the master's choice reaches every version, because a version is a difference FROM the master. */
test('the tick writes the layer being edited, and the master reaches every version', () => {
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const bullets = D.flatten(ctx.ResumeEditor.current()).filter(n => n.type === 'bullet');

  panel.fire('change', { target: target({ show: bullets[0].id }, { checked: false }) });
  assert.deepEqual(ctx.ResumeEditor.current().hidden, [bullets[0].id],
    'with no version active the tick must write the document itself');

  newVersion(el);
  panel.fire('change', { target: target({ show: bullets[1].id }, { checked: false }) });
  const doc = ctx.ResumeEditor.current();
  assert.deepEqual(doc.hidden, [bullets[0].id], 'the version edited the MASTER by surprise');
  assert.deepEqual(doc.tailorings[0].hidden, [bullets[1].id],
    'the tick did not reach the version that was on screen');

  /* And the two compose in the printed document, which is the claim that matters. */
  const printed = D.resolve(doc);
  assert.equal(D.find(printed, bullets[0].id), null, 'the master\u2019s choice did not reach the version');
  assert.equal(D.find(printed, bullets[1].id), null, 'the version\u2019s own choice was not applied');

  /* Under a version, a block the master leaves out is ticked off and DISABLED — turning it on
     here could only do nothing or edit the master by surprise. */
  ctx.ResumeEditor.select(bullets[0].id);
  const row = el('rb-pane-document').innerHTML;
  const at = row.indexOf('data-show="' + bullets[0].id + '"');
  assert.ok(at > -1 && row.slice(at, at + 120).includes('disabled'),
    'a block the master leaves out offers a live tick the version cannot honour');
});

/* ---- dismissing the menus over the bar ----

   A menu that can only be closed by finding its own button again is a trap, and it became a
   visible one when these moved under the bar: the Résumés panel is 247px tall and covers what is
   behind it. */

test('Escape closes an open popover and puts the focus back on its button', () => {
  const { el } = loadEditor();
  el('rb-open').fire('click');
  assert.equal(el('rb-pop-open').hidden, false, 'the menu never opened');

  fireKey(el, { key: 'Escape' });
  assert.equal(el('rb-pop-open').hidden, true, 'Escape left the menu open');
  assert.equal(el('rb-open').getAttribute('aria-expanded'), 'false');
  assert.ok(el('rb-open')._focused, 'Escape dropped the focus wherever the menu had left it');
});

test('a pointerdown outside closes the menu, and one on its own button still toggles', () => {
  const { ctx, el } = loadEditor();
  const outside = fn => (ctx._docHandlers.pointerdown || []).forEach(h => h({ target: fn }));

  el('rb-download').fire('click');
  assert.equal(el('rb-pop-download').hidden, false);
  outside(el('rb-paper'));
  assert.equal(el('rb-pop-download').hidden, true, 'a click on the page left the menu open');

  /* The owning button is excluded from the outside handler on purpose. It decides by reading
     `pop.hidden`, so a pointerdown that closed the menu first would make its own click reopen
     the thing the user asked to close. */
  el('rb-download').fire('click');
  outside(el('rb-download'));
  assert.equal(el('rb-pop-download').hidden, false,
    'the pointerdown closed the menu before the button that owns it had its click');
  el('rb-download').fire('click');
  assert.equal(el('rb-pop-download').hidden, true, 'the button no longer closes its own menu');
});

/* ---- the segment strip ---- */

test('the segment strip keeps one tab stop and moves the selection with the arrows', () => {
  const { ctx, el, segs } = loadEditor();
  const state = () => segs.children.map(b => b.getAttribute('aria-selected') + '/' + b.tabIndex);

  assert.deepEqual(state(), ['true/0', 'false/-1'], 'the tab must open on Edit');
  assert.deepEqual(SEGMENTS.filter(n => !el('rb-pane-' + n).hidden), ['edit']);

  segs.fire('click', { target: segs.children[1] });   // Preview
  assert.deepEqual(state(), ['false/-1', 'true/0'],
    'a tablist is ONE tab stop; two tabIndex-0 buttons is two');
  assert.deepEqual(SEGMENTS.filter(n => !el('rb-pane-' + n).hidden), ['preview']);

  ctx.document.activeElement = segs.children[1];
  segs.fire('keydown', { key: 'ArrowRight', preventDefault() {} });
  assert.equal(segs.children[0].getAttribute('aria-selected'), 'true',
    'the arrows must wrap, or the last tab is a dead end');
  assert.ok(segs.children[0]._focused, 'the arrow moved the selection but not the focus');
  assert.deepEqual(SEGMENTS.filter(n => !el('rb-pane-' + n).hidden), ['edit'],
    'the panel must follow the selection, as it does for a mouse');

  ctx.document.activeElement = segs.children[0];
  segs.fire('keydown', { key: 'End', preventDefault() {} });
  assert.equal(segs.children[1].getAttribute('aria-selected'), 'true');
});

test('a keypress on the strip that is not a traversal is left alone', () => {
  const { ctx, el, segs } = loadEditor();
  const before = segs.children.map(b => b.getAttribute('aria-selected'));
  ctx.document.activeElement = segs.children[0];
  segs.fire('keydown', { key: 'a', preventDefault() { throw new Error('swallowed a keystroke'); } });
  assert.deepEqual(segs.children.map(b => b.getAttribute('aria-selected')), before);

  /* A keydown that reaches the strip from somewhere else — the panel below it, say — must not
     move the selection either. */
  ctx.document.activeElement = el('rb-pane-document');
  segs.fire('keydown', { key: 'ArrowRight', preventDefault() { throw new Error('claimed a key it does not own'); } });
  assert.deepEqual(segs.children.map(b => b.getAttribute('aria-selected')), before);
});

/* ---- what the Checks tab says out loud ---- */

test('the Checks verdict is announced when it changes, and not otherwise', () => {
  const { ctx, el, panel } = loadEditor();
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
  panel.fire('input', { target: target({ node: bullet.id, field: 'text' },
    { type: 'textarea', value: 'Manage the till. And a second sentence.' }) });
  assert.match(live.textContent, /^Checks: [1-9]\d* to fix/, 'a changed verdict went unannounced');
});

/* ---- the form's own escaping -------------------------------------------------------------
   `importJson` rewrites hostile identifiers at the boundary, and its own tests cover that. This
   is the second lock, on the sinks: a document can reach the form WITHOUT passing the import —
   one already sitting in localStorage from before that fix shipped does exactly that — so the
   form has to escape what it is handed rather than trust where it came from. */

test('a hostile node id cannot break out of the attributes the form writes', () => {
  let { ctx, el, panel } = loadEditor();
  const doc = ctx.ResumeDocument.clone(ctx.ResumeEditor.current());
  const nasty = 'x" onmouseover="alert(1)" data-x="';
  const entry = ctx.ResumeDocument.flatten(doc).find(n => n.type === 'work_entry');
  doc.content[nasty] = doc.content[entry.id];
  entry.id = nasty;

  /* Re-opened from this browser's own storage, which is how a document written before that
     import fix shipped arrives: no import, so nothing has had a chance to rewrite the id. */
  const second = loadEditor({ storage: fakeStorage([doc]) });
  second.ctx.ResumeEditor.select(nasty);
  el = second.el; panel = second.panel; ctx = second.ctx;
  assert.equal(ctx.ResumeDocument.find(ctx.ResumeEditor.current(), nasty).type, 'work_entry',
    'the seeded document did not come back out of storage');

  /* The escaped form still CONTAINS the word `onmouseover` — as text inside an attribute value,
     which is inert. What must not appear is the unescaped quote that would end the attribute and
     start a new one, so that is what is asserted. */
  for (const html of [el('rb-pane-document').innerHTML, el('rb-paper').innerHTML]) {
    assert.ok(!html.includes('" onmouseover="'), 'the id closed its attribute and opened another');
    assert.ok(html.includes('&quot; onmouseover=&quot;'), 'the id is not in this markup at all, so this test proves nothing');
  }

  /* And the round trip still works: the escaped attribute is only how it is written, so the
     form must still be able to edit the block it names. */
  panel.fire('input', { target: target({ node: nasty, field: 'role' },
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
  const { ctx, el, panel } = loadEditor();
  el('rb-new-blank').fire('click');      // the button that says which one it starts
  assert.equal(ctx.ResumeEditor.current().name, 'Untitled résumé');

  assert.ok(el('rb-badge').hidden, 'a red badge on a document nobody has started');
  assert.ok(el('rb-pane-checks').innerHTML.includes('when you start writing'),
    'the panel listed faults in an empty page instead of saying it is waiting');

  /* And the moment there are words, the checks are back — this must not be a way to make the
     rule panel go quiet. */
  const header = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current()).find(n => n.type === 'header');
  panel.fire('input', { target: target({ node: header.id, field: 'fullName' },
    { type: 'text', value: 'Lee' }) });
  assert.ok(!el('rb-badge').hidden, 'the checks never came back');
  assert.ok(el('rb-pane-checks').innerHTML.includes('rb-finding'));
});

/* ---- paper size ---- */

test('choosing A4 re-lays the page, and it survives a reload', () => {
  const loadedA4 = loadEditor();
  const { ctx, el } = loadedA4;
  assert.equal(el('rb-paper').style.width, '8.5in', 'the tab did not open on the layout’s sheet');
  assert.deepEqual(ctx.ResumeLayouts.PAPERS.map(p => p.id), ['letter', 'a4'],
    'the picker offers whatever Layer 2 declares; this test names what it expects to find');

  const { panel } = loadedA4;
  panel.fire('change', { target: { id: 'rb-paper-size', value: 'a4', dataset: {} } });
  assert.equal(el('rb-paper').style.width, '8.27in', 'the sheet on screen is still US Letter');
  assert.equal(el('rb-paper').style.minHeight, '11.69in');
  assert.match(el('rb-pane-design').innerHTML, /value="a4" selected/,
    'the control does not show what is in force');

  /* It is the document's, not the session's — so it comes back with the document. */
  ctx.ResumeEditor.flush();
  const saved = ctx.ResumeDocument.clone(ctx.ResumeEditor.current());
  const back = loadEditor({ storage: fakeStorage([saved]) });
  assert.equal(back.el('rb-paper').style.width, '8.27in', 'the sheet was a session setting, not the résumé’s');
});

/* ---- booting on a page that opened on this tab ----

   app.js is not deferred and the résumé scripts are, so on a load that lands straight on
   #resume — a refresh, a bookmark, a shared link — app.js has already run its `showTab()` and
   found no `window.ResumeEditor` to call. Nothing revisits the tab afterwards. The editor has to
   notice this itself, and the only evidence available to it is the panel already being visible. */

test('a page that opened on the résumé tab paints without anyone calling shown()', () => {
  const { el } = loadEditor({ skipBoot: true });
  assert.ok(el('rb-paper').innerHTML.length > 0,
    'the tab was left rendered but never painted — a blank sheet, exactly what a refresh showed');
  assert.match(el('rb-pane-design').innerHTML, /rb-paper-size/,
    'the paper control never got built, so the Design pane is empty too');
});

test('a page that opened on another tab pays nothing until the résumé tab is opened', () => {
  const { el } = loadEditor({ skipBoot: true, panelHidden: true });
  assert.equal(el('rb-paper').innerHTML, '',
    'every visitor to Search or Trends now builds and renders a document they never asked for');
});

/* ---- the account copy (ADR-0124, ADR-0131) -------------------------------------------------
   These drive the REAL editor against a stubbed wire. What is being checked is the wiring the
   browser pass cannot cover cheaply — that the switch is connected to the sync layer and not to
   the repository, that a delete tells the truth about where the résumé lives, and that a
   deployment with no account store is unchanged. */

/** Let the queued promises the wire returns actually run. */
const settled = () => new Promise(resolve => setTimeout(resolve, 0));

test('with no account store the tab is exactly what it was — no sync, no listing, no request', () => {
  /* Real browser storage, so the storage sentence is the one a working browser is shown —
     the MemoryRepository fallback prints a different one about storage being blocked. */
  const { ctx, el } = loadEditor({ storage: fakeStorage() });  // no `account`: resume_sync.js is not even loaded
  assert.equal(ctx.ResumeSync, undefined);
  el('rb-open').fire('click');   // the storage sentence is painted with the Résumés popover
  assert.ok(/Saved in this browser and nowhere else/.test(el('rb-storage').textContent));
  assert.equal(el('rb-doclist-remote').innerHTML, '', 'account rows on a page with no account');
});

test('the switch stores this résumé, and the storage sentence stops saying "nowhere else"', () => {
  const { ctx, el, wire } = loadEditor({ account: {}, storage: fakeStorage() });
  assert.ok(ctx.ResumeSync, 'the sync module did not load');
  el('rb-open').fire('click');   // the switch and the sentence both live in the Résumés popover
  assert.ok(/nowhere else/.test(el('rb-storage').textContent), 'it claims a copy before there is one');

  el('rb-sync').checked = true;
  el('rb-sync').fire('change', { target: el('rb-sync') });
  return settled().then(() => {
    const push = wire.find(c => c.method === 'PUT');
    assert.ok(push, 'flipping the switch stored nothing');
    assert.equal(push.body.sync, true);
    assert.equal(push.body.rev, 1, 'the first push did not offer revision 1');
    assert.ok(/copy is kept on your account/.test(el('rb-storage').textContent),
      'the storage sentence still says the résumé is in this browser and nowhere else');
  });
});

test('forty keystrokes cost no commits; leaving the tab costs one', () => {
  /* The end-to-end half of the cadence rule. What the module-level tests in
     tests/js/resume_sync.test.js pin precisely — the interval, the dirty gate, the floor — this
     one checks is actually WIRED: that a run of typing through the real editor reaches the wire
     not at all, and that the tab going away reaches it once. */
  const { ctx, el, wire } = loadEditor({ account: {} });
  el('rb-sync').checked = true;
  el('rb-sync').fire('change', { target: el('rb-sync') });
  return settled().then(() => {
    const before = wire.filter(c => c.method === 'PUT').length;
    const bullet = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
      .filter(n => n.type === 'bullet')[0];
    ctx.ResumeEditor.select(bullet.id);
    for (let i = 0; i < 40; i++) {
      el('rb-pane-document').fire('input', {
        target: target({ node: bullet.id, field: 'text' }, { value: 'Shipped it ' + i }),
      });
    }
    assert.equal(wire.filter(c => c.method === 'PUT').length, before,
      'typing reached the account — the localStorage debounce was reused for the commit');

    /* One of ADR-0124's three coarse events, and the one that matters most: the tab going away
       is when somebody has actually stopped typing. */
    ctx.document.visibilityState = 'hidden';
    el('rb-paper')._docVisibility.forEach(fn => fn({}));
    return settled();
  }).then(() => {
    assert.ok(wire.filter(c => c.method === 'PUT').length >= 1, 'tab-hide pushed nothing');
  });
});

test('a résumé on the account but not in this browser is offered, and opens', () => {
  const stored = {
    schema: 1, id: 'rmfk3n2wxyz', name: 'From the desktop', layoutId: 'headless-headhunter',
    updatedAt: '2026-09-09T00:00:00+00:00', root: { id: '__root__', type: '__root__', children: [] },
    content: {}, variants: {}, tailorings: [], activeTailoring: null, theme: {}, sync: true, rev: 4,
  };
  const { ctx, el } = loadEditor({ account: {
    '/resumes': { status: 200, body: [{ id: 'rmfk3n2wxyz', name: 'From the desktop',
      layoutId: 'headless-headhunter', updatedAt: '2026-09-09T00:00:00+00:00', rev: 4 }] },
    '/resumes/rmfk3n2wxyz': { status: 200, body: stored },
  } });
  return settled().then(() => {
    /* The whole point of syncing: a browser that has never seen this document can still find
       it. Without these rows a cleared cache is still an empty tab. */
    assert.ok(el('rb-doclist-remote').innerHTML.includes('data-pull="rmfk3n2wxyz"'),
      'the account row was not offered');
    el('rb-doclist-remote').fire('click', { target: target({ pull: 'rmfk3n2wxyz' }) });
    return settled();
  }).then(() => {
    assert.equal(ctx.ResumeEditor.current().id, 'rmfk3n2wxyz', 'pulling it did not open it');
    assert.equal(ctx.ResumeEditor.current().name, 'From the desktop');
  });
});

test('deleting a synced résumé says it leaves the account too, and takes it off', () => {
  const rows = [{ id: 'rmfk3n2wxyz', name: 'On the account', layoutId: 'headless-headhunter',
    updatedAt: '2026-09-09T00:00:00+00:00', rev: 1 }];
  const stored = {
    schema: 1, id: 'rmfk3n2wxyz', name: 'On the account', layoutId: 'headless-headhunter',
    updatedAt: '2026-09-09T00:00:00+00:00', root: { id: '__root__', type: '__root__', children: [] },
    content: {}, variants: {}, tailorings: [], activeTailoring: null, theme: {}, sync: true, rev: 1,
  };
  const { el, wire } = loadEditor({ account: {
    '/resumes': { status: 200, body: rows },
    '/resumes/rmfk3n2wxyz': { status: 200, body: stored },
  } });
  return settled().then(() => {
    /* Pulled down first, because a row you can delete is a row that is HERE — the × lives on the
       local list, and the account-only list below it offers "open a copy" and nothing else. */
    el('rb-doclist-remote').fire('click', { target: target({ pull: 'rmfk3n2wxyz' }) });
    return settled();
  }).then(() => {
    el('rb-open').fire('click');
    /* The × arms the question; it no longer deletes on the spot behind a modal box. */
    el('rb-doclist').fire('click', { target: target({ drop: 'rmfk3n2wxyz' }) });
    /* The old sentence said "only stored in this browser", which is false for this row — and a
       delete confirmation is the worst place in the product to be wrong about that. It is asked
       in the row now, so it can also say WHICH résumé it means. */
    assert.ok(/from your account/.test(el('rb-doclist').innerHTML),
      'the confirmation still claims browser-only');
    assert.ok(!wire.some(c => c.method === 'DELETE'), 'it deleted before anyone confirmed');
    el('rb-doclist').fire('click', { target: target({ dropYes: 'rmfk3n2wxyz' }) });
    return settled();
  }).then(() => {
    assert.ok(wire.some(c => c.method === 'DELETE' && c.url === '/resumes/rmfk3n2wxyz'),
      'the account copy was left behind');
  });
});

test('signed out disables the switch and says why, rather than pretending', () => {
  const { el } = loadEditor({ account: { '/resumes': { status: 401, body: { error: 'sign in first' } } } });
  return settled().then(() => {
    assert.equal(el('rb-sync').disabled, true, 'a switch that flips and stores nothing');
    assert.ok(/sign in/i.test(el('rb-sync-state').textContent));
  });
});

/* ---- the Checks panel at real volume ------------------------------------------------------
   Measured on a résumé of four jobs with seven bullets each: 44 findings in a panel 7,598px
   tall, from two rules. Each finding repeated the same 150-character explanation verbatim, and
   not one of them said which bullet it was about. */

test('a rule that fires many times is one group, explained once, naming the blocks', () => {
  const { ctx, el, panel } = loadEditor();
  const D = ctx.ResumeDocument;
  const bullets = D.flatten(ctx.ResumeEditor.current()).filter(n => n.type === 'bullet');
  assert.ok(bullets.length >= 3, 'the worked example no longer has enough bullets to group');
  /* A weak opener and no result anywhere, on every bullet — so both rules fire on all of them,
     which is the volume the panel could not carry. */
  bullets.forEach((b, i) => panel.fire('input', {
    target: target({ node: b.id, field: 'text' },
      { type: 'textarea', value: 'Responsible for the till in shop number ' + (i + 1) }),
  }));

  const fired = ctx.ResumeEditor.findings().filter(f => f.ruleId === 'opening-verb');
  assert.ok(fired.length >= 3, 'the rule under test fired ' + fired.length + ' times, too few to group');
  const html = el('rb-pane-checks').innerHTML;

  /* The explanation, once. It was printed once per occurrence — which is what made the panel
     taller than three laptop screens. */
  /* The explanation this layout's own `opening-verb` rule states, which every one of its
     findings ends with. */
  const shared = 'opens the bullet without saying what you did';
  const copies = html.split(shared).length - 1;
  assert.equal(copies, 1, 'the explanation is still repeated per finding: ' + copies + ' copies');

  /* One group per rule, wearing its count. */
  const heads = html.match(/class="rb-fgroup-head"/g) || [];
  assert.equal(heads.length, new Set(ctx.ResumeEditor.findings().map(f => f.ruleId)).size,
    'the findings are not grouped by rule');
  assert.ok(html.includes('<span class="rb-fcount">' + fired.length + '</span>'),
    'the group does not say how many blocks broke its rule');

  /* And every finding names the block it is about, by that block's own opening words. */
  for (const b of bullets.slice(0, 3)) {
    const words = D.contentOf(ctx.ResumeEditor.current(), b.id).text.slice(0, 48);
    assert.ok(html.includes(words), 'no finding names the bullet ' + JSON.stringify(words));
  }
});

/* ---- Check coverage paints when it is clicked --------------------------------------------- */

test('Check coverage marks the page at once, not on the next unrelated keystroke', () => {
  const { el, panel } = loadEditor();
  /* Through the delegated listener, which is the path the button really takes. */
  const run = Object.assign(target({}), { id: 'rb-kw-run' });
  el('rb-kw').value = 'Point of Sale';
  panel.fire('click', { target: run });
  /* Measured before this: the summary said "2 of 2 present · 100% in the top half" with zero
     <mark> elements on the sheet, and typing one space into the name box then produced five. */
  assert.ok(/<mark/.test(el('rb-paper').innerHTML),
    'the page was not repainted, so the highlights arrive on some later change or not at all');
  /* And emptying the box takes them off again, which the comment there claimed and the code
     did not: that branch returned before either clearing the terms or repainting. */
  el('rb-kw').value = '';
  panel.fire('click', { target: run });
  assert.ok(!/<mark/.test(el('rb-paper').innerHTML), 'the marks outlived the terms that drew them');
});

/* ---- no native dialogs --------------------------------------------------------------------
   `window.prompt`, `window.confirm` and `window.alert` are modal, unstyled, outside the page's
   own reading order, and answerable only by typing into a box the app drew nothing of. The stub
   throws on all three (see `loadEditor`), so a reintroduction fails wherever it is called; this
   reads the shipped file as well, because a path no test drives would otherwise slip through. */

test('the editor calls none of the three native dialogs', () => {
  const source = fs.readFileSync(path.join(DIR, 'resume_editor.js'), 'utf8');
  const found = ['prompt', 'confirm', 'alert'].filter(fn => source.includes('window.' + fn + '('));
  assert.deepEqual(found, [], 'these are back in the editor: ' + found.join(', '));
});

test('deleting a version asks in the bar, and Keep really keeps it', () => {
  const { ctx, el } = loadEditor();
  newVersion(el, 'Acme, Backend Engineer');
  assert.equal(ctx.ResumeEditor.current().tailorings.length, 1);

  el('rb-version-del').fire('click');
  assert.equal(el('rb-version-confirm').hidden, false, 'nothing asked before deleting');
  assert.ok(/Acme, Backend Engineer/.test(el('rb-version-confirm-text').textContent),
    'the question does not name the version it would delete');
  assert.equal(el('rb-version-del').hidden, true, 'the question and the button it replaces are both up');
  assert.equal(ctx.ResumeEditor.current().tailorings.length, 1, 'it deleted before anyone answered');

  el('rb-version-no').fire('click');
  assert.equal(el('rb-version-confirm').hidden, true, 'Keep left the question up');
  assert.equal(ctx.ResumeEditor.current().tailorings.length, 1, 'Keep deleted it anyway');

  el('rb-version-del').fire('click');
  el('rb-version-yes').fire('click');
  assert.equal(ctx.ResumeEditor.current().tailorings.length, 0, 'Delete did not delete it');
});

test('a résumé is deleted only after the row itself asks', () => {
  const storage = fakeStorage([]);
  const { ctx, el } = loadEditor({ storage });
  const id = ctx.ResumeEditor.current().id;
  el('rb-open').fire('click');

  el('rb-doclist').fire('click', { target: target({ drop: id }) });
  assert.ok(/data-drop-yes="/.test(el('rb-doclist').innerHTML), 'the row did not ask');
  assert.ok(storage.getItem('headstart.resume.' + id), 'it deleted before anyone answered');

  el('rb-doclist').fire('click', { target: target({ dropNo: id }) });
  assert.ok(!/data-drop-yes="/.test(el('rb-doclist').innerHTML), 'Keep left the question up');
  assert.ok(storage.getItem('headstart.resume.' + id), 'Keep deleted it anyway');

  el('rb-doclist').fire('click', { target: target({ drop: id }) });
  el('rb-doclist').fire('click', { target: target({ dropYes: id }) });
  assert.ok(!storage.getItem('headstart.resume.' + id), 'Delete did not delete it');
});

test('an unreadable import says so in the bar rather than in a modal box', () => {
  const { el } = loadEditor();
  el('rb-file').fire('change', {
    target: { files: [{ text: () => Promise.resolve('{ not json') }], value: 'x' },
  });
  return settled().then(() => {
    assert.ok(el('rb-saved').textContent, 'a failed import said nothing anywhere');
  });
});

/* ---- two controls that contradicted each other -------------------------------------------- */

test('the End date is switched off while “Still here” is ticked', () => {
  const { ctx, el, panel } = loadEditor();
  const entry = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .find(n => n.type === 'work_entry');
  ctx.ResumeEditor.select(entry.id);

  const endField = () => {
    const html = el('rb-pane-document').innerHTML;
    const at = html.indexOf('data-field="end"');
    assert.notEqual(at, -1, 'the End field is not in the form');
    const tag = html.slice(html.lastIndexOf('<input', at), html.indexOf('>', at) + 1);
    assert.ok(tag.includes('data-field="end"'), 'this is not the End field: ' + tag);
    return tag;
  };
  const tick = on => panel.fire('input', {
    target: target({ node: entry.id, field: 'current' }, { type: 'checkbox', checked: on }),
  });

  /* The worked example's first job IS a current one, so start by leaving it. */
  tick(false);
  assert.ok(!/disabled/.test(endField()), 'a job with no “Still here” tick cannot type an end date');

  tick(true);
  assert.ok(/disabled/.test(endField()),
    '“Still here” is ticked and the End date still invites a date that is thrown away');
  assert.ok(/Still here/.test(endField()), 'the field does not say which control settled it');

  tick(false);
  assert.ok(!/disabled/.test(endField()), 'unticking it left the field switched off');
});

/* ---- the bounds the editor offers are the DOCUMENT's sheet -------------------------------
   #423 gave `ResumeLayouts` a `boundsFor(layout, doc)` that derives the free canvas's maxima
   from the paper the document actually chose — 6.9 × 9.4in on Letter, 6.67 × 10.09 on A4. The
   editor was the other half and still read the Layout's own raw constants, so on an A4 document
   the renderer clamped a block to 6.67 while the slider beside it still offered 6.9 — the UI
   inviting a position it then takes away, and the overflow rule warning about it afterwards. */

test('the size sliders offer the maximum this document’s paper allows, not the layout’s', () => {
  const { ctx, el } = loadEditor();
  ctx.ResumeEditor.changeLayout('free-canvas');
  const block = ctx.ResumeDocument.flatten(ctx.ResumeEditor.current())
    .find(n => n.type !== '__root__' && ctx.ResumeDocument.parentOf(ctx.ResumeEditor.current(), n.id));
  ctx.ResumeEditor.select(block.id);

  const maxOf = key => {
    const html = el('rb-pane-document').innerHTML;
    const at = html.indexOf('data-geo="' + key + '"');
    assert.notEqual(at, -1, 'there is no ' + key + ' slider on the free canvas');
    return (/max="([\d.]+)"/.exec(html.slice(at, html.indexOf('>', at))) || [])[1];
  };
  const letter = ctx.ResumeLayouts.boundsFor(ctx.ResumeLayouts.get('free-canvas'), null);
  assert.equal(maxOf('x'), String(letter.x[1]), 'the Letter default is not what the slider offers');

  /* Same document, A4 paper. */
  el('rb').fire('change', { target: Object.assign(target({}), { id: 'rb-paper-size', value: 'a4' }) });
  const a4 = ctx.ResumeLayouts.boundsFor(ctx.ResumeLayouts.get('free-canvas'),
    ctx.ResumeEditor.current());
  assert.notEqual(a4.x[1], letter.x[1], 'A4 and Letter bound the canvas identically — no test here');
  assert.equal(maxOf('x'), String(a4.x[1]),
    'the slider still offers the Letter maximum on an A4 document, which the renderer clamps away');
  assert.equal(maxOf('y'), String(a4.y[1]), 'the same, down the page');
});
