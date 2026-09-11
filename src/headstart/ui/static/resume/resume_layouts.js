/* Layer 2 of the résumé builder (ADR-0123) — HOW the components are arranged, and how they look.
 *
 * A Layout owns four things Layer 1 and Layer 3 are forbidden to know about: the page geometry,
 * the type tokens, one render Strategy per component shape (and optionally per type), and the
 * capability contract that says which editor affordances are live in it. That last one is the
 * unobvious part: "draggable and resizable" is not a property of a component, it is a permission
 * the Layout grants. A strict single-column template earns its results by refusing free
 * positioning; a canvas layout grants it. The same component honours both without changing.
 *
 * A Layout emits its own CSS as a string so that the on-screen preview, the print-to-PDF output
 * and the downloaded HTML are rendered from ONE stylesheet rather than three that drift.
 *
 * Adding a Layout is: write a file, call `define`, add one <script> tag. It never requires
 * touching a component, and a component added after it still renders through `byShape`.
 */
(function (root) {
  'use strict';

  const Components = root.ResumeComponents;

  const registry = new Map();
  const order = [];

  /* Its own copy of app.js's escape rather than a shared one, deliberately: these ten files are
     the résumé builder and load independently of the search page's script, and a résumé must not
     stop being escaped because the tab it sits beside was refactored. Eight lines is a cheaper
     coupling than the alternative. */
  const esc = s => (s == null ? '' : String(s)).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** Escaped text with newlines preserved as <br> — bullets are multiline inputs. */
  const escLines = s => esc(s).replace(/\r?\n/g, '<br>');

  const attrs = obj => Object.keys(obj)
    .filter(k => obj[k] != null && obj[k] !== false && obj[k] !== '')
    .map(k => ' ' + k + '="' + esc(obj[k]) + '"').join('');

  function fail(msg) { throw new Error('ResumeLayouts: ' + msg); }

  const clampNum = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /* ---- reading a résumé's dates and prose --------------------------------------------------
     The vocabulary the rules below are written in. It lived in resume_layout_headhunter.js
     while that layout was the only one checking anything; it is here now because the baseline
     and that layout both read it, and two copies of "what counts as a month" would drift. */

  /* Month names in the languages these résumés are actually written in. Nothing scopes a résumé's
     own language — the English-only line in CLAUDE.md §Project Scope is about the *search corpus*,
     not about what a user may type into this builder — and an English-only reader told a Spanish
     user that "enero 2023", a correct start date, had no month on it at all. The seven
     Latin-script languages below are what one table can carry honestly; a language outside it is
     handled by `dateFinding` rather than by a wrong answer. Diacritics are folded before the
     lookup, so "février" and "março" are found under `fevrier` and `marco`. */
  const MONTHS = new Map();
  [
    'january february march april may june july august september october november december',
    'enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre',
    'janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre',
    'januar februar marz april mai juni juli august september oktober november dezember',
    'gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre',
    'janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro',
    'januari februari maart april mei juni juli augustus september oktober november december',
  ].forEach(row => row.split(' ').forEach((name, i) => MONTHS.set(name, i + 1)));

  /** Lowercased, trimmed and stripped of its accents — the form the table is keyed in. */
  const fold = text => String(text || '').trim().toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '');

  /** The month a word names, 1-12, or 0 when no single month fits it.
   *
   *  A PREFIX is enough — "sept", "jun", "fev" — but only while it fits exactly one month and is
   *  at least as long as the three-letter abbreviation every one of these languages uses. The old
   *  reader took the first three characters and asked which month STARTED with them, so a shorter
   *  token always got an answer: "j" was January, "ju" was June and never July, "ma" was March and
   *  never May. A guess there is worse than a refusal, because the `dates` rule then validates a
   *  date the user never wrote.
   *
   *  Ambiguity is counted in MONTHS, never in spellings: "mai" is May in French, German and (as
   *  the prefix of "maio") Portuguese, so it is an answer; "ma" is March and May, so it is not. */
  function monthNamed(word) {
    const w = fold(word);
    if (w.length < 3) return 0;
    let found = 0;
    for (const [name, n] of MONTHS) {
      if (!name.startsWith(w)) continue;
      if (found && found !== n) return 0;
      found = n;
    }
    return found;
  }

  /** {y, m} from "June 2023", "Jun 2023", "06/2023" or "2023-06"; null when no month AND year
   *  can be read. Every standard here asks a job's dates for both. */
  function parseMonth(text) {
    const s = fold(text);
    if (!s) return null;
    let m = s.match(/^([a-z]+)\.?\s+(\d{4})$/);
    if (m) {
      const month = monthNamed(m[1]);
      return month ? { y: +m[2], m: month } : null;
    }
    m = s.match(/^(\d{1,2})[/-](\d{4})$/);
    if (m && +m[1] >= 1 && +m[1] <= 12) return { y: +m[2], m: +m[1] };
    m = s.match(/^(\d{4})[/-](\d{1,2})$/);
    if (m && +m[2] >= 1 && +m[2] <= 12) return { y: +m[1], m: +m[2] };
    return null;
  }
  const asMonths = d => (d ? d.y * 12 + d.m : null);

  const MONTH_SHAPED = /^\s*(\p{L}{3,})\.?\s+\d{4}\s*$/u;

  /* The English words a date cell really does carry that are not months. Without this list the
     note below swallowed them: measured on this change's first draft, "Summer 2023", "Fall 2023",
     "Ongoing 2023" and "Various 2023" all dropped from error to note — and the editor leaves a
     note out of the headline problem count, so a genuinely vague date would have gone nearly
     invisible. These are words the builder KNOWS are not months, so the error is one it can
     justify. A misspelling ("Junuary 2023") still reads as unreadable; telling a typo from a
     Hungarian month needs a dictionary this does not have. */
  const NOT_A_MONTH = new Set(('spring summer fall autumn winter sometime someday various ' +
    'ongoing present current now recent unknown tbd').split(' '));

  /** A cell shaped like a month and a year whose word `parseMonth` could not read, and which is
   *  not one of the English non-months above — all but always a language the table does not
   *  carry. It is NOT a missing month, and a rule that calls it one is making a claim about a
   *  document it cannot read. */
  function unreadableMonth(text) {
    const m = MONTH_SHAPED.exec(String(text || ''));
    return !!m && !parseMonth(text) && !NOT_A_MONTH.has(fold(m[1]));
  }

  /** The finding a date cell `parseMonth` could not read deserves. Shared rather than written
   *  twice because the THIRD verdict is a fact about what this build can read, not a template's
   *  opinion — the layout supplies only its own wording for the one verdict it does own. */
  const dateFinding = (nodeId, value, missing) => (unreadableMonth(value)
    ? { level: 'note', nodeId,
      message: 'That month is not one this builder can read, so the date is unchecked. If it names a month in your language it is fine — though a recruiter’s parser may not read it either.' }
    : { level: 'error', nodeId, message: missing });

  /** An end date written as a word rather than a month — a job still held. Which word is right
   *  is a Layout's argument ("Present" against "Current", see harvard's `present-not-current`);
   *  that the cell is filled in at all is not, so the baseline accepts every spelling and lets
   *  the layout that cares object to the wording on its own terms. */
  /* Anchored, and every arm spelled in full: factoring "to date"/"till date" down to a
     shared `date` arm left a bare one, and an end cell reading literally "date" then
     counted as a job still held — which silenced this rule's own error and sent
     `reverse-chronological` an Infinity start. Measured: that document reported nothing. */
  const STILL_HERE = /^\s*(?:present|current|now|ongoing|to date|till date)\s*$/i;

  /* Roughly how many characters fit on one line, from a MEASURE in inches and the type size
     actually in force. An average advance close to half the point size; this is an estimate and
     the findings that use it say "about" for that reason. */
  function charsIn(measureIn, size, indentIn) {
    const usable = measureIn - (indentIn || 0);
    return Math.max(20, Math.round((usable * 72) / (size * 0.5)));
  }

  /** The same, measured across the whole page. Kept because a Layout's own rules read it, and
   *  because it is the right answer for a single-column template. */
  function charsPerLine(page, size, indentIn) {
    return charsIn(page.width - 2 * page.margin, size, indentIn);
  }

  /** How wide the text of one top-level block actually is, in inches.
   *
   *  The page is not the measure once a Layout has columns, and `three-lines` was handed the page
   *  regardless — so on every layout that puts body text in a column it believed the line held
   *  24-39% more characters than it does, and stayed silent on bullets that really print four
   *  lines. Measured 2026-09-10 on one 281-character bullet: `two-column`, `europass` and
   *  `modern-sidebar` each print about 75 characters to a line where the page-wide sum says
   *  95, 93 and 104. The constant above is NOT the fault and must not be tuned to cover this —
   *  it lands within 1% on `headless-headhunter` (86 true against 85) and would have to be
   *  wrong by 39% somewhere else to fix `modern-sidebar` by that route.
   *
   *  The default is the slot's share of the measure by `grow`, less the gaps between columns.
   *  A Layout whose geometry is not a plain column split says so with its own `measure(slotId,
   *  page, theme)` — Europass is the reason that exists: it has ONE slot and still gives a quarter
   *  of every row to a label gutter, so no share of `grow` could describe it. */
  function measureOf(layout, page, theme, slotId, topNode) {
    const full = page.width - 2 * page.margin;
    /* A free-positioning layout has no columns to share out: a block is as wide as the user
       dragged it, and that width is on the block itself. Measured 2026-09-10 on `free-canvas`,
       whose adopted blocks come out 4.25in wide — the page-wide answer said a line held 95
       characters where it held 56, so a bullet printing five lines was reported as fine. */
    if (layout.caps && layout.caps.mode === 'free') {
      /* No `doc` here, and none needed: the sheet only matters to this clamp as an upper bound,
         and `Math.min(geo.w, full)` below is already that bound for the page in force. */
      const geo = geometryFor(layout, topNode || {});
      return geo.w != null ? Math.min(geo.w, full) : full;
    }
    if (typeof layout.measure === 'function') {
      const own = +layout.measure(slotId, page, theme);
      if (isFinite(own) && own > 0) return Math.min(own, full);
    }
    const slots = layout.slots || [];
    if (slots.length < 2) return full;
    const total = slots.reduce((sum, s) => sum + (+s.grow || 1), 0);
    const slot = slots.find(s => s.id === slotId) || slots[0];
    /* The gap between the columns is deliberately NOT subtracted here. Every layout with columns
       has a `columnGap` token and they do not agree on what it means — `two-column` and
       `deedy-resume` state inches, `modern-sidebar` states em — so reading it as one number
       would be wrong for whichever spelling lost. It is worth 3-5% of the measure against an
       estimate whose own findings say "about"; a Layout that wants it counted states `measure`. */
    return Math.max(1, full * ((+slot.grow || 1) / total));
  }

  /** The first word of a bullet, lowercased, without its punctuation. */
  function opener(text) {
    const m = String(text || '').trim().match(/^[A-Za-z][A-Za-z'’-]*/);
    return m ? m[0].toLowerCase() : '';
  }

  /** Every spelling `word` could be the -ed or -ing form of, `word` itself first. English adds
   *  -ed / -ing four ways and this undoes all four; the lists below are therefore written once,
   *  in the base form, and still match whichever tense somebody typed. */
  function stems(word) {
    const out = [word];
    const cut = word.replace(/(?:ed|ing)$/, '');
    if (cut !== word) {
      out.push(cut, cut + 'e');
      if (/(.)\1$/.test(cut)) out.push(cut.slice(0, -1));        // running  -> run
      if (/i$/.test(cut)) out.push(cut.slice(0, -1) + 'y');      // amplified -> amplify
    }
    return out;
  }
  const anyStemIn = (set, word) => !!word && stems(word).some(w => set.has(w));

  /* Openers that fill the line without saying what was done. Base forms — see `stems`. */
  const WEAK_OPENERS = new Set(['responsible', 'help', 'work', 'assist', 'participate',
    'involve', 'was', 'task', 'duties', 'handle']);

  /* Verbs the standard calls dressed-up rather than wrong, in base form. Checked on the OPENING
     word only, and that scope is load-bearing rather than lazy: "ensured customers had a good
     time with customer service" is a real sentence from a worked example this repo calibrates
     against, and a whole-sentence scan would flag it. */
  const SUPERFLUOUS = new Set(['amplify', 'conceptualize', 'conceptualise', 'craft', 'elevate',
    'employ', 'engage', 'engineer', 'enhance', 'ensure', 'foster', 'head', 'hone', 'innovate',
    'leverage', 'master', 'orchestrate', 'perfect', 'pioneer', 'revolutionize', 'revolutionise',
    'spearhead', 'transform', 'utilize', 'utilise']);

  /* A number, or a sense of scale. Both count — "multiple financial products" is quantification
     in the way that matters, and a bare-metric test would fail bullets that are true. */
  const SCALE = new RegExp('\\d|\\b(?:multiple|several|dozens?|hundreds|thousands|millions|' +
    'numerous|daily|weekly|monthly|every|' +
    /* Spelled out. The standard asks for digits and this repo's own `modern-sidebar` example
       writes "brought four engineers through onboarding in six months" anyway — which the
       digits-only test called a bullet with no number in it at all. Preferring digits is
       advice; pretending a spelled number is not a number is a wrong finding. */
    'one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\\b', 'i');

  /* A clause saying what came of it, or why it was done. */
  const OUTCOME = new RegExp('\\bwhich\\b|\\bresult|\\bso that\\b|\\bleading to\\b|' +
    '\\bin order to\\b|\\bby \\w+ing\\b|' +
    '\\bto (?!the|a|an|our|their|his|her|my|your|its|this|that|these|those|us|them|me|him|it)[a-z]+\\b', 'i');

  /* ---- the baseline every Layout checks (ADR-0127, ADR-0135) --------------------------------
     ADR-0123 put every Rule on the Layout, on the argument that a Rule is its method's opinion.
     That is true of most of them and false of these nine: a job with no dates on it, a name
     nobody can answer, twelve bullets opening "Responsible for" — no template on the picker
     holds a different view, and four of the seven Layouts held NO view, so the panel read
     "Nothing to flag. Every rule this layout states is met." over a résumé nobody should send.

     What is here is hygiene; what is not here is opinion, and the line was drawn against the
     standards packaged in this machine's `resume-builder` skill (Harvard Career Services,
     r/EngineeringResumes) rather than by taste. Six candidates were considered and REFUSED
     because the sources disagree with each other about them — or say nothing at all — and a
     baseline that flattened a Layout's considered opposite position would be a worse defect than
     the silence it fixes:

       · `twelve-years` — every source says drop the ancient jobs, none states a number, and
         Europass is a full-history form by design. The Headless Headhunter's twelve is the
         guide's, so it stays the guide's.
       · `past-tense` — the general standard permits the present tense for a job you still hold
         ("pick one, be consistent"); the guide's words are "even if you are still employed".
       · `one-sentence` / `no-terminal-period` — a bullet takes exactly one period, or none.
         Flatly opposite, one per Layout, already written down that way.
       · `pronouns` — the standard forbids I / my / we; the Headless Headhunter's own worked
         example contains "while I prepared their food", so that layout would fire on the
         document it is calibrated against.
       · `no-www` — the other half of the wiki's "do NOT include https://www.". Refused for the
         same reason as `pronouns`, and measured: `headless-headhunter`'s example writes
         `www.linkedin.com/in/leekorelitz`, and it is the only one of the eighteen shipped
         starters and examples that contains `www.` or `://` at all. The scheme half is below as
         `plain-links`; this half stays in the hint on each of the three `holdsUrl` fields —
         `header.link` had no hint at all until this change, so for the one address every résumé
         carries the advice had nowhere to live.
       · `certification-provenance` — a certification with no issuer and no date. It is
         defensible advice and it is nobody's *stated* standard: certifications appear twice in
         the packaged sources and neither is a requirement — "certifications that are expired or
         irrelevant" (advice to DROP one, not to date it) and a bare section-order listing in the
         India notes. An AWS certification also names its issuer inside its own name, so the
         issuer half would fire on correct entries. Absence of a source is not agreement, which
         is the bar ADR-0127 set. A Layout that ships the block may still state it:
         `deedy-resume` and `mcdowell-cv` are the two that render `certification` by name.

     A Layout OPTS OUT by declaring a Rule with the same id AND marking it `overridesBaseline`,
     and `define` then keeps its own — a better lever than a flag on the layout, because it puts
     the disagreement on the rule, where its author is already looking, and refuses the silent
     version of it outright.

     Seven of the nine ids below are already declared by the Headless Headhunter layout, so those
     seven add nothing to it; `plain-links` and `award-scale` are new to every layout, which is
     the point — the three components ADR-0130 added shipped with no rule of any kind on any of
     the nine, so a certification with an empty issuer, an award titled "Winner" and a profile
     line reading `https://github.com/ravi` produced zero findings everywhere.

     That the seven add nothing to it is not the same as "nothing changed for it": `SCALE` gained
     the spelled-out numbers when this baseline was calibrated, and that layout's own `result` reads
     `SCALE` from here — so a bullet reading "brought four engineers through onboarding in six
     months" used to earn a note there and no longer does. Measured, and correct: it has two
     numbers in it. */

  /* A title that is a ranking and nothing else. Deliberately anchored and deliberately short: the
     shipped worked examples include "Engineering Excellence Award", which carries no number at
     all, and a rule that asked every award for one would flag the document `mcdowell-cv` is
     calibrated against. */
  const BARE_PLACING = new RegExp('^(?:winners?|finalists?|semi-?finalists?|runners?[\\s-]?up|' +
    'champions?|awardee|recipient|honou?ree|' +
    '(?:1st|2nd|3rd|first|second|third|top)\\s+(?:place|prize|position|finish))\\s*[.!]?$', 'i');

  const COMMON_RULES = Object.freeze([
    {
      id: 'contact', label: 'A name, and a way to reach you',
      check(doc, api) {
        const out = [];
        const headers = api.nodesOfType('header');
        if (!headers.length) {
          return [{ level: 'error', nodeId: null,
            message: 'No name and no contact details anywhere on the page.' }];
        }
        for (const n of headers) {
          const c = api.content(n.id);
          if (!String(c.fullName || '').trim()) {
            out.push({ level: 'error', nodeId: n.id, message: 'No name on the résumé.' });
          }
          if (!String(c.email || '').trim() && !String(c.phone || '').trim()) {
            /* Email OR phone, not both: the Harvard guide asks for both, the
               r/EngineeringResumes wiki calls the phone unnecessary, and a baseline states only
               what they agree on. Which of the two to print, and whether to add a city, is the
               Layout's argument — the Headless Headhunter layout makes it. */
            out.push({ level: 'error', nodeId: n.id,
              message: 'No email and no phone number. A résumé nobody can answer is the one fault no rewrite fixes — it is second on Harvard’s list of the commonest résumé mistakes.' });
          }
        }
        return out;
      },
    },
    {
      id: 'dates', label: 'Month and year on every job',
      check(doc, api) {
        const out = [];
        /* Three verdicts per cell rather than two — `dateFinding` is what tells "no month" from
           "a month this build cannot read", which is not an error it could justify. */
        for (const n of api.nodesOfType('work_entry')) {
          const c = api.content(n.id);
          if (!parseMonth(c.start)) {
            out.push(dateFinding(n.id, c.start,
              'Start date needs a month and a year — “June 2023”. A bare year leaves an eleven-month hole, and a parser files the job on this line.'));
          }
          if (!c.current && !parseMonth(c.end) && !STILL_HERE.test(String(c.end || ''))) {
            out.push(dateFinding(n.id, c.end, 'End date needs a month and a year, or tick “Still here”.'));
          }
        }
        return out;
      },
    },
    {
      /* The CEILING only, and the asymmetry is measured rather than tidy. Every source caps a
         job at seven or eight bullets, so twelve is nobody's advice. The floor is a different
         matter: the guide says three, the general standard's own table gives an old role two,
         and this repo's `harvard-classic` and `europass` examples each give a minor entry ONE
         on purpose — a Leadership line and a compressed first job. A baseline that flagged
         those would be house style wearing a baseline's clothes, so the floor stays with the
         Headless Headhunter layout, which has a source that states it. */
      id: 'bullet-count', label: 'No more than eight bullets a job',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('work_entry')) {
          const count = n.children.filter(c => c.type === 'bullet').length;
          if (count > 8) {
            out.push({ level: 'warn', nodeId: n.id,
              message: count + ' bullets. No standard here asks a single job for more than eight, and a recruiter reads the first three.' });
          }
        }
        return out;
      },
    },
    {
      id: 'three-lines', label: 'No bullet over three lines',
      check(doc, api) {
        const out = [];
        const size = +api.theme.bodySize || 10.5;
        const indent = +api.theme.bulletIndent || 0.3;
        for (const n of api.nodesOfType('bullet')) {
          const text = String(api.content(n.id).text || '');
          /* The width of the COLUMN this bullet sits in, not of the page. A bullet in a third
             of the measure wraps three times as often, and reporting the page-wide answer for
             it is the difference between "nothing to flag" and a bullet that really prints
             four lines. */
          const perLine = charsIn(api.measure(n), size, indent);
          if (text.length > perLine * 3) {
            out.push({ level: 'warn', nodeId: n.id,
              message: 'About ' + Math.ceil(text.length / perLine) + ' lines long. Three is the ceiling every standard here states; past that, split it in two.' });
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
            const started = (c.current || STILL_HERE.test(String(c.end || '')))
              ? Infinity : asMonths(parseMonth(c.start));
            if (started == null) { previous = null; continue; }
            if (previous != null && started > previous) {
              out.push({ level: 'warn', nodeId: job.id,
                message: 'Out of order. Reverse chronological — newest job first — is the one thing every résumé standard agrees on, and it is what a reader assumes without checking. Drag it up.' });
            }
            previous = started;
          }
        }
        return out;
      },
    },
    {
      id: 'opening-verb', label: 'Every bullet opens with a strong verb',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('bullet')) {
          const word = opener(api.content(n.id).text);
          if (anyStemIn(WEAK_OPENERS, word)) {
            out.push({ level: 'warn', nodeId: n.id, message: '“' + word +
              '” opens the bullet without saying what you did. “Using passive language ' +
              'instead of action words” is third on Harvard’s list of the commonest résumé ' +
              'mistakes. Start with the action — Built, Operated, Reduced.' });
          } else if (anyStemIn(SUPERFLUOUS, word)) {
            out.push({ level: 'warn', nodeId: n.id, message: '“' + word +
              '” is a dressed-up verb. A résumé gets fifteen seconds, which is not long ' +
              'enough to decode one; use the plain word.' });
          }
        }
        return out;
      },
    },
    {
      id: 'result', label: 'What, how, and the result or the reason',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('bullet')) {
          const c = api.content(n.id);
          /* A bullet flagged as a job's opening summary is exempt: it says what the job WAS,
             and the outcomes belong to the bullets under it. */
          if (c.role) continue;
          const text = String(c.text || '');
          if (!text.trim()) continue;
          if (!SCALE.test(text) && !OUTCOME.test(text)) {
            /* A note, not a warning. "Not demonstrating results" is fifth on Harvard's list,
               but a reason counts where there is no number, and plenty of true bullets have
               neither to claim. */
            out.push({ level: 'note', nodeId: n.id,
              message: 'No number, no result and no reason. A bullet is worth reading when it says what you did, how you did it, and what came of it.' });
          }
        }
        return out;
      },
    },
    {
      /* The one piece of link advice every source here states outright, and the narrowest form of
         it. The wiki's line is "do NOT include https://www.", and the `https://` half is the half
         nobody disagrees with — no template on the picker writes a scheme, and no worked example
         in this repo contains one. The `www.` half is NOT baseline: `headless-headhunter`'s own
         example writes `www.linkedin.com/in/leekorelitz`, so a baseline that included it would
         fire on the document that layout is calibrated against — the same test that kept
         `pronouns` out. It lives in the hint on every `holdsUrl` field instead. */
      id: 'plain-links', label: 'Links in plain text',
      check(doc, api) {
        const out = [];
        for (const n of api.flatten()) {
          const spec = Components.get(n.type);
          if (!spec) continue;
          const c = api.content(n.id);
          for (const f of spec.fields) {
            /* `holdsUrl`, declared by the Component Type, not a list of field names kept here.
               The names do not carry the fact: `certification`'s address field is `credential`,
               and so are `degree_entry`'s and `education_entry`'s, which hold the name of a
               qualification. A list keyed on `credential` would flag a degree; one that left it
               out missed a certification's link, which is what the first draft of this did. */
            if (!f.holdsUrl || !/:\/\//.test(String(c[f.key] || ''))) continue;
            out.push({ level: 'warn', nodeId: n.id, message: '“' + f.label +
              '” starts with a scheme. Drop it — a résumé writes its links as plain text, ' +
              'because an underlined blue URL is the one thing on the page a reader’s eye goes ' +
              'to instead of your work.' });
          }
        }
        return out;
      },
    },
    {
      /* Quantification — the thing every source here agrees on — at its narrowest. It asks only
         of a title that is a ranking word and NOTHING else, and then only when neither the giver
         nor the place has named a number either: "Winner" beside "Smart India Hackathon, 340
         teams" has already said what was beaten. `when` is not read, because a date is digits and
         reading it would silence this rule on every award that states one. */
      id: 'award-scale', label: 'An award says what you beat',
      check(doc, api) {
        const out = [];
        for (const n of api.nodesOfType('award_entry')) {
          const c = api.content(n.id);
          const title = String(c.title || '').trim();
          if (!BARE_PLACING.test(title)) continue;
          if (SCALE.test(String(c.awarder || '') + ' ' + String(c.place || ''))) continue;
          out.push({ level: 'note', nodeId: n.id, message: '“' + title +
            '” is a word, not an award. Say the field you beat — “1st of 340 teams” — because ' +
            'the size of the field is the only part of a placing a reader can weigh.' });
        }
        return out;
      },
    },
  ]);

  /* ---- what the printer may not split -------------------------------------------------------
     A résumé entry is ATOMIC: the printer moves a whole job or degree onto the next sheet rather
     than cutting it in half, and the editor's page-break sweep draws its cuts on that promise.

     The promise used to be made twice, in two vocabularies, by two parties who then drifted: the
     sweep asks the SHAPE, the stylesheets answered in each layout's own entry CLASS, and they
     disagreed on 5 of 9 layouts. ADR-0134 has the measurement and the two options refused.

     So the shape owns the answer and the stylesheet is DERIVED from it, rather than each layout
     restating it and the sweep hoping. Emitted here, after the layout's own rules and at equal
     specificity, so it reaches the preview, the miniature, the gallery card, the print dialog and
     the HTML download through the one `css()` all five already call — and `scope` falls back the
     same way every layout's own `css()` already falls it back, rather than emitting a rule that
     begins `undefined`.

     It is deliberately not a per-layout choice: eight of nine layouts already declared it by
     hand, unanimously, and the ninth had simply never written entry CSS, so there was no seam
     here — only a duplicated fact. A layout that one day wants a splittable entry has to change
     this line, which is also the line the sweep reads. */
  const ATOMIC_ENTRY = ' [data-shape="entry"] { page-break-inside: avoid; break-inside: avoid; }';

  /** Register a Layout. */
  function define(spec) {
    if (!spec || !spec.id) fail('a layout id is required');
    if (registry.has(spec.id)) fail('duplicate layout: ' + spec.id);
    if (typeof spec.css !== 'function') fail(spec.id + ': css(theme, scope) is required');
    if (typeof spec.starter !== 'function') fail(spec.id + ': starter(builder) is required');
    if (!spec.render || !spec.render.byShape) fail(spec.id + ': render.byShape is required');
    /* Every shape must have a strategy. Missing one is not a soft failure: a component type
       added next year will arrive wearing a shape this layout never anticipated, and the whole
       promise of shape dispatch is that it renders anyway. */
    const missing = Components.SHAPES.filter(s => typeof spec.render.byShape[s] !== 'function');
    if (missing.length) fail(spec.id + ': no renderer for shape(s) ' + missing.join(', '));

    const ownRules = (spec.rules || []).slice();
    const ownIds = new Set(ownRules.map(r => r.id));
    /* Shadowing a baseline Rule is allowed and is the opt-out mechanism (see COMMON_RULES) —
       but only ON PURPOSE. Silence here is the exact failure ADR-0127 exists to prevent: an
       author of the next layout who names a rule `dates` for their own reasons would drop the
       baseline's without a word, and rediscover it as a Checks panel that says nothing. So a
       collision must be declared on the rule itself, where its author is already looking. */
    const baselineIds = new Set(COMMON_RULES.map(r => r.id));
    const shadows = ownRules.filter(r => baselineIds.has(r.id) && !r.overridesBaseline);
    if (shadows.length) {
      fail(spec.id + ': rule id(s) ' + shadows.map(r => r.id).join(', ') +
        ' shadow a baseline rule. Set overridesBaseline: true on the rule if replacing it is ' +
        'deliberate — a layout that disagrees should say what it believes — or rename it.');
    }
    /* And the flag must still name something. A baseline rule renamed later would otherwise
       leave a layout claiming to override a rule that no longer exists. */
    const stale = ownRules.filter(r => r.overridesBaseline && !baselineIds.has(r.id));
    if (stale.length) {
      fail(spec.id + ': rule id(s) ' + stale.map(r => r.id).join(', ') +
        ' are marked overridesBaseline but no baseline rule has that id.');
    }

    const layout = Object.freeze({
      id: spec.id,
      label: spec.label || spec.id,
      blurb: spec.blurb || '',
      /* Named after what it is, so the picker can say it: "Single column, Arial, 10.5pt". */
      summary: spec.summary || '',
      /* Whose method this is, for a layout that implements somebody else's. Shown beside the
         picker: the Headless Headhunter's template was credited only in a source comment, which
         is not a credit — nobody using the product ever reads it. Optional, because a layout
         that is nobody's method in particular has nothing to say here. */
      credit: spec.credit || '',
      page: Object.freeze(Object.assign({ width: 8.5, height: 11, margin: 1, unit: 'in' }, spec.page || {})),
      tokens: Object.freeze(Object.assign({}, spec.tokens || {})),
      /* Which tokens the user may move, and between what bounds. This is the "how they look"
         surface: everything here shows up in the inspector as a control, and nothing else does,
         so a layout cannot be dialled out of its own identity. */
      tunables: Object.freeze((spec.tunables || []).map(t => Object.freeze(Object.assign({}, t)))),
      slots: Object.freeze((spec.slots || [{ id: 'main', label: 'Main column', grow: 1 }])
        .map(s => Object.freeze(Object.assign({}, s)))),
      caps: Object.freeze(Object.assign({
        mode: 'flow',        // 'flow' = reorder within slots; 'free' = x/y/w/h positioning
        resize: [],          // geometry keys this layout honours: spaceAfter | gutter | box
        reorder: true,       // may nodes be dragged into a new order at all
      }, spec.caps || {})),
      /* Bounds for every geometry key the layout honours, so a drag cannot produce a document
         the layout would not have rendered. */
      bounds: Object.freeze(Object.assign({
        spaceAfter: [0, 48], gutter: [0.6, 3.2], w: [0.5, 8], h: [0.2, 10], x: [0, 8], y: [0, 10],
      }, spec.bounds || {})),
      css: (theme, scope) => spec.css(theme, scope) + '\n' + (scope || '.rb-doc') + ATOMIC_ENTRY,
      starter: spec.starter,
      /* Optional: a second, filled starting point. A layout that ships a worked example teaches
         the shape of good content far faster than placeholder text can, so the picker offers it
         where one exists and stays quiet where it doesn't. */
      example: typeof spec.example === 'function' ? spec.example : null,
      /* Optional: fix up a document that has just arrived from a different Layout. Content is
         never touched here — only the Layer-2 facts this layout needs and the last one had no
         reason to write. A free-positioning layout uses it to give coordinates to blocks that
         have none, which is the difference between "switched layout" and "half the page is
         stacked in the corner on top of the other half". */
      adopt: typeof spec.adopt === 'function' ? spec.adopt : null,
      /* Optional: how wide the text of one slot really is, in inches, when the Layout's geometry
         is not a plain share of `grow` — a label gutter, a fixed band. Read by `measureOf`, which
         is what tells a Rule how many characters reach the end of a line. */
      measure: typeof spec.measure === 'function' ? spec.measure : null,
      /* The Layout's own rules first, then every baseline rule it did NOT state itself. A
         Layout opts out of one by declaring its own with that id — see COMMON_RULES. */
      rules: Object.freeze(ownRules.concat(COMMON_RULES.filter(r => !ownIds.has(r.id)))),
      _render: spec.render,
    });
    registry.set(layout.id, layout);
    order.push(layout.id);
    return layout;
  }

  const get = id => registry.get(id) || null;
  const all = () => order.map(id => registry.get(id));

  /* ---- paper ---------------------------------------------------------------------------
     Most layouts here are written for US Letter, which is the wrong sheet almost everywhere
     outside North America — and HeadStart is deliberately a global product, not a US one. A4 is
     0.23in narrower and 0.69in taller, so a résumé laid out on Letter and printed on A4 re-wraps
     every bullet and moves its page break: for a builder whose headline check is "no bullet over
     three lines", that is a wrong answer shown confidently.

     The sheet is the DOCUMENT's choice, and it OVERRIDES whatever the Layout declares. What a
     Layout declares is only where a new document starts: this used to read "not the Layout's at
     all", on the argument that a Letter template and an A4 one would be two copies of the same
     layout. That holds for a template. It does not hold for a FORM — Europass is an A4 document
     the way a passport is a passport-sized one, and starting it on Letter is not a preference,
     it is the wrong form. So `europass` declares A4 and everything else declares Letter, and a
     document that names a paper still wins over both.

     It is deliberately not a tunable either: tunables are type tokens interpolated into a
     stylesheet, and this is geometry every rule and every measurement reads. */
  const PAPERS = Object.freeze([
    Object.freeze({ id: 'letter', label: 'US Letter · 8.5 × 11in', width: 8.5, height: 11 }),
    Object.freeze({ id: 'a4', label: 'A4 · 210 × 297mm', width: 8.27, height: 11.69 }),
  ]);

  /** The page a document is actually laid out on: the Layout's own page with the document's
   *  sheet substituted. An absent or unreadable name falls back to the Layout's — a document is
   *  a file people exchange, so `paper` is untrusted, and it is only ever a lookup key here. */
  function pageFor(layout, doc) {
    const paper = PAPERS.find(p => p.id === (doc && doc.paper));
    return paper
      ? Object.assign({}, layout.page, { width: paper.width, height: paper.height })
      : layout.page;
  }

  /** The area a sheet leaves for content, in inches: the page less its two margins, to the
   *  hundredth of an inch. The rounding is not decoration — measured across every layout on both
   *  sheets, `mcdowell-cv` on A4 subtracts to 7.069999999999999, and a bound that reads back like
   *  that is one nobody trusts in a range control or an assertion. */
  const usable = page => ({
    wide: hundredth(page.width - 2 * page.margin),
    tall: hundredth(page.height - 2 * page.margin),
  });
  const hundredth = n => Math.round(n * 100) / 100;

  /** The bounds in force for one document.
   *
   *  Box coordinates — x, y, w, h — are a fact about the SHEET, not about the Layout: the usable
   *  area is the page less its margins, and the page is the document's choice (`pageFor`). The one
   *  free-positioning layout stated the US-Letter answer as constants, so an A4 document, whose
   *  usable area is 6.67 × 10.09in against Letter's 6.9 × 9.4, had its blocks clamped to 6.9in
   *  wide and printed 0.23in past the right margin. Everything else a Layout bounds — spacing, a
   *  gutter — is its own, and comes through untouched.
   *
   *  The minima stay the Layout's: how small a block may be dragged is a readability call, not a
   *  paper one. They are floored at the sheet only so a bound can never come back inverted. */
  function boundsFor(layout, doc) {
    const b = layout.bounds;
    if (!layout.caps.resize.includes('box')) return b;
    const { wide, tall } = usable(pageFor(layout, doc));
    return Object.assign({}, b, {
      x: [b.x[0], wide], y: [b.y[0], tall],
      w: [Math.min(b.w[0], wide), wide], h: [Math.min(b.h[0], tall), tall],
    });
  }

  /** Which entry in PAPERS the sheet in force corresponds to — the Layout's own when the document
   *  states nothing. The Design pane's control read `doc.paper || 'letter'`, so a fresh Europass
   *  document rendered and printed A4 while the control beside it said "US Letter": the one place
   *  in the tab where a control disagreed with the page it governs. Matched on width because a
   *  Layout declares inches, not a paper name. */
  function paperIdFor(layout, doc) {
    if (doc && PAPERS.some(p => p.id === doc.paper)) return doc.paper;
    const page = layout.page || {};
    const match = PAPERS.find(p => Math.abs(p.width - page.width) < 0.05);
    return match ? match.id : PAPERS[0].id;
  }

  /* Every token value is interpolated straight into a stylesheet, and that stylesheet is written
     into a document by the print and download paths. So a token is only allowed to be the kind of
     thing its Layout says it is. Without this, a résumé document — which the product invites
     people to exchange as a .json backup — could carry
     `fontFamily: 'Arial</style><script>…'` and run script in HeadStart's own origin the moment it
     was previewed. Validation lives HERE rather than at the import, because this function is the
     single point every render, print and export passes through; guarding the import alone would
     leave any other future source of a document unguarded. */
  const HEX = /^#[0-9a-fA-F]{3,8}$/;
  /* Conservative on purpose: font stacks, keywords and lengths, and nothing that could close a
     declaration, a rule, or the element itself. */
  const SAFE_CSS_WORD = /^[\w\s,.'"()%#-]{0,120}$/;

  /** The value to use for one token, or null if the override cannot be trusted. */
  function vetted(tunable, value, fallback) {
    if (value == null) return null;
    if (tunable && tunable.kind === 'range') {
      const n = Number(value);
      if (!isFinite(n)) return null;
      return clampNum(n, tunable.min, tunable.max);
    }
    if (tunable && tunable.kind === 'color') return HEX.test(String(value)) ? String(value) : null;
    if (tunable && tunable.kind === 'select') {
      return (tunable.options || []).some(o => o[0] === value) ? value : null;
    }
    /* No tunable: the UI offers no way to set this, so an override can only have arrived with a
       document. Allow it only if it is the same shape as the layout's own default and cannot
       carry markup or a second declaration. */
    if (typeof fallback === 'number') {
      const n = Number(value);
      return isFinite(n) ? n : null;
    }
    return SAFE_CSS_WORD.test(String(value)) ? String(value) : null;
  }

  /** The tokens in force for a document: the layout's defaults with the document's own
   *  overrides on top, for keys the layout declares and values it can vouch for. */
  function themeFor(layout, doc) {
    const out = Object.assign({}, layout.tokens);
    const over = (doc && doc.theme) || {};
    const tunables = new Map(layout.tunables.map(t => [t.key, t]));
    for (const key of Object.keys(out)) {
      const value = vetted(tunables.get(key), over[key], out[key]);
      if (value != null) out[key] = value;
    }
    return out;
  }

  /** A node's geometry, clamped to what this layout allows and stripped of what it ignores.
   *  `doc` is optional and matters only to box coordinates, whose bounds are the sheet's
   *  (`boundsFor`); without it the Layout's own sheet is the one clamped to. */
  function geometryFor(layout, node, doc) {
    const geo = {};
    const allowed = layout.caps.resize;
    const bounds = boundsFor(layout, doc);
    const g = node.geometry || {};
    if (allowed.includes('spaceAfter') && g.spaceAfter != null) {
      geo.spaceAfter = clampNum(+g.spaceAfter, bounds.spaceAfter[0], bounds.spaceAfter[1]);
    }
    if (allowed.includes('gutter') && g.gutter != null) {
      geo.gutter = clampNum(+g.gutter, bounds.gutter[0], bounds.gutter[1]);
    }
    if (allowed.includes('box')) {
      for (const k of ['x', 'y', 'w', 'h']) {
        if (g[k] != null) geo[k] = clampNum(+g[k], bounds[k][0], bounds[k][1]);
      }
    }
    return geo;
  }

  /* ---- rendering ----------------------------------------------------------------------
     One pass, post-order: children are rendered to HTML strings and handed to the parent's
     strategy. Strategies build their outer element with `ctx.el`, which is what stamps the
     node id onto the DOM — the editor finds a node from a click through that attribute and
     nothing else, so a strategy that hand-rolls its outer tag makes its component unselectable.
     That contract is checked by resume_layouts.test.js rather than trusted. */

  function renderNode(layout, doc, node, opts) {
    const theme = opts.theme;
    const kids = node.children.map(c => renderNode(layout, doc, c, opts));
    const spec = Components.get(node.type);
    const shape = spec ? spec.shape : 'text';
    const strategy = (layout._render.byType && layout._render.byType[node.type])
      || layout._render.byShape[shape];

    const geo = geometryFor(layout, node, doc);
    const ctx = {
      node, doc, layout, theme, spec,
      content: doc.content[node.id] || {},
      children: kids,
      childNodes: node.children,
      geo,
      esc, escLines, attrs,
      /* The node's own words, in the order its Component Type declares them, skipping empties.
         A `byShape` renderer must never name a FIELD: it is handed components it has never heard
         of, and the moment it writes `content.label` it renders every component that spells that
         field differently as an empty box. Measured before this existed: a Language line with
         fields `name`/`level` rendered as literally nothing in all three layouts — a user's
         languages would have vanished off the page in silence. `byType` renderers may name
         fields freely; they are written for a type they know. */
      fields() {
        const declared = spec ? spec.fields : [];
        return declared
          .map(f => ({ key: f.key, label: f.label, value: doc.content[node.id] ? doc.content[node.id][f.key] : null }))
          .filter(f => typeof f.value === 'string' && f.value.trim());
      },
      /** Every field's value, joined — the last-resort rendering of an unknown component. */
      textOf(separator) {
        return ctx.fields().map(f => escLines(f.value)).join(separator || ' &middot; ');
      },
      /** The outer element of a rendered node. Adds the id hook, the editor's classes and the
       *  geometry the layout honours; a strategy passes its own class and inner HTML. */
      el(tag, a, inner) {
        const own = Object.assign({}, a || {});
        const style = [own.style || ''];
        if (geo.spaceAfter != null) style.push('margin-bottom:' + geo.spaceAfter + 'px');
        if (layout.caps.mode === 'free' && (geo.x != null || geo.y != null)) {
          style.push('position:absolute',
            'left:' + (geo.x || 0) + layout.page.unit, 'top:' + (geo.y || 0) + layout.page.unit,
            'width:' + (geo.w || 3) + layout.page.unit);
          if (geo.h != null) style.push('min-height:' + geo.h + layout.page.unit);
        }
        own.style = style.filter(Boolean).join(';');
        own.class = ['rb-node', own.class].filter(Boolean).join(' ');
        own['data-node'] = node.id;
        own['data-type'] = node.type;
        own['data-shape'] = shape;
        return '<' + tag + attrs(own) + '>' + (inner == null ? '' : inner) + '</' + tag + '>';
      },
    };
    return strategy(ctx);
  }

  /** Render a whole document to HTML. Slots are rendered in the layout's own slot order, so a
   *  node whose stored slot no longer exists lands in the first one rather than disappearing. */
  function renderDocument(layout, doc) {
    const theme = themeFor(layout, doc);
    const known = new Set(layout.slots.map(s => s.id));
    const bySlot = new Map(layout.slots.map(s => [s.id, []]));
    for (const n of doc.root.children) {
      const slot = known.has(n.slot) ? n.slot : layout.slots[0].id;
      bySlot.get(slot).push(n);
    }
    const cols = layout.slots.map(s => {
      const inner = bySlot.get(s.id).map(n => renderNode(layout, doc, n, { theme })).join('\n');
      return '<div class="rb-slot" data-slot="' + esc(s.id) + '" style="flex:' + (s.grow || 1) +
        ' 1 0%">' + inner + '</div>';
    }).join('\n');
    return '<div class="rb-doc rb-' + esc(layout.id) + '"' +
      (layout.caps.mode === 'free' ? ' data-free="1"' : '') + '>' + cols + '</div>';
  }

  /** A standalone HTML document — what the HTML and Word downloads are built from, and what
   *  makes the download match the preview: same renderer, same stylesheet, different wrapper. */
  function renderStandalone(layout, doc, options) {
    const opts = options || {};
    const theme = themeFor(layout, doc);
    const page = pageFor(layout, doc);
    const frame = [
      '@page { size: ' + page.width + page.unit + ' ' + page.height + page.unit +
        '; margin: ' + page.margin + page.unit + '; }',
      'body { margin: 0; background: #fff; }',
      '.rb-sheet { width: ' + (page.width - 2 * page.margin) + page.unit + '; margin: 0 auto;' +
        ' padding: ' + page.margin + page.unit + ' 0; background: #fff; }',
      '@media print { .rb-sheet { padding: 0; width: auto; } }',
    ].join('\n');
    return '<!doctype html>\n<html><head><meta charset="utf-8">' +
      '<title>' + esc(doc.name || 'Résumé') + '</title>' +
      (opts.wordMeta ? '<meta name="ProgId" content="Word.Document">' : '') +
      '<style>\n' + frame + '\n' + layout.css(theme, '.rb-doc') + '\n</style></head>' +
      '<body><div class="rb-sheet">' + renderDocument(layout, doc) + '</div></body></html>';
  }

  function nodesOf(doc) {
    const out = [];
    (function walkTree(node) {
      for (const child of node.children || []) { out.push(child); walkTree(child); }
    })(doc.root);
    return out;
  }

  /** Run a Layout's own rules over a document. Pure — no DOM — so the rule set can be tested
   *  as data rather than by reading a panel, which is how the Headless Headhunter checks are
   *  pinned against the guide's own worked example.
   *
   *  A rule that throws is caught and reported as one unrunnable check: the other rules still
   *  have something useful to say, and a résumé with one broken check is not a broken résumé. */
  /* Its own walk rather than ResumeDocument's. Layer 2 importing Layer 3 was the one place the
     layering this whole design rests on actually leaked, and the thing borrowed was six lines of
     tree recursion over a plain object — not worth the dependency it cost. */
  function runRules(layout, doc) {
    const all = nodesOf(doc);
    const page = pageFor(layout, doc);
    const theme = themeFor(layout, doc);
    /* Which slot each node's words end up in. Built once by walking down from the top-level
       blocks, because a Rule is handed a bullet and the slot is a fact about its ancestor. */
    const slotOf = new Map();
    const topOf = new Map();
    const known = new Set(layout.slots.map(sl => sl.id));
    for (const top of doc.root.children) {
      const slot = known.has(top.slot) ? top.slot : layout.slots[0].id;
      (function mark(node) {
        slotOf.set(node.id, slot);
        topOf.set(node.id, top);
        for (const kid of node.children || []) mark(kid);
      })(top);
    }
    const api = {
      layout,
      /* The sheet in use, which is not `layout.page` once the document has chosen one. A rule
         that measures the page must measure the page it will be printed on. */
      page,
      theme,
      /** The usable width in inches for one node's text — the column it sits in, not the page.
       *  A single-column Layout answers with the whole measure, which is what it always was. */
      measure: node => measureOf(layout, page, theme,
        slotOf.get(node && node.id) || layout.slots[0].id, topOf.get(node && node.id)),
      content: id => doc.content[id] || {},
      nodesOfType: type => all.filter(n => n.type === type),
      flatten: () => all.slice(),
    };
    const out = [];
    for (const rule of layout.rules) {
      let found;
      try {
        found = rule.check(doc, api) || [];
      } catch (err) {
        found = [{ level: 'note', nodeId: null, message: 'This check could not run.' }];
      }
      for (const f of found) out.push(Object.assign({ rule: rule.label, ruleId: rule.id }, f));
    }
    /* Errors first, then warnings, then notes — the panel renders them in this order and the
       badge counts everything above a note. */
    /* `??`, not `||`: an error's rank is 0, which is falsy, so `||` gave every error the
       unknown-level rank of 3 and sorted it BELOW every note. Measured in Chromium before the
       fix — the first error landed 1,524px down, under twelve lower-priority rows and off the
       bottom of an 1,100px viewport. Nine PRs read the comment above and none read the line. */
    const rank = { error: 0, warn: 1, note: 2 };
    return out.sort((a, b) => (rank[a.level] ?? 3) - (rank[b.level] ?? 3));
  }

  /* ---- shared strategy helpers ---------------------------------------------------------
     Small pieces more than one layout wants. A helper here must be about *structure*
     ("a left label and a right date"), never about a particular look. */

  /** "June 2023 to Current" from a work entry's own fields.
   *
   *  `style` overrides the two words templates disagree about. The Headless Headhunter guide
   *  says "June 2023 to Current" and is the default, so the three layouts written before this
   *  argument existed are untouched; Jake's Resume and the Harvard standard both write
   *  "June 2023 – Present", and the general résumé standard is explicit that "Current" is wrong.
   *  Neither spelling is checked anywhere — it is the layout's call, which is the point. */
  function dateRange(content, style) {
    const s = Object.assign({ joiner: ' to ', current: 'Current' }, style || {});
    const start = (content.start || '').trim();
    const end = content.current ? s.current : (content.end || '').trim();
    /* An end with no start prints the end. Education is the reason — every standard here asks
       for the graduation date alone — and a job that carries only an end date used to render
       its dates as nothing at all, which is a word lost rather than a word withheld. */
    if (!start) return end ? (content.current ? s.joiner.trim() + ' ' + end : end) : '';
    return end ? start + s.joiner + end : start;
  }

  /** "Cashier at Large Ducks Coffee, TX" — the parts that exist, in the guide's own order. */
  function roleLine(content) {
    const bits = [];
    if (content.role) bits.push(content.role);
    if (content.company) bits.push((bits.length ? 'at ' : '') + content.company);
    let line = bits.join(' ');
    if (content.place) line = line ? line + ', ' + content.place : content.place;
    return line;
  }

  /** A row with one thing against each margin — the shape Jake's Resume and the Harvard template
   *  both build their entries out of ("institution left, location right", then "degree left,
   *  dates right"). Structure only: the caller passes the classes, so the two layouts that use it
   *  look nothing like each other. */
  function marginRow(cls, left, right) {
    return '<div class="' + esc(cls) + '"><span class="rb-row-left">' + left + '</span>' +
      '<span class="rb-row-right">' + right + '</span></div>';
  }

  /** Consecutive `inList` children collected into one list, everything else left alone.
   *  Returns HTML. A Section can therefore render a mixed run — two education bullets, a
   *  paragraph, three more bullets — as two lists rather than one wrong one. */
  function groupChildren(ctx, listClass) {
    const out = [];
    let open = null;
    ctx.childNodes.forEach((child, i) => {
      const spec = Components.get(child.type);
      if (spec && spec.inList) {
        if (!open) { open = []; out.push(open); }
        open.push(ctx.children[i]);
      } else {
        open = null;
        out.push(ctx.children[i]);
      }
    });
    return out.map(part => Array.isArray(part)
      ? '<ul class="' + esc(listClass || 'rb-list') + '">' + part.join('') + '</ul>'
      : part).join('');
  }

  /** A generic last resort. Every layout gets this for shapes it has no opinion about, so
   *  "no renderer" is never a blank box. */
  /** The first declared field, treated as the node's own heading or label, and the rest. Shape
   *  renderers need "the important one and the others" without knowing what either is called. */
  function headAndRest(ctx) {
    const all = ctx.fields();
    return { head: all[0] || null, rest: all.slice(1) };
  }

  /* A generic last resort for every shape. None of these names a field — see `ctx.fields`. */
  function plainStrategies() {
    return {
      header: ctx => ctx.el('header', { class: 'rb-header' }, ctx.textOf()),
      text: ctx => ctx.el('p', { class: 'rb-text' }, ctx.textOf(' ')),
      section: ctx => {
        const { head } = headAndRest(ctx);
        return ctx.el('section', { class: 'rb-section' },
          (head ? '<h2>' + escLines(head.value) + '</h2>' : '') + groupChildren(ctx));
      },
      entry: ctx => ctx.el('div', { class: 'rb-entry' }, ctx.textOf() + ctx.children.join('')),
      line: ctx => {
        const { head, rest } = headAndRest(ctx);
        if (!head) return ctx.el('p', { class: 'rb-line' }, '');
        /* One field is just a line; two or more read as "label: the rest". */
        return ctx.el('p', { class: 'rb-line' }, rest.length
          ? '<b>' + escLines(head.value) + '</b> ' + rest.map(f => escLines(f.value)).join(' &middot; ')
          : escLines(head.value));
      },
      bullet: ctx => ctx.el('li', { class: 'rb-bullet' }, ctx.textOf(' ')),
    };
  }

  root.ResumeLayouts = {
    define, get, all, PAPERS, pageFor, paperIdFor, themeFor, usable, boundsFor, geometryFor,
    renderDocument, renderNode, renderStandalone, runRules, COMMON_RULES,
    esc, escLines, dateRange, roleLine, plainStrategies, groupChildren, clampNum, headAndRest,
    marginRow,
    /* The vocabulary a Rule is written in, shared with the layouts that state their own. */
    parseMonth, dateFinding, asMonths, charsPerLine, charsIn, measureOf, opener, anyStemIn,
    WEAK_OPENERS, SUPERFLUOUS, SCALE, OUTCOME,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
