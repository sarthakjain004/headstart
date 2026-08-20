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

const APP_JS = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui', 'static', 'app.js');

function fakeEl() {
  return {
    // `value: ''` so the load-time `go()` call (ADR-0074 — the Search tab browses on load,
    // reading `#q` even when this harness only cares about Trends) sees an empty query
    // rather than throwing on `undefined.trim()`.
    innerHTML: '', textContent: '', hidden: false, style: {}, value: '',
    // The redesigned drawTrends() queries into #trends-chart/#trends-legend for the hover layer
    // and the emphasis pass (querySelector/querySelectorAll). Null/empty is the correct stub
    // answer — every call site already guards on "not found" for the real DOM's own sake (the
    // crosshair group doesn't exist until the first draw), so this never needs to be smarter.
    querySelectorAll: () => [], querySelector: () => null,
    setAttribute() {}, getAttribute: () => null, addEventListener() {},
    tabIndex: 0, classList: { toggle() {}, add() {}, remove() {} },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
  };
}

/** A fresh evaluation of app.js per test, so module-level state never leaks between them.
 *
 * `fetches` records every URL requested. It is the only way to tell "the click was ignored"
 * from "the click ran and happened to land on the same split" — asserting on `trendSplit`
 * alone passes either way, because 'bands' is also the fallback. */
function loadApp() {
  const nodes = {};
  const fetches = [];
  const ctx = {
    document: {
      getElementById: id => (nodes[id] ||= fakeEl()),
      addEventListener() {},
      querySelectorAll: () => [],
      // seriesColor() reads categorical-slot custom properties off :root at draw time (a theme
      // flip repaints correctly instead of freezing on a hardcoded hex array — see app.js). The
      // stub's exact color is irrelevant to every test here; only the structural HTML is asserted.
      documentElement: { getAttribute: () => null, setAttribute() {} },
    },
    window: { addEventListener() {}, location: { hash: '' } },
    location: { hash: '' },
    console, CFG: {}, URLSearchParams, Date, Math, isNaN,
    getComputedStyle: () => ({ getPropertyValue: () => '#000000' }),
    fetch: url => { fetches.push(String(url)); return Promise.resolve({ ok: false }); },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { draw: drawTrends, click: trendClick, split: () => trendSplit,'
    + ' chartMax: CHART_MAX,'
    + ' atsSelected: trendAtsSelected, atsLabel: trendAtsLabel, atsToggle: toggleAtsPopover,'
    + ' colorSlot: name => seriesColorAssignment.get(name),'
    + ' set: (d, drill) => { trendData = d; trendDrill = drill || null; } };';
  vm.runInNewContext(src, ctx);
  // The page fetches on load (the feed, the trends chart). Those are not what any test here is
  // asserting about, so the log starts empty from the caller's point of view.
  fetches.length = 0;
  return { t: ctx.__t, nodes, fetches };
}

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
  t.draw();
  // count unit (not share) makes the summed value land on a fixed, easy-to-check number.
  const other = row(nodes['trends-legend'].innerHTML, '__other__');
  assert.match(other, /Other \(2 smaller categories\)/);
});

test('clicking a marked row opens the roles split it advertised', () => {
  const { t } = loadApp();
  t.set(fixture(), null);
  t.click('software-engineering');
  assert.equal(t.split(), 'roles');
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
  // Scoped to these rows, not the whole legend: a future real toggle must not fail this.
  assert.doesNotMatch(charted, /aria-pressed/);
  assert.doesNotMatch(other, /aria-pressed/);
  // The role belongs on the inner .row so the <li> keeps its implicit `listitem`.
  assert.match(charted, /<span class="row"[^>]*role="button"/);
});

test('the Other row is inert — clicking it issues no request', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  // `__other__` is not a real family name, so trendClick's findIndex(-1) guard catches it —
  // the SAME guard an unknown/mistyped name relies on, not a special case added for Other.
  t.click('__other__');
  assert.deepEqual(fetches, []);
});

test('a name past CHART_MAX is still inert if clicked directly (defence in depth)', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  t.click('ai-ml');                                 // 9th: real name, but past CHART_MAX
  assert.deepEqual(fetches, []);
});

test('a charted row does issue a drill request', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  t.click('software-engineering');
  assert.equal(fetches.length, 1);
  assert.match(fetches[0], /family=software-engineering/);
  assert.match(fetches[0], /split=roles/);
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
  // Reach the roles split the way a user does — by clicking the marked row — then land on the
  // empty series the first post-deploy run produces, before `role_trends` has written any
  // `watch:` rows.
  t.set(fixture(), null);
  t.click('software-engineering');
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
  assert.deepEqual(fetches, []);
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
  assert.deepEqual(t.atsSelected(), ['greenhouse', 'lever']);
});

test('all-checked sends no ats param on the wire', () => {
  const { t, nodes, fetches } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true]]);
  t.set(fixture(), null);
  t.click('software-engineering');   // drills, which calls loadTrends and issues a fetch
  assert.doesNotMatch(fetches[0], /ats=/);
});

test('a narrowed selection is sent as repeated ats params', () => {
  const { t, nodes, fetches } = loadApp();
  fakeAtsMenu(nodes, [['greenhouse', true], ['lever', true], ['workday', false]]);
  t.set(fixture(), null);
  t.click('software-engineering');
  const params = new URLSearchParams(fetches[0].split('?')[1]);
  assert.deepEqual(params.getAll('ats'), ['greenhouse', 'lever']);
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
