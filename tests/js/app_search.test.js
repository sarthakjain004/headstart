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
  return {
    innerHTML: '', textContent: '', hidden: false, style: {}, value: '', checked: false,
    querySelectorAll: () => [],
    setAttribute() {}, getAttribute: () => null, addEventListener() {},
  };
}

const job = (id, extra) => ({
  id, title: 'Backend Engineer', company: 'Acme', location: 'Berlin', remote: true,
  employment_type: 'full-time', min_years: 3, salary: null, ats: 'darwinbox',
  posted_at: '2026-08-01', first_seen: '2026-08-10T00:00:00+00:00',
  url: 'https://example.test/' + id, score: 0.8, ...extra,
});

/** A fresh evaluation of app.js per test. `respond(url)` decides what `/search` answers —
 * tests set it per case, so a page's content can depend on what was actually requested
 * (page, q) the same way the real server's pagination does. */
function loadApp(respond) {
  const nodes = {};
  const fetches = [];
  const ctx = {
    document: {
      getElementById: id => (nodes[id] ||= fakeEl()),
      addEventListener() {},
      querySelectorAll: () => [],
    },
    window: { addEventListener() {}, location: { hash: '' } },
    location: { hash: '' },
    console, CFG: {}, URLSearchParams, Date, Math, isNaN, Number, Array,
    fetch: url => {
      fetches.push(String(url));
      return Promise.resolve({ json: () => Promise.resolve(respond(String(url))) });
    },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { go, goToPage, page: () => page };';
  vm.runInNewContext(src, ctx);
  return { nodes, fetches, t: ctx.__t };
}

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
  const cards = nodes.results.innerHTML.split('<div class="card"').slice(1);
  assert.strictEqual(cards.length, 2);
  assert.ok(!cards[0].includes('class="match"'));
  assert.ok(cards[1].includes('class="match"'));
});

test('an invalid-filter response (non-array) shows the filter error, not a crash', async () => {
  const { t, nodes } = loadApp(() => ({ error: 'invalid filter' }));
  await t.go();
  assert.ok(nodes.results.innerHTML.includes("isn't valid"));
});

test('a description-only (Tier-2) salary still renders a pay tag', async () => {
  // `salary` (the raw display string) is only ever populated from a scraper's own structured
  // field — most of this initiative's own measured salary coverage is Tier-2, description-
  // mined (ADR-0082), which only ever reaches min_salary_annual/max_salary_annual/
  // salary_currency. A Job with those set but salary null must still show a pay tag.
  const { t, nodes } = loadApp(() => [
    job('a', { salary: null, min_salary_annual: 90000, max_salary_annual: 110000, salary_currency: 'EUR' }),
  ]);
  await t.go();
  assert.ok(nodes.results.innerHTML.includes('class="tag pay"'));
  assert.ok(nodes.results.innerHTML.includes('EUR 90,000–110,000/yr'));
});

test('a row with no salary signal at all renders no pay tag', async () => {
  const { t, nodes } = loadApp(() => [job('a', { salary: null })]);
  await t.go();
  assert.ok(!nodes.results.innerHTML.includes('class="tag pay"'));
});
