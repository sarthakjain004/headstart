/* The account copy of a Résumé document (ADR-0124, ADR-0131), against the real
 * src/headstart/ui/static/resume/resume_sync.js.
 *
 * Everything that can go wrong here loses somebody's writing, so the tests are written against
 * that: the cadence (a Git commit must never sit behind a keystroke), the revision protocol (a
 * failed push must not claim a revision the server never saw), the conflict path (neither copy
 * may be discarded), and the stale-payload trap (a push outlives the typing that follows it).
 *
 * The wire is a fake `request` — the module takes one so these run with no network and no clock.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { DIR } = require('./resume_harness.js');

/** A répository stand-in with the four methods the sync layer uses. */
function fakeRepo(docs) {
  const map = new Map((docs || []).map(d => [d.id, JSON.parse(JSON.stringify(d))]));
  return {
    durable: true,
    saves: [],
    get(id) { const d = map.get(id); return d ? JSON.parse(JSON.stringify(d)) : null; },
    save(doc) { map.set(doc.id, JSON.parse(JSON.stringify(doc))); this.saves.push(doc.id); return { ok: true }; },
    remove(id) { map.delete(id); },
    all: () => Array.from(map.values()),
  };
}

/** A scripted wire. `answers` is a queue of {status, body}; every call is recorded. */
function fakeWire(answers) {
  const queue = (answers || []).slice();
  const calls = [];
  const request = (url, init) => {
    calls.push({ url, method: (init && init.method) || 'GET',
      body: init && init.body ? JSON.parse(init.body) : null });
    const next = queue.length ? queue.shift() : { status: 200, body: { ok: true, rev: 1 } };
    if (next.reject) return Promise.reject(new Error('offline'));
    return Promise.resolve({ status: next.status, json: () => Promise.resolve(next.body) });
  };
  return { request, calls, queue };
}

/** A fresh module in its own context, plus a controllable clock and timer queue. */
function loadSync(options) {
  const opts = options || {};
  const ctx = { console, setTimeout, clearTimeout, Date, JSON, Math, Object, Array, String };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  /* `resume_components` then `resume_document` first: the sync layer mints a document id for the
     copy it keeps aside after a conflict, and that lives in `ResumeDocument.newDocumentId` —
     which is also where the Builder mints one, so there is a single spelling of the shape
     `store.py`'s `_RESUME_ID` guards. `resume_export` because every document arriving from the
     wire goes through its `sanitiseIds`, the same hardening the import path has always had —
     stubbing that here would test the stub rather than the thing that keeps a hostile or
     malformed record from reaching the renderer. */
  for (const name of ['resume_components', 'resume_document', 'resume_export', 'resume_sync']) {
    const file = path.join(DIR, name + '.js');
    vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
  }

  const timers = [];
  const clock = { at: 1000000 };
  const messages = [];
  const adopted = [];
  const wire = opts.wire || fakeWire(opts.answers);
  const repository = opts.repository || fakeRepo(opts.docs);
  const sync = ctx.ResumeSync.create({
    repository,
    request: opts.noWire ? null : wire.request,
    live: opts.live || (() => null),
    now: () => clock.at,
    schedule: (fn, ms) => { timers.push({ fn, at: clock.at + ms }); return timers.length - 1; },
    cancel: handle => { if (timers[handle]) timers[handle].cancelled = true; },
    onMessage: (text, sticky) => messages.push({ text, sticky }),
    onAdopt: (incoming, mine) => adopted.push({ incoming, mine }),
  });
  /* Run every timer that is due at the current clock, the way a browser would. */
  const tick = ms => {
    clock.at += ms || 0;
    for (const timer of timers.slice()) {
      if (!timer.cancelled && !timer.ran && timer.at <= clock.at) { timer.ran = true; timer.fn(); }
    }
  };
  return { ctx, sync, wire, repository, timers, clock, tick, messages, adopted,
    MIN: ctx.ResumeSync.MIN_INTERVAL };
}

/** Let every queued promise run. The wire is asynchronous and the clock is not, so a test that
 *  ticks twice in a row is asking about a push whose answer has not arrived yet. */
