/* The Résumé tab's editor (ADR-0123) — the only file here that knows about the DOM.
 *
 * It owns no model of its own. It reads the three layers, dispatches Commands at the store, and
 * re-paints from whatever comes back; every user gesture is a Command, which is why undo works
 * on drags and resizes and not only on typing. The interaction rules come from the Layout's
 * capability contract rather than from anything here: which handles a node gets, whether a drag
 * reorders or repositions, and how far a resize may go are all read from `layout.caps` and
 * `layout.bounds`, so a Layout added later gets the right affordances without this file changing.
 */
(function (root) {
  'use strict';

  const Components = root.ResumeComponents;
  const Doc = root.ResumeDocument;
  const Layouts = root.ResumeLayouts;
  const Repo = root.ResumeRepository;
  const Export = root.ResumeExport;
  const Cmd = Doc.Commands;
  const esc = Layouts.esc;

  const el = id => document.getElementById(id);

  let store = null;
  let repository = null;
  let selectedId = null;
  let zoom = 1;
  let booted = false;
  let savedTimer = null;

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

    const page = lay.page;
    /* The whole sheet, margins included, rather than just the text column. Two reasons, and the
       second one is not cosmetic: it looks like the page that comes out of the printer, and it
       gives the drag handles somewhere to sit. Handles hang off a block's left edge; with the
       paper cropped to the text column they landed outside it and were clipped away by the
       scrolling wrapper — present in the DOM, impossible to grab. */
    paper.style.width = page.width + page.unit;
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
    paper.innerHTML = Layouts.renderDocument(lay, shown);
    decorate(paper, lay, shown);

    el('rb-name').value = d.name || '';
    versionPaint();
    el('rb-undo').disabled = !store.canUndo();
    el('rb-redo').disabled = !store.canRedo();
    el('rb-layout-note').textContent = lay.summary || '';

    railPaint();
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

  function handle(kind, glyph, title) {
    const h = document.createElement('span');
    h.className = 'rb-h rb-h-' + kind;
    h.dataset.handle = kind;
    h.title = title;
    h.textContent = glyph;
    return h;
  }

  /* ---- selection ------------------------------------------------------------------------ */

  function select(id) {
    selectedId = id;
    paint();
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
    return rect.width > 0 ? rect.width / lay.page.width : 96;
  }

  function onPointerDown(e) {
    const handleEl = e.target.closest('.rb-h');
    const nodeEl = e.target.closest('[data-node]');
    if (!nodeEl) return;
    const id = nodeEl.dataset.node;
    const lay = layout();
    const model = Doc.find(doc(), id);
    if (!model) return;

    if (!handleEl) { select(id); return; }

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

    const geo = Layouts.geometryFor(lay, model);
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
    const b = lay.bounds;
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
    const b = lay.bounds;
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
        if (g.target.parentId == null && g.target.slot) store.dispatch(Cmd.setSlot(g.id, g.target.slot));
        store.dispatch(Cmd.moveNode(g.id, g.target.parentId, g.target.index));
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
    const addRun = (containerEl, kids, parentId, slot) => {
      if (!containerEl) return;
      const rows = kids.map(k => k.getBoundingClientRect());
      rows.forEach((r, i) => points.push({ parentId, slot, index: i, x: r.left + r.width / 2, y: r.top }));
      const last = rows[rows.length - 1];
      const box = containerEl.getBoundingClientRect();
      points.push({
        parentId, slot, index: rows.length,
        x: last ? last.left + last.width / 2 : box.left + box.width / 2,
        y: last ? last.bottom : box.top + 8,
      });
    };

    const childElements = (parentEl, parentNode) => {
      const wanted = new Set((parentNode ? parentNode.children : d.root.children).map(c => c.id));
      return Array.from(parentEl.querySelectorAll('[data-node]'))
        .filter(e => wanted.has(e.dataset.node));
    };

    for (const slot of lay.slots) {
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

  /* ---- the rail -------------------------------------------------------------------------- */

  /* Rebuild every rail pane EXCEPT the one the user is currently typing in — replacing a field's
     HTML under the caret loses the caret, and the position with it. Keyed on focus rather than on
     a "the last change came from the rail" flag, which is what this was first: that flag froze the
     WHOLE rail on every keystroke, so the Checks panel sat on a stale list while the badge beside
     it counted the new one. Focus is the fact that actually matters, and it is readable. */
  /** The version picker: the master plus every Tailoring, and the delete button only when one
   *  is active. */
  function versionPaint() {
    const d = doc();
    const pick = el('rb-version');
    const tailorings = d.tailorings || [];
    pick.innerHTML = '<option value="">Master résumé</option>' + tailorings.map(t =>
      '<option value="' + esc(t.id) + '"' + (d.activeTailoring === t.id ? ' selected' : '') + '>' +
      esc(t.name) + '</option>').join('');
    pick.value = d.activeTailoring || '';
    el('rb-version-del').hidden = !d.activeTailoring;
  }

  function railPaint() {
    const active = document.activeElement;
    const holdsCaret = pane => pane && active && pane !== active && pane.contains(active);
    if (!holdsCaret(el('rb-pane-content'))) contentPane();
    if (!holdsCaret(el('rb-pane-design'))) designPane();
    if (!holdsCaret(el('rb-pane-checks'))) checksPane();
  }

  function fieldControl(nodeId, field, value) {
    const id = 'rb-f-' + nodeId + '-' + field.key;
    const label = '<label for="' + id + '">' + esc(field.label) +
      (field.hint ? '<span class="rb-hint-i" title="' + esc(field.hint) + '">?</span>' : '') + '</label>';
    if (field.kind === 'flag') {
      return '<div class="rb-field rb-flag"><label><input type="checkbox" id="' + id +
        '" data-node="' + nodeId + '" data-field="' + field.key + '"' + (value ? ' checked' : '') +
        '> ' + esc(field.label) + '</label></div>';
    }
    if (field.kind === 'multiline') {
      return '<div class="rb-field">' + label + '<textarea id="' + id + '" rows="3" data-node="' +
        nodeId + '" data-field="' + field.key + '" placeholder="' + esc(field.placeholder) + '">' +
        esc(value || '') + '</textarea></div>';
    }
    return '<div class="rb-field">' + label + '<input id="' + id + '" data-node="' + nodeId +
      '" data-field="' + field.key + '" value="' + esc(value || '') + '" placeholder="' +
      esc(field.placeholder) + '"></div>';
  }

  function contentPane() {
    const d = doc();
    const lay = layout();
    const pane = el('rb-pane-content');
    const node = selectedId ? Doc.find(d, selectedId) : null;
    const out = [];

    out.push('<div class="rb-prefill"><button class="ghost rb-mini" id="rb-prefill">Fill from my profile</button>' +
      '<span class="note" id="rb-prefill-note"></span></div>');

    const tailoring = Doc.tailoringOf(d);
    if (tailoring) {
      out.push('<p class="rb-banner">Editing <b>' + esc(tailoring.name) + '</b>. What you type ' +
        'here is kept for this version only — the master keeps its own words, and a fix you make ' +
        'there still reaches every version that has not overridden it.</p>');
    }

    if (!node) {
      out.push('<p class="note">Click a block on the page to edit its words, or add one below.</p>');
    } else {
      const spec = Components.get(node.type);
      const overridden = !!(tailoring && (tailoring.picks || {})[node.id]);
      const left_out = !!(tailoring && (tailoring.hidden || []).includes(node.id));
      out.push('<div class="rb-sel"><b>' + esc(spec.label) + '</b>' +
        (overridden ? '<span class="rb-tag">tailored</span>' : '') +
        (spec.blurb ? '<p class="note">' + esc(spec.blurb) + '</p>' : '') + '</div>');
      const content = Doc.contentOf(d, node.id);
      for (const f of spec.fields) out.push(fieldControl(node.id, f, content[f.key]));
      if (tailoring) {
        out.push('<div class="rb-actions">' +
          (overridden ? '<button class="ghost rb-mini" data-act="unfork">Use the master’s words</button>' : '') +
          '<button class="ghost rb-mini" data-act="hide">' +
          (left_out ? 'Put back in this version' : 'Leave out of this version') + '</button></div>');
      }

      /* Which column a top-level block sits in — only offered where the layout has more than
         one, so a single-column template never shows a control that does nothing. */
      if (lay.slots.length > 1 && d.root.children.includes(node)) {
        out.push('<div class="rb-field"><label for="rb-slot">Column</label><select id="rb-slot">' +
          lay.slots.map(s => '<option value="' + esc(s.id) + '"' +
            ((node.slot || lay.slots[0].id) === s.id ? ' selected' : '') + '>' + esc(s.label) +
            '</option>').join('') + '</select></div>');
      }

      const addable = spec.accepts.includes('*')
        ? Components.all().filter(s => s.type !== 'header')
        : spec.accepts.map(t => Components.get(t)).filter(Boolean);
      if (addable.length) {
        out.push('<div class="rb-add"><span class="note">Add inside:</span>' + addable.map(s =>
          '<button class="ghost rb-mini" data-add="' + s.type + '" data-into="' + node.id + '">' +
          esc(s.label) + '</button>').join('') + '</div>');
      }
      out.push('<div class="rb-actions">' +
        (spec.caps.duplicate ? '<button class="ghost rb-mini" data-act="duplicate">Duplicate</button>' : '') +
        (spec.caps.remove ? '<button class="ghost rb-mini danger" data-act="remove">Delete</button>' : '') +
        '</div>');
    }

    out.push('<div class="rb-outline"><div class="rb-outline-head">Outline</div>' +
      outlineHtml(d) + '</div>');
    out.push('<div class="rb-add rb-add-top"><span class="note">Add to the page:</span>' +
      ['section', 'summary', 'skills_line'].map(t => Components.get(t)).filter(Boolean).map(s =>
        '<button class="ghost rb-mini" data-add="' + s.type + '" data-into="">' + esc(s.label) +
        '</button>').join('') + '</div>');

    pane.innerHTML = out.join('');
  }

  function outlineHtml(d) {
    const rows = [];
    const tailoring = Doc.tailoringOf(d);
    const hidden = new Set((tailoring && tailoring.hidden) || []);
    Doc.walk(d.root, (node, parent, index) => {
      if (node === d.root) return;
      const spec = Components.get(node.type);
      const content = Doc.contentOf(d, node.id);
      const first = spec.fields.map(f => content[f.key]).find(v => typeof v === 'string' && v.trim());
      const depth = (function () { let n = node, k = 0; while ((n = Doc.parentOf(d, n.id)) && n !== d.root) k++; return k; })();
      rows.push('<button class="rb-out-row' + (node.id === selectedId ? ' on' : '') +
        (hidden.has(node.id) ? ' off' : '') +
        '" data-select="' + node.id + '" style="padding-left:' + (8 + depth * 12) + 'px">' +
        '<span class="rb-out-type">' + esc(spec.label) + '</span> ' +
        '<span class="rb-out-text">' + esc(String(first || '').slice(0, 44)) + '</span>' +
        (hidden.has(node.id) ? '<span class="rb-tag">left out</span>' : '') + '</button>');
    });
    return rows.join('') || '<p class="note">Nothing on the page yet.</p>';
  }

  function designPane() {
    const d = doc();
    const lay = layout();
    const theme = Layouts.themeFor(lay, d);
    const out = ['<p class="note">' + esc(lay.blurb) + '</p>'];
    for (const t of lay.tunables) {
      const value = theme[t.key];
      const id = 'rb-t-' + t.key;
      if (t.kind === 'range') {
        out.push('<div class="rb-field rb-range"><label for="' + id + '">' + esc(t.label) +
          ' <b>' + esc(value) + esc(t.unit || '') + '</b></label>' +
          '<input type="range" id="' + id + '" data-token="' + t.key + '" min="' + t.min +
          '" max="' + t.max + '" step="' + t.step + '" value="' + esc(value) + '"></div>');
      } else if (t.kind === 'color') {
        out.push('<div class="rb-field rb-colour"><label for="' + id + '">' + esc(t.label) +
          '</label><input type="color" id="' + id + '" data-token="' + t.key + '" value="' +
          esc(value) + '"></div>');
      } else if (t.kind === 'select') {
        out.push('<div class="rb-field"><label for="' + id + '">' + esc(t.label) + '</label>' +
          '<select id="' + id + '" data-token="' + t.key + '">' + t.options.map(([v, l]) =>
            '<option value="' + esc(v) + '"' + (value === v ? ' selected' : '') + '>' + esc(l) +
            '</option>').join('') + '</select></div>');
      }
    }
    out.push('<div class="rb-actions"><button class="ghost rb-mini" id="rb-theme-reset">' +
      'Back to the layout’s own settings</button></div>');
    el('rb-pane-design').innerHTML = out.join('');
  }

  /** Every finding the current layout's rules produce. The running lives in ResumeLayouts so it
   *  is testable without a DOM; this is only the lookup. */
  function findings() {
    const d = view();
    const lay = layout();
    return d && lay ? Layouts.runRules(lay, d) : [];
  }

  function checksPane() {
    const found = findings();
    const lay = layout();
    const out = ['<p class="note">Checked against the ' + esc(lay.label) +
      ' layout’s own rules. Advice, not locks — the page prints either way.</p>'];
    if (!found.length) {
      out.push('<p class="rb-clear">Nothing to flag. Every rule this layout states is met.</p>');
    } else {
      out.push(found.map(f =>
        '<button class="rb-finding rb-' + esc(f.level) + '"' +
        (f.nodeId ? ' data-select="' + esc(f.nodeId) + '"' : ' disabled') + '>' +
        '<span class="rb-finding-rule">' + esc(f.rule) + '</span>' +
        '<span class="rb-finding-msg">' + esc(f.message) + '</span></button>').join(''));
    }
    el('rb-pane-checks').innerHTML = out.join('');
  }

  function badgePaint() {
    const badge = el('rb-badge');
    const n = findings().filter(f => f.level !== 'note').length;
    badge.hidden = n === 0;
    badge.textContent = String(n);
  }

  /* ---- keyword coverage -------------------------------------------------------------------
     The guide's first instruction, and the one people skip: collect the qualifications from ten
     to fifteen real postings for one job title, then get three quarters of them into the first
     half of the first page. This checks the second half of that — the collecting is still work
     only a person can do. */

  function keywordCheck() {
    const d = view();
    const raw = el('rb-kw').value || '';
    const terms = raw.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    const out = el('rb-kw-out');
    if (!terms.length) {
      out.innerHTML = '';
      el('rb-kw-summary').textContent = '';
      return;
    }
    const text = Export.plainText(d).toLowerCase();
    /* "First half of the first page" is measured in characters of the résumé's own text, not in
       rendered inches — an estimate, and the summary line says so rather than implying the
       figure is exact. */
    const early = text.slice(0, Math.floor(text.length * 0.42));
    const rows = terms.map(term => {
      const needle = term.toLowerCase();
      return { term, found: text.includes(needle), early: early.includes(needle) };
    });
    const hits = rows.filter(r => r.found).length;
    const earlyHits = rows.filter(r => r.early).length;
    el('rb-kw-summary').textContent = hits + ' of ' + rows.length + ' present · ' +
      Math.round((earlyHits / rows.length) * 100) + '% near the top (the guide asks for 75%)';
    out.innerHTML = rows.map(r =>
      '<div class="rb-kwrow rb-kw-' + (r.early ? 'early' : r.found ? 'late' : 'missing') + '">' +
      '<span>' + esc(r.term) + '</span><span class="rb-kwtag">' +
      (r.early ? 'near the top' : r.found ? 'present, further down' : 'missing') +
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
    el('rb-pop-open').hidden = true;
  }

  function docListPaint() {
    const rows = repository.list();
    const current = doc();
    el('rb-doclist').innerHTML = rows.length ? rows.map(r =>
      '<div class="rb-docrow' + (current && r.id === current.id ? ' on' : '') + '">' +
      '<button class="rb-docopen" data-open="' + esc(r.id) + '">' + esc(r.name || 'Untitled') +
      '<span class="note">' + esc((Layouts.get(r.layoutId) || {}).label || r.layoutId) + ' · ' +
      esc(String(r.updatedAt || '').slice(0, 10)) + '</span></button>' +
      '<button class="ghost rb-mini danger" data-drop="' + esc(r.id) + '" title="Delete">×</button>' +
      '</div>').join('') : '<p class="note">Nothing saved yet.</p>';

    el('rb-storage').textContent = repository.durable
      ? 'Saved in this browser only — never uploaded. Clearing site data deletes them, so keep a JSON backup.'
      : 'This browser is blocking storage, so nothing is being kept. Download a JSON backup before you leave.';
  }

  function flashSaved(message) {
    const box = el('rb-saved');
    box.textContent = message;
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { box.textContent = ''; }, 2600);
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
    const header = Doc.flatten(d).find(n => n.type === 'header');
    if (header && profile.location) store.dispatch(Cmd.setContent(header.id, { locationLine: profile.location }));
    if (profile.skills) {
      const line = Doc.flatten(d).find(n => n.type === 'skills_line');
      if (line) store.dispatch(Cmd.setContent(line.id, { value: profile.skills }));
      else {
        store.dispatch(Cmd.addNode(null, 'skills_line'));
        const added = Doc.flatten(doc()).filter(n => n.type === 'skills_line').pop();
        if (added) store.dispatch(Cmd.setContent(added.id, { label: 'Skills', value: profile.skills }));
      }
    }
    if (profile.education) {
      const entry = Doc.flatten(doc()).find(n => n.type === 'education_entry');
      if (entry) store.dispatch(Cmd.setContent(entry.id, { credential: profile.education }));
    }
    if (profile.title) {
      const job = Doc.flatten(doc()).find(n => n.type === 'work_entry');
      if (job) store.dispatch(Cmd.setContent(job.id, { role: profile.title }));
    }
    note.textContent = 'Filled what the profile holds. It keeps no contact details, so name, ' +
      'phone and email are still yours to type.';
  }

  /* ---- wiring ------------------------------------------------------------------------------ */

  function wire() {
    const paper = el('rb-paper');
    paper.addEventListener('pointerdown', onPointerDown);

    el('rb-name').addEventListener('input', e => store.dispatch(Cmd.rename(e.target.value), 'name'));

    el('rb-layout').addEventListener('change', e => {
      selectedId = null;
      changeLayout(e.target.value);
    });

    el('rb-version').addEventListener('change', e => {
      selectedId = null;
      store.dispatch(Cmd.activateTailoring(e.target.value || null));
    });
    el('rb-version-new').addEventListener('click', () => {
      const name = window.prompt(
        'Name this version after the job you are applying to — "Acme, Backend Engineer".\n\n' +
        'It starts as a copy of the master and only stores what you change.');
      if (name == null) return;
      selectedId = null;
      store.dispatch(Cmd.addTailoring(name.trim() || 'Untitled version'));
    });
    el('rb-version-del').addEventListener('click', () => {
      const tailoring = Doc.tailoringOf(doc());
      if (!tailoring) return;
      if (!window.confirm('Delete the version “' + tailoring.name + '”? The master résumé and ' +
        'every other version are untouched.')) return;
      selectedId = null;
      store.dispatch(Cmd.removeTailoring(tailoring.id));
    });

    el('rb-zoom').addEventListener('input', e => { zoom = (+e.target.value) / 100; paint(); });

    el('rb-undo').addEventListener('click', () => { selectedId = null; store.undo(); });
    el('rb-redo').addEventListener('click', () => { selectedId = null; store.redo(); });

    el('rb-open').addEventListener('click', () => {
      const pop = el('rb-pop-open');
      pop.hidden = !pop.hidden;
      el('rb-pop-download').hidden = true;
      if (!pop.hidden) docListPaint();
    });
    el('rb-download').addEventListener('click', () => {
      const pop = el('rb-pop-download');
      pop.hidden = !pop.hidden;
      el('rb-pop-open').hidden = true;
    });

    el('rb-new').addEventListener('click', () => {
      const lay = layout();
      const filled = lay.example && window.confirm(
        'Start from the guide’s worked example?\n\nOK fills a complete example résumé you ' +
        'can edit over. Cancel starts an empty one.');
      selectedId = null;
      const fresh = startDocument(lay.id, filled);
      repository.save(fresh);
      repository.setLastOpened(fresh.id);
      store.adopt(fresh);
      el('rb-pop-open').hidden = true;
    });

    el('rb-doclist').addEventListener('click', e => {
      const open = e.target.closest('[data-open]');
      const drop = e.target.closest('[data-drop]');
      if (open) openDocument(open.dataset.open);
      if (drop) {
        const id = drop.dataset.drop;
        if (!window.confirm('Delete this résumé? It is only stored in this browser, so this cannot be undone.')) return;
        repository.remove(id);
        if (doc() && doc().id === id) {
          const next = repository.list()[0];
          if (next) openDocument(next.id);
          else { const fresh = startDocument('headless-headhunter', false); repository.save(fresh); store.adopt(fresh); }
        }
        docListPaint();
      }
    });

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
        window.alert(err.message || 'That file could not be read.');
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
      el('rb-pop-download').hidden = true;
    });

    /* One delegated listener for the whole rail: its panes are re-rendered from scratch on
       every change, so a listener bound to a control inside them would be replaced with it. */
    const rail = document.querySelector('.rb-rail');
    rail.addEventListener('input', e => {
      const t = e.target;
      if (t.dataset.field) {
        const value = t.type === 'checkbox' ? t.checked : t.value;
        /* Under a version this forks a variant on the first keystroke and writes there after —
           copy-on-write, so tailoring a bullet never edits the master by accident. */
        store.dispatch(
          Cmd.setContentFor(t.dataset.node, { [t.dataset.field]: value }, activeTailoring()),
          'content:' + t.dataset.node + ':' + t.dataset.field);
      } else if (t.dataset.token) {
        store.dispatch(Cmd.setTheme({ [t.dataset.token]: t.type === 'range' ? +t.value : t.value }),
          'theme:' + t.dataset.token);
        /* The range's own label carries the live value, and the rail is deliberately not being
           rebuilt under the user's thumb — so nudge just that label. */
        const label = t.parentElement.querySelector('label b');
        const tunable = layout().tunables.find(x => x.key === t.dataset.token);
        if (label && tunable) label.textContent = t.value + (tunable.unit || '');
      }
    });
    rail.addEventListener('change', e => {
      if (e.target.id === 'rb-slot') store.dispatch(Cmd.setSlot(selectedId, e.target.value));
    });
    rail.addEventListener('click', e => {
      const pick = e.target.closest('[data-select]');
      const add = e.target.closest('[data-add]');
      const act = e.target.closest('[data-act]');
      if (pick) { select(pick.dataset.select); scrollToNode(pick.dataset.select); }
      if (add) {
        const into = add.dataset.into || null;
        store.dispatch(Cmd.addNode(into, add.dataset.add));
        const parent = into ? Doc.find(doc(), into) : doc().root;
        const created = parent && parent.children[parent.children.length - 1];
        if (created) select(created.id);
      }
      if (act && selectedId) {
        if (act.dataset.act === 'remove') {
          const gone = selectedId;
          selectedId = null;
          store.dispatch(Cmd.removeNode(gone));
        } else if (act.dataset.act === 'duplicate') {
          store.dispatch(Cmd.duplicateNode(selectedId));
        } else if (act.dataset.act === 'unfork') {
          store.dispatch(Cmd.clearVariant(selectedId, activeTailoring()));
        } else if (act.dataset.act === 'hide') {
          const tailoring = Doc.tailoringOf(doc());
          const isHidden = !!(tailoring && (tailoring.hidden || []).includes(selectedId));
          store.dispatch(Cmd.setHidden(selectedId, !isHidden, activeTailoring()));
        }
      }
      if (e.target.id === 'rb-theme-reset') store.dispatch(Cmd.setTheme(blankTheme()));
      if (e.target.id === 'rb-prefill') prefill();
      if (e.target.id === 'rb-kw-run') keywordCheck();
    });

    el('rb-rail-tabs').addEventListener('click', e => {
      const tab = e.target.closest('[data-pane]');
      if (!tab) return;
      for (const b of el('rb-rail-tabs').children) {
        b.setAttribute('aria-current', b === tab ? 'page' : 'false');
      }
      for (const name of ['content', 'design', 'checks', 'keywords']) {
        el('rb-pane-' + name).hidden = name !== tab.dataset.pane;
      }
    });

    document.addEventListener('keydown', e => {
      if (el('panel-resume') && el('panel-resume').hidden) return;
      const key = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && key === 'z') {
        e.preventDefault();
        selectedId = null;
        if (e.shiftKey) store.redo(); else store.undo();
      }
      if (key === 'escape' && selectedId) select(null);
    });

    /* Anything not yet written is written now, rather than on a timer that the tab closing
       would cancel. */
    window.addEventListener('beforeunload', () => store && store.flush());
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden' && store) store.flush();
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
    store.dispatch({
      name: 'Change layout',
      apply: d => { d.layoutId = layoutId; if (lay.adopt) lay.adopt(d); return d; },
    });
  }

  /** Zoom so the sheet fits the space it has. A US Letter page is 816 CSS px wide and the stage
   *  is narrower than that on most laptops, so 100% opened with the page clipped and a scrollbar
   *  across it. Only on the way in — after that the zoom is the user's. */
  function fitToWidth() {
    const paper = el('rb-paper');
    const wrap = paper.parentElement;
    const available = wrap.clientWidth - 24;
    const natural = paper.offsetWidth;
    if (!available || !natural) return;
    zoom = Math.max(0.5, Math.min(1, Math.round((available / natural) * 20) / 20));
    el('rb-zoom').value = String(Math.round(zoom * 100));
  }

  function scrollToNode(id) {
    const target = el('rb-paper').querySelector('[data-node="' + id + '"]');
    if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  /* ---- boot --------------------------------------------------------------------------------- */

  function boot() {
    if (booted || !el('rb')) return;
    booted = true;

    repository = Repo.detect(window);
    store = new Doc.Store(repository);
    store.subscribe(paint);

    el('rb-layout').innerHTML = Layouts.all().map(l =>
      '<option value="' + esc(l.id) + '">' + esc(l.label) + '</option>').join('');

    const last = repository.lastOpened();
    const existing = (last && repository.get(last)) || null;
    const first = repository.list()[0];
    const opening = existing || (first && repository.get(first.id)) ||
      startDocument('headless-headhunter', false);
    if (!Layouts.get(opening.layoutId)) opening.layoutId = Layouts.all()[0].id;
    store.adopt(opening);
    repository.setLastOpened(opening.id);
    el('rb-layout').value = opening.layoutId;

    wire();
    if (!repository.durable) flashSaved('Storage is blocked here — download a backup.');
  }

  /* The panel is hidden until its tab opens, and a hidden element measures zero — so every
     pixels-per-inch reading taken before then would be wrong. app.js calls this on the way in. */
  let fitted = false;
  function shown() {
    boot();
    if (store && store.get()) {
      el('rb-layout').value = store.get().layoutId;
      paint();
      if (!fitted) { fitted = true; fitToWidth(); paint(); }
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  /* The tab's public surface. `current` and `flush` are here because the editor holds the only
     reference to the live document — anything outside it (a console, a browser test, a future
     "attach this résumé to an application") would otherwise have no way to read or commit it. */
  root.ResumeEditor = {
    boot, shown, findings, keywordCheck, startDocument, changeLayout,
    current: () => doc(),
    flush: () => store && store.flush(),
    select,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
