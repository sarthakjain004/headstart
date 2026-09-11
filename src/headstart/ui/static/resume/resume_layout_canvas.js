/* A free-positioning Layout — the third one, and the only one that grants `mode: 'free'`.
 *
 * It exists to make the capability contract falsifiable. The claim in resume_layouts.js is that
 * "draggable and resizable" is a permission a Layout grants rather than a property a component
 * has; a build with only flow layouts could assert that and never be tested on it. Here every
 * node carries its own x, y, width and height, dragged and resized directly on the page, and
 * the SAME components — unchanged — accept it.
 *
 * It is labelled as what it is: a layout for a portfolio or a one-page profile, not for an
 * application a recruiter will screen. The Headless Headhunter guide's position on multi-column
 * design résumés is unambiguous, and the picker repeats it rather than hiding it.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();
  const round = n => Math.round(n * 100) / 100;

  /* The sheet a new document on this layout starts on, and the area it leaves. Derived rather
     than written out: 6.9 and 9.4 — the US Letter answer — used to be typed into the starter,
     into `adopt` and into `bounds`, while the paper is the DOCUMENT's choice and may be A4. */
  const PAGE = { width: 8.5, height: 11, margin: 0.8, unit: 'in' };
  const { wide: WIDE, tall: TALL } = L.usable(PAGE);

  const box = (ctx, cls, inner) => ctx.el('div', { class: 'cv-box ' + cls }, inner);

  const render = {
    byShape: Object.assign({}, plain, {
      section: ctx => box(ctx, 'cv-section',
        '<h2 class="cv-h">' + ctx.escLines((L.headAndRest(ctx).head || {}).value || '') + '</h2>' +
        L.groupChildren(ctx, 'cv-list')),
      bullet: ctx => ctx.el('li', { class: 'cv-bullet' }, ctx.textOf(' ')),
      text: ctx => box(ctx, 'cv-note', ctx.textOf(' ')),
      line: ctx => {
        const { head, rest } = L.headAndRest(ctx);
        return box(ctx, 'cv-line', head
          ? (rest.length
            ? '<b>' + ctx.escLines(head.value) + '</b> ' + rest.map(f => ctx.escLines(f.value)).join(' &middot; ')
            : ctx.escLines(head.value))
          : '');
      },
      /* Its OWN words as well as its children. Rendering only children made any entry without
         them — a certification, an award — an empty box on the page. */
      entry: ctx => box(ctx, 'cv-entry', ctx.textOf() + ctx.children.join('')),
      /* First declared field as the name, the rest as the contact line — a byShape renderer is
         handed components it does not know, so it may not name `fullName` or `phone`. */
      header: ctx => {
        const { head, rest } = L.headAndRest(ctx);
        return box(ctx, 'cv-head',
          (head ? '<h1 class="cv-name">' + ctx.escLines(head.value) + '</h1>' : '') +
          (rest.length ? '<p class="cv-contact">' +
            rest.map(f => ctx.escLines(f.value)).join(' &middot; ') + '</p>' : ''));
      },
    }),
    byType: {
      work_entry: ctx => box(ctx, 'cv-entry',
        '<div class="cv-role">' + ctx.esc(L.roleLine(ctx.content)) + '</div>' +
        '<div class="cv-dates">' + ctx.esc(L.dateRange(ctx.content)) + '</div>' +
        (ctx.children.length ? '<ul class="cv-list">' + ctx.children.join('') + '</ul>' : '')),
      education_entry: ctx => ctx.el('li', { class: 'cv-bullet' },
        ctx.esc(ctx.content.credential) + (ctx.content.status ? ' — ' + ctx.esc(ctx.content.status) : '')),
      project_entry: ctx => box(ctx, 'cv-entry',
        '<div class="cv-role">' + ctx.esc(ctx.content.name) + '</div>' +
        (ctx.children.length ? '<ul class="cv-list">' + ctx.children.join('') + '</ul>' : '')),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      /* The page is the positioning context. Every top-level node is absolutely placed against
         it by the framework's own `el`, so nothing here repeats those coordinates. */
      s + ' { position: relative; min-height: 9in; font-family: ' + theme.bodyFont + ';',
      '      color: ' + theme.ink + '; font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + '; }',
      s + ' .rb-slot { position: static; }',
      s + ' .cv-box { box-sizing: border-box; }',
      s + ' .cv-name { font-family: ' + theme.headFont + '; font-size: ' + theme.nameSize + 'pt;',
      '      margin: 0; color: ' + theme.accent + '; }',
      s + ' .cv-contact { font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + '; margin: .2em 0 0; }',
      s + ' .cv-h { font-family: ' + theme.headFont + '; font-size: ' + theme.headSize + 'pt; margin: 0 0 .25em;',
      '      color: ' + theme.accent + '; border-bottom: 2px solid ' + theme.accent + '; padding-bottom: .1em; }',
      s + ' .cv-role { font-weight: 700; }',
      s + ' .cv-dates { font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + '; }',
      s + ' .cv-list { margin: .2em 0 0; padding-left: 1.1em; }',
      s + ' .cv-bullet { margin: 0 0 .1em; }',
      s + ' .cv-entry, ' + s + ' .cv-note, ' + s + ' .cv-line { margin: 0 0 .4em; }',
      s + ' .rb-section h2, ' + s + ' .rb-header { font-size: ' + theme.headSize + 'pt; }',
    ].join('\n');
  }

  /* Placed rather than stacked — a free layout whose starter had no coordinates would open as a
     pile in the top-left corner and read as broken. */
  function starter(b) {
    b.add('header', {}).placed(0, 0, WIDE, 0.9);
    b.section('Experience', s => s.add('work_entry')).placed(0, 1.1, 4.4, 3.4);
    b.section('Education', s => s.add('education_entry')).placed(WIDE - 2.2, 1.1, 2.2, 1.6);
    b.add('skills_line', { label: 'Skills', value: '' }).placed(WIDE - 2.2, 2.9, 2.2, 1.6);
  }

  /** Give every top-level block a position, keeping the ones that already have one. Blocks
   *  arriving from a flow layout carry no coordinates at all, and absolute positioning without
   *  them collapses the page into a pile at the origin. */
  function adopt(doc) {
    /* The width of the sheet THIS document chose. A block handed the Letter measure on an A4
       page arrives already hanging over the right margin, which the overflow rule would then
       have to report about a block the user never touched. (`starter` cannot do the same — it is
       handed a builder and no document — so a Letter starter switched to A4 does report two.) */
    const wide = L.boundsFor(FREE_CANVAS, doc).w[1];
    let y = 0;
    for (const node of doc.root.children) {
      const g = node.geometry || {};
      if (g.x != null && g.y != null) { y = Math.max(y, (g.y || 0) + (g.h || 1)); continue; }
      node.geometry = Object.assign({}, g, { x: 0, y: round(y), w: wide, h: 1.4 });
      y += 1.6;
    }
  }

  const FREE_CANVAS = L.define({
    id: 'free-canvas',
    label: 'Free canvas',
    summary: 'Place every block by hand · drag and resize anywhere',
    blurb: 'Every block carries its own position and size — drag it anywhere, pull a corner to ' +
      'resize. Good for a portfolio one-pager. The Headless Headhunter guide would tell you not ' +
      'to send this to a recruiter, and it is right: a scan-in-fifteen-seconds read wants one column.',
    page: PAGE,
    tokens: {
      bodyFont: 'Helvetica, Arial, sans-serif',
      headFont: 'Helvetica, Arial, sans-serif',
      ink: '#111111', muted: '#666666', accent: '#0F766E',
      bodySize: 10, headSize: 12, nameSize: 24, metaSize: 9, bodyLead: 1.3,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8, max: 13, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 14, max: 36, step: 1, unit: 'pt' },
      { key: 'accent', label: 'Accent', kind: 'color', unit: '' },
      { key: 'ink', label: 'Ink', kind: 'color', unit: '' },
    ],
    slots: [{ id: 'main', label: 'The page', grow: 1 }],
    /* The one layout that grants free positioning, and therefore the one that exercises the
       `box` resize path and the absolute-position branch in ResumeLayouts.renderNode. */
    caps: { mode: 'free', reorder: false, resize: ['box'] },
    /* This layout's own sheet. A document that chose another one is bounded by THAT: the render
       and the rules clamp through `ResumeLayouts.boundsFor`, which derives the four maxima from
       the page in force. What is stated here is still load-bearing, and not only as a default —
       `resume_editor.js` reads `layout.bounds` directly for its drag clamps and its range
       controls, so on an A4 document the handles still offer the Letter maximum until that file
       calls `boundsFor` too. Both ends are numbers because a range control needs two. */
    bounds: { x: [0, WIDE], y: [0, TALL], w: [0.8, WIDE], h: [0.3, TALL] },
    rules: [
      {
        id: 'overflow', label: 'Blocks stay on the page',
        check(doc, api) {
          const out = [];
          const { wide: maxX, tall: maxY } = L.usable(api.page);
          for (const n of doc.root.children) {
            /* The geometry as it will be DRAWN — clamped to the sheet this document chose, not as
               stored. Clamping is per-axis and so cannot stop a block placed far to the right from
               running over the edge: x + w is the only thing that says that, and nothing said it
               at all while this rule looked at the vertical axis alone. */
            const g = L.geometryFor(api.layout, n, doc);
            if ((g.y || 0) + (g.h || 0) > maxY + 0.05) {
              out.push({ level: 'warn', nodeId: n.id, message: 'Hangs off the bottom of the page — it will be cut or pushed to a second sheet.' });
            }
            if ((g.x || 0) + (g.w || 0) > maxX + 0.05) {
              out.push({ level: 'warn', nodeId: n.id, message: 'Runs off the right-hand edge — everything past the margin is outside the printable area.' });
            }
          }
          return out;
        },
      },
    ],
    css, render, starter, adopt,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
