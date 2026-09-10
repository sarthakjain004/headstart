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
    window: { addEventListener() {}, location: { hash: '' } },
    location: { hash: '' },
    console, CFG: {}, URLSearchParams, Date, Math, isNaN,
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
    + '\n;globalThis.__t = { draw: drawTrends, click: trendClick, split: () => trendSplit,'
    + ' chartMax: CHART_MAX,'
    + ' niceAxis: niceAxis, fmtAxis: fmtAxis, deltaText: deltaText, seriesValues: seriesValues,'
    + ' hasIndexBase: hasIndexBase,'
    + ' atsSelected: trendAtsSelected, atsLabel: trendAtsLabel, atsToggle: toggleAtsPopover,'
    + ' colorSlot: name => seriesColorAssignment.get(name), setUnit: setUnit,'
    + ' load: loadTrends,'
    + ' data: () => trendData,'
    + ' set: (d, drill) => { trendData = d; trendDrill = drill || null; } };'
    // Repaints are counted at the global binding, which is what loadTrends' own `drawTrends()`
    // call resolves — so this counts the real paints, not a copy of them.
    + '\n;(() => { let n = 0; const real = drawTrends;'
    + ' globalThis.drawTrends = (...a) => { n++; return real(...a); };'
    + ' globalThis.__t.draws = () => n; })();';
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

// ---- one request owns the panel --------------------------------------------------------
//
// The ATS picker fires one loadTrends per checkbox, so narrowing 21 ATSes to 1 starts 20
// round trips. Measured in Chromium before the fix: 20 requests, 20 repaints, 6-11 answers
// arriving out of the order they were asked in, and 3 runs in 5 settling on an answer that
// was not the last request's — the numbers churning, then coming to rest on the wrong scope.

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
  const { t } = loadApp(impl);
  calls.length = 0;                 // drop anything the page asked for as it loaded
  for (let i = 0; i < 5; i++) t.load(null);   // five boxes ticked before the first answer lands
  assert.equal(calls.length, 5, 'each selection still asks the server for its own answer');
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
  assert.deepEqual(t.seriesValues(high), [null, null, null, null],
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
  assert.deepEqual(t.seriesValues(ok).map(Math.round), [100, 113, 150, 200],
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
