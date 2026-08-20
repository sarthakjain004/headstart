# ADR-0075: `ats` becomes a trends-ledger dimension, filtered by exclusion at read time

- Status: Accepted
- Date: 2026-08-20
- Relates to: [ADR-0040](0040-role-trend-ledger.md) (the ledger and `count_groups`/`append_ledger`
  this extends), [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md) (added `metric` as a
  dimension the same way, and the `_migrate_ledger` in-place-rewrite this reuses), [ADR-0074](0074-browse-and-paginate-the-search-index.md)
  (the immediately preceding feature — same discipline: measure against real data before trusting
  an assumption, grill the design before writing code)

## Context

The Trends tab charts openings over time by role family and seniority band, but has no way to see
or filter that by which ATS posted them, even though every served row already carries `ats`
(`self.atses` in `search.py` is the sorted list of live distinct values). The ask: let a user
select/deselect multiple ATSes and have the Trends charts reflect only the selected subset.

The Trends chart is served entirely from a pre-aggregated, append-only ledger
(`data/state/role_trends.csv`) written once per ~6-hour pipeline run and loaded into memory at
Space startup (`deploy/hf-space/app.py`) — there is no per-request LanceDB query in this path, unlike
Search. `count_groups` (`role_trends.py`) sums the live table into `(metric, family, band, count)`
rows; the per-row `ats` value is available at count time but discarded before the row is written.
That means a historical per-ATS breakdown cannot be reconstructed after the fact — there is nothing
to backfill from. A genuine "filter the trend line by ATS" therefore only ever accumulates history
forward from whenever this ships, never backward.

Verified against live data before deciding anything (not the stale local copy that was sitting in
the working tree — refreshed from HF first): 42,404 ledger rows across 106 runs as of this run
(2026-08-20T10:22 UTC), averaging ~400 rows/run; 18 distinct ATSes actually serving rows in the live
`jobs` table (287,144 rows) today, from Workday (88,392) down to Workable (553) — not the 21
registered scrapers, since a few have zero live rows currently and `join` stays disabled (ADR-0017's
own known exclusion).

## Decision

**One more grouping dimension on the existing ledger, not a second query path.** `count_groups`
buckets into `(metric, family, band, ats)` instead of `(metric, family, band)`; `append_ledger`
writes a 7th column. The rejected alternative — a live, request-time LanceDB query for a
current-moment ATS breakdown — was ruled out up front: it can only ever show a snapshot of right
now, never a trend line, because the `jobs` table itself is not a history; only the ledger
accumulates one, and only along the dimensions it tracks. "Filter the *Trends* tab" has to mean the
existing time series, or it isn't Trends.

**In-place migration, reusing `_migrate_ledger`'s exact shape.** The function already rewrote every
pre-ADR-0051 row once, stamping `metric='stock'` on a file that had none. The same function grows one
more column: every existing row gets `ats='all'`, meaning "not decomposed by ATS." This keeps the
default, unfiltered view's history unbroken — summing across `ats` (the default, see below) sums the
sentinel and the real per-ATS rows together identically, since neither the migration nor the read
path needs to treat them specially. A request that filters to any real ATS name naturally excludes
the sentinel and only sees data from the ship date forward.

**The `/trends` route gets one more filter clause, not a new code path.** The route already sums
"over the other axis" whenever a caller doesn't drill into one specific value — a family point is
already its total across bands, with zero special-casing, because `band` was never part of the
grouping `key` in the no-drill case. Adding `ats` as one more such axis costs one pre-filter step,
parallel to the existing `since`/`until` handling: `?ats=` (repeated, `request.args.getlist("ats")`
— matches the `URLSearchParams`/`getlist` convention `app.js` already uses everywhere else, no
comma-joining to write or parse) narrows `trends_rows` before the same grouping loop runs unchanged.
`role_trends.py` never learns which ATSes a user picked; that's a read-time concern the route already
had the shape for.

**No `ats` param sent at all is the only spelling of "no filter."** The frontend's ATS picker starts
with every ATS checked; checking every box back to 18/18 must produce the *same* request as never
touching the control — not an explicit list of all 18 names. If "all checked" were ever serialized
as an explicit whitelist, it would silently exclude every migrated `ats='all'` sentinel row (which
matches no real ATS name), truncating history to the ship date forward for a user who never meant to
exclude anything. The sentinel is never exposed as a selectable 19th option.

**A new dropdown/popover checklist, scoped to this one control.** Trends has no sidebar (Search's
filters live in a persistent `.rail`; Trends is one compact header row) and the app has no
popover component today. Chose to build one rather than reuse the existing `.seg` toggle-group
CSS/markup as a wrapping multi-press chip row — the simpler option by code volume, but rejected
because a checklist popover keeps the control to one button's height in a header row that already
carries three other toggle groups, at the cost of being the first click-to-open component in this
frontend (own open/close state, outside-click and Escape handling — written for this one call site,
not as a reusable abstraction other controls are expected to adopt). The trigger reads "All ATS ▾"
by default, "`{n}` ATS ▾" once fewer than 18 are checked. Options are `_searcher.atses` — the same
sorted, already-computed list Search's own ATS `<select>` already renders from — so there is exactly
one place that lists live ATSes, not two.

## Rejected alternatives

- **Live current-moment breakdown from LanceDB, no ledger change.** Ships without touching the
  pipeline, but cannot answer "trend," only "right now" — see Context.
- **Leave old ledger rows untouched, let two row shapes coexist.** No migration code to write, but
  the exact situation ADR-0051 already chose not to leave behind — a normalized ledger stays easier
  to reason about than one with two silently different row lengths forever.
- **Comma-joined single `ats=` value.** Would need custom split/join logic on both ends where
  `getlist`/repeated `URLSearchParams.append` already does the job with less code and matches every
  other multi-value convention this stack has (there were none before this — `getlist` is simply
  Flask's own idiom for the case).
- **Exposing the migration sentinel as a selectable option** ("Pre-2026-08-20 data (undecomposed)")
  so every request, including the default, sends an explicit list. More literal, but turns a
  migration implementation detail into a user-facing concept and makes "select all" stop meaning "no
  filter."
- **Repurposing `.seg` as a wrapping multi-press chip row.** Reuses proven CSS/markup with zero new
  interaction code, and was the recommended option going in — the user chose the popover instead,
  trading that simplicity for keeping the header row compact at 18 options.

## Consequences

**Ledger row growth is real, not backfillable.** ~400 rows/run today could grow toward an estimated
2,000–4,000/run (sparse — small ATSes won't span every family × band, so not the full 18× worst
case) the moment this ships, and — unlike every other ledger change so far — there is no way to
manufacture the missing history later; a filtered chart's "before" is a hard wall, not a gap that
narrows over time on its own once a few points are missing. The UI must say this on a filtered,
short-history chart rather than let a sparse line read as "barely any of these openings exist."

**The frontend gets its first popover.** Written narrowly for this one control; a second use case
elsewhere is what would justify generalizing it, not this one.

**Search's ATS dropdown is untouched.** It stays single-select, reading from the same `atses` list
Trends' new picker now also reads from — one source of truth, two different controls built for two
different filtering shapes (a single deterministic where-clause vs. an inclusion set over ledger
rows).
