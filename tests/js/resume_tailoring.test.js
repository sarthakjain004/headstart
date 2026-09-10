/* Tailoring — one résumé, many versions, each storing only what it changes (ADR-0124).
 *
 * The property worth guarding is not "a version can differ". It is that a version stores
 * DIFFERENCES: fixing a sentence on the master has to reach every version that never disagreed
 * with it, or the feature is whole-document copies wearing a better name, and every typo has to
 * be fixed once per application.
 */

const test = require('node:test');
const assert = require('node:assert');
const { load, ALL } = require('./resume_harness.js');

const HH = 'headless-headhunter';

function opened(ctx) {
  const lay = ctx.ResumeLayouts.get(HH);
  const b = ctx.ResumeDocument.builder().named('Lee Korelitz').usingLayout(HH);
  lay.example(b);
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(b.build());
  return store;
}
const bullets = (ctx, store) =>
  ctx.ResumeDocument.flatten(store.get()).filter(n => n.type === 'bullet');

test('a version keeps its own words and leaves the master alone', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, contentOf } = ctx.ResumeDocument;
  const store = opened(ctx);
  const bullet = bullets(ctx, store)[0];
  const original = contentOf(store.get(), bullet.id).text;

  store.dispatch(Cmd.addTailoring('Acme — Backend Engineer'));
  const version = store.get().tailorings[0].id;
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'Rewritten for Acme' }, version));

  assert.equal(contentOf(store.get(), bullet.id).text, 'Rewritten for Acme', 'the version changed');
  assert.equal(contentOf(store.get(), bullet.id, null).text, original, 'the master did not');
});

test('a version stores only the differences, so a fix to the master still reaches it', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, contentOf } = ctx.ResumeDocument;
  const store = opened(ctx);
  const [first, second] = bullets(ctx, store);

  store.dispatch(Cmd.addTailoring('Acme'));
  const version = store.get().tailorings[0].id;
  store.dispatch(Cmd.setContentFor(first.id, { text: 'Tailored' }, version));

  // a typo fixed on the master, on a bullet this version never touched
  store.dispatch(Cmd.setContentFor(second.id, { text: 'Corrected on the master' }, null));
  assert.equal(contentOf(store.get(), second.id).text, 'Corrected on the master',
    'the version inherits the master where it never disagreed');
  assert.equal(contentOf(store.get(), first.id).text, 'Tailored', 'and keeps what it did change');

  // only the one node was forked
  assert.deepEqual(Object.keys(store.get().variants), [first.id]);
});

test('a version can hand a block back to the master', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, contentOf } = ctx.ResumeDocument;
  const store = opened(ctx);
  const bullet = bullets(ctx, store)[0];
  const original = contentOf(store.get(), bullet.id).text;

  store.dispatch(Cmd.addTailoring('Acme'));
  const version = store.get().tailorings[0].id;
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'Tailored' }, version));
  store.dispatch(Cmd.clearVariant(bullet.id, version));

  assert.equal(contentOf(store.get(), bullet.id).text, original);
  assert.deepEqual(store.get().variants, {}, 'and the orphaned wording is not left behind');
});

test('a version can leave a block out without deleting it', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, resolve, flatten } = ctx.ResumeDocument;
  const store = opened(ctx);
  const section = flatten(store.get()).filter(n => n.type === 'section')[1];
  const whole = flatten(store.get()).length;
  const inSection = flatten({ root: section }).length + 1;

  store.dispatch(Cmd.addTailoring('Short version'));
  store.dispatch(Cmd.setHidden(section.id, true, store.get().tailorings[0].id));

  assert.equal(flatten(resolve(store.get())).length, whole - inSection, 'pruned from the version');
  assert.equal(flatten(store.get()).length, whole, 'still in the résumé itself');

  store.dispatch(Cmd.activateTailoring(null));
  assert.equal(flatten(resolve(store.get())).length, whole, 'and the master is unaffected');
});

