/* Layer 1 of the résumé builder (ADR-0123) — WHAT a résumé is made of.
 *
 * A Component Type declares its identity, the content fields it owns, what it may contain,
 * and which editor affordances it can *support*. It says nothing about type, size, colour or
 * position: a Component Type that mentioned a font would have taken a decision that belongs to
 * the Layout, and the same Content could then never be re-laid-out. That split is the whole
 * point of the three layers, so it is enforced by convention here and by review, not by a check
 * — there is no honest runtime test for "this string is a style".
 *
 * `shape` is the extensibility hinge. A Layout renders the types it knows by name and everything
 * else BY SHAPE, so a Component Type added after a Layout shipped still renders in it — badly
 * targeted, perhaps, but never blank. Without that, adding one component would mean editing
 * every layout, which is exactly the coupling the user asked to avoid.
 *
 * Browser script, no build step: it attaches one object to the global, like app.js.
 */
(function (root) {
  'use strict';

  /* The structural vocabulary. A Layout must be able to render every one of these, because a
     Component Type it has never heard of will arrive wearing one. Adding a shape is therefore a
     breaking change for every Layout; adding a TYPE is not, which is why there are few shapes
     and may be many types. */
  const SHAPES = Object.freeze([
    'header',   // the identity block at the top of the document
    'text',     // a free paragraph with no heading of its own
    'section',  // a titled container of entries
    'entry',    // one dated or titled record inside a section
    'line',     // a single labelled line (skills, languages)
    'bullet',   // one sentence inside an entry
  ]);

  /* The shape is only half the contract, and the other half is how a generic strategy finds the
     WORDS. The render fallbacks used to read some fields by name — `text` and `bullet` wanted
     `text`, `section` wanted `title` — and a type wearing one of those shapes that named its
     fields anything else rendered blank in every Layout written before it. That was not
     hypothetical: an `entry` did exactly that in the free-canvas layout until 2026-09-10, and the
     test meant to catch it had picked, by coincidence, the one shape whose keys the fallbacks
     happened to read.

     No render fallback names a field any more. Every one of them goes through `ctx.fields()` or
     `headAndRest()`, which read the type's DECLARED fields in order (`plainStrategies` in
     resume_layouts.js). Measured 2026-09-11 by registering three types with keys no renderer has
     ever seen — a `section` whose only field is `heading`, a `text` whose only field is `body`, a
     `bullet` whose only field is `sentence`: **9 of 9** Layouts print them, and so does the
     plain-text export, whose `content.text` / `content.title` reads each fall back to the
     declared fields.

     THAT LICENCE IS ABOUT THE FALLBACKS ONLY, and it does not make adding a field to an EXISTING
     type free. A `byType` strategy is written for a type it knows and names that type's fields,
     so a field added to one is printed by no Layout that overrides it — and how many that is
     depends entirely on the type. Counted 2026-09-11 across the nine registered Layouts, as the
     number of Layouts that WOULD print a new field: `work_entry`, `education_entry` and
     `project_entry` **0 of 9**; `header` **1**; `degree_entry` and `tech_project` **3**;
     `certification`, `award_entry`, `language_line` and `professional_summary` **7**; `section`,
     `bullet`, `skills_line`, `profile_line` and `summary` **9**. So widening `project_entry` is a
     pass over every Layout, widening `certification` is nearly free, and ADR-0130 §5's flat "0 of
     9" was the worst case read as the general one. Adding a NEW type stays what the shape
     contract above actually makes cheap (ADR-0128).

     The rule for a new Component Type: name your fields for what they ARE. If a fallback cannot
     find them, the fallback is the defect. */

  /* Field kinds the editor knows how to render an input for. Kept deliberately small — a kind
     is a promise the editor must keep, so each one costs a control. */
  const KINDS = Object.freeze(['text', 'multiline', 'month', 'flag']);

  const registry = new Map();

  function fail(msg) { throw new Error('ResumeComponents: ' + msg); }

  /** Register a Component Type. Throws rather than warns: a malformed or duplicate type would
   *  otherwise surface as an empty box on someone's résumé, hours later. */
  function define(spec) {
    if (!spec || typeof spec.type !== 'string' || !spec.type) fail('a type id is required');
    if (registry.has(spec.type)) fail('duplicate type: ' + spec.type);
    if (!spec.label) fail(spec.type + ': a human label is required');
    if (!SHAPES.includes(spec.shape)) fail(spec.type + ': unknown shape ' + spec.shape);

    const fields = (spec.fields || []).map(f => {
      if (!f.key) fail(spec.type + ': a field needs a key');
      if (!KINDS.includes(f.kind || 'text')) fail(spec.type + ': unknown field kind ' + f.kind);
      return Object.freeze({
        key: f.key, label: f.label || f.key, kind: f.kind || 'text',
        placeholder: f.placeholder || '', hint: f.hint || '',
        /* Whether this field's value is a web address. NOT a `kind`: a kind is a promise the
           editor must keep — it costs a control — and an address is typed into the same plain
           text box as everything else. It is a fact a RULE needs (`plain-links` in
           resume_layouts.js), and the name of the field cannot supply it: `certification`'s
           address field is called `credential`, and so are `degree_entry`'s and
           `education_entry`'s, which hold the name of a qualification. */
        holdsUrl: !!f.holdsUrl,
      });
    });

    const frozen = Object.freeze({
      type: spec.type,
      label: spec.label,
      shape: spec.shape,
      /* Whether this component is one item in a list of siblings. Structural, not stylistic —
         a bullet *is* a list item whatever a Layout dresses it as — and it is what lets a
         generic Section renderer wrap consecutive items in one <ul> without knowing their
         types. A Layout may still ignore it. */
      inList: !!spec.inList,
      /* What the type is for, in the builder's own words. Shown in the "Add" menu — a component
         nobody can identify is a component nobody adds. */
      blurb: spec.blurb || '',
      fields: Object.freeze(fields),
      /* Child types this node may contain, by type id. '*' means any registered type, which is
         what a generic Section needs so a type added later can still be dropped into one. */
      accepts: Object.freeze((spec.accepts || []).slice()),
      /* Affordances this type can SUPPORT. The Layout decides which are actually offered — see
         resume_layouts.js. A type that cannot be reordered (the header) says so here once,
         rather than every layout having to remember it. */
      caps: Object.freeze(Object.assign(
        { reorder: true, remove: true, duplicate: true, resize: [] },
        spec.caps || {},
      )),
      /* Content this type starts life with. A function, not an object: two nodes of the same
         type must not share one mutable default. */
      blank: typeof spec.blank === 'function' ? spec.blank : () => ({}),
      /* Children a freshly added node is seeded with. Each entry is a type id, or a
         [type, content] pair where the seeded child needs to start with something set — a Work
         entry's first bullet IS the job summary in this method, and seeding it unflagged made a
         brand-new job trip the opening-summary check before anything had been typed. */
      seed: Object.freeze((spec.seed || []).map(s => Object.freeze(
        typeof s === 'string' ? [s, {}] : [s[0], Object.assign({}, s[1])]))),
    });
    registry.set(spec.type, frozen);
    return frozen;
  }

  const get = type => registry.get(type) || null;
  const all = () => Array.from(registry.values());
  const has = type => registry.has(type);

  /** Whether `childType` may sit inside `parentType`, honouring '*'. */
  function accepts(parentType, childType) {
    const spec = get(parentType);
    if (!spec || !has(childType)) return false;
    return spec.accepts.includes('*') || spec.accepts.includes(childType);
  }

  /* What may sit at the top level of a document. The root has no Component Type of its own, so
     it cannot answer through `accepts` — and every path that needed the answer therefore made
     one up. They disagreed: the form's add menu offered `text` and `line` beside a section,
     while the drag path offered every slot to every dragged node and the model exempted the root
     from the check entirely. A bullet could be dragged above the header, and the plain-text
     export — the copy people paste into application forms — then opened with "- Operated our
     Point of Sale…" and printed the candidate's name on the line below.

     So the rule is written once, here, in shapes rather than types: a résumé is an identity
     block, then titled sections with free paragraphs and labelled lines between them. Entries
     live in sections and bullets in entries, and neither is a thing a page is made of. A
     component added later inherits the answer from the shape it wears. */
  const ROOT_SHAPES = Object.freeze(['header', 'text', 'section', 'line']);

  /** Whether `childType` may sit at the document root — the one rule, read by the form's add
   *  menu, by the drag path's drop points, and by the model's own move guard. */
  function acceptsAtRoot(childType) {
    const spec = get(childType);
    return !!spec && ROOT_SHAPES.includes(spec.shape);
  }

  /** A blank content record for a type — every declared field present, so the editor never has
   *  to distinguish "field absent" from "field empty". */
  function blankContent(type) {
    const spec = get(type);
    if (!spec) return {};
    const out = {};
    for (const f of spec.fields) out[f.key] = f.kind === 'flag' ? false : '';
    return Object.assign(out, spec.blank());
  }

  /* ---- The catalogue ----------------------------------------------------------------
     These are the components of a résumé, not of the Headless Headhunter template. The
     template's opinions (Arial, 10.5pt, which sections in which order, how many bullets) all
     live in resume_layout_headhunter.js. What is here is only the claim that a résumé has an
     identity block, sections, dated entries and bullets — which is true of every résumé. */

  define({
    type: 'header', shape: 'header', label: 'Name & contact',
    blurb: 'Your name, how to reach you, and where you are.',
    caps: { reorder: false, remove: false, duplicate: false },
    fields: [
      { key: 'fullName', label: 'Full name', placeholder: 'Lee Korelitz' },
      { key: 'phone', label: 'Phone', placeholder: '123-456-1234' },
      { key: 'email', label: 'Email', placeholder: 'myemail@email.com' },
      { key: 'link', label: 'LinkedIn or portfolio', placeholder: 'linkedin.com/in/leekorelitz',
        holdsUrl: true,
        hint: 'Plain text, no https:// and no www. This field had no hint at all while the ' +
          'advice for the same thing sat on the Profile component below, so the one address ' +
          'every résumé carries was the one nobody was told about.' },
      { key: 'locationLine', label: 'Status and location', placeholder: 'Citizen or work permit, then your city',
        hint: 'Whatever tells a recruiter they can hire you, then where you are — they screen on both.' },
      { key: 'languages', label: 'Languages (optional)', placeholder: 'English, Spanish' },
    ],
  });

  define({
    type: 'summary', shape: 'text', label: 'Situation note',
    blurb: 'Two lines explaining a career change, a relocation, or a visa. Leave it out otherwise.',
    caps: { duplicate: false },
    fields: [
      { key: 'text', label: 'Note', kind: 'multiline',
        placeholder: 'Moving to Austin on 1 March, can move sooner if needed.',
        hint: 'Only for an industry change, a move, or a visa a recruiter may not know they can hire on.' },
    ],
  });

  /* A second `text` component beside `summary`, and the near-synonym is deliberate rather than
     an oversight: they are the two things people mean by "the paragraph at the top", they are
     written for different reasons, and a résumé wants at most one of them. `summary` is labelled
     "Situation note" and its placeholder is a relocation; this one is the pitch. Rendering one
     under the other's heading would have been the dishonest way to save a type. */
  define({
    type: 'professional_summary', shape: 'text', label: 'Professional summary',
    blurb: 'Two to four lines: what you do, at what level, and what you are after. Sidebar and ' +
      'Europass layouts open with one. Skip it if you are early-career — it costs a fifth of a ' +
      'page and a bullet says more.',
    caps: { duplicate: false },
    fields: [
      /* `text`, because it is the plain name for what this field holds — and because the
         plain-text export still prefers that key before falling back to the declared fields. */
      { key: 'text', label: 'Summary', kind: 'multiline',
        placeholder: 'Backend engineer, six years on payments systems in India and the EU. ' +
          'Looking for platform work on a team that ships to production daily.',
        hint: 'No pronouns, no adjectives about yourself. Say the role, the years, the domain.' },
    ],
  });

  define({
    type: 'section', shape: 'section', label: 'Section',
    blurb: 'A titled block — Work History, Education, Projects, or one you name yourself.',
    fields: [{ key: 'title', label: 'Section title', placeholder: 'Work History' }],
    accepts: ['*'],
    caps: { resize: ['spaceAfter'] },
  });

  define({
    type: 'work_entry', shape: 'entry', label: 'Work entry',
    blurb: 'One job: title, employer, and the dates you were there.',
    accepts: ['bullet'],
    caps: { resize: ['spaceAfter', 'gutter'] },
    fields: [
      { key: 'role', label: 'Job title', placeholder: 'Cashier' },
      { key: 'company', label: 'Employer', placeholder: 'Large Ducks Coffee' },
      { key: 'place', label: 'Location', placeholder: 'TX' },
      { key: 'start', label: 'Start', kind: 'month', placeholder: 'June 2023' },
      { key: 'end', label: 'End', kind: 'month', placeholder: 'March 2025' },
      { key: 'current', label: 'Still here', kind: 'flag' },
    ],
    seed: [['bullet', { role: true }], 'bullet', 'bullet'],
  });

  define({
    type: 'education_entry', shape: 'entry', label: 'Education or certificate', inList: true,
    blurb: 'One degree or certificate, and whether it is finished.',
    caps: { resize: ['gutter'] },
    fields: [
      { key: 'credential', label: 'Qualification', placeholder: 'B.A. in Economics from NYC College in NY' },
      { key: 'status', label: 'Status or year', placeholder: 'Status - Graduated',
        hint: 'The year if you finished within three years; otherwise "Status - Graduated".' },
    ],
  });

  define({
    type: 'degree_entry', shape: 'entry', label: 'Degree', accepts: ['bullet'],
    blurb: 'One degree with its institution, where it was, and when — four fields, so a layout ' +
      'can put the school and the dates at opposite margins. The one-line entry above is the ' +
      'compressed form; this is what Jake’s Resume, the Harvard template and Europass all ask for.',
    caps: { resize: ['spaceAfter'] },
    fields: [
      { key: 'institution', label: 'Institution', placeholder: 'Indian Institute of Technology Bombay' },
      { key: 'credential', label: 'Degree', placeholder: 'B.Tech in Computer Science and Engineering' },
      { key: 'place', label: 'Location', placeholder: 'Mumbai, India' },
      { key: 'start', label: 'From', kind: 'month', placeholder: 'August 2018' },
      { key: 'end', label: 'To', kind: 'month', placeholder: 'May 2022',
        hint: 'Leave "From" empty to show the graduation date alone, which is what most ' +
          'standards ask for. A degree in progress says "Expected May 2027".' },
    ],
    /* No seeded bullet. Jake's education block has none at all, and a bullet that appears by
       itself under a fresh degree is a prompt to write filler. */
  });

  define({
    type: 'project_entry', shape: 'entry', label: 'Project',
    blurb: 'Unpaid work that shows the same skills. No dates.',
    accepts: ['bullet'],
    caps: { resize: ['spaceAfter'] },
    fields: [{ key: 'name', label: 'Project', placeholder: 'Inventory tracker for a food bank' }],
    seed: ['bullet'],
  });

  define({
    type: 'tech_project', shape: 'entry', label: 'Project with a stack', accepts: ['bullet'],
    blurb: 'A project, what it was built with, and when — Jake’s Resume’s signature block. The ' +
      'plain Project above carries neither, because the template it came from asks for neither.',
    caps: { resize: ['spaceAfter'] },
    fields: [
      { key: 'name', label: 'Project', placeholder: 'Gitlytics' },
      { key: 'tech', label: 'Built with', placeholder: 'Python, Flask, React, PostgreSQL, Docker',
        hint: 'The stack, comma-separated. This is the line a recruiter greps for.' },
      { key: 'start', label: 'From', kind: 'month', placeholder: 'June 2020' },
      { key: 'end', label: 'To', kind: 'month', placeholder: 'Present' },
    ],
    seed: ['bullet', 'bullet'],
  });

  /* The two blocks a tech résumé carries that nothing above could hold, each required by a
     Layout that ships and each measured against the templates people actually use (ADR-0130).
     What was REFUSED is recorded in that ADR rather than here, but one refusal belongs beside
     the code it would have sat next to: `education_entry` is labelled "Education or
     certificate", and ADR-0126 declined a Certification type on exactly that ground. That was a
     judgement about the LABEL and not about the fields — `education_entry` owns `credential`
     and `status`, so an AWS certification has nowhere to put the body that issued it, the month
     it was earned, or the id an employer verifies it by, and stuffing four facts into one
     `credential` string is how a field stops being a field. */

  define({
    type: 'certification', shape: 'entry', label: 'Certification',
    blurb: 'One certification: what it is, who issued it, when, and the id it is verified by. ' +
      'Real in infrastructure, cloud and security hiring — AWS, GCP, Azure, CKA, CISSP — and ' +
      'worth little anywhere else. Drop one the moment it expires.',
    caps: { resize: ['spaceAfter'] },
    /* No `accepts`, and no seeded bullet: every template that ships this block renders it as a
       line of facts, and a certification with bullets under it is a job entry wearing the wrong
       name. */
    fields: [
      { key: 'name', label: 'Certification', placeholder: 'AWS Certified Solutions Architect – Associate' },
      { key: 'issuer', label: 'Issued by', placeholder: 'Amazon Web Services' },
      { key: 'earned', label: 'Earned', kind: 'month', placeholder: 'March 2024' },
      { key: 'credential', label: 'Credential id or link', placeholder: 'credly.com/badges/9f2c11a0',
        holdsUrl: true,
        hint: 'What an employer checks it against — an id, or a link in plain text with no https://. Leave it empty rather than writing “available on request”.' },
    ],
  });

  define({
    type: 'award_entry', shape: 'entry', label: 'Award or honour',
    blurb: 'One thing you won or were given, who gave it, and when — a hackathon, a contest ' +
      'placing, a scholarship, an internal engineering award. Say the field you beat: “1st of ' +
      '340 teams” is the award, “Winner” is a word.',
    caps: { resize: ['spaceAfter'] },
    /* Four fields, and they are the union of the two templates that carry this block as a
       first-class one rather than a sentence: Deedy's `Awards` is a year/rank/award table and
       Awesome-CV's `\cvhonor` takes award, event, location, date. */
    fields: [
      { key: 'title', label: 'Award', placeholder: 'Winner, Smart India Hackathon' },
      { key: 'awarder', label: 'Given by', placeholder: 'Ministry of Education, Government of India' },
      { key: 'place', label: 'Location', placeholder: 'New Delhi, India' },
      { key: 'when', label: 'When', kind: 'month', placeholder: 'August 2021' },
    ],
  });

  define({
    type: 'bullet', shape: 'bullet', label: 'Bullet', inList: true,
    blurb: 'One sentence: what you did, how you did it, and what came of it.',
    fields: [
      { key: 'text', label: 'Sentence', kind: 'multiline',
        placeholder: 'Handled a large lunch rush line by providing customer service and multitasking while making their food, which resulted in multiple five-star reviews.' },
      /* Not styling: the first bullet of a Work entry plays a different *role* — it summarises
         the job — and the rule validator counts it separately. How (or whether) a Layout marks
         that role is the Layout's business. */
      { key: 'role', label: 'Opening summary sentence', kind: 'flag' },
    ],
  });

  define({
    type: 'skills_line', shape: 'line', label: 'Skills line',
    blurb: 'One line of tools or skills. The Headless Headhunter template does not ask for one — ' +
      'its method puts skills inside the job bullets, where the recruiter is already reading.',
    fields: [
      { key: 'label', label: 'Label', placeholder: 'Skills' },
      { key: 'value', label: 'Value', placeholder: 'Python, Go, Postgres, Kubernetes' },
    ],
  });

  define({
    type: 'profile_line', shape: 'line', label: 'Profile or link',
    blurb: 'One place someone can read more — GitHub, a portfolio, a personal site. The header ' +
      'holds a single link; this is for the second and third, and it is also how the standards ' +
      'say to handle everything a résumé does not have room for: put it where it lives and link ' +
      'to it, rather than growing a section for it.',
    /* This is the DEFERRAL mechanism, and that is why it is a component rather than a second
       field on the header. University career services draw a hard line between a résumé and a
       CV — publications, presentations, patents, grants and affiliations belong on the CV — and
       the advice for someone who has those is not "delete them" but "keep them where they live
       and link to them". A résumé with one link field cannot do that. A field added to `header`
       could not either: measured 2026-09-10, eight of the nine registered Layouts render the
       header with a `byType` strategy that names its fields, so a tenth field would have been
       typed by the user and printed by exactly one layout (ADR-0130). */
    fields: [
      { key: 'network', label: 'Where', placeholder: 'GitHub' },
      { key: 'url', label: 'Address', placeholder: 'github.com/ananyarao', holdsUrl: true,
        hint: 'Plain text, no https:// and no www. Every standard here asks for links that do ' +
          'not stand out — an underlined blue URL is the one thing on the page a reader’s eye ' +
          'goes to instead of your work.' },
    ],
  });

  define({
    type: 'language_line', shape: 'line', label: 'Language',
    blurb: 'One language and how well you use it. Europass asks for a CEFR level and grades the ' +
      'CV on it; most other markets accept "Native" or "Fluent" and some do not ask at all.',
    /* Named for what they are. The obvious shortcut was to call them `label` and `value`, which
       are the keys the `line` fallbacks used to hardcode — that would have rendered everywhere
       for free and left the next component with an honest name to be the one that breaks. */
    fields: [
      { key: 'language', label: 'Language', placeholder: 'German' },
      { key: 'level', label: 'Level', placeholder: 'B2 · Independent user',
        hint: 'CEFR runs A1, A2, B1, B2, C1, C2 — Europass wants one of those six.' },
    ],
  });

  root.ResumeComponents = {
    SHAPES, KINDS, ROOT_SHAPES, define, get, all, has, accepts, acceptsAtRoot, blankContent,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
