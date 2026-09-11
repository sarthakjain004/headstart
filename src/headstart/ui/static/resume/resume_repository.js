/* Where a résumé is kept (ADR-0123, amended by ADR-0124).
 *
 * **This browser is the working copy, always.** Every word typed on the Résumé tab is written
 * here — to the Account's own machine — and the editor never reads a résumé from anywhere else.
 * That is not merely how it is built: it is what makes the account copy safe to add, because a
 * signed-out session, an outage or a deployment with no account store all leave the tab working
 * exactly as it did before there was one.
 *
 * **One thing has changed, and it is opt-in.** The header here used to say HeadStart's servers do
 * not store résumés, full stop. That is no longer true and must not be left standing: ADR-0124
 * accepted an account copy — one JSON file per document in the same private dataset the Profile
 * and the Saved sets live in — and `resume_sync.js` implements it. It is **off by default and
 * switched on for one résumé at a time**, in the Résumés popover, beside the sentence saying what
 * turning it on means. A résumé nobody switches on never leaves this browser. ADR-0041's rule for
 * the **Résumé** — the text pasted into the Profile tab for extraction, read once and discarded —
 * is a different object with a different lifetime and is untouched by any of it.
 *
 * ADR-0124's own header predicted the sync would be "a second implementation of six methods"
 * against this interface. It is not, and the reason is that ADR's own decision 3: HF is a sync
 * *target*, not a place the editor reads from, so making it a Repository would have put a Git
 * commit behind a keystroke. Note what it must NOT reuse either way — the debounce below exists
 * because localStorage is free to write, and a commit is not.
 *
 * The in-memory repository is not a stub for tests alone: it is what the editor falls back to when
 * localStorage is unavailable (private windows, storage disabled), which keeps the builder usable
 * for the session instead of failing at the first keystroke.
 *
 *   list()          -> [{id, name, layoutId, updatedAt}]  (newest first, no document parsed)
 *   get(id)         -> document | null
 *   save(doc)       -> {ok: true} | {ok: false, reason}
 *   remove(id)      -> void
 *   lastOpened()    -> id | null
 *   setLastOpened(id)
 *   durable         -> whether anything written here survives the tab closing
 */
(function (root) {
  'use strict';

  const INDEX = 'headstart.resumes.index';
  const DOC = 'headstart.resume.';
  const LAST = 'headstart.resumes.last';

  /** A summary row from a full document — what `list` returns without parsing every résumé. */
  /* `rev` rides along so the list can tell a row that is BEHIND the account's copy from one
     that is level with it, without parsing every document to find out (ADR-0124). Rows written
     before this existed carry no `rev` key at all, and a reader must treat that as "unknown"
     rather than as 0 — the next save of that document fills it in. */
  const summarise = doc => ({
    id: doc.id, name: doc.name, layoutId: doc.layoutId, updatedAt: doc.updatedAt,
    rev: doc.rev || 0,
  });

  const byNewest = (a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || ''));

  function LocalStorageRepository(storage) {
    this._s = storage;
    this.durable = true;
  }

  LocalStorageRepository.prototype._index = function () {
    try {
      const raw = this._s.getItem(INDEX);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      /* A corrupt index must not take the documents with it. Returning [] here loses the
         listing, not the résumés — each one is its own key and `get` still finds it. */
      return [];
    }
  };

  LocalStorageRepository.prototype._writeIndex = function (rows) {
    this._s.setItem(INDEX, JSON.stringify(rows.slice().sort(byNewest)));
  };

  LocalStorageRepository.prototype.list = function () {
    return this._index().slice().sort(byNewest);
  };

  LocalStorageRepository.prototype.get = function (id) {
    try {
      const raw = this._s.getItem(DOC + id);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  };

  LocalStorageRepository.prototype.save = function (doc) {
    try {
      this._s.setItem(DOC + doc.id, JSON.stringify(doc));
      const rows = this._index().filter(r => r.id !== doc.id);
      rows.push(summarise(doc));
      this._writeIndex(rows);
      return { ok: true };
    } catch (err) {
      /* Quota is the realistic failure and it is silent by default — the setItem throws, the
         keystroke that triggered it looks fine, and the work is gone at the next reload. The
         caller is told so the editor can say so. */
      const quota = err && /quota|exceeded/i.test(String(err.name) + String(err.message));
      return { ok: false, reason: quota ? 'quota' : 'unavailable', error: err };
    }
  };

  LocalStorageRepository.prototype.remove = function (id) {
    try { this._s.removeItem(DOC + id); } catch (err) { /* already gone */ }
    try { this._writeIndex(this._index().filter(r => r.id !== id)); } catch (err) { /* nothing to do */ }
  };

  LocalStorageRepository.prototype.lastOpened = function () {
    try { return this._s.getItem(LAST); } catch (err) { return null; }
  };
  LocalStorageRepository.prototype.setLastOpened = function (id) {
    try { this._s.setItem(LAST, id); } catch (err) { /* a lost bookmark is not worth a message */ }
  };

  function MemoryRepository() {
    this._docs = new Map();
    this._last = null;
    /* The editor reads this to warn that nothing is being kept. A repository that quietly
       forgot everything at the end of the session would be the worst of both worlds. */
    this.durable = false;
  }
  MemoryRepository.prototype.list = function () {
    return Array.from(this._docs.values()).map(summarise).sort(byNewest);
  };
  MemoryRepository.prototype.get = function (id) {
    const doc = this._docs.get(id);
    return doc ? JSON.parse(JSON.stringify(doc)) : null;
  };
  MemoryRepository.prototype.save = function (doc) {
    this._docs.set(doc.id, JSON.parse(JSON.stringify(doc)));
    return { ok: true };
  };
  MemoryRepository.prototype.remove = function (id) { this._docs.delete(id); };
  MemoryRepository.prototype.lastOpened = function () { return this._last; };
  MemoryRepository.prototype.setLastOpened = function (id) { this._last = id; };

  /** The repository this browser can actually offer. localStorage is *probed*, not assumed:
   *  Safari in private mode and any browser with site data blocked expose the object and throw
   *  on write, so feature-detecting the property would hand back a repository that fails later. */
  function detect(win) {
    const w = win || (typeof window !== 'undefined' ? window : null);
    try {
      const s = w && w.localStorage;
      if (!s) return new MemoryRepository();
      const probe = '__headstart_probe__';
      s.setItem(probe, '1');
      s.removeItem(probe);
      return new LocalStorageRepository(s);
    } catch (err) {
      return new MemoryRepository();
    }
  }

  root.ResumeRepository = { LocalStorageRepository, MemoryRepository, detect, INDEX, DOC, LAST };
})(typeof globalThis !== 'undefined' ? globalThis : this);
