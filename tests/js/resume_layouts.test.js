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

test('a component registered after a layout renders in it — whatever its fields are called', () => {
  const ctx = load(ALL);
  /* This test used to define its new component with fields named `label` and `value` — the exact
     two keys every `line` fallback happened to hardcode — so it passed while the guarantee it
     names was false. Measured at the time: a Language line with fields `name`/`level` rendered as
     literally nothing in all three layouts, and a childless Certification was an empty box on the
     canvas. A user's languages would have vanished off the page in silence.
     So every field name below is deliberately one no renderer has ever seen, and there is one
     component per SHAPE, because the fallback is chosen by shape. If a future field-name
     coincidence creeps back in, this fails. */
  const NEW = [
    ['id_block', 'header', [['who', 'Ada Lovelace'], ['reach', 'ada@example.test']]],
    ['motto', 'text', [['saying', 'Ship it on Friday']]],
    ['panel', 'section', [['heading', 'Further Reading']]],
    ['certification', 'entry', [['awarded', 'AWS Solutions Architect'], ['by', 'Amazon'], ['when', '2024']]],
    /* Not `language_line`: the catalogue has a real one now, and `define` refuses a duplicate.
       A throwaway type in a test is a name in the same namespace as the shipped components. */
    ['tongue_line', 'line', [['tongue', 'French'], ['fluency', 'Full professional']]],
    ['note_item', 'bullet', [['body', 'A thing worth noting']]],
  ];
  for (const [type, shape, fields] of NEW) {
    ctx.ResumeComponents.define({
      type, shape, label: type,
      accepts: shape === 'section' ? ['*'] : [],
      inList: shape === 'bullet',
      fields: fields.map(([key]) => ({ key, label: key })),
    });
  }

  const b = ctx.ResumeDocument.builder().usingLayout(HH);
  for (const [type, shape, fields] of NEW) {
    const content = Object.fromEntries(fields);
    if (shape === 'section') b.add(type, content, s2 => s2.add('note_item', { body: 'A thing worth noting' }));
    else if (shape !== 'bullet') b.add(type, content);
  }
  const doc = b.build();
  const words = NEW.flatMap(([, , fields]) => fields.map(([, value]) => value));

  for (const lay of ctx.ResumeLayouts.all()) {
    const html = ctx.ResumeLayouts.renderDocument(lay, Object.assign({}, doc, { layoutId: lay.id }));
    const text = html.replace(/<[^>]+>/g, ' ').replace(/&middot;/g, '.').replace(/\s+/g, ' ');
    for (const word of words) {
      assert.ok(text.includes(word),
        `${lay.id} rendered nothing for "${word}" — a shape fallback is naming a field`);
    }
    assert.equal(hits(html), ctx.ResumeDocument.flatten(doc).length,
      `${lay.id} dropped or doubled a node`);
  }
  /* And the plain-text export, which had the same defect in its own shape visitors — the words of
     an unknown component were dropped from the copy people paste into an application form. The
     name is upper-cased by the header visitor, as it always has been for the known header. */
  /* Case-insensitive: the export upper-cases the name and the section headings on purpose, as it
     always has for the known header and "WORK HISTORY". What matters here is that no word is
     dropped. */
  const text = ctx.ResumeExport.plainText(doc).toLowerCase();
  for (const word of words) {
    assert.ok(text.includes(word.toLowerCase()), `the export dropped "${word}"`);
  }
});

test('every component in the catalogue reaches the page and the export, in every layout', () => {
  const ctx = load(ALL);
  /* The companion to the test above, and the one that covers the components actually shipped
     rather than six throwaways. One node of every registered type, every string field filled
     with a token only that field has, rendered through every layout: anything a renderer forgets
     shows up as a token that never made it to the page. It caught three real losses the first
     time it ran — the two-column header's `languages`, and the plain-text export truncating a
     project with a stack to its name alone. */
  const b = ctx.ResumeDocument.builder().usingLayout(HH);
  const want = [];
  for (const spec of ctx.ResumeComponents.all()) {
    const content = {};
    for (const field of spec.fields) {
      if (field.kind === 'flag') continue;
      content[field.key] = 'Zx' + spec.type + field.key;
      want.push(content[field.key]);
    }
    b.add(spec.type, content);
  }
  const doc = b.build();

  for (const lay of ctx.ResumeLayouts.all()) {
    const html = ctx.ResumeLayouts.renderDocument(lay, Object.assign({}, doc, { layoutId: lay.id }));
    const text = html.replace(/<[^>]+>/g, ' ');
    for (const token of want) {
      assert.ok(text.includes(token), `${lay.id} rendered nothing for ${token}`);
    }
  }
  /* And the same words reach the plain-text export. Case-insensitive: the export upper-cases the
     name and the section headings, deliberately and always has. */
  const exported = ctx.ResumeExport.plainText(doc).toLowerCase();
  for (const token of want) {
    assert.ok(exported.includes(token.toLowerCase()), `the export dropped ${token}`);
  }
});

