/* Search-tab pagination and browse-on-load (ADR-0074), run against the REAL
 * src/headstart/ui/static/app.js — same harness shape as app_trends.test.js: app.js is a
 * browser script, evaluated in a vm context with a stub DOM, so these tests exercise the
 * shipped file rather than a copy of its logic.
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
    innerHTML: '', textContent: '', hidden: false, value: '', checked: false,
    // `style` needs the methods the code actually calls on it. A bare `{}` let
    // `style.setProperty` throw asynchronously, and Node reports that as an
    // unhandledRejection AFTER the test ends — so 24 tests failed at once with no
    // useful location while the real cause (a custom property set for the two-column
    // results grid) sat in another file entirely.
    style: { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return this[k] ?? ''; }, removeProperty(k) { delete this[k]; } },
    querySelectorAll: () => [],
    setAttribute(k, v) { this[k] = v; }, getAttribute: k => null,
    // Listeners are RECORDED rather than dropped, and `fire` replays one — the only way to
    // test that a control is wired to the right thing. A no-op addEventListener made every
    // binding at the foot of app.js untestable, which is how a currency picker that never
    // re-searched shipped.
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    fire(type) { (handlers[type] || []).forEach(fn => fn.call(this, { type })); },
    dispatchEvent() {},
    classList: {
      add: c => classes.add(c), remove: c => classes.delete(c),
      contains: c => classes.has(c),
      toggle(c, on) { const want = on === undefined ? !classes.has(c) : !!on;
                      if (want) classes.add(c); else classes.delete(c); return want; },
    },
  };
}

const job = (id, extra) => ({
  id, title: 'Backend Engineer', company: 'Acme', location: 'Berlin', remote: true,
  employment_type: 'full-time', min_years: 3, salary: null, ats: 'darwinbox',
  posted_at: '2026-08-01', first_seen: '2026-08-10T00:00:00+00:00',
  url: 'https://example.test/' + id, score: 0.8, ...extra,
});

/** A fresh evaluation of app.js per test. `cfg` is what index() puts on window.CFG — it must
 * be in place BEFORE the context is built: vm contextifies the object, so mutating it afterwards
 * from outside is not seen inside. `respond(url)` decides what `/search` answers —
 * tests set it per case, so a page's content can depend on what was actually requested
 * (page, q) the same way the real server's pagination does. */
