/* Trends legend behaviour, run against the REAL src/headstart/ui/static/app.js.
 *
 * Node's built-in runner (`node --test`) on purpose: this is a Python repo, and a UI assertion
 * is not worth an npm dependency tree, a lockfile, or a second package manager in CI. No
 * package.json, no node_modules — `node --test tests/js/*.test.js` is the whole harness. A
 * glob, not a directory: the directory form is not accepted by every Node the CI image ships,
 * and CI pairs the glob with `shopt -s failglob` so a pattern matching nothing fails loudly
 * instead of reporting `tests 0` and passing.
 *
 * app.js is a browser script, not a module: it declares top-level `const`/`let` and touches
 * `document`/`window` as it loads. So it is evaluated in a vm context with a stub DOM, and a
 * trailing line is appended to expose the few internals a test needs. That keeps the tests
 * honest — they exercise the shipped file, not a copy of its logic.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const same = require('./same_values');

const APP_JS = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui', 'static', 'app.js');

function fakeEl() {
  return {
    // `value: ''` so the load-time `go()` call (ADR-0074 — the Search tab browses on load,
    // reading `#q` even when this harness only cares about Trends) sees an empty query
    // rather than throwing on `undefined.trim()`.
    innerHTML: '', textContent: '', hidden: false, value: '',
    // `style` needs the methods the code actually calls on it. A bare `{}` let
    // `style.setProperty` throw asynchronously, and Node reports that as an
    // unhandledRejection AFTER the test ends — so 24 tests failed at once with no
    // useful location while the real cause (a custom property set for the two-column
    // results grid) sat in another file entirely.
    style: { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return this[k] ?? ''; }, removeProperty(k) { delete this[k]; } },
    // The redesigned drawTrends() queries into #trends-chart/#trends-legend for the hover layer
    // and the emphasis pass (querySelector/querySelectorAll). Null/empty is the correct stub
    // answer — every call site already guards on "not found" for the real DOM's own sake (the
    // crosshair group doesn't exist until the first draw), so this never needs to be smarter.
    querySelectorAll: () => [], querySelector: () => null,
    // Recorded, not swallowed: drawTrends writes the chart's own aria-label, which is one of
    // the places that reports the ATS selection.
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k] ?? null; },
    removeAttribute(k) { delete this.attrs[k]; },
    select() { this.selected = true; },
    // Recorded, not swallowed: the ATS picker's own handler is registered this way, and the
    // racing tests below drive the control the bug report names rather than calling the
    // loader behind it. `fire` is the harness's stand-in for dispatchEvent.
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    fire(type) { (this.listeners[type] || []).forEach(fn => fn({ target: this })); },
    tabIndex: 0, classList: { toggle() {}, add() {}, remove() {} },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
  };
}

/** A fresh evaluation of app.js per test, so module-level state never leaks between them.
 *
 * `fetches` records every URL requested. It is the only way to tell "the click was ignored"
 * from "the click ran and happened to land on the same split" — asserting on `trendSplit`
 * alone passes either way, because 'bands' is also the fallback. */
function loadApp(fetchImpl) {
  const nodes = {};
  const logged = [];
  const fetches = [];
  const ctx = {
    document: {
      getElementById: id => (nodes[id] ||= fakeEl()),
      addEventListener() {},
      querySelector: () => null,
      querySelectorAll: () => [],
      // seriesColor() reads categorical-slot custom properties off :root at draw time (a theme
      // flip repaints correctly instead of freezing on a hardcoded hex array — see app.js). The
      // stub's exact color is irrelevant to every test here; only the structural HTML is asserted.
      documentElement: { getAttribute: () => null, setAttribute() {} },
    },
    // app.js reads its config off `window.CFG`, so tests set flags there.
    window: { addEventListener() {}, location: { hash: '' }, CFG: {} },
    location: { hash: '' },
    // Recorded, not printed: app.js reports every failed request, and the stub fetches fail most
    // of the page-load ones on purpose.
    console: { log: console.log, warn: (...a) => logged.push(a), error: (...a) => logged.push(a) },
    CFG: {}, URLSearchParams, Date, Math, isNaN, setTimeout, clearTimeout,
    // loadTrends cancels its own previous request, so app.js does not evaluate without this.
    // Node's real one, not a stub: the abort tests below need a signal that genuinely fires.
    AbortController,
    getComputedStyle: () => ({ getPropertyValue: () => '#000000' }),
    fetch: (url, opts) => {
      fetches.push(String(url));
      return fetchImpl ? fetchImpl(String(url), opts) : Promise.resolve({ ok: false });
    },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { draw: drawTrends, load: loadTrends, click: trendClick, split: () => trendSplit,'
    + ' chartMax: CHART_MAX,'
    + ' niceAxis: niceAxis, niceBounds: niceBounds, fmtAxis: fmtAxis, deltaText: deltaText, seriesValues: seriesValues,'
    + ' hasIndexBase: hasIndexBase,'
    + ' atsSelected: trendAtsSelected, atsLabel: trendAtsLabel, atsToggle: toggleAtsPopover,'
    + ' coverageSet: value => { trendCoverage = value; }, metricSet: value => { trendMetric = value; },'
    + ' colorSlot: name => seriesColorAssignment.get(name), setUnit: setUnit,'
    + ' load: loadTrends,'
    + ' data: () => trendData,'
    + ' picks: () => trendPicks, setPicks: p => { trendPicks = p; topSplit.stale = true; },'
    + ' top: () => topSplit.chosen, selectSplit: trendSplitSelect, readHash: readTrendHash,'
    + ' suggest: suggestCompanies, choose: chooseCo, options: () => coOptions,'
    + ' follow: boards => { myCompanies = { followed: boards, hidden: [] }; },'
    + ' followed: followedOption, openTrend: openCompanyTrend, chartedAndOther,'
    + ' unit: () => trendUnit, clickUnit: pickUnit, hash: trendHash, pick: setPicks,'
    + ' table: toggleTrendsTable, tooltipNotes, geom: () => lastGeom, hotMeasure: HOT_MEASURE, turnoverOf,'
    + ' set: (d, drill) => { trendData = d; trendRaw = d; trendDrill = drill || null; } };'
    // Repaints are counted at the global binding, which is what loadTrends' own `drawTrends()`
    // call resolves — so this counts the real paints, not a copy of them.
    + '\n;(() => { let n = 0; const real = drawTrends;'
    + ' globalThis.drawTrends = (...a) => { n++; return real(...a); };'
    + ' globalThis.__t.draws = () => n; })();';
  vm.runInNewContext(src, ctx);
  // The page fetches on load (the feed, the trends chart). Those are not what any test here is
  // asserting about, so the log starts empty from the caller's point of view.
  fetches.length = 0;
  return { t: ctx.__t, nodes, fetches, ctx };
}

test('a late Trends response cannot overwrite a newer chart', async () => {
  const { t, nodes, ctx } = loadApp();
  await new Promise(resolve => setTimeout(resolve, 0));
  const pending = {};
  ctx.fetch = url => Promise.resolve({ ok: true, json: () => new Promise(resolve => {
    pending[new URL(url, 'http://test').searchParams.get('family')] = resolve;
  }) });
  const old = t.load('old');
  await new Promise(resolve => setTimeout(resolve, 0));
  const newer = t.load('new');
  await new Promise(resolve => setTimeout(resolve, 0));
  const fresh = fixture(); fresh.series[0].label = 'NEW_TREND';
  pending.new(fresh); await newer;
  pending.old(fixture()); await old;
  assert.ok(nodes['trends-legend'].innerHTML.includes('NEW_TREND'));
});

test('the comparable-coverage control sends its explicit scope', async () => {
  const { t, ctx } = loadApp();
  t.coverageSet('comparable');
  let requested = '';
  ctx.fetch = url => {
    requested = String(url);
    return Promise.resolve({ ok: true, json: () => Promise.resolve(fixture()) });
  };
  await t.load(null);
  assert.match(requested, /coverage=comparable/);
});

const mk = (name, label, latest) => ({ name, label, points: [latest, latest], latest });

/** Ten series, so two (ai-ml, data-science) sit past CHART_MAX (8) and fold into the Other row. */
function fixture() {
  return {
    version: 2, metric: 'stock', split_by: 'family',
    stamps: ['2026-08-13T00:00:00+00:00', '2026-08-13T06:00:00+00:00'],
    totals: [100000, 100000], non_tech: [1000, 1000],
    series: [
      mk('software-engineering', 'Software Engineering (general)', 40000),
      mk('web-development', 'Web & .NET Development', 9000),
      mk('data-engineering', 'Data Engineering', 8000),
      mk('qa-test', 'QA & Test Engineering', 7000),
      mk('devops', 'DevOps', 6000),
      mk('security-engineering', 'Security Engineering', 5000),
      mk('sre-platform', 'SRE & Platform Engineering', 4000),
      mk('mobile-development', 'Mobile Development', 3000),
      mk('ai-ml', 'AI / Machine Learning', 2000),
      mk('data-science', 'Data Science', 1000),
    ],
    watch_parents: ['ai-ml', 'software-engineering'],
  };
}

/** The `<li>` for one series, from the rendered legend HTML. */
function row(html, name) {
  const rows = html.split(/<li[\s>]/).slice(1);
  return rows.find(r => r.includes(`data-name="${name}"`)) || '';
}

test('a category holding watched roles is marked, so the drill is discoverable', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);
  t.draw();
  assert.match(row(nodes['trends-legend'].innerHTML, 'software-engineering'), /class="drill"/);
});

test('a category with no watched roles carries no marker', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);
  t.draw();
  assert.doesNotMatch(row(nodes['trends-legend'].innerHTML, 'qa-test'), /class="drill"/);
});

test('a row past CHART_MAX no longer appears by name — it is folded into Other', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);   // ai-ml is 9th here
  t.draw();
  const html = nodes['trends-legend'].innerHTML;
  assert.equal(row(html, 'ai-ml'), '');           // no row named ai-ml exists any more
  assert.notEqual(row(html, '__other__'), '');    // it is summed into the Other row instead
  assert.doesNotMatch(row(html, '__other__'), /class="drill"/);
});

test('the Other row sums every series past CHART_MAX', () => {
  const { t, nodes } = loadApp();
  // ai-ml (2000) + data-science (1000) are the two past CHART_MAX in fixture().
  t.set(fixture(), null);
  t.setUnit('count', false);   // count is a straight sum; share would also fold in the totals scaling
  t.draw();
  const other = row(nodes['trends-legend'].innerHTML, '__other__');
  assert.match(other, /Other \(2 smaller categories\)/);
  const ct = other.match(/<span class="ct">([^<]+)<\/span>/);
  assert.ok(ct, 'Other row should render a count value');
  // The legend compacts counts — `45,174` is six characters taken out of the label beside it —
  // so this reads the compact form. Still the SUM (2000 + 1000), not the label text.
  assert.equal(ct[1], '3.0k');
});

test('the roles marker opens the roles it names; the row opens the levels that add up to it', () => {
  const { t } = loadApp();
  t.set(fixture(), null);
  t.click('software-engineering', 'roles');
  assert.equal(t.split(), 'roles');
  t.set(fixture(), null);
  t.click('software-engineering', 'bands');
  assert.equal(t.split(), 'bands');
});

test('clicking a category without watched roles opens the experience bands', () => {
  const { t } = loadApp();
  t.set(fixture(), null);
  t.click('web-development');
  assert.equal(t.split(), 'bands');
});

test('charted rows are real buttons; the Other row is not interactive', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);
  t.draw();
  const html = nodes['trends-legend'].innerHTML;
  const charted = row(html, 'software-engineering');
  const other = row(html, '__other__');            // the aggregate of everything past CHART_MAX
  assert.match(charted, /role="button"/);
  assert.match(charted, /tabindex="0"/);
  assert.doesNotMatch(other, /role="button"/);
  assert.doesNotMatch(other, /tabindex/);
  // Scoped to the drill row itself, not the whole <li>: the hide toggle that now sits beside
  // it IS a real aria-pressed button, and what this asserts is that the drill row is not one.
  const drillPart = s => s.split('<button class="vis"')[0];
  assert.doesNotMatch(drillPart(charted), /aria-pressed/);
  assert.doesNotMatch(drillPart(other), /aria-pressed/);
  // The role belongs on the inner .row so the <li> keeps its implicit `listitem`.
  assert.match(charted, /<span class="row"[^>]*role="button"/);
});

test('the Other row is inert — clicking it issues no request', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  // `__other__` is not a real family name, so trendClick's findIndex(-1) guard catches it —
  // the SAME guard an unknown/mistyped name relies on, not a special case added for Other.
  t.click('__other__');
  same(fetches, []);
});

test('a name past CHART_MAX is still inert if clicked directly (defence in depth)', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  t.click('ai-ml');                                 // 9th: real name, but past CHART_MAX
  same(fetches, []);
});

test('a charted row does issue a drill request', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  t.click('software-engineering');
  assert.equal(fetches.length, 1);
  assert.match(fetches[0], /family=software-engineering/);
  assert.match(fetches[0], /split=bands/, 'a row opens the levels that add up to it');
});

test('the scope line names the drillable set when more rows are listed than charted', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);
  t.draw();
  const scope = nodes['trends-scope'].textContent;
  // Assert the shape, not the copy: it must name the drillable count rather than imply every
  // listed row drills. CHART_MAX is read from the module so the two cannot drift.
  assert.match(scope, new RegExp(`top ${t.chartMax}\\b`));
  assert.doesNotMatch(scope, /click a category/);
});

