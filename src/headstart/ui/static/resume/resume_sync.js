/* The account copy of a Résumé document (ADR-0124, ADR-0131).
 *
 * The browser is the working copy and this is a SYNC TARGET, which is the whole shape of this
 * module. It is deliberately not a second `ResumeRepository`: the repository is what the editor
 * reads and writes on every keystroke, and an implementation of it that spoke to the network
 * would put a Git commit behind a keypress. ADR-0124's own header predicted "a second
 * implementation of six methods"; its decision 3 (offline-first, the browser is the source of
 * edits) is what ruled that out, and this is the shape that decision actually asks for.
 *
 * WRITE CADENCE — the part that must not be got wrong. `resume_document.js`'s Store debounces
 * localStorage by 400 ms because localStorage is free. A push here is a commit on one repo head
 * shared with every other Account, so it happens on three coarse events and nothing else:
 *
 *   1. an explicit ask     — the switch going on, or "Save now"
 *   2. the tab going away  — visibilitychange -> hidden, and switching document
 *   3. a slow heartbeat    — at most one push every MIN_INTERVAL while editing
 *
 * All three are gated on `_dirty`: a document nobody has changed is never pushed, so alt-tabbing
 * ten times costs nothing, and opening a document commits nothing at all. Timers are only ever
 * scheduled by 3; 1 and 2 push directly.
 *
 * MIN_INTERVAL is the floor BETWEEN pushes, not a delay on the first one. After a quiet spell
 * the first completed local write goes up promptly, and everything typed behind it waits out the
 * floor — so a sliding window of MIN_INTERVAL still never holds two pushes, which is the claim
 * above. It is written this way deliberately: the alternative leaves the first few minutes of
 * writing on one machine while the switch says the résumé is on the Account, and the cost of
 * being prompt is one commit per editing session, not one per keystroke or per page load.
 *
 * CONFLICTS — a push carries `rev + 1` and the server accepts it only if that is exactly one
 * past what it holds. A refusal is never resolved by discarding: the local document is saved
 * beside the account's as "… (this device)", the account's copy is adopted under the original
 * id, and the user is told. Whole-document granularity, so two devices editing different bullets
 * still conflict — that limit is ADR-0124's and it is stated in the product, not hidden here.
 *
 * DEGRADING — everything is best-effort. A 401 (signed out), a 503 (this deployment keeps no
 * account copies), an offline machine or a Hub outage all leave the tab exactly as ADR-0123
 * shipped it: the browser copy is authoritative and nothing blocks on the network.
 *
 *   status()               -> {state, at, error}   state: unknown|ready|signed-out|off
 *   refresh()              -> Promise<state>       probe + list what the Account holds
 *   rows()                 -> [{id, name, layoutId, updatedAt, rev}]  (last refresh, cached)
 *   note(doc)                                      a local save happened; may schedule a push
 *   flush(reason)          -> Promise              push now if dirty
 *   setEnabled(doc, on)    -> Promise              the per-document opt-in
 *   pull(id)               -> Promise<doc|null>    bring an account copy into this browser
 *   forget(id)             -> Promise              take one off the Account
 */
