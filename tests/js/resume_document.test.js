/* Layers 1 and 3 of the résumé builder — the component catalogue, the document, the command
 * vocabulary and the repository. Run against the REAL browser scripts, evaluated in a vm
 * context, exactly as app_search.test.js and app_trends.test.js do: these exercise the shipped
 * files rather than a copy of their logic.
 */

const test = require('node:test');
const assert = require('node:assert');
const { load } = require('./resume_harness.js');

const MODEL = ['resume_components', 'resume_document', 'resume_repository'];

/** A document with one job and two bullets — the shape most commands act on. */
function sample(ctx) {
  return ctx.ResumeDocument.builder().named('Sample').usingLayout('headless-headhunter')
    .add('header', { fullName: 'Lee Korelitz' })
    .section('Work History', s => s.add('work_entry', { role: 'Cashier', start: 'June 2023' },
      e => { e.bullet('Ran the till', true); e.bullet('Made the coffee'); }))
    .build();
}
const typeOf = (ctx, doc, type) => ctx.ResumeDocument.flatten(doc).filter(n => n.type === type);

/* ---- Layer 1: the catalogue ---- */

test('every catalogued component declares a known shape and known field kinds', () => {
  const ctx = load(MODEL);
  const { SHAPES, KINDS, all } = ctx.ResumeComponents;
  assert.ok(all().length >= 6);
  for (const spec of all()) {
    assert.ok(SHAPES.includes(spec.shape), `${spec.type} has shape ${spec.shape}`);
    for (const f of spec.fields) assert.ok(KINDS.includes(f.kind), `${spec.type}.${f.key}`);
    assert.ok(spec.label, `${spec.type} has no label`);
  }
});

test('define refuses a duplicate type and an unknown shape', () => {
  const ctx = load(MODEL);
  assert.throws(() => ctx.ResumeComponents.define({ type: 'bullet', label: 'x', shape: 'bullet' }),
    /duplicate/);
  assert.throws(() => ctx.ResumeComponents.define({ type: 'novel', label: 'x', shape: 'sidebar' }),
    /unknown shape/);
});

test('accepts honours an explicit list and the wildcard', () => {
  const { ResumeComponents: C } = load(MODEL);
  assert.equal(C.accepts('work_entry', 'bullet'), true);
  assert.equal(C.accepts('work_entry', 'section'), false, 'a section is not a bullet');
  assert.equal(C.accepts('section', 'work_entry'), true, 'a section takes anything');
  assert.equal(C.accepts('bullet', 'bullet'), false, 'a bullet holds nothing');
  assert.equal(C.accepts('section', 'no_such_type'), false);
});

/* ---- Layer 3: the document ---- */

test('the builder seeds a component with the children its type declares', () => {
  const ctx = load(MODEL);
  const doc = ctx.ResumeDocument.builder().usingLayout('x').section('Work', s => s.add('work_entry')).build();
  // work_entry seeds three bullets, so a freshly added job is never an empty box
  assert.equal(typeOf(ctx, doc, 'bullet').length, 3);
});

test('a document without a layout is refused', () => {
  const ctx = load(MODEL);
  assert.throws(() => ctx.ResumeDocument.builder().add('header').build(), /needs a layout/);
});

test('deleting a node takes its whole subtree out of the content map', () => {
  const ctx = load(MODEL);
  const doc = sample(ctx);
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(doc);
  const job = typeOf(ctx, doc, 'work_entry')[0];
  const before = Object.keys(store.get().content).length;
  store.dispatch(ctx.ResumeDocument.Commands.removeNode(job.id));
  // the job and both its bullets, and nothing else
  assert.equal(Object.keys(store.get().content).length, before - 3);
});

test('a duplicate gets fresh ids, so editing the copy leaves the original alone', () => {
  const ctx = load(MODEL);
  const { Commands, flatten, find } = ctx.ResumeDocument;
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(sample(ctx));
  const job = flatten(store.get()).filter(n => n.type === 'work_entry')[0];
  store.dispatch(Commands.duplicateNode(job.id));
  const jobs = flatten(store.get()).filter(n => n.type === 'work_entry');
  assert.equal(jobs.length, 2);
  assert.notEqual(jobs[0].id, jobs[1].id);
  assert.equal(store.get().content[jobs[1].id].role, 'Cashier', 'the copy carries the words');
  store.dispatch(Commands.setContent(jobs[1].id, { role: 'Barista' }));
  assert.equal(store.get().content[jobs[0].id].role, 'Cashier', 'the original is untouched');
});

