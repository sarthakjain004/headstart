/* The McDowell CV — one bold line an entry, with the organisation in the MIDDLE of it.
 *
 * Source: github.com/dnl-blkv/mcdowell-cv, MIT, 2,707 stars / 848 forks, read from
 * the GitHub API on 2026-09-10, last commit 2023-08-30. The repository is one author's LaTeX
 * implementation; the FORMAT is Gayle Laakmann McDowell's, from "Cracking the Coding Interview",
 * which is why it earns a slot in a product whose corpus is software-engineering roles. The
 * credit below names both, because they are two different people's contributions.
 *
 * What makes it a different arrangement rather than a fourth plain column. Every other
 * single-column layout in this picker spends TWO rows on an entry: Jake's Resume writes the job
 * title and the dates on one and the employer and the place on the next, Harvard transposes the
 * same two rows, and the Headless Headhunter runs a role line against a date gutter. This one
 * spends ONE, and puts three things on it —
 *
 *     Senior Software Engineer          Monzo Bank          Sept 2023 – Present
 *     └ left, bold                      └ CENTRED, bold     └ right, bold
 *
 * — from a three-cell `tabu` row in the source (`X[l,p] X[c,p] X[r,p]`). Of twenty templates
 * surveyed on 2026-09-10 it is the only one that centres the organisation, and the saving is not
 * cosmetic: an entry occupies half the vertical space, which is what gets a sixth job onto the
 * one page every standard in this repo asks for. The cost is that the middle cell has nowhere to
 * put a location, so this template writes the place into the organisation cell.
 *
 * Two further things taken from the source rather than invented, because they are what a reader
 * who picked this format by name is expecting:
 *   · Coursework goes INSIDE Education as a labelled line — "Graduate Coursework: …",
 *     "Undergraduate Coursework: …" — not as a section of its own. `skills_line` already is
 *     that shape, so no component was added for it (ADR-0130).
 *   · The extra sections it ships are "Additional Experience and Awards" and "Languages and
 *     Technologies", and `starter()` uses those exact words. A standard heading is the one thing
 *     an ATS parser really does look for.
 *
 * A note on the id, because it collides with vocabulary this same change sharpens. ADR-0130
 * refuses Publications on the ground that a résumé is not a CV, and CONTEXT.md's noun is Résumé
 * document — and then this layout is registered as `mcdowell-cv`. That is deliberate: the
 * upstream repository, the Overleaf listing and everyone who has ever recommended it call it a
 * CV, and a user searching the picker for what they were told to use will look for that word.
 * The document it produces is a résumé by this repo's own definition, and its label — "McDowell"
 * — is what the picker actually shows.
 *
 * Where it disagrees with the general standard, the template wins and the disagreement is
 * recorded — the same rule resume_layout_jakes.js follows. The one that matters: the standard
 * asks for a single left-aligned column so a parser reads straight down. A centred cell still
 * reads down in the DOM here, because the row is a flex line and not a table, but the visual
 * centre is the template's and it is kept.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  const DATES = { joiner: ' – ', current: 'Present' };
  const span = content => L.dateRange(content, DATES);

  /** The template's three-cell row: left, centre, right. `L.marginRow` gives two cells and this
   *  layout is the reason a third exists — it is kept local rather than pushed into
   *  resume_layouts.js, because one layout wanting a shape is not yet a shared shape. */
  function threeCell(left, middle, right) {
    return '<div class="mc-row">' +
      '<span class="mc-l">' + left + '</span>' +
      '<span class="mc-c">' + middle + '</span>' +
      '<span class="mc-r">' + right + '</span></div>';
  }

  /* How each type this template knows fills the three cells. ONE definition, read by the
     renderers below AND by the `one-line-row` rule — the rule used to restate these
     compositions string for string, so changing a renderer's middle cell would have left the
     rule measuring text the page no longer printed, silently. A module-level constant rather
     than a property on the rule object: `runRules` calls `rule.check(doc, api)`, so `this` does
     bind, but one destructured call would break it with no error, and no other rule in the repo
     carries data that way. */
  const ROW_CELLS = {
    work_entry: c => [c.role, [c.company, c.place].filter(Boolean).join(', ')],
    degree_entry: c => [c.credential, [c.institution, c.place].filter(Boolean).join(', ')],
    award_entry: c => [c.title, [c.awarder, c.place].filter(Boolean).join(', ')],
    certification: c => [c.name, c.issuer],
    tech_project: c => [c.name, ''],
  };

  /** An entry: the three-cell row, then whatever hangs under it. */
  function entry(ctx, left, middle, right, extra) {
    return ctx.el('div', { class: 'mc-entry rb-entry' },
      threeCell(ctx.esc(left || ''), ctx.esc(middle || ''), ctx.esc(right || '')) +
      (extra || '') + L.groupChildren(ctx, 'mc-list'));
  }

  const render = {
    byShape: Object.assign({}, plain, {
      /* Field-agnostic, every one — a `byShape` strategy is handed component types that did not
         exist when it was written, so it may name a shape but never a field. */
      section: ctx => {
        const head = L.headAndRest(ctx).head;
        return ctx.el('section', { class: 'mc-section' },
          (head ? '<h2 class="mc-h">' + ctx.escLines(head.value) + '</h2>' : '') +
          L.groupChildren(ctx, 'mc-list'));
      },
      bullet: ctx => ctx.el('li', { class: 'mc-bullet' }, ctx.textOf(' ')),
      text: ctx => ctx.el('p', { class: 'mc-note' }, ctx.textOf(' ')),
      /* "Graduate Coursework: A, B, C" — the label bold, the rest run on after a colon. */
      line: ctx => {
        const { head, rest } = L.headAndRest(ctx);
        if (!head) return ctx.el('p', { class: 'mc-line' }, '');
        return ctx.el('p', { class: 'mc-line' }, rest.length
          ? '<b>' + ctx.escLines(head.value) + ':</b> ' +
            rest.map(f => ctx.escLines(f.value)).join(' &middot; ')
          : ctx.escLines(head.value));
      },
      /* An entry type this template has never heard of still gets the three-cell row: first
         declared field left, second centred, last right, anything left over on a line beneath.
         No field is named, so a component added after this file lands in the template's own
         shape rather than as a run-on paragraph. */
      entry: ctx => {
        const all = ctx.fields();
        if (!all.length) return ctx.el('div', { class: 'mc-entry rb-entry' }, ctx.children.join(''));
        const left = all[0].value;
        const right = all.length > 2 ? all[all.length - 1].value : '';
        const middle = all.length > 1 ? all[1].value : '';
        const spare = all.slice(2, all.length > 2 ? all.length - 1 : all.length);
        return entry(ctx, left, middle, right,
          spare.length ? '<p class="mc-line">' + spare.map(f => ctx.escLines(f.value)).join(' &middot; ') + '</p>' : '');
      },
    }),

    byType: {
      /* Name, then one contact line. The source keeps it to two lines and so does this. */
      header: ctx => {
        const c = ctx.content;
        const contact = [c.email, c.phone, c.link, c.locationLine, c.languages]
          .filter(Boolean).map(ctx.esc).join('<span class="mc-sep"> | </span>');
        return ctx.el('header', { class: 'mc-head' },
          '<h1 class="mc-name">' + ctx.esc(c.fullName || '') + '</h1>' +
          (contact ? '<p class="mc-contact">' + contact + '</p>' : ''));
      },

      /* Job title left, employer centred, dates right — the template's whole argument. The
         location goes into the middle cell behind a comma, because the row has three cells and
         four facts, and the source resolves that the same way. */
      work_entry: ctx => entry(ctx, ...ROW_CELLS.work_entry(ctx.content), span(ctx.content)),

      /* Degree left, institution centred, graduation right. */
      degree_entry: ctx => entry(ctx, ...ROW_CELLS.degree_entry(ctx.content), span(ctx.content)),

      education_entry: ctx => entry(ctx, ctx.content.credential, '', ctx.content.status),

      /* The stack has no cell of its own, so it goes on a line under the row in the smaller
         face — the same place the source puts a project's technologies. */
      tech_project: ctx => entry(ctx, ...ROW_CELLS.tech_project(ctx.content), span(ctx.content),
        ctx.content.tech ? '<p class="mc-tech">' + ctx.esc(ctx.content.tech) + '</p>' : ''),

      project_entry: ctx => entry(ctx, ctx.content.name, '', ''),

      /* "Additional Experience and Awards" is one of this template's own sections, so both
         components it carries get a real row rather than the generic one. */
      award_entry: ctx => entry(ctx, ...ROW_CELLS.award_entry(ctx.content), ctx.content.when),

      certification: ctx => entry(ctx, ...ROW_CELLS.certification(ctx.content), ctx.content.earned,
        ctx.content.credential
          ? '<p class="mc-tech">' + ctx.esc(ctx.content.credential) + '</p>' : ''),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      /* Generic fallbacks first, so a rule below can override one without depending on which
         selector the browser happened to see last. */
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line {',
      '      margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; font-weight: 700;',
      '      text-transform: uppercase; margin: 0 0 .3em;',
      '      border-bottom: 1px solid ' + theme.rule + '; }',

      s + ' { font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + '; }',
      s + ' .rb-slot { display: block; }',

      s + ' .mc-head { text-align: center; margin: 0 0 ' + theme.blockGap + 'em; }',
      /* Plain capitals and no tracking: a name set with `letter-spacing` or synthesised small
         caps extracts out of the printed PDF as "P RIYA V ENKATESH", and the name is the one
         string on the page that has to survive being read by a machine. */
      s + ' .mc-name { font-size: ' + theme.nameSize + 'pt; font-weight: 700; margin: 0 0 .2em; }',
      s + ' .mc-contact { font-size: ' + theme.metaSize + 'pt; margin: 0; }',

      s + ' .mc-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .mc-h { font-size: ' + theme.headSize + 'pt; font-weight: 700; text-transform: uppercase;',
      '      letter-spacing: .04em; margin: 0 0 .28em; padding-bottom: .1em;',
      '      border-bottom: 1px solid ' + theme.rule + '; }',

      s + ' .mc-entry { margin: 0 0 ' + theme.entryGap + 'em; page-break-inside: avoid; break-inside: avoid; }',
      /* The three cells. A flex row rather than a table: the source uses `tabu` because LaTeX
         has no other way to do it, and a table is the one layout device every ATS guide in this
         repo tells people to keep off a résumé. Equal basis with the centre cell centred gives
         the same optical result and still reads left-to-right in the DOM. */
      s + ' .mc-row { display: flex; align-items: baseline; gap: .6em; font-weight: 700;',
      '      font-size: ' + theme.titleSize + 'pt; }',
      s + ' .mc-l { flex: 1 1 0; text-align: left; }',
      s + ' .mc-c { flex: 1 1 0; text-align: center; }',
      s + ' .mc-r { flex: 1 1 0; text-align: right; white-space: nowrap; }',
      s + ' .mc-tech { font-size: ' + theme.metaSize + 'pt; margin: .1em 0 0; }',
      s + ' .mc-list { margin: .15em 0 0; padding-left: ' + theme.bulletIndent + 'in; }',
      s + ' .mc-bullet { margin: 0 0 .1em; }',
      s + ' .mc-line { margin: .1em 0 0; }',
      s + ' .mc-note { margin: 0 0 ' + theme.entryGap + 'em; }',
    ].join('\n');
  }

  /* The template's own section names, spelled as the source spells them. */
  function starter(b) {
    b.add('header', {})
      .section('Education', s => {
        s.add('degree_entry');
        s.add('skills_line', { label: 'Undergraduate Coursework', value: '' });
      })
      .section('Experience', s => s.add('work_entry'))
      .section('Projects', s => s.add('tech_project'))
      .section('Additional Experience and Awards', s => s.add('award_entry'))
      .section('Languages and Technologies', s => {
        s.add('skills_line', { label: 'Languages', value: '' });
        s.add('skills_line', { label: 'Technologies', value: '' });
      });
  }

  /** A worked example in the template's shape. What it teaches is the one-line entry and how
   *  much of a career fits when an entry costs one row instead of two — six dated blocks here. */
  function example(b) {
    b.add('header', {
      fullName: 'Daniel Okafor', email: 'daniel.okafor@gmail.com', phone: '+1 415 555 0148',
      link: 'github.com/dokafor | linkedin.com/in/dokafor', locationLine: 'San Francisco, CA',
    })
      .section('Education', s => {
        s.add('degree_entry', {
          credential: 'B.S. Computer Science, GPA 3.8',
          /* No `place`: the middle cell gets a third of the measure, and "University of
             Washington, Seattle, WA" is over it. Every standard here asks education for the
             graduation date alone, so the city was the cheapest thing on the row to lose. */
          institution: 'University of Washington', end: 'June 2017',
        });
        s.add('skills_line', {
          label: 'Undergraduate Coursework',
          value: 'Distributed Systems, Operating Systems, Compilers, Databases, Machine Learning',
        });
      })
      .section('Experience', s => {
        s.add('work_entry', {
          role: 'Staff Software Engineer', company: 'Stripe', place: 'San Francisco, CA',
          start: 'March 2023', current: true,
        }, e => {
          e.bullet('Rebuilt the ledger write path on an append-only log, taking a 6-hour nightly close down to 11 minutes across 4 billion rows');
          e.bullet('Led the review that found and closed a double-write window in the payouts service, which had produced 3 reconciliation breaks in a year');
          e.bullet('Mentored 5 engineers through the design-review process and wrote the template the team still uses');
        });
        s.add('work_entry', {
          role: 'Senior Software Engineer', company: 'Datadog', place: 'New York, NY',
          start: 'August 2020', end: 'February 2023',
        }, e => {
          e.bullet('Cut ingestion cost per million spans by 38% by moving the sampler off the hot path and batching writes to S3');
          e.bullet('Owned the query planner through a 4x growth in tenants, holding p99 dashboard load under 800ms');
        });
        s.add('work_entry', {
          role: 'Software Engineer', company: 'Amazon', place: 'Seattle, WA',
          start: 'July 2017', end: 'July 2020',
        }, e => {
          e.bullet('Shipped the inventory reservation service behind 2 million daily orders, replacing a job that ran once an hour');
          e.bullet('Removed 14,000 lines of a deprecated pricing path over 2 quarters with no change in order volume');
        });
      })
      .section('Projects', s => {
        s.add('tech_project', {
          name: 'Ledgerlint', tech: 'Rust, PostgreSQL',
          start: 'January 2024', end: 'Present',
        }, e => {
          e.bullet('Wrote a double-entry consistency checker that 40 people run in CI, after finding the same class of bug twice at work');
        });
      })
      .section('Additional Experience and Awards', s => {
        s.add('award_entry', {
          title: 'Engineering Excellence Award', awarder: 'Stripe',
          place: 'San Francisco, CA', when: 'December 2024',
        });
        s.add('award_entry', {
          title: 'DeepRacer League, 2 of 190',
          awarder: 'Amazon Web Services', when: 'November 2019',
        });
      })
      .section('Languages and Technologies', s => {
        s.add('skills_line', { label: 'Languages', value: 'Rust, Go, Java, Python, SQL, TypeScript' });
        s.add('skills_line', { label: 'Technologies', value: 'PostgreSQL, Kafka, Kubernetes, Terraform, AWS, Datadog' });
      });
  }

  const rules = [
    {
      /* The middle cell is the template's whole argument, and an empty one turns a three-cell
         row into a two-cell one that looks like a mistake rather than a choice. */
      id: 'centre-cell', label: 'Every job names its employer',
      check(doc, api) {
        return api.nodesOfType('work_entry')
          .filter(n => !String(api.content(n.id).company || '').trim())
          .map(n => ({ level: 'warn', nodeId: n.id,
            message: 'No employer. This template centres the organisation in the middle of the entry line, so with it empty the row prints as a title on the left and a date on the right with a hole between them.' }));
      },
    },
    {
      /* One row an entry is what this format buys, and it is bought back the moment a cell wraps
         — a long title and a long employer on a third of the measure each is two rows again, at
         which point Harvard classic does the same job and looks deliberate doing it. */
      id: 'one-line-row', label: 'An entry line fits on one line',
      /* Every type this layout renders as a three-cell row, not just the job. It checked
         `work_entry` alone until the layout's own worked example was looked at on a real page:
         the education row and one award row both wrapped, the rule said nothing about either,
         and the example that teaches a user what this format is for was demonstrating the one
         thing it is not for. `ROW_CELLS` is the renderers' own composition, read here rather
         than restated, so the rule cannot drift from what the page prints. */
      check(doc, api) {
        const out = [];
        for (const type of Object.keys(ROW_CELLS)) {
          for (const n of api.nodesOfType(type)) {
            /* Each entry's OWN column, divided three ways. This layout has one column today, so
               every answer is the same one — but asking per node is what keeps it right if that
               stops being true, and measuring some other node's slot only looked like it did. */
            const cell = L.charsIn(api.measure(n), +api.theme.titleSize || 11, 0) / 3;
            const [left, middle] = ROW_CELLS[type](api.content(n.id));
            const longest = Math.max(String(left || '').length, String(middle || '').length);
            if (longest > cell) {
              out.push({ level: 'note', nodeId: n.id,
                message: 'The entry line will wrap — each of its three cells gets about ' +
                  Math.round(cell) + ' characters and this one needs ' + longest +
                  '. One row an entry is what this format is for; shorten the title or drop the city.' });
            }
          }
        }
        return out;
      },
    },
    {
      /* Coursework belongs inside Education here, not in a section of its own — the source is
         explicit about it and it is the difference between this template and Deedy. */
      id: 'coursework-inline', label: 'Coursework sits inside Education',
      check(doc, api) {
        return api.nodesOfType('section')
          .filter(n => /^\s*(?:relevant\s+)?coursework\s*$/i.test(String(api.content(n.id).title || '')))
          .map(n => ({ level: 'note', nodeId: n.id,
            message: 'This template writes coursework as a line inside Education — “Undergraduate Coursework: …” — rather than as a section of its own. A section here costs a heading and a rule for one line of text.' }));
      },
    },
  ];

  L.define({
    id: 'mcdowell-cv',
    label: 'McDowell',
    summary: 'Single column · one bold line an entry · employer centred between title and dates',
    credit: 'Format: Gayle Laakmann McDowell, “Cracking the Coding Interview”. ' +
      'LaTeX implementation: dnl-blkv (MIT), github.com/dnl-blkv/mcdowell-cv.',
    blurb: 'The résumé format from “Cracking the Coding Interview”, which is the book most ' +
      'software candidates prepare from. Its one idea is that an entry takes a single line — job ' +
      'title on the left, employer centred, dates on the right, all bold — so a longer career ' +
      'fits on the one page every standard here asks for. Choose it when you have five or six ' +
      'jobs to show and Jake’s Resume or Harvard classic is running you onto a second page. A ' +
      'poor choice if your titles or employers are long: the line has three cells and each gets ' +
      'a third of the width, and once one wraps the format has bought you nothing.',
    page: { width: 8.5, height: 11, margin: 0.6, unit: 'in' },
    tokens: {
      bodyFont: 'Calibri, Carlito, "Segoe UI", Helvetica, Arial, sans-serif',
      ink: '#000000', rule: '#000000',
      nameSize: 18, headSize: 11, titleSize: 10.5, bodySize: 10, metaSize: 9.5,
      bodyLead: 1.25, blockGap: 0.7, entryGap: 0.5,
      bulletIndent: 0.24,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8.5, max: 12, step: 0.5, unit: 'pt' },
      { key: 'titleSize', label: 'Entry line size', kind: 'range', min: 9, max: 13, step: 0.5, unit: 'pt' },
      { key: 'headSize', label: 'Heading size', kind: 'range', min: 9, max: 15, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 14, max: 28, step: 1, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.7, step: 0.05, unit: '' },
      { key: 'entryGap', label: 'Space between entries', kind: 'range', min: 0, max: 1.5, step: 0.05, unit: 'em' },
      { key: 'blockGap', label: 'Space between sections', kind: 'range', min: 0, max: 2, step: 0.1, unit: 'em' },
      { key: 'bulletIndent', label: 'Bullet indent', kind: 'range', min: 0.1, max: 0.6, step: 0.02, unit: 'in' },
      { key: 'bodyFont', label: 'Font', kind: 'select', unit: '',
        options: [
          ['Calibri, Carlito, "Segoe UI", Helvetica, Arial, sans-serif', 'Calibri'],
          ['Arial, Helvetica, sans-serif', 'Arial'],
          ['Georgia, "Times New Roman", serif', 'Georgia'],
        ] },
      { key: 'rule', label: 'Heading rule', kind: 'color', unit: '' },
    ],
    slots: [{ id: 'main', label: 'The column', grow: 1 }],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    css, render, starter, example, rules,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
