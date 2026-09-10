/* Europass — the European Union's own CV format, and the only layout here that is a FORM.
 *
 * Source: europass.europa.eu, the Commission's free CV tool. It earns its slot on reach rather
 * than on looks: 10.2 million Europass accounts, about 1.5 million more a year, 8.3 million CVs
 * downloaded in 2024 alone, in 31 languages. Public-sector recruiters, universities and Erasmus
 * and mobility programmes across the EU ask for this shape by name, and several application
 * forms accept nothing else. HeadStart is deliberately a global product, not a North American
 * one, so a builder with three US-shaped layouts and no European standard was missing the one
 * format an entire continent's public sector actually asks for.
 *
 * What makes it structurally different from everything else in the picker — and the reason it is
 * worth a file rather than a colour change — is the LABEL GUTTER. A Europass CV is read as a
 * form: a narrow left column names what the row is (the dates of a job, "Mother tongue",
 * "Digital skills"), and the wide column holds the answer. Every other layout here puts its
 * dates on the right and its labels inline. Nothing else in the builder had a gutter, so nothing
 * else could render this.
 *
 * Two honest warnings, both in the blurb rather than hidden here:
 *   · Private-sector recruiters outside the public system frequently dislike it — it is long,
 *     rigid and looks like everyone else's, which is the price of being a standard form.
 *   · Europass invites a photograph and some national conventions expect one. This builder has
 *     no photo component and none was added for this layout: a face is dropped by every parser,
 *     is grounds for rejection in several of the markets HeadStart indexes, and is the single
 *     easiest thing to attach separately when a specific employer asks.
 *
 * The sheet is A4 — the only layout here that declares one. Europass is an A4 form; laying it
 * out on US Letter and printing it in Europe re-wraps every line. A document may still choose
 * Letter with the paper control, and `pageFor` honours that over this default.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  /* Europass writes an open-ended job as "– CURRENT" in its own exports; "Present" is what the
     rest of this builder uses and what an English-language reader expects. */
  const DATES = { joiner: ' – ', current: 'Present' };
  const span = content => L.dateRange(content, DATES);

  /** One form row: what it is on the left, what it says on the right. Both cells always exist,
   *  so an entry with no dates still lines up with the rows above and below it. */
  const formRow = (label, body) =>
    '<div class="ep-row"><div class="ep-gutter">' + label + '</div>' +
    '<div class="ep-body">' + body + '</div></div>';

  const render = {
    byShape: Object.assign({}, plain, {
      /* The section heading spans both columns, as it does on a CV downloaded from the Europass
         platform today; the gutter underneath it carries the dates and the labels. Field-agnostic,
         like every `byShape` renderer here — see the note in resume_layout_jakes.js. */
      section: ctx => {
        const head = L.headAndRest(ctx).head;
        return ctx.el('section', { class: 'ep-section' },
          (head ? '<h2 class="ep-h">' + ctx.escLines(head.value) + '</h2>' : '') +
          L.groupChildren(ctx, 'ep-list'));
      },
      bullet: ctx => ctx.el('li', { class: 'ep-bullet' }, ctx.textOf(' ')),
      text: ctx => ctx.el('div', { class: 'ep-block' },
        formRow('', '<p class="ep-note">' + ctx.textOf(' ') + '</p>')),
      /* Skills lines are form rows too — "Communication skills", "Organisational skills" and
         "Digital skills" are literally rows on the Europass form, and this is their shape: the
         first field the type declares goes in the gutter, the rest of them beside it. */
      line: ctx => {
        const parts = L.headAndRest(ctx);
        if (!parts.head) return ctx.el('div', { class: 'ep-block' }, formRow('', ''));
        return ctx.el('div', { class: 'ep-block' }, parts.rest.length
          ? formRow(ctx.escLines(parts.head.value),
            parts.rest.map(f => ctx.escLines(f.value)).join(' · '))
          : formRow('', ctx.escLines(parts.head.value)));
      },
    }),

    byType: {
      /* "Personal information" and "About me" below are the FORM's headings, not Content — the
         same status as a bullet's marker. They print, and they are absent from every export,
         which is correct: a heading the user never typed is not one of their words. */
      header: ctx => {
        const c = ctx.content;
        const rows = [
          [c.phone, 'Telephone'], [c.email, 'Email'], [c.link, 'Web'],
          [c.locationLine, 'Address'], [c.languages, 'Languages'],
        ].filter(pair => pair[0])
          .map(pair => formRow(ctx.esc(pair[1]), ctx.esc(pair[0]))).join('');
        return ctx.el('header', { class: 'ep-head' },
          '<h1 class="ep-name">' + ctx.esc(c.fullName) + '</h1>' +
          '<h2 class="ep-h">Personal information</h2>' + rows);
      },

      professional_summary: ctx => ctx.el('div', { class: 'ep-section' },
        '<h2 class="ep-h">About me</h2>' +
        formRow('', '<p class="ep-note">' + ctx.escLines(ctx.content.text) + '</p>')),

      /* The Europass work row: dates in the gutter, then "Position — Employer, City" and the
         description under it. */
      work_entry: ctx => ctx.el('div', { class: 'ep-entry' }, formRow(
        ctx.esc(span(ctx.content)),
        '<div class="ep-title">' + ctx.esc(ctx.content.role || '') + '</div>' +
        (([ctx.content.company, ctx.content.place].filter(Boolean).length)
          ? '<div class="ep-org">' +
            ctx.esc([ctx.content.company, ctx.content.place].filter(Boolean).join(', ')) + '</div>' : '') +
        L.groupChildren(ctx, 'ep-list'))),

      degree_entry: ctx => ctx.el('div', { class: 'ep-entry' }, formRow(
        ctx.esc(span(ctx.content)),
        '<div class="ep-title">' + ctx.esc(ctx.content.credential || '') + '</div>' +
        (([ctx.content.institution, ctx.content.place].filter(Boolean).length)
          ? '<div class="ep-org">' +
            ctx.esc([ctx.content.institution, ctx.content.place].filter(Boolean).join(', ')) + '</div>' : '') +
        L.groupChildren(ctx, 'ep-list'))),

      tech_project: ctx => ctx.el('div', { class: 'ep-entry' }, formRow(
        ctx.esc(span(ctx.content)),
        '<div class="ep-title">' + ctx.esc(ctx.content.name || '') + '</div>' +
        (ctx.content.tech ? '<div class="ep-org">' + ctx.esc(ctx.content.tech) + '</div>' : '') +
        L.groupChildren(ctx, 'ep-list'))),

      education_entry: ctx => ctx.el('div', { class: 'ep-entry' }, formRow(
        ctx.esc(ctx.content.status || ''),
        '<div class="ep-title">' + ctx.esc(ctx.content.credential || '') + '</div>')),

      project_entry: ctx => ctx.el('div', { class: 'ep-entry' }, formRow(
        '', '<div class="ep-title">' + ctx.esc(ctx.content.name || '') + '</div>' + L.groupChildren(ctx, 'ep-list'))),

      /* The language row, which is the one part of this form graded against a published scale:
         the language in the gutter, the CEFR level beside it. The full Europass grid splits that
         level five ways — listening, reading, spoken interaction, spoken production, writing —
         and this renders the single overall level instead. Stated rather than hidden: five
         sub-levels would be five fields on a component every other layout would then have to
         render, for a distinction almost no reader of a tech CV acts on. */
      language_line: ctx => ctx.el('div', { class: 'ep-block' },
        formRow(ctx.esc(ctx.content.language), ctx.esc(ctx.content.level))),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      s + ' { font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + '; }',
      s + ' .rb-slot { display: block; }',
      /* The gutter is a fraction of the measure rather than a fixed inch, so the form keeps its
         proportions on both sheets — this layout defaults to A4 and may be printed on Letter. */
      s + ' .ep-row { display: grid; grid-template-columns: ' + theme.gutter + '% 1fr;',
      '      column-gap: ' + theme.gutterGap + 'em; align-items: baseline; margin: 0 0 .35em; }',
      s + ' .ep-gutter { color: ' + theme.muted + '; font-size: ' + theme.metaSize + 'pt;',
      '      text-align: right; }',
      s + ' .ep-body { min-width: 0; }',
      s + ' .ep-head { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .ep-name { font-size: ' + theme.nameSize + 'pt; font-weight: 700; margin: 0 0 .5em;',
      '      color: ' + theme.accent + '; }',
      /* A full-width heading with a rule under it, in the platform's own blue. */
      s + ' .ep-h { font-size: ' + theme.headSize + 'pt; font-weight: 700; text-transform: uppercase;',
      '      letter-spacing: .05em; color: ' + theme.accent + '; margin: 0 0 .5em;',
      '      padding-bottom: .15em; border-bottom: 2px solid ' + theme.accent + '; }',
      s + ' .ep-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .ep-entry { margin: 0 0 ' + theme.entryGap + 'em; page-break-inside: avoid; break-inside: avoid; }',
      s + ' .ep-block { margin: 0 0 .1em; }',
      s + ' .ep-title { font-weight: 700; }',
      s + ' .ep-org { color: ' + theme.muted + '; }',
      s + ' .ep-list { margin: .2em 0 0; padding-left: 1.05em; }',
      s + ' .ep-bullet { margin: 0 0 .12em; }',
      s + ' .ep-note { margin: 0; }',
      /* A component this form has no row for still gets the body column rather than the gutter,
         because an unlabelled row that starts at the left margin reads as a broken form. */
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line {',
      '      margin: 0 0 ' + theme.entryGap + 'em; margin-left: calc(' + theme.gutter + '% + ' +
        theme.gutterGap + 'em); }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; font-weight: 700;',
      '      text-transform: uppercase; color: ' + theme.accent + ';',
      '      border-bottom: 2px solid ' + theme.accent + '; margin: 0 0 .5em; }',
    ].join('\n');
  }

  /* The form's own sections, in the platform's own order and spelling. */
  function starter(b) {
    b.add('header', {})
      .add('professional_summary', {})
      .section('Work experience', s => s.add('work_entry'))
      .section('Education and training', s => s.add('degree_entry'))
      .section('Language skills', s => {
        s.add('language_line', { language: 'Mother tongue', level: '' });
        s.add('language_line', {});
      })
      .section('Digital skills', s => s.add('skills_line', { label: 'Digital skills', value: '' }));
  }

  function example(b) {
    b.add('header', {
      fullName: 'Elena Novak', phone: '+386 40 123 456', email: 'elena.novak@gmail.com',
      link: 'linkedin.com/in/elenanovak', locationLine: 'Ljubljana, Slovenia',
    })
      .add('professional_summary', {
        text: 'Data engineer with five years on public-sector reporting platforms, working across ' +
          'Slovenian and EU statistical standards. Interested in open-data infrastructure.',
      })
      .section('Work experience', s => {
        s.add('work_entry', {
          role: 'Data Engineer', company: 'Statistical Office of the Republic of Slovenia',
          place: 'Ljubljana, Slovenia', start: 'September 2022', current: true,
        }, e => {
          e.bullet('Built the ingestion pipeline behind 40 national indicators published to Eurostat, replacing a quarterly manual upload');
          e.bullet('Cut the monthly release from 6 working days to 1 by validating at source instead of after aggregation');
        });
        s.add('work_entry', {
          role: 'Junior Developer', company: 'Comtrade', place: 'Ljubljana, Slovenia',
          start: 'October 2019', end: 'August 2022',
        }, e => e.bullet('Maintained a Java reporting service used by 11 municipal authorities'));
      })
      .section('Education and training', s => s.add('degree_entry', {
        institution: 'University of Ljubljana, Faculty of Computer and Information Science',
        credential: 'Master of Science in Computer Science', place: 'Ljubljana, Slovenia',
        start: 'October 2017', end: 'September 2019',
      }))
      .section('Language skills', s => {
        s.add('language_line', { language: 'Mother tongue', level: 'Slovenian' });
        s.add('language_line', { language: 'English', level: 'C1' });
        s.add('language_line', { language: 'German', level: 'B2' });
        s.add('language_line', { language: 'Croatian', level: 'B1' });
      })
      .section('Digital skills', s => {
        s.add('skills_line', { label: 'Programming', value: 'Python, SQL, Java, dbt' });
        s.add('skills_line', { label: 'Platforms', value: 'PostgreSQL, Airflow, Docker, Kubernetes' });
        s.add('skills_line', { label: 'Standards', value: 'SDMX, INSPIRE, Eurostat metadata' });
      });
  }

  /* ---- rules ---------------------------------------------------------------------------
     Three, each one a thing the Europass form itself states or requires. Nothing here is about
     how a bullet is written: Europass has no opinion about that and inventing one would be
     putting words in the Commission's mouth. */

  const CEFR = /^(?:A1|A2|B1|B2|C1|C2)\b/i;
  const NATIVE = /\b(?:mother tongue|native|first language)\b/i;

  const rules = [
    {
      id: 'cefr', label: 'Language levels use the CEFR scale',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('language_line')) {
          const c = api.content(n.id);
          const level = String(c.level || '').trim();
          const isMotherTongue = NATIVE.test(String(c.language || '')) || NATIVE.test(level);
          if (!level || isMotherTongue) continue;
          if (!CEFR.test(level)) {
            out.push({ level: 'warn', nodeId: n.id,
              message: '“' + level + '” is not a CEFR level. Europass grades language skills on A1, A2, B1, B2, C1 and C2, and a European recruiter reading this form expects to compare one of those six.' });
          }
        }
        return out;
      },
    },
    {
      id: 'mother-tongue', label: 'The form asks for a mother tongue',
      check(doc, api) {
        const languages = api.nodesOfType('language_line');
        if (!languages.length) return [];
        const stated = languages.some(n => {
          const c = api.content(n.id);
          return NATIVE.test(String(c.language || '')) || NATIVE.test(String(c.level || ''));
        });
        return stated ? [] : [{ level: 'note', nodeId: languages[0].id,
          message: 'No mother tongue stated. The Europass form separates it from the languages you learned, and leaving it out reads as an unfinished form rather than as a choice.' }];
      },
    },
    {
      id: 'a4', label: 'Europass is an A4 form',
      check(doc, api) {
        /* Measured off the sheet actually in force, not off the layout's default: the document
           owns the paper choice and this rule exists to say what that choice costs. */
        if (Math.abs(api.page.width - 8.27) < 0.05) return [];
        return [{ level: 'note', nodeId: null,
          message: 'This is printed on US Letter. Europass is an A4 form, and every office that asks for one will print it on A4 — where the page is 0.23in narrower, so every line re-wraps and the page breaks move. Change it beside the layout picker.' }];
      },
    },
  ];

  L.define({
    id: 'europass',
    label: 'Europass (EU)',
    summary: 'A4 · label gutter · the European Union’s own CV form',
    credit: 'Format: Europass, the European Union’s CV standard (europass.europa.eu).',
    blurb: 'The EU’s official CV form — 10 million accounts and 8.3 million CVs downloaded in ' +
      '2024. Ask for it by name and this is what is expected: a label gutter, dates on the left, ' +
      'and language levels on the CEFR scale. Right for EU public-sector posts, universities, ' +
      'Erasmus and mobility schemes, and for any form that says “Europass”. Wrong almost ' +
      'everywhere else — private-sector recruiters find it long and identical to every other ' +
      'one, and a tech role in Bengaluru or Berlin is better served by Jake’s Resume. No photo ' +
      'block here, on purpose, whatever the form allows.',
    /* The one layout in the picker that is not US Letter. */
    page: { width: 8.27, height: 11.69, margin: 0.75, unit: 'in' },
    tokens: {
      bodyFont: '"Source Sans 3", "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
      ink: '#1A1A1A', muted: '#5C5C5C',
      accent: '#004494',        // the Commission's own blue
      nameSize: 20, headSize: 10.5, bodySize: 10, metaSize: 9,
      bodyLead: 1.35,
      gutter: 26,               // per cent of the measure — see the note in css()
      gutterGap: 1.1,
      blockGap: 1.1,
      entryGap: 0.7,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8.5, max: 12, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 14, max: 28, step: 1, unit: 'pt' },
      { key: 'headSize', label: 'Heading size', kind: 'range', min: 9, max: 14, step: 0.5, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.7, step: 0.05, unit: '' },
      { key: 'gutter', label: 'Label column width', kind: 'range', min: 15, max: 40, step: 1, unit: '%' },
      { key: 'gutterGap', label: 'Gap after the label', kind: 'range', min: 0.4, max: 2.5, step: 0.1, unit: 'em' },
      { key: 'blockGap', label: 'Space between sections', kind: 'range', min: 0.3, max: 2.5, step: 0.1, unit: 'em' },
      { key: 'entryGap', label: 'Space between entries', kind: 'range', min: 0, max: 2, step: 0.05, unit: 'em' },
      { key: 'accent', label: 'Accent', kind: 'color', unit: '' },
      { key: 'ink', label: 'Ink', kind: 'color', unit: '' },
    ],
    slots: [{ id: 'main', label: 'The form', grow: 1 }],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    css, render, starter, example, rules,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
