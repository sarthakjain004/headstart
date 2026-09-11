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
        '<h2 class="tc-h">' + ctx.escLines((L.headAndRest(ctx).head || {}).value || '') + '</h2>' +
        L.groupChildren(ctx, 'tc-list')),
      bullet: ctx => ctx.el('li', { class: 'tc-bullet' }, ctx.textOf(' ')),
      text: ctx => ctx.el('p', { class: 'tc-note' }, ctx.textOf(' ')),
      line: ctx => {
        const { head, rest } = L.headAndRest(ctx);
        if (!head) return ctx.el('p', { class: 'tc-line' }, '');
        return ctx.el('p', { class: 'tc-line' }, rest.length
          ? '<b>' + ctx.escLines(head.value) + '</b><br>' + rest.map(f => ctx.escLines(f.value)).join(' &middot; ')
          : ctx.escLines(head.value));
      },
    }),
    byType: {
      header: ctx => {
        const c = ctx.content;
        const line = [c.phone, c.email, c.link].filter(Boolean).map(ctx.esc).join(' &middot; ');
        return ctx.el('header', { class: 'tc-head' },
          '<h1 class="tc-name">' + ctx.esc(c.fullName) + '</h1>' +
          (line ? '<p class="tc-contact">' + line + '</p>' : '') +
          (c.locationLine ? '<p class="tc-contact">' + ctx.esc(c.locationLine) + '</p>' : '') +
          /* The header owns a `languages` field and this renderer forgot it, so a résumé that
             listed its languages lost them on arriving here — measured over one node of every
             catalogue type, 2026-09-10. A `byType` renderer may name fields; naming all but one
             of them is how a field goes missing in one layout and nowhere else. */
          (c.languages ? '<p class="tc-contact">' + ctx.esc(c.languages) + '</p>' : ''));
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
      s + ' .tc-entry { margin: 0 0 .7em; }',
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

  /** A worked example in this layout's own shape. What it teaches is the SPLIT: the wide column
   *  carries the narrative a reader follows top to bottom — summary, jobs, projects — and the
   *  narrow one carries the lists they scan for a match. Reading a filled page is how somebody
   *  learns which side a block belongs on; the starter's four empty blocks cannot show it.
   *
   *  No bullets in the narrow column, and that is a measurement rather than a preference: the
   *  side slot is 2.16in of a 6.9in measure, so `three-lines` gives a bullet there about 27
   *  characters a line. A list of skills and a one-line degree are what fits. */
  function example(b) {
    b.add('header', {
      fullName: 'Katarzyna Wójcik', phone: '+48 512 340 118', email: 'k.wojcik@gmail.com',
      link: 'linkedin.com/in/kwojcik', locationLine: 'Kraków, Poland · EU citizen',
    });
    b.add('professional_summary', {
      text: 'Backend engineer, six years on payment and settlement systems for Central European ' +
        'banks. Most at home where money moves and the audit trail has to survive it. Looking ' +
        'for platform work on a team that runs what it writes.',
    });
    b.section('Experience', s => {
      s.add('work_entry', {
        role: 'Senior Backend Engineer', company: 'Blik', place: 'Warsaw, Poland',
        start: 'April 2022', current: true,
      }, e => {
        e.bullet('Rebuilt the settlement reconciliation job as an event-sourced service, cutting the nightly close from 4 hours to 35 minutes');
        e.bullet('Cut p99 latency on the transfer API from 840ms to 190ms by moving the fraud lookup off the request path');
        e.bullet('Wrote the runbook the on-call rota now uses, which took mean time to recovery from 51 minutes to 12');
        e.bullet('Brought three engineers through onboarding in six months, two of them straight out of university');
      });
      s.add('work_entry', {
        role: 'Backend Engineer', company: 'Nordea', place: 'Gdańsk, Poland',
        start: 'September 2019', end: 'March 2022',
      }, e => {
        e.bullet('Migrated 60 SOAP endpoints to REST with no downtime, retiring a mainframe adapter that cost 40 hours of maintenance a month');
        e.bullet('Built the PSD2 consent flow used by 11 partner banks, passing external audit on the first submission');
        e.bullet('Reduced the batch job suite from 9 hours to 2 by parallelising the account-balance pass');
      });
    });
    /* `project_entry`, not `tech_project`: this layout renders the first by name and the second
       through the shape fallback, which emits the entry's bullets as bare <li> outside any <ul>
       — a stray bullet at the page margin. An example is the one document that must not
       demonstrate that, so the stack goes in the project's own name here. */
    b.section('Projects', s => {
      s.add('project_entry', {
        name: 'Ledgerfmt — ISO 20022 message formatter in Rust and WebAssembly',
      }, e => {
        e.bullet('Open source since 2023; 1,400 downloads a month and in use inside two of the banks above');
      });
    });
    b.section('Skills', s => {
      s.add('skills_line', { label: 'Languages', value: 'Java, Kotlin, Rust, SQL' });
      s.add('skills_line', { label: 'Platform', value: 'Kafka, PostgreSQL, Kubernetes, Terraform' });
      s.add('skills_line', { label: 'Domain', value: 'ISO 20022, SEPA, PSD2, double-entry ledgers' });
    }).into('side');
    b.section('Education', s => {
      s.add('education_entry', {
        credential: 'MSc Computer Science, AGH University, Kraków', status: '2019',
      });
      s.add('education_entry', {
        credential: 'BSc Computer Science, AGH University, Kraków', status: '2017',
      });
    }).into('side');
    b.section('Languages', s => {
      s.add('language_line', { language: 'Polish', level: 'Native' });
      s.add('language_line', { language: 'English', level: 'C1 · Proficient' });
      s.add('language_line', { language: 'German', level: 'B2 · Independent' });
    }).into('side');
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
    css, render, starter, example,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