test('a node cannot be dropped inside itself', () => {
  const ctx = load(MODEL);
  const { Commands, flatten } = ctx.ResumeDocument;
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(sample(ctx));
  const section = flatten(store.get()).filter(n => n.type === 'section')[0];
  const job = flatten(store.get()).filter(n => n.type === 'work_entry')[0];
  store.dispatch(Commands.moveNode(section.id, job.id, 0));
  // the section is still a child of the root; nothing detached
  assert.equal(store.get().root.children.some(c => c.id === section.id), true);
  assert.equal(flatten(store.get()).length, 5, 'no node was lost');
});

test('a move a parent would not accept is refused', () => {
  const ctx = load(MODEL);
  const { Commands, flatten } = ctx.ResumeDocument;
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(sample(ctx));
  const header = flatten(store.get()).filter(n => n.type === 'header')[0];
  const job = flatten(store.get()).filter(n => n.type === 'work_entry')[0];
  // a work entry accepts bullets only
  store.dispatch(Commands.moveNode(header.id, job.id, 0));
  assert.equal(store.get().root.children.some(c => c.id === header.id), true);
});

test('undo and redo walk the same path back and forward', () => {
  const ctx = load(MODEL);
  const { Commands, flatten } = ctx.ResumeDocument;
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(sample(ctx));
  const job = flatten(store.get()).filter(n => n.type === 'work_entry')[0];
  store.dispatch(Commands.setContent(job.id, { role: 'Barista' }));
  store.dispatch(Commands.setContent(job.id, { company: 'Ducks' }), 'other');
  assert.equal(store.get().content[job.id].company, 'Ducks');
  store.undo();
  assert.equal(store.get().content[job.id].company, '');
  assert.equal(store.get().content[job.id].role, 'Barista');
  store.undo();
  assert.equal(store.get().content[job.id].role, 'Cashier');
  store.redo();
  assert.equal(store.get().content[job.id].role, 'Barista');
});

test('typing collapses into one undo step, and a different field starts a new one', () => {
  const ctx = load(MODEL);
  const { Commands, flatten } = ctx.ResumeDocument;
  let clock = 1000;
  const store = new ctx.ResumeDocument.Store(null, { now: () => clock });
  store.adopt(sample(ctx));
  const job = flatten(store.get()).filter(n => n.type === 'work_entry')[0];
  for (const text of ['B', 'Ba', 'Bar', 'Bari']) {
    store.dispatch(Commands.setContent(job.id, { role: text }), 'role');
    clock += 50;
  }
  store.undo();
  assert.equal(store.get().content[job.id].role, 'Cashier', 'four keystrokes, one undo');

  store.redo();
  store.dispatch(Commands.setContent(job.id, { company: 'Ducks' }), 'company');
  store.undo();
  assert.equal(store.get().content[job.id].company, '', 'a different field is its own step');
  assert.equal(store.get().content[job.id].role, 'Bari', 'and did not swallow the previous one');
});

test('a pause longer than the coalescing window starts a new undo step', () => {
  const ctx = load(MODEL);
  const { Commands, flatten } = ctx.ResumeDocument;
  let clock = 0;
  const store = new ctx.ResumeDocument.Store(null, { now: () => clock, coalesceMs: 1200 });
  store.adopt(sample(ctx));
  const job = flatten(store.get()).filter(n => n.type === 'work_entry')[0];
  store.dispatch(Commands.setContent(job.id, { role: 'A' }), 'role');
  clock += 5000;
  store.dispatch(Commands.setContent(job.id, { role: 'AB' }), 'role');
  store.undo();
  assert.equal(store.get().content[job.id].role, 'A', 'the pause split the run');
});

test('changing layout touches the layout id and not one word', () => {
  const ctx = load(MODEL);
  const store = new ctx.ResumeDocument.Store(null);
  store.adopt(sample(ctx));
  const before = JSON.stringify(store.get().content);
  const beforeTree = JSON.stringify(store.get().root);
  store.dispatch(ctx.ResumeDocument.Commands.setLayout('two-column'));
  assert.equal(store.get().layoutId, 'two-column');
  assert.equal(JSON.stringify(store.get().content), before, 'content moved');
  assert.equal(JSON.stringify(store.get().root), beforeTree, 'structure moved');
});