test('no marker is drawn inside a drill — its rows are bands or roles, not categories', () => {
  const { t, nodes } = loadApp();
  t.set({ ...fixture(), split_by: 'band' }, 'software-engineering');
  t.draw();
  assert.doesNotMatch(nodes['trends-legend'].innerHTML, /class="drill"/);
});

test('the split toggle un-hides for a family that has watched roles', () => {
  const { t, nodes } = loadApp();
  t.set({ ...fixture(), split_by: 'band' }, 'software-engineering');
  t.draw();
  assert.equal(nodes['trends-split'].hidden, false);
});

test('an unmeasured roles drill says so, rather than claiming nothing is tracked', () => {
  const { t, nodes } = loadApp();
  // Reach the roles split the way a user does — by clicking the row's roles marker — then land
  // on the empty series the first post-deploy run produces, before `role_trends` has written
  // any `watch:` rows.
  t.set(fixture(), null);
  t.click('software-engineering', 'roles');
  t.set({ ...fixture(), series: [] }, 'software-engineering');
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /have not been measured yet/);
});

test('an unknown series name is ignored rather than drilled', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  // findIndex returns -1, which passes a bare `>= CHART_MAX` check — so the assertion has to
  // be "no request was issued". Checking trendSplit would pass either way: 'bands' is both the
  // ignored-click state and the fallback a drilled unknown name would land on.
  t.click('no-such-family');
  same(fetches, []);
});

test('a charted category keeps its color slot when a filter reshuffles the ranking', () => {
  // The anti-pattern this guards against: color assigned by array position repaints a category
  // when a filter merely changes who ranks where — a reader who learned "AI/ML is violet" must
  // not see it turn blue because Metric/Unit/ATS changed the order. Reverse only the top 8 (the
  // ones actually charted), leaving the same 8 names on screen, just re-ranked — a real filter
  // reshuffle never changes WHICH 8 make the cut and which 2 fall into Other in the same breath.
  const { t } = loadApp();
  const f = fixture();
  t.set(f, null);
  t.draw();
  const chartedNames = f.series.slice(0, t.chartMax).map(s => s.name);
  const before = chartedNames.map(name => t.colorSlot(name));

  const reordered = { ...f, series: [...f.series.slice(0, t.chartMax)].reverse().concat(f.series.slice(t.chartMax)) };
  t.set(reordered, null);
  t.draw();
  chartedNames.forEach((name, i) => {
    assert.equal(t.colorSlot(name), before[i], `${name} changed color slot after a pure reorder`);
  });
});

test('a slot vacated by a name that drops off screen is free for a new entrant', () => {
  // Complements the stability test above: the cache must not grow unbounded or starve a
  // genuinely new top-8 entrant just because an old name is still technically in the Map.
  const { t } = loadApp();
  const f = fixture();
  t.set(f, null);
  t.draw();                                        // 8 charted slots filled, 0-7
  const dropped = f.series[7].name;                 // mobile-development — about to fall off

  const swapped = { ...f, series: [f.series[8], ...f.series.slice(0, 7)] };   // ai-ml takes its place
  t.set(swapped, null);
  t.draw();
  assert.equal(t.colorSlot(dropped), undefined);     // no longer drawn -> no longer cached
  assert.ok(t.colorSlot('ai-ml') != null);            // the new 8th got a real slot, not undefined
});

// ---- ATS picker (ADR-0075) ----------------------------------------------------------------

/** Fake checkboxes for #trends-ats-menu's querySelectorAll — the harness's generic fakeEl
 * always answers querySelectorAll with [], so the ATS picker needs its own stand-in list. */
function fakeAtsMenu(nodes, pairs) {
  const boxes = pairs.map(([value, checked]) => ({ value, checked }));
  nodes['trends-ats-menu'].querySelectorAll = () => boxes;
  return boxes;
}

test('every box checked selects nothing — the only spelling of "no filter"', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true], ['workday', true]]);
  assert.equal(t.atsSelected(), null);
});

test('an unchecked box narrows the selection to what remains checked', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true], ['workday', false]]);
  same(t.atsSelected(), ['greenhouse', 'lever']);
});

test('all-checked sends no ats param on the wire', () => {
  const { t, nodes, fetches } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true]]);
  t.set(fixture(), null);
  t.click('software-engineering');   // drills, which calls loadTrends and issues a fetch
  assert.doesNotMatch(fetches[0], /ats=/);
});

// An EMPTY selection is the same "no filter" as a full one, because /trends narrows on the
// `ats` params it is given and an empty one appends none — so the panel answers with every
// ATS. Before this was said once in trendAtsSelected, `[]` came back instead of null and every
// truthiness test read it as an active filter: the trigger said "0 ATS", the chart named
// itself "0 of the ATS sources" and the short-history note blamed a narrow selection, all
// three over the unfiltered figure (verified in Chromium: legend 39k, zero ats params).

test('no box checked selects nothing either — the same spelling of "no filter"', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', false], ['lever', false], ['workday', false]]);
  assert.equal(t.atsSelected(), null);
});

// This one cannot go red on the code that had the bug, and is not meant to: the WIRE was
// always right (`[]` is truthy, so `if (ats)` ran, but `forEach` over it appended nothing).
// That is exactly the behaviour being KEPT here, so it is pinned rather than reproduced — the
// fix must change what the panel SAYS and nothing about what it asks for.
test('no box checked sends no ats param, exactly as every box checked does', () => {
  const { t, nodes, fetches } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', false], ['lever', false]]);
  t.set(fixture(), null);
  t.click('software-engineering');
  assert.doesNotMatch(fetches[0], /ats=/);
});

test('the trigger says "All ATS" with nothing checked, which is what the panel shows', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', false], ['lever', false]]);
  t.atsLabel();
  assert.equal(nodes['trends-ats-trigger'].textContent, 'All ATS ▾');
});

test('the chart does not name an ATS scope it is not filtered to', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', false], ['lever', false]]);
  t.set(fixture(), null);
  t.draw();
  assert.doesNotMatch(nodes['trends-chart'].getAttribute('aria-label'), /of the ATS sources/);
});

test('a short history with nothing checked blames the pipeline, not a selection', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', false], ['lever', false]]);
  t.set(oneStampFixture(), null);
  t.draw();
  assert.doesNotMatch(nodes['trends-empty'].textContent, /ATS selection/);
});

test('a narrowed selection is sent as repeated ats params', () => {
  const { t, nodes, fetches } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true], ['workday', false]]);
  t.set(fixture(), null);
  t.click('software-engineering');
  const params = new URLSearchParams(fetches[0].split('?')[1]);
  same(params.getAll('ats'), ['greenhouse', 'lever']);
});

test('the trigger label reads "All ATS" when nothing is excluded', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true]]);
  t.atsLabel();
  assert.equal(nodes['trends-ats-trigger'].textContent, 'All ATS ▾');
});

test('the trigger label counts what remains checked once something is excluded', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true], ['workday', false]]);
  t.atsLabel();
  assert.equal(nodes['trends-ats-trigger'].textContent, '2 ATS ▾');
});

test('the popover opens and closes, toggling by default', () => {
  const { t, nodes } = loadApp();
  nodes['trends-ats-menu'].hidden = true;
  t.atsToggle();
  assert.equal(nodes['trends-ats-menu'].hidden, false);
  t.atsToggle();
  assert.equal(nodes['trends-ats-menu'].hidden, true);
});

test('the popover forces closed regardless of its current state', () => {
  const { t, nodes } = loadApp();
  nodes['trends-ats-menu'].hidden = false;
  t.atsToggle(false);
  assert.equal(nodes['trends-ats-menu'].hidden, true);
});

// ---- one request owns the panel --------------------------------------------------------
//
// The ATS picker fires one loadTrends per checkbox, so narrowing 21 ATSes to 1 starts 20
// round trips. Measured in Chromium before the fix: 20 requests, 20 repaints, 6-11 answers
// arriving out of the order they were asked in, and 3 runs in 5 settling on an answer that
// was not the last request's — the numbers churning, then coming to rest on the wrong scope.
// The first two tests below are that bug and go red on the code that had it. The third cannot:
// with nothing cancelled there is no cancellation to mis-report, so it is a forward guard on
// the one obvious way to get the fix wrong (dropping the aborted check and letting an abort
// raise the network-failure banner), not a reproduction.

/** A /trends stub whose answers are released by hand, so a test can land them out of order.
 *
 * `signal` is honoured the way a browser honours it — an aborted fetch rejects — and is read
 * defensively on purpose: against a loadTrends that passes no signal at all these tests still
 * RUN, and fail on their assertion rather than on a TypeError about `undefined.signal`. */
function deferredTrends() {
  const calls = [];
  const impl = (url, opts) => new Promise((resolve, reject) => {
    calls.push({ url, answer: body => resolve({ ok: true, json: async () => body }) });
    const signal = opts && opts.signal;
    if (signal) signal.addEventListener('abort', () => {
      const err = new Error('aborted'); err.name = 'AbortError'; reject(err);
    });
  });
  return { calls, impl };
}

/** Let every settled promise run its continuations — including the ones created inside the vm. */
const settle = () => new Promise(r => setTimeout(r, 0));

/** The fixture, tagged so a test can say WHICH request's answer reached the panel. */
const tagged = n => ({ ...fixture(), version: n });

test('a burst of selections repaints once, not once per checkbox', async () => {
  const { calls, impl } = deferredTrends();
  const { nodes, t } = loadApp(impl);
  const boxes = fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true], ['workday', true],
                                    ['ashby', true], ['zoho', true], ['keka', true]]);
  calls.length = 0;                 // drop anything the page asked for as it loaded
  // Through the picker's own `change` handler, not the loader behind it: this is the wiring
  // the bug report named. Five boxes unchecked before the first answer can land.
  for (let i = 0; i < 5; i++) {
    boxes[i].checked = false;
    nodes['trends-ats-menu'].fire('change');
  }
  assert.equal(calls.length, 5, 'each selection still asks the server for its own answer');
  assert.match(calls[4].url, /ats=keka/, 'and asks for the selection as it stood at that click');
  calls.forEach((c, i) => c.answer(tagged(i)));   // every answer comes back, oldest first
  await settle();
  assert.equal(t.draws(), 1);       // was 5: one full chart+legend+KPI rewrite per answer
  assert.equal(t.data().version, 4);
});

test('an answer that arrives after a newer one never paints over it', async () => {
  const { calls, impl } = deferredTrends();
  const { t } = loadApp(impl);
  calls.length = 0;
  t.load(null);                     // the first box ticked
  t.load(null);                     // the second, which supersedes it
  calls[1].answer(tagged(2));
  await settle();
  calls[0].answer(tagged(1));       // the older request crawls in last
  await settle();
  assert.equal(t.data().version, 2, 'the panel must hold the newest selection, not the newest arrival');
  assert.equal(t.draws(), 1);
});

test('a cancelled request raises no error banner over the render replacing it', async () => {
  const { calls, impl } = deferredTrends();
  const { t, nodes } = loadApp(impl);
  calls.length = 0;
  t.load(null);
  t.load(null);                     // cancels the first, whose fetch now rejects
  calls[1].answer(tagged(2));
  await settle();
  assert.equal(nodes['trends-error'].hidden, true);
  assert.equal(t.data().version, 2);
});

/** One stamp only, so `runs < 2` and the empty-runs message renders. */
function oneStampFixture() {
  const f = fixture();
  return { ...f, stamps: [f.stamps[0]], series: f.series.map(s => ({ ...s, points: [s.points[0]] })) };
}

test('a short history under an active ATS filter names the filter as the cause', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', false]]);   // narrowed
  t.set(oneStampFixture(), null);
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /ATS selection/);
});

test('a short history with no ATS filter keeps the generic pipeline-is-new message', () => {
  const { t, nodes } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true]]);    // all checked = no filter
  t.set(oneStampFixture(), null);
  t.draw();
  assert.doesNotMatch(nodes['trends-empty'].textContent, /ATS selection/);
  assert.match(nodes['trends-empty'].textContent, /pipeline has run a few more times/);
});

// ---- methodology epochs (ADR-0164) -------------------------------------------------------

test('a methodology epoch draws a marker at its matching stamp', () => {
  const { t, nodes } = loadApp();
  t.set(golden('index_marks_counting_changes_and_takes_nothing_out'), null);
  t.draw();
  const svg = nodes['trends-chart'].innerHTML;
  assert.match(svg, /class="epoch-marker"/);
  assert.match(svg, /Aug 13 00:00 tech filter changed/);
});

test('an epoch with no matching stamp draws nothing, and does not crash the chart', () => {
  const { t, nodes } = loadApp();
  const f = fixture();
  f.epochs = [{ ts: '1999-01-01T00:00:00+00:00', changed: ['tech filter changed'] }];
  t.set(f, null);
  assert.doesNotThrow(() => t.draw());
  assert.doesNotMatch(nodes['trends-chart'].innerHTML, /class="epoch-marker"/);
});

test('a fixture with no epochs field at all still draws (older /trends payload shape)', () => {
  const { t } = loadApp();
  t.set(fixture(), null);  // fixture() carries no `epochs` key
  assert.doesNotThrow(() => t.draw());
});

// ---- the y axis --------------------------------------------------------------------------

test('the y axis rounds up to a whole nice step, so the top tick is never the data max', () => {
  const { t } = loadApp();
  // 27.0% used to be divided by four into 0.0 / 6.8 / 13.5 / 20.3 / 27.0.
  const a = t.niceAxis(27);
  assert.equal(a.top % (a.ticks[1] - a.ticks[0]), 0);
  assert.ok(a.top >= 27, 'the axis must contain the data');
  assert.ok(a.ticks.length >= 4 && a.ticks.length <= 7, `got ${a.ticks.length} ticks`);
  assert.equal(a.ticks[0], 0);
  assert.equal(a.ticks[a.ticks.length - 1], a.top);
});

