/* Loads the résumé builder's browser scripts into a fresh vm context.
 *
 * Fresh per test, not `require`d: the component and layout registries refuse duplicate
 * registration, so a module-cached copy shared between tests would make the second test that
 * defines anything throw — and would let one test's registrations leak into another's
 * assertions about what is registered.
 */

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DIR = path.join(__dirname, '..', '..', 'src', 'headstart', 'ui', 'static', 'resume');

/** load(['resume_components', ...], extraGlobals) -> the context, with each module's global on it. */
function load(files, extra) {
  const ctx = Object.assign({ console, setTimeout, clearTimeout }, extra || {});
  vm.createContext(ctx);
  for (const name of files) {
    const file = path.join(DIR, name + '.js');
    vm.runInContext(fs.readFileSync(file, 'utf8'), ctx, { filename: file });
  }
  return ctx;
}

/** The three layers plus everything that reads them — the usual whole-system load. */
const ALL = [
  'resume_components', 'resume_document', 'resume_layouts',
  /* In the order base.html loads them, which IS the order the layout picker offers them: the
     registry keeps registration order and the <select> is built straight off it. Keep the two in
     step — a test that exercises a different order from the product is a test of nothing. */
  'resume_layout_headhunter', 'resume_layout_jakes', 'resume_layout_harvard',
  'resume_layout_twocolumn', 'resume_layout_sidebar', 'resume_layout_europass',
  'resume_layout_canvas',
  'resume_repository', 'resume_export', 'resume_decorators',
];

module.exports = { load, ALL, DIR };
