/* The modern coloured sidebar — the shape Canva, Zety and Novoresume put in front of everyone.
 *
 * This is the most-used *look* in consumer résumé builders and the reason it is here: a user
 * arriving from one of those tools expects it, and offering only plain columns would read as the
 * product not having it. It is also the riskiest layout in the picker, which the blurb says out
 * loud rather than leaving to be discovered by an application that vanished.
 *
 * The risk is parsing, not taste. An applicant tracking system extracts the text of the PDF in
 * document order; where a résumé is two columns of text boxes, several parsers read straight
 * across the page and interleave a sidebar line with the job it happens to sit beside. Vendor
 * studies (Enhancv, Jobscan and the builders' own) put field loss on sidebar templates in the
 * tens of percent — those are marketing numbers and are treated as such here, but the mechanism
 * is real and the failure is silent. So this layout does three things about it:
 *
 *   1. The WIDE column is declared first and holds the dated history, which is the part a parser
 *      must get right. The reason to declare it first is structural rather than typographic: a
 *      document arriving from any other layout must land in the main column and never inside the
 *      band, and `renderDocument` puts an unknown slot in `slots[0]`, so declaring the band first
 *      would collapse a whole migrated résumé into it. It does NOT change what comes out of the
 *      printed PDF first — measured on a real print of the example below, the extracted text runs
 *      band-then-main (contact, skills, languages, then profile and the jobs), because a PDF's
 *      text order follows where the glyphs sit, not the DOM. That extraction was clean and
 *      column-by-column rather than interleaved; whether a given ATS interleaves instead is a
 *      property of its parser, which is exactly why the blurb warns rather than promises.
 *   2. `column-order` warns when a job history is dragged into the band.
 *   3. There is no photo component, and none is added for this layout. Photographs are asked for
 *      in some markets and are grounds for discarding the application in others (US, UK, India
 *      all advise against), they carry no information a recruiter can act on, and a face in a
 *      text box is the one thing every parser certainly drops.
 *
 * Nothing here is anybody's published template: the family has no canonical source, so this
 * layout states no rules about the words. It has two, and both are about the column split.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  const DATES = { joiner: ' – ', current: 'Present' };
  const span = content => L.dateRange(content, DATES);

  const render = {
    byShape: Object.assign({}, plain, {
      /* Field-agnostic, every one of them — see the note in resume_layout_jakes.js. */
      section: ctx => {
        const head = L.headAndRest(ctx).head;
        return ctx.el('section', { class: 'sb-section' },
          (head ? '<h2 class="sb-h">' + ctx.escLines(head.value) + '</h2>' : '') +
          L.groupChildren(ctx, 'sb-list'));
      },
      bullet: ctx => ctx.el('li', { class: 'sb-bullet' }, ctx.textOf(' ')),
      text: ctx => ctx.el('p', { class: 'sb-note' }, ctx.textOf(' ')),
      line: ctx => {
        const parts = L.headAndRest(ctx);
        if (!parts.head) return ctx.el('p', { class: 'sb-line' }, '');
        return ctx.el('p', { class: 'sb-line' }, (parts.rest.length
          ? '<span class="sb-line-label">' + ctx.escLines(parts.head.value) + '</span>' +
            '<span class="sb-line-value">' +
              parts.rest.map(f => ctx.escLines(f.value)).join(' · ') + '</span>'
          : '<span class="sb-line-value">' + ctx.escLines(parts.head.value) + '</span>'));
      },
    }),

    byType: {
      header: ctx => {
        const c = ctx.content;
        const lines = [c.phone, c.email, c.link, c.locationLine, c.languages]
          .filter(Boolean).map(v => '<p class="sb-contact">' + ctx.esc(v) + '</p>').join('');
        return ctx.el('header', { class: 'sb-head' },
          '<h1 class="sb-name">' + ctx.esc(c.fullName) + '</h1>' + lines);
      },

      /* "Profile" is the layout's own furniture, like a bullet's marker — it is not Content and
         never reaches an export. The block carries the words; the heading names the block. */
      professional_summary: ctx => ctx.el('div', { class: 'sb-section' },
        '<h2 class="sb-h">Profile</h2>' +
        '<p class="sb-note">' + ctx.escLines(ctx.content.text) + '</p>'),

      work_entry: ctx => ctx.el('div', { class: 'sb-entry' },
        '<div class="sb-role">' + ctx.esc(ctx.content.role || '') + '</div>' +
        '<div class="sb-meta">' +
          ctx.esc([ctx.content.company, ctx.content.place].filter(Boolean).join(' · ')) +
          '<span class="sb-dates">' + ctx.esc(span(ctx.content)) + '</span>' +
        '</div>' + L.groupChildren(ctx, 'sb-list')),

      degree_entry: ctx => ctx.el('div', { class: 'sb-entry' },
        '<div class="sb-role">' + ctx.esc(ctx.content.credential || '') + '</div>' +
        '<div class="sb-meta">' +
          ctx.esc([ctx.content.institution, ctx.content.place].filter(Boolean).join(' · ')) +
          '<span class="sb-dates">' + ctx.esc(span(ctx.content)) + '</span>' +
        '</div>' + L.groupChildren(ctx, 'sb-list')),

      tech_project: ctx => ctx.el('div', { class: 'sb-entry' },
        '<div class="sb-role">' + ctx.esc(ctx.content.name || '') + '</div>' +
        '<div class="sb-meta">' + ctx.esc(ctx.content.tech || '') +
          '<span class="sb-dates">' + ctx.esc(span(ctx.content)) + '</span></div>' +
        L.groupChildren(ctx, 'sb-list')),

      education_entry: ctx => ctx.el('div', { class: 'sb-entry' },
        '<div class="sb-role">' + ctx.esc(ctx.content.credential || '') + '</div>' +
        (ctx.content.status ? '<div class="sb-meta">' + ctx.esc(ctx.content.status) + '</div>' : '')),

      project_entry: ctx => ctx.el('div', { class: 'sb-entry' },
        '<div class="sb-role">' + ctx.esc(ctx.content.name || '') + '</div>' + L.groupChildren(ctx, 'sb-list')),

      /* A language is a name and a level, and neither dots nor a part-filled bar says anything
         the level word does not. They are also the first thing a parser drops and the first
         thing a screen reader cannot read, so this template's most recognisable ornament is the
         one thing deliberately left out. */
      language_line: ctx => ctx.el('p', { class: 'sb-line' },
        '<span class="sb-line-label">' + ctx.esc(ctx.content.language) + '</span>' +
        '<span class="sb-line-value">' + ctx.esc(ctx.content.level) + '</span>'),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      /* `row-reverse` paints the band on the left while the MAIN column is still the first slot
         in the document, which is what keeps a migrated résumé out of the band — see the header
         comment, and note what it says about the printed PDF's own order. Set `flex-direction:
         row` in the Design pane and the band moves to the right. */
      s + ' { display: flex; flex-direction: ' + theme.bandSide + '; align-items: stretch;',
      '      font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + '; }',
      s + ' .rb-slot { min-width: 0; }',
      /* The band is inset rather than bled to the paper's edge. A full-bleed colour would have
         to paint under the sheet's own margin, and that margin belongs to the framework (it is
         the printer's, and it changes with the paper) — so this stops at the text column and
         says so, rather than half-working in the preview and clipping in print. */
      s + ' .rb-slot[data-slot="side"] { background: ' + theme.band + '; color: ' + theme.bandInk + ';',
      '      padding: ' + theme.bandPad + 'em; box-sizing: border-box; }',
      s + ' .rb-slot[data-slot="main"] { padding: ' + theme.bandPad + 'em 0 ' + theme.bandPad + 'em ' +
        theme.columnGap + 'em; box-sizing: border-box; }',
      s + ' .rb-slot[data-slot="side"] .sb-h { color: ' + theme.bandInk + '; border-color: ' + theme.bandRule + '; }',
      s + ' .rb-slot[data-slot="side"] .sb-meta, ' + s + ' .rb-slot[data-slot="side"] .sb-dates,',
      s + ' .rb-slot[data-slot="side"] .sb-contact, ' + s + ' .rb-slot[data-slot="side"] .sb-line-value {',
      '      color: ' + theme.bandMuted + '; }',
      s + ' .sb-head { margin: 0 0 1.1em; }',
      s + ' .sb-name { font-size: ' + theme.nameSize + 'pt; font-weight: 700; line-height: 1.1;',
      '      margin: 0 0 .5em; }',
      s + ' .sb-contact { font-size: ' + theme.metaSize + 'pt; margin: 0 0 .25em; word-break: break-word; }',
      s + ' .sb-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .sb-h { font-size: ' + theme.headSize + 'pt; font-weight: 700; text-transform: uppercase;',
      '      letter-spacing: .09em; color: ' + theme.accent + '; margin: 0 0 .45em;',
      '      padding-bottom: .2em; border-bottom: 1.5px solid ' + theme.accent + '; }',
      s + ' .sb-entry { margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .sb-role { font-weight: 700; }',
      s + ' .sb-meta { font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + ';',
      '      display: flex; gap: .6em; align-items: baseline; }',
      s + ' .sb-dates { margin-left: auto; white-space: nowrap; }',
      s + ' .sb-list { margin: .3em 0 0; padding-left: 1.05em; }',
      s + ' .sb-bullet { margin: 0 0 .18em; }',
      s + ' .sb-line { margin: 0 0 .4em; }',
      s + ' .sb-line-label { display: block; font-weight: 700; }',
      s + ' .sb-line-value { display: block; font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + '; }',
      s + ' .sb-note { margin: 0 0 .5em; }',
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line {',
      '      margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; font-weight: 700;',
      '      text-transform: uppercase; color: ' + theme.accent + '; margin: 0 0 .45em; }',
    ].join('\n');
  }

  /* Which types belong in the band. Short, self-contained blocks with no dates in them — the
     parts a parser interleaving the two columns can scramble least. */
  const BAND_TYPES = new Set(['header', 'skills_line', 'language_line']);

  function starter(b) {
    b.add('header', {}).into('side');
    b.add('professional_summary', {});
    b.section('Experience', s => s.add('work_entry'));
    b.section('Education', s => s.add('degree_entry'));
    b.section('Skills', s => {
      s.add('skills_line', { label: 'Languages', value: '' });
      s.add('skills_line', { label: 'Tools', value: '' });
    }).into('side');
    b.section('Languages', s => s.add('language_line')).into('side');
  }

  function example(b) {
    b.add('header', {
      fullName: 'Tomás Herrera', phone: '+34 600 123 456', email: 'tomas.herrera@gmail.com',
      link: 'linkedin.com/in/therrera', locationLine: 'Barcelona, Spain · EU work permit',
    }).into('side');
    b.add('professional_summary', {
      text: 'Full-stack engineer, seven years building logistics software for European carriers. ' +
        'Happiest on the seam between a warehouse and an API. Looking for platform work on a ' +
        'team that owns what it ships.',
    });
    b.section('Experience', s => {
      s.add('work_entry', {
        role: 'Lead Engineer', company: 'Paack', place: 'Barcelona, Spain',
        start: 'March 2022', current: true,
      }, e => {
        e.bullet('Rebuilt route assignment as a service handling 180,000 parcels a day, cutting failed-delivery retries by 23% in the first quarter');
        e.bullet('Took the mobile courier app from 3 crashes per 1,000 sessions to 0.4 by replacing the offline queue');
        e.bullet('Ran hiring for the platform team and brought four engineers through onboarding in six months');
      });
      s.add('work_entry', {
        role: 'Backend Engineer', company: 'Glovo', place: 'Barcelona, Spain',
        start: 'January 2019', end: 'February 2022',
      }, e => {
        e.bullet('Owned the pricing service through a 5x growth in orders, holding p99 under 120ms');
        e.bullet('Migrated 40 endpoints from a monolith to Go services with no customer-visible downtime');
      });
    });
    b.section('Education', s => s.add('degree_entry', {
      institution: 'Universitat Politècnica de Catalunya',
      credential: 'MSc Computer Science', place: 'Barcelona, Spain', end: 'June 2018',
    }));
    b.section('Skills', s => {
      s.add('skills_line', { label: 'Languages', value: 'Go, TypeScript, Python, SQL' });
      s.add('skills_line', { label: 'Platform', value: 'Kubernetes, Terraform, AWS, Kafka' });
      s.add('skills_line', { label: 'Practice', value: 'Incident command, hiring, mentoring' });
    }).into('side');
    b.section('Languages', s => {
      s.add('language_line', { language: 'Spanish', level: 'Native' });
      s.add('language_line', { language: 'Catalan', level: 'Native' });
      s.add('language_line', { language: 'English', level: 'C1 · Proficient' });
      s.add('language_line', { language: 'German', level: 'B1 · Intermediate' });
    }).into('side');
  }

  /** Give an arriving document a column split, so the band is not an empty stripe of colour.
   *
   *  Only the Layer-2 fact — which slot a top-level block sits in — is touched; no word moves and
   *  no block is dropped, so a document that came from a denser layout arrives whole and any
   *  overflow shows up on the page-break markers the editor draws, rather than disappearing.
   *  Blocks keep their order within each column, which is what makes the trip reversible: switch
   *  back to a one-column layout and the band's contents fall back into the single column in the
   *  same sequence. */
  function adopt(doc) {
    for (const node of doc.root.children) {
      const inBand = BAND_TYPES.has(node.type) ||
        (node.type === 'section' && node.children.length &&
          node.children.every(child => BAND_TYPES.has(child.type)));
      node.slot = inBand ? 'side' : 'main';
    }
  }

  const rules = [
    {
      id: 'column-order', label: 'The dated history stays in the wide column',
      check(doc, api) {
        const out = [];
        for (const node of doc.root.children) {
          if (node.slot !== 'side') continue;
          const dated = [node].concat(node.children)
            .filter(n => n.type === 'work_entry' || n.type === 'degree_entry' || n.type === 'tech_project');
          if (dated.length) {
            out.push({ level: 'warn', nodeId: node.id,
              message: 'A dated block is in the coloured band. Parsers that read straight across a two-column page interleave the band with whatever sits beside it — a job history is the part that must survive. Drag it into the wide column.' });
          }
        }
        return out;
      },
    },
    {
      id: 'band-empty', label: 'The band earns its width',
      check(doc, api) {
        const tops = doc.root.children;
        if (tops.length > 2 && !tops.some(n => n.slot === 'side')) {
          return [{ level: 'note', nodeId: null,
            message: 'Nothing is in the coloured band. A two-column page with one empty column is all of this layout’s parsing risk and none of its use — either drag your skills across, or switch to Harvard classic.' }];
        }
        return [];
      },
    },
  ];

  L.define({
    id: 'modern-sidebar',
    label: 'Modern sidebar',
    summary: 'Coloured band · wide column for the history · skills and languages beside it',
    blurb: 'The look every consumer résumé builder opens with: a coloured band carrying contact, ' +
      'skills and languages, and a wide column carrying the work. Use it where a person reads ' +
      'the résumé — a referral, a small company, a portfolio attachment. Do not use it for a ' +
      'high-volume application: some applicant tracking systems read straight across a ' +
      'two-column page and interleave the band with the job beside it, and you will never be ' +
      'told that is what happened. Harvard classic or Jake’s Resume are the safe ones.',
    page: { width: 8.5, height: 11, margin: 0.5, unit: 'in' },
    tokens: {
      bodyFont: '"Source Sans 3", "Source Sans Pro", Lato, "Segoe UI", Helvetica, Arial, sans-serif',
      ink: '#1A1A1A', muted: '#5A5A5A',
      accent: '#2E4057',
      band: '#2E4057', bandInk: '#FFFFFF', bandMuted: '#C8D2DF', bandRule: '#FFFFFF',
      bandSide: 'row-reverse',
      nameSize: 21, headSize: 10, bodySize: 10, metaSize: 9,
      bodyLead: 1.35, blockGap: 1.1, entryGap: 0.8,
      bandPad: 1.4,      // in body ems, so the band's inset scales with the type
      columnGap: 1.4,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8.5, max: 12, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 14, max: 30, step: 1, unit: 'pt' },
      { key: 'headSize', label: 'Heading size', kind: 'range', min: 8, max: 14, step: 0.5, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.8, step: 0.05, unit: '' },
      { key: 'bandPad', label: 'Band padding', kind: 'range', min: 0.4, max: 2.5, step: 0.1, unit: 'em' },
      { key: 'columnGap', label: 'Gap beside the band', kind: 'range', min: 0.4, max: 3, step: 0.1, unit: 'em' },
      { key: 'band', label: 'Band colour', kind: 'color', unit: '' },
      { key: 'bandInk', label: 'Band text', kind: 'color', unit: '' },
      { key: 'bandMuted', label: 'Band secondary text', kind: 'color', unit: '' },
      { key: 'accent', label: 'Heading colour', kind: 'color', unit: '' },
      { key: 'ink', label: 'Ink', kind: 'color', unit: '' },
      { key: 'bandSide', label: 'Band side', kind: 'select', unit: '',
        options: [['row-reverse', 'Left'], ['row', 'Right']] },
    ],
    /* Main first, deliberately — see the header comment. `grow` fixes the split at roughly a
       third; the band is a column of short lines and a wider one only wastes the page. */
    slots: [{ id: 'main', label: 'Wide column', grow: 2.1 }, { id: 'side', label: 'Coloured band', grow: 1 }],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    css, render, starter, example, adopt, rules,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
