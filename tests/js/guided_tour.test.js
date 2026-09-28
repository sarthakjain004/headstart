/* The guided tour (ADR-0249), against the REAL src/headstart/ui/static/guided_tour.js.
 *
 * The tour knows the page only through selectors and the hash router, and its one promise to a
 * page that another change is restyling is that a missing target costs that step and never the
 * tour. These run it over a small stub DOM with a hand-driven clock, so the wait for a target
 * that never arrives is instant here. Geometry (where the card lands, the spotlight's box) needs
 * a real browser and is checked by scripts/eval/ui_smoke.py instead.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const TOUR_JS = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui', 'static', 'guided_tour.js');

function fakeNode(tag, shown = true) {
  const handlers = {};
  const classes = new Set();
  const node = {
    tag, id: '', className: '', textContent: '', type: '', disabled: false, hidden: false,
    shown, children: [], attrs: {}, style: {}, offsetWidth: 300, offsetHeight: 160, focused: 0,
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k] ?? null; },
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    click() { (handlers.click || []).forEach(fn => fn({ target: node })); },
    append(...kids) { this.children.push(...kids); kids.forEach(k => { k.parent = this; }); },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(k => k !== this); this.parent = null; },
    focus() { this.focused++; },
    // Visible while attached (or a page node that is shown), like a real hidden panel.
    getClientRects() { return this.shown && !this.hiddenBy?.hidden ? [{}] : []; },
    getBoundingClientRect: () => ({ top: 100, left: 40, right: 240, bottom: 140, width: 200, height: 40 }),
    scrollIntoView() {},
    closest(sel) { return sel === '[data-tour-start]' && 'data-tour-start' in this.attrs ? this : null; },
    classList: { toggle(c, on) { if (on) classes.add(c); else classes.delete(c); }, contains: c => classes.has(c) },
  };
  return node;
}

/** The page: Home and Search panels, a nav, and the Search targets — minus `missing`.
 *  Setting `location.hash` shows that panel, as app.js's router does. */
function loadTour({ missing = [], stored = null, storageThrows = false, trendsLink = true,
                   folded = false, sidebar = true } = {}) {
  // Shown only where the nav is a sidebar; a narrow window's strip hides it.
  const foldButton = fakeNode('button', sidebar);
  const panels = { home: fakeNode('section'), search: fakeNode('section') };
  panels.search.hidden = true;
  const onSearch = node => { node.hiddenBy = panels.search; return node; };
  const targets = {
    'nav.tabs': fakeNode('nav'),
    '#q': onSearch(fakeNode('input')),
    '#rail': onSearch(fakeNode('aside')),
    '#results .card': onSearch(fakeNode('div')),
    '#results': onSearch(fakeNode('div')),
    '.tabs [data-tab="trends"]': trendsLink ? fakeNode('a') : null,
  };
  for (const sel of missing) targets[sel] = null;
  const body = fakeNode('body');
  const shell = fakeNode('div');
  const docHandlers = {};
  const location = {};
  let hash = '';
  Object.defineProperty(location, 'hash', {
    get: () => hash,
    set: v => { hash = v; for (const [name, p] of Object.entries(panels)) p.hidden = '#' + name !== v; },
  });
  let clock = 0;
  const timers = [];
  const saved = {};
  if (stored) saved['hs.tourOffered'] = stored;
  const find = sel => {
    if (sel === '.shell') return shell;
    if (sel === '.tour-offer') return body.children.find(k => k.className === 'tour-offer') || null;
    return targets[sel] ?? null;
  };
  const ctx = {
    document: {
      body, activeElement: body,
      createElement: tag => fakeNode(tag),
      querySelector: find,
      getElementById: id => (id.startsWith('panel-') ? panels[id.slice(6)] || null
        : id === 'q' ? targets['#q'] : id === 'nav-toggle' ? foldButton : null),
      documentElement: { dataset: folded ? { nav: 'collapsed' } : {} },
      addEventListener(type, fn) { (docHandlers[type] ||= []).push(fn); },
      removeEventListener(type, fn) { docHandlers[type] = (docHandlers[type] || []).filter(f => f !== fn); },
    },
    location,
    localStorage: {
      getItem(k) { if (storageThrows) throw new Error('blocked'); return saved[k] ?? null; },
      setItem(k, v) { if (storageThrows) throw new Error('blocked'); saved[k] = v; },
    },
    innerWidth: 1440, innerHeight: 900,
    addEventListener() {}, removeEventListener() {},
    Date: { now: () => clock },
    setTimeout: fn => timers.push(fn),
    Promise, Math, Object,
  };
  ctx.globalThis = ctx;
  vm.runInNewContext(fs.readFileSync(TOUR_JS, 'utf8'), ctx);
  const pop = () => body.children.find(k => k.className === 'tour-pop');
  const buttons = () => pop().children.find(k => k.className === 'tour-actions').children;
  return {
    ctx, body, shell, saved, targets, docHandlers, panels, pop,
    count: () => pop().children[0].textContent,
    title: () => pop().children[1].textContent,
    skip: () => buttons()[0], back: () => buttons()[1], next: () => buttons()[2],
    // Let every pending promise settle, then fire the timers that were due, `ms` later.
    async tick(ms = 0) {
      for (let round = 0; round < 60; round++) {
        await new Promise(r => setImmediate(r));
        clock += ms / 60;
        const due = timers.splice(0);
        due.forEach(fn => fn());
      }
    },
    key(k) { (docHandlers.keydown || []).forEach(fn => fn({ key: k, preventDefault() {} })); },
  };
}