test('the step is a nice number in both units, not a division of the data max', () => {
  const { t } = loadApp();
  // 1 / 2 / 2.5 / 5 x 10^n — the mantissa is what makes a tick readable at a glance.
  for (const hi of [27, 45174, 0.34, 6.8, 992]){
    const a = t.niceAxis(hi);
    const step = a.ticks[1] - a.ticks[0];
    const mantissa = step / Math.pow(10, Math.floor(Math.log10(step)));
    assert.ok([1, 2, 2.5, 5].some(m => Math.abs(m - mantissa) < 1e-9),
      `hi=${hi} gave step ${step} (mantissa ${mantissa})`);
  }
});

test('every label on one axis prints at the same precision', () => {
  const { t } = loadApp();
  t.setUnit('share', false);
  const a = t.niceAxis(27);
  const decimals = a.ticks.map(v => (t.fmtAxis(v, a.dec).match(/\.(\d+)/) || ['', ''])[1].length);
  assert.equal(new Set(decimals).size, 1, `mixed precisions: ${a.ticks.map(v => t.fmtAxis(v, a.dec))}`);
});

// ---- colour slots ------------------------------------------------------------------------

test('a category that drops off and comes back reclaims the colour it had', () => {
  // Eight slots and twenty-four families means a slot has to change hands eventually, but a
  // name RETURNING must not be handed a different one — the reader who learned it is still in
  // the same session. Two names leave and come back RE-RANKED, so allocating by rank order
  // (what the previous allocator did) hands them each other's colour and fails here; only
  // per-name memory gets both right.
  const { t } = loadApp();
  const f = fixture();
  t.set(f, null);
  t.draw();
  const qa = f.series[3].name, dev = f.series[4].name;
  const qaWas = t.colorSlot(qa), devWas = t.colorSlot(dev);
  assert.notEqual(qaWas, devWas);

  const without = { ...f, series: f.series.filter(s => s.name !== qa && s.name !== dev) };
  t.set(without, null);
  t.draw();                                              // ai-ml + data-science take the two
  assert.equal(t.colorSlot(qa), undefined);
  assert.equal(t.colorSlot(dev), undefined);

  // back, with devops now out-ranking qa-test
  const swapped = { ...f, series: [f.series[0], f.series[1], f.series[2], f.series[4],
                                   f.series[3], ...f.series.slice(5)] };
  t.set(swapped, null);
  t.draw();
  assert.equal(t.colorSlot(qa), qaWas, 'a returning name must get its own colour back');
  assert.equal(t.colorSlot(dev), devWas);
});

/* Both of these shipped and were caught by review, not by a test — so they get one each. */

test('a series measured at zero early is not indexed off a later point', () => {
  const { t } = loadApp();
  const f = fixture();
  // A real measurement of zero, then growth. `find(v => v)` skipped the zero and indexed off
  // the 5, so the first point plotted at 0/5*100 = 0, the line spiked to 800, and the axis
  // stretched to 0-800 — crushing every other series into a few pixels.
  const zero = { name: 'zerostart', label: 'zerostart', points: [0, 0, 5, 10, 20, 40], latest: 40 };
  t.set({ ...f, series: [zero, ...f.series], stamps: [1, 2, 3, 4, 5, 6].map(String),
          totals: [100, 100, 100, 100, 100, 100] }, null);
  t.setUnit('change', false);
  const vals = t.seriesValues(zero);
  assert.ok(vals.every(v => v === null),
    `a base below the floor must yield no line, got ${JSON.stringify(vals)}`);
  // Not just "all null" — that outcome is also what the floor produces, so assert the base
  // SELECTION too. The bug picked 5 (the first truthy value) and plotted point 0 at 0/5*100.
  const high = { name: 'high', label: 'high', points: [0, 0, 50, 100], latest: 100 };
  t.set({ ...f, series: [high, ...f.series], stamps: ['1', '2', '3', '4'],
          totals: [100, 100, 100, 100] }, null);
  same(t.seriesValues(high), [null, null, null, null],
    'a measured zero is the base, so this series has none — it must not index off the 50');
});

test('a tile never headlines a series the chart refuses to draw', () => {
  const { t, nodes } = loadApp();
  const f = fixture();
  // Base 4 is under the floor, but the mean of the first three (4+20+30)/3 = 18 is over it —
  // so the chart drew a gap while the tile read "Biggest riser +233.3%". Two gates, two
  // different quantities.
  const low = { name: 'low', label: 'low', points: [4, 20, 30, 40], latest: 40 };
  t.set({ ...f, series: [low, ...f.series], stamps: ['1', '2', '3', '4'],
          totals: [100, 100, 100, 100] }, null);
  t.setUnit('change', false);
  assert.equal(t.hasIndexBase(low), false);
  t.draw();
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('>low<'),
    'a series with no index base must not appear in a KPI tile');
});

test('a healthy series indexes its RAW COUNT to 100, not its share', () => {
  const { t } = loadApp();
  const f = fixture();
  const ok = { name: 'ok', label: 'ok', points: [8, 9, 12, 16], latest: 16 };
  // `totals` MUST vary. With a flat denominator, index-of-count and index-of-share are the
  // same numbers, so the test cannot fail if share-indexing came back — and indexing the count
  // was an explicit product decision (ADR-0119), which makes it exactly the thing to pin.
  // Doubling the denominator halves every share: index-of-share would be [100, 75, 75, 67].
  t.set({ ...f, series: [ok, ...f.series], stamps: ['1', '2', '3', '4'],
          totals: [100, 150, 200, 300] }, null);
  t.setUnit('change', false);
  same(t.seriesValues(ok).map(Math.round), [100, 113, 150, 200],
    'the base is the count, so a growing denominator must not move the line');
});

test('a delta carries its sign in the number, not only in the arrow', () => {
  const { t } = loadApp();
  // The flat glyph has one shape and two signs: -0.28% and +0.89% both rendered "→ 0.3%" /
  // "→ 0.9%", and the "biggest faller" tile printed a flat arrow with no minus anywhere.
  assert.match(t.deltaText(-0.282), /−0\.3%/);
  assert.match(t.deltaText(0.893), /\+0\.9%/);
  assert.notEqual(t.deltaText(-0.282), t.deltaText(0.282));
  assert.match(t.deltaText(-22), /↓ −22\.0%/);
  assert.match(t.deltaText(118.2), /↑ \+118\.2%/);
});

/* ---- Companies (ADR-0185): the picker, its request, and the views a pick adds. ---- */

const STAMPS = ['2026-09-13T00:00:00+00:00', '2026-09-20T00:00:00+00:00'];
/** A top-level answer under picks. `sizes` are each family's openings at both stamps. */
function picked(sizes, companies) {
  companies = (companies || [{ key: 'greenhouse:acme', label: 'Acme' }]).map(c =>
    ({ board_keys: [c.key], atses: [c.key.split(':')[0]], ...c }));
  return {
    version: 2, metric: 'stock', split_by: 'family', stamps: STAMPS,
    totals: [1000, 1000], non_tech: [10, 10], watch_parents: [],
    series: Object.entries(sizes).map(([name, [a, b]]) => ({ name, label: name, points: [a, b], latest: b })),
    companies,
    company_totals: {}, epochs: [], discovered: [],
    counted_since: Object.fromEntries(companies.map(c => [c.key, STAMPS[0]])),
  };
}
/** A fetch that answers every request with `body` and records the params asked for. */
function answering(ctx, body, status = 200) {
  const asked = [];
  ctx.fetch = url => {
    asked.push(new URLSearchParams(String(url).split('?')[1] || ''));
    return Promise.resolve({ ok: status === 200, status,
      json: () => Promise.resolve(typeof body === 'function' ? body(asked.length) : body) });
  };
  return asked;
}

/* Golden answers (tests/fixtures/trend_answers/, ADR-0230): answers as the Space serves them,
 * every line netted by headstart.trend_netting. pytest proves the Python rule serves exactly
 * these, so the page is tested on what it will be given rather than on hand-written netting. */
const ANSWERS = path.join(__dirname, '..', 'fixtures', 'trend_answers');
function golden(name) {
  return JSON.parse(fs.readFileSync(path.join(ANSWERS, `${name}.json`), 'utf8')).served;
}
/** The picks a golden answer was asked about, as the page holds them after a load. */
function picksOf(d) {
  return d.companies.map(c => ({ key: c.key, label: c.label, boardKeys: c.board_keys, atses: c.atses }));
}
/** Show a golden answer as the page would after asking for it: its picks, measure and drill. */
function showGolden(t, name, extra) {
  const d = { ...golden(name), ...extra };
  t.setPicks(picksOf(d));
  t.metricSet(d.metric);
  t.set(d, d.family);
  return d;
}

test('picks go to /trends as repeated company params', async () => {
  const { t, ctx } = loadApp();
  const asked = answering(ctx, picked({ a: [50, 60], b: [40, 45] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }]);
  await t.load(null);
  same(asked[0].getAll('company'), ['greenhouse:acme', 'lever:beta']);
  assert.equal(asked[0].get('split'), 'company', 'two picks are compared, not summed');
});

test('one pick asks for categories, the Space default', async () => {
  const { t, ctx } = loadApp();
  const asked = answering(ctx, picked({ a: [50, 60], b: [40, 45] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(asked[0].get('split'), null);
});

test('the Space names a pick that arrived by key alone', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, picked({ a: [50, 60], b: [40, 45] },
    [{ key: 'workday:acme/site1', label: 'Acme Corp', name: 'Acme Corp' }]));
  t.setPicks([{ key: 'workday:acme/site2', label: null }]);
  await t.load(null);
  same(t.picks(), [{ key: 'workday:acme/site1', label: 'Acme Corp',
    boardKeys: ['workday:acme/site1'], atses: ['workday'] }]);
  assert.ok(nodes['trends-co-chips'].innerHTML.includes('Acme Corp'));
});

test('a small pick opens on one Total line, the sum of its categories', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, picked({ a: [3, 4], b: [2, 2], c: [1, null] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  const s = t.data().series;
  assert.equal(s.length, 1);
  assert.equal(s[0].name, '__total__');
  same(s[0].points, [6, 6]);
  assert.equal(s[0].latest, 6);
  assert.ok(!nodes['trends-legend'].innerHTML.includes('role="button"'),
    'a Total row opens nothing, so it must not be a button');
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Categories tracked'));
});

test('a pick with two indexable categories opens on them', async () => {
  const { t, ctx } = loadApp();
  answering(ctx, picked({ a: [50, 60], b: [5, 9], c: [1, 1] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(t.data().series.length, 3);
});

test('switching Total to Category redraws without a request, and the choice sticks', async () => {
  const { t, ctx } = loadApp();
  const asked = answering(ctx, picked({ a: [3, 4], b: [2, 2] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  t.selectSplit('families');
  assert.equal(asked.length, 1);
  assert.equal(t.data().series.length, 2);
  await t.load(null);
  assert.equal(t.data().series.length, 2, 'a refetch must not overrule the reader');
});

test('Company asks the Space for one line per pick', async () => {
  const { t, ctx } = loadApp();
  const asked = answering(ctx, picked({ a: [50, 60], b: [40, 45] },
    [{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }]));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }]);
  await t.load(null);
  t.selectSplit('company');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(asked[1].get('split'), 'company');
});

test('Company falls back when the picks shrink below two', async () => {
  const { t, ctx } = loadApp();
  const asked = answering(ctx, picked({ a: [50, 60], b: [40, 45] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }]);
  t.selectSplit('company');
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(asked[asked.length - 1].get('split'), null);
  assert.equal(t.top(), 'auto');
});

test('a company line under Share divides by its own company, not every pick', async () => {
  const { t, ctx } = loadApp();
  answering(ctx, {
    ...picked({}), split_by: 'company', totals: [1000, 1000],
    series: [{ name: 'greenhouse:acme', label: 'Acme', points: [50, 60], latest: 60 }],
    company_totals: { 'greenhouse:acme': [100, 200] },
  });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }]);
  await t.load(null);
  t.setUnit('share', false);
  same(t.seriesValues(t.data().series[0]), [50, 30]);
});

test('a company line opens nothing when clicked', async () => {
  const { t, ctx, fetches } = loadApp();
  answering(ctx, { ...picked({}), split_by: 'company',
    series: [{ name: 'greenhouse:acme', label: 'Acme', points: [50, 60], latest: 60 }] });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }]);
  await t.load(null);
  const before = fetches.length;
  t.click('greenhouse:acme');
  assert.equal(fetches.length, before);
});

test('a pick the directory does not hold is dropped with a sentence, and the rest still draw', async () => {
  const { t, ctx, nodes } = loadApp();
  const asked = [];
  ctx.fetch = url => {
    const q = new URLSearchParams(String(url).split('?')[1]);
    asked.push(q);
    return Promise.resolve(q.getAll('company').includes('workday:ghost')
      ? { ok: false, status: 400, json: () => Promise.resolve({ error: 'unknown company: workday:ghost' }) }
      : { ok: true, status: 200, json: () => Promise.resolve(picked({ a: [50, 60], b: [40, 45] })) });
  };
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'workday:ghost', label: 'Ghost' }]);
  await t.load(null);
  same(asked[1].getAll('company'), ['greenhouse:acme']);
  assert.match(nodes['trends-co-note'].textContent, /HeadStart has no trend for Ghost: it isn’t in the company directory\./);
  assert.equal(nodes['trends-error'].hidden, true);
});

