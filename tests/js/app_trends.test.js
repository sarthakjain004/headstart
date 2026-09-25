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
    console, CFG: {}, URLSearchParams, Date, Math, isNaN, setTimeout, clearTimeout,
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
    + ' unit: () => trendUnit, clickUnit: pickUnit, hash: trendHash, pick: setPicks, netOfSteps,'
    + ' table: toggleTrendsTable,'
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
  const f = fixture();
  f.epochs = [{ ts: f.stamps[1], changed: ['tech filter changed'] }];
  t.set(f, null);
  t.draw();
  const svg = nodes['trends-chart'].innerHTML;
  assert.match(svg, /class="epoch-marker"/);
  assert.match(svg, /Counting changed here: tech filter changed/);
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
  assert.match(nodes['trends-co-note'].textContent, /No trend for Ghost yet/);
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
  answering(ctx, { ...picked({ a: [50, 900], b: [40, 45] }),
    discovered: [{ ts: STAMPS[1], company: 'greenhouse:acme', boards: 83, openings: 1048 }] });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  const svg = nodes['trends-chart'].innerHTML;
  assert.match(svg, /class="found-marker"/);
  assert.match(svg, /83 more boards of Acme found here: 1,048 tech openings across the company, already open/);
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
  const epoch = (changed, field) => [{ ts: STAMPS[1], changed: [changed], fields: [field] }];
  const hdr = [{ key: 'taleo_enterprise:hdr/1', label: 'HDR', board_keys: ['taleo_enterprise:hdr/1', 'taleo_enterprise:hdr/2'],
    atses: ['taleo_enterprise'] }];
  answering(ctx, { ...picked({ a: [50, 90], b: [40, 45] }, hdr), epochs: epoch('duplicate removal changed', 'dedup_version') });
  t.setPicks([{ key: 'taleo_enterprise:hdr/1', label: 'HDR' }]);
  await t.load(null);
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Biggest'));
  answering(ctx, { ...picked({ a: [50, 90], b: [40, 45] }, hdr), epochs: epoch('tech filter changed', 'tech_filter_version') });
  await t.load(null);
  assert.ok(!nodes['trends-kpi'].innerHTML.includes('Biggest'), 'Wipro’s +74.7% held a +25% filter step');
  answering(ctx, { ...picked({ a: [50, 90], b: [40, 45] }, hdr),
    epochs: epoch('experience/salary extraction changed', 'derivations_version') });
  await t.load(null);
  assert.ok(nodes['trends-kpi'].innerHTML.includes('Biggest riser'), 'extraction moves levels, not these lines');
  // a one-Board company has no copies for duplicate removal to take
  answering(ctx, { ...picked({ a: [50, 90], b: [40, 45] }), epochs: epoch('duplicate removal changed', 'dedup_version') });
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
  const two = [{ key: 'workday:nvidia', label: 'NVIDIA' }, { key: 'workday:amd', label: 'AMD' }];
  answering(ctx, { ...picked({ a: [50, 400], b: [40, 45] }, two),
    counted_since: { 'workday:nvidia': STAMPS[0], 'workday:amd': STAMPS[1] } });
  t.setPicks(two);
  await t.load(null);
  assert.match(nodes['trends-chart'].innerHTML, /Counting for AMD starts here/);
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
  const stamps = ['2026-09-13T00:00:00+00:00', '2026-09-14T00:00:00+00:00',
                  '2026-09-15T00:00:00+00:00', '2026-09-16T00:00:00+00:00', '2026-09-17T00:00:00+00:00'];
  answering(ctx, { ...picked({ a: [100, 100, 200, 200, 220], b: [100, 100, 100, 100, 100] }),
    stamps, totals: [1e4, 1e4, 1e4, 1e4, 1e4], non_tech: [0, 0, 0, 0, 0],
    series: [{ name: 'a', label: 'A', points: [100, 100, 200, 200, 220], latest: 220 },
             { name: 'b', label: 'B', points: [100, 100, 100, 100, 100], latest: 100 }],
    epochs: [{ ts: stamps[2], changed: ['tech filter changed'], fields: ['tech_filter_version'] }] });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  await t.load(null);
  t.setUnit('count', false);
  t.draw();
  // Net [200, 200, 200, 200, 220]: first to last is +10.0%, where the raw line read +120%.
  assert.match(row(nodes['trends-legend'].innerHTML, 'a'), /\+10\.0%/, 'the doubling was the filter');
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
  const stamps = ['2026-09-13T00:00:00+00:00', '2026-09-14T00:00:00+00:00',
                  '2026-09-15T00:00:00+00:00', '2026-09-16T00:00:00+00:00'];
  const two = [{ key: 'greenhouse:acme', label: 'Acme' }, { key: 'lever:beta', label: 'Beta' }];
  answering(ctx, { ...picked({}, two), split_by: 'company', stamps, totals: [1e4, 1e4, 1e4, 1e4],
    non_tech: [0, 0, 0, 0],
    series: [{ name: 'greenhouse:acme', label: 'Acme', points: [100, 100, 300, 300], latest: 300 },
             { name: 'lever:beta', label: 'Beta', points: [100, 100, 150, 150], latest: 150 }],
    discovered: [{ ts: stamps[2], company: 'greenhouse:acme', boards: 3, openings: 200 }] });
  t.setPicks(two);
  t.selectSplit('company');
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  t.setUnit('count', false);
  t.draw();
  const legend = nodes['trends-legend'].innerHTML;
  assert.match(row(legend, 'greenhouse:acme'), /\+0\.0%|→/, 'Acme’s jump was its found Boards');
  assert.match(row(legend, 'lever:beta'), /\+50\.0%/, 'Beta’s real growth that run is kept');
});

