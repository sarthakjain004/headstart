/* Layer 3 of the résumé builder (ADR-0123) — the WORDS, and the document that holds them.
 *
 * A Document is a Composite tree of nodes (structure, from Layer 1) plus a flat `content` map
 * keyed by node id. Content is kept OUT of the tree on purpose: switching Layout rebuilds how
 * every node is drawn and must not touch a single sentence, and a flat map makes that
 * separation impossible to violate by accident. It also makes the export of "just the words"
 * a map read rather than a tree walk.
 *
 * The mutation vocabulary is a set of Commands — named operations that produce a NEW document
 * rather than editing one in place. Undo is a bounded stack of prior documents, i.e. a memento,
 * not a stack of inverse operations: removing a subtree can only be undone by having kept it,
 * so inverses would have carried a snapshot anyway and would have been the same thing wearing
 * a harder-to-audit name.
 *
 * Browser script, no build step.
 */
(function (root) {
  'use strict';

  const Components = root.ResumeComponents;
  const SCHEMA = 1;

  /* JSON, not structuredClone: a Document is persisted as JSON, so cloning through JSON means
     what the undo stack holds and what the repository writes cannot diverge. A value that
     would not survive a save is dropped here, loudly and early, rather than at load. */
  const clone = d => JSON.parse(JSON.stringify(d));

  let seq = 0;
  const newId = () => 'n' + (++seq).toString(36) + '-' + Math.random().toString(36).slice(2, 7);

  /* ---- the tree ---------------------------------------------------------------------- */

  function node(type, opts) {
    return Object.assign({
      id: newId(),
      type,
      /* Which Layout slot a top-level node sits in. Nested nodes carry null — their place is
         their parent's. A slot the current Layout doesn't have falls back to its first slot at
         render time, so switching to a one-column Layout never hides a node. */
      slot: null,
      /* Everything drag and resize write. Sparse, and every key is optional: a Layout reads
         only the keys it honours and clamps them, so a free-canvas x/y survives a trip through
         a strict single-column Layout untouched and unused. */
      geometry: {},
      children: [],
    }, opts || {});
  }

  /** Depth-first walk. `fn(node, parent, index)`; returning false prunes that branch. */
  function walk(n, fn, parent, index) {
    if (fn(n, parent || null, index == null ? -1 : index) === false) return;
    n.children.forEach((c, i) => walk(c, fn, n, i));
  }

  function find(doc, id) {
    let hit = null;
    walk(doc.root, n => { if (n.id === id) { hit = n; return false; } });
    return hit;
  }

  function parentOf(doc, id) {
    let hit = null;
    walk(doc.root, (n, p) => { if (n.id === id) { hit = p; return false; } });
    return hit;
  }

  /** Every node in document order, root excluded. */
  function flatten(doc) {
    const out = [];
    walk(doc.root, n => { if (n !== doc.root) out.push(n); });
    return out;
  }

  /* ---- Builder ------------------------------------------------------------------------
     Fluent assembly, used by each Layout's starter document and by the tests. It exists
     because the alternative — hand-writing nested object literals with matching ids in the
     content map — is where the ids stop matching and nobody notices until a field renders
     blank. */

  function NodeBuilder(doc, parent) {
    this._doc = doc;
    this._parent = parent;
  }

  /** add(type, content?, fill?) — `fill` receives a builder scoped to the new node. */
  NodeBuilder.prototype.add = function (type, content, fill) {
    const spec = Components.get(type);
    if (!spec) throw new Error('ResumeDocument: unknown component type ' + type);
    const n = node(type);
    if (this._parent === this._doc.root) n.slot = 'main';
    this._parent.children.push(n);
    this._doc.content[n.id] = Object.assign(Components.blankContent(type), content || {});
    if (fill) fill(new NodeBuilder(this._doc, n));
    else for (const [type, seeded] of spec.seed) new NodeBuilder(this._doc, n).add(type, seeded);
    return this;
  };

  /** Put the block just added into a named Layout slot. Starters for multi-column layouts need
   *  this; without it they reached into `_doc.root.children` directly, and a starter that says
   *  "seeded into the narrow column" in a comment while writing 'main' is how one shipped. */
  NodeBuilder.prototype.into = function (slot) {
    const kids = this._parent.children;
    if (kids.length) kids[kids.length - 1].slot = slot;
    return this;
  };

  /** Position and size the block just added — for free-positioning layouts, whose starters would
   *  otherwise open as a pile in the corner. */
  NodeBuilder.prototype.placed = function (x, y, w, h) {
    const kids = this._parent.children;
    if (kids.length) kids[kids.length - 1].geometry = { x, y, w, h };
    return this;
  };

  /** Sugar for the two shapes every layout's starter uses. */
  NodeBuilder.prototype.section = function (title, fill) {
    return this.add('section', { title }, fill);
  };
  NodeBuilder.prototype.bullet = function (text, isSummary) {
    return this.add('bullet', { text, role: !!isSummary });
  };

  function Builder() {
    this._doc = {
      schema: SCHEMA,
      id: 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      name: 'Untitled résumé',
      layoutId: null,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      root: node('__root__'),
      content: {},
      /* Alternate wordings, per node: variants[nodeId][variantId] holds the fields that differ
         from the base in `content`. Sparse and partial — a variant that changes a bullet's text
         says nothing about its flags, so fixing the base still reaches every variant that never
         disagreed with it. */
      variants: {},
      /* One Tailoring is one job application's version of this résumé: which variant each node
         uses, and which nodes it leaves out. It stores only the differences, which is what makes
         "fix the typo once" work across twenty applications. */
      tailorings: [],
      activeTailoring: null,
      /* Layout token overrides the user has dialled in — Layer 2 values, stored per document
         so the same Layout can look slightly different in two of them. */
      theme: {},
    };
    this._top = new NodeBuilder(this._doc, this._doc.root);
  }
  Builder.prototype.named = function (name) { this._doc.name = name; return this; };
  Builder.prototype.usingLayout = function (id) { this._doc.layoutId = id; return this; };
  Builder.prototype.add = function () { this._top.add.apply(this._top, arguments); return this; };
  Builder.prototype.section = function () { this._top.section.apply(this._top, arguments); return this; };
  Builder.prototype.into = function (slot) { this._top.into(slot); return this; };
  Builder.prototype.placed = function () { this._top.placed.apply(this._top, arguments); return this; };
  Builder.prototype.build = function () {
    if (!this._doc.layoutId) throw new Error('ResumeDocument: a document needs a layout');
    return this._doc;
  };

  /* ---- resolution -------------------------------------------------------------------------
     The document as one Tailoring sees it: base words with that Tailoring's variants merged over
     them, and the nodes it leaves out pruned away. Everything downstream — render, rules, every
     export — runs on the resolved document, so a tailored résumé is checked and printed as the
     thing that will actually be sent, not as the master it came from.

     Pure, and identity when nothing is active, so the master path costs nothing. */

  function tailoringOf(doc, id) {
    const wanted = id === undefined ? doc.activeTailoring : id;
    return (doc.tailorings || []).find(t => t.id === wanted) || null;
  }

  function resolve(doc, id) {
    const tailoring = tailoringOf(doc, id);
    if (!tailoring) return doc;
    const out = clone(doc);
    /* The resolved document is what gets printed and downloaded, so it carries the version's own
       name — three tailored PDFs in a downloads folder all called "Lee Korelitz.pdf" is a real
       way to send the wrong one to the wrong employer. */
    out.name = (doc.name || 'Résumé') + ' — ' + tailoring.name;
    const hidden = new Set(tailoring.hidden || []);
    (function prune(n) {
      n.children = n.children.filter(c => !hidden.has(c.id));
      n.children.forEach(prune);
    })(out.root);
    for (const nodeId of Object.keys(tailoring.picks || {})) {
      const variant = (doc.variants || {})[nodeId];
      const fields = variant && variant[tailoring.picks[nodeId]];
      if (fields) out.content[nodeId] = Object.assign({}, doc.content[nodeId] || {}, fields);
    }
    return out;
  }

  /** The words one node shows under a Tailoring — base, with that Tailoring's variant over it. */
  function contentOf(doc, nodeId, id) {
    const tailoring = tailoringOf(doc, id);
    const base = doc.content[nodeId] || {};
    if (!tailoring) return base;
    const fields = ((doc.variants || {})[nodeId] || {})[(tailoring.picks || {})[nodeId]];
    return fields ? Object.assign({}, base, fields) : base;
  }

  /* ---- Commands -----------------------------------------------------------------------
     Each returns { name, apply(doc) -> doc }. `apply` receives a private clone and may edit it
     freely; it must not touch anything else. `name` is what the undo control shows, so it is
     written for a person ("Delete bullet"), not for a log. */

  const Commands = {
    rename: name => ({ name: 'Rename', apply: d => { d.name = name; return d; } }),

    setContent: (id, patch) => ({
      name: 'Edit text',
      apply: d => { d.content[id] = Object.assign(d.content[id] || {}, patch); return d; },
    }),

    setGeometry: (id, patch) => ({
      name: 'Resize',
      apply: d => {
        const n = find(d, id);
        if (n) n.geometry = Object.assign({}, n.geometry, patch);
        return d;
      },
    }),

    setTheme: patch => ({
      name: 'Restyle',
      apply: d => { d.theme = Object.assign({}, d.theme, patch); return d; },
    }),

    /* Switching Layout touches layoutId and nothing else — that invariant is this whole
       design's payoff, so it is one line here and a test in resume_document.test.js. */
    setLayout: layoutId => ({ name: 'Change layout', apply: d => { d.layoutId = layoutId; return d; } }),

    addNode: (parentId, type, index) => ({
      name: 'Add ' + ((Components.get(type) || {}).label || type).toLowerCase(),
      apply: d => {
        const parent = parentId ? find(d, parentId) : d.root;
        if (!parent) return d;
        const b = new NodeBuilder(d, parent);
        b.add(type);
        if (index != null) {
          const added = parent.children.pop();
          parent.children.splice(index, 0, added);
        }
        return d;
      },
    }),

    removeNode: id => ({
      name: 'Delete',
      apply: d => {
        const parent = parentOf(d, id);
        if (!parent) return d;
        const gone = parent.children.find(c => c.id === id);
        parent.children = parent.children.filter(c => c.id !== id);
        /* Drop the subtree's content too. Leaving it would grow the saved document without
           bound as sections are added and deleted, and would resurrect stale text if an id
           were ever reused. */
        if (gone) {
          walk(gone, n => {
            delete d.content[n.id];
            if (d.variants) delete d.variants[n.id];
            for (const t of d.tailorings || []) {
              if (t.picks) delete t.picks[n.id];
              if (t.hidden) t.hidden = t.hidden.filter(id => id !== n.id);
            }
          });
        }
        return d;
      },
    }),

    duplicateNode: id => ({
      name: 'Duplicate',
      apply: d => {
        const parent = parentOf(d, id);
        const original = find(d, id);
        if (!parent || !original) return d;
        const copy = clone(original);
        /* Fresh ids all the way down, and the content copied across to them — a duplicate that
           shared ids would edit both copies at once. */
        walk(copy, n => {
          const was = n.id;
          n.id = newId();
          d.content[n.id] = clone(d.content[was] || {});
        });
        parent.children.splice(parent.children.indexOf(original) + 1, 0, copy);
        return d;
      },
    }),

    /** Move a node to `index` within `newParentId` (or the root when null). */
    moveNode: (id, newParentId, index) => ({
      name: 'Move',
      apply: d => {
        const moving = find(d, id);
        const from = parentOf(d, id);
        const to = newParentId ? find(d, newParentId) : d.root;
        if (!moving || !from || !to) return d;
        /* Refuse to drop a node inside itself — the tree would detach from the root and the
           document would silently lose everything below the drag. */
        let inside = false;
        walk(moving, n => { if (n === to) inside = true; });
        if (inside) return d;
        if (to !== d.root && !Components.accepts(to.type, moving.type)) return d;

        from.children = from.children.filter(c => c.id !== id);
        const at = index == null ? to.children.length : Math.max(0, Math.min(index, to.children.length));
        to.children.splice(at, 0, moving);
        moving.slot = to === d.root ? (moving.slot || 'main') : null;
        return d;
      },
    }),

    /* ---- tailoring ---------------------------------------------------------------------- */

    addTailoring: (name, jobId) => ({
      name: 'Add version',
      apply: d => {
        const tailoring = { id: newId(), name: name || 'Untitled version', jobId: jobId || null,
          picks: {}, hidden: [], createdAt: new Date().toISOString() };
        (d.tailorings = d.tailorings || []).push(tailoring);
        d.activeTailoring = tailoring.id;
        return d;
      },
    }),

    renameTailoring: (id, name) => ({
      name: 'Rename version',
      apply: d => {
        const t = (d.tailorings || []).find(x => x.id === id);
        if (t) t.name = name;
        return d;
      },
    }),

    removeTailoring: id => ({
      name: 'Delete version',
      apply: d => {
        d.tailorings = (d.tailorings || []).filter(t => t.id !== id);
        if (d.activeTailoring === id) d.activeTailoring = null;
        /* The variants that version forked are dropped with it — no other Tailoring can be
           pointing at them, because a fork belongs to the Tailoring that made it. */
        for (const nodeId of Object.keys(d.variants || {})) {
          for (const variantId of Object.keys(d.variants[nodeId])) {
            const used = (d.tailorings || []).some(t => (t.picks || {})[nodeId] === variantId);
            if (!used) delete d.variants[nodeId][variantId];
          }
          if (!Object.keys(d.variants[nodeId]).length) delete d.variants[nodeId];
        }
        return d;
      },
    }),

    activateTailoring: id => ({
      name: id ? 'Switch version' : 'Back to the master',
      apply: d => { d.activeTailoring = id; return d; },
    }),

    /** Write words for one node under one Tailoring, forking a variant on the first edit.
     *  `tailoringId` null writes the base, which every Tailoring that has not disagreed will
     *  keep seeing — that is the whole point of storing differences rather than copies. */
    setContentFor: (nodeId, patch, tailoringId) => ({
      name: tailoringId ? 'Edit this version' : 'Edit text',
      apply: d => {
        if (!tailoringId) {
          d.content[nodeId] = Object.assign(d.content[nodeId] || {}, patch);
          return d;
        }
        const tailoring = (d.tailorings || []).find(t => t.id === tailoringId);
        if (!tailoring) return d;
        d.variants = d.variants || {};
        d.variants[nodeId] = d.variants[nodeId] || {};
        let variantId = (tailoring.picks || {})[nodeId];
        if (!variantId || !d.variants[nodeId][variantId]) {
          variantId = newId();
          d.variants[nodeId][variantId] = {};
          (tailoring.picks = tailoring.picks || {})[nodeId] = variantId;
        }
        Object.assign(d.variants[nodeId][variantId], patch);
        return d;
      },
    }),

    /** Drop this node's override, so the Tailoring goes back to the master's words for it. */
    clearVariant: (nodeId, tailoringId) => ({
      name: 'Use the master’s words',
      apply: d => {
        const tailoring = (d.tailorings || []).find(t => t.id === tailoringId);
        if (!tailoring || !tailoring.picks) return d;
        const variantId = tailoring.picks[nodeId];
        delete tailoring.picks[nodeId];
        if (variantId && d.variants && d.variants[nodeId]) {
          delete d.variants[nodeId][variantId];
          if (!Object.keys(d.variants[nodeId]).length) delete d.variants[nodeId];
        }
        return d;
      },
    }),

    /** Leave a node out of one Tailoring without deleting it from the résumé. */
    setHidden: (nodeId, hidden, tailoringId) => ({
      name: hidden ? 'Leave out of this version' : 'Put back in this version',
      apply: d => {
        const tailoring = (d.tailorings || []).find(t => t.id === tailoringId);
        if (!tailoring) return d;
        const set = new Set(tailoring.hidden || []);
        if (hidden) set.add(nodeId); else set.delete(nodeId);
        tailoring.hidden = Array.from(set);
        return d;
      },
    }),

    /** Put a top-level node in a named Layout slot (multi-column layouts). */
    setSlot: (id, slot) => ({
      name: 'Move to column',
      apply: d => {
        const n = find(d, id);
        if (n && d.root.children.includes(n)) n.slot = slot;
        return d;
      },
    }),
  };

  /* ---- Store --------------------------------------------------------------------------
     One observable document plus undo. Views subscribe; nothing re-renders itself. */

  const UNDO_LIMIT = 60;

  function Store(repository, options) {
    const opts = options || {};
    this._repo = repository;
    this._doc = null;
    this._undo = [];
    this._redo = [];
    this._subs = new Set();
    /* Persist is debounced because a keystroke is a command: typing a bullet would otherwise
       be one repository write per character. Injectable so tests can run it inline instead of
       waiting on a real timer. */
    this._delay = opts.saveDelay == null ? 400 : opts.saveDelay;
    this._schedule = opts.schedule || ((fn, ms) => setTimeout(fn, ms));
    this._cancel = opts.cancel || (h => clearTimeout(h));
    this._pending = null;
    /* Undo coalescing. Typing a bullet is one command per keystroke, and an undo stack with one
       entry per character is not an undo stack — pressing it forty times to remove a sentence is
       the same as not having one. Consecutive commands sharing a key, inside this window, extend
       the entry already on the stack instead of pushing another. */
    this._lastKey = null;
    this._lastAt = 0;
    this._coalesceMs = opts.coalesceMs == null ? 1200 : opts.coalesceMs;
    this._now = opts.now || (() => Date.now());
  }

  Store.prototype.subscribe = function (fn) {
    this._subs.add(fn);
    return () => this._subs.delete(fn);
  };
  Store.prototype._emit = function () {
    for (const fn of this._subs) fn(this._doc);
  };
  Store.prototype.get = function () { return this._doc; };

  /** Adopt a document without touching the undo stack — used by load and create. */
  Store.prototype.adopt = function (doc) {
    this._doc = doc;
    this._undo = [];
    this._redo = [];
    this._lastKey = null;
    this._emit();
    return doc;
  };

  /** `coalesceKey` groups a run of related commands — typically one field of one node — into a
   *  single undo step. Pass null (the default) for anything structural. */
  Store.prototype.dispatch = function (command, coalesceKey) {
    if (!this._doc) return null;
    const before = this._doc;
    const next = command.apply(clone(before));
    next.updatedAt = new Date().toISOString();
    const at = this._now();
    const merges = coalesceKey != null && coalesceKey === this._lastKey &&
      (at - this._lastAt) < this._coalesceMs && this._undo.length > 0;
    if (!merges) {
      this._undo.push({ label: command.name, doc: before });
      if (this._undo.length > UNDO_LIMIT) this._undo.shift();
    }
    this._lastKey = coalesceKey == null ? null : coalesceKey;
    this._lastAt = at;
    this._redo.length = 0;
    this._doc = next;
    this._emit();
    this._queueSave();
    return next;
  };

  Store.prototype.canUndo = function () { return this._undo.length > 0; };
  Store.prototype.canRedo = function () { return this._redo.length > 0; };
  Store.prototype.undoLabel = function () {
    return this._undo.length ? this._undo[this._undo.length - 1].label : null;
  };

  Store.prototype.undo = function () {
    if (!this._undo.length) return null;
    const step = this._undo.pop();
    this._lastKey = null;   // a new run starts after an undo, never merging into the old one
    this._redo.push({ label: step.label, doc: this._doc });
    this._doc = step.doc;
    this._emit();
    this._queueSave();
    return this._doc;
  };

  Store.prototype.redo = function () {
    if (!this._redo.length) return null;
    const step = this._redo.pop();
    this._lastKey = null;
    this._undo.push({ label: step.label, doc: this._doc });
    this._doc = step.doc;
    this._emit();
    this._queueSave();
    return this._doc;
  };

  Store.prototype._queueSave = function () {
    if (!this._repo) return;
    if (this._pending != null) this._cancel(this._pending);
    this._pending = this._schedule(() => { this._pending = null; this.flush(); }, this._delay);
  };

  /** Write now. Called on tab-hide and before export, so nothing in flight is lost. */
  Store.prototype.flush = function () {
    if (this._pending != null) { this._cancel(this._pending); this._pending = null; }
    if (this._repo && this._doc) this._repo.save(this._doc);
  };

  root.ResumeDocument = {
    SCHEMA, Commands, Store,
    builder: () => new Builder(),
    node, walk, find, parentOf, flatten, clone, resolve, contentOf, tailoringOf,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
