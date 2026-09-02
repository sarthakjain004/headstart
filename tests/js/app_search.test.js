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
      querySelectorAll: () => [],
    },
    // app.js reads the page's config off `window.CFG` (the template sets it), so the stub
    // window carries it; the bare `CFG` global below is kept for any direct reference.
    window: { addEventListener() {}, location: { hash: '' }, CFG: cfg },
    location: { hash: '' },
    console, CFG: cfg, URLSearchParams, Date, Math, isNaN, Number, Array,
    fetch: url => {
      fetches.push(String(url));
      return Promise.resolve({ json: () => Promise.resolve(respond(String(url))) });
    },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { go, goToPage, page: () => page };';
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
