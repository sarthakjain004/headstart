# The Trends SOURCE control made the numbers churn

Reported as: *"when i select ATS source — numbers on trends flicker a lot."* Measured, fixed and
re-measured 2026-09-10. This note exists so the figures quoted in `app.js`'s `loadTrends` comment
can be re-checked rather than taken on trust.

## What the control does per interaction

`#trends-ats-menu` is a popover of one checkbox per ATS, every one checked at first paint, and its
handler is one line (`app.js`, near the other Trends wiring):

```js
el('trends-ats-menu').addEventListener('change', () => { trendAtsLabel(); loadTrends(trendDrill); });
```

There is no select-all/none control, so reaching the reporter's **"1 ATS"** state means unchecking
20 of 21 boxes — **20 `change` events, 20 `GET /trends`, and (before the fix) 20 full repaints**,
each one rewriting the KPI tiles, the legend rows and the chart.

## Method

A stub Flask app serving the **real** `templates/` and `static/app.js`, with `/trends` answering a
fixture after a delay drawn from `[0.9, 1.5]` s — chosen to bracket the deployed Space: the
endpoint's own aggregation is **250-390 ms** of Python over the real 2,644,233-row
`role_trends.parquet` (3 runs, this laptop), on top of a ~0.9 s round trip to HF measured with
`curl` against `https://imposeidon-headstart-search.hf.space/trends` (401 at the auth wall, so the
timing is the round trip, not the query). Driven with Playwright/Chromium: `window.fetch` wrapped
to log request and response order, `window.drawTrends` wrapped to count repaints.

> One harness trap worth recording: `page.evaluate()` **calls** a function-valued result, so
> ending the instrumentation script with the wrapper assignment made Playwright invoke it and
> inflated every repaint count by exactly 1. End such a script with `0;`.

## Numbers, one "select 1 ATS" interaction (20 unchecks)

| Click rate vs. round trip | | requests | repaints | answers out of order | settled on the last request's answer |
|---|---|---|---|---|---|
| Faster (burst) | before | 20 | 20 | 6-11 | 2 of 5 runs |
| Faster (burst) | after | 20 | **1** | 0 | 5 of 5 runs |
| Faster (0.4 s/click) | before | 20 | 20 | 1 | yes |
| Faster (0.4 s/click) | after | 20 | **1** | 0 | yes |
| Slower (2.0 s/click) | after | 20 | 20 | 0 | yes |

Before, the legend's top row walked `32k 33k 35k 30k 37k 28k 26k 20k 24k 22k …` — non-monotonic,
because answers landed in a different order than they were asked for. That is the churn.

## What the fix does and does not bound

`loadTrends` now cancels its own previous request (`AbortController`), so a superseded answer can
neither paint nor be raced. That bounds the interaction to **one repaint at any click rate faster
than the round trip** — the rate that produced the churn.

It does **not** bound the slow regime (last row): when nothing overlaps there is nothing to cancel,
and 20 unchecks paint 20 times. Those 20 are in order and each is the true answer to a click just
made — the panel updating per action rather than churning. A debounce does not fix that row either
(a burst is already collapsed by the abort; slow clicks outrun any sane window). The only
unconditional bound is **committing the selection when the popover closes**, which changes what the
control means — a product call, deliberately not smuggled in with a race fix.

## Separately: "0 ATS" served every ATS — fixed in the follow-up

Found while measuring the same control, not caused by the flicker fix, and **since fixed** — kept
here because the diagnosis is the useful part.

Unchecking *every* box gave `trendAtsSelected()` a `[]` — not `null`, since `0 !== boxes.length`.
`if (ats)` is truthy for an empty array and `ats.forEach(...)` appends nothing, so the request
carried **no** `ats` param, which `/trends` documents as the one spelling of *"no filter"*
(ADR-0075). The wire was therefore right all along; only what the panel *said* was wrong, in three
places at once, each testing that truthy `[]`:

| | said | showed |
|---|---|---|
| trigger label | "0 ATS" | every ATS |
| chart `aria-label` | ", 0 of the ATS sources" | every ATS |
| short-history note | "for this ATS selection … Broaden the selection" | nothing was narrowed |

Verified in Chromium: legend **39k**, the unfiltered figure, under all three.

**Three states reached a wrong label, and all three date from the control shipping**
(`962ab062`, 2026-08-20, #216 — neither `trendAtsSelected` nor `trendAtsLabel` has been touched
since, the flicker fix included):

- **Unchecking the last box** — "0 ATS" over unfiltered numbers, as above.
- **First load** — actually fine, and worth saying because it is the natural suspicion:
  `trends.html` renders every box `checked` unconditionally, so a normal first load really is
  all-checked. There is no "arrive with nothing checked" state to fix.
- **Restore from the back/forward cache** — real, still open, and **not a label bug**; see below.

The fix says it once, at the source: `trendAtsSelected()` answers `null` for an empty selection as
well as a full one, which corrects all three readouts together, and the trigger reads its state off
that same function instead of counting the boxes a second time.

### Still open: the back/forward restore path serves an unfiltered chart

A back-navigation builds a **fresh** document (measured: a marker set before leaving is gone), and
the browser restores the checkboxes *after* the scripts have run — so `loadTrends` has already
asked off the all-checked markup. Measured in Chromium, returning to a page left with 3 of 21
ATSes selected:

| | boxes | trigger | chart |
|---|---|---|---|
| before leaving | 3 checked | "3 ATS" | 5.6k (filtered) |
| after going back | 3 restored | "All ATS" | **39k (unfiltered)** |

So the panel is *already* inconsistent on this path, and the inconsistency is in the **data**, not
the label. The mechanism is a straddle: `loadTrends` reads the selection twice — once to build the
query string, once in `drawTrends` to name the chart — with a network round trip in between, and
restoration lands inside that gap. The request goes out unfiltered, the `aria-label` written when
it returns names all 3 ATSes (measured), and neither is wrong about what it saw. A relabel-only fix was tried and **measured to make it worse**: hanging `trendAtsLabel`
off `pageshow` (the one moment the boxes are final) makes the trigger read "3 ATS" over that same
39k unfiltered chart — the label now agrees with the boxes and disagrees with the numbers, which is
the reported defect inverted. It was removed rather than shipped.

Closing this properly means re-*asking* once the boxes are final, not re-labelling — a wiring
change, on a path nobody has reported hitting. Left for a decision rather than taken here.
(The empty case is safe on this path either way: nothing checked restores to "All ATS" over an
unfiltered chart, which agrees.)