test('the tour walks its steps in order, switching to the tab each one lives on', async () => {
  const t = loadTour();
  t.ctx.GuidedTour.start();
  await t.tick();
  assert.equal(t.title(), 'Everything is one click away');
  assert.equal(t.count(), '1 of 5');
  assert.equal(t.back().disabled, true, 'nothing before the first step');
  assert.equal(t.shell.inert, true, 'the page behind the tour cannot be clicked or tabbed into');
  t.next().click();
  await t.tick();
  assert.equal(t.ctx.location.hash, '#search');
  assert.equal(t.title(), 'Describe the job you want');
  assert.equal(t.count(), '2 of 5');
  assert.ok(t.next().focused > 0, 'focus follows the step, so Enter keeps going');
  t.back().click();
  await t.tick();
  assert.equal(t.count(), '1 of 5');
});

test('a missing target costs that step, never the tour', async () => {
  // Another change renames the filter rail: its selector finds nothing, so the tour waits out
  // its limit and moves on to the results.
  const t = loadTour({ missing: ['#rail'] });
  t.ctx.GuidedTour.start();
  await t.tick();
  t.next().click();
  await t.tick();
  assert.equal(t.title(), 'Describe the job you want');
  t.next().click();
  await t.tick(3000);
  assert.equal(t.title(), 'Open a job at the source');
  // …and the count stops promising the step it skipped.
  assert.equal(t.count(), '3 of 4');
});

test('a step on every tab whose target is absent is neither shown nor counted', async () => {
  // The local renderer draws no Trends tab, so the tour has four steps there, not five, and
  // the last one it can show says Finish.
  const t = loadTour({ trendsLink: false });
  t.ctx.GuidedTour.start();
  await t.tick();
  assert.equal(t.count(), '1 of 4');
  for (let i = 0; i < 3; i++) { t.next().click(); await t.tick(); }
  assert.equal(t.count(), '4 of 4');
  assert.equal(t.next().textContent, 'Finish');
  t.next().click();
  await t.tick();
  assert.equal(t.pop(), undefined, 'Finish closes the tour');
  assert.equal(t.shell.inert, false);
});

test('Escape and Skip both close it and give the page back', async () => {
  for (const how of ['escape', 'skip']) {
    const t = loadTour();
    t.ctx.GuidedTour.start();
    await t.tick();
    if (how === 'escape') t.key('Escape'); else t.skip().click();
    assert.equal(t.pop(), undefined, how);
    assert.equal(t.body.children.length, 0, 'no spotlight left behind: ' + how);
    assert.equal(t.shell.inert, false, how);
    assert.equal((t.docHandlers.keydown || []).length, 0, 'keys are the page\'s again: ' + how);
  }
});

test('the first-visit offer shows once, and never where the browser cannot remember it', () => {
  const first = loadTour();
  assert.ok(first.ctx.document.querySelector('.tour-offer'), 'a first visit on Home is offered the tour');
  assert.equal(first.saved['hs.tourOffered'], '1');

  const again = loadTour({ stored: '1' });
  assert.equal(again.ctx.document.querySelector('.tour-offer'), null, 'offered once, not every visit');

  const blocked = loadTour({ storageThrows: true });
  assert.equal(blocked.ctx.document.querySelector('.tour-offer'), null,
    'blocked storage would offer it on every visit, so it is not offered at all');
});

test('starting the tour takes the offer down', async () => {
  const t = loadTour();
  t.ctx.GuidedTour.start();
  await t.tick();
  assert.equal(t.ctx.document.querySelector('.tour-offer'), null);
});

test('folded to icons, the first step describes icons, not names it cannot see', async () => {
  const body = t => t.pop().children[2].textContent;
  const folded = loadTour({ folded: true });
  folded.ctx.GuidedTour.start();
  await folded.tick();
  assert.match(body(folded), /Hover one to see its name/);
  // A stored fold on a window too narrow for the sidebar shows the strip, names and all.
  const strip = loadTour({ folded: true, sidebar: false });
  strip.ctx.GuidedTour.start();
  await strip.tick();
  assert.match(body(strip), /résumé builder all live here/);
});