/* ---- the layouts added in 2026-09 ---- */

const ADDED = ['jakes-resume', 'harvard-classic', 'modern-sidebar', 'europass'];

test('every added layout ships a worked example that passes its own rules', () => {
  const ctx = load(ALL);
  /* The calibration that keeps a rule set honest: a layout whose own example trips its own
     checks is stating a rule its author did not believe. The Headless Headhunter layout is held
     to this against the guide's worked example; these four are held to it against theirs. */
  for (const id of ADDED) {
    const lay = ctx.ResumeLayouts.get(id);
    assert.ok(lay.example, `${id} ships no example`);
    const doc = ctx.ResumeDocument.builder().named('E').usingLayout(id);
    lay.example(doc);
    assert.deepEqual(ctx.ResumeLayouts.runRules(lay, doc.build()), [], `${id}'s example trips its own rules`);
  }
});

test('every rule an added layout states can actually fire', () => {
  const ctx = load(ALL);
  const { ResumeDocument: D, ResumeLayouts: L } = ctx;
  const idsFor = (layoutId, doc) => L.runRules(L.get(layoutId), doc).map(f => f.ruleId);

  // Jake's: a project with no stack, an uncategorised skills line, and a summary block the
  // template has no place for.
  const jakes = D.builder().usingLayout('jakes-resume')
    .add('header', { fullName: 'A' })
    .add('professional_summary', { text: 'Backend engineer' })
    .section('Projects', s => s.add('tech_project', { name: 'Gitlytics', tech: '' }))
    .section('Technical Skills', s => s.add('skills_line', { label: '', value: 'Go, Python' }))
    .build();
  const jakesIds = idsFor('jakes-resume', jakes);
  for (const rule of ['stack', 'skills-grouped', 'off-template']) {
    assert.ok(jakesIds.includes(rule), `jakes-resume: ${rule} did not fire`);
  }

  // Harvard: a pronoun, a full stop, "Current" typed into a date, a heading nothing parses.
  const harvard = D.builder().usingLayout('harvard-classic')
    .add('header', { fullName: 'A' })
    .section('My Journey So Far', s => s.add('work_entry',
      { company: 'C', role: 'R', start: 'June 2023', end: 'Current' },
      e => e.bullet('I ran the counter and closed the till.')))
    .build();
  const harvardIds = idsFor('harvard-classic', harvard);
  for (const rule of ['pronouns', 'no-terminal-period', 'present-not-current', 'standard-headings']) {
    assert.ok(harvardIds.includes(rule), `harvard-classic: ${rule} did not fire`);
  }
  /* And the same document under the Headless Headhunter layout does NOT report a terminal
     period, because that template asks for one. Two layouts, opposite advice, same words —
     which is the whole reason a rule belongs to a Layout. */
  assert.ok(!idsFor(HH, harvard).includes('no-terminal-period'));

  // Sidebar: a job history dragged into the band, and a band left empty.
  const sidebar = D.builder().usingLayout('modern-sidebar')
    .add('header', { fullName: 'A' }).into('side')
    .section('Experience', s => s.add('work_entry', { role: 'R', start: 'June 2023' })).into('side')
    .add('professional_summary', { text: 'x' })
    .build();
  assert.ok(idsFor('modern-sidebar', sidebar).includes('column-order'));

  const flat = D.builder().usingLayout('modern-sidebar')
    .add('header', { fullName: 'A' })
    .add('professional_summary', { text: 'x' })
    .section('Experience', s => s.add('work_entry', { role: 'R' }))
    .build();
  assert.ok(idsFor('modern-sidebar', flat).includes('band-empty'));

  // Europass: a level that is not CEFR, no mother tongue, and the wrong sheet.
  const europass = D.builder().usingLayout('europass')
    .add('header', { fullName: 'A' })
    .section('Language skills', s => s.add('language_line', { language: 'German', level: 'Fluent' }))
    .build();
  europass.paper = 'letter';
  const europassIds = idsFor('europass', europass);
  for (const rule of ['cefr', 'mother-tongue', 'a4']) {
    assert.ok(europassIds.includes(rule), `europass: ${rule} did not fire`);
  }
  /* A mother tongue is exempt from the CEFR check: nobody grades their own first language. */
  const native = D.builder().usingLayout('europass')
    /* Reachable, because the baseline (ADR-0127) asks every layout for a name and one way to
       answer — and this assertion is that europass's OWN rules have nothing left to say. */
    .add('header', { fullName: 'A', email: 'a@example.com' })
    .section('Language skills', s => {
      s.add('language_line', { language: 'Mother tongue', level: 'Slovenian' });
      s.add('language_line', { language: 'English', level: 'C1' });
    })
    .build();
  native.paper = 'a4';
  assert.deepEqual(idsFor('europass', native), []);
});