test('a step on a gap lands on the line’s next point', () => {
  const { t, ctx } = loadApp();
  void ctx;
  const stamps = ['a', 'b', 'c', 'd'];
  t.set({ ...picked({}), stamps, series: [], discovered: [],
    epochs: [{ ts: 'b', changed: ['tech filter changed'], fields: ['tech_filter_version'] }] });
  t.setPicks([{ key: 'greenhouse:acme', label: 'Acme' }]);
  // Adjusted backwards: the latest value stays real, the history before the step is scaled.
  same(t.netOfSteps([100, null, 200, 220]), [200, null, 200, 220]);
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
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 300, 300]], ['lever:beta', 'Beta', [100, 100, 150, 150]]],
    { discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 3, openings: 200 }] }));
  t.setUnit('change', false);
  const [acme, beta] = t.data().series;
  // Google's line ended at 117 over a legend reading −0.2%: the found Boards are not drawn now.
  same(t.seriesValues(acme), [100, 100, 100, 100]);
  same(t.seriesValues(beta), [100, 100, 150, 150], 'another company’s step is not taken out of Beta');
});

test('duplicate removal is taken out only of the pick it can touch', () => {
  const { t } = loadApp();
  t.setPicks([{ ...ACME, key: 'workday:acme/a', boardKeys: ['workday:acme/a', 'workday:acme/b'] }, BETA]);
  t.set(companies([['workday:acme/a', 'Acme', [100, 100, 80, 80]], ['lever:beta', 'Beta', [100, 100, 80, 80]]],
    { epochs: [{ ts: FOUR[2], changed: ['duplicate removal changed'], fields: ['dedup_version'] }] }));
  t.setUnit('change', false);
  const [acme, beta] = t.data().series;
  same(t.seriesValues(acme), [100, 100, 100, 100]);
  // Beta has one Board: duplicate removal cannot have moved it, so its fall is its own.
  same(t.seriesValues(beta), [100, 100, 80, 80]);
});

test('under Count a marked step breaks the line instead of drawing a climb', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 300, 300]], ['lever:beta', 'Beta', [100, 100, 150, 150]]],
    { discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 3, openings: 200 }] }));
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
  assert.match(html, /<b>Acme<\/b>: 998 tech openings; about flat over 3 days \(−0\.2%, −2 openings, about −5 a week\)\./);
  assert.match(html, /<b>Beta<\/b>: 150 tech openings; up 50\.0% over 3 days \(\+50 openings, about \+117 a week\)\./);
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
  t.setPicks([ACME]);
  // 12 openings, then a found Board doubles it: adjusted, the head reads 24 and cleared 20.
  t.set({ ...picked({}), stamps: FOUR, totals: [1e3, 1e3, 1e3, 1e3], non_tech: [0, 0, 0, 0],
    series: [{ name: 'a', label: 'a', points: [12, 12, 24, 26], latest: 26 }],
    discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 1, openings: 12 }] });
  t.setUnit('count', false);
  t.draw();
  // Net [24, 24, 24, 26]: two openings, and no percentage off a line that started at 12.
  assert.match(row(nodes['trends-legend'].innerHTML, 'a'), /↑ \+2 openings</);
});

test('a counting change is taken out by its size in openings, never scaled', () => {
  const { t } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FIVE, series: [], discovered: [],
    epochs: [{ ts: FIVE[2], changed: ['tech filter changed'], fields: ['tech_filter_version'] }] });
  // Microsoft's architecture line: 5 → 4, a filter change to 58, then flat. Scaled by 14.5,
  // the one real opening became −12; by openings it stays −1.
  // The run after the change (58 → 59) settles and goes too: net [60, 59, 59, 59, 59].
  same(t.netOfSteps([5, 4, 58, 59, 59]), [60, 59, 59, 59, 59]);
});

