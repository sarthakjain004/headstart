/* The Harvard classic — the reverse-chronological résumé every careers office hands out.
 *
 * Source: the Harvard College "Resumes & Cover Letters" guide and its bullet-point résumé
 * template (Mignone Center for Career Success, careerservices.fas.harvard.edu), plus this
 * machine's own `resume-builder` skill, which packages the same standard with numbers attached:
 * 0.75in margins, name 16pt, section headings 11.5pt bold with a rule under them, body 10.5pt,
 * 13pt leading, Calibri. Harvard's own guidance states 10–12pt and names no face.
 *
 * Why this earns a slot when the builder already ships a strict single column: the Headless
 * Headhunter layout is one guru's template and says so — Arial, "to Current", education
 * compressed to a line, skills hidden inside the job bullets, a period on every bullet. This is
 * the neutral standard the rest of the world checks against, and it disagrees with that layout
 * on nearly every one of those points. Both are here, both state their own rules, and the rules
 * contradict each other on purpose: that is what "a rule belongs to a Layout" means. The clearest
 * case is a bullet's final period — `one-sentence` in the Headless Headhunter layout permits
 * exactly one, `no-terminal-period` here says a bullet is not a sentence and takes none.
 *
 * Section order is Harvard's own: Education, Experience, Leadership & Activities, Skills &
 * Interests. Education leads because the template is written for someone leaving university; the
 * standard itself says to move it below Experience after about three years, and doing that is a
 * drag. The four-corner entry is transposed from Jake's Resume and the transposition is the
 * point — here the ORGANISATION is the bold line and the title is the italic one under it:
 *
 *     Employer (bold, left)          Location (right)
 *     Job title (italic, left)       Dates (italic, right)
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;
  const plain = L.plainStrategies();

  /* "March 2022 – Present". The standard is explicit that a current role reads "Present" and
     never "Current" or "Now", and that the dash is an en dash with a space either side. */
  const DATES = { joiner: ' – ', current: 'Present' };
  const span = content => L.dateRange(content, DATES);

  const row = (cls, left, right) => L.marginRow('hv-row ' + cls, left, right);

  const render = {
    byShape: Object.assign({}, plain, {
      /* Field-agnostic, every one of them — see the note in resume_layout_jakes.js. */
      section: ctx => {
        const head = L.headAndRest(ctx).head;
        return ctx.el('section', { class: 'hv-section' },
          (head ? '<h2 class="hv-h">' + ctx.escLines(head.value) + '</h2>' : '') +
          L.groupChildren(ctx, 'hv-list'));
      },
      bullet: ctx => ctx.el('li', { class: 'hv-bullet' }, ctx.textOf(' ')),
      text: ctx => ctx.el('p', { class: 'hv-note' }, ctx.textOf(' ')),
      line: ctx => {
        const parts = L.headAndRest(ctx);
        if (!parts.head) return ctx.el('p', { class: 'hv-line' }, '');
        return ctx.el('p', { class: 'hv-line' }, parts.rest.length
          ? '<b>' + ctx.escLines(parts.head.value) + ':</b> ' +
            parts.rest.map(f => ctx.escLines(f.value)).join(' · ')
          : ctx.escLines(parts.head.value));
      },
    }),

    byType: {
      header: ctx => {
        const c = ctx.content;
        const contact = [c.email, c.phone, c.link, c.locationLine].filter(Boolean)
          .map(ctx.esc).join('<span class="hv-sep"> | </span>');
        return ctx.el('header', { class: 'hv-head' },
          '<h1 class="hv-name">' + ctx.esc(c.fullName) + '</h1>' +
          (contact ? '<p class="hv-contact">' + contact + '</p>' : '') +
          (c.languages ? '<p class="hv-contact">' + ctx.esc(c.languages) + '</p>' : ''));
      },

      /* Employer on the bold line, title in italic under it — the opposite way round from
         Jake's Resume, and the fastest way to tell the two apart on a printed page. */
      work_entry: ctx => ctx.el('div', { class: 'hv-entry' },
        row('hv-top', '<b>' + ctx.esc(ctx.content.company || '') + '</b>',
          ctx.esc(ctx.content.place || '')) +
        row('hv-sub', '<i>' + ctx.esc(ctx.content.role || '') + '</i>',
          '<i>' + ctx.esc(span(ctx.content)) + '</i>') +
        L.groupChildren(ctx, 'hv-list')),

      degree_entry: ctx => ctx.el('div', { class: 'hv-entry' },
        row('hv-top', '<b>' + ctx.esc(ctx.content.institution || '') + '</b>',
          ctx.esc(ctx.content.place || '')) +
        row('hv-sub', '<i>' + ctx.esc(ctx.content.credential || '') + '</i>',
          '<i>' + ctx.esc(span(ctx.content)) + '</i>') +
        L.groupChildren(ctx, 'hv-list')),

      tech_project: ctx => ctx.el('div', { class: 'hv-entry' },
        row('hv-top', '<b>' + ctx.esc(ctx.content.name || '') + '</b>',
          '<i>' + ctx.esc(span(ctx.content)) + '</i>') +
        (ctx.content.tech ? '<div class="hv-tech"><i>Technologies: ' + ctx.esc(ctx.content.tech) + '</i></div>' : '') +
        L.groupChildren(ctx, 'hv-list')),

      education_entry: ctx => ctx.el('div', { class: 'hv-entry hv-thin' },
        row('hv-top', '<b>' + ctx.esc(ctx.content.credential || '') + '</b>',
          '<i>' + ctx.esc(ctx.content.status || '') + '</i>')),

      project_entry: ctx => ctx.el('div', { class: 'hv-entry' },
        row('hv-top', '<b>' + ctx.esc(ctx.content.name || '') + '</b>', '') + L.groupChildren(ctx, 'hv-list')),
    },
  };

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    return [
      s + ' { font-family: ' + theme.bodyFont + '; color: ' + theme.ink + ';',
      '      font-size: ' + theme.bodySize + 'pt; line-height: ' + theme.bodyLead + '; }',
      s + ' .rb-slot { display: block; }',
      s + ' .hv-head { text-align: center; margin: 0 0 ' + theme.blockGap + 'em; }',
      /* Plain text, no letter-spacing and no small caps. A name tracked out for looks comes back
         out of the printed PDF as "S ARTHAK J AIN" when the text is extracted, which is the one
         string on the page that has to survive being read by a machine. The skill's
         render-pipeline notes flag it by name. */
      s + ' .hv-name { font-size: ' + theme.nameSize + 'pt; font-weight: 700; margin: 0 0 .2em; }',
      s + ' .hv-contact { font-size: ' + theme.bodySize + 'pt; margin: 0; }',
      s + ' .hv-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      /* Bold, upper case, with a solid rule under it — the guide's one visual separator. It
         also happens to be the shape an ATS parser is most likely to recognise as a heading. */
      s + ' .hv-h { font-size: ' + theme.headSize + 'pt; font-weight: 700; text-transform: uppercase;',
      '      letter-spacing: .03em; margin: 0 0 .3em; padding-bottom: .12em;',
      '      border-bottom: 1px solid ' + theme.rule + '; }',
      s + ' .hv-entry { margin: 0 0 ' + theme.entryGap + 'em; page-break-inside: avoid; break-inside: avoid; }',
      s + ' .hv-thin { margin-bottom: .25em; }',
      s + ' .hv-row { display: flex; gap: 1em; align-items: baseline; }',
      /* The two cells are the framework's (`L.marginRow`), so the class names are its own. */
      s + ' .rb-row-left { flex: 1 1 auto; }',
      s + ' .rb-row-right { flex: 0 0 auto; text-align: right; white-space: nowrap; }',
      /* 9.5pt italic in a grey that still prints — the standard's own treatment for the line
         that lists what a piece of work was built with. */
      s + ' .hv-tech { font-size: ' + theme.metaSize + 'pt; color: ' + theme.muted + '; margin: .1em 0 0; }',
      s + ' .hv-list { margin: .15em 0 0; padding-left: ' + theme.bulletIndent + 'in; }',
      s + ' .hv-bullet { margin: 0 0 .1em; }',
      s + ' .hv-line { margin: 0 0 .12em; }',
      s + ' .hv-note { margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-header, ' + s + ' .rb-entry, ' + s + ' .rb-text, ' + s + ' .rb-line {',
      '      margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-section h2 { font-size: ' + theme.headSize + 'pt; font-weight: 700;',
      '      text-transform: uppercase; border-bottom: 1px solid ' + theme.rule + '; margin: 0 0 .3em; }',
    ].join('\n');
  }

  /* Harvard's four headings, spelled the way the guide spells them. "Leadership & Activities"
     is the one most other templates have no equivalent for, and it is where a student's clubs,
     societies and volunteering go — in India the same block is usually called Positions of
     Responsibility, which is a rename away. */
  function starter(b) {
    b.add('header', {})
      .section('Education', s => s.add('degree_entry'))
      .section('Experience', s => s.add('work_entry'))
      .section('Leadership & Activities', s => s.add('work_entry'))
      .section('Skills & Interests', s => {
        s.add('skills_line', { label: 'Technical', value: '' });
        s.add('skills_line', { label: 'Language', value: '' });
        s.add('skills_line', { label: 'Interests', value: '' });
      });
  }

  /** Filled in, because the standard's rules are about the WORDS and placeholder text cannot
   *  demonstrate a rule about words. Every bullet here opens with a past-tense verb, carries a
   *  number, ends without a period, and uses no pronoun — so the four rules below all stay
   *  quiet on it, which is the calibration that keeps them honest. */
  function example(b) {
    b.add('header', {
      fullName: 'Miriam Okonkwo', email: 'm.okonkwo@gmail.com', phone: '+44 7700 900412',
      link: 'linkedin.com/in/mokonkwo | github.com/mokonkwo', locationLine: 'Manchester, UK',
    })
      .section('Education', s => s.add('degree_entry', {
        institution: 'University of Manchester', credential: 'BSc Computer Science, First Class',
        place: 'Manchester, UK', end: 'July 2021',
      }, e => e.bullet('Dissertation on incremental type checking, marked 78 and supervised by the compilers group')))
      .section('Experience', s => {
        s.add('work_entry', {
          company: 'Monzo Bank', role: 'Senior Software Engineer', place: 'London, UK (Remote)',
          start: 'September 2023', current: true,
        }, e => {
          e.bullet('Led the migration of 61 services off a shared Postgres instance, removing the single failure point behind 3 of the last 5 customer-facing incidents');
          e.bullet('Cut the median deploy from 22 minutes to 4 by parallelising the test suite and dropping two serialised migration gates');
          e.bullet('Mentored 3 engineers through their first on-call rotation, each of whom now runs incident command unaided');
        });
        s.add('work_entry', {
          company: 'Sky', role: 'Software Engineer', place: 'Leeds, UK',
          start: 'August 2021', end: 'August 2023',
        }, e => {
          e.bullet('Built the entitlement service behind 9 million streaming accounts, sustaining 12,000 requests a second at peak with a 40ms p99');
          e.bullet('Replaced a nightly reconciliation job with an event stream, which closed a 14-hour window in which cancelled accounts kept playing');
        });
      })
      .section('Leadership & Activities', s => s.add('work_entry', {
        company: 'Code Club Manchester', role: 'Volunteer Lead', place: 'Manchester, UK',
        start: 'January 2022', current: true,
      }, e => {
        e.bullet('Ran a weekly Python club for 30 secondary-school students and recruited 6 volunteer tutors from two local employers');
      }))
      .section('Skills & Interests', s => {
        s.add('skills_line', { label: 'Technical', value: 'Go, Python, TypeScript, PostgreSQL, Kafka, Kubernetes, Terraform, AWS' });
        s.add('skills_line', { label: 'Language', value: 'English (native), Igbo (native), French (B1)' });
        s.add('skills_line', { label: 'Interests', value: 'Long-distance running, choral singing, restoring bicycles' });
      });
  }

  /* ---- rules ---------------------------------------------------------------------------
     Four, each one stated by the standard this layout implements, and two of them deliberately
     contradict the Headless Headhunter layout's. Nothing about tense or strong verbs is repeated
     here, and a rule set is worth reading only where every line in it is this template's own
     opinion.

     "That layout already checks it" used to be the second half of that sentence, and it was the
     bug: rules do not carry across layouts, so what the Headless Headhunter layout checks was
     checked for its own users and nobody else's. What is genuinely common now lives in the
     shared baseline (ADR-0127) and reaches every layout, this one included. */

  const PRONOUNS = /\b(?:I|I'm|I’m|me|my|mine|we|our|ours|us)\b/i;

  /* The headings the standard names, plus the ones every ATS parser is trained on. Compared
     lower-cased and without an ampersand, so "Skills and Interests" and "Skills & Interests"
     are the same heading. */
  const STANDARD_HEADINGS = new Set(['education', 'experience', 'professional experience',
    'work experience', 'employment', 'leadership and activities', 'activities', 'leadership',
    'skills and interests', 'skills', 'technical skills', 'projects', 'publications',
    'certifications', 'awards', 'positions of responsibility', 'education and training',
    'volunteering', 'languages', 'summary', 'professional summary', 'interests',
    /* Europass's own spellings. This list is a US-shaped vocabulary and would otherwise flag the
       European Union's standard form as unparseable, which says more about the list than about
       the form — "Language skills" and "Digital skills" are printed on eight million CVs a year.
       A rule is data, and this is a layout borrowing another layout's rule for its own starter
       (tests/js/resume_layouts.test.js), so the two have to agree about the same headings. */
    'language skills', 'digital skills', 'about me']);
  const headingKey = title => String(title || '').toLowerCase().replace(/&/g, 'and')
    .replace(/[^a-z ]/g, '').replace(/\s+/g, ' ').trim();

  const rules = [
    {
      id: 'pronouns', label: 'No personal pronouns',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('bullet')) {
          const match = PRONOUNS.exec(String(api.content(n.id).text || ''));
          if (match) {
            out.push({ level: 'warn', nodeId: n.id,
              message: '“' + match[0] + '” — the standard writes a résumé without pronouns. Cut it and start with the verb.' });
          }
        }
        return out;
      },
    },
    {
      id: 'no-terminal-period', label: 'A bullet is not a sentence',
      check(doc, api) {
        /* Directly against the Headless Headhunter layout's `one-sentence`, which permits one
           period. Neither is wrong; they belong to different templates, and this is the one the
           user picked when they picked this layout. */
        const out = [];
        for (const n of api.nodesOfType('bullet')) {
          if (/\.\s*$/.test(String(api.content(n.id).text || ''))) {
            out.push({ level: 'note', nodeId: n.id,
              message: 'Ends with a full stop. The standard treats a bullet as a fragment and takes no final period — the Headless Headhunter template asks for the opposite.' });
          }
        }
        return out;
      },
    },
    {
      id: 'present-not-current', label: '“Present”, never “Current”',
      check(doc, api) {
        const out = [];
        for (const n of api.flatten()) {
          const c = api.content(n.id);
          for (const key of ['start', 'end']) {
            if (/^\s*(current|now|ongoing|till date|to date)\s*$/i.test(String(c[key] || ''))) {
              out.push({ level: 'warn', nodeId: n.id,
                message: '“' + String(c[key]).trim() + '” — the standard writes “Present”. Tick “Still here” instead and this layout prints it for you.' });
            }
          }
        }
        return out;
      },
    },
    {
      id: 'standard-headings', label: 'Headings a parser recognises',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('section')) {
          const title = String(api.content(n.id).title || '').trim();
          if (title && !STANDARD_HEADINGS.has(headingKey(title))) {
            out.push({ level: 'note', nodeId: n.id,
              message: '“' + title + '” is not one of the standard headings. A résumé is filed by a parser that has been trained on “Experience”, “Education”, “Skills” and a handful more; a heading it does not know is a section it may not file.' });
          }
        }
        return out;
      },
    },
  ];

  L.define({
    id: 'harvard-classic',
    label: 'Harvard classic',
    summary: 'Single column · Calibri 10.5pt · ruled headings · employer over title',
    credit: 'Standard: the Harvard College Resumes & Cover Letters guide, Mignone Center for Career Success.',
    blurb: 'The reverse-chronological standard a careers office hands out, and the format the ' +
      'overwhelming majority of recruiters say they prefer. One column, ruled headings, no ' +
      'colour — as safe through an applicant tracking system as a résumé gets. Education leads, ' +
      'because the template is written for someone leaving university; drag it below Experience ' +
      'once you have a few years. What it will never be is memorable: it is the format the other ' +
      'applicants used too, so if you are sending one résumé to one design-led team who will ' +
      'read it by hand, this is the safe answer to a question nobody asked.',
    page: { width: 8.5, height: 11, margin: 0.75, unit: 'in' },
    tokens: {
      bodyFont: 'Calibri, Carlito, "Segoe UI", Helvetica, sans-serif',
      ink: '#000000', muted: '#505050', rule: '#000000',
      nameSize: 16, headSize: 11.5, bodySize: 10.5, metaSize: 9.5,
      bodyLead: 1.24,      // 13pt leading on a 10.5pt body
      blockGap: 0.8,
      entryGap: 0.6,
      bulletIndent: 0.22,
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 10, max: 12, step: 0.5, unit: 'pt' },
      { key: 'headSize', label: 'Heading size', kind: 'range', min: 10, max: 14, step: 0.5, unit: 'pt' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 14, max: 22, step: 1, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 1.6, step: 0.02, unit: '' },
      { key: 'blockGap', label: 'Space between sections', kind: 'range', min: 0.2, max: 2, step: 0.1, unit: 'em' },
      { key: 'entryGap', label: 'Space between entries', kind: 'range', min: 0, max: 1.5, step: 0.05, unit: 'em' },
      { key: 'bulletIndent', label: 'Bullet indent', kind: 'range', min: 0.1, max: 0.6, step: 0.02, unit: 'in' },
      { key: 'bodyFont', label: 'Font', kind: 'select', unit: '',
        options: [
          ['Calibri, Carlito, "Segoe UI", Helvetica, sans-serif', 'Calibri (what the standard asks for)'],
          ['Arial, Helvetica, sans-serif', 'Arial'],
          ['Garamond, "EB Garamond", Georgia, serif', 'Garamond'],
          ['Georgia, "Times New Roman", serif', 'Georgia'],
        ] },
      { key: 'ink', label: 'Ink', kind: 'color', unit: '' },
      { key: 'rule', label: 'Heading rule', kind: 'color', unit: '' },
      /* The minimum the standard states is 10pt, and the tunable's floor is 10pt for that
         reason — nothing here dials the page below what the guide says is readable. */
    ],
    slots: [{ id: 'main', label: 'The column', grow: 1 }],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter'] },
    css, render, starter, example, rules,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
