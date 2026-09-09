/* Layer 2 of the résumé builder — the layout contract, the Headless Headhunter template's own
 * rules, and the exports. Run against the real browser scripts through the shared vm harness.
 *
 * Two of these are the load-bearing ones. "Every layout renders every shape" and "a component
 * registered after a layout still renders in it" are the two claims the extensibility story
 * rests on; without them "add a component, add a layout" is an aspiration rather than a fact.
 */

const test = require('node:test');
const assert = require('node:assert');
const { load, ALL } = require('./resume_harness.js');

const HH = 'headless-headhunter';

function example(ctx, layoutId) {
  const lay = ctx.ResumeLayouts.get(layoutId || HH);
  const b = ctx.ResumeDocument.builder().named('Example').usingLayout(lay.id);
  (lay.example || lay.starter)(b);
  return b.build();
}
const hits = html => (html.match(/data-node="/g) || []).length;

/* ---- the layout contract ---- */

test('every registered layout has a strategy for every component shape', () => {
  const ctx = load(ALL);
  for (const lay of ctx.ResumeLayouts.all()) {
    for (const shape of ctx.ResumeComponents.SHAPES) {
      assert.equal(typeof lay._render.byShape[shape], 'function',
        `${lay.id} has no renderer for shape ${shape}`);
    }
  }
});

test('a layout missing a shape is refused at registration, not at render time', () => {
  const ctx = load(ALL);
  assert.throws(() => ctx.ResumeLayouts.define({
    id: 'broken', css: () => '', starter: () => {},
    render: { byShape: { header: () => '', text: () => '' } },
  }), /no renderer for shape/);
});

test('every node in a document renders exactly one selectable hook, in every layout', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const expected = ctx.ResumeDocument.flatten(doc).length;
  assert.ok(expected >= 10, 'the example should be a real document');
  for (const lay of ctx.ResumeLayouts.all()) {
    const html = ctx.ResumeLayouts.renderDocument(lay, Object.assign({}, doc, { layoutId: lay.id }));
    assert.equal(hits(html), expected, `${lay.id} dropped or doubled a node`);
    for (const node of ctx.ResumeDocument.flatten(doc)) {
      assert.ok(html.includes('data-node="' + node.id + '"'), `${lay.id} lost ${node.type}`);
    }
  }
});

test('the same words survive every layout, unchanged', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const words = ctx.ResumeExport.plainText(doc);
  for (const lay of ctx.ResumeLayouts.all()) {
    const moved = Object.assign({}, doc, { layoutId: lay.id });
    assert.equal(ctx.ResumeExport.plainText(moved), words, `${lay.id} changed the text`);
  }
});

test('a component type registered after a layout still renders in it', () => {
  const ctx = load(ALL);
  // Nothing here knows this type exists; it arrives wearing a shape, which is the whole point.
  ctx.ResumeComponents.define({
    type: 'award', shape: 'line', label: 'Award',
    fields: [{ key: 'label', label: 'Award' }, { key: 'value', label: 'Detail' }],
  });
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .add('award', { label: 'Best Barista', value: '2025' }).build();
  for (const lay of ctx.ResumeLayouts.all()) {
    const html = ctx.ResumeLayouts.renderDocument(lay, Object.assign({}, doc, { layoutId: lay.id }));
    assert.equal(hits(html), 1, `${lay.id} did not render the new type`);
    assert.ok(html.includes('Best Barista'), `${lay.id} rendered it empty`);
  }
  assert.ok(ctx.ResumeExport.plainText(doc).includes('Best Barista'), 'and it exports');
});

test('a layout registered at runtime works end to end', () => {
  const ctx = load(ALL);
  const plain = ctx.ResumeLayouts.plainStrategies();
  ctx.ResumeLayouts.define({
    id: 'newcomer', label: 'Newcomer', css: (t, s) => s + ' { font-family: serif; }',
    starter: b => b.add('header', {}), render: { byShape: plain },
  });
  const doc = example(ctx);
  const moved = Object.assign({}, doc, { layoutId: 'newcomer' });
  const html = ctx.ResumeLayouts.renderDocument(ctx.ResumeLayouts.get('newcomer'), moved);
  assert.equal(hits(html), ctx.ResumeDocument.flatten(doc).length);
  assert.ok(ctx.ResumeExport.standaloneHtml(moved).includes('font-family: serif'));
});

test('geometry is clamped to the layout that has to draw it, and keys it ignores are dropped', () => {
  const ctx = load(ALL);
  const hh = ctx.ResumeLayouts.get(HH);
  const canvas = ctx.ResumeLayouts.get('free-canvas');
  const node = { geometry: { spaceAfter: 9999, gutter: 40, x: 3, y: 2, w: 4, h: 1 } };
  const flow = ctx.ResumeLayouts.geometryFor(hh, node);
  assert.equal(flow.spaceAfter, 40, 'clamped to the layout bound');
  assert.equal(flow.gutter, 2.6, 'clamped to the layout bound');
  assert.equal(flow.x, undefined, 'a single-column layout has no free positioning to honour');
  const free = ctx.ResumeLayouts.geometryFor(canvas, node);
  assert.equal(free.x, 3);
  assert.equal(free.spaceAfter, undefined, 'the canvas does not honour flow spacing');
});

