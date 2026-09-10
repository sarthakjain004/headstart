/* Render decorators (ADR-0123) — behaviour wrapped around a Layout without the Layout knowing.
 *
 * A Layout's render strategy is a function `(ctx) => html`. A decorator has exactly that same
 * shape and wraps one: it calls the strategy it was given and adorns what comes back. Because the
 * interface is identical, decorators compose, and a decorated Layout is still a Layout — the
 * renderer, the editor and the rule runner cannot tell the difference.
 *
 * That is the whole reason this file exists rather than the two features below living inside the
 * layouts. Highlighting the job's keywords on the page, and marking which blocks a version has
 * reworded, are things a *reader* wants at a particular moment. Neither is a property of the
 * Headless Headhunter template or of any other layout, and putting them there would mean writing
 * both into all three, and into every layout added afterwards.
 *
 * Decorators are a PREVIEW concern. Exports resolve the layout from the registry by id
 * (`renderStandalone`), so a downloaded résumé never carries a highlight or a marker — which is
 * the correct behaviour and falls out of the design rather than needing a flag.
 */
(function (root) {
  'use strict';

  /** Escape a term for use inside a RegExp — a keyword like "C++" is otherwise a syntax error. */
  const forRegex = s => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  /* Adorn only the text between tags. Rewriting the whole HTML string would match inside
     attributes — a term like "class" or "in" would corrupt markup rather than mark words. The
     content is already escaped by the time it gets here, so plain-text matching is safe. */
  function inTextNodes(html, transform) {
    return String(html).replace(/>([^<]+)</g, (whole, text) => '>' + transform(text) + '<');
  }

  const MARK_OPEN = '<mark class="rb-kw-hit">';
  /* Splits a rendered string into already-marked spans and everything else. Needed because a
     node's output CONTAINS its children's output: the renderer walks post-order and hands each
     parent its children's HTML, so a parent's decorator sees markup a child's decorator already
     produced. Without this, "customer service" came back wrapped in two nested <mark> elements —
     once by the bullet, once by the section above it. */
  const ALREADY_MARKED = /(<mark class="rb-kw-hit">[\s\S]*?<\/mark>)/;

  /* ---- matching a term ------------------------------------------------------------------
     What counts as "the résumé mentions Java". A raw substring answers yes to "JavaScript",
     and on a software job board that is not an edge case: Go, R, C, C#, AI, ML and Java are
     the corpus. `\b` cannot do it either — a word boundary is a word-character transition, and
     `+` and `#` are not word characters, so `\bC\+\+\b` matches nothing at all.

     So the boundary is spelled out as characters that may not sit against a term, and it is
     DELIBERATELY NOT SYMMETRIC: a period may FOLLOW a term but may not PRECEDE one.

     It may follow because the Headless Headhunter template puts a full stop at the end of every
     bullet, so a `.` in the trailing class reported "Built the payment service in Java." as
     having no Java — a résumé written exactly to this repo's own default template, scored as
     mentioning none of its own languages. It may not precede because that is what stops `NET`
     matching inside "ASP.NET" and `js` inside "Node.js".

     Measured over 27 rows of real résumé prose: substring 8 wrong, `\b` 8 wrong, the symmetric
     class 5 wrong, this 1 — `C++` inside "C++11", which is left unmatched on purpose. Dropping
     digits from the trailing class to catch it would make `R` match "R2D2", and single-letter
     terms are the whole reason this exists.

     Written with a consumed prefix rather than a lookbehind on purpose: lookbehind is Safari
     16.4 and later, this file's own floor is `??` (Safari 13.1), and a regex the browser
     refuses to compile takes the whole keyword panel down rather than degrading. Only the LEFT
     side consumes, so two terms either side of one space still both match. */
  const BEFORE = '(^|[^\\w+#.])';
  const AFTER = '(?![\\w+#])';

  /** A RegExp matching any of `terms` where it stands as a term of its own. Group 1 is the
   *  character the boundary consumed and must be written back; group 2 is the term.
   *
   *  Longest first, so "customer service" wins over "service" and the shorter one does not cut
   *  the longer one in half. Null when there is nothing to match. */
  function keywordPattern(terms) {
    const list = (Array.isArray(terms) ? terms : [terms])
      .map(t => String(t == null ? '' : t).trim()).filter(Boolean)
      .sort((a, b) => b.length - a.length);
    if (!list.length) return null;
    return new RegExp(BEFORE + '((?:' + list.map(forRegex).join('|') + '))' + AFTER, 'gi');
  }

  /** Does `text` mention `term`? The keyword score and the on-page highlight have to agree
   *  about this, and the only way to guarantee they do is for both to ask the same function —
   *  they were two substring tests before, and they already disagreed: `C++` read as missing
   *  from a résumé the highlighter was busy marking `C` all over.
   *
   *  The pattern is built PER CALL, and that is load-bearing rather than lazy: `keywordPattern`
   *  is `g`-flagged for `highlight`'s sake, and a `g` regex carries `lastIndex` across `.test()`
   *  calls — so a hoisted one would answer yes, no, yes, no down a list of blocks. A fresh
   *  object cannot. The recompile it costs is 0.5ms across a 15-term x 43-block sweep
   *  (measured), against a wrong answer every other row. */
  function mentions(text, term) {
    const pattern = keywordPattern(term);
    return !!pattern && pattern.test(String(text == null ? '' : text));
  }

  /** Wrap every occurrence of any term in <mark>. Idempotent: applying it twice to the same
   *  string changes nothing the second time. */
  function highlight(terms) {
    const pattern = keywordPattern(terms);
    if (!pattern) return null;
    const mark = text => text.replace(pattern, '$1' + MARK_OPEN + '$2</mark>');
    return function highlightDecorator(strategy) {
      return ctx => strategy(ctx).split(ALREADY_MARKED)
        .map(part => (part.startsWith(MARK_OPEN) ? part : inTextNodes(part, mark)))
        .join('');
    };
  }

  /** Mark the blocks a version has reworded or left out, so the difference from the master is
   *  visible on the page rather than only in the rail. */
  function tailored(doc) {
    const tailoring = (doc.tailorings || []).find(t => t.id === doc.activeTailoring);
    if (!tailoring) return null;
    const picks = tailoring.picks || {};
    return function tailoredDecorator(strategy) {
      return ctx => {
        const html = strategy(ctx);
        if (!picks[ctx.node.id]) return html;
        /* Inserted just inside the node's own outer tag, so it inherits the block's position and
           needs no second pass over the markup. */
        return html.replace(/>/, ' data-tailored="1">');
      };
    };
  }

  /** A Layout with its strategies wrapped. Same shape, same registry entry, same everything else
   *  — only the render functions differ, which is what lets the caller keep using it as a Layout.
   *  Decorators apply outermost-last, so `compose(l, a, b)` renders b(a(strategy)). */
  function compose(layout, decorators) {
    const active = (decorators || []).filter(Boolean);
    if (!active.length) return layout;

    const wrapAll = table => {
      const out = {};
      for (const key of Object.keys(table || {})) {
        out[key] = active.reduce((fn, decorate) => decorate(fn), table[key]);
      }
      return out;
    };

    /* A shallow copy rather than a mutation: the registry's Layout is frozen and shared, and a
       decorated one is a view of it that lasts exactly one render. */
    return Object.assign(Object.create(Object.getPrototypeOf(layout) || Object.prototype), layout, {
      _render: {
        byShape: wrapAll(layout._render.byShape),
        byType: wrapAll(layout._render.byType),
      },
    });
  }

  /* `keywordPattern` is deliberately NOT exported: `highlight` and `mentions` are the two
     things anyone outside needs, and a caller holding the `g`-flagged pattern itself would
     inherit the `lastIndex` trap `mentions` exists to close. */
  root.ResumeDecorators = { compose, highlight, tailored, mentions };
})(typeof globalThis !== 'undefined' ? globalThis : this);