(function (root) {
  'use strict';

  /* Three minutes. ADR-0124 says "at most once every few minutes while editing" and this is the
     number that phrase became. It is a floor between heartbeat pushes, not a timer that fires on
     its own: an idle document reaches it and does nothing, because nothing is dirty. */
  const MIN_INTERVAL = 180000;

  /** What `list` and the popover need from a document, without its words. Same projection the
   *  server makes, so a local row and an account row are the same shape. */
  const summarise = doc => ({
    id: doc.id, name: doc.name, layoutId: doc.layoutId, updatedAt: doc.updatedAt,
    rev: doc.rev || 0,
  });

  function Sync(options) {
    const opts = options || {};
    this._repo = opts.repository || null;
    /* Injected so the tests drive real code with a fake wire, and so a page with no `fetch`
       (the node harness) degrades to "off" instead of throwing on load. */
    this._request = opts.request ||
      (typeof fetch === 'function' ? (url, init) => fetch(url, init) : null);
    this._schedule = opts.schedule || ((fn, ms) => setTimeout(fn, ms));
    this._cancel = opts.cancel || (h => clearTimeout(h));
    this._now = opts.now || (() => Date.now());
    /* Told when anything the tab paints has changed, and when the user needs a sentence. */
    this._onChange = opts.onChange || function () {};
    this._onMessage = opts.onMessage || function () {};
    /* Handed the document the tab should now be showing — the conflict path replaces the open
       document with the Account's copy, and the editor is the only thing that can adopt it. */
    this._onAdopt = opts.onAdopt || function () {};
    /* The document the editor currently has open. A push is asynchronous and the user keeps
       typing through it, so the payload that comes back is stale by definition — writing the
       answered revision onto THAT object and saving it would put the pre-push words back into
       storage. Everything that lands after a round trip resolves the document again through
       `_current` first. */
    this._live = opts.live || (() => null);

    this._state = 'unknown';
    this._rows = [];
    this._dirty = null;      // the document that has changed since the last successful push
    this._timer = null;
    this._lastPush = 0;      // ms, 0 = never
    this._inflight = null;   // one push at a time; a second would race itself
    this._error = '';
  }

  Sync.prototype.status = function () {
    return { state: this._state, at: this._lastPush || null, error: this._error };
  };
  Sync.prototype.rows = function () { return this._rows.slice(); };

  /** GET, POST or DELETE against the account store. Answers a small envelope rather than
   *  throwing: every caller here treats "the network did not work" as a state, not an error. */
  Sync.prototype._call = function (url, init) {
    if (!this._request) { this._state = 'off'; return Promise.resolve({ state: 'off' }); }
    return this._request(url, init).then(response => {
      if (response.status === 401) { this._state = 'signed-out'; return { state: 'signed-out' }; }
      if (response.status === 503) { this._state = 'off'; return { state: 'off' }; }
      return response.json().then(
        body => ({ state: 'ready', status: response.status, body }),
        () => ({ state: 'ready', status: response.status, body: null })
      ).then(result => {
        /* A reachable endpoint means the feature is configured and this session is signed in,
           whatever this particular request answered — a 409 is a working sync, not a broken one. */
        this._state = 'ready';
        return result;
      });
    }, () => ({ state: 'unreachable' }));
  };

  Sync.prototype.refresh = function () {
    const self = this;
    return this._call('/resumes').then(result => {
      self._rows = result.state === 'ready' && Array.isArray(result.body) ? result.body : [];
      if (result.state === 'unreachable') self._state = 'unknown';
      self._onChange();
      return self._state;
    });
  };

  Sync.prototype.pull = function (id) {
    return this._call('/resumes/' + encodeURIComponent(id)).then(result =>
      (result.state === 'ready' && result.status === 200 && result.body) || null);
  };

  /** Take one document off the Account. Answers whether it actually went — a caller that
   *  reports "removed" on a request that 401'd would be the same lie as a switch that reads on
   *  while nothing is stored. */
  Sync.prototype.forget = function (id) {
    const self = this;
    return this._call('/resumes/' + encodeURIComponent(id), { method: 'DELETE' }).then(result => {
      // 404 counts as gone: the record is not there, which is what was asked for.
      const gone = result.state === 'ready' && (result.status === 200 || result.status === 404);
      if (gone) { self._rows = self._rows.filter(r => r.id !== id); }
      /* No repaint from in here. The caller knows when the state it is about to paint is
         settled, and this does not: repainting mid-flight redrew the switch from a `doc.sync`
         that was still true, so unticking it visibly snapped back on before turning off. */
      return gone;
    });
  };

  /** The per-document opt-in. ON pushes immediately, so the switch means something the moment
   *  it is flipped; OFF takes the copy off the Account rather than merely stopping future
   *  pushes — leaving a résumé there after being told to stop is the same defect as never
   *  having asked. */
  Sync.prototype.setEnabled = function (doc, on) {
    const self = this;
    if (on) {
      doc.sync = true;
      this._save(doc);
      return this._push(doc, 'switched on');
    }
    /* OFF only takes effect once the account copy is REALLY gone, and the order matters. An
       earlier version turned the switch off, cleared `rev` and announced "Removed" before the
       request answered — so a delete that 401'd left the résumé on the Account with the product
       saying it was not, and the next re-enable pushed `rev 1` against a stored `rev N`. The
       user's own résumé then conflicted with itself and was duplicated as "… (this device)". */
    return this.forget(doc.id).then(gone => {
      if (!gone) {
        self._onMessage('Still on your account — that did not go through.', true);
        self._onChange();
        return false;
      }
      doc.sync = false;
      doc.rev = 0;   // safe only now: there is nothing stored for the next push to be behind
      self._save(doc);
      self._dirty = null;
      self._clearTimer();
      self._onMessage('Removed from your account.');
      self._onChange();
      return true;
    });
  };

  /** A local save happened. Cheap on purpose — it is called on every keystroke's save, and all
   *  it does is remember the document and make sure a heartbeat is pending. */
  Sync.prototype.note = function (doc) {
    if (!doc || !doc.sync) return;
    /* Switching document while another is dirty would otherwise push the WRONG document at the
       next heartbeat — the trap `Store.adopt` already documents one level down. */
    if (this._dirty && this._dirty.id !== doc.id) this.flush('switched document');
    this._dirty = doc;
    this._arm();
  };

  Sync.prototype._arm = function () {
    if (this._timer != null) return;
    const wait = Math.max(0, this._lastPush + MIN_INTERVAL - this._now());
    const self = this;
    this._timer = this._schedule(() => {
      self._timer = null;
      self.flush('heartbeat');
    }, wait);
  };

  Sync.prototype._clearTimer = function () {
    if (this._timer != null) { this._cancel(this._timer); this._timer = null; }
  };

  /** Push now, if there is anything to push. This is what the explicit save and tab-hide call —
   *  neither waits for the interval, because both are the user's own coarse event. */
  Sync.prototype.flush = function (reason) {
    this._clearTimer();
    const doc = this._dirty;
    if (!doc || !doc.sync) return Promise.resolve(null);
    this._dirty = null;
    return this._push(doc, reason || 'save');
  };

  Sync.prototype._push = function (doc, reason) {
    const self = this;
    if (this._inflight) {
      /* One at a time: two pushes of one document would race their own revisions. The second is
         not dropped — it becomes the dirty document again and the heartbeat is re-armed, so it
         goes out with a `rev` that has by then been answered for. Re-arming is the part that is
         easy to miss: `flush` cleared the timer on its way in, so without this line a document
         edited during a push would sit unpushed until the tab was hidden. */
      this._dirty = doc;
      this._arm();
      return this._inflight;
    }
    const candidate = Object.assign({}, doc, { rev: (doc.rev || 0) + 1 });
    this._inflight = this._call('/resumes/' + encodeURIComponent(doc.id), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(candidate),
    }).then(result => {
      self._inflight = null;
      return self._settle(doc, candidate, result, reason);
    }, () => { self._inflight = null; self._dirty = doc; return null; });
    return this._inflight;
  };

  Sync.prototype._settle = function (doc, candidate, result, reason) {
    this._error = '';
    if (result.state === 'signed-out') {
      /* Not an error and not a retry loop: the session expired, the browser copy is untouched,
         and the next explicit save can try again. Said out loud because a switch that reads
         "on" while nothing is being stored is the dishonest state this must never sit in. */
      this._dirty = doc;
      this._error = 'signed out';
      this._onMessage('Signed out — saved in this browser only.', true);
      this._onChange();
      return null;
    }
    if (result.state === 'off' || result.state === 'unreachable') {
      this._dirty = doc;   // try again at the next coarse event; nothing is lost meanwhile
      this._onChange();
      return null;
    }
    if (result.status === 409) {
      return this._conflict(doc, (result.body && result.body.stored) || null);
    }
    if (result.status !== 200) {
      this._dirty = doc;
      this._error = (result.body && result.body.error) || 'your account refused it';
      this._onMessage('Not saved to your account — saved in this browser.', true);
      this._onChange();
      return null;
    }
    /* Only NOW is the revision the account's — writing `rev` optimistically would leave a
       failed push claiming a revision the server never saw, and every later push refused. */
    const mine = this._current(doc);
    mine.rev = (result.body && result.body.rev) || candidate.rev;
    this._save(mine);
    this._lastPush = this._now();
    this._rows = this._rows.filter(r => r.id !== mine.id).concat([summarise(mine)]);
    this._onChange();
    if (reason === 'switched on' || reason === 'save') this._onMessage('Saved to your account.');
    return doc;
  };

  /** Refused: the account copy moved on without this browser. Keep BOTH — the account's copy
   *  under the original id, this browser's beside it under a new one. Silently picking a winner
   *  is how an afternoon's work disappears (ADR-0124 decision 4). */
  Sync.prototype._conflict = function (doc, stored) {
    if (!stored || !stored.root) {
      /* The server refused but told us nothing usable. Stop pushing rather than guess: the
         browser copy is intact and the user is told to look at it. */
      this._error = 'conflict';
      this._onMessage('Edited elsewhere — nothing here was overwritten.', true);
      this._onChange();
      return null;
    }
    const mine = JSON.parse(JSON.stringify(this._current(doc)));
    mine.id = root.ResumeDocument.newDocumentId();
    mine.name = (doc.name || 'Untitled résumé') + ' (this device)';
    mine.sync = false;   // the copy is this browser's; it does not race for the same slot
    mine.rev = 0;
    this._save(mine);
    this._save(stored);
    this._rows = this._rows.filter(r => r.id !== stored.id).concat([summarise(stored)]);
    /* The document about to be open IS the account copy, byte for byte, so the status line must
       say so. Leaving "not saved — conflict" up would be describing the copy that just moved out
       of the way; the sticky message below is what reports the conflict itself. */
    this._error = '';
    this._lastPush = this._now();
    /* Short on purpose. The bar is a one-line status strip beside Download, and a paragraph in
       it reflows the whole band; the explanation belongs in the Résumés list, which now holds
       exactly two rows — the account's, and this device's with its name saying so. */
    this._onMessage('Edited elsewhere — both copies kept, see Résumés.', true);
    this._onAdopt(stored, mine);
    this._onChange();
    return null;
  };

  /** This browser's document with that id, as it stands NOW: the open one if it is that one,
   *  otherwise whatever storage holds, and only as a last resort the copy that was pushed. */
  Sync.prototype._current = function (doc) {
    const open = this._live();
    if (open && open.id === doc.id) return open;
    return (this._repo && this._repo.get(doc.id)) || doc;
  };

  Sync.prototype._save = function (doc) {
    if (this._repo) this._repo.save(doc);
  };

  root.ResumeSync = {
    create: options => new Sync(options),
    MIN_INTERVAL,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