const settled = () => new Promise(resolve => setTimeout(resolve, 0));

const aDoc = (fields) => Object.assign({
  schema: 1, id: 'rmfk3n2abcd', name: 'Ada', layoutId: 'headless-headhunter',
  updatedAt: '2026-09-10T10:00:00+00:00', root: { id: '__root__', children: [] },
  content: {}, sync: true, rev: 0,
}, fields || {});

/* ---- the write cadence -------------------------------------------------------------------
   ADR-0124 is explicit that the localStorage debounce is the wrong instrument and must not be
   reused: localStorage is free to write and a commit is not. These are the tests that would
   fail if somebody wired this to a keystroke. */

test('a local save alone never pushes — nothing reaches the wire until an event does', () => {
  const { sync, wire } = loadSync({ docs: [aDoc()] });
  for (let i = 0; i < 50; i++) sync.note(aDoc({ name: 'Ada ' + i }));
  assert.equal(wire.calls.length, 0, 'typing pushed to the account');
});

test('the heartbeat is a FLOOR between pushes — one per interval, and only when dirty', () => {
  const { sync, wire, tick, MIN } = loadSync({ docs: [aDoc()] });
  assert.ok(MIN >= 60000, 'the floor between heartbeat pushes is not minutes');

  /* The first edit after a quiet spell is written promptly: the interval is a floor between
     commits, not a delay imposed on the first one, and a switch that says "on" while the first
     hour of writing sits nowhere but this browser is the dishonest state to avoid. */
  sync.note(aDoc({ name: 'first' }));
  tick(0);
  assert.equal(wire.calls.length, 1);
  assert.equal(wire.calls[0].method, 'PUT');

  return settled().then(() => {
    /* Everything typed after it waits out the floor. This is the assertion that fails if anyone
       ever wires this to the localStorage debounce. */
    for (let i = 0; i < 200; i++) sync.note(aDoc({ name: 'more ' + i }));
    tick(MIN - 1);
    assert.equal(wire.calls.length, 1, 'two hundred keystrokes cost more than one commit');
    tick(2);
    assert.equal(wire.calls.length, 2);

    return settled();
  }).then(() => {
    /* Nothing has changed since — the next window must cost nothing at all. */
    tick(MIN * 2);
    assert.equal(wire.calls.length, 2, 'an untouched document was pushed again');
  });
});

test('a résumé the Account never switched on is never pushed by anything', () => {
  const { sync, wire, tick, MIN } = loadSync({ docs: [aDoc({ sync: false })] });
  sync.note(aDoc({ sync: false }));
  tick(MIN * 3);
  return sync.flush('tab hidden').then(() => {
    assert.equal(wire.calls.length, 0, 'an opted-out résumé reached the account');
  });
});

test('an explicit save and a tab-hide push immediately, without waiting for the interval', () => {
  const { sync, wire, tick } = loadSync({ docs: [aDoc()] });
  sync.note(aDoc({ name: 'edited' }));
  tick(0);
  return sync.flush('tab hidden').then(() => {
    assert.equal(wire.calls.length, 1, 'the tab going away did not push');
    /* …and a second hide with nothing new must not commit again. */
    return sync.flush('tab hidden').then(() => assert.equal(wire.calls.length, 1));
  });
});

/* ---- the revision protocol ---------------------------------------------------------------- */

test('a push carries one past the stored revision, and writes it back only when accepted', () => {
  const { sync, wire, repository } = loadSync({
    docs: [aDoc({ rev: 4 })], answers: [{ status: 200, body: { ok: true, rev: 5 } }],
  });
  sync.note(aDoc({ rev: 4 }));
  return sync.flush('save').then(() => {
    assert.equal(wire.calls[0].body.rev, 5, 'the push did not offer rev + 1');
    assert.equal(repository.get('rmfk3n2abcd').rev, 5, 'the answered revision was not kept');
    assert.equal(sync.status().state, 'ready');
  });
});