test('a Space with no directory drops every pick and draws the whole index', async () => {
  const { t, ctx, nodes } = loadApp();
  const asked = [];
  ctx.fetch = url => {
    const q = new URLSearchParams(String(url).split('?')[1] || '');
    asked.push(q);
    return Promise.resolve(q.getAll('company').length
      ? { ok: false, status: 503, json: () => Promise.resolve({ error: 'no company directory' }) }
      : { ok: true, status: 200, json: () => Promise.resolve(fixture()) });
  };
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(asked.length, 2);
  same(t.picks(), []);
  assert.match(nodes['trends-co-note'].textContent, /aren’t available/);
});

test('a 400 about something other than a pick is an error, never a retry loop', async () => {
  const { t, ctx, nodes } = loadApp();
  const asked = answering(ctx, { error: 'since/until/base must be ISO-8601' }, 400);
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(asked.length, 1);
  assert.equal(nodes['trends-error'].hidden, false);
});

test('the chart says where a company history starts', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, picked({ a: [50, 60], b: [40, 45] }));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  // Said once, in the company's own sentence block, rather than again above the chart.
  assert.match(nodes['trends-verdict'].innerHTML, /HeadStart has counted Acme since Sep 13 — too short to tell a trend from noise/);
  assert.doesNotMatch(nodes['trends-empty'].textContent, /counted Acme/);
});

test('suggestions leave out what is picked and show openings and Boards', async () => {
  const { t, ctx, nodes } = loadApp();
  const c = (key, label, openings, boards) => ({ key, label, openings, boards, atses: ['workday'] });
  answering(ctx, { companies: [c('workday:acme', 'Acme', 9214, 3), c('lever:beta', 'Beta', 1, 1)] });
  t.setPicks([{ key: 'lever:beta', label: 'Beta' }]);
  await t.suggest('ac');
  assert.equal(t.options().length, 1);
  assert.match(nodes['trends-co-list'].innerHTML, /9,214 tech openings · 3 boards · workday/);
  assert.equal(nodes['trends-co-list'].hidden, false);
});

test('no suggestion is said in the status line, not as an option', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, { companies: [] });
  await t.suggest('zzz');
  assert.equal(t.options().length, 0);
  assert.equal(nodes['trends-co-list'].hidden, true);
  assert.match(nodes['trends-co-note'].textContent, /No company matches “zzz”/);
});

test('the follow list is one option that adds every followed company not already picked', async () => {
  const { t } = loadApp();
  t.follow(['greenhouse:acme', 'lever:beta']);
  t.setPicks([{ key: 'Greenhouse:Acme', label: 'Acme' }]);
  const [option] = t.followed();
  same(option.followed, ['lever:beta'], 'casing differs between Board keys');
  t.follow([]);
  same(t.followed(), [], 'nothing followed, nothing offered');
});

test('a link replaces the picks through the hash, labelled by the name it showed', () => {
  const { t, ctx } = loadApp();
  t.openTrend('workday:acme/site1', 'Acme');
  assert.equal(ctx.location.hash, '#trends?company=workday%3Aacme%2Fsite1');
  assert.ok(t.readHash());
  same(t.picks(), [{ key: 'workday:acme/site1', label: 'Acme' }]);
  assert.ok(!t.readHash(), 'the same hash again changes nothing');
});

test('a bare #trends keeps the picks — it is the tab strip, not a request to clear them', () => {
  const { t, ctx } = loadApp();
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  ctx.location.hash = '#trends';
  assert.ok(!t.readHash());
  assert.equal(t.picks().length, 1);
});

test('a shared link carries the breakdown the reader chose', () => {
  const { t, ctx } = loadApp();
  ctx.location.hash = '#trends?company=a&company=b&by=company';
  t.readHash();
  assert.equal(t.top(), 'company');
  ctx.location.hash = '#trends?by=total';
  t.readHash();
  assert.equal(t.top(), 'auto', 'a breakdown with no company to break down means nothing');
});

test('Share leaves a top-level Company split, where every line would read 100%', async () => {
  const { t, ctx, nodes } = loadApp();
  const two = [{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }];
  answering(ctx, { ...picked({}, two), split_by: 'company',
    series: [{ name: 'greenhouse:acme', label: 'Acme', points: [50, 60], latest: 60 }] });
  t.setUnit('share', false);
  t.setPicks(two);
  t.selectSplit('company');
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.notEqual(t.data().series[0].name, undefined);
  assert.equal(nodes['trends-unit-static'].hidden, false);
  assert.match(nodes['trends-unit-static'].textContent, /always 100%/);
});

test('picks keep the order they were added in, though the Space answers sorted by key', async () => {
  const { t, ctx } = loadApp();
  answering(ctx, picked({ a: [50, 60], b: [40, 45] },
    [{ key: 'a:first', label: 'A' }, { key: 'z:last', label: 'Z' }, { key: 'm:canonical', label: 'M' }]));
  t.setPicks([{ key: 'z:last', label: null }, { key: 'm:alias', label: null }, { key: 'a:first', label: null }]);
  await t.load(null);
  same(t.picks().map(p => p.key), ['z:last', 'a:first', 'm:canonical'],
    'a pick made by another Board of its company goes last, under the directory key');
});

test('a 503 for missing trend data keeps the picks and says trends did not load', async () => {
  const { t, ctx, nodes } = loadApp();
  const asked = answering(ctx, { error: 'no trend data yet' }, 503);
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(asked.length, 1, 'no retry into the same 503');
  assert.equal(t.picks().length, 1);
  assert.equal(nodes['trends-error'].hidden, false);
});

test('past eight picks, Company folds the rest into Other, a share of their own totals', async () => {
  const { t, ctx } = loadApp();
  const keys = Array.from({ length: 10 }, (_, i) => `greenhouse:c${i}`);
  answering(ctx, { ...picked({}, keys.map(key => ({ key, label: key }))), split_by: 'company',
    totals: [10000, 10000],
    series: keys.map((key, i) => ({ name: key, label: key, points: [100 - i, 100 - i], latest: 100 - i })),
    company_totals: Object.fromEntries(keys.map(key => [key, [200, 200]])) });
  t.setPicks(keys.map(key => ({ key, label: key })));
  await t.load('ai-ml');   // inside a drill, where Share stays on under Company
  t.setUnit('share', false);
  const { other } = t.chartedAndOther(t.data());
  assert.match(other.label, /Other \(2 smaller companies\)/);
  // c8 + c9 = 92 + 91 openings, over their own two totals of 200: 45.75%, not 183 of 10,000
  same(t.seriesValues(other), [45.75, 45.75]);
});


/* ---- Fixes from measuring the shipped feature (ADR-0185, 2026-09-24 critique). ---- */

test('a company too small to index is drawn in counts, and Change comes back for a big one', async () => {
  const { t, ctx, nodes } = loadApp();
  let body = picked({ a: [3, 3], b: [1, 1] });
  ctx.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(t.unit(), 'count', 'a Total of 4 openings has no index base');
  assert.match(nodes['trends-unit-static'].textContent, /Change is off here/);
  body = picked({ a: [50, 60], b: [40, 45] });
  t.setPicks([{ key: 'greenhouse:big', label: 'Big' }]);
  await t.load(null);
  assert.equal(t.unit(), 'change', 'the reader’s unit returns once the view can show it');
});

test('picking a unit does not bring back one a lock withdrew', async () => {
  const { t, ctx } = loadApp();
  const two = [{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }];
  answering(ctx, { ...picked({}, two), split_by: 'company',
    series: [{ name: 'greenhouse:acme', label: 'Acme', points: [50, 60], latest: 60 }] });
  t.setPicks(two);
  t.selectSplit('company');
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  const buttons = ['share', 'count', 'change'].map(unit => ({ dataset: { unit }, hidden: false, disabled: false,
    setAttribute() {} }));
  ctx.document.getElementById('trends-unit').querySelectorAll = () => buttons;
  t.clickUnit('count');
  assert.equal(buttons[0].hidden, true, 'Share stays withdrawn under a top-level Company split');
});

test('a company counted from later than the ledger says when its own line begins', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, { ...picked({ a: [null, 50], b: [null, 40] }), counted_since: { 'greenhouse:acme': STAMPS[1] } });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.match(nodes['trends-verdict'].innerHTML, /HeadStart has counted Acme since Sep 20/);
});

test('a Board found after a company began is marked where its backlog lands', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, golden('found_board_marked_where_its_backlog_lands'));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  const svg = nodes['trends-chart'].innerHTML;
  assert.match(svg, /class="found-marker"/);
  assert.match(svg, /83 more boards of Acme found/);
  assert.match(nodes['trends-foot'].textContent, /boards found later/, 'explained under the chart');
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Biggest'), 'a found Board is no riser');
});

test('a pick closes and clears the list, keeping the field for the next name', async () => {
  const { t, ctx, nodes } = loadApp();
  const c = (key, label) => ({ key, label, openings: 5, boards: 1, atses: ['icims'] });
  answering(ctx, { companies: [c('icims:apac', 'Apac Atlassian'), c('icims:global', 'Globalcareers Atlassian')] });
  await t.suggest('atlassian');
  answering(ctx, picked({ a: [50, 60], b: [40, 45] }, [{ key: 'icims:apac', label: 'Apac Atlassian' }]));
  t.choose(0);
  assert.equal(nodes['trends-co-list'].hidden, true, 'an open list covered the Date range and Source controls');
  assert.equal(nodes['trends-co-q'].value, '');
});

test('a flat indexed line still gets an axis with height', () => {
  const { t } = loadApp();
  const b = t.niceBounds(100, 100);
  assert.ok(b.top > b.bot);
  assert.ok(b.bot <= 100 && b.top >= 100);
});

test('one pick offers its open roles and names itself in the heading', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, picked({ a: [50, 60], b: [40, 45] }, [{ key: 'greenhouse:acme', label: 'Acme', name: 'Acme' }]));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.equal(nodes['trends-co-roles'].hidden, false);
  assert.equal(nodes['trends-title'].textContent, 'Which tech roles are growing at Acme');
  answering(ctx, picked({ a: [50, 60], b: [40, 45] },
    [{ key: 'greenhouse:acme', label: 'Acme', name: 'Acme' }, { key: 'lever:beta', label: 'Beta', name: 'Beta' }]));
  t.setPicks([...t.picks(), { key: 'lever:beta', label: 'Beta' }]);
  await t.load(null);
  assert.equal(nodes['trends-co-roles'].hidden, false, 'several picks hand over together');
  assert.equal(nodes['trends-co-roles'].textContent, 'See their open roles');
});

test('a name with no match says the board may be unread or named otherwise', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, { companies: [] });
  await t.suggest('jpmorgan');
  assert.match(nodes['trends-co-note'].textContent, /may not read its board yet, or knows it by another name/);
});

test('duplicate removal and a tech-filter change withhold a company mover; extraction does not', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, golden('two_section_tenant_duplicate_removal_withholds_its_mover'));
  t.setPicks([{ key: 'taleo_enterprise:hdr/1', label: 'HDR' }]);
  await t.load(null);
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Biggest'));
  answering(ctx, golden('two_section_tenant_filter_change_withholds_its_mover'));
  await t.load(null);
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Biggest'), 'Wipro’s +74.7% held a +25% filter step');
  answering(ctx, golden('two_section_tenant_extraction_change_leaves_its_mover'));
  await t.load(null);
  assert.ok(nodes['trends-kpi'].innerHTML.includes('Biggest riser'), 'extraction moves levels, not these lines');
  // a one-Board company has no copies for duplicate removal to take
  answering(ctx, golden('one_board_company_duplicate_removal_leaves_its_mover'));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.ok(nodes['trends-kpi'].innerHTML.includes('Biggest riser'));
});


test('several picks are each dated from their own first counted run', async () => {
  const { t, ctx, nodes } = loadApp();
  const two = [{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }];
  answering(ctx, { ...picked({ a: [50, 60], b: [40, 45] }, two),
    counted_since: { 'greenhouse:acme': STAMPS[0], 'lever:beta': STAMPS[1] } });
  t.setPicks(two);
  await t.load(null);
  assert.match(nodes['trends-verdict'].innerHTML, /Acme since Sep 13, Beta since Sep 20/);
});

test('a company counted for one run keeps its own note, not a pipeline one', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, { ...picked({ a: [50], b: [40] }), stamps: [STAMPS[1]],
    counted_since: { 'greenhouse:acme': STAMPS[1] } });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.match(nodes['trends-verdict'].innerHTML, /counted Acme since Sep 20/);
  assert.match(nodes['trends-empty'].textContent, /a few more runs/);
  assert.ok(!/pipeline/.test(nodes['trends-empty'].textContent));
});


test('a view survives its link: drill, unit, measure, window and coverage ride the hash', async () => {
  const { t, ctx } = loadApp();
  ctx.location.hash = '#trends?company=greenhouse%3Aacme&family=ai-ml&split=roles&unit=count&metric=new&days=30&coverage=comparable';
  const link = ctx.location.hash;
  const linked = t.readHash();
  assert.equal(linked.family, 'ai-ml');
  assert.equal(t.split(), 'roles');
  assert.equal(t.unit(), 'count');
  answering(ctx, { ...picked({ a: [50, 60], b: [40, 45] }), watch_parents: ['ai-ml'] });
  await t.load(linked.family);
  assert.equal(t.hash(), link, 'the state reads back as the same link');
});

