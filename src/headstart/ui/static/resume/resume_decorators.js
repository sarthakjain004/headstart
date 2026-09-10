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

  const Components = root.ResumeComponents;

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

  /** Wrap every occurrence of any term in <mark>. Longest terms first, so "customer service"
   *  wins over "service" and the shorter one does not cut the longer one in half. Idempotent:
   *  applying it twice to the same string changes nothing the second time. */
  function highlight(terms) {
    const wanted = (terms || []).map(t => String(t).trim()).filter(Boolean)
      .sort((a, b) => b.length - a.length);
    if (!wanted.length) return null;
    const pattern = new RegExp('(' + wanted.map(forRegex).join('|') + ')', 'gi');
    const mark = text => text.replace(pattern, MARK_OPEN + '$1</mark>');
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

  root.ResumeDecorators = { compose, highlight, tailored, inTextNodes };
})(typeof globalThis !== 'undefined' ? globalThis : this);
