# Trends page redesign — design research (primary sources)

Research for the in-place redesign of the Trends page chart (`src/headstart/ui/templates/trends.html`,
drawn by `drawTrends()` in `src/headstart/ui/static/app.js`, styled in `src/headstart/ui/static/style.css`).
No code changed by this doc. Constraint carried through every recommendation below: vanilla
JavaScript / inline SVG / CSS, no framework, no build step — libraries below (Observable Plot,
ECharts, Chart.js, amCharts) are cited for their **documented mechanisms and reasoning**, not as
dependencies to add.

Current implementation, for reference against the recommendations in §6:
- Chart: hand-built SVG, `W=720 H=260`, `PAD_L=44`, up to `CHART_MAX=8` lines drawn, 5 horizontal
  gridlines (`g=0..4`), 3 x-axis labels (start/middle/end), `TREND_COLOURS` is 8 hardcoded hex values.
- Legend: up to `LEGEND_MAX=12` rows (`.trends-legend`), rows past `CHART_MAX` shown dimmed
  (`opacity:.45`) and non-interactive; hovering a charted row only changes its own background
  (`.charted .row:hover{ background:var(--rule); }`) — it does not touch the chart.
- No tooltip exists on the chart itself (no per-point hover, no crosshair).
- `.axis-label{ fill:var(--ink-3); font-size:10px; }` — no `font-variant-numeric:tabular-nums`.
- Segmented controls (`#trends-metric`, `#trends-unit`, `#trends-split`): `<span role="group">`
  wrapping `<button aria-pressed="true|false">` — mutually exclusive, single-select, applies
  immediately (no save step).

---

## 1. Chart type selection

