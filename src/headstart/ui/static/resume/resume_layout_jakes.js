/* Jake's Resume — the LaTeX template that most software-engineering job hunts are built on.
 *
 * Source: github.com/jakegut/resume by Jake Gutierrez, MIT licensed, itself based on
 * sb2nov/resume; also carried in the Overleaf gallery under the same name. 2.8k stars and 697
 * forks as of 2026-09-10, which is what picked it over the prettier candidates: this is the one
 * a HeadStart user has most likely already seen, and the corpus here is tech roles.
 *
 * Every number below is read off `resume.tex`, not chosen: `\documentclass[letterpaper,11pt]`,
 * `fullpage` then half an inch clawed back on all four sides (0.5in margins), `\Huge\scshape`
 * for the name, `\large\scshape` section headings each followed by a `\titlerule`, `\small`
 * bullets, and the four-corner `\resumeSubheading` — which is NOT the same way round for the two
 * sections that use it:
 *
 *     Education   institution (bold, left)   place (right)      degree (italic)   dates (italic)
 *     Experience  job title  (bold, left)    dates (right)      employer (italic) place (italic)
 *
 * and `\resumeProjectHeading` puts "Name | Technologies" left with the dates right. Getting that
 * transposition wrong is the single most visible way to render a near-copy of this template that
 * is not this template, so both are written out above rather than left to the code.
 *
 * Where the general résumé standard (Harvard / r/EngineeringResumes, as packaged in this
 * machine's `resume-builder` skill) disagrees with the template, THE TEMPLATE WINS HERE and the
 * disagreement is recorded rather than silently resolved — the same rule resume_layout_
 * headhunter.js follows, and for the same reason: someone who picked "Jake's Resume" picked a
 * template, not a committee.
 *
 *   · Skills last. The standard puts Technical Skills at the TOP for a technical role, on the
 *     five-second-scan argument. Jake's order is Education, Experience, Projects, Technical
 *     Skills, and `starter()` follows it. Reordering is a drag away for anyone who disagrees.
 *   · Education first. The standard moves Education to the bottom after ~3 years. Jake's puts it
 *     first because the template was written for a student, and most of its users still are.
 *   · No summary. The template has no summary or objective block at all; the `off-template` rule
 *     below says so as a note rather than refusing to render one.
 *   · A serif face. The standard asks for Calibri or Arial. The template ships LaTeX's own
 *     Latin Modern Roman, with a sans (`FiraSans`) line commented out in the source — both are
 *     offered in the font tunable, serif first because that is what an unedited `resume.tex`
 *     produces.
 *
 * The one place it agrees loudly: "Present", never "Current", and the dates carry an en dash.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  /* The template's own spelling, handed to the shared date helper. */
  const DATES = { joiner: ' – ', current: 'Present' };
  const span = content => L.dateRange(content, DATES);

  /** One `\resumeSubheading` row: something at the left margin, something at the right. */
  const row = (cls, left, right) => L.marginRow('jr-row ' + cls, left, right);

  const render = {
    byShape: Object.assign({}, plain, {
      /* Not one of these names a field. A `byShape` renderer is handed component types that did
         not exist when it was written — `L.headAndRest` gives it "the first declared field and
         the others" without needing to know what either is called. */
      section: ctx => {
        const head = L.headAndRest(ctx).head;
        return ctx.el('section', { class: 'jr-section' },
          (head ? '<h2 class="jr-h">' + ctx.escLines(head.value) + '</h2>' : '') +
          L.groupChildren(ctx, 'jr-list'));
      },
      bullet: ctx => ctx.el('li', { class: 'jr-bullet' }, ctx.textOf(' ')),
      /* The template has no paragraph block, so one gets the body face and nothing else — no
         box, no rule, no italic. Off-template, and legible. */
      text: ctx => ctx.el('p', { class: 'jr-note' }, ctx.textOf(' ')),
      /* `\resumeSubItem`: a bold label, a colon, and the list. No bullet marker — the Technical
         Skills block is an itemize with `label={}`, which is why it reads as four dense lines
         rather than four bullets. */
      line: ctx => {
        const parts = L.headAndRest(ctx);
        if (!parts.head) return ctx.el('p', { class: 'jr-line' }, '');
        return ctx.el('p', { class: 'jr-line' }, parts.rest.length
          ? '<b>' + ctx.escLines(parts.head.value) + '</b>: ' +
            parts.rest.map(f => ctx.escLines(f.value)).join(' · ')
          : ctx.escLines(parts.head.value));
      },
    }),

    byType: {
      header: ctx => {
        const c = ctx.content;
        /* The contact line is `\small` with `$|$` separators, and the template underlines the
           three that are links. The general standard says links should NOT stand out; the
           template underlines them, so they are underlined. */
        const contact = [
          c.phone ? ctx.esc(c.phone) : '',
          c.email ? '<span class="jr-link">' + ctx.esc(c.email) + '</span>' : '',
          c.link ? '<span class="jr-link">' + ctx.esc(c.link) + '</span>' : '',
        ].filter(Boolean).join('<span class="jr-sep"> | </span>');
        return ctx.el('header', { class: 'jr-head' },
          '<h1 class="jr-name">' + ctx.esc(c.fullName) + '</h1>' +
          (contact ? '<p class="jr-contact">' + contact + '</p>' : '') +
          /* Two fields the template has no line for, printed in the same small type rather than
             dropped: a document arriving from another layout brings them, and a residency or a
             work permit is the fact a recruiter screens on first. */
          ([c.locationLine, c.languages].filter(Boolean).length
            ? '<p class="jr-contact">' +
              [c.locationLine, c.languages].filter(Boolean).map(ctx.esc).join('<span class="jr-sep"> | </span>') +
              '</p>' : ''));
      },

      /* Experience: title over employer, dates top-right, location bottom-right. */
      work_entry: ctx => ctx.el('div', { class: 'jr-entry' },
        row('jr-top', '<b>' + ctx.esc(ctx.content.role || '') + '</b>', ctx.esc(span(ctx.content))) +
        row('jr-sub', '<i>' + ctx.esc(ctx.content.company || '') + '</i>',
          '<i>' + ctx.esc(ctx.content.place || '') + '</i>') +
        L.groupChildren(ctx, 'jr-list')),

      /* Education: institution over degree, and the two right-hand cells swapped. */
      degree_entry: ctx => ctx.el('div', { class: 'jr-entry' },
        row('jr-top', '<b>' + ctx.esc(ctx.content.institution || '') + '</b>',
          ctx.esc(ctx.content.place || '')) +
        row('jr-sub', '<i>' + ctx.esc(ctx.content.credential || '') + '</i>',
          '<i>' + ctx.esc(span(ctx.content)) + '</i>') +
        L.groupChildren(ctx, 'jr-list')),

      /* `\resumeProjectHeading`: the stack sits on the title line behind a pipe, in italic. */
      tech_project: ctx => ctx.el('div', { class: 'jr-entry' },
        row('jr-top',
          '<b>' + ctx.esc(ctx.content.name || '') + '</b>' +
          (ctx.content.tech ? ' <span class="jr-sep">|</span> <i>' + ctx.esc(ctx.content.tech) + '</i>' : ''),
          ctx.esc(span(ctx.content))) +
        L.groupChildren(ctx, 'jr-list')),

      /* The two entry types this template has no block for, rendered in its own shape rather
         than through the generic fallback: a document that arrives from the Headless Headhunter
         layout is made of these, and it should look like this template on arrival, not like a
         paragraph of joined-up fields. */
      education_entry: ctx => ctx.el('div', { class: 'jr-entry jr-thin' },
        row('jr-top', '<b>' + ctx.esc(ctx.content.credential || '') + '</b>',
          '<i>' + ctx.esc(ctx.content.status || '') + '</i>')),

      project_entry: ctx => ctx.el('div', { class: 'jr-entry' },
        row('jr-top', '<b>' + ctx.esc(ctx.content.name || '') + '</b>', '') + L.groupChildren(ctx, 'jr-list')),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      s + ' { font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + '; }',
      s + ' .rb-slot { display: block; }',
      s + ' .jr-head { text-align: center; margin: 0 0 ' + theme.blockGap + 'em; }',
      /* `\Huge\scshape` in the source, and capitals here rather than `font-variant: small-caps`.
         Measured on a real print-to-PDF: a browser with no small-caps font available SYNTHESISES
         them, rendering each lower-case letter as a scaled capital in its own text run, and the
         name then extracts out of the finished PDF as “A NANYA R AO”. The name and the section
         headings are the two strings a parser must read, and this is the exact failure the
         résumé-builder skill's render notes flag as “S ARTHAK J AIN”. Real capitals extract as
         “ANANYA RAO” and are within a hair of the same page. */
      s + ' .jr-name { font-size: ' + theme.nameSize + 'pt; text-transform: uppercase;',
      '      font-weight: 400; margin: 0 0 .15em; }',
      s + ' .jr-contact { font-size: ' + theme.contactSize + 'pt; margin: 0; }',
      s + ' .jr-link { text-decoration: underline; }',
      s + ' .jr-sep { text-decoration: none; }',
      s + ' .jr-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      /* `\titleformat{\section}{\scshape\raggedright\large}...[\titlerule]` — the rule is the
         section's own bottom border and runs the full measure. */
      s + ' .jr-h { font-size: ' + theme.headSize + 'pt; text-transform: uppercase; font-weight: 400;',
      '      letter-spacing: .04em; margin: 0 0 .25em; padding-bottom: .1em;',
      '      border-bottom: 1px solid ' + theme.rule + '; }',
      s + ' .jr-entry { margin: 0 0 ' + theme.entryGap + 'em; page-break-inside: avoid; break-inside: avoid; }',
      s + ' .jr-thin { margin-bottom: .2em; }',
      s + ' .jr-row { display: flex; gap: 1em; align-items: baseline; }',
      /* The two cells are the framework's (`L.marginRow`), so the class names are its own. */
      s + ' .rb-row-left { flex: 1 1 auto; }',
      s + ' .rb-row-right { flex: 0 0 auto; text-align: right; white-space: nowrap; }',
      s + ' .jr-top { font-size: ' + theme.titleSize + 'pt; }',
      s + ' .jr-sub { font-size: ' + theme.bodySize + 'pt; }',
      s + ' .jr-list { margin: .12em 0 0; padding-left: ' + theme.bulletIndent + 'in; }',
      s + ' .jr-bullet { margin: 0 0 .08em; }',
      s + ' .jr-line { margin: 0 0 .1em; }',
      s + ' .jr-note { margin: 0 0 ' + theme.entryGap + 'em; }',
      /* Anything this layout has never heard of still prints in its face rather than the
         browser's default. */
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line {',
      '      margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; text-transform: uppercase;',
      '      border-bottom: 1px solid ' + theme.rule + '; margin: 0 0 .25em; }',
    ].join('\n');
  }

  /* The template's own section order and section names — "Technical Skills" spelled exactly as
     the source spells it, because a standard heading is the one thing an ATS parser really does
     look for, and a renamed one is the cheapest possible own goal. */
  function starter(b) {
    b.add('header', {})
      .section('Education', s => s.add('degree_entry'))
      .section('Experience', s => s.add('work_entry'))
      .section('Projects', s => s.add('tech_project'))
      .section('Technical Skills', s => {
        s.add('skills_line', { label: 'Languages', value: '' });
        s.add('skills_line', { label: 'Frameworks', value: '' });
        s.add('skills_line', { label: 'Developer Tools', value: '' });
        s.add('skills_line', { label: 'Libraries', value: '' });
      });
  }

  /** A worked example in the template's shape, in HeadStart's own words rather than the
   *  template's — the four categories, the transposed education block and the piped project
   *  heading are what this teaches, and the résumé it teaches them with may as well be a
   *  plausible one for the corpus this product indexes. */
  function example(b) {
    b.add('header', {
      fullName: 'Ananya Rao', phone: '+91 98765 43210', email: 'ananya.rao@gmail.com',
      link: 'github.com/ananyarao | linkedin.com/in/ananyarao', locationLine: 'Bengaluru, India',
    })
      .section('Education', s => s.add('degree_entry', {
        institution: 'Indian Institute of Technology Bombay',
        credential: 'B.Tech in Computer Science and Engineering, CGPA 8.7 / 10',
        place: 'Mumbai, India', start: 'August 2018', end: 'May 2022',
      }))
      .section('Experience', s => {
        s.add('work_entry', {
          role: 'Backend Engineer', company: 'Razorpay', place: 'Bengaluru, India',
          start: 'July 2023', current: true,
        }, e => {
          e.bullet('Rebuilt the settlement reconciliation service in Go, cutting the nightly batch from 90 minutes to 7 across 14 million transactions a day');
          e.bullet('Moved 40 endpoints behind an idempotency layer in Postgres, which took duplicate-charge incidents from 3 a month to 0 over two quarters');
          e.bullet('Wrote the on-call runbook and the load tests for the UPI payout path, then ran the game day that found a connection-pool ceiling at 800 concurrent payouts before Diwali traffic did');
        });
        s.add('work_entry', {
          role: 'Software Engineer', company: 'Zoho', place: 'Chennai, India',
          start: 'June 2022', end: 'June 2023',
        }, e => {
          e.bullet('Shipped a React admin console used by 2,300 internal users, replacing four spreadsheets and a nightly email');
          e.bullet('Cut p95 API latency from 850ms to 210ms by adding covering indexes and paging the two queries that scanned the audit table');
        });
      })
      .section('Projects', s => {
        s.add('tech_project', {
          name: 'Gitlytics', tech: 'Python, Flask, React, PostgreSQL, Docker',
          start: 'June 2023', end: 'Present',
        }, e => {
          e.bullet('Built a dashboard that ranks a repository’s review latency by author, on GitHub’s GraphQL API and a Celery queue that refreshes every six hours');
          e.bullet('Ran it against 120 public repositories to check the numbers against GitHub’s own insights before publishing');
        });
        s.add('tech_project', {
          name: 'Hostel Mess Planner', tech: 'TypeScript, Next.js, SQLite',
          start: 'January 2022', end: 'April 2022',
        }, e => {
          e.bullet('Wrote the rota solver that 900 students use to swap meal slots, which removed the paper register the wardens had kept since 2004');
        });
      })
      .section('Technical Skills', s => {
        s.add('skills_line', { label: 'Languages', value: 'Go, Python, TypeScript, SQL (Postgres), Java' });
        s.add('skills_line', { label: 'Frameworks', value: 'React, Next.js, Flask, FastAPI, Spring Boot' });
        s.add('skills_line', { label: 'Developer Tools', value: 'Git, Docker, Kubernetes, Terraform, GitHub Actions, Grafana' });
        s.add('skills_line', { label: 'Libraries', value: 'pandas, NumPy, pytest, testcontainers' });
      });
  }

  /* ---- rules ---------------------------------------------------------------------------
     Three, and every one of them is about the TEMPLATE'S OWN SHAPE rather than about résumé
     writing in general — this template is a .tex file, not a method, so it states nothing about
     verbs, tense or metrics and no such rule is invented for it here. The Headless Headhunter
     layout has fourteen because its source is a guide that says fourteen things.

     Résumé hygiene it does not state — dates, bullet counts, weak openers — is the shared
     baseline's (ADR-0127), which every layout gets. Three used to mean the panel said nothing
     at all about a résumé this template would render perfectly and no employer would read. */

  const rules = [
    {
      id: 'stack', label: 'Every project names what it was built with',
      check(doc, api) {
        return api.nodesOfType('tech_project')
          .filter(n => !String(api.content(n.id).tech || '').trim())
          .map(n => ({ level: 'warn', nodeId: n.id,
            message: 'No stack. The template prints “Name | Technologies” on the project line, and with the technologies empty it prints a bare pipe.' }));
      },
    },
    {
      id: 'skills-grouped', label: 'Technical Skills is grouped by category',
      check(doc, api) {
        const out = [];
        for (const section of api.nodesOfType('section')) {
          const lines = section.children.filter(c => c.type === 'skills_line');
          if (!lines.length) continue;
          const unlabelled = lines.filter(n => !String(api.content(n.id).label || '').trim());
          if (unlabelled.length) {
            out.push({ level: 'note', nodeId: unlabelled[0].id,
              message: 'The template’s skills block is categorised — Languages, Frameworks, Developer Tools, Libraries — each with its own bold label. A line with no label prints as a run-on list.' });
          }
        }
        return out;
      },
    },
    {
      id: 'off-template', label: 'The blocks this template has',
      check(doc, api) {
        /* A note, and only a note: both of these render perfectly well. What they are not is
           part of the template, and a user who chose it by name is owed that fact. */
        const extra = api.flatten().filter(n => n.type === 'professional_summary' || n.type === 'summary');
        return extra.map(n => ({ level: 'note', nodeId: n.id,
          message: 'Jake’s Resume has no summary block — it opens at Education. This will print; the template just never asks for it.' }));
      },
    },
  ];

  L.define({
    id: 'jakes-resume',
    label: 'Jake’s Resume',
    summary: 'Single column · ruled headings · Education, Experience, Projects, Skills',
    credit: 'Template: “Jake’s Resume” by Jake Gutierrez (MIT), github.com/jakegut/resume.',
    blurb: 'The LaTeX template most software-engineering job hunts are built on, rebuilt here as ' +
      'HTML. One column, standard headings, no colour and no graphics — which is also why it ' +
      'parses cleanly in every applicant tracking system. Written for students and early-career ' +
      'engineers: Education first, Technical Skills last, no summary. A poor choice if you are ' +
      'senior enough that the first thing to say is what you have run rather than where you ' +
      'studied, and a poor choice outside software — the categorised skills block is the whole ' +
      'point of it and a résumé with no stack to list has nothing to put there.',
    page: { width: 8.5, height: 11, margin: 0.5, unit: 'in' },
    tokens: {
      bodyFont: '"Latin Modern Roman", "Computer Modern", Georgia, "Times New Roman", serif',
      ink: '#000000', rule: '#000000',
      nameSize: 24,      // \Huge at an 11pt base
      contactSize: 10,   // \small
      headSize: 12,      // \large
      titleSize: 11,     // the document's own size, for the bold line of a subheading
      bodySize: 10,      // \small, which is what the bullets and the italic lines are set in
      bodyLead: 1.15,
      blockGap: 0.6,
      entryGap: 0.45,
      /* Inches. The source overrides only the OUTER list — `\resumeSubHeadingListStart` is
         `\begin{itemize}[leftmargin=0.15in, label={}]`, and the Technical Skills block repeats
         that same declaration — while `\resumeItemListStart` is a bare `\begin{itemize}` at
         LaTeX's own default indent. So 0.15in is NOT the bullet indent, which is what a comment
         here used to claim; this is a browser-side approximation of that default and the exact
         figure is a list parameter the template never states. */
      bulletIndent: 0.25,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8.5, max: 12, step: 0.5, unit: 'pt' },
      { key: 'titleSize', label: 'Entry title size', kind: 'range', min: 9, max: 13, step: 0.5, unit: 'pt' },
      { key: 'headSize', label: 'Heading size', kind: 'range', min: 10, max: 16, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 16, max: 32, step: 1, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.6, step: 0.05, unit: '' },
      { key: 'entryGap', label: 'Space between entries', kind: 'range', min: 0, max: 1.5, step: 0.05, unit: 'em' },
      { key: 'blockGap', label: 'Space between sections', kind: 'range', min: 0, max: 2, step: 0.1, unit: 'em' },
      { key: 'bulletIndent', label: 'Bullet indent', kind: 'range', min: 0.1, max: 0.6, step: 0.05, unit: 'in' },
      { key: 'bodyFont', label: 'Font', kind: 'select', unit: '',
        options: [
          ['"Latin Modern Roman", "Computer Modern", Georgia, "Times New Roman", serif',
            'Latin Modern (what resume.tex prints)'],
          ['"Fira Sans", "Helvetica Neue", Arial, sans-serif', 'Fira Sans (the template’s commented-out line)'],
          ['Calibri, Carlito, sans-serif', 'Calibri'],
          ['Arial, Helvetica, sans-serif', 'Arial'],
        ] },
      { key: 'ink', label: 'Ink', kind: 'color', unit: '' },
      { key: 'rule', label: 'Heading rule', kind: 'color', unit: '' },
    ],
    slots: [{ id: 'main', label: 'The column', grow: 1 }],
    /* One column and no free positioning, for the reason the template is popular in the first
       place: it is the plainest thing that still looks composed, and every affordance that could
       make it un-plain is one an ATS would then have to guess its way through. */
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    css, render, starter, example, rules,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