test('the sidebar puts an arriving document in the wide column, never inside the band', () => {
  const ctx = load(ALL);
  /* `renderDocument` sends a node whose slot this layout does not know to `slots[0]`, and
     `resume_document.js` writes the literal slot id 'main' when a block is added. A layout that
     declared its band first would therefore swallow every migrated résumé whole. This asserts
     the split that keeps that from happening, and that the trip costs no block. */
  const sidebar = ctx.ResumeLayouts.get('modern-sidebar');
  assert.equal(sidebar.slots[0].id, 'main', 'the wide column must be the first slot');

  const source = ctx.ResumeLayouts.get(HH);
  const b = ctx.ResumeDocument.builder().named('E').usingLayout(HH);
  source.example(b);
  const doc = b.build();
  const before = ctx.ResumeDocument.flatten(doc).length;

  const moved = ctx.ResumeDocument.clone(doc);
  moved.layoutId = 'modern-sidebar';
  sidebar.adopt(moved);

  assert.equal(ctx.ResumeDocument.flatten(moved).length, before, 'adopt dropped a block');
  assert.equal(JSON.stringify(moved.content), JSON.stringify(doc.content), 'adopt touched a word');
  const banded = moved.root.children.filter(n => n.slot === 'side').map(n => n.type);
  assert.deepEqual(banded, ['header'], 'only the contact block belongs in the band by default');
  /* And the layout's own rule agrees with its own adopt — no dated block ended up in the band. */
  assert.ok(!ctx.ResumeLayouts.runRules(sidebar, moved).some(f => f.ruleId === 'column-order'));

  const html = ctx.ResumeLayouts.renderDocument(sidebar, moved);
  const bandInner = html.split('data-slot="side"')[1] || '';
  assert.ok(!bandInner.includes('data-type="work_entry"'), 'a job rendered inside the band');
});

