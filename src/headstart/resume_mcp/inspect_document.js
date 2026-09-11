/* The block-by-block reading of one Résumé document, produced by the REAL model (ADR-0136).
 *
 * This file exists so that nothing about a Résumé document is decided twice. The rules that
 * say what a block is made of (`resume_components.js`'s catalogue), which words one Tailoring
 * shows (`contentOf`), and which blocks that Tailoring prints (`resolve`) are all JavaScript,
 * and the MCP server that serves them is Python. Rather than translate them, the server runs
 * this script under `node` and reads the answer: one implementation of the rule, one place it
 * can be wrong.
 *
 * stdin  — the stored document, as JSON.
 * argv[2]— which version to read it as: `master`, or a Tailoring's id or name.
 * stdout — one JSON object, the shape `inspection.py` renders. `{"error": "..."}` and a
 *          non-zero exit for anything this cannot answer, so a failure is never an empty view.
 *
 * Loaded through `vm` in a fresh context the way `tests/js/resume_harness.js` does, for the
 * reason that file gives: the component registry refuses duplicate registration, so these are
 * browser scripts run once, not modules to require.
 */

'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DIR = path.join(__dirname, '..', 'ui', 'static', 'resume');

function load() {
  const ctx = { console };
  vm.createContext(ctx);
  /* The two layers this reading needs, and no more. The nine Layouts are not loaded: nothing
     here renders or checks anything, and `layoutId` is reported verbatim. */
  for (const name of ['resume_components', 'resume_document']) {
    const file = path.join(DIR, name + '.js');
    vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
  }
  return ctx;
}

/** Which Tailoring `selector` names — null for the master. Throws when it names none. */
function chooseView(doc, selector) {
  const tailorings = doc.tailorings || [];
  if (!selector || selector === 'master') return null;
  const wanted = String(selector);
  /* Case-insensitively by name, which subsumes matching it exactly — a separate exact-name
     clause was here and no mutation of it could be made to fail a test, because every input
     that reached it had already matched. */
  const hit =
    tailorings.find(t => t.id === wanted) ||
    tailorings.find(t => String(t.name || '').toLowerCase() === wanted.toLowerCase());
  if (!hit) {
    const known = tailorings.map(t => `${t.name} (${t.id})`).join(', ') || 'none';
    throw new Error(`no version called "${selector}" in this résumé — it has: ${known}`);
  }
  return hit;
}

