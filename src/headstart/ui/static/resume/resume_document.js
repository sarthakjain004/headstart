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

  /** A DOCUMENT's own id — `newId` above mints NODE ids, and the two are not interchangeable:
   *  node ids are woven into HTML attributes and `querySelector`, a document id is a path
   *  segment in the account store. Named apart for that reason (the near-homograph rule in
   *  CLAUDE.md §3); a file that declares both must never let one stand in for the other.
   *
   *  Exported because it is no longer only the Builder's: the account sync mints one for the
   *  copy it keeps aside after a conflict (ADR-0124 decision 4), and `store.py`'s `_RESUME_ID`
   *  is this exact shape spelled as a regex on the far side of the wire. */
  const newDocumentId = () =>
    'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  function Builder() {
    this._doc = {
      schema: SCHEMA,
      id: newDocumentId(),
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
      /* Account sync (ADR-0124), and both fields are OFF-by-default facts rather than state the
         editor maintains. `sync` is the opt-in: false until the Account switches it on for this
         particular résumé, because storing one reverses a promise ADR-0041 made and that is not
         something to inherit silently. `rev` is what the store's conflict check compares — the
         revision the account copy is at, 0 while there is no account copy. Neither is content:
         no Command touches them and they never enter the undo stack. */
      sync: false,
      rev: 0,
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

  /** Every node left out of what `id` would print: the document's own set, plus that Tailoring's.
   *
   *  TWO LAYERS, one meaning. A node in `doc.hidden` is kept in the résumé but off every version
   *  of it — the answer to "I have five projects and this one is for a different kind of job".
   *  A node in `tailoring.hidden` is off THAT version only. They compose the way the rest of the
   *  model composes: a Tailoring is a difference from the master, so a block the master does not
   *  print is not printed by a difference from it either. */
  function hiddenIn(doc, tailoring) {
    return new Set((doc.hidden || []).concat(tailoring ? tailoring.hidden || [] : []));
  }

  /** Whether one node prints, as `id` sees it. The editor's tick reads this rather than either
   *  list, so the control cannot disagree with the page. */
  function isHidden(doc, nodeId, id) {
    return hiddenIn(doc, tailoringOf(doc, id)).has(nodeId);
  }

  function resolve(doc, id) {
    const tailoring = tailoringOf(doc, id);
    const hidden = hiddenIn(doc, tailoring);
    /* Identity while nothing is active AND nothing is left out, which is the common case and the
       one the master path is meant to cost nothing. */
    if (!tailoring && !hidden.size) return doc;
    const out = clone(doc);
    /* The resolved document is what gets printed and downloaded, so it carries the version's own
       name — three tailored PDFs in a downloads folder all called "Lee Korelitz.pdf" is a real
       way to send the wrong one to the wrong employer. */
    if (tailoring) out.name = (doc.name || 'Résumé') + ' — ' + tailoring.name;
    (function prune(n) {
      n.children = n.children.filter(c => !hidden.has(c.id));
      n.children.forEach(prune);
    })(out.root);
    for (const nodeId of Object.keys((tailoring && tailoring.picks) || {})) {
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

    /* Switching Layout touches layoutId and the Layer-2 facts the incoming layout needs — never
       a word of Content, which is this whole design's payoff and is pinned in
       resume_document.test.js. `prepare` is the incoming Layout's own `adopt`, handed in by the
       caller: Layer 3 runs it without knowing what a Layout is. */
    setLayout: (layoutId, prepare) => ({
      name: 'Change layout',
      apply: d => {
        d.layoutId = layoutId;
        if (typeof prepare === 'function') prepare(d);
        return d;
      },
    }),

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
            if (d.hidden) d.hidden = d.hidden.filter(id => id !== n.id);
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
        /* Whether each copied node prints, carried over with its words. A duplicate of a block
           the résumé leaves out is another block the résumé leaves out — "duplicate" means
           another one like this, and `like this` includes off. Collected before the ids are
           reissued below, and applied after. */
        const wasHidden = new Set(d.hidden || []);
        const carry = [];
        /* Fresh ids all the way down, and the content copied across to them — a duplicate that
           shared ids would edit both copies at once.
           Variants come too: without this, duplicating a block that a version had reworded gave
           a copy showing the MASTER's words, with nothing to say the tailoring had been dropped.
           Each Tailoring that had a pick for the original gets one for the copy. */
        walk(copy, n => {
          const was = n.id;
          n.id = newId();
          d.content[n.id] = clone(d.content[was] || {});
          if (wasHidden.has(was)) carry.push(n.id);
          for (const tailoring of d.tailorings || []) {
            const picked = (tailoring.picks || {})[was];
            const fields = picked && ((d.variants || {})[was] || {})[picked];
            if (!fields) continue;
            const variantId = newId();
            d.variants = d.variants || {};
            d.variants[n.id] = d.variants[n.id] || {};
            d.variants[n.id][variantId] = clone(fields);
            (tailoring.picks = tailoring.picks || {})[n.id] = variantId;
          }
        });
        if (carry.length) d.hidden = (d.hidden || []).concat(carry);
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
        /* The root answers through `acceptsAtRoot`, never by being exempted from the check:
           exempting it let a bullet be dropped above the header, which put a stray <li>
           outside any <ul> on the page and a bullet ahead of the name in the plain-text
           export. Same rule the add menu and the drop points read (resume_components.js). */
        const legal = to === d.root
          ? Components.acceptsAtRoot(moving.type)
          : Components.accepts(to.type, moving.type);
        if (!legal) return d;

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

    /** Which sheet this résumé is printed on. A plain lookup key — Layer 2 turns it into
     *  geometry, and an unreadable one falls back there rather than being validated here.
     *
     *  `scale` re-fits blocks that were PLACED by hand — `{ x, y }` multipliers, one per axis,
     *  the caller derives from the two usable areas. A box carries inches measured against the
     *  sheet it was placed on, so changing the sheet moves the margin out from under it: a fresh
     *  free-canvas document switched from Letter to A4 reported two "Runs off the right-hand
     *  edge" warnings about blocks nobody had touched, and the warnings were right — 4.7 + 2.2
     *  is 6.9in on a 6.67in measure. Scaling rather than clamping because it keeps the
     *  composition, and because the trip back returns the inches rather than leaving a permanent
     *  0.23in gutter down the right of a Letter page. Each hop rounds to the hundredth, so that
     *  is reversibility to a hundredth of an inch and not an identity — measured on the
     *  free-canvas starter's four blocks, where Letter → A4 → Letter comes back exactly.
     *
     *  Whether to scale at all is the CALLER's call, not this command's — only a Layout that
     *  grants `resize: ['box']` has blocks placed in inches, and Layer 1 does not read caps.
     *  Omit it and the sheet changes alone, which is what every flow layout wants. */
    setPaper: (id, scale) => ({
      name: 'Change paper size',
      apply: d => {
        d.paper = id;
        if (!scale) return d;
        for (const n of d.root.children) {
          const g = n.geometry;
          if (!g) continue;
          for (const [key, factor] of [['x', scale.x], ['w', scale.x], ['y', scale.y], ['h', scale.y]]) {
            if (g[key] != null) g[key] = Math.round(g[key] * factor * 100) / 100;
          }
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

    /** Leave a node out of what is on screen, without deleting it from the résumé.
     *
     *  ONE command for one control. Which list it writes is which layer the user is editing —
     *  the named Tailoring's when a version is active, the document's own otherwise — because
     *  the tick beside a block means the same thing in both places: "print this". Splitting it
     *  into two commands would have put two controls that look identical next to each other and
     *  left the user to work out which layer each one reached. */
    setHidden: (nodeId, hidden, tailoringId) => ({
      name: hidden ? 'Leave out of the résumé' : 'Put back in the résumé',
      apply: d => {
        const tailoring = tailoringId ? (d.tailorings || []).find(t => t.id === tailoringId) : null;
        if (tailoringId && !tailoring) return d;
        const holder = tailoring || d;
        const set = new Set(holder.hidden || []);
        if (hidden) set.add(nodeId); else set.delete(nodeId);
        holder.hidden = Array.from(set);
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
    /* Called when a write is REFUSED — a full quota, storage switched off mid-session. The
       repository already reports this; without a caller it went nowhere, and the first the user
       knew of it was an empty résumé after a reload. */
    this._onError = opts.onError || null;
    /* Called after a write the repository ACCEPTED. The account sync (ADR-0124) hangs off this
       rather than off `subscribe`, and the difference matters: `subscribe` also fires on
       `adopt`, which changes no words at all, so a sync driven by it would push a document
       nobody had edited every time somebody opened one. This fires only where the browser copy
       actually changed, and no more often than the debounce below allows. */
    this._onSaved = opts.onSaved || null;
  }

  Store.prototype.subscribe = function (fn) {
    this._subs.add(fn);
    return () => this._subs.delete(fn);
  };
  Store.prototype._emit = function () {
    for (const fn of this._subs) fn(this._doc);
  };
  Store.prototype.get = function () { return this._doc; };

  /** Adopt a document without touching the undo stack — used by load and create.
   *
   *  Writes the OUTGOING document first. A save is debounced by 400 ms, so opening another
   *  résumé within that window used to leave a queued flush pointing at `this._doc` — which by
   *  then was the new document. The pending edit was not written late; it was written to the
   *  wrong file and lost, silently. */
  Store.prototype.adopt = function (doc) {
    if (this._pending != null && this._doc) this.flush();
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

  /** Write now. Called on tab-hide, on switching documents and before export, so nothing in
   *  flight is lost. Returns the repository's own answer, and reports a refusal. */
  Store.prototype.flush = function () {
    if (this._pending != null) { this._cancel(this._pending); this._pending = null; }
    if (!this._repo || !this._doc) return { ok: true };
    const result = this._repo.save(this._doc) || { ok: true };
    if (!result.ok && this._onError) this._onError(result);
    if (result.ok && this._onSaved) this._onSaved(this._doc);
    return result;
  };

  root.ResumeDocument = {
    SCHEMA, Commands, Store, newDocumentId,
    builder: () => new Builder(),
    node, walk, find, parentOf, flatten, clone, resolve, contentOf, tailoringOf, isHidden,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