test('found openings off zero lift the line', () => {
  const { t } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FOUR, series: [], epochs: [],
    discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 1, openings: 40 }] });
  // Found openings were open all along: the history is lifted by them.
  same(t.netOfSteps([0, 0, 40, 42]), [40, 40, 40, 42]);
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
  assert.match(nodes['trends-verdict'].innerHTML, /Acme<\/b>: 100 tech openings; this window is too short to show a direction\./);
});

// ---- critique round 4 ------------------------------------------------------------------------
const FIVE = [...FOUR, '2026-09-17T00:00:00+00:00'];

test('a counting change that lands over two runs is left out whole (Amazon, Sep 17)', () => {
  const { t } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FIVE, series: [], discovered: [],
    epochs: [{ ts: FIVE[2], changed: ['tech filter changed'], fields: ['tech_filter_version'] }] });
  const net = t.netOfSteps([9000, 9000, 9308, 8869, 8880]).map(v => Math.round(v));
  // +308 at the change and −439 the run after were one change; only the last +11 is hiring.
  same(net, [8869, 8869, 8869, 8869, 8880]);
});

test('found openings lift the history rather than scale it, so a sum moves by its parts', () => {
  const { t } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FOUR, series: [], epochs: [],
    discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 2, openings: 200 }] });
  // Scaled, the +10 of real growth before the find became +28; lifted, it stays +10.
  same(t.netOfSteps([100, 110, 310, 320]), [300, 310, 310, 320]);
});

test('the sentence says how much of the chart’s move was not hiring', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FOUR, split_by: 'family', totals: [1e4, 1e4, 1e4, 1e4], non_tech: [0, 0, 0, 0],
    series: [{ name: 'a', label: 'a', points: [1000, 1000, 1200, 1200], latest: 1200 },
             { name: 'b', label: 'b', points: [500, 500, 500, 500], latest: 500 }],
    discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 3, openings: 200 }] });
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML,
    /Acme<\/b>: 1,700 tech openings; about flat over 3 days \(\+0\.0%, \+0 openings\); the chart’s other \+200 openings came in runs marked as counting changes or boards found later\./);
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


test('several picks summed get a pointer, not a figure that is nearly their sum', () => {
  const { t, nodes } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set({ ...picked({ a: [100, 110] }), stamps: STAMPS });
  t.draw();
  assert.match(nodes['trends-verdict'].innerHTML, /These 2 companies<\/b>: summed here — break down by Company/);
});

test('a found Board on a whole company line is lifted by its own size, keeping that run’s hiring', () => {
  const { t } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 310, 310]], ['lever:beta', 'Beta', [50, 50, 50, 50]]],
    { discovered: [{ ts: FOUR[2], company: 'greenhouse:acme', boards: 2, openings: 200 }] }));
  t.setUnit('count', false);
  const acme = t.data().series[0];
  // 200 were found; the other 10 that run were hiring and stay in (by the run's jump, 210).
  same(t.netOfSteps(acme.points, acme), [300, 300, 310, 310]);
});


test('duplicate removal is taken out of an Eightfold-only company too (Micron Technology)', () => {
  const { t } = loadApp();
  t.setPicks([{ key: 'eightfold:careers.micron.com', label: 'Micron Technology', boardKeys: ['eightfold:careers.micron.com'] }, BETA]);
  t.set(companies([['eightfold:careers.micron.com', 'Micron Technology', [1887, 1887, 20, 20]], ['lever:beta', 'Beta', [100, 100, 80, 80]]],
    { epochs: [{ ts: FOUR[2], changed: ['duplicate removal changed'], fields: ['dedup_version'] }] }));
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
  assert.match(html, /Change, hiring only<\/th><th scope="col">Counting changes, openings<\/th><th scope="col">Start, as counted/);
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
test('duplicate postings removed later are taken out of that company’s line, exactly', () => {
  const { t } = loadApp();
  t.setPicks([ACME, BETA]);
  t.set(companies([['greenhouse:acme', 'Acme', [100, 100, 95, 96]], ['lever:beta', 'Beta', [50, 50, 50, 50]]],
    { evicted: [{ ts: FOUR[2], company: 'greenhouse:acme', count: 7 }] }));
  t.setUnit('count', false);
  const [acme, beta] = t.data().series;
  // 7 duplicates went; the other +2 that run was hiring and stays.
  same(t.netOfSteps(acme.points, acme), [93, 93, 95, 96]);
  same(t.netOfSteps(beta.points, beta), [50, 50, 50, 50]);
});

test('a category line does not guess where a company’s removals fell', () => {
  const { t } = loadApp();
  t.setPicks([ACME]);
  t.set({ ...picked({}), stamps: FOUR, series: [], epochs: [], discovered: [],
    evicted: [{ ts: FOUR[2], company: 'greenhouse:acme', count: 7 }] });
  const line = { name: 'software-engineering', points: [100, 100, 95, 96] };
  same(t.netOfSteps(line.points, line), [100, 100, 95, 96]);
});
