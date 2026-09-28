# ADR-0247: Search filters are always open, beside the results on a wide screen

**Status:** accepted · **Date:** 2026-09-28 · **Amends:** [ADR-0116](0116-a-quiet-palette-and-a-scanning-layout.md) (its "filters above the results, collapsed by default" layout, and two of its "three behaviours": the per-row dismiss and the compact-density toggle) · **Issue:** #755 items 4, 5, 6, 7, 8, 12 and 14

## Context

ADR-0116 moved the filter rail into a full-width panel above the results, closed until the
Filters button opened it. The owner's feedback on it (#755): changing a filter took a click to open
the panel and another to shut it again, because open it pushed the results off the screen; and a
`<select>` with four or five options took two clicks where a row of options takes one. The sort
control was the same complaint about four options behind a click.

## Decision

**No Filters button.** The filters are always rendered, and what changes with the width is where
they sit:

- **At 1100px and wider, a 296px column beside the results.** The rows start at the top of the
  page as they did with the panel shut, and every filter is in view without a click. The column is
  not sticky: at a laptop height it is taller than the window, and a sticky column would need its
  own scroll region — the unmarked scroller ADR-0116 already removed from the panel once.
- **Below 1100px, one row that scrolls sideways.** Wrapped, the fields stacked 1,306px above the
  first job on a 390px phone (measured); in one row the bar is 94px, and the field cut off at the
  edge says there is more. The salary slider is hidden there — it is a convenience over the two
  figures, which stay — because it alone doubled the row's height.

**A caveat shows while its field has keyboard or typing focus, floating, not in the flow.** In the
flow, a caveat appearing and vanishing re-wrapped the bar, and a click on the next control landed
where that control had just moved from (measured: "Clear all filters" missed its click this way).
The float has `pointer-events:none`, so it never takes a click meant for what it covers. On a
narrow screen it docks to the foot of the viewport, because a scrolling row would clip it.

**A `<select>` of five options or fewer is drawn as a row of radios** — sort, keyword scope,
employment type and posted date. The `<select>` stays in the page, hidden, as the one value every
reader and writer uses (the filter object, the facet counts written onto its options, a Saved Set);
the radios are redrawn from it. Selects with more options (India, first seen, ATS, currency) stay
selects.

**Every pick searches at once.** Before, only the sort and keyword-scope selects did; a switch or
any other select waited for the Search button. Now a chip, a switch or a select searches on change,
and a typed field (years, location, company, salary bounds) searches on Enter.

**Three things the owner named go** (#755 items 5, 8, 14): the per-row dismiss (×) and its
"N hidden" note, the compact-density toggle — both ADR-0116 records as authorised by its critique
loop — and the "via {ATS}" provenance label, on result cards and on Hiring-now rows alike. A card with no salary leaves its pay cell empty rather than printing "—".
The trend link is an icon button, same target. The company-level "hide" (ADR-0171) is kept: it
removes a company, not a job, and is server-side state with its own undo.

## Alternatives

**Always open, above the results, wrapping.** The shape the request suggested. Measured at
1440x1000 it was 465px tall, putting the first job at y≈885 — below the fold on a laptop, which is
the "takes a lot of space" complaint unchanged.

**Keep the panel, but remember whether it was open.** One click fewer on a return visit, and the
space complaint untouched.

**A disclosure on phones only.** The conventional phone pattern; rejected because it is the
toggle the owner asked to remove, and the sideways row costs 94px without one.