test('a refused push leaves the local revision alone, so the next one is not refused forever', () => {
  const { sync, repository, wire } = loadSync({
    docs: [aDoc({ rev: 4 })],
    answers: [{ status: 500, body: { error: 'the hub fell over' } },
      { status: 200, body: { ok: true, rev: 5 } }],
  });
  sync.note(aDoc({ rev: 4 }));
  return sync.flush('save').then(() => {
    assert.equal(repository.get('rmfk3n2abcd').rev, 4, 'a failed push claimed a revision');
    return sync.flush('save');
  }).then(() => {
    assert.equal(wire.calls[1].body.rev, 5, 'the retry did not offer the same revision again');
    assert.equal(repository.get('rmfk3n2abcd').rev, 5);
  });
});

test('the answered revision is stamped on the document as it stands NOW, not on what was sent', () => {
  /* The push is a round trip and the user keeps typing through it. Writing the answer onto the
     payload object and saving THAT puts the pre-push words back into storage — a silent loss of
     everything typed during the request. */
  let open = aDoc({ rev: 1, name: 'before the push' });
  const { sync, repository } = loadSync({
    docs: [open], live: () => open, answers: [{ status: 200, body: { ok: true, rev: 2 } }],
  });
  sync.note(open);
  const pushing = sync.flush('save');
  /* The editor dispatches a command under the request. Every command REPLACES the document
     object (resume_document.js clones it), so what was pushed is now a different object from
     what the tab holds — which is the whole trap. */
  open = Object.assign({}, open, { name: 'typed while it was in flight' });
  repository.save(open);
  return pushing.then(() => {
    const kept = repository.get('rmfk3n2abcd');
    assert.equal(kept.name, 'typed while it was in flight', 'the in-flight edit was overwritten');
    assert.equal(kept.rev, 2);
  });
});

/* ---- conflicts (ADR-0124 decision 4) ------------------------------------------------------- */

test('a refused push keeps BOTH copies and never picks a winner', () => {
  const theirs = aDoc({ rev: 9, name: 'written on the desktop' });
  const mine = aDoc({ rev: 1, name: 'written on the laptop' });
  const { sync, repository, adopted, messages } = loadSync({
    docs: [mine], live: () => mine,
    answers: [{ status: 409, body: { error: 'this résumé changed somewhere else', stored: theirs } }],
  });
  sync.note(mine);
  return sync.flush('save').then(() => {
    const all = repository.all();
    assert.equal(all.length, 2, 'a conflict left one copy, not two');
    const kept = all.find(d => d.id === 'rmfk3n2abcd');
    const loser = all.find(d => d.id !== 'rmfk3n2abcd');
    assert.equal(kept.name, 'written on the desktop', 'the account copy did not win its own id');
    assert.equal(loser.name, 'written on the laptop (this device)');
    assert.equal(loser.sync, false, 'the kept-aside copy races for the same slot');
    assert.equal(loser.rev, 0);
    /* The editor is handed the winner so the user is not left typing into a stale document. */
    assert.equal(adopted.length, 1);
    assert.equal(adopted[0].incoming.name, 'written on the desktop');
    assert.ok(messages.some(m => m.sticky), 'a conflict faded away after two seconds');
    /* And the status line describes the document now open — which is the account's copy, byte
       for byte — rather than the one that just moved out of the way. */
    assert.equal(sync.status().error, '');
    assert.ok(sync.status().at, 'the adopted copy reads as unsaved');
  });
});

test('a 409 with nothing usable in it stops rather than guessing', () => {
  const { sync, repository, adopted, messages } = loadSync({
    docs: [aDoc({ rev: 1 })], answers: [{ status: 409, body: { error: 'conflict' } }],
  });
  sync.note(aDoc({ rev: 1 }));
  return sync.flush('save').then(() => {
    assert.equal(repository.all().length, 1, 'a copy was invented from nothing');
    assert.equal(adopted.length, 0);
    assert.ok(messages.some(m => m.sticky && /nothing here was overwritten/i.test(m.text)));
    assert.equal(sync.status().error, 'conflict');
  });
});

/* ---- degrading honestly (open question 4) --------------------------------------------------- */

