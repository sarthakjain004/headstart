/* Render decorators — behaviour wrapped around a Layout without the Layout knowing (ADR-0123).
 *
 * The property that matters most is the boring one: a decorator adorns TEXT and never markup.
 * A keyword like "node", "class" or "hh" appears in this renderer's own attributes, and a naive
 * whole-string replace would rewrite them and corrupt the page — silently, and only for users
 * whose job happens to use that word.
 */

const test = require('node:test');
const assert = require('node:assert');
const { load, ALL } = require('./resume_harness.js');

const HH = 'headless-headhunter';

function example(ctx) {
  const lay = ctx.ResumeLayouts.get(HH);
  const b = ctx.ResumeDocument.builder().named('Example').usingLayout(HH);
  lay.example(b);
  return b.build();
}
const render = (ctx, lay, doc) => ctx.ResumeLayouts.renderDocument(lay, doc);
const hits = html => (html.match(/data-node="/g) || []).length;

test('composing nothing gives back the very same layout', () => {
  const ctx = load(ALL);
  const lay = ctx.ResumeLayouts.get(HH);
  assert.equal(ctx.ResumeDecorators.compose(lay, []), lay);
  assert.equal(ctx.ResumeDecorators.compose(lay, [null, null]), lay);
});

test('a decorated layout is still a layout — every node still renders exactly once', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const lay = ctx.ResumeLayouts.get(HH);
  const dressed = ctx.ResumeDecorators.compose(lay, [ctx.ResumeDecorators.highlight(['customer service'])]);
  assert.equal(hits(render(ctx, dressed, doc)), ctx.ResumeDocument.flatten(doc).length);
  assert.equal(dressed.id, lay.id);
  assert.equal(dressed.page.width, lay.page.width);
  assert.equal(typeof dressed.css, 'function');
});

test('highlighting marks the words and leaves the original layout untouched', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const lay = ctx.ResumeLayouts.get(HH);
  const dressed = ctx.ResumeDecorators.compose(lay, [ctx.ResumeDecorators.highlight(['Point of Sale'])]);
  const marked = render(ctx, dressed, doc);
  assert.ok(marked.includes('<mark class="rb-kw-hit">Point of Sale</mark>'));
  assert.ok(!render(ctx, lay, doc).includes('<mark'), 'the registry’s layout is unchanged');
});

test('a keyword that also appears in the markup cannot corrupt it', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  const lay = ctx.ResumeLayouts.get(HH);
  const before = render(ctx, lay, doc);
  // Every one of these occurs in this renderer's own attributes and class names.
  for (const term of ['node', 'class', 'hh', 'data', 'span', 'li']) {
    const dressed = ctx.ResumeDecorators.compose(lay, [ctx.ResumeDecorators.highlight([term])]);
    const after = render(ctx, dressed, doc);
    assert.equal(hits(after), hits(before), `"${term}" broke the node hooks`);
    assert.ok(!/<[a-z]+[^>]*<mark/i.test(after), `"${term}" was marked inside a tag`);
    assert.ok(after.includes('class="hh-name"'), `"${term}" rewrote a class attribute`);
  }
});

test('a keyword with regex characters is matched literally, not compiled', () => {
  const ctx = load(ALL);
  const lay = ctx.ResumeLayouts.get(HH);
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .section('Skills', s => s.add('bullet', { text: 'Wrote C++ and a (very) fast parser' })).build();
  const dressed = ctx.ResumeDecorators.compose(lay, [ctx.ResumeDecorators.highlight(['C++', '(very)'])]);
  const html = render(ctx, dressed, doc);
  assert.ok(html.includes('<mark class="rb-kw-hit">C++</mark>'));
  assert.ok(html.includes('<mark class="rb-kw-hit">(very)</mark>'));
});

test('the longest matching term wins, so a short one cannot cut a long one in half', () => {
  const ctx = load(ALL);
  const lay = ctx.ResumeLayouts.get(HH);
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .section('X', s => s.add('bullet', { text: 'Performed customer service daily' })).build();
  const dressed = ctx.ResumeDecorators.compose(lay,
    [ctx.ResumeDecorators.highlight(['service', 'customer service'])]);
  const html = render(ctx, dressed, doc);
  assert.ok(html.includes('<mark class="rb-kw-hit">customer service</mark>'));
  assert.equal((html.match(/<mark/g) || []).length, 1);
});

test('the version marker lands on the blocks a version reworded, and only those', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, resolve } = ctx.ResumeDocument;
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(example(ctx));
  const bullet = ctx.ResumeDocument.flatten(store.get()).filter(n => n.type === 'bullet')[0];

  store.dispatch(Cmd.addTailoring('Acme'));
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'Reworded' }, store.get().tailorings[0].id));

  const lay = ctx.ResumeLayouts.get(HH);
  const dressed = ctx.ResumeDecorators.compose(lay, [ctx.ResumeDecorators.tailored(store.get())]);
  const html = render(ctx, dressed, resolve(store.get()));
  assert.equal((html.match(/data-tailored="1"/g) || []).length, 1);
  assert.ok(html.includes('data-tailored="1"'));

  store.dispatch(Cmd.activateTailoring(null));
  assert.equal(ctx.ResumeDecorators.tailored(store.get()), null, 'nothing to mark on the master');
});

