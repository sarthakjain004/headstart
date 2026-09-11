/* The Résumé tab's editor (ADR-0123) — the only file here that knows about the DOM.
 *
 * It owns no model of its own. It reads the three layers, dispatches Commands at the store, and
 * re-paints from whatever comes back; every user gesture is a Command, which is why undo works
 * on drags and resizes and not only on typing. The interaction rules come from the Layout's
 * capability contract rather than from anything here: which handles a node gets, whether a drag
 * reorders or repositions, and how far a resize may go are all read from `layout.caps` and
 * `Layouts.boundsFor(layout, doc)` — so a Layout added later gets the right affordances without
 * this file changing, and a document on A4 is bounded by A4 rather than by the Letter the Layout
 * declares (ADR-0132).
 */
(function (root) {
  'use strict';

  const Components = root.ResumeComponents;
  const Doc = root.ResumeDocument;
  const Layouts = root.ResumeLayouts;
  const Repo = root.ResumeRepository;
  const Export = root.ResumeExport;
  const Decorators = root.ResumeDecorators;
  const AccountSync = root.ResumeSync;
  const Cmd = Doc.Commands;
  const esc = Layouts.esc;

  const el = id => document.getElementById(id);

  let store = null;
  let repository = null;
  /* The account copy (ADR-0124). Null on a deployment that keeps none — every use is guarded,
     because the tab must work identically with no account behind it. */
  let sync = null;
  let selectedId = null;
  let zoom = 1;
  let booted = false;
  let savedTimer = null;
  /* The terms the Keywords pane last checked. They decorate the preview so the answer to "is this
     keyword near the top" is visible on the page, not just tallied beside the form. */
  let keywordTerms = [];
  /* Which rows of the document accordion are open. Node ids, plus the synthetic key `geo:<id>`
     for a row's own size-and-spacing disclosure. Held here rather than in the DOM because the
     form is rebuilt from innerHTML on every change, which would throw away any state
     living on the elements themselves. */
  const expanded = new Set();
  /* Whether the tab opened this document because the browser held nothing at all. That is the
     only moment the first-run card is honest — "this is an example, make it yours" is wrong
     advice for the résumé somebody wrote yesterday — and no field on the document records it. */
  let firstRun = false;

  const doc = () => store && store.get();
  /* What the reader sees: the master, or the active version with its words merged in and its
     left-out blocks pruned. Rendering, the rule panel and every export except the JSON backup
     run on this, so a tailored résumé is checked and printed as the thing that will be sent. */
  const view = () => { const d = doc(); return d ? Doc.resolve(d) : null; };
  const activeTailoring = () => { const d = doc(); return d ? d.activeTailoring : null; };
  const layout = () => { const d = doc(); return d ? Layouts.get(d.layoutId) : null; };

  /* ---- painting ------------------------------------------------------------------------ */

  function paint() {
    const d = doc();
    const lay = layout();
    if (!d || !lay) return;
    const paper = el('rb-paper');

    /* The sheet the DOCUMENT chose. Everything below measures against it, so a résumé set to A4
       previews, breaks and prints on A4 rather than on the Letter every layout declares. */
    const page = Layouts.pageFor(lay, d);
    /* The whole sheet, margins included, rather than just the text column. Two reasons, and the
       second one is not cosmetic: it looks like the page that comes out of the printer, and it
       gives the drag handles somewhere to sit. Handles hang off a block's left edge; with the
       paper cropped to the text column they landed outside it and were clipped away by the
       scrolling wrapper — present in the DOM, impossible to grab. */
    paper.style.width = page.width + page.unit;
    /* At least one sheet, and as many as the content needs — `minHeight` rather than `height`,
       so a three-page résumé is three pages of paper with the cuts drawn on it. */
    paper.style.minHeight = page.height + page.unit;
    paper.style.padding = page.margin + page.unit;
    paper.style.transform = 'scale(' + zoom + ')';
    /* The wrapper has to reserve the scaled height itself: `transform` does not affect layout,
       so without this the page overlaps whatever follows it at any zoom above 100%. */
    const wrap = paper.parentElement;
    wrap.style.height = (paper.offsetHeight * zoom) + 'px';

    /* One <style> element per layout+theme, replaced rather than appended — the preview and the
       download are the same stylesheet (renderStandalone emits it too), so a rule that only
       existed on screen could not happen. */
    let sheet = el('rb-sheet');
    if (!sheet) {
      sheet = document.createElement('style');
      sheet.id = 'rb-sheet';
      document.head.appendChild(sheet);
    }
    sheet.textContent = lay.css(Layouts.themeFor(lay, d), '#rb-paper .rb-doc');

    const shown = view();
    /* Decorated for the preview only. Exports resolve the layout from the registry by id, so a
       downloaded résumé never carries a highlight or a version marker. */
    const dressed = Decorators.compose(lay, [
      Decorators.highlight(keywordTerms),
      Decorators.tailored(d),
    ]);
    paper.innerHTML = Layouts.renderDocument(dressed, shown);
    decorate(paper, lay, shown);
    paintPageBreaks(paper, page);

    el('rb-name').value = d.name || '';
    versionPaint();
    el('rb-undo').disabled = !store.canUndo();
    el('rb-redo').disabled = !store.canRedo();
    /* The strip under the page says what is being previewed and how big, and nothing else. The
       layout picker, the sheet and the layout's own summary line moved into the Design pane and
       the gallery — they are properties of the document, and putting them above the page made a
       second band of chrome out of choices nobody makes twice in a session. */
    el('rb-template-name').textContent = lay.label;
    el('rb-zoom-read').textContent = Math.round(zoom * 100) + '%';
    if (templatesOpen) templatesPaint();
    miniPaint();

    panesPaint();
    badgePaint();
  }

  /** Add the editor's own furniture to the rendered page: a selection outline, a drag chip and
   *  whichever resize handles this layout grants this node. None of it survives into an export,
   *  which re-renders from the document rather than scraping the DOM. */
  function decorate(paper, lay, d) {
    paper.querySelectorAll('[data-node]').forEach(node => {
      const id = node.dataset.node;
      const spec = Components.get(node.dataset.type);
      const model = Doc.find(d, id);
      if (!spec || !model) return;
      node.classList.add('rb-hit');
      if (id === selectedId) node.classList.add('rb-selected');

      const canMove = (lay.caps.mode === 'free') || (lay.caps.reorder && spec.caps.reorder);
      if (canMove) node.appendChild(handle('move', '⠿', 'Drag to ' +
        (lay.caps.mode === 'free' ? 'move' : 'reorder')));

      if (id !== selectedId) return;   // resize handles only on the selected block
      const granted = lay.caps.resize.filter(k => k === 'box' || spec.caps.resize.includes(k));
      if (granted.includes('spaceAfter')) {
        node.appendChild(handle('spaceAfter', '', 'Drag to change the space below'));
      }
      if (granted.includes('gutter')) {
        node.appendChild(handle('gutter', '', 'Drag to change the date column width'));
      }
      if (granted.includes('box')) {
        node.appendChild(handle('box', '', 'Drag to resize'));
      }
    });
  }

  /* ---- page breaks -------------------------------------------------------------------------
     The preview is one continuous sheet, but the printer is not: it cuts every
     `height - 2 * margin` of content, and `break-inside: avoid` on an entry pushes a whole entry
     down rather than splitting it. A résumé writer's first question is "where does page one
     end", so the preview draws the cuts instead of leaving them to be discovered in the PDF. */

  /** Where the printer will cut, as unscaled px from the top of the document's content box. */
  function pageBreaks(paper, page) {
    const origin = paper.querySelector('.rb-doc');
    if (!origin) return [];
    const perPage = (page.height - 2 * page.margin) * (paper.offsetWidth / page.width);
    if (!(perPage > 0)) return [];
    const total = origin.offsetHeight;
    if (total <= perPage + 1) return [];

    /* Blocks the stylesheets mark unbreakable, found by the shape every layout already stamps
       (`data-shape`, written in renderNode) rather than by naming each layout's own CSS class.

       The list of classes this replaces was wrong for four of the seven layouts registered at the
       time and had no way of knowing: `jakes-resume`, `harvard-classic`, `modern-sidebar` and
       `europass` name their entries something else, so the sweep found ZERO unbreakable blocks on
       them and the preview's page count was simply a different number from the printer's —
       measured at 14 wrong counts across 63 layout/length combinations, every one of them on a
       layout the selector could not see. The comment here used to promise "never a wrong page
       count", which is the kind of claim that stops the next reader looking.

       Keying on the shape is what makes that unfixable-by-omission: a layout cannot register
       without declaring the shapes it renders, so an entry it draws is an entry this finds. */
    const atoms = Array.from(origin.querySelectorAll('[data-shape="entry"]'));
    const top0 = origin.getBoundingClientRect().top;
    const scale = origin.getBoundingClientRect().height / (origin.offsetHeight || 1) || 1;
    const boxOf = e => {
      const r = e.getBoundingClientRect();
      return { top: (r.top - top0) / scale, bottom: (r.bottom - top0) / scale };
    };

    const breaks = [];
    let bottom = perPage;
    const guard = Math.ceil(total / perPage) + 4;   // a cut can only move up, so this terminates
    for (const atom of atoms) {
      if (breaks.length > guard) break;
      const box = boxOf(atom);
      while (box.top >= bottom) { breaks.push(bottom); bottom += perPage; }
      if (box.bottom > bottom && box.top < bottom && box.bottom - box.top <= perPage) {
        /* It would straddle the cut and it fits on a page of its own, so the printer moves the
           whole block down — exactly what `break-inside: avoid` does. */
        breaks.push(box.top);
        bottom = box.top + perPage;
      }
    }
    while (bottom < total && breaks.length <= guard) { breaks.push(bottom); bottom += perPage; }
    return breaks;
  }

  /** Draw the cuts, and hand back where they are — the miniature reports the page count in
   *  words, and counting them twice would mean measuring the sheet twice. */
  function paintPageBreaks(paper, page) {
    paper.querySelectorAll('.rb-break').forEach(e => e.remove());
    const origin = paper.querySelector('.rb-doc');
    if (!origin) return [];
    const offsetTop = origin.offsetTop;
    const breaks = pageBreaks(paper, page);
    breaks.forEach((y, i) => {
      const marker = document.createElement('div');
      marker.className = 'rb-break';
      marker.style.top = (offsetTop + y) + 'px';
      marker.dataset.label = 'Page ' + (i + 2);
      marker.setAttribute('aria-hidden', 'true');
      paper.appendChild(marker);
    });
    return breaks;
  }

  function handle(kind, glyph, title) {
    const h = document.createElement('span');
    h.className = 'rb-h rb-h-' + kind;
    h.dataset.handle = kind;
    h.title = title;
    h.textContent = glyph;
    return h;
  }

  /* ---- selection ------------------------------------------------------------------------ */

  /** Select a block, and open the form down to it. The two views are one document seen twice, so
   *  a click on the page has to leave the form showing the same block's fields — otherwise
   *  "click a block to edit it" ends at a form that is still somewhere else, which is what the
   *  arrangement this replaced actually did. `quiet` is for the callers that must NOT move the
   *  form — collapsing a row, and the paper's own drag and resize gestures, where the form
   *  scrolling under a held pointer is motion nobody asked for.
   *
   *  `keepSegment` is for the click that came FROM the page. The form is still opened down to
   *  the block and scrolled to it, so Edit is ready when the user goes there — but the segment
   *  they are reading in is theirs to change. Switching it for them threw somebody inspecting a
   *  block in Preview straight out of Preview, which is the auto-switching ADR-0128 rejected
   *  arriving through a second door: a mode that changes itself is a mode nobody can rely on. */
  function select(id, quiet, keepSegment) {
    selectedId = id;
    if (id && !quiet) {
      const d = doc();
      /* Ancestors first: a bullet's fields are inside its entry's row, which is inside its
         section's row, so opening only the bullet would open nothing anyone can see. */
      for (let n = d && Doc.find(d, id); n && n !== d.root; n = Doc.parentOf(d, n.id)) expanded.add(n.id);
      /* Which paints — `showSegment` re-measures whatever it put on screen. */
      if (keepSegment) paint(); else showSegment('edit');
      scrollFormTo(id);
    } else paint();
  }

  /* ---- dragging and resizing --------------------------------------------------------------
     One pointer gesture, one Command. The document is not touched while the pointer is down —
     the page is nudged with inline styles for feedback and the real change is dispatched on
     release, so a drag is a single undo step rather than sixty. */

  let gesture = null;

  function pxPerInch() {
    const paper = el('rb-paper');
    const lay = layout();
    const rect = paper.getBoundingClientRect();
    /* Measured, not assumed: it already includes the zoom transform, and a panel that is hidden
       measures zero — which is why `shown()` re-paints when the tab opens. */
    /* `border-box` is the app's global default, so the measured width IS the sheet width. */
    return rect.width > 0 ? rect.width / Layouts.pageFor(lay, doc()).width : 96;
  }

  function onPointerDown(e) {
    const handleEl = e.target.closest('.rb-h');
    const nodeEl = e.target.closest('[data-node]');
    if (!nodeEl) return;
    const id = nodeEl.dataset.node;
    const lay = layout();
    const model = Doc.find(doc(), id);
    if (!model) return;

    if (!handleEl) { select(id, /* quiet */ false, /* keepSegment */ true); return; }

    e.preventDefault();
    const kind = handleEl.dataset.handle;

    /* Select BEFORE anything is measured, and re-acquire the element afterwards. `paint()`
       replaces the page's innerHTML, so selecting once the gesture was under way handed the
       drag a detached node — the browser then refused `setPointerCapture` on it and the whole
       gesture died silently on pointerdown. Everything below therefore works from the element
       that exists after the repaint, and pointer capture is gone: the document-level listeners
       already keep events flowing when the pointer leaves the handle, which is all it bought. */
    if (selectedId !== id) { selectedId = id; paint(); }
    const live = el('rb-paper').querySelector('[data-node="' + id + '"]');
    if (!live) return;

    const geo = Layouts.geometryFor(lay, model, doc());
    const ppi = pxPerInch();
    gesture = {
      id, kind, nodeEl: live,
      startX: e.clientX, startY: e.clientY,
      ppi,
      from: {
        spaceAfter: geo.spaceAfter || 0,
        gutter: geo.gutter || 1.6,
        x: geo.x || 0, y: geo.y || 0,
        w: geo.w || (live.offsetWidth / ppi),
        h: geo.h || (live.offsetHeight / ppi),
      },
      moved: false,
      /* Computed from the repainted DOM, so the rectangles are the ones on screen now. */
      drops: kind === 'move' && lay.caps.mode === 'flow' ? dropPoints(id) : null,
      target: null,
    };
    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp, { once: true });
  }

  function onPointerMove(e) {
    if (!gesture) return;
    const dx = e.clientX - gesture.startX;
    const dy = e.clientY - gesture.startY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) gesture.moved = true;
    const lay = layout();
    const b = Layouts.boundsFor(lay, doc());
    const node = gesture.nodeEl;
    const clamp = Layouts.clampNum;

    if (gesture.kind === 'move' && lay.caps.mode === 'free') {
      node.style.left = clamp(gesture.from.x + dx / gesture.ppi, b.x[0], b.x[1]) + lay.page.unit;
      node.style.top = clamp(gesture.from.y + dy / gesture.ppi, b.y[0], b.y[1]) + lay.page.unit;
    } else if (gesture.kind === 'move') {
      node.classList.add('rb-dragging');
      gesture.target = nearestDrop(gesture.drops, e.clientX, e.clientY);
      showDropLine(gesture.target);
    } else if (gesture.kind === 'spaceAfter') {
      node.style.marginBottom = clamp(gesture.from.spaceAfter + dy, b.spaceAfter[0], b.spaceAfter[1]) + 'px';
    } else if (gesture.kind === 'gutter') {
      const dates = node.querySelector('.hh-dates, .tc-dates');
      const width = clamp(gesture.from.gutter - dx / gesture.ppi, b.gutter[0], b.gutter[1]);
      if (dates) dates.style.minWidth = width + lay.page.unit;
    } else if (gesture.kind === 'box') {
      node.style.width = clamp(gesture.from.w + dx / gesture.ppi, b.w[0], b.w[1]) + lay.page.unit;
      node.style.minHeight = clamp(gesture.from.h + dy / gesture.ppi, b.h[0], b.h[1]) + lay.page.unit;
    }
  }

  function onPointerUp(e) {
    document.removeEventListener('pointermove', onPointerMove);
    if (!gesture) return;
    const g = gesture;
    gesture = null;
    hideDropLine();
    if (!g.moved) { paint(); return; }

    const lay = layout();
    const b = Layouts.boundsFor(lay, doc());
    const clamp = Layouts.clampNum;
    const dx = e.clientX - g.startX;
    const dy = e.clientY - g.startY;

    if (g.kind === 'move' && lay.caps.mode === 'free') {
      store.dispatch(Cmd.setGeometry(g.id, {
        x: round(clamp(g.from.x + dx / g.ppi, b.x[0], b.x[1])),
        y: round(clamp(g.from.y + dy / g.ppi, b.y[0], b.y[1])),
      }));
    } else if (g.kind === 'move') {
      if (g.target) {
        /* Only when the column really changes. This fired on EVERY top-level drop, so a drag
           within one column pushed two undo entries — the second a visible no-op — and labelled
           the whole move "Move to column". `UNDO_LIMIT` is 60, so a drag-heavy session spent
           half of it undoing nothing. A node arriving from inside a section carries no slot of
           its own, and `moveNode` writes the literal `'main'` for it (resume_document.js), so
           that — not `lay.slots[0].id` — is what a missing slot is compared against. */
        const moving = Doc.find(doc(), g.id);
        const was = (moving && moving.slot) || 'main';
        if (g.target.parentId == null && g.target.slot && g.target.slot !== was) {
          store.dispatch(Cmd.setSlot(g.id, g.target.slot));
        }
        store.dispatch(Cmd.moveNode(g.id, g.target.parentId, masterIndex(g.target)));
      } else paint();
    } else if (g.kind === 'spaceAfter') {
      store.dispatch(Cmd.setGeometry(g.id, {
        spaceAfter: Math.round(clamp(g.from.spaceAfter + dy, b.spaceAfter[0], b.spaceAfter[1])),
      }));
    } else if (g.kind === 'gutter') {
      store.dispatch(Cmd.setGeometry(g.id, {
        gutter: round(clamp(g.from.gutter - dx / g.ppi, b.gutter[0], b.gutter[1])),
      }));
    } else if (g.kind === 'box') {
      store.dispatch(Cmd.setGeometry(g.id, {
        w: round(clamp(g.from.w + dx / g.ppi, b.w[0], b.w[1])),
        h: round(clamp(g.from.h + dy / g.ppi, b.h[0], b.h[1])),
      }));
    }
  }

  const round = n => Math.round(n * 100) / 100;

  /** Every place the dragged node could legally land, as screen rectangles. Legality is
   *  Layer 1's answer (`Components.accepts`), never a rule invented here — which is what stops
   *  a bullet being dropped into the Education section it has no renderer for. */
  function dropPoints(dragId) {
    const d = view();
    const lay = layout();
    const paper = el('rb-paper');
    const dragged = Doc.find(d, dragId);
    if (!dragged) return [];

    const inside = new Set();
    Doc.walk(dragged, n => inside.add(n.id));

    const points = [];
    /* A drop point names the sibling to land BEFORE (or null for "at the end"), never a
       positional index. The DOM the pointer is over is the RESOLVED document — under a version
       that leaves blocks out, its indices are short by one per hidden sibling, and applying them
       to the master dropped the block in the wrong place. A node id survives the translation;
       a number does not. */
    const addRun = (containerEl, kids, parentId, slot) => {
      if (!containerEl) return;
      const rows = kids.map(k => k.getBoundingClientRect());
      rows.forEach((r, i) => points.push({
        parentId, slot, beforeId: kids[i].dataset.node, x: r.left + r.width / 2, y: r.top }));
      const last = rows[rows.length - 1];
      const box = containerEl.getBoundingClientRect();
      points.push({
        parentId, slot, beforeId: null,
        x: last ? last.left + last.width / 2 : box.left + box.width / 2,
        y: last ? last.bottom : box.top + 8,
      });
    };

    const childElements = (parentEl, parentNode) => {
      const wanted = new Set((parentNode ? parentNode.children : d.root.children).map(c => c.id));
      return Array.from(parentEl.querySelectorAll('[data-node]'))
        .filter(e => wanted.has(e.dataset.node));
    };

    /* The slots ARE the document root, so they are offered only for a node that may sit there
       (resume_components.js states that rule once). Offering every slot to every dragged node
       is how a bullet reached the root — the model refuses it now, but a drop point the model
       will refuse is a target the page drew and then ignored, which reads as a broken drag. */
    if (Components.acceptsAtRoot(dragged.type)) for (const slot of lay.slots) {
      const slotEl = paper.querySelector('[data-slot="' + slot.id + '"]');
      if (!slotEl) continue;
      const kids = Array.from(slotEl.children).filter(e => e.dataset && e.dataset.node &&
        !inside.has(e.dataset.node));
      addRun(slotEl, kids, null, slot.id);
    }

    paper.querySelectorAll('[data-node]').forEach(container => {
      const id = container.dataset.node;
      if (inside.has(id)) return;
      const node = Doc.find(d, id);
      if (!node || !Components.accepts(node.type, dragged.type)) return;
      addRun(container, childElements(container, node).filter(e => !inside.has(e.dataset.node)),
        id, null);
    });
    return points;
  }

  /** Where `point` lands in the master document — the tree the Command will actually edit. */
  function masterIndex(point) {
    const d = doc();
    const parent = point.parentId ? Doc.find(d, point.parentId) : d.root;
    if (!parent) return null;
    if (!point.beforeId) return parent.children.length;
    const at = parent.children.findIndex(c => c.id === point.beforeId);
    return at < 0 ? parent.children.length : at;
  }

  function nearestDrop(points, x, y) {
    let best = null;
    let bestScore = Infinity;
    for (const p of points) {
      /* Vertical distance dominates: a résumé is a stack, and weighting x equally made a drop
         next to a wide section jump into the narrow column beside it. */
      const score = Math.abs(p.y - y) + Math.abs(p.x - x) * 0.25;
      if (score < bestScore) { bestScore = score; best = p; }
    }
    return best;
  }

  function showDropLine(point) {
    let line = el('rb-dropline');
    if (!line) {
      line = document.createElement('div');
      line.id = 'rb-dropline';
      line.className = 'rb-dropline';
      document.body.appendChild(line);
    }
    if (!point) { line.hidden = true; return; }
    line.hidden = false;
    line.style.left = (point.x - 90) + 'px';
    line.style.top = point.y + 'px';
  }
  function hideDropLine() { const l = el('rb-dropline'); if (l) l.hidden = true; }

  /* ---- the panes -------------------------------------------------------------------------- */

  /** The version picker: the master plus every Tailoring, and the delete button only when one
   *  is active. */
  /* "Version" is what this control is called for users; **Tailoring** is what the model calls the
     same thing (CONTEXT.md). The two words are deliberate and this is the only place they meet —
     everything below the view says `tailoring`, so a reader seeing both in one file is not looking
     at two concepts. */
  /* Whether the bar is currently asking "delete this version?". It lives out here because
     `versionPaint` runs on every keystroke and would otherwise put the Delete button back over
     the question it had just asked. */
  let askVersionDrop = false;
  /* Which **Saved job** the version about to be created is for, or null for one named by hand.
     It is the popover's state rather than the document's: it lives only between opening the menu
     and creating the version, and `addTailoring` is where it stops being a UI fact and becomes
     the Tailoring's own `jobId`. */
  let versionJob = null;

  /* The stars app.js already fetched (`window.savedJobs`, ADR-0133), not a second GET /saved from
     here. The rows are on the same page and that list is the one every star and unstar keeps
     current, so a picker reading it can never disagree with the Saved tab — and this tab stays
     what it has always been: a feature that needs no server at all.

     Null and empty mean different things and both are handled: null is "there is no such list"
     — signed out, no account store, or /saved has not answered — and the picker is not offered;
     empty is "signed in, nothing starred", which it says out loud. */
  function savedJobs() {
    const read = typeof window !== 'undefined' && window.savedJobs;
    const rows = typeof read === 'function' ? read() : null;
    return Array.isArray(rows) ? rows : null;
  }

  /** What a version made from a Saved job is called: "Razorpay · Backend Engineer". The company
   *  first, because the picker and the version dropdown are both read down their left edge and
   *  the company is what tells two applications apart; the same `·` the result cards and the
   *  Résumés list already join fields with. A row with no company is its title alone.
   *
   *  It is a starting point, not a lock — the field it fills is editable before Create, which is
   *  the only chance to name it: nothing renames a Tailoring afterwards.
   *
   *  No empty-name fallback, because a Saved job cannot have one: `POST /saved` refuses a body
   *  with no title (deploy/hf-space/app.py), so every row here has at least that. `createVersion`
   *  carries the fallback for the field a person can genuinely leave blank. */
  const versionName = j => [j.company, j.title].filter(Boolean).join(' · ');

  /** The picker: every Saved job, newest star first, with the chosen one marked.
   *
   *  Sorted rather than trusted: app.js keeps `mySaved` nearly in star order but not exactly — a
   *  refused unstar puts its row back on the end — which is why the Saved tab sorts its own copy
   *  too. On a copy this function makes, so "may I reorder this?" is answered here rather than
   *  being an invariant held across two modules. */
  function versionJobsPaint() {
    const rows = savedJobs();
    /* Through `el(…)` rather than a local, and not for style: `resume_markup.test.js` derives the
       list of elements this file hides by scanning for `el('…').hidden =`, and that scan is what
       makes the stylesheet's matching `[hidden]` rule enforced rather than remembered. Hidden
       through a local the element is invisible to it — and `.rb-jobpick` sets `display`, which
       outranks the attribute, so the picker would stay on screen after being put away. */
    el('rb-version-jobs').hidden = !rows;
    if (!rows) return;
    if (!rows.length) {
      el('rb-version-jobs').innerHTML =
        '<p class="note">No saved jobs yet — hit the ☆ on any search result and it shows up ' +
        'here, ready to tailor for.</p>';
      return;
    }
    const newest = rows.slice()
      .sort((a, b) => String(b.starred_at || '').localeCompare(String(a.starred_at || '')));
    el('rb-version-jobs').innerHTML = '<p class="rb-doclist-head">Your saved jobs</p>' +
      newest.map(j => {
        const where = [j.location, j.salary].filter(Boolean).join(' · ');
        return '<button class="rb-docopen' + (versionJob === j.job_id ? ' on' : '') +
          '" data-saved="' + esc(j.job_id) + '" aria-pressed="' + (versionJob === j.job_id) + '">' +
          esc(versionName(j)) + (where ? '<span class="note">' + esc(where) + '</span>' : '') +
          '</button>';
      }).join('');
  }

  function versionPaint() {
    const d = doc();
    const pick = el('rb-version');
    const tailorings = d.tailorings || [];
    pick.innerHTML = '<option value="">Master résumé</option>' + tailorings.map(t =>
      '<option value="' + esc(t.id) + '"' + (d.activeTailoring === t.id ? ' selected' : '') + '>' +
      esc(t.name) + '</option>').join('');
    pick.value = d.activeTailoring || '';
    const asking = askVersionDrop && !!d.activeTailoring;
    el('rb-version-del').hidden = !d.activeTailoring || asking;
    el('rb-version-confirm').hidden = !asking;
    if (asking) {
      el('rb-version-confirm-text').textContent =
        'Delete “' + (Doc.tailoringOf(d) || {}).name + '”? The master and every other version ' +
        'keep every word.';
    }
  }

  /* What the Content pane is currently showing. The caret guard below may only skip a repaint
     while the pane would rebuild the SAME thing; if the selection, the layout or the active
     version changed, the pane must repaint even though a field holds focus — otherwise clicking a
     new block while a form input is focused leaves the previous block's fields on screen, wired to
     a node the user is no longer looking at. Measured: selecting a block on the free-canvas layout
     right after typing in a range control showed the previous layout's controls. */
  let contentShowing = null;
  const contentKey = () => {
    const d = doc();
    /* Which rows are open belongs in this key too: opening a row is a repaint the user asked
       for, and without it a click on a row's header while a field elsewhere held the caret
       toggled `aria-expanded` in the model and nothing at all on screen. */
    return d ? [selectedId, d.layoutId, d.activeTailoring,
      Array.from(expanded).sort().join(',')].join('|') : null;
  };

  /* Rebuild every pane EXCEPT the one the user is currently typing in — replacing a field's
     HTML under the caret loses the caret, and the position with it. Keyed on focus rather than on
     a "the last change came from the form" flag, which is what this was first: that flag froze
     every pane on every keystroke, so the Checks panel sat on a stale list while the badge beside
     it counted the new one. Focus is the fact that actually matters, and it is readable. */
  function panesPaint() {
    const active = document.activeElement;
    /* A CARET, not merely focus. This guard used to fire on any focused descendant — including
       the button the user had just clicked — so clicking an Outline row, "Add inside", "Delete"
       or "Duplicate" left the pane those controls live in frozen on its previous contents until
       focus happened to move elsewhere. That is the primary editing loop, and it was broken:
       selecting a block showed the block selected on the page and no fields beside it. */
    const typing = active && typeof active.matches === 'function' &&
      active.matches('input:not([type="checkbox"]), textarea, select');
    const holdsCaret = pane => typing && pane && pane !== active && pane.contains(active);

    const key = contentKey();
    if (!holdsCaret(el('rb-pane-document')) || key !== contentShowing) {
      documentPane();
      contentShowing = key;
    } else {
      patchChecks();
    }
    if (!holdsCaret(el('rb-pane-design'))) designPane();
    if (!holdsCaret(el('rb-pane-checks'))) checksPane();
  }

  /* A field whose answer another control has already given. "Still here" and an End date are two
     controls that contradict each other and nothing on screen said which one wins — the layouts'
     own `dates` rule reads the tick and ignores the field, so the tick wins, and the field it
     settles is switched off rather than left inviting an answer that is thrown away. */
  const SETTLED_BY = Object.freeze({ end: 'current' });

  function fieldControl(nodeId, field, value, settled) {
    const safeNode = esc(nodeId);
    const id = esc('rb-f-' + nodeId + '-' + field.key);
    const label = '<label for="' + id + '">' + esc(field.label) +
      (field.hint ? '<span class="rb-hint-i" title="' + esc(field.hint) + '">?</span>' : '') + '</label>';
    if (field.kind === 'flag') {
      return '<div class="rb-field rb-flag"><label><input type="checkbox" id="' + id +
        '" data-node="' + safeNode + '" data-field="' + esc(field.key) + '"' + (value ? ' checked' : '') +
        '> ' + esc(field.label) + '</label></div>';
    }
    if (field.kind === 'multiline') {
      /* The whole width of the grid, because it holds a paragraph. Everything else pairs up. */
      return '<div class="rb-field rb-field-wide">' + label + '<textarea id="' + id + '" rows="4" data-node="' +
        safeNode + '" data-field="' + esc(field.key) + '" placeholder="' + esc(field.placeholder) + '">' +
        esc(value || '') + '</textarea></div>';
    }
    return '<div class="rb-field' + (settled ? ' rb-field-off' : '') + '">' + label +
      '<input id="' + id + '" data-node="' + safeNode +
      '" data-field="' + esc(field.key) + '" value="' + esc(value || '') + '" placeholder="' +
      esc(field.placeholder) + '"' +
      (settled ? ' disabled title="' + esc('Settled by “' + settled + '”. Untick it to type a date.') + '"' : '') +
      '></div>';
  }

  /* ---- the document accordion --------------------------------------------------------------
     The form IS the résumé, as its own sections: Contact, then each Section, each opening to its
     entries, each entry opening to its fields and its bullets. Editing happens where you
     navigate, so the "Outline" and "Content" of the arrangement this replaces are one thing now.

     What it replaces was a flat developer-style node tree — every node in the document at one
     indent, which is how a debugger shows a tree and not how a résumé reads. Measured on the
     guide's own worked example: 13 rows, 7 of them bullets truncated to "Bullet Operated our
     Point of Sale (POS) cas…". This shows 3, and a bullet is now what it actually is — a field
     of the job it belongs to, not a sibling of "Work History". */

  /** The words that name a row: what the block SAYS, falling back to what it is. A row reading
   *  "Work entry" for every job is a row nobody can navigate by. */
  function rowTitle(d, spec, node) {
    const content = Doc.contentOf(d, node.id);
    const first = spec.fields.map(f => content[f.key])
      .find(v => typeof v === 'string' && v.trim());
    return String(first || '').trim().slice(0, 60) || spec.label;
  }

  /** Findings that name a block, and every block that contains one. Two different jobs: the
   *  first shows a rule inline in the row it is about (which is where it can be acted on), the
   *  second puts a mark on a COLLAPSED ancestor so a problem three levels down is still
   *  findable without opening every row to hunt for it. */
  function findingIndex(d) {
    const byNode = new Map();
    const marked = new Set();
    if (untouched(view())) return { byNode, marked };
    for (const f of findings()) {
      if (!f.nodeId) continue;
      if (!byNode.has(f.nodeId)) byNode.set(f.nodeId, []);
      byNode.get(f.nodeId).push(f);
      if (f.level === 'note') continue;
      for (let n = Doc.find(d, f.nodeId); n && n !== d.root; n = Doc.parentOf(d, n.id)) marked.add(n.id);
    }
    return { byNode, marked };
  }

  /* The two lines of a finding, spelled once. The Checks panel wraps them in a button that
     selects the block; inline in the block's own row they are a paragraph, because you are
     already there. Different element, same body. */
  const findingBody = f => '<span class="rb-finding-rule">' + esc(f.rule) +
    '</span><span class="rb-finding-msg">' + esc(f.message) + '</span>';

  const findingRows = list => (list || []).map(f =>
    '<p class="rb-finding rb-' + esc(f.level) + '">' + findingBody(f) + '</p>').join('');

  /* The container is emitted even when it is empty, and the mark even when it is clear. Both are
     refilled in place by `patchChecks` while the pane is frozen under a caret — and the pane IS
     frozen exactly when this matters, because the rule you are breaking is the one you are
     typing. Measured before this: a bullet given two sentences showed its warning on the badge
     immediately and inside its own row not at all, until some later change happened to repaint
     the pane. Same trick the range labels below use, and for the same reason. */
  const findingsHtml = (id, list) =>
    '<div class="rb-inline-checks" data-checks="' + esc(id) + '">' + findingRows(list) + '</div>';

  const flagHtml = (id, marked) =>
    '<span class="rb-row-flag" data-flag="' + esc(id) + '" aria-label="has a check to fix"' +
    (marked ? '' : ' hidden') + '><span aria-hidden="true">●</span></span>';

  function patchChecks() {
    const pane = el('rb-pane-document');
    if (!pane || typeof pane.querySelectorAll !== 'function') return;
    const marks = findingIndex(doc());
    for (const box of pane.querySelectorAll('[data-checks]')) {
      box.innerHTML = findingRows(marks.byNode.get(box.dataset.checks));
    }
    for (const dot of pane.querySelectorAll('[data-flag]')) {
      dot.hidden = !marks.marked.has(dot.dataset.flag);
    }
  }

  /** The tick that says whether a block is printed — the answer to "I keep five projects and
   *  show two". ONE control, whichever layer is being edited: on the master it writes the
   *  document's own list, under a version it writes that version's, and `Cmd.setHidden` decides
   *  which from the tailoring id it is handed. Two controls that looked identical and reached
   *  different layers would have been the confusing way to say the same thing.
   *
   *  It is not offered where Layer 1 says the block is not optional — the header cannot be
   *  removed, so it cannot be switched off either.
   *
   *  Under a version, a block the MASTER leaves out is ticked off and disabled rather than
   *  silently dead: a version is a difference from the master, so turning it on here could only
   *  either do nothing or edit the master by surprise. */
  function showTick(d, spec, node, inline) {
    /* A row that cannot be switched off still owes the tick's column, or its label starts 24px
       left of every other row's and the accordion reads as two lists. */
    if (!spec.caps.remove) return inline ? '' : '<span class="rb-show rb-show-none"></span>';
    const tailoring = Doc.tailoringOf(d);
    const byMaster = !!tailoring && (d.hidden || []).includes(node.id);
    const on = !Doc.isHidden(d, node.id);
    const what = rowTitle(d, spec, node);
    const title = byMaster
      ? 'Left off the master résumé, so every version leaves it off too.'
      : tailoring
      ? 'Show this in “' + tailoring.name + '”. Unticked keeps every word — it just does not print here.'
      : 'Show this on your résumé. Unticked keeps every word — it just does not print.';
    return '<label class="' + (inline ? 'rb-show-inline' : 'rb-show') + '" title="' + esc(title) + '">' +
      '<input type="checkbox" id="' + esc('rb-show-' + node.id) + '" data-show="' + esc(node.id) + '"' +
      (on ? ' checked' : '') + (byMaster ? ' disabled' : '') + '>' +
      (inline ? '<span>Show on the résumé</span>'
        : '<span class="rb-vh">Show ' + esc(what) + ' on the résumé</span>') + '</label>';
  }

  /** Every field a block owns, as a grid. In a 400px rail each was a full row whatever it held. */
  const fieldsHtml = (d, spec, node, override) => {
    if (!spec.fields.length) return '';
    const content = Doc.contentOf(d, node.id);
    /* The label of the tick that settles a field, or null — the words the user sees, so the
       disabled field can say which control answered for it. */
    const settledBy = f => {
      const key = SETTLED_BY[f.key];
      if (!key || !content[key]) return null;
      const tick = spec.fields.find(other => other.key === key);
      return tick ? tick.label : key;
    };
    return '<div class="rb-fields">' + spec.fields.map((f, i) =>
      fieldControl(node.id, override ? override(f, i) : f, content[f.key], settledBy(f))).join('') +
      '</div>';
  };

  /** The action row a block carries: reorder without a mouse, duplicate, delete, and — under a
   *  version — the two tailoring choices. Alt + arrow does the same reorder from the keyboard;
   *  these exist because a drag handle on the page was the only OTHER way to move a block, and
   *  WCAG 2.2 SC 2.5.7 asks for a path that is not a drag. */
  function actionsHtml(d, lay, spec, node) {
    const tailoring = Doc.tailoringOf(d);
    const parts = [];
    if (lay.caps.reorder && spec.caps.reorder) {
      const label = esc(rowTitle(d, spec, node));
      parts.push('<button class="rb-act" data-act="moveup" data-node="' + esc(node.id) +
        '" title="Move up" aria-label="Move ' + label + ' up">↑</button>' +
        '<button class="rb-act" data-act="movedown" data-node="' + esc(node.id) +
        '" title="Move down" aria-label="Move ' + label + ' down">↓</button>');
    }
    if (spec.caps.duplicate) {
      parts.push('<button class="rb-act" data-act="duplicate" data-node="' + esc(node.id) +
        '" title="Duplicate" aria-label="Duplicate ' + esc(rowTitle(d, spec, node)) + '">⧉</button>');
    }
    if (spec.caps.remove) {
      parts.push('<button class="rb-act rb-act-danger" data-act="remove" data-node="' + esc(node.id) +
        '" title="Delete" aria-label="Delete ' + esc(rowTitle(d, spec, node)) + '">✕</button>');
    }
    /* "Leave out of this version" was a button here. It is a tick on the row now, beside the
       one the master carries — the same question, asked once, in the place somebody scanning
       "what does this résumé say" is already looking. */
    if (tailoring && (tailoring.picks || {})[node.id]) {
      parts.push('<button class="ghost rb-mini" data-act="unfork" data-node="' + esc(node.id) +
        '">Use the master’s words</button>');
    }
    return parts.length ? '<div class="rb-acts">' + parts.join('') + '</div>' : '';
  }

  /** One bullet, inline. Deliberately not a row of its own: three levels of opening to reach a
   *  sentence is three clicks to type, and a bullet has one field anybody edits.
   *
   *  It still carries `data-block` and the geometry disclosure, because "not a row" is a drawing
   *  decision and neither of those is about drawing. Without the first, clicking a bullet on the
   *  page revealed nothing in the form — `scrollFormTo` found no anchor and no-oped silently, on
   *  the block type that gets edited most. Without the second, the free-canvas layout grants a
   *  bullet move and box handles on the page (it grants them to every node) with no typed way to
   *  set the same numbers — a WCAG 2.2 SC 2.5.7 failure visible on one layout only. */
  function bulletHtml(d, lay, node, ordinal, marks) {
    const spec = Components.get(node.type);
    const tailoring = Doc.tailoringOf(d);
    const off = Doc.isHidden(d, node.id);
    /* Rendered through `fieldControl` over the type's OWN declared fields, like every other block
       in this form. Hand-rolling the textarea and the checkbox meant naming `text` and `role` as
       literals and reaching for `fields[0]` by position — Layer-1 facts copied into Layer 3,
       which ADR-0123 puts on the Component. Only the first field's label is overridden, to carry
       the bullet's number. */
    return '<div class="rb-bullet' + (node.id === selectedId ? ' on' : '') +
      (off ? ' rb-off' : '') + '" data-block="' + esc(node.id) + '">' +
      ((tailoring && (tailoring.picks || {})[node.id]) ? '<span class="rb-tag">tailored</span>' : '') +
      (off ? '<span class="rb-tag">not shown</span>' : '') +
      findingsHtml(node.id, marks.byNode.get(node.id)) +
      fieldsHtml(d, spec, node, (f, i) =>
        (i === 0 ? Object.assign({}, f, { label: 'Bullet ' + ordinal }) : f)) +
      geometryHtml(lay, node) +
      '<div class="rb-bullet-foot">' + showTick(d, spec, node, true) +
      '<span class="spacer"></span>' + actionsHtml(d, lay, spec, node) + '</div></div>';
  }

  /** The "Size and spacing" disclosure a block carries when its layout grants it any geometry.
   *  Shared by rows and bullets so a layout cannot grant a handle on the page that has no typed
   *  equivalent in the form — that pairing is the whole of SC 2.5.7 here. Folded away because it
   *  is not what somebody opening a job came to do, so it does not sit above the words. */
  function geometryHtml(lay, node) {
    const geo = geometryControls(lay, node);
    if (!geo) return '';
    const key = 'geo:' + node.id;
    const id = esc('rb-geo-' + node.id);
    return '<div class="rb-sub"><button class="rb-sub-btn" data-row="' + esc(key) +
      '" aria-expanded="' + (expanded.has(key) ? 'true' : 'false') + '" aria-controls="' + id +
      '"><span class="rb-caret" aria-hidden="true">›</span>Size and spacing</button>' +
      '<div id="' + id + '"' + (expanded.has(key) ? '' : ' hidden') + '>' + geo + '</div></div>';
  }

  /** One row of the accordion, and its children. `depth` is only for the indent and the heading
   *  level — the nesting itself comes from the document. */
  function rowHtml(d, lay, node, depth, marks) {
    const spec = Components.get(node.type);
    if (!spec) return '';
    const open = expanded.has(node.id);
    const bodyId = esc('rb-row-' + node.id);
    /* h2 for a top-level row, h3 below it. The app's own h1 is the masthead and every other
       tab uses h2 for a group inside a panel, so this continues that outline rather than
       starting at h3 and skipping a level — a screen reader navigates this form by heading, and
       a gap in the sequence is a gap in the document it is describing. */
    const heading = depth === 0 ? 'h2' : 'h3';
    const off = Doc.isHidden(d, node.id);
    const kids = node.children || [];
    const bullets = kids.filter(k => (Components.get(k.type) || {}).shape === 'bullet');
    const rows = kids.filter(k => (Components.get(k.type) || {}).shape !== 'bullet');

    const out = ['<div class="rb-row rb-row-d' + depth + (node.id === selectedId ? ' on' : '') +
      (off ? ' rb-off' : '') + '" data-block="' + esc(node.id) + '">'];
    /* The tick and the disclosure are siblings on one line. The tick cannot go inside the
       disclosure button — a control inside a control is not operable — and it has to be on the
       CLOSED row: "which of my five projects does this résumé show" is a question answered by
       reading down the column, not by opening every row to look. */
    out.push('<div class="rb-row-line">' + showTick(d, spec, node) +
      '<' + heading + ' class="rb-row-head"><button class="rb-row-btn" data-row="' +
      esc(node.id) + '" aria-expanded="' + (open ? 'true' : 'false') + '" aria-controls="' + bodyId + '">' +
      '<span class="rb-caret" aria-hidden="true">›</span>' +
      '<span class="rb-row-label">' + esc(rowTitle(d, spec, node)) + '</span>' +
      (off ? '<span class="rb-tag">not shown</span>' : '') +
      '<span class="rb-row-kind">' + esc(spec.label) + '</span>' +
      /* Spoken as well as drawn: a dot that only exists as a colour tells a screen-reader user
         nothing, and it is the one mark in this form that reports a problem. */
      flagHtml(node.id, marks.marked.has(node.id)) +
      '</button></' + heading + '></div>');

    out.push('<div class="rb-row-body" id="' + bodyId + '"' + (open ? '' : ' hidden') + '>');
    if (open) {
      out.push(findingsHtml(node.id, marks.byNode.get(node.id)));
      out.push(fieldsHtml(d, spec, node));
      if (bullets.length) {
        out.push('<div class="rb-bullets">' +
          bullets.map((b, i) => bulletHtml(d, lay, b, i + 1, marks)).join('') + '</div>');
      }
      for (const child of rows) out.push(rowHtml(d, lay, child, depth + 1, marks));

      /* Everything Layer 1 says this type accepts, minus the header, which is addable nowhere.
         Filtering `section` out too would have quietly narrowed what the model allows —
         `Components.accepts('*')` still permits a Section inside a Section and the drag path
         still does it, so the form refusing it is a disagreement, not a simplification. */
      const addable = spec.accepts.includes('*')
        ? Components.all().filter(s => s.type !== 'header')
        : spec.accepts.map(t => Components.get(t)).filter(Boolean);
      if (addable.length) {
        /* A Section accepts '*', so this is eleven buttons in a row unless it is grouped — and
           "Project" and "Project with a stack", the two somebody adding a second project is
           looking for, were in the middle of it beside "Situation note" and "Language". The
           entries a section is FOR come first; everything the model still allows is one
           disclosure away, so nothing is narrowed, only ordered. */
        const first = addable.filter(x => x.shape === 'entry' || x.shape === 'bullet');
        const rest = addable.filter(x => first.indexOf(x) < 0);
        const addBtn = x => '<button class="ghost rb-mini" data-add="' + esc(x.type) +
          '" data-into="' + esc(node.id) + '" title="' + esc(x.blurb) + '">+ ' + esc(x.label) +
          '</button>';
        out.push('<div class="rb-add">' + (first.length ? first : rest).map(addBtn).join('') + '</div>');
        if (first.length && rest.length) {
          const key = 'add:' + node.id;
          const listId = esc('rb-add-' + node.id);
          out.push('<div class="rb-sub"><button class="rb-sub-btn" data-row="' + esc(key) +
            '" aria-expanded="' + (expanded.has(key) ? 'true' : 'false') + '" aria-controls="' +
            listId + '"><span class="rb-caret" aria-hidden="true">›</span>More block types</button>' +
            '<div class="rb-add" id="' + listId + '"' + (expanded.has(key) ? '' : ' hidden') + '>' +
            rest.map(addBtn).join('') + '</div></div>');
        }
      }

      out.push(geometryHtml(lay, node));

      /* Which column a top-level block sits in — only offered where the layout has more than
         one, so a single-column template never shows a control that does nothing. */
      if (lay.slots.length > 1 && d.root.children.includes(node)) {
        const slotId = esc('rb-slot-' + node.id);
        out.push('<div class="rb-field"><label for="' + slotId + '">Column</label><select id="' +
          slotId + '" data-slot-for="' + esc(node.id) + '">' + lay.slots.map(s =>
            '<option value="' + esc(s.id) + '"' +
            ((node.slot || lay.slots[0].id) === s.id ? ' selected' : '') + '>' + esc(s.label) +
            '</option>').join('') + '</select></div>');
      }

      out.push(actionsHtml(d, lay, spec, node));
    }
    out.push('</div></div>');
    return out.join('');
  }

  /** The one thing a first visit should do next. Not a hint sentence — the arrangement this
   *  replaces offered "Click a block on the page to edit its words" beside a page that gave no
   *  sign a block was clickable, which is a sentence standing in for an affordance. A genuinely
   *  first visit opens the guide's worked example, so the honest next step is "make it yours". */
  function firstRunCard() {
    return '<div class="rb-firstrun"><h2>Start here</h2>' +
      '<p>This is the guide’s worked example, so you can see the shape of a good résumé before ' +
      'you write one. Make it yours — your own name and contact details first.</p>' +
      '<div class="rb-firstrun-acts"><button class="go rb-mini" data-act="startwriting">' +
      'Start with my name &amp; contact</button>' +
      '<button class="ghost rb-mini" data-act="blank">or start from an empty page</button>' +
      '</div></div>';
  }

  function documentPane() {
    const d = doc();
    const lay = layout();
    const out = [];
    const tailoring = Doc.tailoringOf(d);
    const marks = findingIndex(d);

    if (firstRun) out.push(firstRunCard());
    if (tailoring) {
      out.push('<p class="rb-banner">Editing <b>' + esc(tailoring.name) + '</b>. What you type ' +
        'here is kept for this version only — the master keeps its own words, and a fix you make ' +
        'there still reaches every version that has not overridden it.</p>');
    }

    out.push('<div class="rb-tree">');
    const kids = d.root.children || [];
    if (!kids.length) out.push('<p class="note">Nothing on the page yet. Add a section below.</p>');
    for (const kid of kids) out.push(rowHtml(d, lay, kid, 0, marks));
    out.push('</div>');

    /* One obvious way to grow the résumé, and the two blocks that are not sections kept quiet
       beside it. The arrangement this replaces offered all three at equal weight under "Add to
       the page:", which reads as three unrelated choices rather than one and two footnotes. */
    out.push('<div class="rb-doc-foot">' +
      '<button class="go rb-mini rb-add-section" data-add="section" data-into="">+ Add section</button>' +
      '<div class="rb-add"><span class="note">Also:</span>' +
      /* Derived from the registry, not a list. It used to name three types, so a component added
         later — `professional_summary`, `language_line` — could be reached inside a section but
         never added at the top level, and nothing said so. What belongs at the top level is
         `Components.acceptsAtRoot`, the SAME rule the drag path and the model's move guard read
         — it used to be spelled out again here, and the three spellings disagreed. Minus the two
         handled elsewhere: a section has its own button above, and the header is addable nowhere. */
      Components.all().filter(s => Components.acceptsAtRoot(s.type) &&
        s.shape !== 'section' && s.shape !== 'header').map(s =>
        '<button class="ghost rb-mini" data-add="' + esc(s.type) + '" data-into="">' + esc(s.label) +
        '</button>').join('') + '</div>' +
      '<div class="rb-prefill"><button class="ghost rb-mini" data-act="prefill">Fill from my profile</button>' +
      '<span class="note" id="rb-prefill-note"></span></div></div>');

    el('rb-pane-document').innerHTML = out.join('');
  }

  /* Every geometry a layout lets you DRAG, you can also type. Dragging was the only way to
     change the space below a block or the width of its date column — no keyboard path and no
     single-pointer alternative, which is a WCAG 2.2 SC 2.5.7 failure and, less formally, means
     the two affordances were unusable by anyone who cannot drag precisely. These read from the
     same `caps.resize` the handles do, so a layout cannot grant one without the other. */
  const GEOMETRY_LABELS = {
    spaceAfter: ['Space below', 'px', 1],
    gutter: ['Date column width', 'in', 0.05],
    w: ['Width', 'in', 0.05],
    h: ['Height', 'in', 0.05],
    x: ['From the left', 'in', 0.05],
    y: ['From the top', 'in', 0.05],
  };

  function geometryControls(lay, node) {
    const spec = Components.get(node.type);
    if (!spec) return '';
    const granted = lay.caps.resize.filter(k => k === 'box' || spec.caps.resize.includes(k));
    if (!granted.length) return '';
    const keys = granted.includes('box') ? ['x', 'y', 'w', 'h'] : granted;
    /* The sheet THIS document chose (#423), not the Letter the Layout declares. The renderer
       clamps a box to the paper, so a slider offering the Layout's own maximum on an A4 document
       invites a position that is then clamped away — and warned about by the overflow rule. */
    const bounds = Layouts.boundsFor(lay, doc());
    const geo = Layouts.geometryFor(lay, node, doc());
    const rows = keys.map(key => {
      const [label, unit, step] = GEOMETRY_LABELS[key] || [key, '', 1];
      const bound = bounds[key];
      if (!bound) return '';
      const value = geo[key] != null ? geo[key] : bound[0];
      const id = 'rb-g-' + esc(node.id) + '-' + key;
      return '<div class="rb-field rb-range"><label for="' + id + '">' + esc(label) +
        ' <b>' + esc(String(value)) + esc(unit) + '</b></label>' +
        '<input type="range" id="' + id + '" data-geo="' + key + '" data-node="' + esc(node.id) +
        '" min="' + bound[0] + '" max="' + bound[1] + '" step="' + step +
        '" value="' + esc(String(value)) + '"></div>';
    }).join('');
    return rows ? '<div class="rb-geo"><span class="note">Size and spacing — the same thing the ' +
      'handles on the page drag.</span>' + rows + '</div>' : '';
  }

  const MINI_OFF = 'headstart.resume.minioff';

  /** Show or hide the miniature, and say so on the control that did it. Painting it while hidden
   *  would be work nobody can see — and worse, a measurement taken against a hidden frame, which
   *  is the trap that has produced three separate bugs on this tab. */
  function showMini(on) {
    el('rb-mini').hidden = !on;
    el('rb-mini-toggle').textContent = on ? 'Hide preview' : 'Show preview';
    el('rb-mini-toggle').setAttribute('aria-expanded', on ? 'true' : 'false');
  }

  /* Whether "Fine tuning" is open. Held here rather than read off the element, because the pane
     is rebuilt wholesale on every paint and a <details> that closed itself mid-drag would be
     worse than one that never opened. */
  let fineOpen = false;

  function designPane() {
    const d = doc();
    const lay = layout();
    const theme = Layouts.themeFor(lay, d);
    /* The template first, because every control under it belongs to the template: the tunables
       are its own declared tokens, and the sheet is what it is laid out on. Both moved here out
       of a chrome band above the page, where they read as per-session controls — they are not,
       they are properties of the document, and one of them re-wraps every bullet. */
    const out = ['<button class="rb-template-card" data-act="templates">' +
      '<span class="rb-template-card-label">' + esc(lay.label) + '</span>' +
      (lay.summary ? '<span class="note">' + esc(lay.summary) + '</span>' : '') +
      '<span class="rb-chip-cue">Change template</span></button>'];
    if (lay.blurb) out.push('<p class="note">' + esc(lay.blurb) + '</p>');
    if (lay.credit) out.push('<p class="note rb-credit">' + esc(lay.credit) + '</p>');
    out.push('<div class="rb-field"><label for="rb-paper-size">Paper size</label>' +
      '<select id="rb-paper-size">' + Layouts.PAPERS.map(p =>
        '<option value="' + esc(p.id) + '"' + (p.id === Layouts.paperIdFor(lay, d) ? ' selected' : '') +
        '>' + esc(p.label) + '</option>').join('') + '</select></div>');
    /* Two tiers, because a layout declares up to twelve tunables and opening the pane on all of
       them reads as a control panel rather than a choice. What stays in the open is what a person
       picks ONCE and can see the effect of at a glance — the typeface, the accent colour. What
       folds away is the millimetre work: eight or nine sliders for sizes, leading and gaps, each
       a refinement of a layout that already made a considered choice. Nothing is removed; the
       pane simply stops opening on every dial it owns. */
    const dial = t => {
      const value = theme[t.key];
      const id = 'rb-t-' + t.key;
      if (t.kind === 'range') {
        return '<div class="rb-field rb-range"><label for="' + id + '">' + esc(t.label) +
          ' <b>' + esc(value) + esc(t.unit || '') + '</b></label>' +
          '<input type="range" id="' + id + '" data-token="' + t.key + '" min="' + t.min +
          '" max="' + t.max + '" step="' + t.step + '" value="' + esc(value) + '"></div>';
      }
      if (t.kind === 'color') {
        return '<div class="rb-field rb-colour"><label for="' + id + '">' + esc(t.label) +
          '</label><input type="color" id="' + id + '" data-token="' + t.key + '" value="' +
          esc(value) + '"></div>';
      }
      if (t.kind === 'select') {
        return '<div class="rb-field"><label for="' + id + '">' + esc(t.label) + '</label>' +
          '<select id="' + id + '" data-token="' + t.key + '">' + t.options.map(([v, l]) =>
            '<option value="' + esc(v) + '"' + (value === v ? ' selected' : '') + '>' + esc(l) +
            '</option>').join('') + '</select></div>';
      }
      return '';
    };
    const fine = lay.tunables.filter(t => t.kind === 'range');
    for (const t of lay.tunables) if (t.kind !== 'range') out.push(dial(t));
    if (fine.length) {
      /* A real <details>, so the browser owns the open state and the keyboard behaviour. It stays
         in the DOM either way, which is what keeps the delegated `data-token` listeners wired. */
      out.push('<details class="rb-fine"' + (fineOpen ? ' open' : '') + '><summary>Fine tuning' +
        '<span class="note"> \u2014 ' + fine.length + ' sizes and spacings</span></summary>' +
        fine.map(dial).join('') + '</details>');
    }
    out.push('<div class="rb-actions"><button class="ghost rb-mini" id="rb-theme-reset">' +
      'Back to the layout’s own settings</button></div>');
    el('rb-pane-design').innerHTML = out.join('');
    /* `toggle` does not bubble, so this cannot be delegated with the rest of the pane. */
    const fold = el('rb-pane-design').querySelector('details.rb-fine');
    if (fold) fold.addEventListener('toggle', () => { fineOpen = fold.open; });
  }

  /** Every finding the current layout's rules produce. The running lives in ResumeLayouts so it
   *  is testable without a DOM; this is only the lookup. */
  function findings() {
    const d = view();
    const lay = layout();
    return d && lay ? Layouts.runRules(lay, d) : [];
  }

  /* The words the starter document writes for you — its section titles — as plain text, so
     "has anything been typed" is a comparison against what the tab handed you rather than a list
     of fields to keep in step with each layout's `starter()`. Built once per layout. */
  const starterText = new Map();
  function untouched(d) {
    if (!starterText.has(d.layoutId)) {
      starterText.set(d.layoutId, Export.plainText(startDocument(d.layoutId, false)));
    }
    return Export.plainText(d) === starterText.get(d.layoutId);
  }

  /** Split a group's messages into the tail they all share and the part each one owns.
   *
   *  A rule that fires twenty times states its explanation twenty times, verbatim: `result`
   *  fired 24 times and `opening-verb` 20 on a résumé of four jobs, and the panel was 7,598px
   *  tall because 44 copies of a 150-character paragraph were in it. The shared tail is printed
   *  once under the group's heading and what is left on each row is the part that is genuinely
   *  about that block — the quoted word `opening-verb` opens with, and nothing where every
   *  message is identical. Split on spaces so the cut can never fall inside a word.
   */
  function sharedTail(messages) {
    const words = messages.map(m => String(m).split(' '));
    let n = 0;
    while (words.every(w => n < w.length &&
      w[w.length - 1 - n] === words[0][words[0].length - 1 - n])) n++;
    return {
      tail: words[0].slice(words[0].length - n).join(' '),
      heads: words.map(w => w.slice(0, w.length - n).join(' ')),
    };
  }

  /** What to call the block a finding is about. Nothing in this panel named one, so "24 bullets
   *  have no result" was 24 identical rows and no way to tell which bullet each meant. A block's
   *  own opening words are how somebody recognises their own sentence; the kind of block is the
   *  fallback for one that has not been written yet. */
  function findingWhere(d, nodeId) {
    const node = nodeId ? Doc.find(d, nodeId) : null;
    const spec = node ? Components.get(node.type) : null;
    if (!node || !spec) return 'The résumé';
    return rowTitle(d, spec, node);
  }

  /** Every finding of one rule, as one block: the rule named once, a count, the explanation
   *  once, and one row per block that broke it. */
  function findingGroupHtml(d, rule, list) {
    const split = sharedTail(list.map(f => f.message));
    /* The group wears the worst level in it, so a rule holding one error among its notes is not
       drawn as a note. */
    const worst = list.some(f => f.level === 'error') ? 'error'
      : list.some(f => f.level === 'warn') ? 'warn' : 'note';
    return '<section class="rb-fgroup rb-' + esc(worst) + '">' +
      '<h3 class="rb-fgroup-head">' + esc(rule) +
      '<span class="rb-fcount">' + list.length + '</span></h3>' +
      (split.tail ? '<p class="rb-fgroup-why">' + esc(split.tail) + '</p>' : '') +
      list.map((f, i) =>
        '<button class="rb-finding rb-' + esc(f.level) + '"' +
        (f.nodeId ? ' data-select="' + esc(f.nodeId) + '"' : ' disabled') + '>' +
        '<span class="rb-finding-where">' + esc(findingWhere(d, f.nodeId)) + '</span>' +
        (split.heads[i]
          ? '<span class="rb-finding-msg">' + esc(split.heads[i]) + '</span>' : '') +
        '</button>').join('') +
      '</section>';
  }

  function checksPane() {
    const found = findings();
    const lay = layout();
    const out = ['<p class="note">Checked against the ' + esc(lay.label) +
      ' layout’s own rules. Advice, not locks — the page prints either way.</p>'];
    if (untouched(view())) {
      out.push('<p class="note">These start when you start writing. An empty page breaks nearly ' +
        'every rule here, and saying so before you have typed a word is noise, not advice.</p>');
      el('rb-pane-checks').innerHTML = out.join('');
      return;
    }
    if (!found.length) {
      out.push('<p class="rb-clear">Nothing to flag. Every rule this layout states is met.</p>');
    } else {
      /* Grouped by rule, in the order `runRules` hands them over — worst level first — so the
         group a reader must act on is the group at the top. No order is invented here. */
      const order = [];
      const byRule = new Map();
      for (const f of found) {
        if (!byRule.has(f.ruleId)) { byRule.set(f.ruleId, []); order.push(f.ruleId); }
        byRule.get(f.ruleId).push(f);
      }
      const d = view();
      out.push(order.map(id =>
        findingGroupHtml(d, byRule.get(id)[0].rule, byRule.get(id))).join(''));
    }
    el('rb-pane-checks').innerHTML = out.join('');
  }

  /* What the Checks tab last said. `paint()` runs on every keystroke, so announcing the verdict
     unconditionally would read the whole panel out again on each character typed; only a change
     of verdict is news. */
  let lastVerdict = null;

  function badgePaint() {
    const found = findings();
    const blank = untouched(view());
    const n = blank ? 0 : found.filter(f => f.level !== 'note').length;
    const notes = blank ? 0 : found.length - n;
    /* Two badges for one number, and both are needed: the Checks panel is beside the form, so
       the count has to ride the Edit segment tab as well — otherwise a finding raised while
       somebody was looking at the page would be silent, which is exactly what a persistent
       indicator exists to prevent. */
    for (const id of ['rb-badge', 'rb-badge-checks']) {
      const badge = el(id);
      if (!badge) continue;
      badge.hidden = n === 0;
      /* The number is the badge; the words beside it are only spoken. Without them the tab reads
         as "Checks 3" and a screen reader user has to guess what the 3 counts. */
      badge.innerHTML = String(n) + '<span class="rb-vh"> to fix</span>';
    }
    const verdict = blank ? 'Checks: waiting for the first words.'
      : n === 0 && notes === 0
      ? 'Checks: nothing to flag.'
      : 'Checks: ' + n + ' to fix' +
        (notes ? ', ' + notes + ' note' + (notes === 1 ? '' : 's') : '') + '.';
    /* The badge changing and the findings list being rewritten were both silent: the panel is
       the one part of this builder that tells you something you did not already know, and it
       announced nothing at all. */
    if (verdict !== lastVerdict) { lastVerdict = verdict; announce(verdict); }
  }

  /* ---- the split ----------------------------------------------------------------------------
     Edit and Preview (ADR-0128), and ONE strip where there were two nested ones. The four panes
     this replaces were a segment (Document / Polish) with a tablist inside it (Design / Checks /
     Keywords), which is two levels of tabs to reach the layout picker; a third level on top of
     that is what adding Edit / Preview naively would have cost. Instead Design moved to the
     Preview segment, where what it changes is what you are looking at, and Checks and Keywords
     moved into a column beside the form, where the block they name is what you fix. So there is
     no nesting left at all.

     Still a real tablist: roving tabindex and arrow traversal, automatic activation, and
     `aria-selected` rather than the `aria-current="page"` this was once written with — that says
     "navigation", which is the wrong thing, and it cost the panel-to-tab association a tablist
     gets for free. */

  const SEGMENTS = ['edit', 'preview'];

  let segment = 'edit';

  /** Show one segment. `moveFocus` for a keyboard traversal, where focus must follow the
   *  selection; a click has already put focus where it belongs. */
  function showSegment(name, moveFocus) {
    if (SEGMENTS.indexOf(name) < 0) return;
    segment = name;
    let picked = null;
    for (const b of el('rb-seg').children) {
      const on = b.dataset.seg === name;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      /* Roving tabindex: the whole strip is one tab stop rather than one per button, which is
         what the arrow keys are for. */
      b.tabIndex = on ? 0 : -1;
      if (on) picked = b;
    }
    for (const one of SEGMENTS) el('rb-pane-' + one).hidden = one !== name;
    if (moveFocus && picked && picked.focus) picked.focus();
    measureShown();
  }

  /* A HIDDEN ELEMENT MEASURES ZERO, and both segments hold something this editor measures: the
     sheet in Preview, the miniature in Edit. Every reading taken while its segment is off screen
     is a zero — `fitToWidth` divides by it, `pageBreaks` refuses to draw on it, and the wrapper
     reserves no height for it. So whatever just became visible is painted again, and the sheet is
     fitted the first time it is genuinely on screen rather than at load.

     Fitting is once, painting is every time. Re-fitting on every switch would throw away a zoom
     the user had chosen; not painting would leave the tenth switch showing what the first one
     measured, which for the page-break markers means showing nothing. */
  let fitted = false;
  function measureShown() {
    if (!store || !store.get()) return;
    if (segment === 'preview' && !fitted) { fitted = true; fitToWidth(); }
    paint();
  }

  /* ---- the template gallery -----------------------------------------------------------------
     A picker that shows the templates instead of naming them, taking the stage over rather than
     dropping a list of words out of the chrome. Two reasons it is not a <select>. People choose
     a résumé template by looking at it — a line of text cannot say what "two column" does to
     their own bullets. And a list of names hides the one thing a chooser most needs to be told
     at the moment of choosing: that a handsome coloured sidebar is the template an ATS parses
     worst. That warning is the Layout's own `blurb`, so the card carries it.

     Each card is the CURRENT DOCUMENT rendered through that layout — not a stock thumbnail — so
     what the card shows is what picking it gives you, page-break line included. That is only
     affordable because a Layout's render is a pure string function (resume_layouts.js): one
     render per registered layout, and only while the gallery is open. The grid wraps and reads
     every card from the registry, so it neither knows nor cares how many there are.

     SEVEN are registered as this is written, and ADR-0125 guessed the wrong cost for that. It
     called the gallery O(layouts) full document renders and named ~30 as the point where
     thumbnails would need caching or virtualising. Measured in Chromium at 1280x800 on the worked
     example, the seven cards open in 15.3ms — of which the seven renders are 0.6ms. The render is
     not the cost; the forced synchronous layout for MEASURING each sheet is (`fitSheet`), and at
     ~2.1ms a card the frame budget is not in danger until well past thirty. */

  let templatesOpen = false;

  /** The sheet is drawn at its true width and scaled, exactly as the real preview is, because a
   *  miniature that reflowed to fit its card would misrepresent the very thing it is shown for:
   *  where the lines break. The gallery's cards are a fixed width; the Edit segment's single
   *  miniature measures its own frame instead. */
  const MINI_WIDTH = 190;

  /** Scale one true-width sheet into a fixed-width frame.
   *
   *  MEASURED, not computed from the page numbers. `page.unit` is whatever the layout declared —
   *  every one registered today says `in`, and a `MINI_WIDTH / (page.width * 96)` shortcut quietly
   *  assumed that; a layout declaring mm would have rendered its miniature about 25x off. The
   *  browser already knows how wide `210mm` is, so this asks it. Same reason `pxPerInch()`
   *  measures the real paper rather than trusting the same arithmetic.
   *
   *  Returns false when the sheet measures zero, which is what a sheet inside a hidden segment
   *  does — the caller has nothing to draw and must be painted again when its segment opens. */
  function fitSheet(sheetEl, width) {
    const natural = sheetEl.offsetWidth;
    if (!natural || !width) return false;
    const scale = width / natural;
    sheetEl.style.transform = 'scale(' + scale.toFixed(4) + ')';
    /* `transform` does not affect layout, so the frame has to reserve the scaled height
       itself — the same thing `paint()` does for the real preview's wrapper. */
    sheetEl.parentElement.style.height = Math.round(sheetEl.offsetHeight * scale) + 'px';
    return true;
  }

  /* ---- the miniature, in the Edit segment ---------------------------------------------------
     What a live preview beside a form is actually consulted for while somebody is typing is two
     questions: did my words land on the page, and does it still fit on one. Both survive being
     small, and this is the real document through the real layout — the same pure render function
     the page and the gallery use — so it cannot drift from the sheet it stands in for.

     It is the deliberate softening of what ADR-0128 gives up. The page itself is one click away
     and this card is the click. */

  function miniPaint() {
    const d = doc();
    const lay = layout();
    const sheet = el('rb-mini-sheet');
    if (!d || !lay || !sheet || el('rb-mini').hidden) return;
    const page = Layouts.pageFor(lay, d);
    sheet.style.width = page.width + page.unit;
    sheet.style.minHeight = page.height + page.unit;
    sheet.style.padding = page.margin + page.unit;

    let sheetCss = el('rb-mini-css');
    if (!sheetCss) {
      sheetCss = document.createElement('style');
      sheetCss.id = 'rb-mini-css';
      document.head.appendChild(sheetCss);
    }
    sheetCss.textContent = lay.css(Layouts.themeFor(lay, d), '#rb-mini-sheet .rb-doc');
    sheet.innerHTML = Layouts.renderDocument(lay, view());

    /* As wide as the column gives it, MEASURED — the card is ~276px at 1280 and a fixed 190
       would have thrown away a third of the only look at the page this segment offers. Zero is
       what the frame measures while the Edit segment is off screen, and `fitSheet` refuses it:
       nothing below here reads correctly then, and `measureShown` paints again on the way in. */
    if (!fitSheet(sheet, sheet.parentElement.offsetWidth)) return;
    const breaks = paintPageBreaks(sheet, page);
    el('rb-mini-pages').textContent = breaks.length
      ? (breaks.length + 1) + ' pages'
      : 'Fits on one page';
  }

  function templatesPaint() {
    const d = doc();
    const shown = view();
    const grid = el('rb-template-grid');
    const cards = [];
    const sheets = [];
    Layouts.all().forEach((lay, i) => {
      const page = Layouts.pageFor(lay, d);
      /* An index, not the layout's id: this string is interpolated straight into a CSS selector,
         and an id is only guaranteed to be a registry key, not a valid one. */
      const miniId = 'rb-tmini-' + i;
      sheets.push(lay.css(Layouts.themeFor(lay, d), '#' + miniId + ' .rb-doc'));
      const on = lay.id === d.layoutId;
      cards.push('<div class="rb-tcard' + (on ? ' on' : '') + '">' +
        '<button class="rb-tpick" data-layout="' + esc(lay.id) + '" aria-pressed="' +
        (on ? 'true' : 'false') + '">' +
        '<span class="rb-tmini">' +
        '<span class="rb-tsheet" id="' + miniId + '" aria-hidden="true" style="width:' +
        page.width + page.unit + '; min-height:' + page.height + page.unit + '; padding:' +
        page.margin + page.unit + '">' +
        Layouts.renderDocument(lay, shown) + '</span></span>' +
        '<span class="rb-tname">' + esc(lay.label) +
        (on ? '<span class="rb-tag">in use</span>' : '') +
        '<span class="rb-tpages" id="rb-tpages-' + i + '"></span></span>' +
        (lay.summary ? '<span class="note">' + esc(lay.summary) + '</span>' : '') +
        (lay.blurb ? '<span class="note rb-tblurb">' + esc(lay.blurb) + '</span>' : '') +
        (lay.credit ? '<span class="note rb-credit">' + esc(lay.credit) + '</span>' : '') +
        '</button></div>');
    });
    grid.innerHTML = cards.join('');

    /* One <style> for every miniature, replaced wholesale. Appending would leave the previous
       gallery's rules live over the next one's cards. */
    let sheet = el('rb-tsheets');
    if (!sheet) {
      sheet = document.createElement('style');
      sheet.id = 'rb-tsheets';
      document.head.appendChild(sheet);
    }
    sheet.textContent = sheets.join('\n');

    /* Scaled after the sheets are in the document, because the scale is measured off them —
       see `fitSheet` — and then counted.

       The gallery's copy used to promise "the dashed page-break line tells you if the new one
       runs onto a second sheet" and `templatesPaint` never called the break painter: nine cards,
       zero lines. Drawing them would not have been much of an answer either — at a 190px card a
       hairline is all but invisible, and it arrives carrying a "Page 2" label of its own — so
       this uses `pageBreaks`, which only MEASURES, and the card states the count in words. The
       template's copy promises that and nothing else. */
    Layouts.all().forEach((lay, i) => {
      const sheetEl = el('rb-tmini-' + i);
      const label = el('rb-tpages-' + i);
      if (!sheetEl || !label || !fitSheet(sheetEl, MINI_WIDTH)) return;
      const breaks = pageBreaks(sheetEl, Layouts.pageFor(lay, d));
      label.textContent = breaks.length ? (breaks.length + 1) + ' pages' : '1 page';
    });
  }

  function showTemplates(on) {
    templatesOpen = on;
    el('rb-templates').hidden = !on;
    el('rb-paper-wrap').hidden = on;
    el('rb-stage-foot').hidden = on;
    /* The column beside the page goes with it. The gallery takes the stage over (ADR-0125), and
       the panel that column holds — Keywords — measures the sheet the gallery has just hidden;
       leaving it reachable there is the same zero-measurement bug by another door. */
    el('rb-preview-aside').hidden = on;
    if (on) {
      templatesPaint();
      const close = el('rb-templates-close');
      if (close && close.focus) close.focus();
    }
  }

  /* ---- keyword coverage -------------------------------------------------------------------
     The guide's first instruction, and the one people skip: collect the qualifications from ten
     to fifteen real postings for one job title, then get three quarters of them into the first
     half of the first page. This checks the second half of that — the collecting is still work
     only a person can do. */

  function keywordCheck() {
    const d = view();
    const lay = layout();
    const raw = el('rb-kw').value || '';
    const terms = raw.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    const out = el('rb-kw-out');
    if (!terms.length) {
      out.innerHTML = '';
      el('rb-kw-summary').textContent = '';
      /* Emptying the box really does clear the marks now. The comment below said so while this
         branch returned before either clearing the terms or repainting, so the previous check's
         highlights stayed on the page with nothing left beside them to explain them. */
      keywordTerms = [];
      paint();
      return;
    }
    const text = Export.plainText(d);

    /* "The first half of the first page" is measured on the PAGE, in inches, against the
       rendered document — not as a share of the whole text. The share version scored a hit
       halfway down page three of a three-page résumé as "near the top", which is the opposite
       of what the guide asks for. This is exact now that the preview is dimensionally the
       printed page. */
    const page = Layouts.pageFor(lay, d);
    const halfOfPageOne = (page.height - 2 * page.margin) / 2;
    const ppi = pxPerInch();
    const paper = el('rb-paper');
    const origin = paper.querySelector('.rb-doc');
    const blocks = [];
    if (origin && ppi) {
      const top0 = origin.getBoundingClientRect().top;
      paper.querySelectorAll('[data-node]').forEach(node => {
        blocks.push({
          text: node.textContent || '',
          top: (node.getBoundingClientRect().top - top0) / ppi,
        });
      });
    }
    /* Both questions below go through the SAME matcher the on-page highlight uses
       (`resume_decorators.js`). They were two substring tests, and a substring answers yes to
       "does JavaScript mention Java" — so `R`, `C` and `AI` were reported present in a résumé
       containing none of them as terms, while `C++` was reported missing from one the page was
       marking `C` all over. Two implementations of one question disagreed; now there is one. */

    /** How far down the page a term first appears, in inches, or null if it is absent. */
    const firstAt = term => {
      let best = null;
      for (const b of blocks) {
        if (Decorators.mentions(b.text, term) && (best === null || b.top < best)) best = b.top;
      }
      return best;
    };

    const rows = terms.map(term => {
      const at = firstAt(term);
      return { term, found: Decorators.mentions(text, term), early: at !== null && at < halfOfPageOne };
    });
    /* Repaint so the page marks them, NOW. This set the terms and returned: measured straight
       after clicking Check coverage the summary read "2 of 2 present · 100% in the top half"
       with zero <mark> elements on the sheet, and typing one space into the name box then
       produced five. A highlight that arrives on an unrelated keystroke is worse than none. */
    keywordTerms = terms;
    paint();
    const hits = rows.filter(r => r.found).length;
    const earlyHits = rows.filter(r => r.early).length;
    el('rb-kw-summary').textContent = hits + ' of ' + rows.length + ' present · ' +
      Math.round((earlyHits / rows.length) * 100) + '% in the top half of page one ' +
      '(the guide asks for 75%)';
    out.innerHTML = rows.map(r =>
      '<div class="rb-kwrow rb-kw-' + (r.early ? 'early' : r.found ? 'late' : 'missing') + '">' +
      '<span>' + esc(r.term) + '</span><span class="rb-kwtag">' +
      (r.early ? 'top half, page one' : r.found ? 'present, further down' : 'missing') +
      '</span></div>').join('');
  }

  /* ---- documents -------------------------------------------------------------------------- */

  function startDocument(layoutId, filled) {
    const lay = Layouts.get(layoutId) || Layouts.all()[0];
    const b = Doc.builder().usingLayout(lay.id)
      .named(filled && lay.example ? 'Example résumé' : 'Untitled résumé');
    (filled && lay.example ? lay.example : lay.starter)(b);
    return b.build();
  }

  function openDocument(id) {
    const loaded = repository.get(id);
    if (!loaded) return;
    selectedId = null;
    store.adopt(loaded);
    repository.setLastOpened(id);
    /* Through the shared close, so `aria-expanded` goes with it. Hiding the panel alone left the
       button still telling a screen reader the menu was open. */
    closePopovers();
  }

  /* Which row, if any, is currently asking to be confirmed. The confirmation used to be a
     `window.confirm`; it is the row itself now, which is also the only place that can show you
     WHICH résumé you are about to lose. */
  let askDocDrop = null;

  /* The old sentence — "only stored in this browser" — is false for a résumé the Account
     switched sync on for, and a delete confirmation is the worst place to be wrong about where
     a thing lives. Read from the account list, not from the open document: this row may be some
     other résumé. */
  const onAccount = id => !!sync && sync.rows().some(r => r.id === id);

  function docListPaint() {
    const rows = repository.list();
    const current = doc();
    if (askDocDrop && !rows.some(r => r.id === askDocDrop)) askDocDrop = null;
    el('rb-doclist').innerHTML = rows.length ? rows.map(r =>
      '<div class="rb-docrow' + (current && r.id === current.id ? ' on' : '') + '">' +
      '<button class="rb-docopen" data-open="' + esc(r.id) + '">' + esc(r.name || 'Untitled') +
      '<span class="note">' + esc((Layouts.get(r.layoutId) || {}).label || r.layoutId) + ' · ' +
      esc(String(r.updatedAt || '').slice(0, 10)) +
      (onAccount(r.id) ? ' · on your account' : '') + '</span></button>' +
      (askDocDrop === r.id
        ? '<span class="rb-confirm rb-confirm-row"><span class="note">' +
          esc(onAccount(r.id)
            ? 'Delete? It goes from this browser and from your account, and cannot be undone.'
            : 'Delete? It is only in this browser, so this cannot be undone.') + '</span>' +
          '<button class="ghost rb-mini danger" data-drop-yes="' + esc(r.id) + '">Delete</button>' +
          '<button class="ghost rb-mini" data-drop-no="' + esc(r.id) + '">Keep</button></span>'
        : '<button class="ghost rb-mini danger" data-drop="' + esc(r.id) +
          '" title="Delete" aria-label="Delete ' + esc(r.name || 'Untitled') + '">×</button>') +
      '</div>').join('') : '<p class="note">Nothing saved yet.</p>';

    /* The other half of the list, and the reason the account copy is worth having at all: a
       résumé this browser has never seen. On a new machine, or after a cleared cache, the
       local list above is empty and these are the only rows there are — so leaving them out
       would have made "synced" true and useless in the same change. */
    const here = new Set(rows.map(r => r.id));
    const elsewhere = (sync ? sync.rows() : []).filter(r => !here.has(r.id));
    el('rb-doclist-remote').innerHTML = elsewhere.length
      ? '<p class="rb-doclist-head">On your account, not on this device</p>' + elsewhere.map(r =>
        '<button class="rb-docopen" data-pull="' + esc(r.id) + '">' + esc(r.name || 'Untitled') +
        '<span class="note">' + esc(String(r.updatedAt || '').slice(0, 10)) +
        ' · open a copy here</span></button>').join('')
      : '';

    /* What is true right now for THIS résumé, never a general claim about the product. The
       wording before this said "nothing here is uploaded" and, later, that account saving was
       "planned" — both were accurate when written and both stop being true the moment the
       switch above exists, which is the worst kind of stale sentence to leave in a privacy
       claim. */
    /* The last clause is only offered where it is available: a signed-out session, or a
       deployment with no account store, would otherwise be told to use a switch that is
       disabled or absent. */
    const offer = sync && sync.status().state === 'ready'
      ? ' — or switch on "Keep a copy on my account" above for this résumé.' : '';
    const kept = current && current.sync
      ? 'Saved in this browser, and a copy is kept on your account because you switched it on ' +
        'for this résumé. Turning it off, or deleting the résumé, removes that copy at once — ' +
        'and it is out of the backups within 30 days.'
      : 'Saved in this browser and nowhere else. Clearing site data deletes it, so keep a JSON ' +
        'backup' + (offer || '.');
    el('rb-storage').textContent = repository.durable ? kept
      : 'This browser is blocking storage, so nothing is being kept. Download a JSON backup before you leave.';
    syncPaint();
  }

  /* ---- the account copy (ADR-0124) ---------------------------------------------------------
     Every path here is best-effort and none of it blocks the editor: a signed-out session, a
     deployment with no account store, or an unreachable Hub leaves the tab exactly as ADR-0123
     shipped it, with the browser copy authoritative. */

  /** How long ago, in the coarsest honest unit — a status line nobody reads twice. */
  function ago(at) {
    const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
    if (seconds < 60) return 'just now';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + ' min ago';
    const hours = Math.round(minutes / 60);
    return hours < 24 ? hours + 'h ago' : Math.round(hours / 24) + 'd ago';
  }

  function syncPaint() {
    const box = el('rb-account');
    if (!box || !sync) return;
    const d = doc();
    const status = sync.status();
    const on = !!(d && d.sync);
    const toggle = el('rb-sync');
    toggle.checked = on;
    /* Signed out is the one state where the control must refuse rather than pretend. It is
       disabled AND says why: a switch that flips and then silently stores nothing is the
       dishonest failure this whole feature has to avoid. */
    toggle.disabled = status.state === 'signed-out';
    el('rb-sync-now').hidden = !on || status.state !== 'ready';
    el('rb-sync-state').textContent =
      status.state === 'signed-out' ? 'Sign in to use this'
      : !on ? ''
      : status.error ? 'Not saved — ' + status.error
      : status.at ? 'Saved ' + ago(status.at)
      : 'Saving…';
  }

  /** A word in the bar. `sticky` is for the things the user must not miss — a refused write
   *  stays put rather than fading after two seconds like a confirmation. */
  function flashSaved(message, sticky) {
    const box = el('rb-saved');
    box.textContent = message;
    box.classList.toggle('rb-saved-bad', !!sticky);
    clearTimeout(savedTimer);
    if (!sticky) savedTimer = setTimeout(() => { box.textContent = ''; }, 2600);
  }

  /* ---- profile prefill ---------------------------------------------------------------------
     The Profile deliberately holds no contact details (ADR-0041), so this can fill the career
     facts and nothing else. Saying so beats a button that silently leaves the name blank. */

  async function prefill() {
    const note = el('rb-prefill-note');
    note.textContent = 'Reading…';
    let profile = null;
    try {
      const r = await fetch('/profile');
      if (r.ok) profile = await r.json();
    } catch (err) { profile = null; }
    if (!profile || profile.error) {
      note.textContent = 'No profile available here.';
      return;
    }
    const d = doc();
    /* Through the active version, like every other edit — filling from the profile while a
       version is on screen used to write straight to the master, which is the one place in the
       editor where what you saw change was not what you were editing. */
    const into = activeTailoring();
    const header = Doc.flatten(d).find(n => n.type === 'header');
    /* Only when it is empty. This line means work authorisation AND city — "US Citizen in Los
       Angeles" — and the Profile holds only the city. Overwriting a filled line with half of what
       it is for replaced the recruiter-facing half with nothing, and the `contact` rule checks
       only that the line is non-empty, so nothing would have said so. */
    if (header && profile.location && !String(Doc.contentOf(d, header.id).locationLine || '').trim()) {
      store.dispatch(Cmd.setContentFor(header.id, { locationLine: profile.location }, into));
    }
    if (profile.skills) {
      const line = Doc.flatten(d).find(n => n.type === 'skills_line');
      if (line) store.dispatch(Cmd.setContentFor(line.id, { value: profile.skills }, into));
      else {
        store.dispatch(Cmd.addNode(null, 'skills_line'));
        const added = Doc.flatten(doc()).filter(n => n.type === 'skills_line').pop();
        if (added) {
          store.dispatch(Cmd.setContentFor(added.id, { label: 'Skills', value: profile.skills }, into));
        }
      }
    }
    if (profile.education) {
      const entry = Doc.flatten(doc()).find(n => n.type === 'education_entry');
      if (entry) store.dispatch(Cmd.setContentFor(entry.id, { credential: profile.education }, into));
    }
    if (profile.title) {
      const job = Doc.flatten(doc()).find(n => n.type === 'work_entry');
      if (job) store.dispatch(Cmd.setContentFor(job.id, { role: profile.title }, into));
    }
    note.textContent = 'Filled what the profile holds. It keeps no contact details, so name, ' +
      'phone and email are still yours to type — and add your work authorisation in front of the ' +
      'city, which is the half of that line a recruiter screens on.';
  }

  /* ---- wiring ------------------------------------------------------------------------------ */

  /* The two menus over the bar, as [button, popover] — one list, so opening one, closing both and
     dismissing on an outside click cannot drift apart. */
  const POPOVERS = [['rb-open', 'rb-pop-open'], ['rb-download', 'rb-pop-download'],
    ['rb-version-new', 'rb-pop-version']];

  /** Shut every popover. Returns the id of the button whose menu was open, so Escape can put the
   *  focus back where the user left it rather than on whatever the page had. */
  function closePopovers() {
    let wasOpen = null;
    for (const [buttonId, popId] of POPOVERS) {
      if (!el(popId).hidden) wasOpen = buttonId;
      el(popId).hidden = true;
      el(buttonId).setAttribute('aria-expanded', 'false');
    }
    return wasOpen;
  }

  function wire() {
    const paper = el('rb-paper');
    paper.addEventListener('pointerdown', onPointerDown);

    /* Putting the miniature away. A per-viewer convenience, so it lives in localStorage rather
       than in the document — it is not a property of the résumé and must not travel with it to
       another machine. Every read and write is guarded: a private window exposes the object and
       throws on use, which is the same trap `ResumeRepository.detect` probes for. */
    el('rb-mini-toggle').addEventListener('click', () => {
      const hide = !el('rb-mini').hidden;
      showMini(!hide);
      /* `miniPaint` refuses to draw into a hidden frame, so bringing it back has to ask for the
         paint it skipped — otherwise the miniature returns as an empty sheet and stays that way
         until some unrelated edit repaints it. */
      if (!hide) miniPaint();
      try { window.localStorage.setItem(MINI_OFF, hide ? '1' : ''); } catch (err) { /* not fatal */ }
    });

    el('rb-name').addEventListener('input', e => store.dispatch(Cmd.rename(e.target.value), 'name'));

    /* The gallery takes the stage over and hands it back. Both triggers carry `data-act` rather
       than an id, so the Design pane's card and the strip under the page reach one handler
       instead of duplicating it — the panes are rebuilt from innerHTML, so a listener bound to a
       control inside one would be replaced along with it. */
    el('rb-templates').addEventListener('click', e => {
      const pick = e.target.closest('[data-layout]');
      if (!pick) return;
      selectedId = null;
      changeLayout(pick.dataset.layout);
      showTemplates(false);
      fitToWidth();
      paint();
    });
    el('rb-templates-close').addEventListener('click', () => showTemplates(false));
    /* The strip under the page used to need its own listener for the template chip, because the
       delegated one was bound to the rail and the chip was not in it. The delegated listener now
       covers the whole tab and already answers `data-act="templates"`, so a second one here would
       open the gallery twice. */
    el('rb-zoom-fit').addEventListener('click', () => { zoomChosen = false; fitToWidth(); paint(); });

    /* The miniature IS the way through to the page — a segment nobody finds is a segment that
       hides the résumé. Its own click, not `data-seg` on the strip, because the button is not in
       the strip and a roving tabindex is not what a card wants. */
    el('rb-mini').addEventListener('click', () => showSegment('preview', true));

    /* Keywords is the one thing in that column folded away: it is a task somebody opts into once
       per application rather than something to read while writing. */
    el('rb-kw-toggle').addEventListener('click', e => {
      const open = el('rb-pane-keywords').hidden;
      el('rb-pane-keywords').hidden = !open;
      e.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    el('rb-version').addEventListener('change', e => {
      selectedId = null;
      askVersionDrop = false;
      store.dispatch(Cmd.activateTailoring(e.target.value || null));
    });
    /* Naming a version was a `window.prompt`: a modal box drawn outside the page, unstyled, and
       out of the reading order a screen-reader user is in. It is a field in the panel now, in
       the same popover shape the other two menus use — so Escape and a click outside dismiss it
       like everything else here. */
    const createVersion = () => {
      const name = (el('rb-version-name').value || '').trim();
      selectedId = null;
      closePopovers();
      /* `closePopovers` only hides, and the focus was inside what it hid — so without this it
         lands on <body>, which is the reading-order complaint that took the prompt out in the
         first place. Back to the button that opened it. */
      const opener = el('rb-version-new');
      if (opener.focus) opener.focus();
      /* The Job this version is for, where the visitor picked one (CONTEXT.md — **Tailoring**).
         The Tailoring keeps the id and its own name, and nothing else about the job: the name is
         what still reads once the star is gone, and the Saved job is already a copy taken at star
         time, so a third copy here would be one more thing to keep in step. */
      store.dispatch(Cmd.addTailoring(name || 'Untitled version', versionJob));
    };
    el('rb-version-create').addEventListener('click', createVersion);
    /* Picking a job fills the name rather than creating the version outright. Two reasons, and
       the second is not taste: the free-text field and the picker then converge on one path that
       makes a version, and the name stays editable for the one moment it can be — no control
       renames a Tailoring afterwards. */
    el('rb-version-jobs').addEventListener('click', e => {
      const hit = e.target && e.target.closest && e.target.closest('[data-saved]');
      if (!hit) return;
      const row = (savedJobs() || []).find(j => j.job_id === hit.dataset.saved);
      if (!row) return;
      versionJob = row.job_id;
      const box = el('rb-version-name');
      box.value = versionName(row);
      versionJobsPaint();
      if (box.focus) box.focus();
    });
    /* Enter in the name field is what a text field with one button beside it promises. */
    el('rb-version-name').addEventListener('keydown', e => {
      if (e.key === 'Enter') { if (e.preventDefault) e.preventDefault(); createVersion(); }
    });

    /* And deleting one was a `window.confirm`. Two named buttons in the bar instead: the choice
       is Delete or Keep, which is what the reader has to answer — an OK/Cancel pair makes them
       translate the question before they can. */
    el('rb-version-del').addEventListener('click', () => {
      if (!Doc.tailoringOf(doc())) return;
      askVersionDrop = true;
      versionPaint();
      const yes = el('rb-version-yes');
      if (yes.focus) yes.focus();
    });
    el('rb-version-no').addEventListener('click', () => { askVersionDrop = false; versionPaint(); });
    el('rb-version-yes').addEventListener('click', () => {
      const tailoring = Doc.tailoringOf(doc());
      askVersionDrop = false;
      if (!tailoring) { versionPaint(); return; }
      selectedId = null;
      store.dispatch(Cmd.removeTailoring(tailoring.id));
    });

    el('rb-zoom').addEventListener('input', e => {
      zoom = (+e.target.value) / 100;
      zoomChosen = true;
      paint();
    });

    el('rb-undo').addEventListener('click', () => { selectedId = null; store.undo(); });
    el('rb-redo').addEventListener('click', () => { selectedId = null; store.redo(); });

    /* A popover that says whether it is open. `aria-expanded` on the button that owns it is the
       only thing telling a screen-reader user that "Résumés" reveals a list rather than
       navigating somewhere, and both buttons were silent about it. */
    const popover = (buttonId, popId, before) => el(buttonId).addEventListener('click', () => {
      const pop = el(popId);
      const opening = pop.hidden;
      closePopovers();
      pop.hidden = !opening;
      el(buttonId).setAttribute('aria-expanded', opening ? 'true' : 'false');
      if (opening && before) before();
    });
    popover('rb-open', 'rb-pop-open', docListPaint);
    popover('rb-download', 'rb-pop-download', null);
    popover('rb-version-new', 'rb-pop-version', () => {
      const box = el('rb-version-name');
      box.value = '';
      /* The previous visit's pick goes with the previous visit's name. Left standing, the next
         version — typed out by hand, for another job entirely — would quietly carry the job id of
         the one before it. */
      versionJob = null;
      /* Painted on the way in, not at boot: `/saved` answers after the page settles, and this is
         the moment the list is both wanted and known. */
      versionJobsPaint();
      if (box.focus) box.focus();
    });

    /* A menu you can only close by finding its own button again is a trap, and it became a
       visible one when these moved under the bar: the Résumés panel is 247px tall and covers the
       top of what is behind it, so somebody who opens it and changes their mind has nowhere
       obvious to click. Escape and a click outside both close it.
       `pointerdown`, not `click`, is deliberate — but the OWNING BUTTON is excluded from it,
       because that button decides by reading `pop.hidden` and closing the menu before its own
       click arrived would make it reopen the thing it was asked to close. */
    document.addEventListener('pointerdown', e => {
      const inside = !!e.target && !!e.target.closest &&
        POPOVERS.some(([b, popId]) => e.target.closest('#' + popId) || e.target.closest('#' + b));
      if (!inside) closePopovers();
    });

    /* Two named buttons rather than one behind a confirm() whose OK and Cancel both start a
       résumé. Which one you get is the choice; a dialog that spends it on OK-or-Cancel makes the
       reader translate before they can answer. */
    const startFresh = filled => {
      const lay = layout();
      selectedId = null;
      expanded.clear();
      firstRun = false;
      const fresh = startDocument(lay.id, filled && !!lay.example);
      repository.save(fresh);
      repository.setLastOpened(fresh.id);
      store.adopt(fresh);
      closePopovers();
    };
    el('rb-new').addEventListener('click', () => startFresh(true));
    el('rb-new-blank').addEventListener('click', () => startFresh(false));

    el('rb-doclist-remote').addEventListener('click', e => {
      const pull = e.target.closest('[data-pull]');
      if (!pull || !sync) return;
      flashSaved('Opening from your account…');
      sync.pull(pull.dataset.pull).then(incoming => {
        if (!incoming || !incoming.root) { flashSaved('That résumé could not be read.', true); return; }
        repository.save(incoming);
        openDocument(incoming.id);
        flashSaved('Opened from your account.');
      });
    });

    el('rb-doclist').addEventListener('click', e => {
      const open = e.target.closest('[data-open]');
      const drop = e.target.closest('[data-drop]');
      const keep = e.target.closest('[data-drop-no]');
      const go = e.target.closest('[data-drop-yes]');
      if (open) openDocument(open.dataset.open);
      /* Ask in the row, rather than in a modal box that cannot say which row it means. */
      if (drop) { askDocDrop = drop.dataset.drop; docListPaint(); }
      if (keep) { askDocDrop = null; docListPaint(); }
      if (go) {
        const id = go.dataset.dropYes;
        const kept = onAccount(id);
        askDocDrop = null;
        /* The local delete goes ahead either way — the user asked for it and this browser is
           theirs. But a refused account delete must not be silent: the copy is still up there,
           and the row it leaves behind in "On your account" is the only other sign of it. */
        if (kept) {
          sync.forget(id).then(gone => {
            if (!gone) flashSaved('Still on your account — that did not go through.', true);
            docListPaint();
          });
        }
        repository.remove(id);
        if (doc() && doc().id === id) {
          const next = repository.list()[0];
          if (next) openDocument(next.id);
          else { const fresh = startDocument('headless-headhunter', false); repository.save(fresh); store.adopt(fresh); }
        }
        docListPaint();
      }
    });

    /* The opt-in, per résumé (ADR-0124 decision 2). Turning it ON pushes immediately, so the
       switch means something the moment it is flipped; turning it OFF takes the copy off the
       Account rather than merely stopping future pushes. */
    if (el('rb-account') && sync) {
      el('rb-sync').addEventListener('change', e => {
        const d = doc();
        if (!d) return;
        /* Repainted only once it has settled. Painting straight away redrew the switch from a
           document whose `sync` flag had not changed yet — turning it off, which waits for the
           account copy to really be gone, visibly bounced back on first. */
        sync.setEnabled(d, !!e.target.checked).then(docListPaint);
      });
      /* The explicit "Save to my account" ADR-0124 names. Everything else is coarse and
         automatic; this is the one control that answers "is it up there NOW?". */
      el('rb-sync-now').addEventListener('click', () => {
        const d = doc();
        if (!d || !d.sync) return;
        store.flush();
        sync.note(d);
        sync.flush('save').then(syncPaint);
      });
    }

    el('rb-import').addEventListener('click', () => el('rb-file').click());
    el('rb-file').addEventListener('change', async e => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      try {
        const imported = Export.importJson(await file.text());
        repository.save(imported);
        repository.setLastOpened(imported.id);
        selectedId = null;
        store.adopt(imported);
        docListPaint();
        flashSaved('Imported.');
      } catch (err) {
        /* `window.alert` was the last modal here. The bar already carries every other thing that
           went wrong, and `sticky` keeps this one up rather than fading after two seconds. */
        flashSaved(err.message || 'That file could not be read.', true);
      }
      e.target.value = '';
    });

    el('rb-pop-download').addEventListener('click', e => {
      const button = e.target.closest('[data-fmt]');
      if (!button) return;
      store.flush();
      const format = button.dataset.fmt;
      /* The JSON backup is the WHOLE document — every version, every variant — because it is the
         only copy that exists off this browser. Everything else is the version on screen. */
      const payload = format === 'json' ? doc() : view();
      const by = { pdf: Export.print, word: Export.asWord, text: Export.asText,
        html: Export.asHtml, json: Export.asJson }[format];
      if (by) by(window, payload);
      closePopovers();
    });

    /* One delegated listener for the whole tab: every pane is re-rendered from scratch on every
       change, so a listener bound to a control inside one would be replaced along with it. It was
       bound to the rail while there was a rail; the panes now sit in two workspaces on opposite
       sides of a segment switch, and binding it twice would be one contract kept in two places.
       Nothing in the bar carries the data attributes these handlers read, so widening the target
       adds no reachable case. */
    const panel = el('rb');
    panel.addEventListener('input', e => {
      const t = e.target;
      if (t.dataset.field) {
        const value = t.type === 'checkbox' ? t.checked : t.value;
        /* The card says "make it yours". Once a word has been typed it has been made theirs, so
           it stands down — leaving it up would spend the top of the form on advice already
           taken, and it is the largest thing in the panel. */
        firstRun = false;
        /* Under a version this forks a variant on the first keystroke and writes there after —
           copy-on-write, so tailoring a bullet never edits the master by accident. */
        store.dispatch(
          Cmd.setContentFor(t.dataset.node, { [t.dataset.field]: value }, activeTailoring()),
          'content:' + t.dataset.node + ':' + t.dataset.field);
      } else if (t.dataset.geo) {
        store.dispatch(Cmd.setGeometry(t.dataset.node, { [t.dataset.geo]: +t.value }),
          'geo:' + t.dataset.node + ':' + t.dataset.geo);
        const label = t.parentElement.querySelector('label b');
        const spec = GEOMETRY_LABELS[t.dataset.geo];
        if (label && spec) label.textContent = t.value + spec[1];
      } else if (t.dataset.token) {
        store.dispatch(Cmd.setTheme({ [t.dataset.token]: t.type === 'range' ? +t.value : t.value }),
          'theme:' + t.dataset.token);
        /* The range's own label carries the live value, and the pane is deliberately not being
           rebuilt under the user's thumb — so nudge just that label. */
        const label = t.parentElement.querySelector('label b');
        const tunable = layout().tunables.find(x => x.key === t.dataset.token);
        if (label && tunable) label.textContent = t.value + (tunable.unit || '');
      }
    });
    panel.addEventListener('change', e => {
      const slot = e.target.dataset ? e.target.dataset.slotFor : null;
      if (slot) store.dispatch(Cmd.setSlot(slot, e.target.value));
      if (e.target.id === 'rb-paper-size') store.dispatch(Cmd.setPaper(e.target.value));
      const show = e.target.dataset ? e.target.dataset.show : null;
      if (show) {
        store.dispatch(Cmd.setHidden(show, !e.target.checked, activeTailoring()));
        /* The pane is rebuilt from innerHTML on the repaint, which takes the focused checkbox
           with it — so somebody ticking four projects off in a row would lose the keyboard after
           the first. The tick carries an id for exactly this. */
        const again = el('rb-show-' + show);
        if (again && again.focus) again.focus();
      }
    });
    panel.addEventListener('click', e => {
      const row = e.target.closest('[data-row]');
      const pick = e.target.closest('[data-select]');
      const add = e.target.closest('[data-add]');
      const act = e.target.closest('[data-act]');
      /* Opening a row selects the block it names. The form and the page are one document seen
         twice, so navigating in one has to move the other — the "Outline" this replaces selected
         without opening, and the fields it selected lived somewhere else on the panel. */
      if (row) {
        const key = row.dataset.row;
        if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
        /* A node id or one of this form's synthetic keys — `geo:<id>`, `add:<id>`. Ids are
           `[\w-]` (resume_export.js SAFE_ID), so a colon is the discriminator and cannot collide.
           Keyed on the `geo:` prefix alone before, which made "More block types" select a block
           whose id was the whole key and show nothing. */
        if (key.indexOf(':') < 0) { select(expanded.has(key) ? key : null, false); return; }
        paint();
        return;
      }
      if (pick) { select(pick.dataset.select); scrollPaperTo(pick.dataset.select); }
      if (add) {
        const into = add.dataset.into || null;
        store.dispatch(Cmd.addNode(into, add.dataset.add));
        const parent = into ? Doc.find(doc(), into) : doc().root;
        const created = parent && parent.children[parent.children.length - 1];
        if (created) select(created.id);
      }
      if (act) {
        /* The block the button is ON, not the block that happens to be selected. Every action
           now sits inside the row it acts on, so reading the selection would have made a
           button's meaning depend on something else the user last clicked. */
        const on = act.dataset.node || selectedId;
        const what = act.dataset.act;
        if (what === 'templates') showTemplates(true);
        else if (what === 'prefill') prefill();
        else if (what === 'blank') startFresh(false);
        else if (what === 'startwriting') {
          const header = Doc.flatten(doc()).find(n => n.type === 'header');
          firstRun = false;
          if (header) {
            select(header.id);
            const first = el('rb-f-' + header.id + '-fullName');
            if (first && first.focus) first.focus();
          }
        } else if (what === 'moveup' || what === 'movedown') {
          nudge(on, what === 'moveup' ? -1 : 1);
        } else if (on && what === 'remove') {
          if (selectedId === on) selectedId = null;
          expanded.delete(on);
          store.dispatch(Cmd.removeNode(on));
        } else if (on && what === 'duplicate') {
          store.dispatch(Cmd.duplicateNode(on));
        } else if (on && what === 'unfork') {
          store.dispatch(Cmd.clearVariant(on, activeTailoring()));
        }
      }
      if (e.target.id === 'rb-theme-reset') store.dispatch(Cmd.setTheme(blankTheme()));
      if (e.target.id === 'rb-kw-run') keywordCheck();
    });

    /* The segment strip. Automatic activation — the panel follows focus — is the APG default
       for a set this small with no expensive panel to build, and it is what a mouse user already
       gets. This was once a generic `wireStrip` called twice, over two nested tablists; there is
       one strip now, so it is written once, here. */
    const strip = el('rb-seg');
    strip.addEventListener('click', e => {
      const tab = e.target.closest('[data-seg]');
      if (tab) showSegment(tab.dataset.seg, false);
    });
    strip.addEventListener('keydown', e => {
      const active = document.activeElement;
      const at = SEGMENTS.indexOf(active && active.dataset ? active.dataset.seg : null);
      if (at < 0) return;
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      let to = null;
      if (step != null) to = (at + step + SEGMENTS.length) % SEGMENTS.length;
      else if (e.key === 'Home') to = 0;
      else if (e.key === 'End') to = SEGMENTS.length - 1;
      if (to == null) return;
      e.preventDefault();
      showSegment(SEGMENTS[to], true);
    });

    document.addEventListener('keydown', e => {
      if (el('panel-resume') && el('panel-resume').hidden) return;
      const key = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && key === 'z') {
        e.preventDefault();
        selectedId = null;
        if (e.shiftKey) store.redo(); else store.undo();
      }
      if (e.altKey && (key === 'arrowup' || key === 'arrowdown')) {
        if (nudge(selectedId, key === 'arrowup' ? -1 : 1)) e.preventDefault();
      }
      if (key === 'escape') {
        /* The menu first: it is the thing on top, and Escape means "close what is over me"
           before it means "deselect the block underneath". */
        const owner = closePopovers();
        if (owner) {
          const button = el(owner);
          if (button && button.focus) button.focus();
        } else if (selectedId) select(null);
      }
    });

    /* Anything not yet written is written now, rather than on a timer that the tab closing
       would cancel. */
    window.addEventListener('beforeunload', () => store && store.flush());
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'hidden') return;
      if (store) store.flush();
      /* One of ADR-0124's three coarse events. It is not on `beforeunload`: a push is a network
         round trip a closing page will not finish, and a half-sent commit is worse than a late
         one. Leaving the tab is the moment somebody has stopped typing, and the browser copy is
         already safe by the line above. */
      if (sync) sync.flush('tab hidden');
    });
  }

  const blankTheme = () => {
    const reset = {};
    for (const t of layout().tunables) reset[t.key] = undefined;
    /* undefined does not survive the JSON clone, which is exactly the intent: the override is
       removed rather than set to a literal undefined the stylesheet would print. */
    return reset;
  };

  /** Switching layout, as one undo step: the id, then whatever the incoming layout needs to
   *  make sense of a document written for a different one. Composed here rather than inside
   *  `Cmd.setLayout` so Layer 3 keeps knowing nothing about Layer 2. */
  function changeLayout(layoutId) {
    const lay = Layouts.get(layoutId);
    if (!lay) return;
    store.dispatch(Cmd.setLayout(layoutId, lay.adopt));
    /* Said out loud, because the gallery closes on the choice and the only other confirmation is
       the page itself, which a screen-reader user is not looking at. It also names the way back:
       the switch is one Command, so Undo returns the previous template exactly. */
    announce(lay.label + ' applied. Your words are unchanged; Undo puts the previous template back.');
  }

  /** Zoom so the sheet fits the space it has. A US Letter page is 816 CSS px wide and the stage
   *  is narrower than that on most laptops — and far narrower on a phone. The sheet keeps its
   *  true width (see `flex: 0 0 auto` in resume.css); this is what makes it fit, because scaling
   *  preserves the line breaks the printed page will have and shrinking does not.
   *
   *  The floor is 0.25 rather than 0.5 because a 390px phone needs about 0.42 — clamping higher
   *  left the tab scrolling sideways. Only on the way in; after that the zoom is the user's. */
  /* The room the sheet has, less the gutter its wrapper keeps around it. One spelling, because
     the fit and the "does it still fit" test have to agree about where the edge is. */
  const STAGE_GUTTER = 24;
  const stageRoom = () => el('rb-paper').parentElement.clientWidth - STAGE_GUTTER;

  function fitToWidth() {
    const paper = el('rb-paper');
    const available = stageRoom();
    const natural = paper.offsetWidth;
    if (!available || !natural) return;
    zoom = Math.max(0.25, Math.min(1, Math.floor((available / natural) * 20) / 20));
    el('rb-zoom').value = String(Math.round(zoom * 100));
  }

  /** Does the sheet, at the zoom it is drawn at, still fit the space it has? */
  function overflowsStage() {
    const width = el('rb-paper').offsetWidth;
    return !!width && width * zoom > stageRoom();
  }

  /* Whether the zoom on screen is one the user chose. A fit is the editor's guess and may be
     replaced freely; a number somebody dragged the slider to is theirs, and is only overruled
     when it stops fitting — which is the defect this exists for. */
  let zoomChosen = false;

  /* Refitting when the space changes. `fitToWidth` ran once behind the `fitted` flag and nothing
     watched for a resize, so a tab opened at 1440 and narrowed to 420 drew the sheet 211px wider
     than its wrapper while the readout still said 100%.

     A ResizeObserver on the STAGE, not a `resize` listener on the window: the Edit/Preview split
     hands the whole panel from one segment to the other, and the template gallery takes it over
     and hands it back, so the stage changes width plenty of times the window does not. Debounced,
     because a drag of the window edge fires this on every frame and each pass re-renders the
     sheet. */
  const REFIT_MS = 120;
  let refitTimer = null;
  function watchStage() {
    const wrap = el('rb-paper-wrap');
    if (!wrap || typeof ResizeObserver !== 'function' || !wrap.getBoundingClientRect) return;
    new ResizeObserver(() => {
      clearTimeout(refitTimer);
      refitTimer = setTimeout(() => {
        if (!store || !store.get() || segment !== 'preview' || templatesOpen) return;
        if (zoomChosen && !overflowsStage()) return;
        fitToWidth();
        paint();
      }, REFIT_MS);
    }).observe(wrap);
  }

  /* Reordering by keyboard. Selection was already reachable — the outline rows are buttons — but
     moving a block was a drag and nothing else, which put the one decision this template says
     matters most (what a recruiter reads first) behind a pointer. Alt+Up/Down moves the selected
     block among its siblings; the same Command the drag dispatches, so undo is identical. */
  function nudge(id, direction) {
    const d = doc();
    if (!id || !d) return false;
    const node = Doc.find(d, id);
    const parent = Doc.parentOf(d, id);
    const lay = layout();
    if (!node || !parent || !lay.caps.reorder) return false;
    const spec = Components.get(node.type);
    if (!spec || !spec.caps.reorder) return false;

    const siblings = parent.children;
    const at = siblings.indexOf(node);
    const to = at + direction;
    if (to < 0 || to >= siblings.length) {
      announce(spec.label + ' is already ' + (direction < 0 ? 'first' : 'last') + '.');
      return true;
    }
    store.dispatch(Cmd.moveNode(id, parent === d.root ? null : parent.id, to));
    announce(spec.label + ' moved to position ' + (to + 1) + ' of ' + siblings.length + '.');
    scrollPaperTo(id);
    return true;
  }

  /** Say something to a screen reader without changing what anyone sees. The region itself is
   *  in the template — a live region inserted with its text already in it does not announce in
   *  most screen readers, so building it here on first use would have swallowed the first
   *  thing it ever had to say. This still builds one if it is missing, because the editor is
   *  also driven by tests that mount less than the whole partial. */
  function announce(message) {
    let box = el('rb-live');
    if (!box) {
      box = document.createElement('div');
      box.id = 'rb-live';
      box.className = 'rb-vh';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      box.setAttribute('aria-atomic', 'true');
      el('rb').appendChild(box);
    }
    box.textContent = message;
  }

  /* Two scrollers, named apart on purpose. `scrollToNode` was one function when there was one
     place a block could be shown; the form shows the same block as a row, and a single name
     covering both would be read as whichever one the reader had in mind. */
  function scrollPaperTo(id) {
    const target = el('rb-paper').querySelector('[data-node="' + id + '"]');
    if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function scrollFormTo(id) {
    const pane = el('rb-pane-document');
    /* `data-block`, which every row AND every bullet carries — `data-row` is the toggle on a
       row's own header, and a bullet has no toggle, so keying on it revealed nothing at all for
       the block type that gets edited most. */
    const at = pane && pane.querySelector ? pane.querySelector('[data-block="' + id + '"]') : null;
    if (at && at.scrollIntoView) at.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  /* ---- boot --------------------------------------------------------------------------------- */

  function boot() {
    if (booted || !el('rb')) return;
    booted = true;

    /* The miniature's remembered state, applied before the first paint so it is never drawn
       only to be hidden — and never measured against a hidden frame. */
    let miniOff = false;
    try { miniOff = !!window.localStorage.getItem(MINI_OFF); } catch (err) { miniOff = false; }
    showMini(!miniOff);

    repository = Repo.detect(window);
    /* The account copy, on a deployment that has one. `rb-account` is server-rendered only where
       an Account can actually hold records, so its absence is the whole feature switch — and the
       editor is identical without it. */
    sync = el('rb-account') && AccountSync ? AccountSync.create({
      repository,
      live: () => doc(),
      onChange: () => { docListPaint(); },
      onMessage: (message, sticky) => flashSaved(message, sticky),
      /* The conflict path (ADR-0124 decision 4): the Account's copy becomes the open document
         and this device's is kept beside it under a new id. Nothing is discarded, and the user
         is looking at the one that won rather than at a stale editor. */
      onAdopt: incoming => {
        selectedId = null;
        expanded.clear();
        store.adopt(incoming);
        repository.setLastOpened(incoming.id);
      },
    }) : null;
    store = new Doc.Store(repository, {
      onError: result => flashSaved(result.reason === 'quota'
        ? 'This browser is out of storage — your last change is NOT saved. Download a JSON backup.'
        : 'Could not save to this browser. Download a JSON backup.', true),
      /* The account push hangs off a completed local write, never off a keystroke and never off
         a repaint — see the `_onSaved` comment in resume_document.js, and the write-cadence
         header in resume_sync.js. This is the seam ADR-0124 says must not reuse the debounce. */
      onSaved: saved => { if (sync) sync.note(saved); },
    });
    store.subscribe(paint);

    const last = repository.lastOpened();
    const existing = (last && repository.get(last)) || null;
    const first = repository.list()[0];
    /* Nothing stored at all — a genuinely first visit — opens the guide's worked example rather
       than an empty sheet. Reading a filled résumé is how the guide itself teaches the What /
       How / Result shape, and an empty page is the one starting point that teaches nothing while
       failing every rule the panel beside it states. "New" still offers both. */
    /* And it is the ONLY moment the "Start here" card is honest: it says "this is an example,
       make it yours", which is wrong advice for a résumé somebody wrote yesterday. Nothing on
       the document records where it came from, so it is recorded here. */
    firstRun = !existing && !first;
    const opening = existing || (first && repository.get(first.id)) ||
      startDocument('headless-headhunter', true);
    if (!Layouts.get(opening.layoutId)) opening.layoutId = Layouts.all()[0].id;
    store.adopt(opening);
    repository.setLastOpened(opening.id);
    /* `last` has to name a document the repository actually HOLDS. The worked example is minted
       here rather than loaded, and nothing wrote it: the next visit read `last`, found nothing
       under that id, minted a second example with a second id, and showed the "Start here" card
       again — while the Résumés list stayed empty however much had been typed into it. Only on
       the first run, so a visit that opened a stored résumé still writes nothing it did not
       change. */
    if (firstRun) store.flush();

    wire();
    watchStage();
    /* One listing, after the tab is usable rather than before it. What comes back changes only
       the Résumés popover, so nothing on screen is waiting for it. */
    if (sync) sync.refresh();
    if (!repository.durable) flashSaved('Storage is blocked here — download a backup.');
  }

  /* The panel is hidden until its tab opens, and a hidden element measures zero — so every
     reading taken before then would be wrong. app.js calls this on the way in, and `measureShown`
     is the same rule applied one level down, to whichever segment is on screen. */
  function shown() {
    boot();
    measureShown();
  }

  /* Booting is deliberately NOT on DOMContentLoaded. The ten scripts load on every page of the
     app, and booting eagerly meant every visitor to Search or Trends probed localStorage, built
     and rendered a document into a hidden panel, ran the rule set, and wrote a "last opened" key
     — for a tab they never opened. Two things call it, both at the first moment any of it is
     wanted: app.js on the way in, and the block at the foot of this file for the one load app.js
     cannot catch. */

  /* The tab's public surface. `shown` is what app.js calls; the rest exists because the editor
     holds the only reference to the live document, and the browser tests drive the real page
     through it rather than reaching into a closure they cannot see. */
  root.ResumeEditor = {
    boot, shown, findings, keywordCheck, startDocument, changeLayout,
    current: () => doc(),
    flush: () => store && store.flush(),
    select,
  };

  /* There is one path on which app.js cannot make that call. app.js is not deferred and this is,
     so on a load that lands straight on #resume — a refresh, a bookmark, a shared link — its
     `showTab()` has already run by the time this script executes. Its guard reads
     `window.ResumeEditor`, finds nothing, and moves on; no later event revisits a tab the page
     opened on. The panel is left rendered but never painted: blank sheet, empty layout and paper
     controls, empty form.

     So the editor boots itself in exactly that case, and the condition is the panel already being
     visible — which is true only when showTab chose this tab and found nobody home. A visitor who
     lands on Search or Trends still pays nothing.

     Deferring app.js instead would be the tidier seam, since the routing decision is its own. It
     is not available: base.html loads Google's GSI client `async` with `onload="initAlerts()"`,
     and that handler is defined in app.js — deferring it lets a third-party script call a
     function that does not exist yet. */
  const opened = document.getElementById('panel-resume');
  if (opened && !opened.hidden) shown();
})(typeof globalThis !== 'undefined' ? globalThis : this);
