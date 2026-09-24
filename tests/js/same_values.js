/* Deep equality that is strict on values but not on realms, for the vm-hosted app.js harnesses.
 *
 * app.js runs in a vm context, so its arrays and objects carry that context's prototypes and
 * fail `deepStrictEqual` on prototype alone. The loose `deepEqual` these files used instead
 * treats `[40, 40]` and `[null, null]` as equal on Node 26, which let a wrong netting result pass
 * locally and fail on CI. `structuredClone` copies the value into this realm and keeps NaN,
 * Infinity and undefined, which a JSON round trip would turn into null or drop — exactly the
 * values a division-by-zero regression produces.
 */
const assert = require('node:assert');

module.exports = (actual, expected, message) =>
  assert.deepStrictEqual(structuredClone(actual), expected, message);