test('in a summed view, a pick counted from later marks where it joins and names no mover', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, golden('pick_counted_later_marks_where_it_joins'));
  t.setPicks([{ key: 'workday:nvidia', label: 'NVIDIA' }, { key: 'workday:amd', label: 'AMD' }]);
  await t.load(null);
  assert.match(nodes['trends-chart'].innerHTML, /counting for AMD starts/);
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Biggest'));
});

test('an empty comparable window says where per-board counting began', async () => {
  const { t, ctx, nodes } = loadApp();
  t.coverageSet('comparable');
  answering(ctx, { ...fixture(), stamps: [], totals: [], non_tech: [], series: [], ledger_start: STAMPS[0] });
  await t.load(null);
  assert.match(nodes['trends-empty'].textContent, /counting by board began Sep 13/);
});

test('a refusal note clears on the next pick the reader makes', async () => {
  const { t, ctx, nodes } = loadApp();
  ctx.document.getElementById('trends-co-note').textContent = 'No trend for Ghost yet.';
  answering(ctx, picked({ a: [50, 60], b: [40, 45] }));
  t.pick([{ key: 'greenhouse:acme', label: 'Acme' }]);
  assert.equal(nodes['trends-co-note'].textContent, '');
});


test('an empty answer does not decide the breakdown for a company', async () => {
  const { t, ctx } = loadApp();
  answering(ctx, { ...picked({}), series: [] });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  answering(ctx, picked({ a: [50, 60], b: [40, 45] }));
  await t.load(null);
  assert.equal(t.data().series.length, 2, 'a big company still opens on its categories');
});

test('Enter with nothing highlighted takes the top suggestion', async () => {
  const { t, ctx } = loadApp();
  const c = (key, label) => ({ key, label, openings: 50, boards: 1, atses: ['workday'] });
  answering(ctx, { companies: [c('workday:amd', 'AMD'), c('workday:amdocs', 'Amdocs')] });
  await t.suggest('amd');
  answering(ctx, picked({ a: [50, 60], b: [40, 45] }, [{ key: 'workday:amd', label: 'AMD' }]));
  const input = ctx.document.getElementById('trends-co-q');
  input.listeners.keydown.forEach(fn => fn({ key: 'Enter', preventDefault() {} }));
  same(t.picks().map(p => p.key), ['workday:amd']);
});

test('a held backlog under New says why nothing is new yet, with no empty tiles', async () => {
  const { t, ctx, nodes } = loadApp();
  t.metricSet('new');
  answering(ctx, { ...picked({}), metric: 'new', series: [] });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.match(nodes['trends-empty'].textContent, /Nothing counts as new at Acme yet/);
  assert.equal(nodes['trends-kpi'].hidden, true);
});


test('percentages leave out a marked step; the plotted line keeps it', async () => {
  const { t, ctx, nodes } = loadApp();
  // Five runs; a tech-filter change at the third doubles the line, the run after it settles
  // (also left out), and the last run grows 10%.
  answering(ctx, golden('tech_filter_doubles_one_category'));
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  t.setUnit('count', false);
  t.draw();
  // Net [200, 200, 200, 200, 220]: first to last is +20 openings (+10.0%), where the raw line
  // read +120 (+120%). Under Count the legend gives it in openings.
  assert.match(row(nodes['trends-legend'].innerHTML, 'a'), /\+20 openings/, 'the doubling was the filter');
});

test('a small count gets whole-number ticks', () => {
  const { t } = loadApp();
  const axis = t.niceAxis(2, true);
  assert.ok(axis.ticks.every(v => Number.isInteger(v)), JSON.stringify(axis.ticks));
  assert.equal(axis.dec, 0);
});


test('under New, the tab says from when openings count as new', async () => {
  const { t, ctx, nodes } = loadApp();
  t.metricSet('new');
  answering(ctx, { ...picked({ a: [null, 50], b: [null, 40] }), metric: 'new',
    new_counted_from: { 'greenhouse:acme': STAMPS[1] } });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  assert.match(nodes['trends-empty'].textContent, /New openings count for Acme from Sep 20/);
});


test('under a Company breakdown, one company’s found Board nets only its own line', async () => {
  const { t, ctx, nodes } = loadApp();
  const two = [{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }];
  answering(ctx, golden('found_board_nets_only_its_own_company'));
  t.setPicks(two);
  t.selectSplit('company');
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  t.setUnit('count', false);
  t.draw();
  const legend = nodes['trends-legend'].innerHTML;
  assert.match(row(legend, 'greenhouse:acme'), /→ \+0 openings/, 'Acme’s jump was its found Boards');
  assert.match(row(legend, 'lever:beta'), /\+50 openings/, 'Beta’s real growth that run is kept');
});


// ---- critique round 3: the line and its number agree, and nothing is left unsaid ----------
const FOUR = ['2026-09-13T00:00:00+00:00', '2026-09-14T00:00:00+00:00',
              '2026-09-15T00:00:00+00:00', '2026-09-16T00:00:00+00:00'];
function companies(series, extra) {
  return { version: 2, metric: 'stock', split_by: 'company', stamps: FOUR,
    totals: [1e4, 1e4, 1e4, 1e4], non_tech: [0, 0, 0, 0], watch_parents: [],
    series: series.map(([name, label, points]) => ({ name, label, points, latest: points[3] })),
    companies: series.map(([name, label]) => ({ key: name, label, board_keys: [name], atses: [name.split(':')[0]] })),
    company_totals: {}, epochs: [], discovered: [], uncounted: [],
    counted_since: Object.fromEntries(series.map(([name]) => [name, FOUR[0]])), ...extra };
}
const ACME = { key: 'greenhouse:acme', label: 'Acme', boardKeys: ['greenhouse:acme'] };
const BETA = { key: 'lever:beta', label: 'Beta', boardKeys: ['lever:beta'] };

test('under Change the plotted line is the one its percentage is read from', () => {
  const { t } = loadApp();
  showGolden(t, 'found_board_nets_only_its_own_company');
  t.setUnit('change', false);
  const [acme, beta] = t.data().series;
  // Google's line ended at 117 over a legend reading −0.2%: the found Boards are not drawn now.
  same(t.seriesValues(acme), [100, 100, 100, 100]);
  same(t.seriesValues(beta), [100, 100, 150, 150], 'another company’s step is not taken out of Beta');
});

test('duplicate removal is taken out only of the pick it can touch', () => {
  const { t } = loadApp();
  showGolden(t, 'duplicate_removal_touches_only_a_two_site_tenant');
  t.setUnit('change', false);
  const [acme, beta] = t.data().series;
  same(t.seriesValues(acme), [100, 100, 100, 100]);
  // Beta has one Board: duplicate removal cannot have moved it, so its fall is its own.
  same(t.seriesValues(beta), [100, 100, 80, 80]);
});

test('duplicate removal needs two sites of one Tenant, as Hot and the Space read it', () => {
  // Duplicate removal parks copies among one Tenant's sites (ADR-0186/0187). A company holding
  // two Tenants' Workday sites has nothing to deduplicate, so its fall is its own.
  const { t } = loadApp();
  showGolden(t, 'duplicate_removal_needs_two_sites_of_one_tenant');
  t.setUnit('change', false);
  same(t.seriesValues(t.data().series[0]), [100, 100, 80, 80]);
});

test('under Count a marked step breaks the line instead of drawing a climb', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'found_board_nets_only_its_own_company');
  t.setUnit('count', false);
  t.draw();
  const path = name => (nodes['trends-chart'].innerHTML.match(
    new RegExp(`class="series-line"[^>]*data-name="${name}" d="([^"]*)"`)) || [])[1] || '';
  assert.equal((path('greenhouse:acme').match(/M/g) || []).length, 2, path('greenhouse:acme'));
  assert.equal((path('lever:beta').match(/M/g) || []).length, 1);
});

test('a pick the view leaves out is named, with the reason', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.coverageSet('comparable');
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]]],
    { uncounted: ['lever:beta'], base: FOUR[0], coverage: 'comparable',
      counted_since: { 'greenhouse:acme': FOUR[0], 'lever:beta': FOUR[2] } }));
  t.draw();
  assert.match(nodes['trends-empty'].textContent,
    /Beta isn’t in this view: HeadStart began counting it after Sep 13/);
  assert.match(nodes['trends-scope'].textContent, /^1 company ·/, 'not "1 companies"');
});

test('a small line moves in openings, and no tile headlines it', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ small: [11, 13], big: [100, 150] }), stamps: STAMPS });
  t.setUnit('count', false);
  t.draw();
  assert.match(row(nodes['trends-legend'].innerHTML, 'small'), /\+2 openings/);
  assert.doesNotMatch(row(nodes['trends-legend'].innerHTML, 'small'), /%/);
  assert.match(nodes['trends-kpi'].innerHTML, /Biggest riser<\/span>\s*<span class="kpi-value">big/);
});

test('each company gets a sentence: its openings and which way they moved', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [1000, 1000, 1000, 998]], ['lever:beta', 'Beta', [100, 100, 150, 150]]]));
  t.draw();
  const html = nodes['trends-verdict'].innerHTML;
  assert.equal(nodes['trends-verdict'].hidden, false);
  assert.match(html, /<b>Acme<\/b>: [^—<]*— 998 tech openings; about flat over 3 days \(−0\.2%, −2 openings, about −5 a week\)\./);
  assert.match(html, /<b>Beta<\/b>: [^—<]*— 150 tech openings; up 50\.0% over 3 days \(\+50 openings, about \+117 a week\)\./);
  assert.match(html, /HeadStart has counted these companies since Sep 13 — too short to tell a trend from noise/);
});

test('the notes fit the view: no dashed line or reassignment caveat on whole companies', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]], ['lever:beta', 'Beta', [100, 100, 150, 150]]]));
  t.setUnit('change', false);
  t.draw();
  assert.doesNotMatch(nodes['trends-foot'].textContent, /dashed line/);
  assert.doesNotMatch(nodes['trends-chart'].innerHTML, /ref-line/);
  assert.equal(nodes['trends-how'].hidden, true);
  t.set(fixture(), null);
  t.setPicks([]);
  t.draw();
  assert.match(nodes['trends-foot'].textContent, /dashed line/);
  assert.equal(nodes['trends-how'].hidden, false);
});

test('with nothing measured, no caption describes a line', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: [], series: [], totals: [], non_tech: [] });
  t.draw();
  assert.equal(nodes['trends-foot'].textContent, '');
});

test('under a pick, a counting change that cannot move its lines is not marked', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ a: [100, 100, 100, 100] }), stamps: FOUR,
    series: [{ name: 'a', label: 'a', points: [100, 100, 100, 100], latest: 100 }],
    totals: [1e3, 1e3, 1e3, 1e3], non_tech: [0, 0, 0, 0],
    epochs: [{ ts: FOUR[2], changed: ['experience extraction changed'], fields: ['derivations_version'] }] });
  t.draw();
  assert.doesNotMatch(nodes['trends-chart'].innerHTML, /epoch-marker/);
});


// ---- code review of the third round -----------------------------------------------------
test('the mover floor is held to the openings a line really started with', () => {
  const { t, nodes } = loadApp();
  // 12 openings, then a found Board doubles it: adjusted, the head reads 24 and cleared 20.
  showGolden(t, 'found_board_doubles_a_small_line');
  t.setUnit('count', false);
  t.draw();
  // Net [24, 24, 24, 26]: two openings, and no percentage off a line that started at 12.
  assert.match(row(nodes['trends-legend'].innerHTML, 'a'), /↑ \+2 openings</);
});

test('a pick the ATS selection drops is told so, even under Comparable', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, { ...BETA, atses: ['lever'] }]);
  t.coverageSet('comparable');
  nodes['trends-ats-menu'].querySelectorAll = () => [
    { value: 'greenhouse', checked: true, parentElement: { hidden: false } },
    { value: 'lever', checked: false, parentElement: { hidden: false } }];
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]]],
    { uncounted: ['lever:beta'], base: FOUR[0], coverage: 'comparable',
      counted_since: { 'greenhouse:acme': FOUR[0], 'lever:beta': FOUR[0] } }));
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /Beta isn’t in this view: none of its boards are on the selected sources/);
});

test('how long a company has been counted comes from its counting, not the window', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  // A window of the last day, over a company counted since Sep 13: three days, not one.
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]]],
    { stamps: FOUR, series: [{ name: 'greenhouse:acme', label: 'Acme', points: [null, null, 100, 100], latest: 100 }],
      split_by: 'company', counted_since: { 'greenhouse:acme': FOUR[0] } }));
  t.draw();
  // Counted since Sep 13, three days: it is the window that is short, not the company.
  assert.match(nodes['trends-verdict'].innerHTML, /Acme<\/b>: [^—<]*too short a window to tell — 100 tech openings; \+0 openings over the last 24 hours\./);
});

// ---- critique round 4 ------------------------------------------------------------------------
const FIVE = [...FOUR, '2026-09-17T00:00:00+00:00'];

test('the sentence says how much of the chart’s move was not hiring', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'found_board_on_a_category_line');
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML,
    /Acme<\/b>: [^—<]*— 1,700 tech openings; about flat over 3 days \(\+0\.0%, \+0 openings\)\.<details class="verdict-why"><summary>Not hiring: \+200 openings<\/summary>\+200 openings from boards found later\./);
});

test('compared company by company, the heading asks how hiring compares', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]], ['lever:beta', 'Beta', [100, 100, 150, 150]]]));
  t.draw();
  assert.equal(nodes['trends-title'].textContent, 'How tech hiring compares at 2 companies');
});

