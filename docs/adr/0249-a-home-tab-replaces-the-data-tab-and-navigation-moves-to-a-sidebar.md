# ADR-0249: A Home tab replaces the Data tab, and navigation moves to a sidebar

**Status:** accepted · **Date:** 2026-09-28 · **Supersedes:** [ADR-0113](0113-publish-the-indexs-own-limits-in-the-product.md) (the Data tab) · **Supersedes in part:** [ADR-0116](0116-a-quiet-palette-and-a-scanning-layout.md) (its top tab strip, on wide screens only) · **Issue:** #755 items 1, 3, 10 and 16

## Context

Issue #755 is the owner's list of what makes the signed-in app hard to trust and hard to use. Four
of its items change the app's shell rather than any one tab:

- **16.** The Data tab (ADR-0113) should go — "whats the point of this tab even?" — and a homepage
  should say what HeadStart is, with room for a product video.
- **1.** The tabs belong in a column on the left.
- **3.** A "Take a tour" feature.
- **10.** A real logo; the brand mark was a filled square with a dot.

The Data tab was written for a sceptical reader who wants every claim's mechanism: ADR links in
running prose, live field-coverage counts from `/coverage`, the worst measured eviction case. The
owner's reading is that a job seeker does not want any of that, and the ADR citations in
particular are the "stuff they don't care about" item 15 names.

## Decision

**Home is the first tab and the landing view.** The bare URL, `#home`, the retired `#data` and any
hash naming neither a panel nor an element inside one all land there (`DEFAULT_TAB` in `app.js`).
It says what the product is in plain words and puts a search box on its first screen — the
words run on the Search tab, so Home keeps no results of its own. It links each feature to its
tab (a card only for a tab the deployment renders), and keeps the Data tab's facts a visitor
needs, in plain words and with no ADR references: the index size and provider count, read from
the index at request time; the refresh cadence; English-only tech roles; what the match score
does and does not mean; that closed jobs drop out after two missed checks; and a link to the
privacy policy.

**The Data tab, `/coverage` and `JobSearch.coverage()` are removed.** Nothing else read them. The
three links into the tab now point at sections of Home (`#how-matching`, `#good-to-know`) or are
dropped, and the door no longer promises a Data tab inside.

**A hash can name an element inside a panel, not only a panel.** The router resolves `#results`
to the Search tab that holds it. This was needed, not extra: the skip link on Search is
`href="#results"`, and it had stayed on Search only because Search was the fallback. With Home as
the fallback it would have switched the reader to Home.

**Navigation is a sidebar at 1280px and wider, and the existing scrolling strip below that.** One
`<nav class="tabs">` list, laid out twice by CSS, so `app.js` reads the same links either way. The
shell's ceiling grows by the sidebar's width so the content column keeps its measure; the sidebar
is sticky, so a tab switch now starts the new tab at its top. A tab the deployment cannot serve is
left out of the list rather than shown as a disabled "soon" entry (Apple's tab-bar guidance, per
`docs/ux-research/2026-09-28_what-makes-users-stick.md`). The breakpoint is 1280px because Search
has its own column: ADR-0247 puts the filters beside the results from 1100px, and with the
sidebar too, a 1180px window left the results 525px wide (measured); at 1280px they get 631px,
about what ADR-0247 accepted at its own 1100px edge.

**The tour is a plain script with no library** (`static/guided_tour.js`). A step names its tab and
selectors tried in order; a target missing or hidden after a short wait is skipped, never pointed
at, so a restyle that renames something costs one step. The page behind the tour is `inert`;
Escape closes it; the first visit that lands on Home is offered it once (`localStorage`, and not at
all where storage is blocked).

**The logo is one SVG file** (`static/logo_mark.svg`): a start line with an arrow already leaving
it, in a fixed brand colour rather than a theme token so the same file is the favicon and reads on
both grounds (3.1:1 on the dark ground, 5.2:1 on the light one). The wall lets that one static path
through, so the door shows it signed out.

## Alternatives

- **Keep the Data tab and add Home beside it.** Rejected by the issue itself: the owner asked for
  the tab to go, and a second prose page would split "what is this" across two tabs.
- **Show the live field-coverage counts on Home** ("about 1 in 4 jobs states a salary"). They are
  the one thing on the Data tab that measured itself, and dropping them loses that. Kept out for
  now because Home's job is orientation, not audit; `git show` recovers the route and method whole.
- **A drawer or a bottom bar on phones.** The strip is already measured at 390px (it scrolls, with
  a faded edge), a bottom bar holds five entries and there are eight, and a drawer puts every
  destination one more tap away. The strip stays.
- **A tour library** (Shepherd, Intro.js). Rejected: this app has no build step and no dependency
  on a front-end package, and five steps do not justify the first.

## Consequences

- ADR-0113's commitment — every number counted live, every claim linked to its decision — no
  longer has a page in the product. The privacy disclosures it carried per deployment are now the
  privacy policy's, which Home and the footer link.
- On a 1280–1439px screen the content column is narrower than before by the sidebar's width,
  and a 1100–1279px window keeps the top strip.
  The sidebar cannot be collapsed yet; the UX research asks for "collapsible but open by
  default", which is left for a follow-up rather than built with an icon-only mode now.
- A step whose target another change renames is silently skipped, so the tour needs its selectors
  re-checked when the Search tab's markup changes (`tests/js/guided_tour.test.js`,
  `scripts/eval/ui_smoke.py`).
- The product video drops into Home's hero by naming its file in `home.html`
  (`intro_video`); until then the frame holds a sketch of the Search tab.
