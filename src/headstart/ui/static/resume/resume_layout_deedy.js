/* The Deedy résumé — the asymmetric two-column shape, and the only one here with no right margin.
 *
 * Source: github.com/deedy/Deedy-Resume by Debarghya Das, Apache-2.0. 5,055 stars / 1,288 forks,
 * read from the GitHub API on 2026-09-10; also carried in the Overleaf gallery, which is where
 * most of its users meet it. Its last commit is 2015-12-02 — this is an old template that stayed
 * popular, and the age is stated rather than hidden because it is the reason a candidate should
 * check the arrangement suits them rather than assume it is current advice.
 *
 * Why it earns a file when the picker already holds two two-column layouts. Both of those put the
 * WIDE column first and align something against the right margin — `two-column` floats the dates
 * there with `margin-left:auto`, `modern-sidebar` runs a coloured band beside the history. Deedy
 * does neither, and the two differences are the whole template:
 *
 *   1. The narrow column comes FIRST, on the left, at 0.33 of the measure against 0.66 — a
 *      `\begin{minipage}[t]{0.33\textwidth}` and a `\hfill` in the source. Sections are
 *      column-scoped: Education, Links, Coursework and Skills live in the narrow one, Experience,
 *      Research and Awards in the wide one, and neither column knows about the other.
 *   2. An entry has NO right-aligned cell at all. It is three stacked left-aligned lines —
 *      `\subsection{organisation}`, then `\descript{title}`, then `\location{date | place}` with
 *      a literal pipe between the two. Every other layout in this builder puts the date against
 *      a margin; this one puts it on its own third line, in a lighter grey, behind a pipe. That
 *      is what makes a Deedy résumé recognisable across a room, and it is why a narrow column
 *      can hold a real dated entry at all — there is no date column to run out of.
 *
 * The name is full width above both columns, so this layout declares a third slot for it and
 * lays the three out on a CSS grid rather than the flex row the two-slot layouts use. `main` is
 * still declared first, so a résumé arriving from a single-column layout lands in the wide column
 * and not in the narrow one — the same rule `modern-sidebar` is held to.
 *
 * Deedy-Resume-Reversed is offered as a tunable rather than as a second file, and that is a
 * measured call rather than a shortcut: the reversed edition swaps the two columns and thickens a
 * weight, which is a reflow of this arrangement and not another one. Two files would have been
 * two copies of everything below. The Overleaf listing for it still credits `ZDTaylor`, an
 * account that returned 404 on 2026-09-10, so there is no maintained upstream to track.
 *
 * One thing this template does that this builder will not: `\section{Publications}` on a bibtex
 * bibliography. See ADR-0130 — the corpus here is software-engineering roles and the standards
 * this builder is calibrated against never mention publications at all.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  /* The source writes a range as "Jun 2013 – Sep 2013" and glues the place on behind a pipe;
     "Present" rather than "Current" is what every English-language standard here asks for. */
  const DATES = { joiner: ' – ', current: 'Present' };
  const span = content => L.dateRange(content, DATES);

  /** `\location{date | place}` — the third line of an entry: the facts that are not the title
   *  or the sub-line, pipe-separated, in the order given. Named `tail` rather than `place`
   *  because not every type's second fact is a place — a certification's is its credential id,
   *  and a parameter called `place` carrying one is a comment that lies in the signature. */
  function tailLine(ctx, ...facts) {
    const parts = facts.filter(Boolean).map(ctx.esc);
    return parts.length
      ? '<div class="dy-loc">' + parts.join('<span class="dy-pipe"> | </span>') + '</div>'
      : '';
  }

  /** The three stacked lines every dated block in this template is built from. */
  function stacked(ctx, head, sub, tail, kids) {
    return ctx.el('div', { class: 'dy-entry rb-entry' },
      (head ? '<div class="dy-head">' + ctx.esc(head) + '</div>' : '') +
      (sub ? '<div class="dy-sub">' + ctx.esc(sub) + '</div>' : '') +
      tailLine(ctx, ...(Array.isArray(tail) ? tail : [tail])) +
      (kids == null ? L.groupChildren(ctx, 'dy-list') : kids));
  }

  const render = {
    byShape: Object.assign({}, plain, {
      /* Not one of these names a field — a `byShape` strategy is handed component types that did
         not exist when it was written, and `L.headAndRest` gives it "the first declared field and
         the others" without needing to know what either is called. */
      section: ctx => {
        const head = L.headAndRest(ctx).head;
        return ctx.el('section', { class: 'dy-section' },
          (head ? '<h2 class="dy-h">' + ctx.escLines(head.value) + '</h2>' : '') +
          L.groupChildren(ctx, 'dy-list'));
      },
      bullet: ctx => ctx.el('li', { class: 'dy-bullet' }, ctx.textOf(' ')),
      text: ctx => ctx.el('p', { class: 'dy-note' }, ctx.textOf(' ')),
      /* `Links` and `Coursework` are both this shape in the source, and both read as a label and
         then the things under it — Deedy sets the label in the body face and the value in bold. */
      line: ctx => {
        const { head, rest } = L.headAndRest(ctx);
        if (!head) return ctx.el('p', { class: 'dy-line' }, '');
        return ctx.el('p', { class: 'dy-line' }, rest.length
          ? ctx.escLines(head.value) + '<span class="dy-slash">//</span><b>' +
            rest.map(f => ctx.escLines(f.value)).join(' &middot; ') + '</b>'
          : ctx.escLines(head.value));
      },
      /* Any entry type this template has never heard of still gets its three-line stack, built
         out of whatever fields the component declares: the first is the head, the second the
         sub-line, and everything after it goes on the location line behind pipes. No field is
         named, so a component added after this file still looks like a Deedy entry. */
      entry: ctx => {
        const { head, rest } = L.headAndRest(ctx);
        return stacked(ctx, head ? head.value : '', rest.length ? rest[0].value : '',
          rest.slice(1).map(f => f.value).join(' · '));
      },
    }),

    byType: {
      /* The name is `\Huge` with the given name in a light weight and the family name bold —
         one <h1>, so it still extracts as one string. Real letterforms and no small caps: a
         synthesised small-cap name comes out of a printed PDF as "D EBARGHYA D AS". */
      header: ctx => {
        const c = ctx.content;
        const names = String(c.fullName || '').trim().split(/\s+/);
        const nameHtml = names.length > 1
          ? ctx.esc(names.slice(0, -1).join(' ')) + ' <b>' + ctx.esc(names[names.length - 1]) + '</b>'
          : ctx.esc(c.fullName || '');
        const contact = [c.email, c.phone, c.link, c.locationLine, c.languages]
          .filter(Boolean).map(ctx.esc).join('<span class="dy-pipe"> | </span>');
        return ctx.el('header', { class: 'dy-head-block' },
          '<h1 class="dy-name">' + nameHtml + '</h1>' +
          (contact ? '<p class="dy-contact">' + contact + '</p>' : ''));
      },

      /* Experience: employer, then the job title, then "dates | place". */
      work_entry: ctx => stacked(ctx, ctx.content.company, ctx.content.role,
        [span(ctx.content), ctx.content.place]),

      /* Education is the same three lines with the institution on top — Deedy's narrow column
         carries it, and the degree is the sub-line. */
      degree_entry: ctx => stacked(ctx, ctx.content.institution, ctx.content.credential,
        [span(ctx.content), ctx.content.place]),

      /* A project's stack takes the place of the job title, which is what it is here. */
      tech_project: ctx => stacked(ctx, ctx.content.name, ctx.content.tech, span(ctx.content)),

      project_entry: ctx => stacked(ctx, ctx.content.name, '', ''),

      /* The compressed one-line education block, kept as two lines rather than three. */
      education_entry: ctx => ctx.el('div', { class: 'dy-entry dy-thin rb-entry' },
        '<div class="dy-head">' + ctx.esc(ctx.content.credential || '') + '</div>' +
        (ctx.content.status ? '<div class="dy-loc">' + ctx.esc(ctx.content.status) + '</div>' : '')),

      /* `\section{Awards}` is a `tabular{rll}` of year, rank and award in the source — three
         cells on one row, the year first. Rebuilt here as the same reading order in this
         template's own type rather than as a table, because a table is the one layout device
         every ATS guide in this repo tells people to keep off a résumé. */
      award_entry: ctx => ctx.el('div', { class: 'dy-entry dy-thin rb-entry' },
        (ctx.content.when ? '<span class="dy-year">' + ctx.esc(ctx.content.when) + '</span>' : '') +
        '<span class="dy-award">' + ctx.esc(ctx.content.title || '') + '</span>' +
        ([ctx.content.awarder, ctx.content.place].filter(Boolean).length
          ? '<div class="dy-loc">' + [ctx.content.awarder, ctx.content.place]
            .filter(Boolean).map(ctx.esc).join('<span class="dy-pipe"> | </span>') + '</div>'
          : '')),

      certification: ctx => stacked(ctx, ctx.content.name, ctx.content.issuer,
        [ctx.content.earned, ctx.content.credential]),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      /* The generic fallbacks first, so a layout rule below can override one without depending
         on which selector a browser saw last. */
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line {',
      '      margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; font-weight: 700;',
      '      text-transform: uppercase; color: ' + theme.accent + '; margin: 0 0 .3em; }',

      /* Three regions on a grid: the name across the top, then the narrow column and the wide
         one. `columnOrder` is what makes this the reversed edition — the grid columns swap and
         the two areas swap with them, so no block moves and no word changes. */
      s + ' { font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + ';',
      '      display: grid; gap: 0 ' + theme.columnGap + 'in;',
      '      grid-template-columns: ' + (theme.columnOrder === 'reversed'
        ? '1.94fr 1fr' : '1fr 1.94fr') + ';',
      '      grid-template-areas: "top top" ' + (theme.columnOrder === 'reversed'
        ? '"main side"' : '"side main"') + '; align-items: start; }',
      s + ' .rb-slot[data-slot="top"] { grid-area: top; }',
      s + ' .rb-slot[data-slot="side"] { grid-area: side; }',
      s + ' .rb-slot[data-slot="main"] { grid-area: main; }',

      /* `\Huge` and centred, with the family name the only bold thing on the page above the
         section headings. */
      s + ' .dy-head-block { text-align: center; margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .dy-name { font-size: ' + theme.nameSize + 'pt; font-weight: 300; letter-spacing: .01em;',
      '      margin: 0 0 .18em; color: ' + theme.ink + '; }',
      s + ' .dy-name b { font-weight: 700; }',
      s + ' .dy-contact { font-size: ' + theme.metaSize + 'pt; margin: 0; color: ' + theme.muted + '; }',

      /* `\titleformat{\section}` is small-caps blue with a rule beneath. Capitals rather than
         `font-variant: small-caps`, for the extraction reason above. */
      s + ' .dy-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .dy-h { font-size: ' + theme.headSize + 'pt; font-weight: 700; text-transform: uppercase;',
      '      letter-spacing: .08em; color: ' + theme.accent + '; margin: 0 0 .3em;',
      '      padding-bottom: .1em; border-bottom: 1px solid ' + theme.rule + '; }',

      /* Three stacked left-aligned lines, and nothing anywhere against a right margin. */
      s + ' .dy-entry { margin: 0 0 ' + theme.entryGap + 'em; page-break-inside: avoid; break-inside: avoid; }',
      s + ' .dy-thin { margin-bottom: ' + (theme.entryGap / 2) + 'em; }',
      s + ' .dy-head { font-size: ' + theme.titleSize + 'pt; font-weight: 700; }',
      s + ' .dy-sub { font-style: italic; }',
      s + ' .dy-loc { font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + '; }',
      s + ' .dy-pipe { color: ' + theme.muted + '; }',
      s + ' .dy-year { font-weight: 700; color: ' + theme.accent + '; margin-right: .5em; }',
      s + ' .dy-award { font-weight: 700; }',
      s + ' .dy-slash { color: ' + theme.accent + '; margin: 0 .3em; }',
      s + ' .dy-list { margin: .18em 0 0; padding-left: ' + theme.bulletIndent + 'in; }',
      s + ' .dy-bullet { margin: 0 0 .1em; }',
      s + ' .dy-line { margin: 0 0 .16em; }',
      s + ' .dy-note { margin: 0 0 ' + theme.entryGap + 'em; }',
    ].join('\n');
  }

  /* The source's own split, section names included: the narrow column carries what is short and
     undated-ish, the wide one carries the history. */
  function starter(b) {
    b.add('header', {}).into('top');
    b.section('Education', s => s.add('degree_entry')).into('side');
    b.section('Links', s => {
      s.add('profile_line', { network: 'GitHub', url: '' });
      s.add('profile_line', { network: 'LinkedIn', url: '' });
    }).into('side');
    b.section('Coursework', s => s.add('skills_line', { label: 'Relevant Coursework', value: '' })).into('side');
    b.section('Skills', s => {
      s.add('skills_line', { label: 'Languages', value: '' });
      s.add('skills_line', { label: 'Tools', value: '' });
    }).into('side');
    b.section('Experience', s => s.add('work_entry'));
    b.section('Projects', s => s.add('tech_project'));
    b.section('Awards', s => s.add('award_entry'));
  }

  /** A worked example in the template's shape, in HeadStart's own words. What it teaches is the
   *  column split and the three-line entry — and the Awards block, which is one of the two
   *  components this layout brought with it. */
  function example(b) {
    b.add('header', {
      fullName: 'Priya Venkatesh', email: 'priya.venkatesh@gmail.com', phone: '+91 99400 21873',
      link: 'github.com/pvenkatesh | linkedin.com/in/pvenkatesh',
      locationLine: 'Chennai, India',
    }).into('top');

    b.section('Education', s => {
      s.add('degree_entry', {
        institution: 'National Institute of Technology Tiruchirappalli',
        credential: 'B.Tech Computer Science, CGPA 9.1 / 10',
        place: 'Tiruchirappalli, India', start: 'July 2017', end: 'May 2021',
      });
    }).into('side');

    b.section('Links', s => {
      s.add('profile_line', { network: 'GitHub', url: 'github.com/pvenkatesh' });
      s.add('profile_line', { network: 'LinkedIn', url: 'linkedin.com/in/pvenkatesh' });
    }).into('side');

    b.section('Coursework', s => {
      s.add('skills_line', { label: 'Undergraduate', value: 'Distributed Systems, Compilers, Operating Systems, Databases, Networks' });
    }).into('side');

    b.section('Skills', s => {
      s.add('skills_line', { label: 'Languages', value: 'Go, Java, Python, SQL, TypeScript' });
      s.add('skills_line', { label: 'Infrastructure', value: 'Kubernetes, Kafka, Terraform, AWS, Prometheus' });
    }).into('side');

    b.section('Certifications', s => {
      s.add('certification', {
        name: 'Certified Kubernetes Administrator', issuer: 'Cloud Native Computing Foundation',
        earned: 'September 2024', credential: 'LF-9f2c11a0be',
      });
    }).into('side');

    b.section('Experience', s => {
      s.add('work_entry', {
        company: 'Freshworks', role: 'Senior Software Engineer', place: 'Chennai, India',
        start: 'April 2023', current: true,
      }, e => {
        e.bullet('Split the notification pipeline into 4 Kafka consumer groups, which took the p99 delivery lag from 40 seconds to 900ms at 12,000 messages a second');
        e.bullet('Wrote the tenant-isolation layer that let 3 regulated customers move off dedicated clusters, retiring 11 machines a year');
        e.bullet('Ran the migration off a shared Redis instance over 6 weeks with no customer-visible downtime, and wrote the runbook the team now uses for every cutover');
      });
      s.add('work_entry', {
        company: 'Zoho', role: 'Software Engineer', place: 'Chennai, India',
        start: 'June 2021', end: 'March 2023',
      }, e => {
        e.bullet('Rebuilt the report scheduler on a work queue, cutting failed overnight exports from 90 a week to 2');
        e.bullet('Added covering indexes to the 3 queries behind 70% of database time, dropping median API latency from 620ms to 180ms');
      });
    });

    b.section('Projects', s => {
      s.add('tech_project', {
        name: 'Kessel', tech: 'Go, SQLite, Raft',
        start: 'February 2024', end: 'Present',
      }, e => {
        e.bullet('Built a single-binary replicated key-value store to learn Raft properly, and ran it through 200 randomised partition tests before publishing it');
      });
    });

    b.section('Awards', s => {
      s.add('award_entry', {
        title: '1st of 340 teams, Smart India Hackathon',
        awarder: 'Ministry of Education, Government of India',
        place: 'New Delhi, India', when: 'August 2020',
      });
      s.add('award_entry', {
        title: 'ACM-ICPC Regionals, rank 14 of 412',
        awarder: 'ACM India', place: 'Amritapuri, India', when: 'December 2019',
      });
    });
  }

  /** Give an arriving document the three regions, so the page is not a narrow column of nothing
   *  beside everything. Only the slot — a Layer-2 fact — is written; no word moves and no block
   *  is dropped, so switching back to a single column restores the original order. */
  const SIDE_TYPES = new Set(['skills_line', 'language_line', 'certification', 'profile_line']);

  function adopt(doc) {
    for (const node of doc.root.children) {
      if (node.type === 'header') { node.slot = 'top'; continue; }
      const short = SIDE_TYPES.has(node.type) ||
        (node.type === 'section' && node.children.length &&
          node.children.every(child => SIDE_TYPES.has(child.type) ||
            child.type === 'degree_entry' || child.type === 'education_entry'));
      node.slot = short ? 'side' : 'main';
    }
  }

  const rules = [
    {
      id: 'narrow-column', label: 'The narrow column holds short blocks',
      check(doc, api) {
        const out = [];
        for (const node of doc.root.children) {
          if (node.slot !== 'side') continue;
          const long = [node].concat(node.children)
            .filter(n => n.type === 'work_entry' || n.type === 'tech_project');
          if (long.length) {
            out.push({ level: 'warn', nodeId: node.id,
              message: 'A job or project is in the narrow column. It is a third of the measure — a bullet that reads as two lines beside it reads as five in here, and the template puts the history in the wide column for that reason. Drag it across.' });
          }
        }
        return out;
      },
    },
    {
      id: 'both-columns', label: 'Both columns carry something',
      check(doc, api) {
        const tops = doc.root.children.filter(n => n.type !== 'header');
        if (tops.length < 3) return [];
        for (const slot of ['side', 'main']) {
          if (!tops.some(n => n.slot === slot)) {
            return [{ level: 'note', nodeId: null,
              message: 'The ' + (slot === 'side' ? 'narrow' : 'wide') + ' column is empty. This template is an asymmetric split; with one column filled it is a single-column résumé with a third of the page wasted, and Harvard classic does that better.' }];
          }
        }
        return [];
      },
    },
    {
      /* The template's own third line, and the reason it is worth a rule: this is the only
         layout in the picker where a missing date does not leave a visible hole. A blank
         right-hand cell is obvious on Jake's Resume; a missing `\location` line here just is
         not there, so the baseline's `dates` error can be true and invisible at the same time. */
      id: 'stacked-dates', label: 'A dated block shows its dates',
      check(doc, api) {
        const out = [];
        for (const type of ['tech_project', 'degree_entry']) {
          for (const n of api.nodesOfType(type)) {
            const c = api.content(n.id);
            if (!String(c.start || '').trim() && !String(c.end || '').trim() && !c.current) {
              out.push({ level: 'note', nodeId: n.id,
                message: 'No dates. This template prints them on a third line rather than against a margin, so an entry with none looks finished instead of looking empty — which is exactly how a date goes missing without anyone noticing.' });
            }
          }
        }
        return out;
      },
    },
  ];

  L.define({
    id: 'deedy-resume',
    label: 'Deedy',
    summary: 'Narrow column left · wide column right · dates on a third line, never at a margin',
    credit: 'Template: “Deedy Resume” by Debarghya Das (Apache-2.0), github.com/deedy/Deedy-Resume.',
    blurb: 'The asymmetric two-column résumé a lot of computer-science students meet on Overleaf: ' +
      'a narrow left column for education, links, coursework and skills, a wide right column for ' +
      'the work. Its entries stack three lines and align nothing to the right, which is what lets ' +
      'a real dated job sit in a third of the page. Good where a person reads the résumé and you ' +
      'have more short facts than long ones. Two cautions: some applicant tracking systems read ' +
      'straight across a two-column page and interleave the columns, so Harvard classic or ' +
      'Jake’s Resume are the safer send; and the template has not been touched upstream since ' +
      '2015, so treat its section list as one author’s, not as current advice.',
    page: { width: 8.5, height: 11, margin: 0.55, unit: 'in' },
    tokens: {
      /* Deedy sets the whole document in Lato, with the name in Lato Light. */
      bodyFont: 'Lato, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif',
      ink: '#111111', muted: '#666666',
      accent: '#0E5A8A', rule: '#CCCCCC',
      nameSize: 30, headSize: 11, titleSize: 11, bodySize: 9.5, metaSize: 8.5,
      bodyLead: 1.3, blockGap: 0.8, entryGap: 0.55, columnGap: 0.3,
      bulletIndent: 0.22,
      columnOrder: 'classic',
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 8.5, max: 12, step: 0.5, unit: 'pt' },
      { key: 'titleSize', label: 'Entry title size', kind: 'range', min: 9, max: 13, step: 0.5, unit: 'pt' },
      { key: 'headSize', label: 'Heading size', kind: 'range', min: 9, max: 15, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 18, max: 40, step: 1, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.7, step: 0.05, unit: '' },
      { key: 'entryGap', label: 'Space between entries', kind: 'range', min: 0, max: 1.5, step: 0.05, unit: 'em' },
      { key: 'blockGap', label: 'Space between sections', kind: 'range', min: 0, max: 2, step: 0.1, unit: 'em' },
      { key: 'columnGap', label: 'Gap between columns', kind: 'range', min: 0.15, max: 0.7, step: 0.05, unit: 'in' },
      { key: 'accent', label: 'Heading colour', kind: 'color', unit: '' },
      { key: 'rule', label: 'Rule colour', kind: 'color', unit: '' },
      /* The reversed edition, which is a published variant of this template and not another
         one — it swaps the columns and nothing else. A tunable rather than a second file. */
      { key: 'columnOrder', label: 'Column order', kind: 'select', unit: '',
        options: [['classic', 'Narrow left (Deedy)'], ['reversed', 'Narrow right (Deedy Reversed)']] },
      { key: 'bodyFont', label: 'Font', kind: 'select', unit: '',
        options: [
          ['Lato, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif', 'Lato (what the template sets)'],
          ['Calibri, Carlito, sans-serif', 'Calibri'],
          ['Arial, Helvetica, sans-serif', 'Arial'],
        ] },
    ],
    /* `main` first, so a document arriving from a single-column layout lands in the WIDE column
       and never inside the narrow one — the same rule `modern-sidebar` is held to, and the grid
       above is what puts the narrow column on the left regardless of this order. */
    slots: [
      { id: 'main', label: 'Wide column', grow: 1.94 },
      { id: 'side', label: 'Narrow column', grow: 1 },
      { id: 'top', label: 'Across the top', grow: 1 },
    ],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    /* Three slots, but `top` SPANS the other two rather than sitting beside them, so the default
       share-by-`grow` would divide the measure three ways and hand the wide column half of it
       instead of two thirds. The grid in `css` is the authority and this repeats its two ratios:
       1 : 1.94 across the columns, and the whole measure for the band above them. */
    measure(slotId, page, theme) {
      const full = page.width - 2 * page.margin;
      if (slotId === 'top') return full;
      const gap = +theme.columnGap || 0;
      return (full - gap) * (slotId === 'side' ? 1 / 2.94 : 1.94 / 2.94);
    },
    css, render, starter, example, adopt, rules,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