test('Enter before the suggestions arrive picks the top one when they do', async () => {
  const { t, ctx, nodes } = loadApp();
  ctx.fetch = url => Promise.resolve({ ok: true, json: () => Promise.resolve(String(url).startsWith('/companies/suggest')
    ? { companies: [{ key: 'amazon:jobs', label: 'Amazon', openings: 9000, boards: 1, atses: ['amazon'] }] }
    : picked({ a: [50, 60] }, [{ key: 'amazon:jobs', label: 'Amazon' }])) });
  const input = nodes['trends-co-q'];
  input.value = 'amazon';
  input.listeners.keydown.forEach(fn => fn({ key: 'Enter', preventDefault() {} }));
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  same(t.picks().map(p => p.key), ['amazon:jobs']);
  assert.equal(input.value, '', 'cleared for the next name, not run on into it');
});


test('several picks summed get their own sentence, the sum of each company’s', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set({ ...picked({ a: [100, 110] }), stamps: STAMPS });
  t.setUnit('count', false);
  t.draw();
  // It read only "summed here — break down by Company", no move and no direction.
  assert.match(nodes['trends-verdict'].innerHTML, /These 2 companies<\/b>: [^—<]*— 110 tech openings; up 10\.0% over 7 days/);
});


test('duplicate removal is taken out of an Eightfold-only company too (Micron Technology)', () => {
  const { t } = loadApp();
  showGolden(t, 'micron_eightfold_only_company_steps_at_duplicate_removal');
  t.setUnit('change', false);
  const [micron, beta] = t.data().series;
  same(t.seriesValues(micron), [100, 100, 100, 100]);
  same(t.seriesValues(beta), [100, 100, 80, 80], 'one Board off Eightfold: its fall is its own');
});


// ---- critique round 5 ------------------------------------------------------------------------
test('the table names what its change leaves out, and the counting changes add up', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FOUR, split_by: 'family', totals: [1e4, 1e4, 1e4, 1e4], non_tech: [0, 0, 0, 0],
    series: [{ name: 'a', label: 'a', points: [764, 764, 1018, 1018], latest: 1018 }],
    discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 3, openings: 254 }] });
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });   // no failed load showing
  t.table(true);
  const html = nodes['trends-table'].innerHTML;
  assert.match(html, /Hiring, %<\/th><th scope="col">Hiring, openings<\/th><th scope="col">Counting changes, openings<\/th><th scope="col">Start, as counted/);
  assert.match(html, /\+254 openings/);
});

test('a custom range rides in the link and checks no preset', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  nodes['trends-since'].value = '2026-09-18T00:00';
  nodes['trends-since'].fire('change');
  assert.match(t.hash(), /since=2026-09-1\dT/);
  assert.doesNotMatch(t.hash(), /days=/);
});

test('a drilled category hands over to Search as that category', () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([{ ...ACME, boardKeys: ['greenhouse:acme'] }]);
  t.set({ ...picked({}), family_label: 'AI / Machine Learning' }, 'ai-ml');
  ctx.window.CFG.family_handoff = true; ctx.window.CFG.max_family_ids = 5000;
  nodes['trends-co-roles'].fire('click');
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  same(hash.getAll('board'), ['greenhouse:acme']);
  assert.equal(hash.get('family'), 'ai-ml');
  assert.equal(hash.get('family_label'), 'AI / Machine Learning');
  assert.equal(hash.get('q'), null, 'the category is the filter, not a query');
});

test('without the Space’s category filter, the category ranks the jobs and no pill claims it', () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([{ ...ACME, boardKeys: ['greenhouse:acme'] }]);
  t.set({ ...picked({}), family_label: 'Software Engineering (general)' }, 'software-engineering');
  ctx.window.CFG.family_handoff = false;
  nodes['trends-co-roles'].fire('click');
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  assert.equal(hash.get('family'), null);
  assert.equal(hash.get('q'), 'Software Engineering');
});


// ---- duplicate removals, sized (#649) --------------------------------------------------------

test('a leap one run puts straight back is a partial read, not hiring', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, golden('partial_read_put_straight_back'));
  t.setPicks([ACME]);
  await t.load(null);
  same(t.data().series.find(x => x.name === 'a').points, [26, null, 26, 27]);
  same(t.data().series.find(x => x.name === 'b').points, [100, 110, 120, 130], 'steady growth is kept');
  assert.match(nodes['trends-empty'].textContent, /1 run where a board was read only partly/);
});

test('a partial read is judged against the last point kept, and counted per run', async () => {
  const { t, ctx, nodes } = loadApp();
  answering(ctx, golden('partial_read_judged_against_the_last_point_kept'));
  t.setPicks([ACME]);
  await t.load(null);
  same(t.data().series.find(x => x.name === 'a').points, [26, null, 26, 0], 'the 26 after the leap is real');
  assert.match(nodes['trends-empty'].textContent, /1 run where a board was read only partly/);
});


test('the sentence names each cause of the non-hiring move, with its size', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'duplicate_postings_removed_named_as_the_cause');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML,
    /Acme<\/b>: [^—<]*— 1,010 tech openings; up 1\.0% over 3 days \(\+10 openings[^)]*\)\.<details class="verdict-why"><summary>Not hiring: −2,000 openings<\/summary>−2,000 openings from duplicate postings removed\./);
});

test('a run with duplicates removed beside a counting change names each by its size', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'nvidia_duplicates_beside_a_counting_change');
  t.setUnit('count', false);
  t.draw();
  // The refit run moved −2,000: −2,041 duplicates, +41 from the family change beside them.
  assert.match(nodes['trends-verdict'].innerHTML,
    /<\/summary>−2,041 openings from duplicate postings removed, \+41 openings from a role family assignment change\./);
  // The removals have their own figure, so the change that made them is not named again.
  assert.doesNotMatch(nodes['trends-verdict'].innerHTML, /duplicate removal change/);
});


test('Search says how many of its jobs the trend leaves out as non-tech', () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ a: [90, 100] }), stamps: STAMPS,
    company_totals: { 'greenhouse:acme': [100, 108] } });
  nodes['trends-co-roles'].fire('click');
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  assert.equal(hash.get('aside'), '8', '108 served, 100 tech');
});

test('the latest figure is one rule: a line now at none reads 0 in the tile and the sentence', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FOUR, totals: [1e3, 1e3, 1e3, 1e3], non_tech: [0, 0, 0, 0],
    series: [{ name: 'a', label: 'a', points: [100, 100, 100, 100], latest: 100 },
             { name: 'b', label: 'b', points: [83, 83, 83, null], latest: null }] });
  t.draw();
  assert.match(nodes['trends-kpi'].innerHTML, /<span class="kpi-value">100<\/span>/);
  assert.match(nodes['trends-verdict'].innerHTML, /Acme<\/b>: [^—<]*— 100 tech openings/);
});

test('a drill is titled for its category and its company', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), family_label: 'AI / Machine Learning' }, 'ai-ml');
  t.draw();
  assert.equal(nodes['trends-title'].textContent, 'How AI / Machine Learning hiring is moving at Acme');
});


test('a tracked role hands over to Search as that role', () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([{ ...ACME, boardKeys: ['greenhouse:acme'] }]);
  const legend = nodes['trends-legend'];
  legend.listeners.click.forEach(fn => fn({ target: { closest: sel => sel === '[data-hide]' ? null
    : sel === '[data-role]' ? { dataset: { role: 'watch:llm-genai', roleLabel: 'LLM / GenAI' } } : null } }));
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  assert.equal(hash.get('role'), 'llm-genai');
  assert.equal(hash.get('family_label'), 'LLM / GenAI');
  assert.equal(hash.get('family'), null);
});


test('the table heads a company\'s categories with its own total, and says why they need not sum', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ a: [100, 110], b: [50, 55] }), stamps: STAMPS });
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const html = nodes['trends-table'].innerHTML;
  assert.match(html, /<caption>The first row is the company’s hiring; the categories below add up to it/);
  assert.match(html, /<tr class="total"><th scope="row"><b>All tech roles<\/b><\/th><td>165<\/td>/);
});

test('a refit that moves openings between categories leaves them adding up to the company', () => {
  const { t, nodes } = loadApp();
  // a hires 20, then a refit moves 24 of its openings to b. The company hires +20; by openings a
  // reads +20 and b +0, so nothing sits between them. (Scaled, a read +16: a +4 "Between" row.)
  showGolden(t, 'refit_moves_openings_between_categories');
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const html = nodes['trends-table'].innerHTML;
  assert.doesNotMatch(html, /class="between"/);
  const a = html.split('</tr>').find(r => />a</.test(r));
  assert.match(a, /<td class="up">\+20 openings<\/td>/);
});

test('an unknown category says so', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), series: [], family_known: false }, 'nonsense-family');
  t.draw();
  assert.equal(nodes['trends-title'].textContent, 'No category called “nonsense-family”');
  assert.match(nodes['trends-empty'].textContent, /HeadStart has no category called “nonsense-family”/);
});


// ---- critique round 11 review ----------------------------------------------------------------
const MICRON = { key: 'eightfold:micron', label: 'Micron', boardKeys: ['eightfold:micron'] };

test('a duplicate-removal change is named only on the line of a pick it can touch', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'duplicate_removal_change_named_on_the_touched_pick_only');
  t.setUnit('count', false);
  t.draw();
  const [acme, micron] = nodes['trends-verdict'].innerHTML.split('</li>');
  assert.match(acme, /\+20 openings from a role family assignment change\./);
  assert.match(micron, /−20 openings from a duplicate removal change and a role family assignment change\./);
});

test('counting changes are named in words that read, and counted once each', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'three_counting_changes_named_once_each');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML,
    /\+50 openings from a role taxonomy refit, a tech filter change and a role family map edit\./);
});

test('under New a filter change and its week-later echo are one change', () => {
  const { t, nodes } = loadApp();
  // The change lands at its own run, and its echo a week on.
  showGolden(t, 'new_filter_change_and_its_echo_are_one_change');
  t.setUnit('count', false);
  t.draw();
  const html = nodes['trends-verdict'].innerHTML;
  assert.match(html, /from a tech filter change\./);
  assert.doesNotMatch(html, /2 tech filter changes/);
});

test('a rise over a duplicate-removal run is hiring, not a removal', () => {
  const { t, nodes } = loadApp();
  // Under New a duplicate-removal change has no size; removing duplicates cannot add openings,
  // so the +20 at its run is that run's hiring.
  showGolden(t, 'new_rise_over_a_duplicate_removal_run_is_hiring');
  t.setUnit('count', false);
  t.draw();
  const html = nodes['trends-verdict'].innerHTML;
  assert.match(html, /\+20 openings/);
  assert.doesNotMatch(html, /Not hiring/);
});

test('an older company with one run in the window has a short window, not a new company', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(companies([['greenhouse:acme', 'Acme', [null, null, null, 100]]], { counted_since: { 'greenhouse:acme': FOUR[0] } }));
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /too short a window to tell — 100 tech openings\./);
  assert.doesNotMatch(nodes['trends-verdict'].innerHTML, /too new/);
});

test('under Share a small line is no tile riser, and a short window gives no openings', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ small: [10, 19], big: [100, 120] }), stamps: STAMPS });
  t.setUnit('share', false);
  t.draw();
  assert.match(nodes['trends-kpi'].innerHTML, /Biggest riser<\/span>\s*<span class="kpi-value">big/);
  // A window of a day over a company counted for a week: shares, so no change in openings.
  t.set(companies([['greenhouse:acme', 'Acme', [null, null, 100, 104]]], { counted_since: { 'greenhouse:acme': FOUR[0] } }));
  t.draw();
  assert.doesNotMatch(nodes['trends-legend'].innerHTML, /opening/);
});

test('markers on one day are drawn as one, titled with every change', () => {
  const { t, nodes } = loadApp();
  t.set(golden('index_marks_counting_changes_and_takes_nothing_out'), null);
  t.draw();
  const svg = nodes['trends-chart'].innerHTML;
  assert.equal((svg.match(/class="epoch-marker"/g) || []).length, 1);
  assert.match(svg, /Aug 13 00:00 tech filter changed\nAug 13 12:00 role taxonomy refit/, 'each change at its own time');
});

// ---- critique round 12 ------------------------------------------------------------------------
test('a whole company’s line takes a counting change out by openings, as Hot does', () => {
  const { t, nodes } = loadApp();
  // A +100 filter change at FIVE[2] and its settling run; hiring +10, +20 either side of them.
  showGolden(t, 'whole_company_line_takes_a_filter_change_out_by_openings');
  t.setUnit('count', false);
  t.draw();
  // Scaled, the history before the step doubled and the line read +50; Hot sums the runs
  // outside the change: +10 + 20 = +30.
  assert.match(nodes['trends-verdict'].innerHTML, /\(\+30 openings/);
  assert.match(nodes['trends-verdict'].innerHTML, /\+100 openings from a tech filter change/);
});

test('the five largest sentences stand, with the tiles’ riser, and the rest fold', () => {
  const { t, nodes } = loadApp();
  const keys = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  t.setPicks(keys.map(k => ({ key: `lever:${k}`, label: k.toUpperCase(), boardKeys: [`lever:${k}`] })));
  // Largest first, as the payload orders them; G, the smallest, rises most, and F falls most.
  t.set(companies(keys.map((k, n) => [`lever:${k}`, k.toUpperCase(),
    k === 'g' ? [100, 100, 100, 200] : k === 'f' ? [300, 300, 300, 200] : k === 'h' ? [50, 50, 50, 50]
      : [700 - n * 100, 700 - n * 100, 700 - n * 100, 700 - n * 100]])));
  t.setUnit('count', false);
  t.draw();
  const [shownPart, folded] = nodes['trends-verdict'].innerHTML.split('<details');
  assert.deepEqual([...shownPart.matchAll(/<b>(\w)<\/b>/g)].map(m => m[1]), ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
    'five, then the faller F and the riser G kept in view');
  assert.match(folded, /1 more company/);
  assert.match(folded, /<b>H<\/b>/);
});

test('a category first seen inside the window reads as new, not flat', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ old: [50, 50], arch: [null, 32] }), stamps: STAMPS,
    counted_since: { 'greenhouse:acme': '2026-09-01T00:00:00+00:00' } });
  t.setUnit('count', false);
  t.draw();
  assert.match(row(nodes['trends-legend'].innerHTML, 'arch'), /new since Sep 20/);
  assert.doesNotMatch(row(nodes['trends-legend'].innerHTML, 'arch'), /\+0/);
});

