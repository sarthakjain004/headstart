# ADR-0080: Trends chart redesign — validated palette, Other bucket, hover layer, radiogroup ARIA

**Status:** accepted · **Date:** 2026-08-20 · **Relates to:** ADR-0040/0051/0052 (the trends
ledger and watchlist this chart renders), ADR-0075 (the ATS picker, unchanged), ADR-0042
(sign-in wall the whole feature sits behind, unchanged)

## Context

Two independent complaints about the Trends page prompted this: the chart "doesn't look
appealing," and "some tabs... are locked sometimes or doesn't change anything." Investigation
found the second complaint had a precise root cause, not a vague UX gap: legend rows past
`CHART_MAX` (8) were listed with a real category name and a real percentage, styled only
`opacity:.45`, but a click on them was a hard-coded no-op (`trendClick`'s `i >= CHART_MAX`
guard). With 24 curated families and only 8 charted, **16 of 24 rows on the default view were
permanent dead clicks** — Nielsen's "visibility of system status" heuristic violated in the most
literal sense (see `docs/product/2026-08-20_trends-ui-design-research.md` §4). The chart itself
was a hand-rolled inline SVG with no hover state, no tooltip, no area fill, a hardcoded 8-hex
color array, and colors assigned by array index rather than category identity.

No JS framework or build step exists in this codebase (`src/headstart/ui/static/app.js` is a
plain `<script>` tag), and none was introduced — every fix below is vanilla JS/SVG/CSS.

## Decisions

**1. Fold everything past `CHART_MAX` into one "Other" aggregate row, not N dead rows.**
Sums the hidden series' points into a synthetic `{name: '__other__', ...}` pseudo-series,
rendered as its own muted-gray legend row and chart line. It is honestly non-interactive — no
`role`/`tabindex` — and needs no special-case guard: `trendClick`'s existing `findIndex` returns
-1 for `__other__` (it is not a real family name), the same guard an unknown name already relied
on. This is the dataviz skill's own anti-pattern fix ("cycling past 8... fold the tail into
Other"), and it eliminates the dead-click bug entirely rather than better-signaling it.
`LEGEND_MAX` (the old 12-row cap) is gone — every category is now represented, individually or
in Other, so there is no "top N of M" case left to phrase.

**2. Color is 8 CSS custom properties from a CVD-validated palette, assigned by entity identity,
not array position.** The old `TREND_COLOURS` array failed the dataviz skill's own validator on
this app's actual chart surfaces (dark: lightness-band FAIL; light: CVD-separation FAIL, normal-
vision-floor FAIL — the lime/amber pair measured ΔE 12.4, below the 15 floor even for full-color
vision). The skill's documented default palette passes clean on both of this app's real surfaces
and is what's in `style.css` now as `--series-1..8` (dark + both light-mode blocks). Three of
the old eight hexes were also hardcoded literals that never repainted for the light theme at
all — a real, silent bug. Assignment (`assignSeriesColors` / `seriesColorAssignment`, a `Map`
cached at module scope) is keyed on series **name**, sticky for as long as that name is drawn,
freed when it drops out of the charted set — so a Metric/Unit/ATS filter that merely reshuffles
the ranking never repaints a still-visible category (the "recolor-on-filter" anti-pattern: a
reader who learned "AI/ML is violet" must not see it turn blue because a filter changed who's
biggest). Covered by two new JS tests (`app_trends.test.js`).

**3. A hover layer ships by default: crosshair + one tooltip listing every series, plus
highlight-on-hover/dim-the-rest.** The chart had zero interactivity before this. The emphasis
spec (opacity `.25` on non-hovered lines, thicker stroke + re-paint-on-top for the hovered one)
is synthesized from three converging named mechanisms — ECharts' emphasis/blur, Chart.js's
legend-hover alpha-dim, amCharts' stroke-width highlight — per the design-research doc §2, wired
to both `pointerover`/`pointerout` and `focusin`/`focusout` so keyboard users get the same
feedback sighted mouse users do. Built with direct DOM node reuse (`dotEls`, `lastGeom`) rather
than a full `drawTrends()` re-render on every `pointermove`, so it stays cheap and never
re-triggers a fetch.

**4. The segmented controls (Measure/Unit/Drill) move from `role="group"` + `aria-pressed` to
`role="radiogroup"` + `role="radio"`/`aria-checked`, with roving tabindex and arrow-key
navigation.** The WAI-ARIA APG's own Toolbar Example resolves this exact shape — toggle-styled
buttons that are mutually exclusive and apply immediately, no save step — as radiogroup, and
explicitly rejects the "requires a save button" objection (design-research doc §4). GitHub
Primer's dissenting `aria-current`-on-a-button-list approach is noted as the fallback if the
roving-tabindex retrofit ever proves disruptive, but was not needed here.

**5. A real error/retry state, and "refetch keeps the frame."** A failed `/trends` fetch used to
`display:none` the *entire* `#trends` section — every toggle then looked locked with no
explanation, which is its own instance of the "locked tabs" complaint. Now only the chart/legend
area (`#trends-viz`) is replaced by an error banner + Retry button; the header and toggles stay
reachable. While a fetch is in flight, `#trends-viz` gets a `.loading` class (dimmed, not
blanked) instead of the panel disappearing mid-filter-change.

**6. A table-view toggle — the WCAG-clean twin every chart needs per the dataviz skill.** One
button swaps the SVG+legend for a real `<table>` (same rows: 8 charted + Other, one column per
measurement).

**7. Two small, cheap, sourced fixes**: axis labels move from 10px (below Datawrapper's stated
12px legibility floor) to 12px with `tabular-nums`; the y-axis extends to 100 when a share
reading exceeds 80%, matching Datawrapper's rule to show the true ceiling — scoped narrowly
(`hi > 80` only) so it doesn't flatten the common case where every category is a single-digit
share.

## A real bug the browser check caught, not the unit tests

`.trends-error{ display:flex; }` and `.trends-grid{ display:grid; }` are author CSS rules that
silently override the `hidden` attribute's UA-stylesheet `display:none` — the error banner
rendered on page load with no actual error, in a real Chromium run, invisible to the Node test
suite (no real CSS cascade in a `vm` context). Fixed with the same `[hidden]{ display:none
!important; }` escape hatch `style.css` already used for `.panel[hidden]`. See
`experiment/trends-redesign-2026-08-20/LOG.md` for the full verification pass (Playwright,
11-family synthetic fixture, 13 assertions, all green after this fix).

## What this deliberately does not touch

- **`CHART_MAX` stays 8.** It's cross-coupled to the validated palette's slot count and to
  `config/role_watchlist.json`'s own documented ceiling (enforced by
  `tests/test_role_watchlist_config.py`). Raising it would need a bigger validated palette or a
  composite-encoding fallback, not attempted here.
- **The backend/`role_trends.py`/`/trends` endpoint are untouched.** Everything above is
  computable from the existing `/trends` JSON response; no new field, no schema change.
- **The `docs/product/2026-08-14_adversarial-audit.md` kill-it recommendation** was surfaced and
  the decision to proceed anyway was made explicitly by the person who asked for this redesign,
  not silently overridden — recorded here so the tradeoff isn't lost to history.
