/* app.js's **Home** tab (ADR-0249), run against the REAL src/headstart/ui/static/app.js.
 *
 * Same harness shape, and the same naming, as app_search.test.js and app_saved.test.js: one
 * file per tab's worth of app.js, named for the tab. Home is the tab the hash router falls back
 * to, so most of this file states that routing: the bare URL lands on Home, a named tab still
 * lands on itself, the retired `#data` lands on Home, and an in-page anchor lands on the tab
 * that holds it (the skip link's `#results` must keep the reader on Search, which it only did
 * for free while Search was the fallback). The rest is Home's one behaviour of its own: its
 * search box hands the words to Search.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui', 'static', 'app.js');

// The panels this page renders, and the in-page anchors inside them.
const PANELS = ['home', 'search', 'trends', 'resume'];
const ANCHORS = { results: 'search', 'how-matching': 'home', 'good-to-know': 'home' };

function fakeEl(extra) {
  const classes = new Set();
  const handlers = {};
  return {
    innerHTML: '', textContent: '', hidden: false, value: '', checked: false, dataset: {}, options: [],
    style: { setProperty() {}, getPropertyValue: () => '', removeProperty() {} },
    querySelector: () => null, querySelectorAll: () => [],
    setAttribute(k, v) { this[k] = v; }, getAttribute: () => null,
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); }, dispatchEvent() {},
    fire(type, event) { (handlers[type] || []).forEach(fn => fn(event)); },
    closest: () => null,
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle(c, on) { const want = on === undefined ? !classes.has(c) : !!on;
                      if (want) classes.add(c); else classes.delete(c); return want; } },
    ...extra,
  };
}

/** A fresh evaluation of app.js with the page at `hash`. Every id the load-time code asks for
 *  exists, EXCEPT a `panel-*` this page does not render — which is exactly the case the
 *  router's fallback exists for. */
function loadApp(hash) {
  const panels = Object.fromEntries(PANELS.map(name => [name, fakeEl({ id: 'panel-' + name })]));
  const nodes = {};
  const requested = [];
  const location = { hash };
  const ctx = {
    document: {
      getElementById: id => {
        if (id.startsWith('panel-')) return panels[id.slice('panel-'.length)] || null;
        if (ANCHORS[id]) return (nodes[id] ||= fakeEl({ closest: () => panels[ANCHORS[id]] }));
        return (nodes[id] ||= fakeEl());
      },
      addEventListener() {}, querySelector: () => null,
      querySelectorAll: sel => (sel === '.panel' ? Object.values(panels) : []),
    },
    window: { addEventListener() {}, location, CFG: {}, scrollTo() {} },
    location,
    console: { log() {}, warn() {}, error() {} },
    CFG: {}, URLSearchParams, Date, Math, isNaN, Number, Array,
    Event: class { constructor(type) { this.type = type; } },
    fetch: url => { requested.push(String(url));
                    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) }); },
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false }),
    setTimeout, clearTimeout,
  };
  ctx.globalThis = ctx;
  vm.runInNewContext(fs.readFileSync(APP_JS, 'utf8'), ctx);
  return { ctx, panels, nodes, requested };
}

const shownPanel = panels => Object.keys(panels).filter(name => !panels[name].hidden);

test('the bare URL lands on Home, and Home is the only panel shown', () => {
  const { ctx, panels } = loadApp('');
  assert.equal(ctx.currentTab(), 'home');
  assert.deepEqual(shownPanel(panels), ['home']);
});

test('a named tab still lands on itself, with or without its own state after `?`', () => {
  const { ctx } = loadApp('');
  for (const [hash, tab] of [['#search', 'search'], ['#resume', 'resume'],
                             ['#trends?company=greenhouse:acme', 'trends']]) {
    ctx.location.hash = hash;
    assert.equal(ctx.currentTab(), tab, hash);
  }
});

test('the retired #data, and any unknown hash, fall back to Home rather than a blank page', () => {
  const { ctx } = loadApp('');
  for (const hash of ['#data', '#nonsense', '#']) {
    ctx.location.hash = hash;
    assert.equal(ctx.currentTab(), 'home', hash);
  }
});

test('an in-page anchor lands on the tab that holds it', () => {
  const { ctx } = loadApp('');
  // The skip link: "Skip the filters, go to results" must not send the reader to Home.
  ctx.location.hash = '#results';
  assert.equal(ctx.currentTab(), 'search');
  // The match-score link on Search opens Home's section that explains it.
  ctx.location.hash = '#how-matching';
  assert.equal(ctx.currentTab(), 'home');
});

test('a deep link to another tab shows that tab at load, not Home', () => {
  const { panels } = loadApp('#resume');
  assert.deepEqual(shownPanel(panels), ['resume']);
});

test("Home's search box runs its words on the Search tab", () => {
  const { ctx, nodes, requested } = loadApp('');
  ctx.document.getElementById('home-q').value = 'backend engineer at a climate startup';
  let prevented = false;
  nodes['home-search'].fire('submit', { preventDefault() { prevented = true; } });
  assert.ok(prevented, 'the form must not reload the page');
  assert.equal(ctx.location.hash, '#search');
  assert.equal(nodes.q.value, 'backend engineer at a climate startup');
  assert.ok(requested.some(u => u.startsWith('/search?') &&
    new URLSearchParams(u.split('?')[1]).get('q') === 'backend engineer at a climate startup'),
    'the search runs with the words typed on Home');
});
