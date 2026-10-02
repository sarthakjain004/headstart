# ADR-0379: Navigation follows the seeker's journey, and a skin does not own layout

**Status:** accepted · **Date:** 2026-10-02 · **Amends:** ADR-0249 (navigation/tour), ADR-0247
(compact filters) and ADR-0128 (compact résumé preview affordance).

The owner requested the whole site's tabs, buttons, shapes, motion and transitions be designed
as a visitor journey, with a future reskin preserving placement. Home-only changes did not
satisfy that request. Keep Flask/Jinja and browser scripts, organise destinations by discovery
and retained work, and move navigation/focus/history into one module with `current`, `navigate`
and `start`. Page entry still owns each screen's real data freshness policy. One destination
registry renders the desktop sidebar and compact combination navigation; existing hashes,
capability gates and `hs.navCollapsed` remain stable.

Compact navigation exposes Home, Search, Saved jobs and Résumé, with More for the remaining
destinations. This reopens ADR-0249's rejection of a bottom bar: the grouped experience has
four visible destinations plus a labelled disclosure, rather than eight equal tabs in a
sideways strip. Reserve its viewport/safe-area space and test the last controls and keyboard
focus. Bottom rather than top, the exact visible destinations, and the promoted filter
priorities remain HeadStart hypotheses. The [research report](../product/2026-10-02_website-journey-design-research.md)
distinguishes source evidence from those choices and unmeasured marketing effects.

Country, experience and remote are directly accessible on compact Search; optional filters
live in a disclosure, with active conditions still visible above results. Move the actual
fields on resize instead of cloning state. Tours start only on request. Saved-search management
operates on the selected search, rename/delete have a named native dialog with cancellation,
Profile's manual path comes first, compact résumé Edit keeps writing checks in a disclosure,
and compact Preview exposes template change above the sheet. Result cards no longer stagger or animate similarity into place.

Physically separate app/editor geometry, skin and motion, expanding mixed border shorthands
before extraction so property ownership is disjoint. Layout owns fonts, spacing, dimensions,
structural transforms, visibility and responsive order. Skin owns colour, surface, radius,
shadow and decorative appearance. The template loads those files in parallel through
`url_for`, preserving boot-version cache URLs; compatibility CSS entrypoints remain. Résumé
document/export styles still own physical paper and printed content. Browser tests remove all
application/editor skins and compare visible geometry on every screen in both themes.

Rejected alternatives: a new rendering framework or configurable layout language would add
interfaces without improving this existing site's journey; another override stylesheet would
leave ownership mixed and make future skins depend on the old cascade. Replacing the actual
filter controls would duplicate state and event behavior. Authentication policy remains with
the separately developed access change.