**Financial Times Visual Vocabulary** (Financial Times Visual Journalism team, canonical source:
[`Financial-Times/chart-doctor`, `visual-vocabulary/README.md`](https://github.com/Financial-Times/chart-doctor/tree/main/visual-vocabulary)),
under the "Change over time" category:
- Purpose of the category: *"Give emphasis to changing trends. These can be short (intra-day)
  movements or extended series traversing decades or centuries. Choosing the correct time period
  is important to provide suitable context for the reader."*
- Line chart: *"The standard way to show a changing time series. If data are irregular, consider
  markers to represent data points."*
- Column chart: *"Columns work well for showing change over time – but usually best with only one
  series of data at a time."* — i.e. FT itself steers multi-series time data away from columns.
- Area chart: *"Use with care – these are good at showing changes to total, but seeing change in
  components can be very difficult."* — this is FT's own caution against stacked-area/streamgraph
  for a multi-category story; the total is legible, the individual series are not.
- Slope chart: *"Good for showing changing data as long as the data can be simplified into 2 or 3
  points without missing a key part of story"* — ruled out here; Trends has many more than 3
  points per series (one per measurement run).

**Datawrapper Academy, "What to consider when creating line charts"**
([datawrapper.de/academy/what-to-consider-when-creating-line-charts](https://www.datawrapper.de/academy/what-to-consider-when-creating-line-charts)):
- *"Line symbols can even be visually overwhelming if you have lots of lines or dates."*
- Direct labeling over a legend is preferred where it fits: *"Consider turning off automatic
  labeling and place the labels yourself... Try to use the same colors for the text that you use
  for the lines."* But *"these automatic labels will turn into a color key above the chart once
  the chart width is too narrow to show them next to the lines"* — i.e. Datawrapper's own fallback
  for "too many lines to label directly" **is** a legend, not a different chart type.
- Axis: *"Consider extending your vertical axis to zero"* and *"consider extending your vertical
  axis to 100% if you want to show shares and your values come close to 100%."*
- Emphasis: *"Use colors, line width and line dashes to make your most important values stick
  out"*; *"Grey is a great color to separate what's important in your chart from what's not
  important."*

**Datawrapper Blog, "What to consider when creating small multiple line charts"**
([datawrapper.de/blog/what-to-consider-when-creating-small-multiple-line-charts](https://www.datawrapper.de/blog/what-to-consider-when-creating-small-multiple-line-charts)):
- When small multiples win: *"Use small multiple line charts to untangle overlapping lines"*
  (works even with only a few lines if they overlap heavily); *"to let readers see the trends in
  each individual category"*; to compare series of very different magnitude, since each panel can
  carry its own y-axis; *"to tell a story"* via the panel sequence.
- When a single chart wins instead: *"Use normal line charts if you want readers to compare lines
  with each other at specific points in time"* — small multiples actively work against
  point-in-time cross-series comparison, which is exactly what a legend/tooltip-driven "who's
  bigger right now" reading needs.
- No hard numeric panel cap is given, but: *"Don't give your readers dozens of panels"*, and the
  practical test offered is mobile scroll length: *"if it takes long to scroll through all the
  panels? Then you might still have too many."*

**Observable Plot documentation** ([Line mark](https://observablehq.com/plot/marks/line),
Mike Bostock / Observable, Inc.):
- Multiple series: *"To draw multiple lines, use the **z** channel to group tidy data into
  series"*; when a `stroke`/`fill` channel is given, *"the z option defaults to the same,
  automatically grouping series."*
- Draw order matters for legibility: *"When using z, lines are drawn in input order"*, and *"the
  series are drawn in input order such that the last series is drawn on top."* Plot's own example
  uses the [sort transform](https://observablehq.com/plot/transforms/sort) to reorder so an
  emphasized (red) series draws over de-emphasized (gray) ones — *"places the red lines on top of
  the gray ones to improve readability."* This is a directly reusable technique for a hand-rolled
  SVG: re-append the hovered `<path>` element last (SVG paints in document order) so it draws over
  its dimmed siblings.

### Verdict for 5-10 role-family series

A single multi-line chart is the right primary form here, not small multiples and not a
stacked/streamgraph: FT explicitly cautions area/stacked forms lose per-component legibility (the
exact failure mode Trends needs to avoid — the whole point is comparing *individual* role-family
trajectories); Datawrapper's small-multiples case for-and-against is a wash at N≈8 (its "untangle
overlap" motivation still applies, but its "no point-in-time comparison" cost is a direct loss for
a page whose header controls exist to compare categories against each other by measure/unit), and
Trends already has a "By role" drill (`#trends-split`) that plays the small-multiples-style role of
narrowing focus, reached by clicking a category rather than by permanently subdividing the canvas.
Datawrapper's own overwhelm signal — "lots of lines" — is already bounded by `CHART_MAX=8`, in the
range this research covers (5-10).

---

## 2. Color systems for categorical time series

**ColorBrewer** ([colorbrewer2.org](https://colorbrewer2.org/); Cynthia Brewer, Mark Harrower, The
Pennsylvania State University — the palettes' own designers, per the page's own attribution line
"© Cynthia Brewer, Mark Harrower and The Pennsylvania State University"):
- The tool exposes three families (sequential, diverging, qualitative) and a "Number of data
  classes: 3 4 5 6 7 8 9 10 11 12" control, plus a "colorblind safe" filter checkbox — i.e.
  colorblind-safety is evaluated **per scheme, per class count**, not as a blanket property of a
  named palette.
- ColorBrewer's qualitative `Dark2` and `Set2` schemes both cap at 8 classes (confirmed directly
  against ColorBrewer's own specification data,
  [`axismaps/colorbrewer/export/colorbrewer.json`](https://raw.githubusercontent.com/axismaps/colorbrewer/master/export/colorbrewer.json),
  which lists entries for `"3"` through `"8"` for both). This makes either a plausible fit for
  `CHART_MAX=8`.
- **Caveat worth carrying into implementation, not glossing over:** multiple secondary
  technical sources that discuss ColorBrewer's own per-*N* flags (e.g. r-statistics.co, Datanovia)
  describe colorblind-safety narrowing sharply as class count rises — one is explicit that among
  Dark2/Paired/Set2, only `Paired` stays flagged colorblind-safe once you're at 4 classes, let
  alone 8. The raw export JSON pulled above does not carry the `"blind"` metadata field to confirm
  this directly per-scheme-per-N, so **before locking a palette, check the live colorblind-safe
  filter at colorbrewer2.org with N=8 selected** rather than trusting "Dark2/Set2 are the
  colorblind-safe qualitative ones" as a fact independent of class count.

**Datawrapper, "Your friendly guide to colors in data visualisation"**
([datawrapper.de/blog/colorguide](https://www.datawrapper.de/blog/colorguide)):
- Categorical colors should be *"distinctive... [they] should scream: 'I'm by myself and have
  nothing to do with all these other colors here!'"*
- On de-emphasis, citing dataviz practitioner Andy Kirk: *"grey is the most important color in
  data vis"* — used to push everything not currently in focus into the background so the
  highlighted element reads immediately.
- Recommends validating a palette with Viz Palette and colorblind simulators (Coblis, the Spectrum
  browser extension) rather than assuming a named palette is safe by reputation.

**Datawrapper, "How to pick more beautiful colors for your data visualizations"**
([datawrapper.de/blog/beautifulcolors](https://www.datawrapper.de/blog/beautifulcolors)):
- Constrain hue range rather than spreading around the wheel: *"There's no need to rely on hues
  from all around the color wheel"* — stay in *"a small area of the color wheel"* using
  *"complementary colors and their neighbors."*
- *"Combine colors with different lightness"* so the set survives grayscale/low-vision conversion
  without adjacent colors collapsing into the same tone.
- *"Try to desaturate bright colors. Put more saturation in dark colors"* so all swatches stay
  equally attention-grabbing rather than some colors visually dominating.
- *"Yellow/orange/red and blue"* is called out as *"especially loved by data visualization
  designers"* and explicitly *"accessible: colorblind people can easily distinguish blue and
  orange/red from each other."*

### Highlight-on-hover / gray-out-the-rest — three named, primary-source mechanisms

**Apache ECharts**, its own handbook
([echarts-handbook, "ECharts 5 Features"](https://apache.github.io/echarts-handbook/en/basics/release-note/v5-feature/)):
prior to v5 there were only two states — *"In ECharts 4, there were two interactive states,
`emphasis` and `normal`, graph will enter the `emphasis` state when the mouse hovered to
distinguish the data."* v5 added a third: *"we have added a new effect of **blur** other
non-related elements to the original mouse hover highlighting, so that the target data can be
focused."* Concretely: *"when the mouse hovers over a series, other non-related series will fade
out, thus highlighting more clearly the comparison of data in the focused series."* This is the
canonical documented shape of the pattern: hovered series → emphasized; every other series → a
distinct, lower-opacity "blur" visual state; on hover-out, both revert to "normal."

**Chart.js**, its own docs sample
([chartjs.org, "Legend — Events"](https://www.chartjs.org/docs/latest/samples/legend/events.html)):
wires `plugins.legend.onHover` / `onLeave`. The hover handler appends an alpha suffix to each
dataset's color to dim it, keeping only the hovered dataset opaque:
`colors[index] = index === item.index || color.length === 9 ? color : color + '4D';` (`4D` hex ≈
30% opacity) and `onLeave` strips the suffix to restore full opacity. This is a first-party,
literal, copyable mechanism: legend-item hover → full opacity on the matching series, ~30% opacity
on the rest → restored on leave.

**amCharts**, its own demo gallery
([amcharts.com, "Highlighting Line Chart Series on Legend Hover"](https://www.amcharts.com/demos/highlighting-line-chart-series-on-legend-hover/)):
listens for `pointerover` on the legend and *"highlight[s] our selected series by increasing its
stroke width, then dim[s] all the other series. On `pointerout` we restore the view."* — a second
concrete variant of the same shape, additionally using stroke-width (not just opacity) to carry
the "this one" signal.

All three converge on the same three-part mechanism, independent of library: (1) a hover source
(legend item, in Chart.js and amCharts; hovering the mark itself, in ECharts), (2) a two-class
visual split — one emphasized state (full opacity, and optionally thicker stroke) and one
de-emphasized "blur" state applied to every other series (reduced opacity), not a binary
show/hide, and (3) full restoration on hover-out. None of this requires a framework — it is a
`mouseenter`/`mouseleave` listener toggling an opacity/stroke-width attribute or class on SVG
elements, which the current hand-rolled `drawTrends()` can do directly.

---

## 3. Typography, gridlines, axis, and tooltip design

**Tufte's data-ink ratio**, as originally stated (*The Visual Display of Quantitative
Information*, 1983) — corroborated by multiple secondary summaries since the original book text
itself isn't web-hosted verbatim, and Tufte's own site
([edwardtufte.com/notebook/chartjunk](https://www.edwardtufte.com/notebook/chartjunk/)) confirms
the book pages (106-121) as the source without reproducing the passage:
- Tufte's own maxim, quoted consistently across summaries of the book: *"Above all else show data.
  Maximize the data-ink ratio. Erase non-data-ink. Erase non-data-ink, within reason."*
- Data-ink is defined as *"the non-erasable core of a graphic, the non-redundant ink arranged in
  response to variation in the numbers represented"*; chartjunk is *"ink that does not tell the
  viewer anything new... it is all non-data-ink or redundant data-ink."*

**Datawrapper, "Our new axis ticks make your charts easier to read"**
([datawrapper.de/blog/new-axis-ticks](https://www.datawrapper.de/blog/new-axis-ticks)) —
a useful counterweight to a pure minimize-everything reading of Tufte: *"Grids and axes turn a
blank page into a data space and provide the context we need to navigate it. We consider grids to
be one of the most crucial features of a well-readable data visualization."* Datawrapper's own
practice is not "erase gridlines," it's "make the *necessary* grid legible" (their redesign in that
post addressed multi-line date formatting, truncating rather than hiding long labels, and
consistent grid behavior across chart types) — i.e. Tufte's ratio argues against *decorative* ink,
not against the axis/gridline structure itself, and Datawrapper's practice reflects that reading.

**Datawrapper, "Which fonts to use for your charts and tables"**
([datawrapper.de/blog/fonts-for-data-visualization](https://www.datawrapper.de/blog/fonts-for-data-visualization)):
- *"Sans-serif ('without serifs') typefaces are most often the better choice. They look cleaner and
  are often easier to skim than serif fonts."*
- Numbers: use fonts with *"lining and tabular"* figures — *"tabular figures maintain consistent
  width across all numbers, making tables easier to scan."*
- Weight: *"Use bold text only for titles or to emphasize a few words in annotations."*
- Size floor: *"Everything below 12px will likely be too small"* at standard resolution.
- Case: uppercase is *"harder to read"* than sentence case — reserve it for short labels only
  (filter buttons, category headers).

**Datawrapper Academy, line-chart guidance** (same source as §1) supplies the concrete axis rules
already quoted above: extend the y-axis to zero when values approach it; extend to 100% for a
share/percentage unit when values come close to 100%; use color + line-width + dash — not just
color — to carry emphasis, so the encoding survives grayscale/colorblind viewing.

**Tooltip design specifically:** this is the one topic where primary-source, tooltip-specific
guidance was thin. Datawrapper's own tooltip documentation
([academy.datawrapper.de, "How to create tooltips"](https://academy.datawrapper.de/article/116-how-to-create-useful-tooltips-for-your-maps),
["How to customize tooltips"](https://academy.datawrapper.de/article/237-i-want-to-change-how-my-data-appears-in-tooltips))
is largely mechanical (how to author tooltip HTML/fields in their tool), not a stated design
rationale for tooltip *content or layout*. No FT- or Tufte-level primary source on tooltip
specifics turned up either. The tooltip rules in §6 below are therefore extrapolated from the
adjacent, better-sourced guidance above (data-ink minimalism, tabular numerals, the 12px floor,
color-plus-shape redundancy) rather than a single named tooltip-specific source — flagged here so
that gap is explicit rather than silently papered over with an invented citation.

---

## 4. Accessible segmented-control interaction patterns, and avoiding dead clicks

**WAI-ARIA Authoring Practices Guide (APG)**, W3C Web Accessibility Initiative — the authoritative
spec-adjacent source, evaluated against the current markup (`role="group"` + `aria-pressed`
buttons):

**[Tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/):** *"Tabs are a set of layered
sections of content, known as tab panels, that display one panel of content at a time."* Roles:
`tablist` on the container, `tab` on each control, `tabpanel` on each content region. This doesn't
fit: Trends' segmented controls don't show/hide separate content panels — they change what's
plotted in one persistent chart. Tabs is the wrong pattern here.

**[Radio Group pattern](https://www.w3.org/WAI/ARIA/apg/patterns/radio/):** *"A radio group is a
set of checkable buttons, known as radio buttons, where no more than one of the buttons can be
checked at a time."* Roles: `radiogroup` on the container, `radio` on each button, with
`aria-checked` reflecting state, arrow-key roving-tabindex navigation (Tab/Shift+Tab moves in and
out of the whole group as one stop; arrow keys move — and auto-check — within it).

**[Toolbar pattern](https://www.w3.org/WAI/ARIA/apg/patterns/toolbar/):** *"A toolbar is a
container for grouping a set of controls, such as buttons, menubuttons, or checkboxes"* — a
grouping/focus-management mechanism, recommended *"only if the group contains 3 or more
controls,"* with the same roving-tabindex/arrow-key shape as radiogroup but no notion of mutual
exclusivity or checked state by itself.

**The APG's own worked example resolves the exact case at hand.** The
[Toolbar Example](https://www.w3.org/WAI/ARIA/apg/patterns/toolbar/examples/toolbar/) includes a
text-alignment button trio (left/center/right) that looks like a row of toggle buttons but behaves
as mutually exclusive — precisely Trends' Measure/Unit/Drill controls. The APG's own reasoning for
that example: *"Because ARIA is designed to inform assistive technologies about UI semantics and
behaviors, not styling,"* the alignment control follows the **Radio Group Pattern**, not
independent `aria-pressed` toggle buttons — despite looking visually like toggle buttons, and
despite applying its effect immediately (no save step). That last point matters: it directly rules
out a plausible objection to radiogroup.

**A named counterpoint exists and is worth recording honestly.** GitHub's Primer design system
explicitly rejects radiogroup for its own `SegmentedControl` component
([primer.style/product/components/segmented-control/accessibility](https://primer.style/product/components/segmented-control/accessibility/)):
*"`radiogroup` requires a save button to apply changes"* — Primer instead renders a plain
`<ul>`/`<li>`/button list with `aria-current` marking the active segment. Weighing the two: Primer's
stated reason ("radiogroup requires a save button") is a UX-convention generalization about how
*some* radiogroups are used in forms, not a requirement of the ARIA role itself — and the APG's
own toolbar example is the direct counter-proof, an officially-documented radiogroup whose
selection also applies immediately. Given the task explicitly asks to check this against the APG,
and the APG's own worked example matches Trends' shape exactly (visually a toggle-button row,
semantically mutually exclusive, immediate effect), **radiogroup is the pattern to adopt** — see
the verdict in §6. Primer's alternative (`aria-current` on a button list) is noted as a legitimate,
shipped, differently-reasoned choice in case the roving-tabindex/arrow-key requirement of
radiogroup turns out to be awkward to retrofit.

**Dead clicks / perceived responsiveness — Nielsen Norman Group**, "Visibility of System Status"
([nngroup.com/articles/visibility-system-status](https://www.nngroup.com/articles/visibility-system-status/)),
Jakob Nielsen's original heuristic #1 of his 10 usability heuristics, as NN/g's own site states it
today: *"How well the state of the system is conveyed to its users. Ideally, systems should always
keep users informed about what is going on, through appropriate feedback within reasonable
time."* NN/g frames the cost directly in terms of repeated, unresolved clicks: *"Appropriate
feedback for a user action is perhaps the most basic guideline of user-interface design,"* and
without visible confirmation, *"users cannot distinguish between successful interaction and system
failure"* — the mechanism by which an element that looks interactive but produces no visible
change erodes trust and invites the user to click it again. This is squarely relevant to Trends'
legend rows: rows past `CHART_MAX` are currently shown un-interactive (correctly, per the code
comment at `app.js:770-773`) but the *chart itself* currently gives no feedback at all for hovering
a charted legend row (no highlight reaches the SVG) — exactly the "looks interactive, does
nothing visible" gap this heuristic warns against.

---

## 5. Responsive / small-screen chart design

**Datawrapper, "Datawrapper becomes a mobile-first charting tool"**
([datawrapper.de/blog/datawrapper-becomes-mobile-first-charting-tool](https://www.datawrapper.de/blog/datawrapper-becomes-mobile-first-charting-tool/)):
- The concrete adaptation for line charts specifically: *"line charts switch from direct labeling
  to legend-based labeling"* on narrow screens — i.e. the responsive strategy for a multi-line
  chart is not a different chart type, it's collapsing in-chart labels into an external legend
  (which Trends already has as its primary labeling mechanism, per §1's Academy citation about
  automatic labels "turning into a color key").
  - Other cited adaptations: secondary chrome (sharing buttons) switches to a vertical layout to
    save width, and embed height is computed automatically so there's no dead scroll space below
    the chart.

**Datawrapper Blog, small multiples** (same source as §1) supplies the one concrete mobile-specific
test found across this research: *"if it takes long to scroll through all the panels? Then you
might still have too many"* — a scroll-length heuristic for any small-multiples or drill view,
directly applicable to Trends' "By role" drill if it is ever presented as a grid rather than a
single re-plotted chart.

No FT-specific primary source on responsive chart mechanics (beyond the general Visual Vocabulary
guidance already cited in §1) turned up in this pass — Datawrapper is the strongest, most concrete
primary source on this specific sub-topic.

---

## 6. Concrete recommendations for this redesign

**Chart type — keep the multi-line chart.** Do not switch to stacked area, streamgraph, or a
permanent small-multiples grid. FT's own caution against area charts for multi-component stories,
and Datawrapper's small-multiples trade-off (loses point-in-time cross-category comparison, which
this page's controls exist to support), both argue for staying with one chart. `CHART_MAX=8` is
already inside the well-supported 5-10 range from both FT and Datawrapper's guidance — keep it, or
if lowered, keep it aligned with a colorblind-safe palette size (see below), not raised past what
that palette can carry.

**Color — a named strategy, not new hex values by fiat.** Adopt a ColorBrewer qualitative scheme
sized to `CHART_MAX` (Set2 or Dark2, N=8, both natively cap at 8 per ColorBrewer's own spec data),
but **verify colorblind-safety directly against colorbrewer2.org's "colorblind safe" filter at
N=8 before locking hex values** — per §2's caveat, safety is flagged per class-count, not as a
blanket property of the palette name, and secondary sources suggest it degrades well before 8
classes for most qualitative schemes. If the N=8 flagged-safe set turns out too constrained,
Datawrapper's manual-construction fallback (§2: stay in one hue neighborhood + complements, vary
lightness so grayscale doesn't collapse adjacent swatches, desaturate the brights / saturate the
darks) is a documented, primary-sourced way to hand-build 8 safe categorical colors instead of
"steal a palette and hope."

**De-emphasis color: keep using grey, formally.** The existing `var(--ink-3)` used for off-chart
legend rows (`opacity:.45`) already matches the "grey is the most important color in data vis"
principle (§2, Datawrapper/Andy Kirk) — extend that same token to the hover-dim state below rather
than introducing a second de-emphasis mechanism.

**Hover-highlight interaction — concrete spec**, synthesized from the three converging library
mechanisms in §2 (ECharts' emphasis/blur, Chart.js's alpha-dim, amCharts' stroke-width) and
implementable in the existing hand-rolled SVG with no library:
1. On `mouseenter`/`focus` of a charted legend row (`.charted .row`), read its `data-name`, find
   the matching `<path>` in `#trends-chart`.
2. Set every *other* series' `<path>` and end-point `<circle>` to a reduced-opacity state (e.g.
   `opacity: .25`, matching the existing dimmed-legend-row treatment) — not `display:none`;
   Chart.js and ECharts both dim rather than hide, preserving shape/position context.
3. Bring the hovered series' `<path>` to full opacity and (optionally, per amCharts) a slightly
   thicker `stroke-width`; re-append it as the SVG's last child in its `<g>` so it paints on top of
   the dimmed lines (the Observable Plot sort-transform technique from §1, achievable in plain DOM
   with `appendChild` re-insertion).
4. On `mouseleave`/`blur`, remove all inline overrides to restore the normal per-series colors from
   `TREND_COLOURS`. Keep the transition short (~120-150ms) so the state change reads as instant
   feedback (NN/g §4) without feeling laggy.
5. Wire the same handler to keyboard focus, not just mouse hover — the legend rows are already
   `tabindex="0"` (`app.js:786`), so this is required, not optional, to keep the interaction
   accessible.

This directly closes the NN/g "dead click" gap identified in §4: right now a charted legend row is
interactive-looking (hover background change, cursor:pointer) but produces zero visible change in
the chart itself until you commit to a full click-through drill.

**Gridline / axis rules:**
- Keep gridlines sparse and horizontal-only, consistent with both Datawrapper's "grids are crucial
  but shouldn't be decorative" framing and Tufte's data-ink minimalism — the current 5-line grid
  is already in that spirit; don't add vertical gridlines per gridpoint, which would push toward
  chartjunk with no reading benefit given the sparse 3-label x-axis already anchors time.
- Already correct: `lo = 0` always extends the y-axis to zero, matching Datawrapper's rule.
- **Gap found:** the `hi` calculation (`app.js:734`) never extends to 100 for the `share` unit —
  Datawrapper's rule is to extend to 100% specifically when values come close to it. Add that: when
  `trendUnit === 'share'` and `hi > ~80`, round `hi` up to 100.
- Use color + stroke-width + (optionally) dash together for the hovered/emphasized series, not
  color alone — carries the encoding through grayscale/colorblind viewing per Datawrapper's line-
  chart guidance.

**Typography — two concrete, code-level gaps found, both worth fixing:**
- `axis-label` is `font-size:10px` (`style.css:367`) — below Datawrapper's stated 12px legibility
  floor, and the SVG's `preserveAspectRatio="none"` + `width:100%` scaling means the *effective*
  rendered size on a narrow container can be smaller still. Raise it to at least 12px (or make the
  SVG viewBox scale account for minimum legible px on narrow layouts).
- `axis-label` has no `font-variant-numeric:tabular-nums`, unlike other numeric UI in this same
  stylesheet (`.count`, `.toolbar .n`) — add it, matching Datawrapper's tabular-figures rule so
  axis numbers align visually as the y-axis relabels on hover/unit-toggle.
- Keep the existing sans-serif (`var(--sans)`) for chart text; reserve any serif/weight emphasis
  for the page heading only, not chart labels — consistent with Datawrapper's "bold only for titles"
  rule.

**ARIA verdict for the segmented controls — change `role="group"` to `role="radiogroup"`.**
Per §4: the APG's own toolbar example is a documented, exact match for Trends' Measure/Unit/Drill
controls (visually toggle-button-styled, semantically mutually exclusive, immediate effect, no
save step), and its resolution is radiogroup, not a plain button group. Concretely: change
`<span role="group">` to `<span role="radiogroup">`, change each `<button aria-pressed="...">` to
`role="radio" aria-checked="true|false"` (dropping `aria-pressed`, which per the APG button pattern
means something different — an independent two-state toggle, not a mutually-exclusive selection),
and add roving-tabindex + arrow-key handling (`tabindex="0"` on the checked/first button,
`tabindex="-1"` on the rest, Left/Right arrows move and auto-check, matching the exact keyboard
model the APG Radio Group pattern specifies). This is a real interaction-model change, not just an
attribute swap — flagging Primer's dissenting `aria-current`-on-a-button-list approach (§4) as the
fallback if the roving-tabindex retrofit proves disruptive to the existing click handlers in
`trendSeg()` (`app.js:890-896`).

**Responsive — mirror Datawrapper's mobile-first line-chart strategy, which Trends already has the
bones of.** Trends already labels via an external legend rather than in-chart direct labels, so
the "collapse direct labels into a legend on narrow screens" adaptation Datawrapper describes is
already the resting state here — no chart-type change needed for mobile. Concretely: verify (a)
the existing `.trends-grid` two-column layout (`grid-template-columns:1fr 232px`, `style.css:341`)
reflows to stacked on narrow viewports rather than squeezing the SVG below a legible width, and
(b) if the "By role" drill (`#trends-split`) is ever rendered as multiple panels rather than a
re-plotted single chart, apply Datawrapper's scroll-length test — if reaching the last panel takes
noticeable scrolling, that's the signal to cap panel count, not a fixed number chosen up front.