test('signed out says so, keeps the work dirty, and does not bump the revision', () => {
  const { sync, repository, messages } = loadSync({
    docs: [aDoc({ rev: 3 })], answers: [{ status: 401, body: { error: 'sign in first' } },
      { status: 200, body: { ok: true, rev: 4 } }],
  });
  sync.note(aDoc({ rev: 3 }));
  return sync.flush('save').then(() => {
    assert.equal(sync.status().state, 'signed-out');
    assert.equal(repository.get('rmfk3n2abcd').rev, 3);
    assert.ok(messages.some(m => m.sticky && /signed out/i.test(m.text)));
    /* Still dirty: signing back in and saving must send the same edit, not lose it. */
    return sync.flush('save');
  }).then(() => assert.equal(repository.get('rmfk3n2abcd').rev, 4));
});

test('a deployment that keeps no account copies reads as off, and nothing throws', () => {
  const { sync } = loadSync({ docs: [aDoc()], answers: [{ status: 503, body: { error: 'not configured' } }] });
  return sync.refresh().then(state => {
    assert.equal(state, 'off');
    assert.deepEqual(sync.rows(), []);
  });
});

test('a browser with no fetch at all is off rather than an exception', () => {
  const { sync } = loadSync({ docs: [aDoc()], noWire: true });
  return sync.refresh().then(state => {
    assert.equal(state, 'off');
    assert.equal(sync.status().state, 'off');
  });
});

test('an unreachable hub keeps the edit and tries again at the next event', () => {
  const wire = fakeWire([{ reject: true }, { status: 200, body: { ok: true, rev: 1 } }]);
  const { sync, repository } = loadSync({ docs: [aDoc()], wire });
  sync.note(aDoc());
  return sync.flush('save').then(() => {
    assert.equal(repository.get('rmfk3n2abcd').rev, 0, 'an offline push claimed a revision');
    return sync.flush('save');
  }).then(() => assert.equal(repository.get('rmfk3n2abcd').rev, 1));
});

test('a push that never landed stops status() reading as a saved account copy', () => {
  /* The lie this pins, in the shape the user saw it: the switch on, the line reading
     "Saved 3 min ago", and every push since the switch went on having failed. `status()` kept
     `ready` because only an ANSWERED request ever touched the state, and it kept an empty error
     because the off/unreachable branch of `_settle` said nothing at all — so the line that
     paints it fell through to `at`, which is the last SUCCESS and never moves. Two mechanisms,
     one sentence on screen; fixing either alone leaves the same sentence on screen. */
  const doc = aDoc({ sync: true, rev: 0 });
  const wire = fakeWire([
    { status: 200, body: { ok: true, rev: 1 } }, { reject: true }, { reject: true },
    { status: 200, body: { ok: true, rev: 2 } },
  ]);
  const { sync, clock } = loadSync({ docs: [doc], live: () => doc, wire });
  sync.note(doc);
  return sync.flush('save').then(() => {
    const landed = sync.status().at;
    assert.equal(sync.status().state, 'ready');
    assert.ok(landed, 'a successful push recorded no time');

    clock.at += 180000;
    sync.note(doc);
    return sync.flush('save').then(() => {
      assert.notEqual(sync.status().state, 'ready',
        'status() still reports a working account copy after an unanswered push');
      assert.ok(sync.status().error,
        'nothing marks the failure, so the line falls back to the last success and says "Saved"');
      assert.equal(sync.status().at, landed, 'a failed push moved the saved-at time');

      clock.at += 180000;
      sync.note(doc);
      return sync.flush('save');
    }).then(() => {
      assert.ok(sync.status().error, 'the second failure in a row read as saved');
      /* And it clears itself the moment one really lands — an error that sticks after the work
         is on the account is the same dishonesty pointing the other way. */
      clock.at += 180000;
      sync.note(doc);
      return sync.flush('save');
    }).then(() => {
      assert.equal(sync.status().state, 'ready');
      assert.equal(sync.status().error, '');
      assert.notEqual(sync.status().at, landed, 'a push that landed did not move the time');
    });
  });
});

test('a deployment that keeps no account copies says so on a PUSH, not only on a probe', () => {
  /* Same branch, the other state. `refresh` has always reported this one; a push through it
     said nothing, so the line kept the last success here too. */
  const doc = aDoc({ sync: true, rev: 0 });
  const { sync } = loadSync({
    docs: [doc], live: () => doc,
    answers: [{ status: 503, body: { error: 'not configured' } }],
  });
  sync.note(doc);
  return sync.flush('save').then(() => {
    assert.equal(sync.status().state, 'off');
    assert.ok(sync.status().error, 'a push into a deployment that stores nothing read as saved');
  });
});

