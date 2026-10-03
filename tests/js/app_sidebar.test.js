/* app.js's sidebar fold (ADR-0249), run against the REAL src/headstart/ui/static/app.js.
 *
 * Same harness shape as the app_{tab}.test.js files. The sidebar is not a tab, so it gets a file
 * of its own, named for what it tests. base.html applies a stored fold to <html> before the first
 * paint; app.js owns the button: its state has to match <html> at load, and every flip is both
 * shown and remembered — or, where storage is blocked, still shown.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui', 'static', 'app.js');

function fakeEl() {
  const classes = new Set();
  const handlers = {};
  return {
    innerHTML: '', textContent: '', hidden: false, value: '', checked: false, dataset: {}, options: [],
    style: { setProperty() {}, getPropertyValue: () => '', removeProperty() {} },
    querySelector: () => null, querySelectorAll: () => [], closest: () => null,
    setAttribute(k, v) { this[k] = v; }, getAttribute(k) { return this[k] ?? null; },
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); }, dispatchEvent() {},
    fire(type) { (handlers[type] || []).forEach(fn => fn({ type })); },
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle(c, on) { const want = on === undefined ? !classes.has(c) : !!on;
                      if (want) classes.add(c); else classes.delete(c); return want; } },
  };
}

/** app.js at load. `folded` is what base.html's pre-paint script left on <html>; `storage` is
 *  the saved values, or `null` for a browser that refuses storage altogether. */
function loadApp({ folded = false, storage = {} } = {}) {
  const nodes = {};
  const root = { dataset: folded ? { nav: 'collapsed' } : {} };
  const ctx = {
    document: {
      getElementById: id => (id.startsWith('panel-') && id !== 'panel-home' ? null : (nodes[id] ||= fakeEl())),
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      documentElement: root,
    },
    window: { addEventListener() {}, location: { hash: '' }, CFG: {}, scrollTo() {} },
    location: { hash: '' },
    console: { log() {}, warn() {}, error() {} },
    CFG: {}, URLSearchParams, Date, Math, isNaN, Number, Array,
    Event: class { constructor(type) { this.type = type; } },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) }),
    localStorage: {
      getItem(k) { if (storage === null) throw new Error('blocked'); return storage[k] ?? null; },
      setItem(k, v) { if (storage === null) throw new Error('blocked'); storage[k] = v; },
    },
    matchMedia: () => ({ matches: false }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    setTimeout, clearTimeout,
  };
  ctx.globalThis = ctx;
  vm.runInNewContext(fs.readFileSync(path.join(path.dirname(APP_JS), 'navigation.js'), 'utf8') + '\n' + fs.readFileSync(APP_JS, 'utf8'), ctx);
  return { root, button: nodes['nav-toggle'], storage };
}

test('the sidebar opens unfolded, and its button says so', () => {
  const { root, button } = loadApp();
  assert.equal(root.dataset.nav, undefined);
  assert.equal(button['aria-expanded'], 'true');
  assert.equal(button['data-tip'], 'Collapse navigation', 'the tooltip says what a click will do');
});

test('a fold applied before the first paint is what the button reports at load', () => {
  const { button } = loadApp({ folded: true });
  assert.equal(button['aria-expanded'], 'false');
  assert.equal(button['data-tip'], 'Expand navigation');
});

test('each click flips the sidebar, and the choice is remembered for the next visit', () => {
  const { root, button, storage } = loadApp();
  button.fire('click');
  assert.equal(root.dataset.nav, 'collapsed');
  assert.equal(button['aria-expanded'], 'false');
  assert.equal(button['aria-label'], undefined,
    'a disclosure keeps the one name base.html gives it; only aria-expanded flips');
  assert.equal(storage['hs.navCollapsed'], '1');
  button.fire('click');
  assert.equal(root.dataset.nav, undefined);
  assert.equal(button['aria-expanded'], 'true');
  assert.equal(storage['hs.navCollapsed'], '', 'unfolded is stored as falsy, which base.html reads as open');
});

test('where storage is blocked the button still folds the sidebar, it just is not remembered', () => {
  const { root, button } = loadApp({ storage: null });
  button.fire('click');
  assert.equal(root.dataset.nav, 'collapsed');
  assert.equal(button['aria-expanded'], 'false');
});