test('a free-positioning layout gives arriving blocks coordinates rather than a pile', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const canvas = ctx.ResumeLayouts.get('free-canvas');
  assert.equal(typeof canvas.adopt, 'function');
  const moved = ctx.ResumeDocument.clone(doc);
  moved.layoutId = 'free-canvas';
  canvas.adopt(moved);
  const ys = moved.root.children.map(n => n.geometry.y);
  assert.ok(ys.every(y => typeof y === 'number'), 'every block was placed');
  assert.deepEqual(ys, ys.slice().sort((a, b) => a - b), 'and stacked in reading order');
  assert.equal(JSON.stringify(moved.content), JSON.stringify(doc.content), 'adopt touched no words');
});

/* ---- content is escaped ---- */

test('résumé text is escaped everywhere it reaches HTML', () => {
  const ctx = load(ALL);
  const nasty = '<img src=x onerror="alert(1)">';
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .add('header', { fullName: nasty })
    .section(nasty, s => s.add('work_entry', { role: nasty }, e => e.bullet(nasty)))
    .build();
  for (const lay of ctx.ResumeLayouts.all()) {
    const html = ctx.ResumeLayouts.renderDocument(lay, Object.assign({}, doc, { layoutId: lay.id }));
    assert.ok(!html.includes('<img'), `${lay.id} let a tag through`);
    assert.ok(html.includes('&lt;img'), `${lay.id} did not escape it`);
  }
  assert.ok(!ctx.ResumeExport.standaloneHtml(doc).includes('onerror="alert'));
});

/* ---- the Headless Headhunter template's own rules ---- */

test('the guide’s own worked example passes every rule the template states', () => {
  const ctx = load(ALL);
  const found = ctx.ResumeLayouts.runRules(ctx.ResumeLayouts.get(HH), example(ctx));
  assert.deepEqual(found, [], 'the example the guide ships must not trip its own checks');
});

test('each rule actually fires — a check that cannot fail is not a check', () => {
  const ctx = load(ALL);
  const { ResumeDocument: D, ResumeLayouts: L } = ctx;
  const hh = L.get(HH);
  const ids = doc => L.runRules(hh, doc).map(f => f.ruleId);

  // too few bullets, no opening summary, no dates
  const thin = D.builder().usingLayout(HH)
    .add('header', {})
    .section('Work History', s => s.add('work_entry', {}, e => e.bullet('One thing')))
    .build();
  const thinIds = ids(thin);
  for (const rule of ['contact', 'bullet-count', 'opening-summary', 'dates']) {
    assert.ok(thinIds.includes(rule), `${rule} did not fire on a document that breaks it`);
  }

  // Two sentences in one bullet. The guide's limit is "no more than one period", and its own
  // example bullets carry none at all — so two periods is what breaks it, not two clauses.
  const chatty = D.builder().usingLayout(HH)
    .add('header', { fullName: 'A', phone: '1', email: 'e', locationLine: 'x' })
    .section('Work History', s => s.add('work_entry',
      { role: 'R', start: 'June 2023', current: true },
      e => { e.bullet('Did the first thing. Then did a second thing.', true);
        e.bullet('Two'); e.bullet('Three'); }))
    .build();
  assert.ok(ids(chatty).includes('one-sentence'));

  // jobs out of order
  const jumbled = D.builder().usingLayout(HH)
    .add('header', { fullName: 'A', phone: '1', email: 'e', locationLine: 'x' })
    .section('Work History', s => {
      s.add('work_entry', { role: 'Old', start: 'January 2019', end: 'January 2020' },
        e => { e.bullet('a', true); e.bullet('b'); e.bullet('c'); });
      s.add('work_entry', { role: 'New', start: 'January 2024', end: 'January 2025' },
        e => { e.bullet('a', true); e.bullet('b'); e.bullet('c'); });
    }).build();
  assert.ok(ids(jumbled).includes('reverse-chronological'));

  // typography moved off the template
  const restyled = example(ctx);
  restyled.theme = { bodySize: 12 };
  const off = L.runRules(hh, restyled).find(f => f.ruleId === 'on-template');
  assert.ok(off && /10\.5/.test(off.message), 'the finding should name what the template asks for');

  // a bullet longer than three lines
  const wordy = D.builder().usingLayout(HH)
    .add('header', { fullName: 'A', phone: '1', email: 'e', locationLine: 'x' })
    .section('Work History', s => s.add('work_entry',
      { role: 'R', start: 'June 2023', current: true },
      e => { e.bullet('x'.repeat(1200), true); e.bullet('b'); e.bullet('c'); }))
    .build();
  assert.ok(ids(wordy).includes('three-lines'));

  // more than three lines of education
  const schooled = D.builder().usingLayout(HH)
    .add('header', { fullName: 'A', phone: '1', email: 'e', locationLine: 'x' })
    .section('Education & Certificates', s => {
      for (let i = 0; i < 4; i++) s.add('education_entry', { credential: 'Degree ' + i });
    }).build();
  assert.ok(ids(schooled).includes('education-length'));
});