test('an added layout starts you on section headings a parser knows', () => {
  const ctx = load(ALL);
  /* The starter document is the only résumé most people will ever see from this layout, and a
     heading a parser cannot file is the cheapest possible own goal. Harvard's own rule is
     borrowed to check the other three, which is exactly the sort of thing a rule being data
     rather than code makes free. */
  const harvard = ctx.ResumeLayouts.get('harvard-classic');
  const headings = harvard.rules.find(r => r.id === 'standard-headings');
  for (const id of ADDED) {
    const lay = ctx.ResumeLayouts.get(id);
    const b = ctx.ResumeDocument.builder().named('S').usingLayout(id);
    lay.starter(b);
    const doc = b.build();
    const api = { nodesOfType: type => ctx.ResumeDocument.flatten(doc).filter(n => n.type === type),
      content: nodeId => doc.content[nodeId] || {} };
    assert.deepEqual(headings.check(doc, api), [], `${id}'s starter uses a non-standard heading`);
  }
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

test('the paper control names the sheet actually in force, including a layout that is A4', () => {
  const ctx = load(ALL);
  const { ResumeLayouts: L, ResumeDocument: D } = ctx;
  /* The Design pane read `doc.paper || 'letter'`, so a fresh Europass document rendered and
     printed A4 while the control beside it said "US Letter" — a control disagreeing with the page
     it governs. */
  for (const id of L.all().map(l => l.id)) {
    const lay = L.get(id);
    const b = D.builder().usingLayout(id);
    lay.starter(b);
    const doc = b.build();
    const named = L.PAPERS.find(p => p.id === L.paperIdFor(lay, doc));
    assert.ok(named, `${id}: no paper named`);
    assert.ok(Math.abs(named.width - L.pageFor(lay, doc).width) < 0.05,
      `${id}: control says ${named.id} but the sheet is ${L.pageFor(lay, doc).width}in`);
  }
  /* And a document that states its own sheet still wins over the layout's. */
  const eu = L.get('europass');
  assert.equal(L.paperIdFor(eu, { paper: 'letter' }), 'letter');
  assert.equal(L.paperIdFor(eu, {}), 'a4', 'the layout\'s own sheet is the fallback');
});

/* ---- a theme override is untrusted input ---- */

test('an imported document cannot choose its own identifiers', () => {
  const ctx = load(ALL);
  /* Node ids are woven into HTML attributes across the editor's rail and into
     querySelector('[data-node="…"]'). In an imported file every one of them is attacker-chosen,
     and the rail wrote them unescaped — so a résumé "backup" someone sent you ran script in
     HeadStart's own origin the moment you picked the file, and again on every later visit,
     because the import is saved. Identifiers are rewritten at the boundary now. */
  const hostile = 'a">&lt;img src=x onerror=alert(1)&gt;'.replace('&lt;', '<').replace('&gt;', '>');
  const doc = {
    schema: 1, id: 'r1', name: 'Backup', layoutId: HH,
    root: { id: '__root__', type: '__root__', slot: null, geometry: {}, children: [
      { id: hostile, type: 'section', slot: 'main', geometry: {}, children: [] }] },
    content: { [hostile]: { title: 'Work History' } },
    variants: { [hostile]: { 'v">x': { title: 'Other' } } },
    tailorings: [{ id: 'bad"id', name: 'V', picks: { [hostile]: 'v">x' }, hidden: [hostile] }],
    hidden: [hostile],
    activeTailoring: 'bad"id', theme: {},
  };
  const back = ctx.ResumeExport.importJson(JSON.stringify(doc));
  const id = back.root.children[0].id;

  assert.ok(ctx.ResumeExport.SAFE_ID.test(id), 'the hostile id survived');
  assert.equal(back.content[id].title, 'Work History', 'the words came through the rewrite');
  assert.deepEqual(Object.keys(back.variants), [id], 'variants follow the node');
  assert.ok(ctx.ResumeExport.SAFE_ID.test(Object.keys(back.variants[id])[0]));
  const tailoring = back.tailorings[0];
  assert.ok(ctx.ResumeExport.SAFE_ID.test(tailoring.id));
  assert.equal(back.activeTailoring, tailoring.id, 'the active version still points at it');
  assert.deepEqual(Object.keys(tailoring.picks), [id], 'picks follow the node');
  assert.deepEqual(tailoring.hidden, [id]);
  /* And the DOCUMENT's own left-out list, which is the other half of ADR-0128's two layers. A
     list left holding ids the tree no longer answers to does not fail loudly — every block the
     résumé had switched off simply comes back on, in an imported backup, silently. */
  assert.deepEqual(back.hidden, [id], 'the document\u2019s own left-out list did not follow the node');
  /* And the whole document renders without a tag escaping an attribute. */
  const html = ctx.ResumeLayouts.renderDocument(ctx.ResumeLayouts.get(HH), back);
  assert.ok(!html.includes('<img'), 'a tag reached the page');
});

test('an identifier that is already the right shape is left alone', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const before = ctx.ResumeDocument.flatten(doc).map(n => n.id);
  const back = ctx.ResumeExport.importJson(JSON.stringify(doc));
  assert.deepEqual(ctx.ResumeDocument.flatten(back).map(n => n.id), before,
    'a normal backup must round-trip unchanged');
});

test('a theme cannot escape the stylesheet it is interpolated into', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  /* A résumé document is a file the product invites people to exchange — the JSON backup is the
     only backup it offers — so its theme is untrusted input, and every token value is
     interpolated straight into a <style> element by the print and download paths. */
  doc.theme = {
    fontFamily: 'Arial</style><script>alert(1)<\/script><style>',
    ink: 'red; background: url(https://evil.test/x)',
  };
  const html = ctx.ResumeExport.standaloneHtml(doc);
  assert.ok(!html.includes('</style><script'), 'the payload closed the style element');
  assert.ok(!html.includes('evil.test'), 'a url() reached the stylesheet');
  const theme = ctx.ResumeLayouts.themeFor(ctx.ResumeLayouts.get(HH), doc);
  assert.equal(theme.fontFamily, 'Arial, Helvetica, sans-serif', 'fell back to the layout default');
  assert.equal(theme.ink, '#000000');
});

