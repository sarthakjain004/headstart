/* `resume_mcp/inspect_document.js` — the block-by-block reading the MCP server serves.
 *
 * Here rather than in `tests/test_resume_mcp.py` because this is where the model is. ADR-0137's
 * whole decision is that the server does not re-implement `resolve()` or the component
 * catalogue, so the facts it reports are JavaScript's answers and belong in the JavaScript
 * suite — which CI runs on its own step, with a pinned Node, unconditionally. The Python side
 * tests the transport, the account binding and the rendering, and skips when `node` is absent.
 *
 * The script is run as a real subprocess, not required: stdin, argv and the exit code are its
 * contract with the server, and a test that called `inspect()` directly would prove none of it.
 */

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { load, ALL } = require('./resume_harness.js');

const SCRIPT = path.join(
  __dirname, '..', '..', 'src', 'headstart', 'resume_mcp', 'inspect_document.js');

/** Run the script over `doc`, as `view`. Returns the parsed answer; throws on a non-zero exit. */
function reading(doc, view) {
  return JSON.parse(execFileSync('node', [SCRIPT, view || 'master'],
    { input: JSON.stringify(doc), encoding: 'utf8' }));
}

/** The same failure the server turns into a sentence: exit 1, with `error` on stdout. */
function refusal(doc, view) {
  try {
    execFileSync('node', [SCRIPT, view || 'master'],
      { input: JSON.stringify(doc), encoding: 'utf8', stdio: 'pipe' });
  } catch (err) {
    return JSON.parse(err.stdout).error;
  }
  assert.fail('expected the script to refuse this document');
}

const HH = 'headless-headhunter';

/** A résumé with one work entry and two bullets, plus a Projects section. */
function built(ctx) {
  const D = ctx.ResumeDocument;
  const store = new D.Store(null);
  store.adopt(D.builder().named('Lee Korelitz').usingLayout(HH)
    .add('header', { fullName: 'Lee Korelitz', email: 'lee@example.com' })
    .section('Work History', b => b.add('work_entry', { employer: 'Acme' },
      c => { c.bullet('Shipped the payments rewrite.'); c.bullet('Cut p99 by 40%.'); }))
    .section('Projects', b => b.add('project_entry', { name: 'Tiny DB' }))
    .build());
  return store;
}
const byType = (ctx, store, type) =>
  ctx.ResumeDocument.flatten(store.get()).filter(n => n.type === type);
const block = (facts, id) => facts.blocks.find(b => b.id === id);

test('every block is reported with its type, its declared fields and their values', () => {
  const ctx = load(ALL);
  const store = built(ctx);
  const header = byType(ctx, store, 'header')[0];

  const facts = reading(store.get());

  const seen = block(facts, header.id);
  assert.equal(seen.type, 'header');
  assert.equal(seen.label, 'Name & contact');
  /* The fields come from the Component Type, not from what happens to be set — an empty field
     is a field the résumé HAS and has not filled in, which is the thing a caller most wants
     told. `fullName` is set, `phone` is not, and both are listed. */
  const fields = Object.fromEntries(seen.fields.map(f => [f.key, f.value]));
  assert.equal(fields.fullName, 'Lee Korelitz');
  assert.equal(fields.phone, '');
  assert.ok('locationLine' in fields, 'a declared but unset field is still reported');
});

test('a field the Component Type does not declare is reported, not dropped', () => {
  const ctx = load(ALL);
  const store = built(ctx);
  const entry = byType(ctx, store, 'work_entry')[0];
  /* What a document written by an older or newer build looks like. Silently dropping it would
     make this tool answer "nothing is set there" about words that are set. */
  store.dispatch(ctx.ResumeDocument.Commands.setContent(entry.id, { jobTitle: 'Engineer' }));

  const seen = block(reading(store.get()), entry.id);

  assert.equal(seen.undeclared_fields.jobTitle, 'Engineer');
  assert.ok(!seen.fields.some(f => f.key === 'jobTitle'));
});

test('reading it as a version shows that version\'s words and names the master\'s', () => {
  const ctx = load(ALL);
  const D = ctx.ResumeDocument;
  const store = built(ctx);
  const bullet = byType(ctx, store, 'bullet')[0];
  store.dispatch(D.Commands.addTailoring('Stripe backend', 'job-123'));
  const version = store.get().tailorings[0].id;
  store.dispatch(D.Commands.setContentFor(bullet.id, { text: 'Rewrote payments.' }, version));

  const tailored = block(reading(store.get(), version), bullet.id);
  const master = block(reading(store.get(), 'master'), bullet.id);

  const text = tailored.fields.find(f => f.key === 'text');
  assert.equal(text.value, 'Rewrote payments.');
  assert.equal(text.master_value, 'Shipped the payments rewrite.');
  /* And from the master's side, the block says which versions reword it — the question
     "is this sentence tailored anywhere" answered without opening every version. */
  assert.deepEqual(master.reworded_by, ['Stripe backend']);
  assert.equal(master.fields.find(f => f.key === 'text').value,
    'Shipped the payments rewrite.');
  assert.ok(!('master_value' in master.fields.find(f => f.key === 'text')),
    'the master does not disagree with itself');
});

