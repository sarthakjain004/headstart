/* The saved-jobs hand-off in the REAL src/headstart/ui/static/app.js — same harness shape as
 * app_search.test.js and app_trends.test.js: app.js is a browser script, evaluated in a vm
 * context over a stub DOM, so this exercises the shipped file rather than a copy of its logic.
 *
 * `window.savedJobs` is the only thing outside app.js that reads the stars. The Résumé tab's
 * "Tailor for a job" picker calls it to name a version after a job the visitor already saved
 * (ADR-0124), instead of fetching `/saved` a second time from a second module. That makes its
 * three answers a contract rather than an implementation detail, and this file states them:
 * null while there is no list, the rows once there is one, and a copy every time.
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
    // `value: ''` so the load-time `go()` (ADR-0074 — Search browses on load) reads an empty
    // query rather than throwing on `undefined.trim()`.
    innerHTML: '', textContent: '', hidden: false, value: '', checked: false,
    style: { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return this[k] ?? ''; },
             removeProperty(k) { delete this[k]; } },
    querySelector: () => null, querySelectorAll: () => [],
    setAttribute(k, v) { this[k] = v; }, getAttribute: () => null,
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    fire(type) { (handlers[type] || []).forEach(fn => fn.call(this, { type })); },
    dispatchEvent() {},
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle(c, on) { const want = on === undefined ? !classes.has(c) : !!on;
                      if (want) classes.add(c); else classes.delete(c); return want; } },
  };
}

const star = (id, extra) => ({
  job_id: id, title: 'Backend Engineer', company: 'Razorpay', url: 'https://example.test/' + id,
  location: 'Bengaluru', remote: false, salary: '', starred_at: '2026-09-02T09:00:00+00:00',
  open: true, ...extra,
});

/** A fresh evaluation of app.js. `saved` is what `GET /saved` answers with — an array for a
 *  signed-in account, or `{ status: 503 }` for a deployment that keeps none. Everything else
 *  answers `[]`, which is enough for the load-time search and `/me`. */
function loadApp(saved) {
  const nodes = {};
  const ctx = {
    document: {
      getElementById: id => (nodes[id] ||= fakeEl()),
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
    },
    window: { addEventListener() {}, location: { hash: '' }, CFG: {} },
    location: { hash: '' },
    console, CFG: {}, URLSearchParams, Date, Math, isNaN, Number, Array,
    Event: class { constructor(type) { this.type = type; } },
    fetch: url => {
      if (String(url) !== '/saved') return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      // `ok`, not just a body: `loadSaved` gives up on a non-ok response, which is how a
      // signed-out visitor and an unconfigured deployment both arrive here.
      return Array.isArray(saved)
        ? Promise.resolve({ ok: true, json: () => Promise.resolve(saved) })
        : Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ error: 'nope' }) });
    },
  };
  ctx.globalThis = ctx;
  vm.runInNewContext(fs.readFileSync(APP_JS, 'utf8'), ctx);
  return ctx;
}

/* One turn of the microtask queue is not enough: `loadSaved` awaits the fetch and then the
   body, so the rows land two ticks in. `setTimeout(0)` clears both. */
const settled = () => new Promise(r => setTimeout(r, 0));

test('the stars a signed-in account has are handed over, in the shape the picker reads', async () => {
  const ctx = loadApp([star('greenhouse:razorpay:9001'), star('lever:dreamsports:42')]);
  await settled();
  const rows = ctx.window.savedJobs();
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(j => j.job_id), ['greenhouse:razorpay:9001', 'lever:dreamsports:42']);
  // The four fields the picker names a version and draws a row from.
  assert.deepEqual(
    [rows[0].company, rows[0].title, rows[0].location, rows[0].starred_at],
    ['Razorpay', 'Backend Engineer', 'Bengaluru', '2026-09-02T09:00:00+00:00']);
});

test('no list is null, never an empty one — "nothing starred" is a different answer', async () => {
  // A 503 is what a signed-out visitor and a deployment with no account store both get.
  const ctx = loadApp(null);
  await settled();
  assert.strictEqual(ctx.window.savedJobs(), null,
    'a page with no saved-jobs list at all reports zero stars, so the picker offers an empty list');

  const signedIn = loadApp([]);
  await settled();
  assert.deepEqual(signedIn.window.savedJobs(), [],
    'an account with nothing starred is indistinguishable from having no account');
});

test('nothing is handed over before /saved has answered', () => {
  const ctx = loadApp([star('greenhouse:razorpay:9001')]);
  // Deliberately not awaited: this is the résumé tab opening while the fetch is still in flight.
  assert.strictEqual(ctx.window.savedJobs(), null,
    'an in-flight /saved reads as "nothing starred", which is a wrong answer rather than no answer');
});

test('the list handed over is a copy, so a reader cannot reorder the Saved tab', async () => {
  const ctx = loadApp([star('a'), star('b')]);
  await settled();
  // The picker sorts what it is handed, newest star first. If that were the page's own array,
  // the Saved tab's list would be reordered underneath it by a menu merely being opened.
  ctx.window.savedJobs().reverse().push(star('c'));
  assert.deepEqual(ctx.window.savedJobs().map(j => j.job_id), ['a', 'b']);
});