test('the store persists through the repository, debounced, and flush forces it', () => {
  const ctx = load(MODEL);
  const repo = new ctx.ResumeRepository.MemoryRepository();
  const queued = [];
  const store = new ctx.ResumeDocument.Store(repo, {
    schedule: fn => { queued.push(fn); return queued.length; },
    cancel: () => {},
  });
  store.adopt(sample(ctx));
  store.dispatch(ctx.ResumeDocument.Commands.rename('Renamed'));
  assert.equal(repo.list().length, 0, 'nothing written yet — the save is debounced');
  store.flush();
  assert.equal(repo.list()[0].name, 'Renamed');
});

test('switching documents writes the one being left behind first', () => {
  const ctx = load(MODEL);
  const repo = new ctx.ResumeRepository.MemoryRepository();
  /* A save is debounced, so a click landing inside that window used to leave a queued write
     pointing at whatever document was current when it FIRED — by then, the new one. The edit was
     not written late; it went to the wrong document and was lost without a word. */
  const store = new ctx.ResumeDocument.Store(repo, { schedule: () => 1, cancel: () => {} });
  const a = Object.assign(sample(ctx), { id: 'a', name: 'Document A' });
  store.adopt(a);
  store.dispatch(ctx.ResumeDocument.Commands.rename('Edited A'));

  const b = Object.assign(sample(ctx), { id: 'b', name: 'Document B' });
  store.adopt(b);   // the pending write for A must land before B is adopted

  assert.equal(repo.get('a').name, 'Edited A', 'the edit to A was lost');
  assert.equal(store.get().id, 'b');
});

test('a refused write is reported rather than swallowed', () => {
  const ctx = load(MODEL);
  const refusing = {
    list: () => [], get: () => null, remove() {}, lastOpened: () => null, setLastOpened() {},
    durable: true,
    save: () => ({ ok: false, reason: 'quota' }),
  };
  const seen = [];
  const store = new ctx.ResumeDocument.Store(refusing, {
    schedule: () => 1, cancel: () => {}, onError: r => seen.push(r.reason),
  });
  store.adopt(sample(ctx));
  store.dispatch(ctx.ResumeDocument.Commands.rename('Anything'));
  const result = store.flush();
  assert.equal(result.ok, false);
  assert.deepEqual(seen, ['quota'], 'the editor is told, so it can tell the user');
});

/* ---- Layer 3: the repository ---- */

test('the local-storage repository round-trips, lists newest first, and forgets on remove', () => {
  const ctx = load(MODEL);
  const backing = new Map();
  const storage = {
    getItem: k => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: k => backing.delete(k),
  };
  const repo = new ctx.ResumeRepository.LocalStorageRepository(storage);
  const older = Object.assign(sample(ctx), { id: 'a', name: 'Older', updatedAt: '2026-01-01T00:00:00Z' });
  const newer = Object.assign(sample(ctx), { id: 'b', name: 'Newer', updatedAt: '2026-06-01T00:00:00Z' });
  repo.save(older);
  repo.save(newer);
  assert.deepEqual(repo.list().map(r => r.name), ['Newer', 'Older']);
  assert.equal(repo.get('a').name, 'Older');
  repo.remove('a');
  assert.equal(repo.get('a'), null);
  assert.deepEqual(repo.list().map(r => r.id), ['b']);
});

test('a full disk is reported rather than swallowed', () => {
  const ctx = load(MODEL);
  const storage = {
    getItem: () => null,
    setItem: () => { const e = new Error('exceeded the quota'); e.name = 'QuotaExceededError'; throw e; },
    removeItem: () => {},
  };
  const repo = new ctx.ResumeRepository.LocalStorageRepository(storage);
  const result = repo.save(sample(ctx));
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'quota');
});

test('a corrupt index loses the listing, never the documents', () => {
  const ctx = load(MODEL);
  const backing = new Map([[ctx.ResumeRepository.INDEX, '{not json']]);
  const storage = {
    getItem: k => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: k => backing.delete(k),
  };
  const repo = new ctx.ResumeRepository.LocalStorageRepository(storage);
  const doc = Object.assign(sample(ctx), { id: 'kept' });
  backing.set(ctx.ResumeRepository.DOC + 'kept', JSON.stringify(doc));
  assert.deepEqual(repo.list(), []);
  assert.equal(repo.get('kept').id, 'kept', 'the document is still readable');
});

test('storage that throws on write falls back to memory rather than to nothing', () => {
  const ctx = load(MODEL);
  const win = { localStorage: { setItem: () => { throw new Error('blocked'); },
    getItem: () => null, removeItem: () => {} } };
  const repo = ctx.ResumeRepository.detect(win);
  assert.equal(repo.durable, false, 'and it says so, so the editor can warn');
  repo.save(sample(ctx));
  assert.equal(repo.list().length, 1, 'still usable for the session');
});
