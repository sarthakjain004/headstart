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
    innerHTML: '', textContent: '', hidden: false, value: '', checked: false, dataset: {}, options: [],
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
  // Recorded, like an element's: the capped-row controls are one delegated document listener.
  const docHandlers = {};
  const ctx = {
    document: {
      getElementById: id => (nodes[id] ||= fakeEl()),
      addEventListener(type, fn) { (docHandlers[type] ||= []).push(fn); },
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
      return Promise.resolve({ ok: String(url) === '/sets', json: () => Promise.resolve(respond(String(url))) });
    },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { go, goToPage, loadSets, runSet, page: () => page, jobCard, savedRow,'
    + ' salStop, SALARY_STOPS, stops: () => SALARY_STOPS, sync: syncSalarySlider, slide: salSlide,'
    + ' dismiss: dismissRow, dismissed, handleSetAction, searchCompany, dropFilter, readSearchHash };';
  vm.runInNewContext(src, ctx);
  return { nodes, fetches, t: ctx.__t, ctx, docHandlers };
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

for (const staleFailure of [false, true]) {
  test(`a superseded search cannot replace newer results (${staleFailure ? 'error' : 'success'})`, async () => {
    const pending = {};
    const { nodes, t } = loadApp(url => {
      if (url.startsWith('/facets?')) return { total: 1 };
      if (!url.startsWith('/search?') || !qs(url).q) return [];
      return new Promise((resolve, reject) => { pending[qs(url).q] = { resolve, reject }; });
    });
    await new Promise(resolve => setTimeout(resolve, 0));
    set(nodes, 'q', 'old'); const old = t.go();
    set(nodes, 'q', 'new'); const newer = t.go();
    await new Promise(resolve => setTimeout(resolve, 0));
    pending.new.resolve([job('new', { title: 'NEW_RESULT' })]);
    await newer;
    if (staleFailure) pending.old.reject(new Error('old request failed'));
    else pending.old.resolve([job('old', { title: 'OLD_RESULT' })]);
    await old;
    assert.ok(nodes.results.innerHTML.includes('NEW_RESULT'));
    assert.ok(!nodes.results.innerHTML.includes('OLD_RESULT'));
    assert.strictEqual(nodes.q.value, 'new');
  });
}

test('switching Saved sets keeps the newer matches when the old request finishes last', async () => {
  const pending = {};
  const { nodes, t } = loadApp(url => {
    if (url === '/sets') return ['initial', 'old', 'new'].map(id => ({ id, name: id, query: id === 'initial' ? '' : id }));
    if (!url.startsWith('/search?') || !qs(url).q) return [];
    return new Promise(resolve => { pending[qs(url).q] = resolve; });
  });
  await t.loadSets();
  const old = t.runSet('old'), newer = t.runSet('new');
  await new Promise(resolve => setTimeout(resolve, 0));
  pending.new([job('new', { title: 'NEW_MATCH' })]); await newer;
  pending.old([job('old', { title: 'OLD_MATCH' })]); await old;
  assert.ok(nodes['matches-results'].innerHTML.includes('NEW_MATCH'));
  assert.ok(!nodes['matches-results'].innerHTML.includes('OLD_MATCH'));
  assert.ok(nodes['matches-msg'].textContent.includes('“new”'));
});

test('a refused email toggle stays on screen — the set re-run does not overwrite it', async () => {
  // The reload after the POST re-ran the set without awaiting it, so its "N matches" landed
  // one /search later on top of the refusal — an ✉ that did not turn on, and no reason why.
  const later = ms => new Promise(r => setTimeout(r, ms));
  const { nodes, t } = loadApp(url => {
    if (url === '/sets') return [{ id: 's1', name: 'Backend', query: 'backend', emails: false }];
    if (url.endsWith('/email')) return { error: 'email alerts are invite-only — ask for access' };
    if (url.startsWith('/search?') && qs(url).q === 'backend') return later(10).then(() => [job('a')]);
    return [];
  });
  await t.loadSets();
  await later(20);
  await t.handleSetAction('email', 's1');
  await later(20);
  assert.strictEqual(nodes['matches-msg'].textContent, 'email alerts are invite-only — ask for access');
});

test('late facets cannot replace newer counts or release an old search render', async () => {
  let oldFacets;
  const { nodes, t } = loadApp(url => {
    const q = qs(url).q;
    if (url.startsWith('/facets?')) {
      if (q === 'old') return new Promise(resolve => { oldFacets = resolve; });
      return { total: 2, facets: { ats: [{ value: 'lever', count: 2 }] } };
    }
    return url.startsWith('/search?') ? [job(q || 'initial', { title: (q || 'initial').toUpperCase() })] : [];
  });
  await new Promise(resolve => setTimeout(resolve, 0));
  nodes.ats.options = [{ value: 'lever', textContent: 'Lever', dataset: {} }];
  set(nodes, 'q', 'old'); const old = t.go();
  await new Promise(resolve => setTimeout(resolve, 0));
  set(nodes, 'q', 'new'); await t.go();
  const newestCount = nodes.ats.options[0].textContent;
  oldFacets({ total: 99, facets: { ats: [{ value: 'lever', count: 99 }] } });
  await old;
  assert.strictEqual(nodes.ats.options[0].textContent, newestCount);
  assert.ok(nodes.results.innerHTML.includes('NEW'));
  assert.ok(!nodes.results.innerHTML.includes('OLD'));
});

test('search rows paint before a slow facet count finishes', async () => {
  let resolveFacets;
  const { nodes, t } = loadApp(url => {
    const q = qs(url).q;
    if (url.startsWith('/facets?') && q === 'measured') {
      return new Promise(resolve => { resolveFacets = resolve; });
    }
    if (url.startsWith('/facets?')) return { total: 1, facets: {} };
    return url.startsWith('/search?') ? [job(q || 'initial', { title: (q || 'initial').toUpperCase() })] : [];
  });
  await new Promise(resolve => setTimeout(resolve, 0));
  set(nodes, 'q', 'measured');
  const pending = t.go();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(nodes.results.innerHTML.includes('MEASURED'));
  assert.strictEqual(nodes.n.textContent, '1 result');

  resolveFacets({ total: 42, facets: {} });
  await pending;
  assert.ok(nodes.n.textContent.includes('of 42 matching your filters'));
});

test('deleting a Saved set invalidates its in-flight matches', async () => {
  let resolveOld, sets = [{ id: 'old', name: 'old', query: 'old' }];
  const { nodes, t } = loadApp(url => {
    if (url === '/sets') return sets;
    if (url.startsWith('/search?') && qs(url).q === 'old') return new Promise(resolve => { resolveOld = resolve; });
    return [];
  });
  await t.loadSets();
  await new Promise(resolve => setTimeout(resolve, 0));
  sets = [];
  await t.loadSets();
  resolveOld([job('old', { title: 'DELETED_SET_RESULT' })]);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(nodes['matches-results'].innerHTML.includes('No saved sets yet'));
  assert.ok(!nodes['matches-results'].innerHTML.includes('DELETED_SET_RESULT'));
});

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

test('Prev/Next page the search that ran, not an edit that was never submitted', async () => {
  // The pager re-read the query box and the rail live, so a query typed (or a box ticked)
  // without pressing Search went out at page 2 — and the first 20 rows of it were never shown.
  // The lines describing the rows follow the same snapshot, so the screen describes what is
  // actually being paged.
  const { t, nodes, fetches } = loadApp(url => (url.startsWith('/facets?') ? { total: 500 }
    : Array.from({ length: 20 }, (_, i) => job(qs(url).page + '-' + i))));
  await t.go();                                   // a browse: empty query, no filters
  set(nodes, 'q', 'data scientist');
  nodes.remote.checked = true;
  await t.goToPage(2);
  const sent = qs(fetches.filter(f => f.startsWith('/search?')).pop());
  assert.strictEqual(sent.page, '2');
  assert.strictEqual(sent.q, '', 'the unsubmitted query went out');
  assert.ok(!('remote' in sent), 'the unsubmitted filter went out');
  assert.strictEqual(nodes.active.innerHTML, '', 'the chips describe a filter nothing applied');
  assert.ok(/no search yet/.test(nodes.kind.textContent), 'the kind line: ' + nodes.kind.textContent);
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

test('an expired session says so, rather than blaming a filter', async () => {
  // The sign-in wall answers every /search with 401 {"error": "sign in first"} once the session
  // ends (measured on the live Space), and a non-array body read as an invalid filter — so the
  // user cleared filters that were never the problem.
  const { t, nodes, ctx } = loadApp(url => (url === '/sets'
    ? [{ id: 's1', name: 'Backend', query: 'backend' }] : []));
  const base = ctx.fetch;
  ctx.fetch = url => (String(url).startsWith('/search?')
    ? Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({ error: 'sign in first' }) })
    : base(url));
  await t.go();
  assert.ok(/session expired/i.test(nodes.results.innerHTML), 'Search says: ' + nodes.results.innerHTML);
  await t.loadSets();
  await new Promise(r => setTimeout(r, 0));
  assert.ok(/session expired/i.test(nodes['matches-msg'].textContent),
    'Matches says: ' + nodes['matches-msg'].textContent);
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
  set(nodes, 'kw', 'rust'); set(nodes, 'kwin', 'description');
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

test('a description scope with no keyword asks and says nothing', async () => {
  const { nodes, t } = loadApp(url => url.startsWith('/facets')
    ? { total: 12, facets: {}, blocking: null, description_coverage: null } : [], SCOPES);
  set(nodes, 'kw', ''); set(nodes, 'kwin', 'description');
  await t.go();
  assert.equal(nodes.kwnote.textContent, '');
});

test('a description-bearing scope shows the coverage against the other filters\' total, not the header\'s', async () => {
  const { nodes, t } = loadApp(url => url.startsWith('/facets')
    ? { total: 12, facets: {}, blocking: null, description_coverage: { covered: 42, total: 100 } } : [], SCOPES);
  set(nodes, 'kw', 'rust');
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
  set(nodes, 'kw', 'rust'); set(nodes, 'kwin', 'description');
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
  set(nodes, 'kw', 'rust'); set(nodes, 'kwin', 'description');
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

test('the "N hidden" note counts the page on screen, not every row drawn this session', async () => {
  // The recount on hide went over every row ever drawn — both lists, every page — so hiding one
  // row on page 2 after three on page 1 read "4 hidden" with one hidden row in view.
  const { t, nodes } = loadApp(url => (qs(url).page === '2' ? [job('p2a'), job('p2b')]
    : [job('p1a'), job('p1b'), job('p1c')]));
  await t.go();
  for (const id of ['p1a', 'p1b', 'p1c']) t.dismiss(id);
  assert.ok(nodes.hidden.innerHTML.startsWith('3 hidden'));
  await t.goToPage(2);
  t.dismiss('p2a');
  assert.ok(nodes.hidden.innerHTML.startsWith('1 hidden'), 'the note says: ' + nodes.hidden.innerHTML);
  for (const id of ['p1a', 'p1b', 'p1c', 'p2a']) t.dismissed.delete(id);
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

test('a salary sort is stated in the picker\'s currency even with no bound set', async () => {
  // Salary is stored in each employer's own currency (ADR-0082), so the server orders a salary
  // sort in ONE currency. With no bound the bracket sends none, and an India browse sorted by
  // salary listed USD first no matter what the picker said. The note names the same currency.
  const { t, nodes, fetches } = loadApp(() => []);
  set(nodes, 'sort', 'salary');
  set(nodes, 'salcur', 'INR');
  fetches.length = 0;
  await t.go();
  const search = fetches.filter(u => u.startsWith('/search?')).at(-1);
  assert.strictEqual(qs(search).sort, 'salary');
  assert.strictEqual(qs(search).salary_currency, 'INR');
  assert.ok(nodes.sortnote.textContent.includes('INR'), nodes.sortnote.textContent);

  set(nodes, 'sort', 'posted');
  fetches.length = 0;
  await t.go();
  assert.strictEqual(qs(fetches.filter(u => u.startsWith('/search?')).at(-1)).salary_currency,
    undefined, 'the currency only rides along with a salary sort or a bound');
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

test('hiding a company from a Matches card takes it off the Matches list', async () => {
  // The one delegated handler always re-ran the Search list, which is not the one on screen:
  // the company just hidden stayed in Matches until the tab was opened again.
  const hidden = new Set();
  const row = (co, title) => job('greenhouse:' + co + ':1', { company: co, title });
  const { nodes, t, ctx, docHandlers } = loadApp(url => {
    if (url === '/sets') return [{ id: 's1', name: 'Backend', query: 'backend' }];
    if (!url.startsWith('/search?')) return [];
    return [row('spamco', 'SPAMCO_JOB'), row('goodco', 'GOODCO_JOB')]
      .filter(r => !hidden.has(r.id.slice(0, r.id.lastIndexOf(':'))));
  });
  const base = ctx.fetch;
  ctx.fetch = (url, init) => {
    if (url !== '/companies' || !init) return base(url);
    hidden.add(JSON.parse(init.body).board);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ followed: [], hidden: [...hidden] }) });
  };
  await t.loadSets();
  await new Promise(r => setTimeout(r, 0));
  assert.ok(nodes['matches-results'].innerHTML.includes('SPAMCO_JOB'));

  ctx.location.hash = '#matches';
  const button = { disabled: false, dataset: { hideCompany: 'greenhouse:spamco' } };
  const target = { closest: sel => (sel === '[data-hide-company]' ? button : null) };
  for (const handler of docHandlers.click) await handler({ target });
  await new Promise(r => setTimeout(r, 0));
  assert.ok(!nodes['matches-results'].innerHTML.includes('SPAMCO_JOB'),
    'the company just hidden is still listed on Matches');
  assert.ok(nodes['matches-results'].innerHTML.includes('GOODCO_JOB'));
});

test('a result card links its company into Trends by the Board its id names (ADR-0185)', () => {
  const { t } = loadApp(() => []);
  const html = t.jobCard(job('greenhouse:acme:123'), 0);
  assert.match(html, /data-trend="greenhouse:acme"/);
  assert.match(html, /data-trend-name="Acme"/);
  assert.match(html, /aria-label="Hiring trend at Acme"/);
  assert.ok(!t.jobCard(job('noboard'), 0).includes('data-trend='), 'no Board, no link');
});


test('a company handed over from Trends or Hot searches its Boards, shown as one removable pill', async () => {
  const { t, fetches, nodes } = loadApp(() => []);
  nodes['company'] = Object.assign(nodes['company'] || {}, { value: 'stale text' });
  t.searchCompany(['workday:citi/2', 'workday:citi/3'], 'Citi (workday)');
  await new Promise(resolve => setTimeout(resolve, 0));
  const url = fetches.filter(u => u.startsWith('/search?')).pop();
  assert.deepEqual(new URLSearchParams(url.split('?')[1]).getAll('board'), ['workday:citi/2', 'workday:citi/3']);
  assert.equal(nodes['company'].value, '', 'the name filter would narrow the Boards again');
  assert.match(nodes['active'].innerHTML, /<b>Company<\/b> Citi \(workday\)/);
  t.dropFilter('board');
  await new Promise(resolve => setTimeout(resolve, 0));
  const after = fetches.filter(u => u.startsWith('/search?')).pop();
  assert.equal(new URLSearchParams(after.split('?')[1]).getAll('board').length, 0);
});


test('a company hand-off rides in the hash, so a reload keeps it', async () => {
  const { t, ctx, nodes } = loadApp(() => []);
  t.searchCompany(['workday:citi/2'], 'Citi', 'AI / Machine Learning');
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  assert.deepEqual(hash.getAll('board'), ['workday:citi/2']);
  assert.equal(hash.get('label'), 'Citi');
  assert.equal(nodes['q'].value, 'AI / Machine Learning', 'a drilled category is the query');
  const reload = loadApp(() => []);
  reload.ctx.location.hash = ctx.location.hash;
  assert.equal(reload.t.readSearchHash(), true);
  reload.t.go();
  await new Promise(resolve => setTimeout(resolve, 0));
  const url = reload.fetches.filter(u => u.startsWith('/search?')).pop();
  assert.deepEqual(new URLSearchParams(url.split('?')[1]).getAll('board'), ['workday:citi/2']);
  assert.match(reload.nodes['active'].innerHTML, /<b>Company<\/b> Citi/);
});