/* ---- what arrives from the wire -------------------------------------------------------------
   A document coming back from the Account is the browser's own export round-tripped through a
   store, and it reaches `renderDocument` without a file dialog in between — so it must get the
   hardening the import path has. `Export.sanitiseIds` was called from `importJson` and nowhere
   else, which made the account path the LESS checked of the two. */

const aHostileRecord = () => ({
  schema: 1, id: 'rmfk3n2wxyz', name: 'From the desktop', layoutId: 'headless-headhunter',
  updatedAt: '2026-09-09T00:00:00+00:00',
  /* #418's shape exactly: an object is truthy and not iterable, and `|| []` does not save it.
     It reached the renderer as "doc.root.children is not iterable" out of the middle of a paint. */
  root: { id: '__root__', type: '__root__', children: { '0': { id: 'a', children: [] } } },
  content: { 'x"><img src=x onerror=alert(1)>': { text: 'hi' } }, sync: true, rev: 4,
});

test('a document pulled from the account is hardened exactly like an imported file', () => {
  const { sync } = loadSync({ docs: [], answers: [{ status: 200, body: aHostileRecord() }] });
  return sync.pull('rmfk3n2wxyz').then(incoming => {
    assert.ok(Array.isArray(incoming.root.children),
      'the pull path handed the renderer a children object it cannot iterate');
    assert.ok(Object.keys(incoming.content).every(id => /^[\w-]{1,64}$/.test(id)),
      'an id the editor puts in a selector and in markup arrived unchecked');
    /* And the two things `importJson` resets stay untouched: this IS the account's copy, and a
       cleared revision would make its next push land as a first push. */
    assert.equal(incoming.id, 'rmfk3n2wxyz');
    assert.equal(incoming.rev, 4);
    assert.equal(incoming.sync, true);
  });
});

test('the copy adopted after a conflict is hardened too — same bytes, same door', () => {
  const doc = aDoc({ sync: true, rev: 1 });
  const { sync, repository, adopted } = loadSync({
    docs: [doc], live: () => doc,
    answers: [{ status: 409, body: { error: 'changed elsewhere', stored: aHostileRecord() } }],
  });
  sync.note(doc);
  return sync.flush('save').then(() => {
    assert.equal(adopted.length, 1, 'the account copy was not adopted');
    assert.ok(Array.isArray(adopted[0].incoming.root.children),
      'the conflict path opened a document the renderer cannot paint');
    assert.ok(Array.isArray(repository.get('rmfk3n2wxyz').root.children),
      'and stored it in that state, so it throws again on the next load');
  });
});

test('the account copy can be taken deliberately, and this device keeps its own', () => {
  /* What the Résumés list offers when a row is behind: neither copy is discarded, exactly as a
     refused push resolves it — because the local one may hold edits that never went up. */
  const mine = aDoc({ sync: true, rev: 1, name: 'Ada' });
  const stored = Object.assign(aHostileRecord(), { id: 'rmfk3n2abcd' });
  const { sync, repository, adopted } = loadSync({
    docs: [mine], live: () => mine, answers: [{ status: 200, body: stored }],
  });
  return sync.adoptAccountCopy('rmfk3n2abcd').then(got => {
    assert.equal(got, true);
    assert.equal(adopted[0].incoming.name, 'From the desktop', 'it did not open the account copy');
    assert.equal(repository.get('rmfk3n2abcd').name, 'From the desktop');
    const kept = repository.all().find(d => /this device/.test(d.name || ''));
    assert.ok(kept, "this device's copy was discarded");
    assert.equal(kept.sync, false, 'the kept copy races the account for the same slot');
    assert.equal(kept.rev, 0);
  });
});

test('there is nothing to adopt for a document this browser does not have', () => {
  const { sync, wire } = loadSync({ docs: [] });
  return sync.adoptAccountCopy('rmfk3n2abcd').then(got => {
    assert.equal(got, false);
    assert.equal(wire.calls.length, 0, 'it went to the wire for a document it cannot keep beside');
  });
});