test('a version can be named by its name as well as its id', () => {
  const ctx = load(ALL);
  const store = built(ctx);
  store.dispatch(ctx.ResumeDocument.Commands.addTailoring('Stripe backend'));

  assert.equal(reading(store.get(), 'Stripe backend').viewing.kind, 'version');
  assert.equal(reading(store.get(), 'stripe BACKEND').viewing.name, 'Stripe backend');
  assert.match(refusal(store.get(), 'Datadog'), /no version called "Datadog"/);
});

test('a block off for the whole résumé is told apart from one inside it', () => {
  const ctx = load(ALL);
  const D = ctx.ResumeDocument;
  const store = built(ctx);
  const projects = D.flatten(store.get())
    .find(n => (store.get().content[n.id] || {}).title === 'Projects');
  const inside = byType(ctx, store, 'project_entry')[0];
  store.dispatch(D.Commands.setHidden(projects.id, true, null));

  const facts = reading(store.get());

  /* `isHidden` alone answers false for the entry — it is not on either hidden list. Only
     `resolve()`'s recursive prune knows it is off the page, and the distinction picks which
     switch to flick: the section's, not the entry's. */
  assert.equal(block(facts, projects.id).prints, false);
  assert.equal(block(facts, projects.id).hidden_on_master, true);
  assert.equal(block(facts, inside.id).prints, false);
  assert.equal(block(facts, inside.id).hidden_on_master, false);
});

test('the master says which versions leave a block out, as well as which reword it', () => {
  const ctx = load(ALL);
  const D = ctx.ResumeDocument;
  const store = built(ctx);
  const bullet = byType(ctx, store, 'bullet')[1];
  store.dispatch(D.Commands.addTailoring('Stripe backend'));
  store.dispatch(D.Commands.addTailoring('Datadog SRE'));
  const [stripe, datadog] = store.get().tailorings.map(t => t.id);
  store.dispatch(D.Commands.setHidden(bullet.id, true, stripe));
  store.dispatch(D.Commands.setContentFor(bullet.id, { text: 'Tuned p99.' }, datadog));

  /* Both ways a version overrides a block, answered from the master in one read. Only the
     rewording half was reported at first, so a bullet one version drops entirely looked
     untouched unless every version was opened one at a time. */
  const seen = block(reading(store.get(), 'master'), bullet.id);

  assert.deepEqual(seen.left_out_by, ['Stripe backend']);
  assert.deepEqual(seen.reworded_by, ['Datadog SRE']);
  /* And it is a fact about the document, not about the view: the master still prints it. */
  assert.equal(seen.prints, true);
});

test('a block off for one version still prints on the master', () => {
  const ctx = load(ALL);
  const D = ctx.ResumeDocument;
  const store = built(ctx);
  const bullet = byType(ctx, store, 'bullet')[1];
  store.dispatch(D.Commands.addTailoring('Stripe backend'));
  const version = store.get().tailorings[0].id;
  store.dispatch(D.Commands.setHidden(bullet.id, true, version));

  assert.equal(block(reading(store.get(), version), bullet.id).prints, false);
  assert.equal(block(reading(store.get(), version), bullet.id).hidden_by_this_version, true);
  assert.equal(block(reading(store.get(), 'master'), bullet.id).prints, true);
  assert.equal(block(reading(store.get(), 'master'), bullet.id).hidden_by_this_version, false);
});

test('a pick whose variant is gone is not reported as a rewording', () => {
  const ctx = load(ALL);
  const D = ctx.ResumeDocument;
  const store = built(ctx);
  const bullet = byType(ctx, store, 'bullet')[0];
  store.dispatch(D.Commands.addTailoring('Stripe backend'));
  const version = store.get().tailorings[0].id;
  store.dispatch(D.Commands.setContentFor(bullet.id, { text: 'Rewrote payments.' }, version));
  /* Whatever `resolve()` ignores, this ignores — otherwise the reading claims a difference
     that no export, print or preview would ever show. */
  const doc = D.clone(store.get());
  delete doc.variants[bullet.id];

  assert.deepEqual(block(reading(doc, 'master'), bullet.id).reworded_by, []);
});

test('the document header carries what the account copy is, not just what it says', () => {
  const ctx = load(ALL);
  const store = built(ctx);
  const doc = ctx.ResumeDocument.clone(store.get());
  doc.rev = 4;

  const facts = reading(doc);

  assert.equal(facts.rev, 4);
  assert.equal(facts.layout_id, HH);
  assert.deepEqual(facts.viewing, { kind: 'master' });
  assert.deepEqual(facts.unknown_types, []);
});

test('a record that is not a résumé is refused, not read as an empty one', () => {
  assert.match(refusal({ id: 'rzzz', name: 'not a résumé' }),
    /not a Résumé document/);
});