test('one opening is one opening', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(companies([['greenhouse:acme', 'Acme', [1, 1, 1, 1]]]));
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /Acme<\/b>: [^—<]*— 1 tech opening;/);
});

test('every marked line is listed under the chart, a merged day at its biggest jump', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'two_changes_on_one_day_each_own_their_run');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'];
  assert.equal(list.hidden, false);
  // Each change at its own time, sized so they sum to the day's +200: the 21:19 jump is the
  // 21:19 change's own run, not the 18:00 change's settling run.
  assert.match(list.innerHTML, /Marked changes in this window \(2\)/);
  assert.match(list.innerHTML, /Sep 24 18:00<\/b>[^<]*— Acme \+1 opening</);
  assert.match(list.innerHTML, /Sep 24 21:19<\/b>[^<]*— Acme \+199 openings/);
});

test('the roles view says what its lines are', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(fixture(), null);
  t.click('software-engineering', 'roles');
  t.set({ ...picked({ 'watch:llm': [10, 12] }), stamps: STAMPS, family: 'ai-ml' }, 'ai-ml');
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /roles tracked by their titles inside this category/);
});


test('a trend opened from Hot says how Hot’s figure reads on it', () => {
  const { t, nodes } = loadApp();
  t.openTrend('greenhouse:bosch', 'Bosch', '2026-09-13T00:00:00+00:00', '440');
  t.readHash();
  const bosch = { key: 'greenhouse:bosch', label: 'Bosch', boardKeys: ['greenhouse:bosch', 'lever:bosch'] };
  t.setPicks([bosch]);
  t.set(companies([['greenhouse:bosch', 'Bosch', [100, 200, 300, 540]]]));
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /Hot’s \+440 net tech roles is one of Bosch’s 2 boards; this line sums all of them and reads \+440 openings\./);
  t.setPicks([{ ...bosch, boardKeys: ['greenhouse:bosch'] }]);
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /Hot’s \+440 net tech roles is this line’s change/);
});

// ---- critique round 13 ------------------------------------------------------------------------
test('a trend opened from Hot keeps Hot’s figure in its link and states both when they differ', () => {
  const { t, nodes } = loadApp();
  t.openTrend('google:careers', 'Google', '2026-09-13T00:00:00+00:00', '-27');
  t.readHash();
  const google = { key: 'google:careers', label: 'Google', boardKeys: ['google:careers'] };
  t.setPicks([google]);
  t.set(companies([['google:careers', 'Google', [100, 90, 80, 58]]]));
  t.draw();
  assert.match(nodes['trends-empty'].textContent,
    /Hot measured −27 net tech roles on this board over the same week; this line reads −42 openings\./);
  assert.match(t.hash(), /hot=-27&hot_board=google%3Acareers/, 'a reload keeps it');
});

test('the scope line says as of when, and how old a paused count is', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]]]));
  t.draw();
  assert.match(nodes['trends-scope'].textContent, /latest Sep 16 00:00 UTC — \d+ hours ago: the pipeline has written no newer count/);
});

test('a hand-off tells Search what the trend counted, and when', () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ a: [90, 100] }), stamps: STAMPS, company_totals: { 'greenhouse:acme': [100, 108] } });
  nodes['trends-co-roles'].fire('click');
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  assert.equal(hash.get('trend_n'), '100');
  assert.equal(hash.get('trend_at'), STAMPS[1]);
});

test('a counting change that did not move a line is not named for it', () => {
  const { t, nodes } = loadApp();
  // Two filter changes; only the second moves Acme.
  showGolden(t, 'two_filter_changes_only_one_moves_the_line');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /\+60 openings from a tech filter change\./);
});

test('under New, an echo whose change fell before the window is named as an echo', () => {
  const { t, nodes } = loadApp();
  // The change at Sep 11 12:00 sits before the window's second run; its echo lands Sep 19.
  showGolden(t, 'new_echo_of_a_change_before_the_window');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /−40 openings from the week-later echo of an earlier tech filter change\./);
});

test('a category sorted in by a counting change reads so, and its openings count as that change', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'category_sorted_in_by_a_counting_change');
  t.setUnit('count', false);
  t.draw();
  assert.match(row(nodes['trends-legend'].innerHTML, 'web'), /sorted in by a counting change, Sep 15/);
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const web = nodes['trends-table'].innerHTML.split('</tr>').find(r => />web</.test(r));
  assert.match(web, /<td>\+16 openings<\/td>/, 'its 16 arrived by the change');
});

test('Total is the sum of the Company breakdown, each company’s steps out of its own part', () => {
  const { t, nodes } = loadApp();
  // A duplicate-removal change touches only Micron (Eightfold); Acme hires +30 that run.
  const moves = () => [...nodes['trends-legend'].innerHTML.matchAll(/([−+]\d+) openings?/g)].map(m => Number(m[1].replace('−', '-')));
  showGolden(t, 'duplicate_removal_breakdown_by_company');
  t.setUnit('count', false);
  t.draw();
  const breakdown = moves();
  const summed = golden('duplicate_removal_total_sums_each_company');
  t.set({ ...summed, total: true, series: [{ ...summed.series_sum, label: 'All tech roles', latest: 280 }] });
  t.draw();
  assert.deepEqual(breakdown, [30, 0]);
  assert.deepEqual(moves(), [30], 'Total is the breakdown’s sum; summed whole it read 0');
});

test('a window ending in the past gives its time, not an age', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  nodes['trends-until'] = Object.assign(fakeEl(), { value: '2026-09-16T00:00' });
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]]]));
  t.draw();
  assert.match(nodes['trends-scope'].textContent, /latest Sep 16 00:00 UTC/);
  assert.doesNotMatch(nodes['trends-scope'].textContent, /hours ago/);
});

test('the marked-changes list gives each change’s size on each line, and counts repeats', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'filter_change_sized_in_the_marked_changes_list');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-changes'].innerHTML, /Sep 15 00:00<\/b> tech filter changed — Acme \+40 openings/);
});

test('a window ending before counting began says so', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  nodes['trends-until'] = Object.assign(fakeEl(), { value: '2026-09-01T00:00' });
  t.set({ ...companies([]), stamps: [], series: [], uncounted: ['greenhouse:acme'],
    counted_since: { 'greenhouse:acme': FOUR[0] }, ledger_start: FOUR[0] });
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /This window ends before HeadStart began counting companies, on Sep 13/);
});

// ---- critique round 14 ------------------------------------------------------------------------
test('Hot’s build time rides in UTC, whatever zone the reader is in', () => {
  // A reader in India: sliced to wall time and read back as local, the 11:14 UTC build read 05:44.
  const zone = process.env.TZ; process.env.TZ = 'Asia/Kolkata';
  try {
    const { t, nodes } = loadApp();
    t.openTrend('google:careers', 'Google', '2026-09-13T00:00:00+00:00', '-27', '2026-09-25T11:14:00+00:00');
    t.readHash();
    t.setPicks([{ key: 'google:careers', label: 'Google', boardKeys: ['google:careers'] }]);
    t.set(companies([['google:careers', 'Google', [100, 90, 80, 58]]]));
    t.draw();
    assert.match(nodes['trends-empty'].textContent, /in its list built Sep 25 11:14 UTC/);
  } finally {
    if (zone === undefined) delete process.env.TZ; else process.env.TZ = zone;
  }
});

test('a Hot row on a board counted for hours gets no week’s change beside it', () => {
  const { t, nodes } = loadApp();
  t.openTrend('adp:sitime', 'SiTime', '2026-09-13T00:00:00+00:00', '59');
  t.readHash();
  t.setPicks([{ key: 'adp:sitime', label: 'SiTime', boardKeys: ['adp:sitime'] }]);
  t.set(companies([['adp:sitime', 'SiTime', [null, null, 70, 70]]], { counted_since: { 'adp:sitime': FOUR[2] } }));
  t.draw();
  assert.match(nodes['trends-empty'].textContent, /Hot’s \+59 net tech roles is its board’s first days, counted since Sep 15/);
  assert.doesNotMatch(nodes['trends-empty'].textContent, /reads \+0/);
});

test('a change named for a line is every change whose left-out runs moved it, sized in the list', () => {
  const { t, nodes } = loadApp();
  // The change moved Acme by 0 at its run and −3 at its settling run, which is left out on every
  // line alike; so the sentence names it with that −3, and the list sizes it the same.
  showGolden(t, 'change_whose_settling_run_alone_moved_the_line');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /<\/summary>−3 openings from a tech filter change\./);
  assert.match(nodes['trends-changes'].innerHTML, /— Acme −3 openings/);
});

test('the list gives a counting change without the duplicates removed on its run', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'nvidia_counting_change_without_the_duplicates_on_its_run');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'].innerHTML;
  assert.match(list, /duplicate removal changed, role family assignment changed — NVIDIA −97 openings/, '−2,138 less the 2,041 removed');
  assert.match(list, /duplicate postings of NVIDIA removed[^<]*— NVIDIA −2,041 openings/, 'its own size, not the run’s −2,138');
});

test('a move under half an opening has no arrow', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ big: [100, 150], tiny: [30, 30] }), stamps: STAMPS });
  t.draw();
  assert.match(row(nodes['trends-legend'].innerHTML, 'tiny'), /→ \+0 openings/);
});

test('a window with no runs names the picks, not "0 companies"', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set({ ...companies([]), stamps: [], series: [] });
  t.draw();
  assert.equal(nodes['trends-scope'].textContent, '2 companies picked · no measurements in this window');
});


// ---- round 14 review ------------------------------------------------------------------------------
test('a change landing one run late is still left out whole (Amazon’s Sep 17 shape)', () => {
  const { t, nodes } = loadApp();
  // The filter change moved nothing at its own run and −400 at the next, its settling run.
  showGolden(t, 'filter_change_landing_one_run_late');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /<\/summary>−400 openings from a tech filter change\./);
  assert.doesNotMatch(nodes['trends-verdict'].innerHTML, /down 80/);
});

test('a refit leaves a company’s categories adding up to it, settling run and all', () => {
  const { t, nodes } = loadApp();
  // The refit moves 24 from a to b (the total holds); the run after, a hires 5.
  showGolden(t, 'refit_with_a_settling_run_still_adds_up');
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  assert.match(nodes['trends-table'].innerHTML, /<caption>The first row is the company’s hiring; the categories below add up to it\.<\/caption>/);
});

test('several picks in a drill leave an extraction change in the level total', () => {
  const { t, nodes } = loadApp();
  // Beta's category rises 20 at a run where only experience extraction changed, which re-sorts
  // levels but never a category's total: that +20 is hiring.
  showGolden(t, 'several_picks_in_a_drill_keep_an_extraction_change');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /\+20 openings/);
  assert.doesNotMatch(nodes['trends-verdict'].innerHTML, /Not hiring/);
});

test('a marker names duplicate removal only where a pick can be touched', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'duplicate_removal_change_named_only_where_a_pick_can_be_touched');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-chart'].innerHTML, /Sep 15 00:00 role family assignment changed/);
  assert.doesNotMatch(nodes['trends-chart'].innerHTML, /duplicate removal changed/);
});

test('a breakdown change is a step Back can undo', () => {
  const { t, ctx } = loadApp();
  const pushed = [];
  ctx.history = { pushState: (_, __, h) => pushed.push(h), replaceState: () => {} };
  ctx.location.hash = '#trends?company=greenhouse%3Aacme';
  t.setPicks([ACME]);
  t.set({ ...picked({ a: [100, 110], b: [50, 55] }), stamps: STAMPS });
  t.selectSplit('total');
  assert.equal(pushed.length, 1, 'a history entry, not a replace');
  assert.match(pushed[0], /by=total/);
});

// ---- critique round 15 ------------------------------------------------------------------------
test('the marked-changes list sizes each change on the company alone, as the sentence totals it', () => {
  const { t, nodes } = loadApp();
  // Nine categories, so the ninth is Other; a filter change moves each by +10 at FIVE[2].
  showGolden(t, 'nine_categories_one_folded_into_other');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'].innerHTML;
  assert.match(list, /tech filter changed — Acme \+90 openings<\/li>/, 'the company alone, no category beside it');
  assert.match(nodes['trends-verdict'].innerHTML, /Not hiring: \+90 openings/, 'the same +90');
});
test('a category a counting change sorted into existence reads so, and the list gives the company’s size', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'category_sorted_in_by_a_counting_change');
  t.setUnit('count', false);
  t.draw();
  assert.match(row(nodes['trends-legend'].innerHTML, 'web'), /sorted in by a counting change, Sep 15/);
  // The company moved 0 at the change and +1 at its settling run.
  assert.match(nodes['trends-changes'].innerHTML, /role family assignment changed — Acme \+1 opening<\/li>/);
});
test('each sentence opens with the answer in plain words', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 120]]]));
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /<b>Acme<\/b>: growing — 120 tech openings; up 20\.0%/);
});

