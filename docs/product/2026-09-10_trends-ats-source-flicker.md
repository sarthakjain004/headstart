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

## Separately: "0 ATS" serves every ATS

Not caused by this change and **not fixed here**, but found while measuring the same control, and
user-visible. Unchecking *every* box gives `trendAtsSelected()` a `[]` — not `null`, since
`0 !== boxes.length`. `if (ats)` is truthy for an empty array, `ats.forEach(...)` appends nothing,
so the request carries **no** `ats` param, which `/trends` documents as the one spelling of *"no
filter"* (ADR-0075). Verified in Chromium: the trigger reads **"0 ATS"**, the wire carries zero
`ats` params, and the legend shows **39k** — the unfiltered, all-ATS figure. The control claims
nothing is selected while the panel shows everything.