test('decorators compose — both adornments appear', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, resolve } = ctx.ResumeDocument;
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(example(ctx));
  const bullet = ctx.ResumeDocument.flatten(store.get()).filter(n => n.type === 'bullet')[0];
  store.dispatch(Cmd.addTailoring('Acme'));
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'Ran the espresso bar' },
    store.get().tailorings[0].id));

  const lay = ctx.ResumeLayouts.get(HH);
  const dressed = ctx.ResumeDecorators.compose(lay, [
    ctx.ResumeDecorators.highlight(['espresso']),
    ctx.ResumeDecorators.tailored(store.get()),
  ]);
  const html = render(ctx, dressed, resolve(store.get()));
  assert.ok(html.includes('<mark class="rb-kw-hit">espresso</mark>'));
  assert.ok(html.includes('data-tailored="1"'));
});

test('a downloaded résumé carries no adornment at all', () => {
  const ctx = load(ALL);
  const doc = example(ctx);
  /* renderStandalone resolves the layout from the registry by id, so this is structural rather
     than a flag someone has to remember to pass. */
  const file = ctx.ResumeExport.standaloneHtml(doc);
  assert.ok(!file.includes('<mark'));
  assert.ok(!file.includes('data-tailored'));
  assert.ok(!ctx.ResumeExport.plainText(doc).includes('mark'));
});

test('a term is matched as a term — "Java" is not the first half of "JavaScript"', () => {
  const ctx = load(ALL);
  const lay = ctx.ResumeLayouts.get(HH);
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .section('Skills', s => s.add('bullet', { text: 'Fluent in JavaScript and TypeScript' })).build();
  const dressed = ctx.ResumeDecorators.compose(lay, [ctx.ResumeDecorators.highlight(['Java'])]);
  assert.ok(!render(ctx, dressed, doc).includes('<mark'),
    '"Java" was marked inside "JavaScript"');
});

test('the one- and two-letter languages do not light up ordinary prose', () => {
  const ctx = load(ALL);
  const lay = ctx.ResumeLayouts.get(HH);
  /* Not an edge case on a software job board: Go, R, C, ML and AI ARE the corpus, and every
     one of them is a substring of words a résumé is full of. */
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .section('Work', s => s
      .add('bullet', { text: 'Responsible for the roster, ran reports regularly' })
      .add('bullet', { text: 'Coordinated the customer care crew, good at going the extra mile' })
      .add('bullet', { text: 'Maintained the HTML and XML templates for the retail chain' })).build();
  const dressed = ctx.ResumeDecorators.compose(lay,
    [ctx.ResumeDecorators.highlight(['Go', 'R', 'C', 'ML', 'AI'])]);
  const html = render(ctx, dressed, doc);
  const marked = (html.match(/<mark class="rb-kw-hit">([\s\S]*?)<\/mark>/g) || [])
    .map(m => m.replace(/<\/?mark[^>]*>/g, ''));
  assert.deepEqual(marked, [], 'prose containing none of these terms was marked anyway');
});

test('a term made of punctuation still matches itself, even at the end of a bullet', () => {
  const ctx = load(ALL);
  const lay = ctx.ResumeLayouts.get(HH);
  /* The trailing period is the trap. This template asks for one at the end of every bullet, so
     a boundary that refuses a following "." reports a résumé written to the template's own
     rules as mentioning none of its own languages. */
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .section('Work', s => s
      .add('bullet', { text: 'Wrote the driver in C++ and the tooling in F#.' })
      .add('bullet', { text: 'Built .NET 8 services and a Node.js gateway.' })
      .add('bullet', { text: 'Ported the desktop app to C#.' })).build();
  const dressed = ctx.ResumeDecorators.compose(lay,
    [ctx.ResumeDecorators.highlight(['C++', 'C#', '.NET', 'F#', 'Node.js'])]);
  const marked = (render(ctx, dressed, doc).match(/<mark class="rb-kw-hit">([\s\S]*?)<\/mark>/g) || [])
    .map(m => m.replace(/<\/?mark[^>]*>/g, ''));
  assert.deepEqual(marked.sort(), ['.NET', 'C#', 'C++', 'F#', 'Node.js']);
});

test('the score and the highlight answer the same question', () => {
  const ctx = load(ALL);
  const { mentions, highlight, compose } = ctx.ResumeDecorators;
  const lay = ctx.ResumeLayouts.get(HH);
  const text = 'Shipped a C++ trading engine in Java, plus JavaScript tooling.';
  const doc = ctx.ResumeDocument.builder().usingLayout(HH)
    .section('Work', s => s.add('bullet', { text })).build();
  /* `C++` read as missing while the page was busy marking `C` all over it — two substring
     tests, two answers. One matcher, one answer. */
  for (const term of ['C++', 'C', 'Java', 'JavaScript', 'R', 'Go']) {
    const marked = render(ctx, compose(lay, [highlight([term])]), doc).includes('<mark');
    assert.equal(mentions(text, term), marked, `"${term}": the score and the page disagree`);
  }
  assert.deepEqual(['C++', 'C', 'Java', 'JavaScript', 'R', 'Go'].map(t => mentions(text, t)),
    [true, false, true, true, false, false]);
});
