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

  const box = (ctx, cls, inner) => ctx.el('div', { class: 'cv-box ' + cls }, inner);

  const render = {
    byShape: Object.assign({}, plain, {
      section: ctx => box(ctx, 'cv-section',
        '<h2 class="cv-h">' + ctx.esc(ctx.content.title) + '</h2>' + L.groupChildren(ctx, 'cv-list')),
      bullet: ctx => ctx.el('li', { class: 'cv-bullet' }, ctx.escLines(ctx.content.text)),
      text: ctx => box(ctx, 'cv-note', ctx.escLines(ctx.content.text)),
      line: ctx => box(ctx, 'cv-line',
        (ctx.content.label ? '<b>' + ctx.esc(ctx.content.label) + '</b> ' : '') + ctx.escLines(ctx.content.value)),
      entry: ctx => box(ctx, 'cv-entry', ctx.children.join('')),
      header: ctx => box(ctx, 'cv-head',
        '<h1 class="cv-name">' + ctx.esc(ctx.content.fullName) + '</h1>' +
        '<p class="cv-contact">' + [ctx.content.phone, ctx.content.email, ctx.content.link,
          ctx.content.locationLine].filter(Boolean).map(ctx.esc).join(' &middot; ') + '</p>'),
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
    b.add('header', {});
    b.section('Experience', s => s.add('work_entry'));
    b.section('Education', s => s.add('education_entry'));
    b.add('skills_line', { label: 'Skills', value: '' });
    const kids = b._doc.root.children;
    const at = (i, x, y, w, h) => { if (kids[i]) kids[i].geometry = { x, y, w, h }; };
    at(0, 0, 0, 6.9, 0.9);
    at(1, 0, 1.1, 4.4, 3.4);
    at(2, 4.7, 1.1, 2.2, 1.6);
    at(3, 4.7, 2.9, 2.2, 1.6);
  }

  /** Give every top-level block a position, keeping the ones that already have one. Blocks
   *  arriving from a flow layout carry no coordinates at all, and absolute positioning without
   *  them collapses the page into a pile at the origin. */
  function adopt(doc) {
    let y = 0;
    for (const node of doc.root.children) {
      const g = node.geometry || {};
      if (g.x != null && g.y != null) { y = Math.max(y, (g.y || 0) + (g.h || 1)); continue; }
      node.geometry = Object.assign({}, g, { x: 0, y: round(y), w: 6.9, h: 1.4 });
      y += 1.6;
    }
  }
  const round = n => Math.round(n * 100) / 100;

  L.define({
    id: 'free-canvas',
    label: 'Free canvas',
    summary: 'Place every block by hand · drag and resize anywhere',
    blurb: 'Every block carries its own position and size — drag it anywhere, pull a corner to ' +
      'resize. Good for a portfolio one-pager. The Headless Headhunter guide would tell you not ' +
      'to send this to a recruiter, and it is right: a scan-in-fifteen-seconds read wants one column.',
    page: { width: 8.5, height: 11, margin: 0.8, unit: 'in' },
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
    bounds: { x: [0, 6.9], y: [0, 9.4], w: [0.8, 6.9], h: [0.3, 9.4] },
    rules: [
      {
        id: 'overflow', label: 'Blocks stay on the page',
        check(doc, api) {
          const out = [];
          const maxY = api.layout.page.height - 2 * api.layout.page.margin;
          for (const n of doc.root.children) {
            const g = n.geometry || {};
            if ((g.y || 0) + (g.h || 0) > maxY + 0.05) {
              out.push({ level: 'warn', nodeId: n.id, message: 'Hangs off the bottom of the page — it will be cut or pushed to a second sheet.' });
            }
          }
          return out;
        },
      },
    ],
    css, render, starter, adopt,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
