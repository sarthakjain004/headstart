/* The Headless Headhunter layout — the first Layout, and the one the builder ships pointed at.
 *
 * Source: "How to Get a Job" (Resume guide 2.0) and its Resume Template / Example Resume, by the
 * Headless Headhunter. The template's claim is that a résumé's only job is to prove, in the
 * fifteen seconds a recruiter gives it, that you meet the minimum qualifications — so it reads
 * like a form rather than a brochure. Every number below is that document's, not a taste call:
 * Arial only, one column, black ink, name 14pt bold, contact 12pt, everything below 10.5pt at
 * 1.5 line spacing, section headings the only other bold thing on the page.
 *
 * The template's rules are enforced as *findings*, never as locks. The user may set a 12pt body
 * and the layout will render it; the rule panel then says the template asks for 10.5. Locking the
 * controls would have made the layout a straitjacket, and silently allowing the change would have
 * made "Headless Headhunter" a name on a menu rather than a claim about the output.
 */
(function (root) {
  'use strict';

  const L = root.ResumeLayouts;

  /* ---- the template's own numbers, kept in one place so the rules and the defaults cannot
     disagree about what "on template" means. ---- */
  const CANON = { bodySize: 10.5, nameSize: 14, contactSize: 12, bodyLead: 1.5, headLead: 1.15 };

  const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
    'september', 'october', 'november', 'december'];

  /** {y, m} from "June 2023", "Jun 2023", "06/2023" or "2023-06"; null when no month AND year
   *  can be read. The guide is explicit that work and internship dates carry both. */
  function parseMonth(text) {
    const s = String(text || '').trim().toLowerCase();
    if (!s) return null;
    let m = s.match(/^([a-z]+)\.?\s+(\d{4})$/);
    if (m) {
      const i = MONTHS.findIndex(name => name.startsWith(m[1].slice(0, 3)));
      return i < 0 ? null : { y: +m[2], m: i + 1 };
    }
    m = s.match(/^(\d{1,2})[/-](\d{4})$/);
    if (m && +m[1] >= 1 && +m[1] <= 12) return { y: +m[2], m: +m[1] };
    m = s.match(/^(\d{4})[/-](\d{1,2})$/);
    if (m && +m[2] >= 1 && +m[2] <= 12) return { y: +m[1], m: +m[2] };
    return null;
  }
  const asMonths = d => (d ? d.y * 12 + d.m : null);

  /** Sentence-ending periods, with the abbreviations and decimals that would inflate the count
   *  neutralised first — "B.A." and "4.5" are not sentences, and a rule that said they were
   *  would fire on the guide's own example résumé. */
  function periods(text) {
    const cleaned = String(text || '')
      .replace(/\b(?:[A-Za-z]{1,2}\.){2,}/g, 'X')
      .replace(/(\d)\.(\d)/g, '$1$2');
    return (cleaned.match(/\.(\s|$)/g) || []).length;
  }

  /* Roughly how many characters fit on one line, from the page and the type size actually in
     force. Arial's average advance is close to half its point size at this measure; this is an
     estimate and the finding says "about" for that reason. */
  function charsPerLine(page, size, indentIn) {
    const usable = page.width - 2 * page.margin - (indentIn || 0);
    return Math.max(20, Math.round((usable * 72) / (size * 0.5)));
  }

  /* ---- rules -------------------------------------------------------------------------- */

  const rules = [
    {
      id: 'contact', label: 'Name and contact details',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('header')) {
          const c = api.content(n.id);
          if (!String(c.fullName || '').trim()) out.push({ level: 'error', nodeId: n.id, message: 'No name on the résumé.' });
          if (!String(c.phone || '').trim() || !String(c.email || '').trim()) {
            out.push({ level: 'error', nodeId: n.id, message: 'The guide asks for a phone number and an email on the contact line.' });
          }
          if (!String(c.locationLine || '').trim()) {
            out.push({ level: 'warn', nodeId: n.id, message: 'Add work authorisation and city — "US Citizen in Los Angeles". Recruiters screen on both.' });
          }
        }
        return out;
      },
    },
    {
      id: 'bullet-count', label: 'Three to eight bullets a job',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('work_entry')) {
          const count = n.children.filter(c => c.type === 'bullet').length;
          if (count < 3) out.push({ level: 'warn', nodeId: n.id, message: 'Only ' + count + ' bullet' + (count === 1 ? '' : 's') + ' — the guide asks for at least 3, counting the opening summary.' });
          if (count > 8) out.push({ level: 'warn', nodeId: n.id, message: count + ' bullets — the guide caps a job at 8.' });
        }
        return out;
      },
    },
    {
      id: 'opening-summary', label: 'Each job opens with a summary sentence',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('work_entry')) {
          const bullets = n.children.filter(c => c.type === 'bullet');
          if (!bullets.length) continue;
          const first = bullets[0];
          if (!api.content(first.id).role) {
            out.push({ level: 'warn', nodeId: first.id, message: 'The first bullet of a job should be the plain-English summary of what you did there. Tick "Opening summary sentence".' });
          }
          for (const b of bullets.slice(1)) {
            if (api.content(b.id).role) {
              out.push({ level: 'warn', nodeId: b.id, message: 'Only the first bullet is the opening summary.' });
            }
          }
        }
        return out;
      },
    },
    {
      id: 'one-sentence', label: 'One sentence a bullet',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('bullet')) {
          const text = api.content(n.id).text || '';
          if (periods(text) > 1) {
            out.push({ level: 'warn', nodeId: n.id, message: 'More than one sentence. The guide allows one period a bullet — split it or join it.' });
          }
        }
        return out;
      },
    },
    {
      id: 'three-lines', label: 'No bullet over three lines',
      check(doc, api) {
        const out = [];
        const cap = charsPerLine(api.layout.page, +api.theme.bodySize || CANON.bodySize, 0.3) * 3;
        for (const n of api.nodesOfType('bullet')) {
          const text = String(api.content(n.id).text || '');
          if (text.length > cap) {
            out.push({ level: 'warn', nodeId: n.id, message: 'About ' + Math.ceil(text.length / (cap / 3)) + ' lines long — the guide caps a bullet at 3.' });
          }
        }
        return out;
      },
    },
    {
      id: 'dates', label: 'Month and year on every job',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('work_entry')) {
          const c = api.content(n.id);
          if (!parseMonth(c.start)) {
            out.push({ level: 'error', nodeId: n.id, message: 'Start date needs a month and a year — "June 2023".' });
          }
          if (!c.current && !parseMonth(c.end)) {
            out.push({ level: 'error', nodeId: n.id, message: 'End date needs a month and a year, or tick "Still here".' });
          }
        }
        return out;
      },
    },
    {
      id: 'reverse-chronological', label: 'Newest job first',
      check(doc, api) {
        const out = [];
        for (const section of api.nodesOfType('section')) {
          const jobs = section.children.filter(c => c.type === 'work_entry');
          let previous = null;
          for (const job of jobs) {
            const c = api.content(job.id);
            const started = c.current ? Infinity : asMonths(parseMonth(c.start));
            if (started == null) { previous = null; continue; }
            if (previous != null && started > previous) {
              out.push({ level: 'warn', nodeId: job.id, message: 'Out of order — the guide asks for reverse chronological, newest job first. Drag it up.' });
            }
            previous = started;
          }
        }
        return out;
      },
    },
    {
      id: 'education-length', label: 'Education kept to three lines',
      check(doc, api) {
        const out = [];
        for (const section of api.nodesOfType('section')) {
          const entries = section.children.filter(c => c.type === 'education_entry');
          if (entries.length > 3) {
            out.push({ level: 'warn', nodeId: section.id, message: entries.length + ' entries — the guide keeps Education & Certificates to 3 lines.' });
          }
        }
        return out;
      },
    },
    {
      id: 'project-bullets', label: 'Three bullets a project',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('project_entry')) {
          const count = n.children.filter(c => c.type === 'bullet').length;
          if (count > 3) out.push({ level: 'warn', nodeId: n.id, message: count + ' bullets — a project gets 3 at most.' });
        }
        return out;
      },
    },
    {
      id: 'twelve-years', label: 'No more than twelve years back',
      check(doc, api) {
        const out = [];
        const starts = api.nodesOfType('work_entry')
          .map(n => ({ n, at: asMonths(parseMonth(api.content(n.id).start)) }))
          .filter(x => x.at != null);
        if (!starts.length) return out;
        const now = new Date();
        const nowMonths = now.getFullYear() * 12 + now.getMonth() + 1;
        for (const { n, at } of starts) {
          if (nowMonths - at > 12 * 12) {
            out.push({ level: 'warn', nodeId: n.id, message: 'Over 12 years old — the guide stops there unless you are a senior director.' });
          }
        }
        return out;
      },
    },
    {
      id: 'on-template', label: 'Typography matches the template',
      check(doc, api) {
        const out = [];
        const t = api.theme;
        const off = [];
        if (+t.bodySize !== CANON.bodySize) off.push('body ' + t.bodySize + 'pt (template: 10.5)');
        if (+t.nameSize !== CANON.nameSize) off.push('name ' + t.nameSize + 'pt (14)');
        if (+t.contactSize !== CANON.contactSize) off.push('contact ' + t.contactSize + 'pt (12)');
        if (+t.bodyLead !== CANON.bodyLead) off.push('line spacing ' + t.bodyLead + ' (1.5)');
        if (off.length) {
          out.push({ level: 'note', nodeId: null, message: 'Off template: ' + off.join(', ') + '. It will still print — the guide just does not ask for it.' });
        }
        return out;
      },
    },
  ];

  /* ---- render strategies --------------------------------------------------------------- */

  const plain = L.plainStrategies();

  const render = {
    byShape: Object.assign({}, plain, {
      /* Anything the template has no opinion about still has to land somewhere sane. A `line`
         (the Skills line, added after this layout was written) renders as a left-aligned
         labelled line in the body type — off-template, but legible and never blank. */
      line: ctx => ctx.el('p', { class: 'hh-line' },
        (ctx.content.label ? '<b>' + ctx.esc(ctx.content.label) + ':</b> ' : '') +
        ctx.escLines(ctx.content.value)),
      text: ctx => ctx.el('p', { class: 'hh-note' }, ctx.escLines(ctx.content.text)),
      section: ctx => ctx.el('section', { class: 'hh-section' },
        '<h2 class="hh-h">' + ctx.esc(ctx.content.title) + '</h2>' + L.groupChildren(ctx, 'hh-list')),
      bullet: ctx => ctx.el('li', { class: 'hh-bullet' }, ctx.escLines(ctx.content.text)),
    }),

    byType: {
      header: ctx => {
        const c = ctx.content;
        /* The contact line is the one place the guide allows blue, and only for the three
           things that are links in substance: phone, email, profile. */
        const contact = [c.phone, c.email, c.link].filter(Boolean)
          .map(v => '<span class="hh-link">' + ctx.esc(v) + '</span>').join('<span class="hh-sep"> | </span>');
        return ctx.el('header', { class: 'hh-head' },
          '<h1 class="hh-name">' + ctx.esc(c.fullName) + '</h1>' +
          (contact ? '<p class="hh-contact">' + contact + '</p>' : '') +
          (c.locationLine ? '<p class="hh-contact">' + ctx.esc(c.locationLine) + '</p>' : '') +
          (c.languages ? '<p class="hh-contact">' + ctx.esc(c.languages) + '</p>' : ''));
      },

      work_entry: ctx => {
        const gutter = ctx.geo.gutter || 1.6;
        const bullets = ctx.children.length ? '<ul class="hh-list">' + ctx.children.join('') + '</ul>' : '';
        return ctx.el('div', { class: 'hh-entry' },
          '<div class="hh-entry-line">' +
            '<span class="hh-role">' + ctx.esc(L.roleLine(ctx.content)) + '</span>' +
            '<span class="hh-dates" style="min-width:' + gutter + 'in">' +
              ctx.esc(L.dateRange(ctx.content)) + '</span>' +
          '</div>' + bullets);
      },

      education_entry: ctx => {
        const gutter = ctx.geo.gutter || 1.6;
        return ctx.el('li', { class: 'hh-bullet hh-edu' },
          '<span class="hh-edu-text">' + ctx.esc(ctx.content.credential) + '</span>' +
          '<span class="hh-dates" style="min-width:' + gutter + 'in">' +
            ctx.esc(ctx.content.status) + '</span>');
      },

      project_entry: ctx => {
        const bullets = ctx.children.length ? '<ul class="hh-list">' + ctx.children.join('') + '</ul>' : '';
        return ctx.el('div', { class: 'hh-entry' },
          '<div class="hh-entry-line"><span class="hh-role">' + ctx.esc(ctx.content.name) + '</span></div>' +
          bullets);
      },
    },
  };

  /* ---- stylesheet ----------------------------------------------------------------------
     Emitted from the tokens rather than written as static CSS, so the preview, the print
     output and the downloaded file are the same document at three sizes of paper. */

  function css(theme, scope) {
    const s = scope || '.rb-doc';
    const body = +theme.bodySize || CANON.bodySize;
    return [
      s + ' { font-family: ' + theme.fontFamily + '; color: ' + theme.ink + '; font-size: ' + body + 'pt;',
      '      line-height: ' + (+theme.bodyLead || CANON.bodyLead) + '; text-align: left; }',
      s + ' .rb-slot { display: block; }',
      /* The header block runs at its own tighter leading, exactly as the template's annotated
         page does — 1.15 there, 1.5 everywhere below it. */
      s + ' .hh-head { text-align: center; line-height: ' + (+theme.headLead || CANON.headLead) + ';',
      '      margin: 0 0 ' + theme.headGap + 'em; }',
      s + ' .hh-name { font-size: ' + (+theme.nameSize || CANON.nameSize) + 'pt; font-weight: 700;',
      '      margin: 0; letter-spacing: 0; }',
      s + ' .hh-contact { font-size: ' + (+theme.contactSize || CANON.contactSize) + 'pt; font-weight: 400; margin: 0; }',
      s + ' .hh-link { color: ' + theme.linkInk + '; }',
      s + ' .hh-sep { color: ' + theme.ink + '; }',
      s + ' .hh-note { margin: 0 0 ' + theme.blockGap + 'em; }',
      s + ' .hh-section { margin: 0 0 ' + theme.blockGap + 'em; }',
      /* The section heading is bold and nothing else. No rule, no caps, no colour — the guide
         is explicit that the only bold text below the name is these headings. */
      s + ' .hh-h { font-size: ' + body + 'pt; font-weight: 700; margin: 0 0 .1em; }',
      s + ' .hh-entry { margin: 0 0 ' + theme.entryGap + 'em; page-break-inside: avoid; break-inside: avoid; }',
      s + ' .hh-entry-line { display: flex; align-items: baseline; gap: .5em; font-style: italic; }',
      s + ' .hh-role { flex: 1 1 auto; }',
      s + ' .hh-dates { flex: 0 0 auto; text-align: right; white-space: nowrap; }',
      s + ' .hh-list { margin: 0; padding-left: ' + theme.bulletIndent + 'in; list-style: disc; }',
      s + ' .hh-bullet { margin: 0; }',
      s + ' .hh-edu { display: flex; align-items: baseline; gap: .5em; }',
      s + ' .hh-edu-text { flex: 1 1 auto; }',
      s + ' .hh-line { margin: 0 0 ' + theme.entryGap + 'em; }',
      /* Anything the layout does not recognise still prints in the body face rather than the
         browser default, so an unknown component looks plain rather than broken. */
      s + ' .rb-header, ' + s + ' .rb-section, ' + s + ' .rb-entry, ' + s + ' .rb-text,',
      s + ' .rb-line { margin: 0 0 ' + theme.entryGap + 'em; }',
      s + ' .rb-section h2 { font-size: ' + body + 'pt; font-weight: 700; margin: 0 0 .1em; }',
    ].join('\n');
  }

  /* ---- the starting document ----------------------------------------------------------- */

  function starter(b) {
    b.add('header', {})
      .section('Education & Certificates', s => s.add('education_entry'))
      .section('Work History', s => s.add('work_entry'))
      .section('Projects', s => s.add('project_entry'));
  }

  /** The guide's own worked example, offered as a second starting point. Reading a filled
   *  résumé teaches the What / How / Result shape faster than any placeholder can. */
  function example(b) {
    b.add('header', {
      fullName: 'Lee Korelitz', phone: '123-456-1234', email: 'myemail@email.com',
      link: 'www.linkedin.com/in/leekorelitz', locationLine: 'US Citizen in Los Angeles',
    })
      .section('Education & Certificates', s => s.add('education_entry', {
        credential: 'B.A. in Economics from NYC College in NY', status: 'Status - Graduated',
      }))
      .section('Work History', s => {
        s.add('work_entry', {
          role: 'Cashier', company: 'Large Ducks Coffee', place: 'TX',
          start: 'June 2023', current: true,
        }, e => {
          e.bullet('Operated our Point of Sale (POS) cash register to collect money from customers and performed basic math to give them the proper change, keeping them engaged with customer service while I prepared their food and beverages', true);
          e.bullet('Handled a large lunch rush line by providing customer service and multitasking while making their food, which resulted in multiple five-star reviews on our Google Page mentioning me by name');
          e.bullet('Gave customers correct change by adding and subtracting cash');
          e.bullet('Used a desktop computer to read company emails, take online barista training courses, and input shift availability into our schedule');
        });
        s.add('work_entry', {
          role: 'Waiter', company: 'Shake and Bake Doughnuts', place: '',
          start: 'October 2021', end: 'June 2023',
        }, e => {
          e.bullet('Spoke with customers to get their orders and delivered them to our kitchen staff via our POS, ensured customers had a good time with customer service, and kept everything cleaned and up to code', true);
          e.bullet('Took customers’ orders and performed customer service (Active Listening) for multiple tables of customers');
          e.bullet('Upsold drink sizes and gave customers correct change when asked for the bill by performing addition and subtraction if they paid in cash rather than a credit card');
        });
      });
  }

  L.define({
    id: 'headless-headhunter',
    label: 'Headless Headhunter',
    summary: 'Single column · Arial · 10.5pt · 1.5 spacing',
    blurb: 'The template from "How to Get a Job". Built to be read in fifteen seconds by a ' +
      'recruiter checking whether you meet the minimum qualifications — which is the only ' +
      'thing a résumé is good at. Deliberately plain: one column, black ink, no design.',
    page: { width: 8.5, height: 11, margin: 1, unit: 'in' },
    tokens: {
      fontFamily: 'Arial, Helvetica, sans-serif',
      ink: '#000000',
      linkInk: '#1155CC',
      nameSize: CANON.nameSize,
      contactSize: CANON.contactSize,
      bodySize: CANON.bodySize,
      headLead: CANON.headLead,
      bodyLead: CANON.bodyLead,
      headGap: 1.0,      // the "single space here" under the contact block, in body ems
      blockGap: 0.9,     // between sections
      entryGap: 0.7,     // between jobs
      bulletIndent: 0.3, // inches
    },
    tunables: [
      { key: 'bodySize', label: 'Body size', kind: 'range', min: 9, max: 12, step: 0.5, unit: 'pt' },
      { key: 'bodyLead', label: 'Line spacing', kind: 'range', min: 1, max: 2, step: 0.05, unit: '' },
      { key: 'nameSize', label: 'Name size', kind: 'range', min: 12, max: 20, step: 1, unit: 'pt' },
      { key: 'contactSize', label: 'Contact size', kind: 'range', min: 9, max: 14, step: 0.5, unit: 'pt' },
      { key: 'blockGap', label: 'Space between sections', kind: 'range', min: 0, max: 2.5, step: 0.1, unit: 'em' },
      { key: 'entryGap', label: 'Space between entries', kind: 'range', min: 0, max: 2, step: 0.1, unit: 'em' },
      { key: 'bulletIndent', label: 'Bullet indent', kind: 'range', min: 0.1, max: 0.8, step: 0.05, unit: 'in' },
      { key: 'fontFamily', label: 'Font', kind: 'select', unit: '',
        options: [
          ['Arial, Helvetica, sans-serif', 'Arial (what the guide asks for)'],
          ['Helvetica, Arial, sans-serif', 'Helvetica'],
          ['Calibri, Carlito, sans-serif', 'Calibri'],
          ['Georgia, serif', 'Georgia'],
        ] },
      { key: 'ink', label: 'Ink', kind: 'color', unit: '' },
      { key: 'linkInk', label: 'Contact line colour', kind: 'color', unit: '' },
    ],
    /* One column, and free positioning off. This is the template's whole argument — a recruiter
       scanning for qualifications reads a column, not a canvas — so the layout does not grant
       the affordance rather than granting it and disapproving of it afterwards. What IS
       draggable here is order, which is the decision that actually matters: what a recruiter
       sees in the first half of the first page. */
    slots: [{ id: 'main', label: 'The column', grow: 1 }],
    caps: { mode: 'flow', reorder: true, resize: ['spaceAfter', 'gutter'] },
    bounds: { spaceAfter: [0, 40], gutter: [0.8, 2.6] },
    css, render, starter, example, rules,
  });

  /* Exported for the tests and the rule panel — the parser and the sentence count are the two
     places a wrong answer would be invisible in the UI. */
  root.ResumeHeadhunter = { CANON, parseMonth, periods, charsPerLine };
})(typeof globalThis !== 'undefined' ? globalThis : this);