test('a rule that throws is contained rather than taking the panel with it', () => {
  const ctx = load(ALL);
  ctx.ResumeLayouts.define({
    id: 'explosive', css: () => '', starter: () => {},
    render: { byShape: ctx.ResumeLayouts.plainStrategies() },
    rules: [
      { id: 'boom', label: 'Boom', check() { throw new Error('nope'); } },
      { id: 'fine', label: 'Fine', check() { return [{ level: 'warn', nodeId: null, message: 'still here' }]; } },
    ],
  });
  const found = ctx.ResumeLayouts.runRules(ctx.ResumeLayouts.get('explosive'), example(ctx));
  assert.equal(found.length, 2);
  assert.ok(found.some(f => f.message === 'still here'), 'the other rules still ran');
});

test('the sentence count ignores abbreviations and decimals', () => {
  const { ResumeHeadhunter: H } = load(ALL);
  assert.equal(H.periods('Ran the till and gave correct change'), 0);
  assert.equal(H.periods('Ran the till. Gave change'), 1);
  assert.equal(H.periods('Earned a B.A. in Economics'), 0, 'B.A. is not a sentence');
  assert.equal(H.periods('Raised the score to 4.5 out of 5'), 0, 'a decimal is not a sentence');
  assert.equal(H.periods('Worked in the U.S. and in the E.U. for years'), 0);
});

test('dates are read in the formats people type, and a bare year is refused', () => {
  const { ResumeHeadhunter: H } = load(ALL);
  assert.deepEqual(H.parseMonth('June 2023'), { y: 2023, m: 6 });
  assert.deepEqual(H.parseMonth('Jun 2023'), { y: 2023, m: 6 });
  assert.deepEqual(H.parseMonth('06/2023'), { y: 2023, m: 6 });
  assert.deepEqual(H.parseMonth('2023-06'), { y: 2023, m: 6 });
  assert.equal(H.parseMonth('2023'), null, 'the guide asks for a month as well');
  assert.equal(H.parseMonth('sometime'), null);
  assert.equal(H.parseMonth(''), null);
});

/* ---- exports ---- */

test('the plain-text export carries every node’s words, in reading order', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const text = ctx.ResumeExport.plainText(doc);
  assert.ok(text.startsWith('LEE KORELITZ'));
  assert.ok(text.includes('WORK HISTORY'));
  assert.ok(text.includes('Cashier at Large Ducks Coffee, TX'));
  assert.ok(text.includes('June 2023 to Current'));
  assert.ok(text.indexOf('EDUCATION') < text.indexOf('WORK HISTORY'), 'reading order');
  assert.ok(!/<[a-z]/i.test(text), 'no markup leaked into the text export');
});

test('the standalone file carries the layout’s own stylesheet and page size', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const html = ctx.ResumeExport.standaloneHtml(doc);
  assert.ok(html.startsWith('<!doctype html>'));
  assert.ok(html.includes('@page'));
  assert.ok(html.includes('8.5in 11in'), 'the sheet size the layout declares');
  assert.ok(html.includes('Arial'), 'the template’s typeface reached the file');
  assert.ok(!ctx.ResumeExport.standaloneHtml(doc, {}).includes('Word.Document'));
  assert.ok(ctx.ResumeExport.standaloneHtml(doc, { wordMeta: true }).includes('Word.Document'));
});

test('a JSON export re-imports, under a new id, and survives an unknown layout', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const back = ctx.ResumeExport.importJson(JSON.stringify(doc));
  assert.notEqual(back.id, doc.id, 'importing a backup must not overwrite what is open');
  assert.equal(JSON.stringify(back.content), JSON.stringify(doc.content));

  const alien = JSON.parse(JSON.stringify(doc));
  alien.layoutId = 'a-layout-this-build-never-had';
  assert.equal(ctx.ResumeExport.importJson(JSON.stringify(alien)).layoutId, HH,
    'falls back rather than refusing a readable document');

  assert.throws(() => ctx.ResumeExport.importJson('not json at all'), /not JSON/);
  assert.throws(() => ctx.ResumeExport.importJson('{"hello":1}'), /not a HeadStart/);
});

test('the download filename comes from the résumé’s own name, safely', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  doc.name = 'Lee Korelitz — Barista/Cashier';
  assert.equal(ctx.ResumeExport.filename(doc, 'pdf'), 'Lee_Korelitz_BaristaCashier.pdf');
  doc.name = '';
  assert.equal(ctx.ResumeExport.filename(doc, 'txt'), 'resume.txt');
});