function inspect(ctx, doc, selector) {
  const Components = ctx.ResumeComponents;
  const Doc = ctx.ResumeDocument;

  const tailoring = chooseView(doc, selector);
  const viewId = tailoring ? tailoring.id : null;

  /* What this version actually prints, from `resolve` itself rather than from a re-reading of
     the two hidden lists. `resolve` prunes recursively, so a block inside a switched-off
     section is absent here even though its own `isHidden` is false — a distinction the server
     would have got wrong on its own, and one that matters: it is the difference between "you
     turned this bullet off" and "the whole section it lives in is off". */
  const printed = new Set(Doc.flatten(Doc.resolve(doc, viewId)).map(n => n.id));

  /* How each Tailoring overrides each node, by name — the two ways it can: reword it, or
     leave it out. Both are computed against every Tailoring regardless of which version is
     being read, because "is this block tailored anywhere" is a fact about the document, not
     about the view. Only the rewording half was here at first, so reading the master told you
     a bullet had been rewritten for Stripe but not that another version drops it entirely —
     and finding that out meant re-reading every version one at a time. */
  const rewordedBy = {};
  const leftOutBy = {};
  for (const t of doc.tailorings || []) {
    for (const nodeId of Object.keys(t.picks || {})) {
      const variant = ((doc.variants || {})[nodeId] || {})[t.picks[nodeId]];
      if (!variant) continue;   // a pick whose variant is gone changes nothing — `resolve` skips it too
      (rewordedBy[nodeId] = rewordedBy[nodeId] || []).push(t.name || t.id);
    }
    for (const nodeId of t.hidden || []) {
      (leftOutBy[nodeId] = leftOutBy[nodeId] || []).push(t.name || t.id);
    }
  }

  const depths = new Map([[doc.root.id, 0]]);
  const blocks = [];
  const unknownTypes = new Set();

  Doc.walk(doc.root, (node, parent) => {
    if (!parent) return;   // the root is scaffolding, not a block of the résumé
    const depth = (depths.get(parent.id) || 0) + 1;
    depths.set(node.id, depth);

    const spec = Components.get(node.type);
    if (!spec) unknownTypes.add(node.type);

    const shown = Doc.contentOf(doc, node.id, viewId);
    const base = doc.content[node.id] || {};
    const declared = (spec ? spec.fields : []).map(f => {
      const field = {
        key: f.key,
        label: f.label,
        kind: f.kind,
        value: shown[f.key] === undefined ? null : shown[f.key],
      };
      /* Only when this version disagrees with the master, so the common case stays quiet and
         a difference is impossible to miss. */
      if (viewId && shown[f.key] !== base[f.key]) {
        field.master_value = base[f.key] === undefined ? null : base[f.key];
      }
      return field;
    });

    /* Content keys the Component Type does not declare. Usually empty; when it is not, it is
       either a field removed from a type after documents were written with it, or a document
       from a newer build — both things worth seeing rather than silently dropping, since this
       tool's whole job is reporting what is set. */
    const known = new Set(declared.map(f => f.key));
    const undeclared = {};
    for (const key of Object.keys(shown)) {
      if (!known.has(key)) undeclared[key] = shown[key];
    }

    blocks.push({
      id: node.id,
      type: node.type,
      label: spec ? spec.label : null,
      depth,
      slot: node.slot || null,
      geometry: Object.keys(node.geometry || {}).length ? node.geometry : null,
      prints: printed.has(node.id),
      /* Three facts, not one: switched off by the document, switched off by this version, or
         printing fine itself but inside something that is off. */
      hidden_on_master: (doc.hidden || []).includes(node.id),
      hidden_by_this_version: !!(tailoring && (tailoring.hidden || []).includes(node.id)),
      fields: declared,
      undeclared_fields: Object.keys(undeclared).length ? undeclared : null,
      reworded_by: rewordedBy[node.id] || [],
      left_out_by: leftOutBy[node.id] || [],
    });
  });

  return {
    id: doc.id,
    name: doc.name,
    layout_id: doc.layoutId,
    schema: doc.schema,
    updated_at: doc.updatedAt || null,
    rev: doc.rev || 0,
    theme: doc.theme && Object.keys(doc.theme).length ? doc.theme : null,
    viewing: tailoring ? { kind: 'version', id: tailoring.id, name: tailoring.name } : { kind: 'master' },
    tailorings: (doc.tailorings || []).map(t => ({
      id: t.id,
      name: t.name,
      job_id: t.jobId || null,
      reworded_blocks: Object.keys(t.picks || {}).length,
      hidden_blocks: (t.hidden || []).length,
    })),
    blocks,
    unknown_types: Array.from(unknownTypes),
  };
}

function main() {
  const raw = fs.readFileSync(0, 'utf8');
  const doc = JSON.parse(raw);
  if (!doc || typeof doc !== 'object' || !doc.root || !Array.isArray(doc.root.children)) {
    throw new Error('this record is not a Résumé document — it has no node tree');
  }
  /* A document with no `content` map is malformed rather than empty, but every read below
     would fault on it one field at a time. One default here keeps the failure legible. */
  if (!doc.content || typeof doc.content !== 'object') doc.content = {};
  process.stdout.write(JSON.stringify(inspect(load(), doc, process.argv[2])));
}

try {
  main();
} catch (err) {
  process.stdout.write(JSON.stringify({ error: String((err && err.message) || err) }));
  process.exit(1);
}
