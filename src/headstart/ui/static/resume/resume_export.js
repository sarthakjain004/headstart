/* Getting a résumé off the page (ADR-0123).
 *
 * Four formats, each a Visitor over the same document — dispatching on the component's SHAPE,
 * never its type, so a component added later exports without touching this file:
 *
 *   PDF    — the browser's own print pipeline against the Layout's stylesheet. No PDF library
 *            and no server renderer: the preview, the print and the download are literally the
 *            same HTML and the same CSS, so "what you see is what you get" is structural rather
 *            than a thing to keep testing. It costs one dialog.
 *   Word   — the same HTML with the headers Word uses to open it as a document. The guide tells
 *            people to build the résumé in Word or Docs, so a file they can keep editing there
 *            is not a nice-to-have. It is genuinely HTML inside, which is why the button says
 *            "Word (.doc)" and not "Word (.docx)".
 *   Text   — one column, no markup. What to paste into the "or paste your résumé here" box that
 *            half of all application forms still have, and the version an ATS parses cleanly.
 *   JSON   — the document itself: content, structure, geometry, layout id. The backup, and the
 *            import path, since browser storage is one cleared cache away from empty.
 */
(function (root) {
  'use strict';

  const Components = root.ResumeComponents;
  const Layouts = root.ResumeLayouts;
  const Doc = root.ResumeDocument;

  /** A filename that will not surprise anyone: the résumé's own name, ASCII-folded. */
  /* `\w` is ASCII, so stripping to it threw away every script that is not Latin: a résumé named
     in Devanagari, Han, Cyrillic or Arabic reduced to nothing and downloaded as `resume.pdf`,
     for every user with a name in their own alphabet. Unicode classes keep the letters, the
     digits and the combining marks that make them — NFC rather than NFKD for the same reason,
     since decomposing a Devanagari cluster and then dropping its marks destroys the word.
     What is still removed is what a filename cannot carry: separators, quotes, control
     characters, and anything else outside those classes. */
  function filename(doc, ext) {
    const stem = String(doc.name || 'resume').normalize('NFC')
      .replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, '').trim().replace(/\s+/g, '_').slice(0, 60) || 'resume';
    return stem + '.' + ext;
  }

  /* ---- the plain-text visitor ----------------------------------------------------------
     Shape dispatch, so an unknown component still contributes its words rather than being
     dropped — a silently missing section in the copy someone pastes into an application form
     is the worst failure this module could have. */

  /** Every declared field with a value, in the order the Component Type lists them. The shape
   *  visitors below fall back to this when the fields they know by name are all absent — which is
   *  what happens for a component added after they were written. Without it an unknown component's
   *  words were dropped from the plain-text export entirely, and that export is the copy people
   *  paste into an application form. */
  function declared(content, spec) {
    if (!spec) return [];
    return spec.fields
      .map(f => content[f.key])
      .filter(v => typeof v === 'string' && v.trim());
  }

  const TEXT_VISITORS = {
    header: (content, node, spec) => {
      const lines = [];
      if (content.fullName) lines.push(String(content.fullName).toUpperCase());
      const contact = [content.phone, content.email, content.link].filter(Boolean).join(' | ');
      if (contact) lines.push(contact);
      if (content.locationLine) lines.push(content.locationLine);
      if (content.languages) lines.push(content.languages);
      if (lines.length) return lines.join('\n');
      const own = declared(content, spec);
      return own.length ? [String(own[0]).toUpperCase()].concat(own.slice(1)).join('\n') : '';
    },
    text: (content, node, spec) => String(content.text || declared(content, spec).join(' ')),
    section: (content, node, spec) =>
      '\n' + String(content.title || declared(content, spec)[0] || '').toUpperCase(),
    entry: (content, node, spec) => {
      if (node.type === 'work_entry') {
        const left = Layouts.roleLine(content);
        const dates = Layouts.dateRange(content);
        return '\n' + [left, dates].filter(Boolean).join('   ');
      }
      if (node.type === 'education_entry') {
        return '- ' + [content.credential, content.status].filter(Boolean).join('   ');
      }
      const own = declared(content, spec);
      /* Every declared field, NOT `content.name` first. Preferring a known key and falling back
         only when it is absent looks safe and is not: `tech_project` has a `name` AND carries the
         stack it was built with and the dates it ran, so the shortcut printed the project's name
         and silently dropped the rest — out of the plain-text copy, which is the one an ATS reads
         and the one people paste into an application form. Measured 2026-09-10 over one node of
         every catalogue type. */
      /* `declared` is empty only for a type this build has never registered — an older backup, or
         a file from a newer build — so the last arm is the unregistered case, not dead code. */
      return '\n' + (own.length ? own.join('   ') : String(content.name || content.title || ''));
    },
    line: (content, node, spec) => {
      if (content.label || content.value) return [content.label, content.value].filter(Boolean).join(': ');
      const own = declared(content, spec);
      return own.length > 1 ? own[0] + ': ' + own.slice(1).join(', ') : (own[0] || '');
    },
    bullet: (content, node, spec) =>
      '- ' + String(content.text || declared(content, spec).join(' ')).replace(/\s*\n\s*/g, ' '),
  };

  function plainText(doc) {
    const out = [];
    Doc.walk(doc.root, node => {
      if (node === doc.root) return;
      const spec = Components.get(node.type);
      const visit = TEXT_VISITORS[spec ? spec.shape : 'text'];
      /* An unregistered type still has content; join its string fields rather than skip it. */
      const content = doc.content[node.id] || {};
      const line = visit ? visit(content, node, spec)
        : Object.values(content).filter(v => typeof v === 'string' && v).join(' ');
      if (line != null && String(line).trim()) out.push(String(line));
    });
    return out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
  }

  /* ---- files ---------------------------------------------------------------------------- */

  function standaloneHtml(doc, options) {
    const layout = Layouts.get(doc.layoutId);
    if (!layout) throw new Error('ResumeExport: unknown layout ' + doc.layoutId);
    return Layouts.renderStandalone(layout, doc, options || {});
  }

  /** Hand the browser a file. Kept in one place so every format revokes its URL — a builder
   *  that leaked one blob per export would hold whole documents in memory for the session. */
  function download(win, data, name, mime) {
    const doc = win.document;
    const blob = new win.Blob([data], { type: mime });
    const url = win.URL.createObjectURL(blob);
    const a = doc.createElement('a');
    a.href = url;
    a.download = name;
    a.style.display = 'none';
    doc.body.appendChild(a);
    a.click();
    /* Revoked on a turn of the event loop, not immediately: Firefox cancels an in-flight
       download when the URL is revoked in the same tick as the click. */
    win.setTimeout(() => { win.URL.revokeObjectURL(url); a.remove(); }, 0);
  }

  const asHtml = (win, doc) =>
    download(win, standaloneHtml(doc), filename(doc, 'html'), 'text/html;charset=utf-8');

  const asWord = (win, doc) =>
    /* The BOM matters: without it Word guesses the encoding and mangles anything non-ASCII —
       an accented name, a curly apostrophe, the en-dash in a date range. */
    download(win, '﻿' + standaloneHtml(doc, { wordMeta: true }),
      filename(doc, 'doc'), 'application/msword;charset=utf-8');

  const asText = (win, doc) =>
    download(win, plainText(doc), filename(doc, 'txt'), 'text/plain;charset=utf-8');

  const asJson = (win, doc) =>
    download(win, JSON.stringify(doc, null, 2), filename(doc, 'json'), 'application/json');

  /** Print — an off-screen iframe rather than a popup, because a popup is the one thing a
   *  blocker stops even on a click, and because printing the tab itself would drag the whole
   *  application chrome into the page box. */
  function print(win, doc) {
    const frame = win.document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
    win.document.body.appendChild(frame);
    const inner = frame.contentWindow;
    inner.document.open();
    inner.document.write(standaloneHtml(doc));
    inner.document.close();
    const go = () => {
      inner.focus();
      inner.print();
      /* Left in place for a beat: removing the frame while the print dialog is still reading
         it produces a blank sheet in Chrome. */
      win.setTimeout(() => frame.remove(), 1000);
    };
    if (inner.document.readyState === 'complete') go();
    else frame.onload = go;
  }

  /* Identifiers are woven into HTML attributes and into `querySelector('[data-node="…"]')`
     all over the editor, so a document that arrived from outside must not be able to choose them.
     This is the shape `newId()` produces; anything else is rewritten to something that is.

     Escaping at each sink is the other half of this and is done there too — but a value that can
     never be hostile is worth more than a promise that every present and future consumer of it
     remembered to escape. A quote in an id also makes `querySelector` throw, which this fixes at
     the same time. */
  const SAFE_ID = /^[\w-]{1,64}$/;

  /** Rewrite every identifier an imported document carries that is not the shape we issue,
   *  remapping consistently so the tree, the content map, the variants and the tailorings all
   *  still refer to each other. Returns how many had to be replaced. */
  function sanitiseIds(doc) {
    const remap = new Map();
    const safe = value => {
      const id = String(value);
      if (SAFE_ID.test(id)) return id;
      if (!remap.has(id)) remap.set(id, 'n' + Math.random().toString(36).slice(2, 10));
      return remap.get(id);
    };
    const rekey = (table, mapValue) => {
      const out = {};
      for (const key of Object.keys(table || {})) {
        out[safe(key)] = mapValue ? mapValue(table[key]) : table[key];
      }
      return out;
    };

    (function walk(node) {
      node.id = safe(node.id);
      /* An imported file's `children` can be anything, and `|| []` does not save it: an object is
         truthy and not iterable, a string iterates into characters. That reached the user as a
         raw "object is not iterable" alert from inside the render. Normalising here — the one
         place every node is already visited — is what makes the tree readable rather than the
         file refused. */
      node.children = Array.isArray(node.children)
        ? node.children.filter(child => child && typeof child === 'object')
        : [];
      for (const child of node.children) walk(child);
    })(doc.root);

    doc.content = rekey(doc.content);
    doc.variants = rekey(doc.variants, inner => rekey(inner));
    /* The document's own left-out list, alongside each Tailoring's below (ADR-0128). Missed, an
       imported backup keeps ids nothing in the tree answers to any more, so every block the
       résumé had switched off comes back on — silently, and only for a file that needed
       sanitising at all. */
    doc.hidden = (doc.hidden || []).map(safe);
    for (const tailoring of doc.tailorings || []) {
      tailoring.id = safe(tailoring.id);
      tailoring.picks = rekey(tailoring.picks, variantId => safe(variantId));
      tailoring.hidden = (tailoring.hidden || []).map(safe);
    }
    if (doc.activeTailoring) doc.activeTailoring = safe(doc.activeTailoring);
    return remap.size;
  }

  /** Read a .json export back. Returns the document or throws with a message meant for a person
   *  — an import that fails silently looks like a lost résumé. */
  function importJson(text) {
    let parsed;
    try { parsed = JSON.parse(text); } catch (err) { throw new Error('That file is not JSON.'); }
    if (!parsed || !parsed.root || !parsed.content) throw new Error('That JSON is not a HeadStart résumé.');
    if (!Layouts.get(parsed.layoutId)) {
      /* A document naming a layout this build does not have is still readable — every word is
         in `content`. Falling back beats refusing the file. */
      parsed.layoutId = Layouts.all()[0].id;
    }
    /* A fresh id, so importing a backup never overwrites the résumé you are looking at. */
    parsed.id = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    /* And a fresh account state. A backup taken from a synced résumé carries `sync: true` and
       a revision, and inheriting either would be an import silently switching on storage the
       Account never asked for — the one thing ADR-0124 decision 2 says must never happen. The
       revision would be wrong anyway: this is a new document, with nothing stored under it. */
    parsed.sync = false;
    parsed.rev = 0;
    if (!parsed.root || typeof parsed.root !== 'object') throw new Error('That JSON is not a HeadStart résumé.');
    sanitiseIds(parsed);
    return parsed;
  }

  root.ResumeExport = {
    plainText, standaloneHtml, filename, download,
    asHtml, asWord, asText, asJson, print, importJson, sanitiseIds, SAFE_ID,
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
