/* A two-column Layout — the second one, and the proof that adding a Layout is adding a file.
 *
 * It shares no code with the Headless Headhunter layout, defines no new component, and renders
 * every component that one does, including any added since — the `byShape` fallbacks come free
 * from `plainStrategies`. What differs is everything Layer 2 owns: two slots instead of one, a
 * draggable split between them, a serif heading face, and rules of its own.
 *
 * It is deliberately NOT the recommended layout. The Headless Headhunter guide is explicit that
 * a single column is what a recruiter scans; this exists because "extensible" is a claim that
 * has to be demonstrated, and because a two-column CV is what some markets outside the US expect.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  const render = {
    byShape: Object.assign({}, plain, {
      section: ctx => ctx.el('section', { class: 'tc-section' },
        '<h2 class="tc-h">' + ctx.esc(ctx.content.title) + '</h2>' + L.groupChildren(ctx, 'tc-list')),
      bullet: ctx => ctx.el('li', { class: 'tc-bullet' }, ctx.escLines(ctx.content.text)),
      text: ctx => ctx.el('p', { class: 'tc-note' }, ctx.escLines(ctx.content.text)),
      line: ctx => ctx.el('p', { class: 'tc-line' },
        (ctx.content.label ? '<b>' + ctx.esc(ctx.content.label) + '</b><br>' : '') +
        ctx.escLines(ctx.content.value)),
    }),
    byType: {
      header: ctx => {
        const c = ctx.content;
        const line = [c.phone, c.email, c.link].filter(Boolean).map(ctx.esc).join(' &middot; ');
        return ctx.el('header', { class: 'tc-head' },
          '<h1 class="tc-name">' + ctx.esc(c.fullName) + '</h1>' +
          (line ? '<p class="tc-contact">' + line + '</p>' : '') +
          (c.locationLine ? '<p class="tc-contact">' + ctx.esc(c.locationLine) + '</p>' : ''));
      },
      work_entry: ctx => ctx.el('div', { class: 'tc-entry' },
        '<div class="tc-role">' + ctx.esc(ctx.content.role || '') + '</div>' +
        '<div class="tc-meta">' + ctx.esc([ctx.content.company, ctx.content.place].filter(Boolean).join(', ')) +
          (L.dateRange(ctx.content) ? '<span class="tc-dates">' + ctx.esc(L.dateRange(ctx.content)) + '</span>' : '') +
        '</div>' +
        (ctx.children.length ? '<ul class="tc-list">' + ctx.children.join('') + '</ul>' : '')),
      /* The row is a flex line so the status can sit against the right margin — `margin-left:auto`
         does nothing inside a plain list item, which left it running on after the credential. */
      education_entry: ctx => ctx.el('li', { class: 'tc-bullet' },
        '<span class="tc-row">' + ctx.esc(ctx.content.credential) +
        (ctx.content.status ? '<span class="tc-dates">' + ctx.esc(ctx.content.status) + '</span>' : '') +
        '</span>'),
      project_entry: ctx => ctx.el('div', { class: 'tc-entry' },
        '<div class="tc-role">' + ctx.esc(ctx.content.name) + '</div>' +
        (ctx.children.length ? '<ul class="tc-list">' + ctx.children.join('') + '</ul>' : '')),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      s + ' { font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + ';',
      '      display: flex; gap: ' + theme.columnGap + 'in; align-items: flex-start; }',
      /* The header sits at the top of the wide column, not across both: the two slots are flex
         columns, and spanning them would need a region the slot model does not have. Stated
         because the comment here used to claim otherwise. */
      s + ' .tc-head { text-align: left; margin: 0 0 .9em; border-bottom: 1px solid ' + theme.rule + '; padding-bottom: .4em; }',
      s + ' .tc-name { font-family: ' + theme.headFont + '; font-size: ' + theme.nameSize + 'pt;',
      '      font-weight: 700; margin: 0; color: ' + theme.accent + '; }',
      s + ' .tc-contact { font-size: ' + theme.metaSize + 'pt; margin: .15em 0 0; color: ' + theme.muted + '; }',
      s + ' .tc-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .tc-h { font-family: ' + theme.headFont + '; font-size: ' + theme.headSize + 'pt; font-weight: 700;',
      '      margin: 0 0 .3em; color: ' + theme.accent + '; text-transform: uppercase; letter-spacing: .06em;',
      '      border-bottom: 1px solid ' + theme.rule + '; padding-bottom: .15em; }',
      s + ' .tc-entry { margin: 0 0 .7em; page-break-inside: avoid; break-inside: avoid; }',
      s + ' .tc-role { font-weight: 700; }',
      s + ' .tc-meta { font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + '; display: flex; gap: .5em; }',
      s + ' .tc-dates { margin-left: auto; white-space: nowrap; }',
      s + ' .tc-list { margin: .2em 0 0; padding-left: 1.1em; }',
      s + ' .tc-bullet { margin: 0 0 .12em; }',
      s + ' .tc-row { display: flex; gap: .5em; align-items: baseline; }',
      s + ' .tc-note, ' + s + ' .tc-line { margin: 0 0 .5em; }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; font-weight: 700; margin: 0 0 .2em; }',
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line { margin: 0 0 .6em; }',
    ].join('\n');
  }

  function starter(b) {
    b.add('header', {})
      .section('Experience', s => s.add('work_entry'))
      .section('Education', s => s.add('education_entry'));
    /* Into the narrow column, so the split is doing visible work from the first render rather
       than looking like a one-column layout with dead space beside it — and so this layout's own
       `balance` rule does not fire on the document it just created. */
    b.add('skills_line', { label: 'Skills', value: '' }).into('side');
    b.section('Projects', s => s.add('project_entry')).into('side');
  }

  L.define({
    id: 'two-column',
    label: 'Two column',
    summary: 'Two columns · serif headings · Calibri body',
    blurb: 'A conventional two-column CV. Off the Headless Headhunter method on purpose — it is ' +
      'here to show that the same words re-lay themselves out under a layout that shares no ' +
      'code with the first one.',
    page: { width: 8.5, height: 11, margin: 0.8, unit: 'in' },
    tokens: {
      bodyFont: 'Calibri, Carlito, Helvetica, sans-serif',
      headFont: 'Georgia, "Times New Roman", serif',
      ink: '#111111', muted: '#555555', accent: '#1F3A5F', rule: '#BBBBBB',
      bodySize: 10, headSize: 11, nameSize: 20, metaSize: 9,
      bodyLead: 1.3, blockGap: 0.9, columnGap: 0.35,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8.5, max: 12, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 14, max: 30, step: 1, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.8, step: 0.05, unit: '' },
      { key: 'columnGap', label: 'Gap between columns', kind: 'range', min: 0.1, max: 0.8, step: 0.05, unit: 'in' },
      { key: 'accent', label: 'Heading colour', kind: 'color', unit: '' },
      { key: 'rule', label: 'Rule colour', kind: 'color', unit: '' },
      { key: 'headFont', label: 'Heading font', kind: 'select', unit: '',
        options: [['Georgia, "Times New Roman", serif', 'Georgia'],
          ['Calibri, Carlito, sans-serif', 'Calibri'],
          ['Arial, Helvetica, sans-serif', 'Arial']] },
    ],
    /* Two slots, so a node can be dragged from one column to the other — which is the only new
       *interaction* this layout introduces. `grow` fixes the split; the width between the columns
       is the `columnGap` tunable in the Design pane. There is no divider gesture: this comment
       used to claim one, and the layout's own summary advertised it to users. */
    slots: [{ id: 'main', label: 'Wide column', grow: 2.2 }, { id: 'side', label: 'Narrow column', grow: 1 }],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    rules: [
      {
        id: 'balance', label: 'Neither column left empty',
        check(doc, api) {
          const tops = doc.root.children;
          const side = tops.filter(n => n.slot === 'side').length;
          if (tops.length > 2 && side === 0) {
            return [{ level: 'note', nodeId: null, message: 'Everything is in the wide column. Drag a section into the narrow one, or use a single-column layout.' }];
          }
          return [];
        },
      },
    ],
    css, render, starter,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
