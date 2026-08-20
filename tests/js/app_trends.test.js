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
    querySelectorAll: () => [],
    setAttribute() {}, getAttribute: () => null, addEventListener() {},
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
    },
    window: { addEventListener() {}, location: { hash: '' } },
    location: { hash: '' },
    console, CFG: {}, URLSearchParams, Date, Math, isNaN,
    fetch: url => { fetches.push(String(url)); return Promise.resolve({ ok: false }); },
  };
  ctx.globalThis = ctx;
  const src = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { draw: drawTrends, click: trendClick, split: () => trendSplit,'
    + ' chartMax: CHART_MAX,'
    + ' set: (d, drill) => { trendData = d; trendDrill = drill || null; } };';
  vm.runInNewContext(src, ctx);
  // The page fetches on load (the feed, the trends chart). Those are not what any test here is
  // asserting about, so the log starts empty from the caller's point of view.
  fetches.length = 0;
  return { t: ctx.__t, nodes, fetches };
}

const mk = (name, label, latest) => ({ name, label, points: [latest, latest], latest });

/** Ten series, so four sit past CHART_MAX (8) and are listed-but-not-charted. */
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

test('a row past CHART_MAX is not marked — clicking it would not drill', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);   // ai-ml is 9th here
  t.draw();
  assert.doesNotMatch(row(nodes['trends-legend'].innerHTML, 'ai-ml'), /class="drill"/);
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

test('charted rows are real buttons; listed-but-uncharted rows are not interactive', () => {
  const { t, nodes } = loadApp();
  t.set(fixture(), null);
  t.draw();
  const html = nodes['trends-legend'].innerHTML;
  const charted = row(html, 'software-engineering');
  const uncharted = row(html, 'ai-ml');            // 9th, past CHART_MAX
  assert.match(charted, /role="button"/);
  assert.match(charted, /tabindex="0"/);
  assert.doesNotMatch(uncharted, /role="button"/);
  assert.doesNotMatch(uncharted, /tabindex/);
  // Scoped to these rows, not the whole legend: a future real toggle must not fail this.
  assert.doesNotMatch(charted, /aria-pressed/);
  assert.doesNotMatch(uncharted, /aria-pressed/);
  // The role belongs on the inner .row so the <li> keeps its implicit `listitem`.
  assert.match(charted, /<span class="row"[^>]*role="button"/);
});

test('an uncharted row is inert — clicking it issues no request', () => {
  const { t, fetches } = loadApp();
  t.set(fixture(), null);
  t.click('ai-ml');                                 // 9th: listed, not charted
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