test('a tracked role’s jobs link tells Search what the trend counted', () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(fixture(), null);
  t.click('software-engineering', 'roles');
  t.set({ ...picked({ 'watch:llm': [80, 84] }), stamps: STAMPS }, 'software-engineering');
  t.draw();
  const button = { dataset: { role: 'watch:llm', roleLabel: 'LLM / GenAI' }, closest: sel => sel === '[data-role]' ? button : null };
  nodes['trends-legend'].listeners.click.forEach(fn => fn({ target: button }));
  const hash = new URLSearchParams(ctx.location.hash.split('?')[1]);
  assert.equal(hash.get('role'), 'llm');
  assert.equal(hash.get('trend_n'), '84');
});

test('under New a duplicate-removal change is named as one, as under All openings', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'new_duplicate_removal_change_named_as_one');
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /<\/summary>−20 openings from a duplicate removal change\./);
});


// ---- round 15 review ------------------------------------------------------------------------------
test('the crosshair on a marker says what the list says, the company’s size included (Micron, Sep 17)', () => {
  const { t, nodes } = loadApp();
  // +32 at the change's own run, −296 at its settling run: one change of −264.
  showGolden(t, 'micron_filter_change_sized_with_its_settling_run');
  t.setUnit('count', false);
  t.draw();
  assert.deepEqual(t.tooltipNotes(t.geom(), 2), ['Sep 15 00:00 tech filter changed — Acme −264 openings']);
  assert.match(nodes['trends-changes'].innerHTML, /Acme −264 openings/);
});

test('a day’s marker names every change that day at its own time', () => {
  const { t } = loadApp();
  showGolden(t, 'day_marker_names_every_change_at_its_own_time');
  t.setUnit('count', false);
  t.draw();
  const at = [...t.geom().changesAt.keys()];
  assert.equal(at.length, 1, 'one marker for the day');
  const notes = t.tooltipNotes(t.geom(), at[0]);
  assert.equal(notes.length, 2);
  assert.match(notes[0], /^Sep 24 11:32 tech filter changed — Acme −11 openings$/);
  assert.match(notes[1], /^Sep 24 21:19 role family assignment changed — Acme \+10 openings$/);
});
test('each lead word says the answer', () => {
  const lead = points => {
    const { t, nodes } = loadApp();
    t.setPicks([ACME]);
    t.set(companies([['greenhouse:acme', 'Acme', points]]));
    t.setUnit('count', false);
    t.draw();
    return nodes['trends-verdict'].innerHTML.match(/<\/b>: ([^—]*) —/)[1];
  };
  assert.equal(lead([100, 100, 100, 80]), 'shrinking');
  assert.equal(lead([100, 100, 100, 100]), 'holding steady');
  assert.equal(lead([10, 10, 10, 60]), 'more openings');
});

test('Share’s table names what its figures are', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({ a: [100, 110], b: [50, 55] }), stamps: STAMPS });
  t.setUnit('share', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const html = nodes['trends-table'].innerHTML;
  assert.match(html, /Share, relative change/);
  assert.match(html, /All tech roles \(of all its openings\)/);
});

// ---- job turnover (ADR-0227) ------------------------------------------------------------------
// Acme opens and closes about as many jobs as it holds: flat by net, busy by turnover. The Sep 15
// tech-filter change lands 400 jobs that look newly posted, so its run and the run after it are
// left out of opened and closed, exactly as they are out of the net change.
function busyAcme(extra) {
  return { ...golden('busy_company_turnover_leaves_out_a_filter_change'), ...extra };
}

test('a company sentence gives the jobs its net change is made of', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(busyAcme());
  t.draw();
  // #684's shape: the answer first, then the move, then what it is made of, in the main text.
  assert.match(nodes['trends-verdict'].innerHTML,
    /<b>Acme<\/b>: holding steady — 1,000 tech openings; [^<]* — about 500 opened, 490 closed, closures not counted on 1 board\./);
});

test('turnover stays in the main text, never in the not-hiring disclosure', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'busy_company_step_disclosed_turnover_kept');
  t.draw();
  const html = nodes['trends-verdict'].innerHTML;
  assert.match(html, /<details class="verdict-why">/, 'the tech-filter step is disclosed');
  const [main, why] = html.split('<details class="verdict-why">');
  assert.match(main, /about 500 opened, 490 closed/);
  assert.doesNotMatch(why, /opened/);
});

test('turnover that began inside the window says from when', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(busyAcme({ turnover_since: FOUR[1], closures_unseen: {} }));
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, / — about 500 opened, 490 closed since Sep 14[.;]/);
});

test('the table gives each line its opened and closed', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(busyAcme());
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const html = nodes['trends-table'].innerHTML;
  assert.match(html, /Counting changes, openings<\/th><th scope="col">Opened<\/th><th scope="col">Closed<\/th>/);
  assert.match(html, /<td>500<\/td><td>490<\/td>/);
});

test('the index gets a hiring net from its turnover, and table columns too', () => {
  // The Space has already left the Sep 15 change's runs out (gaps), Board by Board, and names
  // them in `turnover_left_out`, which is what the sentence's closing clause rests on.
  const { t, nodes } = loadApp();
  t.setPicks([]);
  const index = golden('index_turnover_with_a_counting_change_left_out');
  t.set(index);
  t.setUnit('count', false);
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML,
    /<b>All tech roles<\/b>: about \+10 net from hiring — about 50 opened, 40 closed, closures not counted on 3 boards, runs where HeadStart changed how it counts left out\./);
  assert.doesNotMatch(nodes['trends-verdict'].innerHTML, /HeadStart has counted/);
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  assert.match(nodes['trends-table'].innerHTML, /<td>50<\/td><td>40<\/td>/);
  t.set({ ...index, turnover_left_out: [], epochs: [], notes: [], closures_unseen: {} });
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /about \+10 net from hiring — about 50 opened, 40 closed\./,
    'no counting change in the window, so nothing is said about one');
});

test('a Hot row shows the week’s opened and closed, and Volume leads with opened', () => {
  const { t } = loadApp();
  const amazon = { net: -3, opened: 1396, closed: 1399, stock: 9081, new7: 1300, rate: 14 };
  assert.equal(t.hotMeasure.volume(amazon).big, '1396');
  assert.match(t.hotMeasure.expansion(amazon).sub, /1396 opened · 1399 closed this week/);
  assert.match(t.hotMeasure.rate(amazon).sub, /^1300 new and still open of 9081 · 1396 opened/,
    'Rate divides new7, so its row leads with it');
});

// ---- critique round 16 ------------------------------------------------------------------------
test('a duplicate removal scales the history before it, so doubled growth is halved (Micron)', () => {
  const { t, nodes } = loadApp();
    // 1,000 real jobs listed twice (2,000); 50 real hires showed as +100; then the 1,050 copies are
  // removed, and 10 more are hired.
showGolden(t, 'duplicate_removal_scales_the_history_before_it');
  t.setUnit('count', false);
  t.draw();
  const html = nodes['trends-verdict'].innerHTML;
  assert.match(html, /\(\+60 openings/, '50 + 10 real hires; lifted, it read +110');
  assert.match(html, /Not hiring: −1,000 openings/);
  assert.match(nodes['trends-changes'].innerHTML, /duplicate postings of Micron removed — Micron −1,000 openings/,
    'the list sizes the removal by what it does to the line, so it sums to the sentence');
});

test('Comparable says its base moved only when the window starts before counting by board', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.coverageSet('comparable');
  // Asked from before the run the cohort starts at, but after counting by board began: the
  // window starts at the first run inside it, and nothing was moved.
  nodes['trends-since'] = Object.assign(fakeEl(), { value: '2026-09-13T12:00' });
  t.set({ ...companies([['greenhouse:acme', 'Acme', [100, 100, 100, 100]]]), base: FOUR[1], ledger_start: FOUR[0] });
  t.draw();
  assert.doesNotMatch(nodes['trends-empty'].textContent, /the first run it counted by board/);
});

test('a link’s values are read case-blind, as its company keys are', () => {
  const { t, ctx } = loadApp();
  ctx.location.hash = '#trends?company=greenhouse%3Aacme&by=TOTAL&unit=COUNT';
  t.readHash();
  assert.equal(t.top(), 'total');
  assert.equal(t.unit(), 'count');
});

test('a company counted from a later run is not sized by a change before it (Zomato’s phantom +2)', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'company_counted_later_is_not_sized_by_a_change_before_it');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'].innerHTML;
  assert.match(list, /tech filter changed — Acme \+10 openings<\/li>/);
  assert.doesNotMatch(list, /Zomato/, 'its first run is when counting began, not a change');
});

test('before a removal a change counts at the scale the removal leaves, and the list sums to the sentence', () => {
  const { t, nodes } = loadApp();
    // A filter change adds 200 while every job is listed twice; then half the list is removed.
showGolden(t, 'change_before_a_removal_counts_at_the_scale_it_leaves');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'].innerHTML;
  assert.match(list, /tech filter changed — Micron \+100 openings/, 'its 200 were 100 real jobs');
  assert.match(list, /duplicate postings of Micron removed — Micron −1,000 openings/);
  assert.match(nodes['trends-verdict'].innerHTML, /Not hiring: −900 openings/);
});


// ---- round 16 review ------------------------------------------------------------------------------
test('a removal alone on its run leaves a company’s categories adding up to it', () => {
  const { t, nodes } = loadApp();
    // Two categories, every job listed twice; a removal of half, alone on its run.
showGolden(t, 'removal_alone_on_its_run_leaves_categories_adding_up');
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const cells = name => nodes['trends-table'].innerHTML.split('</tr>').find(r => r.includes(`>${name}<`));
  // 50 real hires in a and 10 after: +60 in all, +60 in a, 0 in b.
  assert.match(cells('All tech roles'), /<td class="up">\+60 openings<\/td>/);
  assert.match(cells('a'), /<td class="up">\+60 openings<\/td>/);
  assert.match(cells('b'), /<td class="flat">\+0 openings<\/td>/);
});

test('a change settling on a removal’s run is sized at that run’s own scale', () => {
  const { t, nodes } = loadApp();
    // A filter change at [2] adds 200, then half the list is removed at [3], its settling run,
  // where 50 more also land: the list must say +150 (+100 scaled, +50 at the removal's run), as
  // the sentence does; scaled whole it said +125.
showGolden(t, 'change_settling_on_a_removal_run_is_sized_at_that_runs_scale');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'].innerHTML;
  const total = [...list.matchAll(/— Micron ([−+][\d,]+) opening/g)].reduce((a, m) => a + Number(m[1].replace('−', '-').replace(',', '')), 0);
  const said = nodes['trends-verdict'].innerHTML.match(/Not hiring: ([−+][\d,]+) opening/)[1];
  assert.equal(total, Number(said.replace('−', '-').replace(',', '')), 'the list sums to the sentence');
  assert.match(list, /tech filter changed — Micron \+150 openings/);
});

test('with several picks each category scales by its own companies’ removals and they add up to the Total', () => {
  const { t, nodes } = loadApp();
    // Micron listed every job twice until half its list is removed at [2]; Acme is counted once.
showGolden(t, 'each_category_scales_by_its_own_companies_removals');
  t.setUnit('count', false);
  t.draw();
  nodes['trends-error'] = Object.assign(fakeEl(), { hidden: true });
  t.table(true);
  const cells = name => nodes['trends-table'].innerHTML.split('</tr>').find(r => r.includes(`>${name}<`));
  // Acme +10 in each; Micron's 50 + 10 real hires all in a. Unscaled, a read +120.
  assert.match(cells('All tech roles'), /<td class="up">\+80 openings<\/td>/);
  assert.match(cells('a'), /<td class="up">\+70 openings<\/td>/);
  assert.match(cells('b'), /<td class="up">\+10 openings<\/td>/);
});

test('inside a category the marked removal is sized, so the list sums to the sentence', () => {
  const { t, nodes } = loadApp();
  showGolden(t, 'removal_inside_a_category_is_sized_on_its_levels');
  t.setUnit('count', false);
  t.draw();
  const list = nodes['trends-changes'].innerHTML;
  const said = nodes['trends-verdict'].innerHTML.match(/Not hiring: ([−+][\d,]+) opening/);
  assert.ok(said, nodes['trends-verdict'].innerHTML);
  const total = [...list.matchAll(/— Micron, ai-ml ([−+][\d,]+) opening/g)].reduce((a, m) => a + Number(m[1].replace('−', '-').replace(',', '')), 0);
  assert.equal(total, Number(said[1].replace('−', '-').replace(',', '')), list);
});

test('a refused pick takes its chart and sentence with it', async () => {
  const { t, ctx, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 100, 120]]]));
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /Acme/);
  answering(ctx, { error: 'unknown company: greenhouse:acme' }, 400);
  await t.load(null);
  assert.equal(nodes['trends-verdict'].innerHTML, '', 'no sentence left for a company that is gone');
});

test('the roles view marks the changes that moved its roles', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set(fixture(), null);
  t.click('software-engineering', 'roles');
  showGolden(t, 'roles_view_marks_the_changes_that_moved_its_roles');
  t.draw();
  assert.match(nodes['trends-chart'].innerHTML, /class="epoch-marker"/);
  assert.match(nodes['trends-changes'].innerHTML, /tech filter changed — watch:llm \+16 openings/);
});