function loadApp(respond, cfg = {}) {
  const nodes = {};
  const fetches = [];
  const ctx = {
    document: {
      getElementById: id => (nodes[id] ||= fakeEl()),
      addEventListener() {},
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    // app.js reads the page's config off `window.CFG` (the template sets it), so the stub
    // window carries it; the bare `CFG` global below is kept for any direct reference.
    window: { addEventListener() {}, location: { hash: '' }, CFG: cfg },
    location: { hash: '' },
    console, CFG: cfg, URLSearchParams, Date, Math, isNaN, Number, Array,
    Event: class { constructor(type) { this.type = type; } },
    fetch: url => {
      fetches.push(String(url));
      return Promise.resolve({ json: () => Promise.resolve(respond(String(url))) });
    },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { go, goToPage, page: () => page, jobCard, savedRow,'
    + ' salStop, SALARY_STOPS, stops: () => SALARY_STOPS, sync: syncSalarySlider, slide: salSlide,'
    + ' dismiss: dismissRow, dismissed };';
  vm.runInNewContext(src, ctx);
  return { nodes, fetches, t: ctx.__t, ctx };
}

/** The server's Keyword-filter scope map as index() puts it on CFG (ADR-0104). */
const SCOPES = { keyword_scopes: { title: false, description: true, both: true },
                 keyword_default_scope: 'title' };

/** Set a control's value BEFORE go() runs. The stub DOM creates a node on first lookup, and
 * `kwin` is first looked up inside go() — so assigning `.value` on the not-yet-created node would
 * write to undefined. This materialises the fake the way the page would already have. */
const set = (nodes, id, value) => { (nodes[id] ||= fakeEl()).value = value; };

function qs(url) {
  return Object.fromEntries(new URL(url, 'http://x').searchParams);
}

test('an empty query browses on load instead of showing a static empty state', async () => {
  // Page load also fires /me and, in this harness, /saved (CAN_STAR reads truthy against the
  // stub DOM regardless of real config) — unrelated to Search, so this checks the search
  // fetch happened correctly rather than asserting it was the only one.
  const { fetches, nodes } = loadApp(() => [job('a'), job('b')]);
  await new Promise(r => setTimeout(r, 0));   // let the load-time go() promise settle
  const searchFetch = fetches.find(f => f.startsWith('/search?'));
  assert.ok(searchFetch, `no /search call among: ${fetches}`);
  assert.strictEqual(qs(searchFetch).q, '');
  assert.ok(nodes.results.innerHTML.includes('Backend Engineer'));
});

test('go() always requests page 1, k=20, regardless of prior navigation', async () => {
  const { t, fetches } = loadApp(() => [job('a')]);
  await t.goToPage(3);
  fetches.length = 0;
  await t.go();
  assert.strictEqual(qs(fetches[0]).page, '1');
  assert.strictEqual(qs(fetches[0]).k, '20');
  assert.strictEqual(t.page(), 1);
});

test('goToPage sends the requested page and updates the page counter', async () => {
  const { t, fetches } = loadApp(() => [job('a')]);
  await t.goToPage(4);
  assert.strictEqual(qs(fetches.at(-1)).page, '4');
  assert.strictEqual(t.page(), 4);
});

test('goToPage clamps below 1 and above the 20-page ceiling', async () => {
  const { t, fetches } = loadApp(() => [job('a')]);
  await t.goToPage(0);
  assert.strictEqual(qs(fetches.at(-1)).page, '1');
  await t.goToPage(999);
  assert.strictEqual(qs(fetches.at(-1)).page, '20');
});

test('a full page enables Next; a short page disables it', async () => {
  const full = Array.from({ length: 20 }, (_, i) => job('j' + i));
  const { t, nodes } = loadApp(() => full);
  await t.go();
  assert.ok(!nodes.pager.innerHTML.includes('Next ›" disabled'));
  const { t: t2, nodes: nodes2 } = loadApp(() => full.slice(0, 5));
  await t2.go();
  assert.match(nodes2.pager.innerHTML, /disabled[^>]*onclick="goToPage\(2\)"|onclick="goToPage\(2\)"[^>]*disabled/);
});

test('page 1 never shows a Prev button as enabled', async () => {
  const { t, nodes } = loadApp(() => [job('a')]);
  await t.go();
  assert.match(nodes.pager.innerHTML, /disabled[^>]*onclick="goToPage\(0\)"|onclick="goToPage\(0\)"[^>]*disabled/);
});

test('an empty page 1 shows "nothing matched"; an empty later page shows "no more jobs"', async () => {
  const { t, nodes } = loadApp(() => []);
  await t.go();
  assert.ok(nodes.results.innerHTML.includes('Nothing matched'));

  const { t: t2, nodes: nodes2 } = loadApp(url => (qs(url).page === '1' ? [job('a')] : []));
  await t2.goToPage(2);
  assert.ok(nodes2.results.innerHTML.includes('No more jobs'));
});

test('a browsed row (score: null) renders no match ring; a ranked row does', async () => {
  const { t, nodes } = loadApp(() => [job('a', { score: null }), job('b', { score: 0.77 })]);
  await t.go();
  const cards = nodes.results.innerHTML.split('<div class="card').slice(1);
  assert.strictEqual(cards.length, 2);
  assert.ok(!cards[0].includes('class="ring"'));
  assert.ok(cards[1].includes('class="ring"'));
});

test('an unranked row still reserves the match column, so the star column never moves', async () => {
  // The columns landing at the same x on every row is what the row layout buys; two grids —
  // one with the match track and one without — cannot align across a browse and a search.
  const { t, nodes } = loadApp(() => [job('a', { score: null })]);
  await t.go();
  assert.ok(nodes.results.innerHTML.includes('<div class="match" aria-hidden="true"></div>'));
});

test('an invalid-filter response (non-array) shows the filter error, not a crash', async () => {
  const { t, nodes } = loadApp(() => ({ error: 'invalid filter' }));
  await t.go();
  assert.ok(nodes.results.innerHTML.includes("isn't valid"));
});

test('a description-only (Tier-2) salary still reaches the pay column', async () => {
  // `salary` (the raw display string) is only ever populated from a scraper's own structured
  // field — most of this initiative's own measured salary coverage is Tier-2, description-
  // mined (ADR-0082), which only ever reaches min_salary_annual/max_salary_annual/
  // salary_currency. A Job with those set but salary null must still show its pay.
  const { t, nodes } = loadApp(() => [
    job('a', { salary: null, min_salary_annual: 90000, max_salary_annual: 110000, salary_currency: 'EUR' }),
  ]);
  await t.go();
  assert.ok(nodes.results.innerHTML.includes('<div class="pay">EUR 90,000–110,000/yr</div>'));
});

test('a row with no salary signal at all still renders the pay column, as an em dash', async () => {
  // The column is reserved either way: an empty cell keeps every other row's pay on the same
  // x, and "we were not told" is a different answer from "this pays nothing".
  const { t, nodes } = loadApp(() => [job('a', { salary: null })]);
  await t.go();
  assert.ok(!nodes.results.innerHTML.includes('<div class="pay">EUR'));
  assert.ok(nodes.results.innerHTML.includes('class="nopay"'));
});


// ── The Keyword filter and its disclaimer (ADR-0104) ─────────────────────────────────────────

test('a keyword is sent, and its scope only when it is not the default', async () => {
  const { nodes, fetches, t } = loadApp(() => [], SCOPES);
  set(nodes, 'kw', 'kubernetes'); set(nodes, 'kwin', 'title');
  await t.go();
  const lastSearch = () => qs(fetches.filter(u => u.startsWith('/search?')).at(-1));
  let q = lastSearch();                      // app.js also browses on load; read OUR request
  assert.equal(q.kw, 'kubernetes');
  assert.equal(q.kw_in, undefined);           // default scope: omitted, the server assumes it
  fetches.length = 0;
  set(nodes, 'kwin', 'description');
  await t.go();
  q = lastSearch();
  assert.equal(q.kw_in, 'description');
});

test('the disclaimer is silent for the title scope', async () => {
  const { nodes, t } = loadApp(url => url.startsWith('/facets')
    ? { total: 12, facets: {}, blocking: null, description_coverage: { covered: 42, total: 100 } } : [], SCOPES);
  set(nodes, 'kwin', 'title');
  await t.go();
  assert.equal(nodes.kwnote.textContent, '');
});

test('a description-bearing scope shows the coverage against the other filters\' total, not the header\'s', async () => {
  const { nodes, t } = loadApp(url => url.startsWith('/facets')
    ? { total: 12, facets: {}, blocking: null, description_coverage: { covered: 42, total: 100 } } : [], SCOPES);
  for (const scope of ['description', 'both']){       // both come from the map, not a name
    set(nodes, 'kwin', scope);
    await t.go();
    assert.match(nodes.kwnote.textContent, /42 of the 100 jobs your other filters match/);
    assert.doesNotMatch(nodes.kwnote.textContent, /of the 12/);   // the keyword-lifted total, not the header's
    assert.match(nodes.kwnote.textContent, /no stored description/);
  }
});

test('a null coverage means the column does not exist yet, not zero', async () => {
  const { nodes, t } = loadApp(url => url.startsWith('/facets')
    ? { total: 12, facets: {}, blocking: null, description_coverage: null } : [], SCOPES);
  set(nodes, 'kwin', 'description');
  await t.go();
  assert.match(nodes.kwnote.textContent, /isn't available yet/);
  assert.doesNotMatch(nodes.kwnote.textContent, /0 of/);
});

test('a failed /facets replaces a stale note with the plain fact, never leaves the old numbers', async () => {
  let facetsOk = true;
  const { nodes, t } = loadApp(url => {
    if (!url.startsWith('/facets')) return [];
    if (!facetsOk) throw new Error('down');       // .catch(() => null) in fetchPage
    return { total: 12, facets: {}, blocking: null, description_coverage: { covered: 42, total: 100 } };
  }, SCOPES);
  set(nodes, 'kwin', 'description');
  await t.go();
  assert.match(nodes.kwnote.textContent, /42 of the 100/);
  facetsOk = false;
  await t.go();
  assert.doesNotMatch(nodes.kwnote.textContent, /42 of the 100/);   // not stale
  assert.match(nodes.kwnote.textContent, /not every job has one/);
});


// ── One card for Search, Matches and Saved ───────────────────────────────────────────────────

test('a saved job renders through the same card as a search result', async () => {
  // Saved used to build its own markup and drifted: no external-link glyph, the salary as a
  // tag rather than in the pay column, a `.hd` wrapper no stylesheet has carried since the row
  // layout landed. The record's shape is SavedJob.to_dict() plus the route's `open`.
  const { t } = loadApp(() => []);
  const html = t.jobCard(t.savedRow({
    id: 'r1', job_id: 'greenhouse:acme:7', title: 'Backend Engineer', company: 'Acme',
    location: 'Berlin', url: 'https://example.test/x', remote: true,
    salary: 'EUR 90,000/yr', starred_at: '2026-09-01T00:00:00+00:00', open: true,
  }), 0);
  assert.ok(html.includes('<div class="pay">EUR 90,000/yr</div>'));
  assert.ok(html.includes('class="ext"'));                       // it leaves for the employer
  assert.ok(html.includes("opens on the employer's own board"));
  assert.ok(html.includes('class="tag rem"'));
  assert.ok(html.includes('class="star on"'));
});

test('a saved job the index has dropped is marked closed — from `open`, not a `closed` key', async () => {
  // The server answers `open: false`; nothing in the payload is called `closed`. Reading the
  // wrong key here is silent — the tag simply never appears, under a caption promising it.
  const { t } = loadApp(() => []);
  const rec = { job_id: 'lever:acme:1', title: 'X', company: 'Y', url: 'https://example.test/x',
                location: '', remote: false, salary: '', starred_at: '', open: false };
  assert.ok(t.jobCard(t.savedRow(rec), 0).includes('class="tag closed"'));
  assert.ok(t.jobCard(t.savedRow({ ...rec, open: true }), 0).includes('class="tag closed"') === false);
});

test('a dismissed row is marked, not dropped — the server\'s own count stays true', async () => {
  const { t, nodes } = loadApp(() => [job('a'), job('b')]);
  await t.go();
  t.dismissed.add('a');
  await t.go();
  const cards = nodes.results.innerHTML.split('<div class="card').slice(1);
  assert.strictEqual(cards.length, 2, 'both rows are still rendered');
  assert.ok(cards[0].includes('dismissed'));
  assert.ok(!cards[1].includes('dismissed'));
  t.dismissed.delete('a');
});

// ── The bracket's cross-currency labels (ADR-0117) ───────────────────────────────────────────

/** The rate table as index() puts it on CFG — the same object `headstart.fx.table()` returns. */
const FX = { fx: { base: 'USD', as_of: '2024-06-01', rates: { USD: 1.0, INR: 83.0 } } };
const inrJob = () => job('a', { salary: null, min_salary_annual: 2800000,
                                max_salary_annual: 4200000, salary_currency: 'INR' });

test('a row priced in another currency says what it comes to in the bracket\'s currency', async () => {
  const { t, nodes } = loadApp(() => [inrJob()], { ...SCOPES, ...FX });
  set(nodes, 'salmin', '60000');
  set(nodes, 'salcur', 'USD');
  await t.go();
  // Three significant figures, never finer than a thousand: the rates are approximate and
  // dated, and 2,800,000 / 83 = 33,734.94 printed to the dollar would claim otherwise.
  assert.match(nodes.results.innerHTML, /≈ USD 34,000–51,000/);
  // …and the date of those rates is on the page beside them, not only inside the filter panel.
  assert.match(nodes.fxnote.textContent, /rates from 2024-06-01/);
  assert.match(nodes.fxnote.textContent, /not cost of living/);
});

test('nothing is converted without a bracket, or without a rate table', async () => {
  // No bound set: the picker has a default, so reading it alone would label every row on a
  // page nobody filtered by pay.
  const { t, nodes } = loadApp(() => [inrJob()], { ...SCOPES, ...FX });
  set(nodes, 'salcur', 'USD');
  await t.go();
  assert.ok(!nodes.results.innerHTML.includes('class="conv"'));
  assert.strictEqual(nodes.fxnote.textContent, '');

  // No table — the server could not read it either, so its own bracket did not convert
  // anything. Printing a conversion here would describe a query that never ran.
  const { t: t2, nodes: n2 } = loadApp(() => [inrJob()], SCOPES);
  set(n2, 'salmin', '60000');
  set(n2, 'salcur', 'USD');
  await t2.go();
  assert.ok(!n2.results.innerHTML.includes('class="conv"'));
  assert.strictEqual(n2.fxnote.textContent, '');
});

test('a row already in the bracket\'s currency is left alone', async () => {
  const { t, nodes } = loadApp(() => [job('a', { salary: null, min_salary_annual: 120000,
    max_salary_annual: 160000, salary_currency: 'USD' })], { ...SCOPES, ...FX });
  set(nodes, 'salmin', '60000');
  set(nodes, 'salcur', 'USD');
  await t.go();
  assert.ok(!nodes.results.innerHTML.includes('class="conv"'));
});

// ── The salary bracket's slider ──────────────────────────────────────────────────────────────

test('the scale is restated in the bracket\'s currency, at a one-significant-figure rate', () => {
  // 0-500,000 is a USD ladder. Left at those numbers an INR bracket topped out at ₹5,00,000,
  // below entry-level pay in the market this index covers best. The rate is rounded to one
  // significant figure (83 → 80) so every stop stays a round number in the currency printed.
  const { t, nodes } = loadApp(() => [], { ...SCOPES, ...FX });
  set(nodes, 'salcur', 'INR');
  t.sync();
  assert.strictEqual(t.stops()[t.stops().length - 1], 40000000);
  assert.strictEqual(nodes.salcap1.textContent, '40,000,000+');
  set(nodes, 'salcur', 'USD');
  t.sync();
  assert.strictEqual(t.stops()[t.stops().length - 1], 500000);
  assert.strictEqual(nodes.salcap1.textContent, '500,000+');
});

test('with no rate table the scale stays as written, rather than guessing a factor', () => {
  const { t, nodes } = loadApp(() => [], SCOPES);
  set(nodes, 'salcur', 'INR');
  t.sync();
  assert.strictEqual(t.stops()[t.stops().length - 1], 500000);
});

test('a typed figure rests on the nearest stop, and one past the scale parks on the top', () => {
  const { t } = loadApp(() => []);
  const top = t.SALARY_STOPS.length - 1;
  assert.strictEqual(t.SALARY_STOPS[t.salStop(137000)], 140000);   // nearest, not floor
  assert.strictEqual(t.salStop(0), 0);
  assert.strictEqual(t.salStop(9_000_000), top);
  assert.strictEqual(t.SALARY_STOPS[top], 500000);
});

test('the slider follows the number fields and never rewrites what was typed', () => {
  const { t, nodes } = loadApp(() => []);
  nodes.salmin.value = '137000';
  nodes.salmax.value = '';
  t.sync();
  assert.strictEqual(nodes.salmin.value, '137000', 'the typed figure is untouched');
  assert.strictEqual(nodes.salrmin.value, String(t.salStop(137000)));
  assert.strictEqual(nodes.salrmax.value, String(t.SALARY_STOPS.length - 1));
  assert.ok(nodes.salread.textContent.includes('137,000'));
  assert.ok(nodes.salread.textContent.includes('no maximum'));
});

test('changing the currency re-runs the search — the label and the results cannot disagree', async () => {
  // The bracket is compared ACROSS currencies (ADR-0117), so the picker is part of the
  // where-clause: switching it while a bound is set has to re-query. It used to only relabel
  // the read-out, leaving the previous currency's rows on screen under the new currency's name.
  const { t, nodes, fetches } = loadApp(() => []);
  await t.go();
  set(nodes, 'salmin', '60000');
  set(nodes, 'salcur', 'INR');
  fetches.length = 0;
  nodes.salcur.fire('change');
  await new Promise(r => setTimeout(r, 0));
  const search = fetches.filter(u => u.startsWith('/search?')).at(-1);
  assert.ok(search, `no /search after the currency changed: ${fetches}`);
  assert.strictEqual(qs(search).salary_currency, 'INR');
});

test('the handles cannot cross, and an end stop means unbounded rather than zero', () => {
  const { t, nodes } = loadApp(() => []);
  nodes.salrmin.value = '30';
  nodes.salrmax.value = '12';
  t.slide('min');
  assert.strictEqual(nodes.salrmin.value, '12');
  assert.strictEqual(nodes.salrmax.value, '12');

  nodes.salrmin.value = '0';
  nodes.salrmax.value = String(t.SALARY_STOPS.length - 1);
  t.slide('max');
  // Blank, not "0" and not "500000": a bound the server would compile into a clause is a
  // different filter from no bound at all.
  assert.strictEqual(nodes.salmin.value, '');
  assert.strictEqual(nodes.salmax.value, '');
});

test('the hide control is drawn on the Search list only', async () => {
  // `rows.map(jobCard)` would pass the array itself as the third argument and put a × on every
  // list — including Saved, where the gesture is unstarring and a second control for the same
  // intent would disagree with it about which list the row is in.
  const { t, nodes } = loadApp(() => [job('a')]);
  await t.go();
  assert.ok(nodes.results.innerHTML.includes('data-dismiss='));
  assert.ok(!t.jobCard(job('a'), 0, false).includes('data-dismiss='));
});