/* ---- the opt-in itself ---------------------------------------------------------------------- */

test('switching on pushes at once; switching off takes the copy off the account', () => {
  const doc = aDoc({ sync: false, rev: 0 });
  const wire = fakeWire([{ status: 200, body: { ok: true, rev: 1 } }, { status: 200, body: { ok: true } }]);
  const { sync, repository } = loadSync({ docs: [doc], live: () => doc, wire });
  return sync.setEnabled(doc, true).then(() => {
    assert.equal(wire.calls[0].method, 'PUT', 'the switch did not store anything');
    assert.equal(repository.get('rmfk3n2abcd').sync, true);
    assert.equal(repository.get('rmfk3n2abcd').rev, 1);
    return sync.setEnabled(doc, false);
  }).then(gone => {
    assert.equal(gone, true, 'a successful removal did not report itself');
    /* OFF removes it, rather than merely stopping future pushes — leaving a résumé on the
       account after being told to stop is the same defect as never having asked. */
    assert.equal(wire.calls[1].method, 'DELETE');
    assert.equal(wire.calls[1].url, '/resumes/rmfk3n2abcd');
    assert.equal(repository.get('rmfk3n2abcd').sync, false);
    assert.equal(repository.get('rmfk3n2abcd').rev, 0, 'a stale revision would refuse the next push');
  });
});

test('turning it off only says "removed" when the account copy really went', () => {
  /* The compounding failure this guards, in order: the DELETE is refused, the switch reports
     success anyway, `rev` is cleared locally — and the next time the user turns sync back on,
     their push of `rev 1` lands against the stored `rev N`, 409s, and their own résumé is
     duplicated beside itself as "… (this device)". One ignored response, one lost résumé. */
  const doc = aDoc({ sync: true, rev: 4 });
  const { sync, repository, messages } = loadSync({
    docs: [doc], live: () => doc,
    answers: [{ status: 401, body: { error: 'sign in first' } }],
  });
  return sync.setEnabled(doc, false).then(gone => {
    assert.equal(gone, false);
    assert.equal(doc.sync, true, 'the switch went off while the copy is still up there');
    assert.equal(repository.get('rmfk3n2abcd').rev, 4, 'the revision was cleared on a failed delete');
    assert.ok(messages.some(m => m.sticky && /still on your account/i.test(m.text)));
  });
});

test('a 404 on the way off counts as gone — the record is not there, which is what was asked', () => {
  const doc = aDoc({ sync: true, rev: 4 });
  const { sync, repository } = loadSync({
    docs: [doc], live: () => doc, answers: [{ status: 404, body: { error: 'no such résumé' } }],
  });
  return sync.setEnabled(doc, false).then(gone => {
    assert.equal(gone, true);
    assert.equal(repository.get('rmfk3n2abcd').rev, 0);
    assert.equal(repository.get('rmfk3n2abcd').sync, false);
  });
});

test('switching a document while another is dirty pushes the one that changed', () => {
  const first = aDoc({ id: 'rmfk3n2aaaa', name: 'first' });
  const second = aDoc({ id: 'rmfk3n2bbbb', name: 'second' });
  const { sync, wire } = loadSync({ docs: [first, second] });
  sync.note(first);
  sync.note(second);   // the editor opened another résumé before the heartbeat came round
  return Promise.resolve().then(() => {
    assert.equal(wire.calls.length, 1, 'the outgoing document was not written');
    assert.equal(wire.calls[0].url, '/resumes/rmfk3n2aaaa', 'the wrong document was pushed');
  });
});

test('the listing is what a browser that has never seen these résumés reads', () => {
  const rows = [{ id: 'rmfk3n2abcd', name: 'Ada', layoutId: 'jakes-resume', updatedAt: 'x', rev: 3 }];
  const { sync } = loadSync({ answers: [{ status: 200, body: rows }] });
  return sync.refresh().then(state => {
    assert.equal(state, 'ready');
    assert.deepEqual(sync.rows(), rows);
  });
});
