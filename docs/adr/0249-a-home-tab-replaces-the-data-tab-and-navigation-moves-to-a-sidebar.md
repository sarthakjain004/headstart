# ADR-0249: A Home tab replaces the Data tab, and navigation moves to a sidebar

**Status:** accepted, amended 2026-09-28 (the sidebar folds to its icons; the shell is fluid — both below) · **Date:** 2026-09-28 · **Supersedes:** [ADR-0113](0113-publish-the-indexs-own-limits-in-the-product.md) (the Data tab) · **Supersedes in part:** [ADR-0116](0116-a-quiet-palette-and-a-scanning-layout.md) (its top tab strip, on wide screens only) · **Issue:** #755 items 1, 3, 10 and 16

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
- On a 1280–1439px screen the content column is narrower than before by the sidebar's width
  unless the sidebar is folded (see the amendment), and a 1100–1279px window keeps the top strip.
- A step whose target another change renames is silently skipped, so the tour needs its selectors
  re-checked when the Search tab's markup changes (`tests/js/guided_tour.test.js`,
  `scripts/eval/ui_smoke.py`).
- The product video drops into Home's hero by naming its file in `home.html`
  (`intro_video`); until then the frame holds a sketch of the Search tab.

## Amendment (2026-09-28): the sidebar folds to its icons

The owner asked for the sidebar to be collapsible so more of the screen shows content, which the
UX research had also asked for ("collapsible but open by default").

**A button at the top of the sidebar folds it to a 64px column of icons and back.** ("Rail"
is not used for it: that word already names Search's filter rail.) Icons stay; each entry's
name stays in the link as its accessible name and shows as a tooltip on hover or keyboard focus;
the current tab keeps its marker. The fold is on `<html data-nav="collapsed">`, set by a
one-line script in `<head>` before the stylesheet from `localStorage` (`hs.navCollapsed`, in
try/catch; unfolded by default), so a reload opens at the right width with no sideways jump. The
width change animates, and not at all under reduced motion (the global rule). Every icon and
the button sit at the same x in both states — centred in the folded width — so only the
column's edge moves; a label is clipped while the column unfolds instead of spilling over the
content.

**The shell keeps its unfolded ceiling when folded**, so folding hands its whole saving to the
content column instead of shrinking the page. (Superseded by the fluid shell below, which has
no ceiling at all.) Measured on Search (fixture data, Chromium): the
results column goes from 631px to 743px at 1280 and from 782px to 894px at 1440 (+112px both).

**The sidebar, folded or not, still starts at 1280px.** A folded sidebar below that always
costs Search 88px against the top strip those windows get today — results 662 → 574px at 1100, 737 → 649 at
1180, 830 → 742 at 1279 (measured by forcing the folded layout). At 1100 that is under the 631px
this ADR already treats as Search's floor, and at every width the strip leaves the content wider
— which is the point of folding. A folded-only band between 1180 and 1279 (649px, above the
floor) would add a third layout that gives the content less width than the strip it replaces,
for a row's worth of vertical space, so it was not built.

**The tour's first step** points at the navigation in either state and names the fold button.

## Amendment (2026-09-28): the shell fills the window

The owner, looking at Home at 1351px with the sidebar folded: "too much empty space on left and
right, make it adaptive to the user screen size". The shell was a centred box capped at ADR-0116's
1150px measure (plus the sidebar), so the background either side grew with the window: 67px each
side at 1351, 256 at 2560, 696 at 3440 (measured, fixture data, Chromium).

**The shell has no maximum width.** It spans the viewport less a fluid gutter,
`clamp(12px, 1.5vw, 32px)` (19px at 1280, 20 at 1351, 32 from 2134 up); the sidebar sits at that
gutter on the left and the content column takes the rest. Readability is held by capping prose,
not layout: paragraphs keep their own ~70ch measures (Home's notes move from 78ch to 70ch), and
grids, result lists, the filter column, the Trends chart and tables take the width. Home's feature
cards use `auto-fit`, so a wide screen puts more of them on one row instead of leaving empty
tracks.

**Two things are still capped, deliberately.** Home's hero (text beside the video frame) stops at
1720px: uncapped at 3440 the frame grew 890px tall with the text 1,600px away from it. The
sign-in page keeps a cap, raised from 1100px to 1440px with a fluid gutter: it is one argument in
prose, not an app, and its point list gains a 72ch measure.

**The phone layout is unchanged** — at 560px and below the shell is still 94vw, centred, with its
14px padding (390px: 26px each side, 339px of content, before and after). Between 561px and the
sidebar's 1280px the page now uses the fluid gutter too.

Measured before → after (sidebar open / folded, Search's results column): 1280 631 → 722 / 743 →
834; 1351 698 → 791 / 810 → 903; 1920 1194 → 1342 / 1306 → 1454; 3440 1528 → 2856 / 1640 → 2968.
Two result columns still start at 1720px, where the column is now ~1150px (~570px a card). The
Trends chart re-lays out at every width through the plot's ResizeObserver (#792).