test('deleting a version takes its wordings with it and nothing else', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, contentOf } = ctx.ResumeDocument;
  const store = opened(ctx);
  const [first, second] = bullets(ctx, store);

  store.dispatch(Cmd.addTailoring('Keep'));
  const keep = store.get().tailorings[0].id;
  store.dispatch(Cmd.setContentFor(first.id, { text: 'Kept wording' }, keep));

  store.dispatch(Cmd.addTailoring('Drop'));
  const drop = store.get().tailorings[1].id;
  store.dispatch(Cmd.setContentFor(second.id, { text: 'Doomed wording' }, drop));

  store.dispatch(Cmd.removeTailoring(drop));
  assert.equal(store.get().activeTailoring, null, 'deleting the active version returns to the master');
  assert.deepEqual(Object.keys(store.get().variants), [first.id], 'only the survivor’s fork remains');

  store.dispatch(Cmd.activateTailoring(keep));
  assert.equal(contentOf(store.get(), first.id).text, 'Kept wording');
});

test('deleting a block clears it out of every version too', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, flatten } = ctx.ResumeDocument;
  const store = opened(ctx);
  const bullet = bullets(ctx, store)[0];

  store.dispatch(Cmd.addTailoring('Acme'));
  const version = store.get().tailorings[0].id;
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'Tailored' }, version));
  store.dispatch(Cmd.setHidden(bullet.id, true, version));
  store.dispatch(Cmd.removeNode(bullet.id));

  const t = store.get().tailorings[0];
  assert.deepEqual(store.get().variants, {}, 'no orphaned wording');
  assert.deepEqual(t.picks, {}, 'no pick pointing at a node that is gone');
  assert.deepEqual(t.hidden, [], 'no stale hidden id');
  assert.equal(flatten(store.get()).some(n => n.id === bullet.id), false);
});

test('the version on screen is what gets checked and printed, under its own name', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, resolve } = ctx.ResumeDocument;
  const { ResumeExport: E, ResumeLayouts: L } = ctx;
  const store = opened(ctx);
  const bullet = bullets(ctx, store)[0];

  store.dispatch(Cmd.addTailoring('Acme — Backend Engineer'));
  const version = store.get().tailorings[0].id;
  // deliberately break a rule in the version only
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'One thing. And another thing.' }, version));

  const shown = resolve(store.get());
  assert.ok(E.plainText(shown).includes('One thing. And another thing.'));
  assert.equal(E.filename(shown, 'pdf'), 'Lee_Korelitz_Acme_Backend_Engineer.pdf',
    'so three tailored PDFs are not three files with the same name');

  const ids = L.runRules(L.get(HH), shown).map(f => f.ruleId);
  assert.ok(ids.includes('one-sentence'), 'the version is checked, not the master');

  store.dispatch(Cmd.activateTailoring(null));
  assert.deepEqual(L.runRules(L.get(HH), resolve(store.get())), [], 'the master is still clean');
});

test('duplicating a reworded block keeps the words the version was showing', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, contentOf, flatten } = ctx.ResumeDocument;
  const store = opened(ctx);
  const bullet = bullets(ctx, store)[0];

  store.dispatch(Cmd.addTailoring('Acme'));
  const version = store.get().tailorings[0].id;
  store.dispatch(Cmd.setContentFor(bullet.id, { text: 'Tailored for Acme' }, version));
  store.dispatch(Cmd.duplicateNode(bullet.id));

  const copies = flatten(store.get()).filter(n => n.type === 'bullet');
  const copy = copies[copies.indexOf(copies.find(n => n.id === bullet.id)) + 1];
  assert.equal(contentOf(store.get(), copy.id).text, 'Tailored for Acme',
    'the copy fell back to the master’s words with nothing to say the tailoring was dropped');
  // and the copy is its own variant — editing it must not change the original
  store.dispatch(Cmd.setContentFor(copy.id, { text: 'Changed' }, version));
  assert.equal(contentOf(store.get(), bullet.id).text, 'Tailored for Acme');
});

test('the JSON backup carries every version; resolve is identity on the master', () => {
  const ctx = load(ALL);
  const { Commands: Cmd, resolve } = ctx.ResumeDocument;
  const store = opened(ctx);
  store.dispatch(Cmd.addTailoring('Acme'));
  store.dispatch(Cmd.setContentFor(bullets(ctx, store)[0].id, { text: 'Tailored' },
    store.get().tailorings[0].id));

  const back = ctx.ResumeExport.importJson(JSON.stringify(store.get()));
  assert.equal(back.tailorings.length, 1);
  assert.equal(Object.keys(back.variants).length, 1);

  store.dispatch(Cmd.activateTailoring(null));
  assert.equal(resolve(store.get()), store.get(), 'no clone, no cost, on the master path');
});
