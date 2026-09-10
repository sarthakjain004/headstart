/* Layer 2 of the résumé builder (ADR-0123) — HOW the components are arranged, and how they look.
 *
 * A Layout owns four things Layer 1 and Layer 3 are forbidden to know about: the page geometry,
 * the type tokens, one render Strategy per component shape (and optionally per type), and the
 * capability contract that says which editor affordances are live in it. That last one is the
 * unobvious part: "draggable and resizable" is not a property of a component, it is a permission
 * the Layout grants. A strict single-column template earns its results by refusing free
 * positioning; a canvas layout grants it. The same component honours both without changing.
 *
 * A Layout emits its own CSS as a string so that the on-screen preview, the print-to-PDF output
 * and the downloaded HTML are rendered from ONE stylesheet rather than three that drift.
 *
 * Adding a Layout is: write a file, call `define`, add one <script> tag. It never requires
 * touching a component, and a component added after it still renders through `byShape`.
 */
(function (root) {
  'use strict';

  const Components = root.ResumeComponents;

  const registry = new Map();
  const order = [];

  /* Its own copy of app.js's escape rather than a shared one, deliberately: these ten files are
     the résumé builder and load independently of the search page's script, and a résumé must not
     stop being escaped because the tab it sits beside was refactored. Eight lines is a cheaper
     coupling than the alternative. */
  const esc = s => (s == null ? '' : String(s)).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** Escaped text with newlines preserved as <br> — bullets are multiline inputs. */
  const escLines = s => esc(s).replace(/\r?\n/g, '<br>');

  const attrs = obj => Object.keys(obj)
    .filter(k => obj[k] != null && obj[k] !== false && obj[k] !== '')
    .map(k => ' ' + k + '="' + esc(obj[k]) + '"').join('');

  function fail(msg) { throw new Error('ResumeLayouts: ' + msg); }

  const clampNum = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /** Register a Layout. */
  function define(spec) {
    if (!spec || !spec.id) fail('a layout id is required');
    if (registry.has(spec.id)) fail('duplicate layout: ' + spec.id);
    if (typeof spec.css !== 'function') fail(spec.id + ': css(theme, scope) is required');
    if (typeof spec.starter !== 'function') fail(spec.id + ': starter(builder) is required');
    if (!spec.render || !spec.render.byShape) fail(spec.id + ': render.byShape is required');
    /* Every shape must have a strategy. Missing one is not a soft failure: a component type
       added next year will arrive wearing a shape this layout never anticipated, and the whole
       promise of shape dispatch is that it renders anyway. */
    const missing = Components.SHAPES.filter(s => typeof spec.render.byShape[s] !== 'function');
    if (missing.length) fail(spec.id + ': no renderer for shape(s) ' + missing.join(', '));

    const layout = Object.freeze({
      id: spec.id,
      label: spec.label || spec.id,
      blurb: spec.blurb || '',
      /* Named after what it is, so the picker can say it: "Single column, Arial, 10.5pt". */
      summary: spec.summary || '',
      /* Whose method this is, for a layout that implements somebody else's. Shown beside the
         picker: the Headless Headhunter's template was credited only in a source comment, which
         is not a credit — nobody using the product ever reads it. Optional, because a layout
         that is nobody's method in particular has nothing to say here. */
      credit: spec.credit || '',
      page: Object.freeze(Object.assign({ width: 8.5, height: 11, margin: 1, unit: 'in' }, spec.page || {})),
      tokens: Object.freeze(Object.assign({}, spec.tokens || {})),
      /* Which tokens the user may move, and between what bounds. This is the "how they look"
         surface: everything here shows up in the inspector as a control, and nothing else does,
         so a layout cannot be dialled out of its own identity. */
      tunables: Object.freeze((spec.tunables || []).map(t => Object.freeze(Object.assign({}, t)))),
      slots: Object.freeze((spec.slots || [{ id: 'main', label: 'Main column', grow: 1 }])
        .map(s => Object.freeze(Object.assign({}, s)))),
      caps: Object.freeze(Object.assign({
        mode: 'flow',        // 'flow' = reorder within slots; 'free' = x/y/w/h positioning
        resize: [],          // geometry keys this layout honours: spaceAfter | gutter | box
        reorder: true,       // may nodes be dragged into a new order at all
      }, spec.caps || {})),
      /* Bounds for every geometry key the layout honours, so a drag cannot produce a document
         the layout would not have rendered. */
      bounds: Object.freeze(Object.assign({
        spaceAfter: [0, 48], gutter: [0.6, 3.2], w: [0.5, 8], h: [0.2, 10], x: [0, 8], y: [0, 10],
      }, spec.bounds || {})),
      css: spec.css,
      starter: spec.starter,
      /* Optional: a second, filled starting point. A layout that ships a worked example teaches
         the shape of good content far faster than placeholder text can, so the picker offers it
         where one exists and stays quiet where it doesn't. */
      example: typeof spec.example === 'function' ? spec.example : null,
      /* Optional: fix up a document that has just arrived from a different Layout. Content is
         never touched here — only the Layer-2 facts this layout needs and the last one had no
         reason to write. A free-positioning layout uses it to give coordinates to blocks that
         have none, which is the difference between "switched layout" and "half the page is
         stacked in the corner on top of the other half". */
      adopt: typeof spec.adopt === 'function' ? spec.adopt : null,
      rules: Object.freeze((spec.rules || []).slice()),
      _render: spec.render,
    });
    registry.set(layout.id, layout);
    order.push(layout.id);
    return layout;
  }

  const get = id => registry.get(id) || null;
  const all = () => order.map(id => registry.get(id));

  /* ---- paper ---------------------------------------------------------------------------
     Most layouts here are written for US Letter, which is the wrong sheet almost everywhere
     outside North America — and HeadStart is deliberately a global product, not a US one. A4 is
     0.23in narrower and 0.69in taller, so a résumé laid out on Letter and printed on A4 re-wraps
     every bullet and moves its page break: for a builder whose headline check is "no bullet over
     three lines", that is a wrong answer shown confidently.

     The sheet is the DOCUMENT's choice, and it OVERRIDES whatever the Layout declares. What a
     Layout declares is only where a new document starts: this used to read "not the Layout's at
     all", on the argument that a Letter template and an A4 one would be two copies of the same
     layout. That holds for a template. It does not hold for a FORM — Europass is an A4 document
     the way a passport is a passport-sized one, and starting it on Letter is not a preference,
     it is the wrong form. So `europass` declares A4 and everything else declares Letter, and a
     document that names a paper still wins over both.

     It is deliberately not a tunable either: tunables are type tokens interpolated into a
     stylesheet, and this is geometry every rule and every measurement reads. */
  const PAPERS = Object.freeze([
    Object.freeze({ id: 'letter', label: 'US Letter · 8.5 × 11in', width: 8.5, height: 11 }),
    Object.freeze({ id: 'a4', label: 'A4 · 210 × 297mm', width: 8.27, height: 11.69 }),
  ]);

  /** The page a document is actually laid out on: the Layout's own page with the document's
   *  sheet substituted. An absent or unreadable name falls back to the Layout's — a document is
   *  a file people exchange, so `paper` is untrusted, and it is only ever a lookup key here. */
  function pageFor(layout, doc) {
    const paper = PAPERS.find(p => p.id === (doc && doc.paper));
    return paper
      ? Object.assign({}, layout.page, { width: paper.width, height: paper.height })
      : layout.page;
  }

  /** Which entry in PAPERS the sheet in force corresponds to — the Layout's own when the document
   *  states nothing. The Design pane's control read `doc.paper || 'letter'`, so a fresh Europass
   *  document rendered and printed A4 while the control beside it said "US Letter": the one place
   *  in the tab where a control disagreed with the page it governs. Matched on width because a
   *  Layout declares inches, not a paper name. */
  function paperIdFor(layout, doc) {
    if (doc && PAPERS.some(p => p.id === doc.paper)) return doc.paper;
    const page = layout.page || {};
    const match = PAPERS.find(p => Math.abs(p.width - page.width) < 0.05);
    return match ? match.id : PAPERS[0].id;
  }

  /* Every token value is interpolated straight into a stylesheet, and that stylesheet is written
     into a document by the print and download paths. So a token is only allowed to be the kind of
     thing its Layout says it is. Without this, a résumé document — which the product invites
     people to exchange as a .json backup — could carry
     `fontFamily: 'Arial</style><script>…'` and run script in HeadStart's own origin the moment it
     was previewed. Validation lives HERE rather than at the import, because this function is the
     single point every render, print and export passes through; guarding the import alone would
     leave any other future source of a document unguarded. */
  const HEX = /^#[0-9a-fA-F]{3,8}$/;
  /* Conservative on purpose: font stacks, keywords and lengths, and nothing that could close a
     declaration, a rule, or the element itself. */
  const SAFE_CSS_WORD = /^[\w\s,.'"()%#-]{0,120}$/;

  /** The value to use for one token, or null if the override cannot be trusted. */
  function vetted(tunable, value, fallback) {
    if (value == null) return null;
    if (tunable && tunable.kind === 'range') {
      const n = Number(value);
      if (!isFinite(n)) return null;
      return clampNum(n, tunable.min, tunable.max);
    }
    if (tunable && tunable.kind === 'color') return HEX.test(String(value)) ? String(value) : null;
    if (tunable && tunable.kind === 'select') {
      return (tunable.options || []).some(o => o[0] === value) ? value : null;
    }
    /* No tunable: the UI offers no way to set this, so an override can only have arrived with a
       document. Allow it only if it is the same shape as the layout's own default and cannot
       carry markup or a second declaration. */
    if (typeof fallback === 'number') {
      const n = Number(value);
      return isFinite(n) ? n : null;
    }
    return SAFE_CSS_WORD.test(String(value)) ? String(value) : null;
  }

  /** The tokens in force for a document: the layout's defaults with the document's own
   *  overrides on top, for keys the layout declares and values it can vouch for. */
  function themeFor(layout, doc) {
    const out = Object.assign({}, layout.tokens);
    const over = (doc && doc.theme) || {};
    const tunables = new Map(layout.tunables.map(t => [t.key, t]));
    for (const key of Object.keys(out)) {
      const value = vetted(tunables.get(key), over[key], out[key]);
      if (value != null) out[key] = value;
    }
    return out;
  }

  /** A node's geometry, clamped to what this layout allows and stripped of what it ignores. */
  function geometryFor(layout, node) {
    const geo = {};
    const allowed = layout.caps.resize;
    const g = node.geometry || {};
    if (allowed.includes('spaceAfter') && g.spaceAfter != null) {
      geo.spaceAfter = clampNum(+g.spaceAfter, layout.bounds.spaceAfter[0], layout.bounds.spaceAfter[1]);
    }
    if (allowed.includes('gutter') && g.gutter != null) {
      geo.gutter = clampNum(+g.gutter, layout.bounds.gutter[0], layout.bounds.gutter[1]);
    }
    if (allowed.includes('box')) {
      for (const k of ['x', 'y', 'w', 'h']) {
        if (g[k] != null) geo[k] = clampNum(+g[k], layout.bounds[k][0], layout.bounds[k][1]);
      }
    }
    return geo;
  }

  /* ---- rendering ----------------------------------------------------------------------
     One pass, post-order: children are rendered to HTML strings and handed to the parent's
     strategy. Strategies build their outer element with `ctx.el`, which is what stamps the
     node id onto the DOM — the editor finds a node from a click through that attribute and
     nothing else, so a strategy that hand-rolls its outer tag makes its component unselectable.
     That contract is checked by resume_layouts.test.js rather than trusted. */

  function renderNode(layout, doc, node, opts) {
    const theme = opts.theme;
    const kids = node.children.map(c => renderNode(layout, doc, c, opts));
    const spec = Components.get(node.type);
    const shape = spec ? spec.shape : 'text';
    const strategy = (layout._render.byType && layout._render.byType[node.type])
      || layout._render.byShape[shape];

    const geo = geometryFor(layout, node);
    const ctx = {
      node, doc, layout, theme, spec,
      content: doc.content[node.id] || {},
      children: kids,
      childNodes: node.children,
      geo,
      esc, escLines, attrs,
      /* The node's own words, in the order its Component Type declares them, skipping empties.
         A `byShape` renderer must never name a FIELD: it is handed components it has never heard
         of, and the moment it writes `content.label` it renders every component that spells that
         field differently as an empty box. Measured before this existed: a Language line with
         fields `name`/`level` rendered as literally nothing in all three layouts — a user's
         languages would have vanished off the page in silence. `byType` renderers may name
         fields freely; they are written for a type they know. */
      fields() {
        const declared = spec ? spec.fields : [];
        return declared
          .map(f => ({ key: f.key, label: f.label, value: doc.content[node.id] ? doc.content[node.id][f.key] : null }))
          .filter(f => typeof f.value === 'string' && f.value.trim());
      },
      /** Every field's value, joined — the last-resort rendering of an unknown component. */
      textOf(separator) {
        return ctx.fields().map(f => escLines(f.value)).join(separator || ' &middot; ');
      },
      /** The outer element of a rendered node. Adds the id hook, the editor's classes and the
       *  geometry the layout honours; a strategy passes its own class and inner HTML. */
      el(tag, a, inner) {
        const own = Object.assign({}, a || {});
        const style = [own.style || ''];
        if (geo.spaceAfter != null) style.push('margin-bottom:' + geo.spaceAfter + 'px');
        if (layout.caps.mode === 'free' && (geo.x != null || geo.y != null)) {
          style.push('position:absolute',
            'left:' + (geo.x || 0) + layout.page.unit, 'top:' + (geo.y || 0) + layout.page.unit,
            'width:' + (geo.w || 3) + layout.page.unit);
          if (geo.h != null) style.push('min-height:' + geo.h + layout.page.unit);
        }
        own.style = style.filter(Boolean).join(';');
        own.class = ['rb-node', own.class].filter(Boolean).join(' ');
        own['data-node'] = node.id;
        own['data-type'] = node.type;
        own['data-shape'] = shape;
        return '<' + tag + attrs(own) + '>' + (inner == null ? '' : inner) + '</' + tag + '>';
      },
    };
    return strategy(ctx);
  }

  /** Render a whole document to HTML. Slots are rendered in the layout's own slot order, so a
   *  node whose stored slot no longer exists lands in the first one rather than disappearing. */
  function renderDocument(layout, doc) {
    const theme = themeFor(layout, doc);
    const known = new Set(layout.slots.map(s => s.id));
    const bySlot = new Map(layout.slots.map(s => [s.id, []]));
    for (const n of doc.root.children) {
      const slot = known.has(n.slot) ? n.slot : layout.slots[0].id;
      bySlot.get(slot).push(n);
    }
    const cols = layout.slots.map(s => {
      const inner = bySlot.get(s.id).map(n => renderNode(layout, doc, n, { theme })).join('\n');
      return '<div class="rb-slot" data-slot="' + esc(s.id) + '" style="flex:' + (s.grow || 1) +
        ' 1 0%">' + inner + '</div>';
    }).join('\n');
    return '<div class="rb-doc rb-' + esc(layout.id) + '"' +
      (layout.caps.mode === 'free' ? ' data-free="1"' : '') + '>' + cols + '</div>';
  }

  /** A standalone HTML document — what the HTML and Word downloads are built from, and what
   *  makes the download match the preview: same renderer, same stylesheet, different wrapper. */
  function renderStandalone(layout, doc, options) {
    const opts = options || {};
    const theme = themeFor(layout, doc);
    const page = pageFor(layout, doc);
    const frame = [
      '@page { size: ' + page.width + page.unit + ' ' + page.height + page.unit +
        '; margin: ' + page.margin + page.unit + '; }',
      'body { margin: 0; background: #fff; }',
      '.rb-sheet { width: ' + (page.width - 2 * page.margin) + page.unit + '; margin: 0 auto;' +
        ' padding: ' + page.margin + page.unit + ' 0; background: #fff; }',
      '@media print { .rb-sheet { padding: 0; width: auto; } }',
    ].join('\n');
    return '<!doctype html>\n<html><head><meta charset="utf-8">' +
      '<title>' + esc(doc.name || 'Résumé') + '</title>' +
      (opts.wordMeta ? '<meta name="ProgId" content="Word.Document">' : '') +
      '<style>\n' + frame + '\n' + layout.css(theme, '.rb-doc') + '\n</style></head>' +
      '<body><div class="rb-sheet">' + renderDocument(layout, doc) + '</div></body></html>';
  }

  function nodesOf(doc) {
    const out = [];
    (function walkTree(node) {
      for (const child of node.children || []) { out.push(child); walkTree(child); }
    })(doc.root);
    return out;
  }

  /** Run a Layout's own rules over a document. Pure — no DOM — so the rule set can be tested
   *  as data rather than by reading a panel, which is how the Headless Headhunter checks are
   *  pinned against the guide's own worked example.
   *
   *  A rule that throws is caught and reported as one unrunnable check: the other rules still
   *  have something useful to say, and a résumé with one broken check is not a broken résumé. */
  /* Its own walk rather than ResumeDocument's. Layer 2 importing Layer 3 was the one place the
     layering this whole design rests on actually leaked, and the thing borrowed was six lines of
     tree recursion over a plain object — not worth the dependency it cost. */
  function runRules(layout, doc) {
    const all = nodesOf(doc);
    const api = {
      layout,
      /* The sheet in use, which is not `layout.page` once the document has chosen one. A rule
         that measures the page must measure the page it will be printed on. */
      page: pageFor(layout, doc),
      theme: themeFor(layout, doc),
      content: id => doc.content[id] || {},
      nodesOfType: type => all.filter(n => n.type === type),
      flatten: () => all.slice(),
    };
    const out = [];
    for (const rule of layout.rules) {
      let found;
      try {
        found = rule.check(doc, api) || [];
      } catch (err) {
        found = [{ level: 'note', nodeId: null, message: 'This check could not run.' }];
      }
      for (const f of found) out.push(Object.assign({ rule: rule.label, ruleId: rule.id }, f));
    }
    /* Errors first, then warnings, then notes — the panel renders them in this order and the
       badge counts everything above a note. */
    const rank = { error: 0, warn: 1, note: 2 };
    return out.sort((a, b) => (rank[a.level] || 3) - (rank[b.level] || 3));
  }

  /* ---- shared strategy helpers ---------------------------------------------------------
     Small pieces more than one layout wants. A helper here must be about *structure*
     ("a left label and a right date"), never about a particular look. */

  /** "June 2023 to Current" from a work entry's own fields.
   *
   *  `style` overrides the two words templates disagree about. The Headless Headhunter guide
   *  says "June 2023 to Current" and is the default, so the three layouts written before this
   *  argument existed are untouched; Jake's Resume and the Harvard standard both write
   *  "June 2023 – Present", and the general résumé standard is explicit that "Current" is wrong.
   *  Neither spelling is checked anywhere — it is the layout's call, which is the point. */
  function dateRange(content, style) {
    const s = Object.assign({ joiner: ' to ', current: 'Current' }, style || {});
    const start = (content.start || '').trim();
    const end = content.current ? s.current : (content.end || '').trim();
    /* An end with no start prints the end. Education is the reason — every standard here asks
       for the graduation date alone — and a job that carries only an end date used to render
       its dates as nothing at all, which is a word lost rather than a word withheld. */
    if (!start) return end ? (content.current ? s.joiner.trim() + ' ' + end : end) : '';
    return end ? start + s.joiner + end : start;
  }

  /** "Cashier at Large Ducks Coffee, TX" — the parts that exist, in the guide's own order. */
  function roleLine(content) {
    const bits = [];
    if (content.role) bits.push(content.role);
    if (content.company) bits.push((bits.length ? 'at ' : '') + content.company);
    let line = bits.join(' ');
    if (content.place) line = line ? line + ', ' + content.place : content.place;
    return line;
  }

  /** A row with one thing against each margin — the shape Jake's Resume and the Harvard template
   *  both build their entries out of ("institution left, location right", then "degree left,
   *  dates right"). Structure only: the caller passes the classes, so the two layouts that use it
   *  look nothing like each other. */
  function marginRow(cls, left, right) {
    return '<div class="' + esc(cls) + '"><span class="rb-row-left">' + left + '</span>' +
      '<span class="rb-row-right">' + right + '</span></div>';
  }

  /** Consecutive `inList` children collected into one list, everything else left alone.
   *  Returns HTML. A Section can therefore render a mixed run — two education bullets, a
   *  paragraph, three more bullets — as two lists rather than one wrong one. */
  function groupChildren(ctx, listClass) {
    const out = [];
    let open = null;
    ctx.childNodes.forEach((child, i) => {
      const spec = Components.get(child.type);
      if (spec && spec.inList) {
        if (!open) { open = []; out.push(open); }
        open.push(ctx.children[i]);
      } else {
        open = null;
        out.push(ctx.children[i]);
      }
    });
    return out.map(part => Array.isArray(part)
      ? '<ul class="' + esc(listClass || 'rb-list') + '">' + part.join('') + '</ul>'
      : part).join('');
  }

  /** A generic last resort. Every layout gets this for shapes it has no opinion about, so
   *  "no renderer" is never a blank box. */
  /** The first declared field, treated as the node's own heading or label, and the rest. Shape
   *  renderers need "the important one and the others" without knowing what either is called. */
  function headAndRest(ctx) {
    const all = ctx.fields();
    return { head: all[0] || null, rest: all.slice(1) };
  }

  /* A generic last resort for every shape. None of these names a field — see `ctx.fields`. */
  function plainStrategies() {
    return {
      header: ctx => ctx.el('header', { class: 'rb-header' }, ctx.textOf()),
      text: ctx => ctx.el('p', { class: 'rb-text' }, ctx.textOf(' ')),
      section: ctx => {
        const { head } = headAndRest(ctx);
        return ctx.el('section', { class: 'rb-section' },
          (head ? '<h2>' + escLines(head.value) + '</h2>' : '') + groupChildren(ctx));
      },
      entry: ctx => ctx.el('div', { class: 'rb-entry' }, ctx.textOf() + ctx.children.join('')),
      line: ctx => {
        const { head, rest } = headAndRest(ctx);
        if (!head) return ctx.el('p', { class: 'rb-line' }, '');
        /* One field is just a line; two or more read as "label: the rest". */
        return ctx.el('p', { class: 'rb-line' }, rest.length
          ? '<b>' + escLines(head.value) + '</b> ' + rest.map(f => escLines(f.value)).join(' &middot; ')
          : escLines(head.value));
      },
      bullet: ctx => ctx.el('li', { class: 'rb-bullet' }, ctx.textOf(' ')),
    };
  }

  root.ResumeLayouts = {
    define, get, all, PAPERS, pageFor, paperIdFor, themeFor, geometryFor, renderDocument, renderNode,
    renderStandalone, runRules,
    esc, escLines, dateRange, roleLine, plainStrategies, groupChildren, clampNum, headAndRest,
    marginRow,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