test('a theme override is kept when the layout can vouch for it', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  doc.theme = { bodySize: 11, ink: '#222222', fontFamily: 'Georgia, serif' };
  const theme = ctx.ResumeLayouts.themeFor(ctx.ResumeLayouts.get(HH), doc);
  assert.equal(theme.bodySize, 11, 'a number inside the tunable range');
  assert.equal(theme.ink, '#222222', 'a hex colour');
  assert.equal(theme.fontFamily, 'Georgia, serif', 'one of the font options the layout offers');
});

test('a number outside its tunable range is clamped rather than trusted', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  doc.theme = { bodySize: 9999, bulletIndent: -50 };
  const theme = ctx.ResumeLayouts.themeFor(ctx.ResumeLayouts.get(HH), doc);
  assert.equal(theme.bodySize, 12, 'the tunable’s own maximum');
  assert.equal(theme.bulletIndent, 0.1, 'the tunable’s own minimum');
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

test('the twelve-year rule never tells you to delete the job you still have', () => {
  const ctx = load(ALL);
  const { ResumeDocument: D, ResumeLayouts: L } = ctx;
  const hh = L.get(HH);
  const job = (extra) => D.builder().usingLayout(HH)
    .add('header', { fullName: 'A', phone: '1', email: 'e', locationLine: 'x' })
    .section('Work History', s => s.add('work_entry', extra,
      e => { e.bullet('a', true); e.bullet('b'); e.bullet('c'); }))
    .build();
  const ids = doc => L.runRules(hh, doc).map(f => f.ruleId);

  // Fifteen years at the same employer, still there. The guide's rule is about how far BACK the
  // résumé reaches, not how long you have been somewhere.
  assert.ok(!ids(job({ role: 'R', start: 'January 2010', current: true })).includes('twelve-years'),
    'told the user to drop their current employer');
  // A job that ENDED fifteen years ago is genuinely out of range.
  assert.ok(ids(job({ role: 'R', start: 'January 2008', end: 'January 2010' })).includes('twelve-years'));
  // And a recent one is not.
  assert.ok(!ids(job({ role: 'R', start: 'January 2022', end: 'January 2024' })).includes('twelve-years'));
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
  /* Counted per rule, not in total: this layout states nothing, so it also inherits the shared
     baseline (ADR-0127), and a total would be a count of that rather than of containment. */
  assert.equal(found.filter(f => f.ruleId === 'boom').length, 1, 'the throwing rule reported once');
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

/* ---- how a bullet is written ---------------------------------------------------------------
   Three rules the guide states in words and the general résumé standard also asks for. Each test
   below pairs "it fires on what it is for" with the calibration that matters more: the guide's
   own worked example still produces nothing. A rule that flags the document this layout was
   copied from is a wrong rule, not a strict one. */

/** One work entry carrying `bullets`, with everything else the other rules want already right,
 *  so the ids that come back are only the ones under test. */
function oneJob(ctx, bullets) {
  const b = ctx.ResumeDocument.builder().usingLayout(HH)
    .add('header', { fullName: 'A', phone: '1', email: 'e', locationLine: 'x' })
    .section('Work History', s => s.add('work_entry',
      { role: 'R', company: 'C', start: 'June 2023', current: true },
      e => bullets.forEach((text, i) => e.bullet(text, i === 0))));
  return b.build();
}
const ruleIds = (ctx, doc) => ctx.ResumeLayouts.runRules(ctx.ResumeLayouts.get(HH), doc)
  .filter(f => f.ruleId).map(f => f.ruleId);

test('a bullet in the present tense is flagged, and an irregular past tense is not', () => {
  const ctx = load(ALL);
  const past = ['Operated the till by counting cash', 'Gave customers correct change by adding cash',
    'Spoke with customers to take their orders'];
  assert.ok(!ruleIds(ctx, oneJob(ctx, past)).includes('past-tense'),
    'Gave and Spoke are past tense; an "-ed or wrong" test would flag them');

  for (const bad of ['Manage a team of four people by running the rota',
                     'Managing a team of four people by running the rota',
                     'Run the till by counting cash',
                     'Using a desktop computer to read company emails']) {
    const ids = ruleIds(ctx, oneJob(ctx, [past[0], bad, past[1]]));
    assert.ok(ids.includes('past-tense'), `did not flag ${JSON.stringify(bad)}`);
  }

  /* A noun that ends in -ing is not a verb in the progressive, and treating it as one would
     flag a correct bullet. */
  assert.ok(!ruleIds(ctx, oneJob(ctx,
    [past[0], 'Marketing campaigns were rewritten by the team to reach more people', past[1]]))
    .includes('past-tense'), 'Marketing is a noun here');
});

test('a weak or dressed-up opening verb is named, and the guide’s own openers are not', () => {
  const ctx = load(ALL);
  const good = ['Operated the till by counting cash', 'Handled the lunch rush by multitasking',
    'Used a desktop computer to read company emails'];
  assert.ok(!ruleIds(ctx, oneJob(ctx, good)).includes('opening-verb'),
    'Handled opens a bullet in the guide’s own example; the general standard calls it weak and loses');

  for (const bad of ['Responsible for the till and the lunch rush by rota',
                     'Helped the team by covering the lunch rush',
                     'Spearheaded the rota by rewriting it every week',
                     'Leveraged the till software to reduce queue times',
                     /* The lists are written in one tense and people write in another. A browser
                        pass caught "Spearheading the front counter" producing no finding at all:
                        it is not the past-tense spelling on the list, and its stem is not a verb
                        the tense rule knows either, so it fell through both. */
                     'Spearheading the rota by rewriting it every week',
                     'Working the till by counting cash',
                     'Utilising the till software to reduce queue times']) {
    assert.ok(ruleIds(ctx, oneJob(ctx, [good[0], bad, good[1]])).includes('opening-verb'),
      `did not flag ${JSON.stringify(bad)}`);
  }
});

test('a bullet with no number and no outcome gets a note, not a warning', () => {
  const ctx = load(ALL);
  const bare = oneJob(ctx, ['Operated the till by counting cash', 'Gave customers correct change',
    'Ran the front counter']);
  const found = ctx.ResumeLayouts.runRules(ctx.ResumeLayouts.get(HH), bare)
    .filter(f => f.ruleId === 'result');
  assert.equal(found.length, 2, 'both bullets with no result and no reason');
  assert.ok(found.every(f => f.level === 'note'), 'plenty of good bullets carry no metric');

  /* The guide's own third bullet is "Gave customers correct change by adding and subtracting
     cash" — no number anywhere; the REASON is the main clause and the "by ..." is the how. */
  assert.equal(ruleIds(ctx, oneJob(ctx, ['Operated the till by counting cash',
    'Gave customers correct change by adding and subtracting cash',
    'Served multiple tables of customers'])).filter(id => id === 'result').length, 0);

  /* The opening summary is exempt: the guide asks it for what you did, and puts the
     What / How / Result shape on the bullets after it. */
  assert.equal(ruleIds(ctx, oneJob(ctx, ['Ran the front counter',
    'Gave customers correct change by adding cash', 'Handled the rush by multitasking']))
    .filter(id => id === 'result').length, 0);
});

test('dates are read in the formats people type, and a bare year is refused', () => {
  const { ResumeLayouts: H } = load(ALL);
  assert.deepEqual(H.parseMonth('June 2023'), { y: 2023, m: 6 });
  assert.deepEqual(H.parseMonth('Jun 2023'), { y: 2023, m: 6 });
  assert.deepEqual(H.parseMonth('06/2023'), { y: 2023, m: 6 });
  assert.deepEqual(H.parseMonth('2023-06'), { y: 2023, m: 6 });
  assert.equal(H.parseMonth('2023'), null, 'every standard here asks for a month as well');
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

/* ---- paper size ---------------------------------------------------------------------------
   Most layouts here are written for US Letter, which is the wrong sheet almost everywhere outside
   North America — and HeadStart is deliberately not a North American product. The sheet is the
   DOCUMENT's choice and overrides the layout's: the same template prints on both. A layout may
   still declare where a new document starts, and one does — Europass is an A4 form. */

test('a document chooses its paper, and every layout is laid out on the one it chose', () => {
  const ctx = load(ALL);
  const L = ctx.ResumeLayouts;
  for (const lay of L.all()) {
    /* A layout with no document has its own declared sheet. This used to assert US Letter for
       every layout, which was true while every layout was a US one — Europass is an A4 form and
       declares A4, and a form laid out on the wrong sheet re-wraps every line of it. */
    assert.deepEqual([L.pageFor(lay, {}).width, L.pageFor(lay, {}).height],
      [lay.page.width, lay.page.height], `${lay.id} default`);
    /* And the DOCUMENT still wins, in both directions — the Europass layout prints on Letter if
       that is what the résumé asks for, and the US layouts print on A4. */
    const a4 = L.pageFor(lay, { paper: 'a4' });
    assert.deepEqual([a4.width, a4.height], [8.27, 11.69], `${lay.id} on A4`);
    const letter = L.pageFor(lay, { paper: 'letter' });
    assert.deepEqual([letter.width, letter.height], [8.5, 11], `${lay.id} on US Letter`);
    assert.equal(a4.margin, lay.page.margin, 'the sheet changed, not the layout’s margin');
    assert.equal(a4.unit, lay.page.unit);
  }
  /* Which layouts default to which sheet is a decision, not an accident: exactly one of them is
     a European form. If a layout starts defaulting to A4 for taste, this says so. */
  assert.deepEqual(L.all().filter(lay => lay.page.width < 8.4).map(lay => lay.id), ['europass']);
});

test('an unreadable paper name falls back rather than laying out on nothing', () => {
  const ctx = load(ALL);
  const hh = ctx.ResumeLayouts.get(HH);
  for (const paper of ['', null, undefined, 'foolscap', '<script>', 0]) {
    assert.equal(ctx.ResumeLayouts.pageFor(hh, { paper }).width, 8.5, JSON.stringify(paper));
  }
});

test('the printed file carries the sheet the document chose, not the layout’s own', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  assert.ok(ctx.ResumeExport.standaloneHtml(doc).includes('8.5in 11in'));
  doc.paper = 'a4';
  const html = ctx.ResumeExport.standaloneHtml(doc);
  assert.ok(html.includes('8.27in 11.69in'), 'the @page rule still says US Letter');
  assert.ok(html.includes('width: 6.27in'), 'the text column was not re-measured for A4');
});

test('a rule that measures the page measures the sheet in use', () => {
  const { ResumeLayouts: H } = load(ALL);
  const letter = H.charsPerLine({ width: 8.5, margin: 1 }, 10.5, 0.3);
  const a4 = H.charsPerLine({ width: 8.27, margin: 1 }, 10.5, 0.3);
  assert.ok(a4 < letter, 'A4 is narrower, so fewer characters fit on a line and a bullet wraps sooner');
});

/* ---- the shared baseline (ADR-0127) ---- */

/** A résumé nobody should send: no name and no way to reach the writer, one job with no dates,
 *  two more in the wrong order, twelve bullets on one of them, each opening "Responsible for"
 *  and running well past three printed lines with no number, result or reason anywhere. */
function badResume(ctx, layoutId) {
  const b = ctx.ResumeDocument.builder().named('Bad').usingLayout(layoutId);
  b.add('header', { fullName: '', phone: '', email: '', locationLine: '' });
  b.section('Work History', s => {
    s.add('work_entry', { role: 'Engineer', company: 'Undated Co', start: '', end: '' },
      w => w.bullet('Responsible for the running of things.'));
    s.add('work_entry', { role: 'Engineer', company: 'Older Co', start: 'January 2015', end: 'March 2018' },
      w => { for (let i = 0; i < 12; i++) {
        w.bullet('Responsible for a stretch of prose that runs on and on well past any sensible ' +
          'bullet length and keeps going, and going, so that it certainly wraps past three ' +
          'printed lines on any sheet of paper at any body size these layouts offer, while ' +
          'saying nothing whatsoever about what was actually done, how it was done, who it ' +
          'was done for, or what on earth came of any of it in the end.');
      } });
    s.add('work_entry', { role: 'Engineer', company: 'Newer Co', start: 'June 2020', end: 'August 2023' },
      w => w.bullet('Responsible for the newer things.'));
  });
  return b.build();
}

test('every registered layout checks the baseline — a bad résumé is never called clean', () => {
  const ctx = load(ALL);
  const { ResumeLayouts: L } = ctx;
  const baseline = L.COMMON_RULES.map(r => r.id).sort();
  assert.ok(baseline.length, 'there is no baseline to check');
  /* Iterating the registry, never a list written here: the whole point is that layout number
     eight cannot ship with a silent Checks panel, and a hardcoded list would not see it. */
  for (const lay of L.all()) {
    const fired = [...new Set(L.runRules(lay, badResume(ctx, lay.id))
      .map(f => f.ruleId).filter(id => baseline.includes(id)))].sort();
    assert.deepEqual(fired, baseline,
      `${lay.id} said nothing about ${baseline.filter(id => !fired.includes(id)).join(', ')}`);
  }
});

test('an end date that is a word is a job still held — and only the words that mean that', () => {
  const ctx = load(ALL);
  const { ResumeDocument: D, ResumeLayouts: L } = ctx;
  const ended = end => {
    const doc = D.builder().usingLayout('jakes-resume')
      .add('header', { fullName: 'A', email: 'a@example.com' })
      .section('Work', s => s.add('work_entry',
        { role: 'R', company: 'C', start: 'June 2020', end }, w => {
          w.bullet('Built the ingest path for 40 teams.');
          w.bullet('Shipped it daily.');
        }))
      .build();
    return L.runRules(L.get('jakes-resume'), doc).map(f => f.ruleId);
  };
  /* The words a résumé actually uses for a job it still holds. Which one is RIGHT is a Layout's
     argument — harvard-classic's `present-not-current` makes it — so the baseline accepts all. */
  for (const word of ['Present', 'Current', 'now', 'Ongoing', 'to date', 'till date']) {
    assert.deepEqual(ended(word), [], `"${word}" should read as a job still held`);
  }
  /* And nothing else. Factoring "to date"/"till date" down to a shared arm once left a bare
     `date`, and an end cell reading literally "date" then silenced this rule altogether. */
  for (const word of ['date', '2020', 'soon', 'TBD', '']) {
    assert.ok(ended(word).includes('dates'), `"${word}" is not a date and is not "still here"`);
  }
});

test('shadowing a baseline rule is refused unless the layout says it means to', () => {
  const ctx = load(ALL);
  const { ResumeLayouts: L } = ctx;
  const spec = (id, rules) => ({
    id, css: () => '', starter: () => {},
    render: { byShape: L.plainStrategies() }, rules,
  });
  /* The failure ADR-0127 exists to prevent: layout number eight names a rule `dates` for its own
     reasons and silently loses the baseline's, which reads as a Checks panel that says nothing. */
  assert.throws(() => L.define(spec('shadower',
    [{ id: 'dates', label: 'my own dates thing', check: () => [] }])),
    /shadow a baseline rule/);
  /* Declared, it registers — and the baseline's is genuinely replaced, not doubled. */
  L.define(spec('deliberate', [{
    id: 'dates', overridesBaseline: true, label: 'my own dates thing',
    check: () => [{ level: 'warn', nodeId: null, message: 'mine' }],
  }]));
  const rules = L.get('deliberate').rules.filter(r => r.id === 'dates');
  assert.equal(rules.length, 1, 'the baseline rule was kept alongside the override');
  assert.equal(rules[0].label, 'my own dates thing');
  /* And a flag naming nothing is refused too, so renaming a baseline rule cannot leave a layout
     claiming to override one that is gone. */
  assert.throws(() => L.define(spec('stale',
    [{ id: 'not-a-baseline-rule', overridesBaseline: true, label: 'x', check: () => [] }])),
    /no baseline rule has that id/);
});
