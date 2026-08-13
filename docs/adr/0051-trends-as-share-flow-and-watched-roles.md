# ADR-0051: Trends measure share and flow, and can watch a named role

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-0040

## Context

ADR-0040 shipped a trends chart of live index stock per (role family × seniority band). Read
against real data, the chart could not answer the question it exists to answer. A snapshot of one
run's deltas: **ten of twelve categories up, mean +1.46%, median +1.50%**, spread across security,
hardware, QA and data engineering alike. The tech job market does not move 1.5% in a day and a half
in near-lockstep. What that pattern shows is **our index growing** — Boards discovered, backlog
embedding, evictions lagging — distributed across categories roughly in proportion to their size.

ADR-0040 chose index stock over per-run scrape counts deliberately, and for a good reason: scrape
counts swing for pipeline reasons. But it traded one artifact for another. The ledger carried
`(ts, version, family, band, count)` with **no denominator**, so nothing let a reader separate "this
category grew" from "we are scraping more".

Three further gaps came out of asking what different readers need. Someone choosing a
specialisation wants months, and got a chart with y-axis labels but **no x-axis labels at all** —
no way to tell whether the window was two hours or two years — plus a headline delta computed from
the single first and last points, which one noisy run swings. Someone timing a job hunt wants
**flow** ("what appeared this week") and got only stock; a category can hold 70,000 stable openings
while posting almost nothing new. And someone tracking a specific role — the motivating example was
**Forward Deployed Engineer** — got nothing. 761 Jobs carry a forward-deployed title (370 distinct
titles) in a July tech snapshot of 75,166, so ~1% of that snapshot and **0.17% of the 438,424 rows
the live K=72 fit clustered**. At that share it earns no centroid of its own, and the role is
smeared across general software-engineering clusters.

## Decision

**The ledger gains a `metric` axis**, becoming `(ts, version, metric, family, band, count)`.
`stock` is every live row, as before. `new` counts the subset whose `first_seen` falls inside a
rolling **7-day** window — a fixed window rather than a per-run diff, because the 2-hour cadence is
irregular (33–124 minutes) so per-run deltas are pipeline noise, and "roles posted this week" is
the question people actually have. `first_seen` survives an ADR-0050 re-embed, so an upgraded
vector does not read as a fresh opening.

The ledger is append-only and rides the HF state round trip, so **it migrates in place** on the
first six-column write: every pre-ADR-0051 row was a stock measurement, making the backfill exact
rather than a guess. The file is a few dozen rows per run, so rewriting it whole is free.

**Share of index is the default unit for stock.** The endpoint returns a per-stamp `totals` array —
families plus non-tech, which is the whole served table, since `count_groups` assigns every row
exactly once — and the UI plots `count / total`. An index that grows 1.5% overnight moves every
count and no share; a share moves only when categories move *relative to each other*, which is the
only thing this data can honestly say about the market. Counts stay one click away, with a footer
that now names the coverage caveat instead of implying there isn't one. `new` is charted as counts,
because its meaning is the number itself.

**Named roles are watched by title pattern**, curated in `config/role_watchlist.json` and counted
into the same ledger under a reserved `watch:` family namespace. Patterns, not centroids: a role
this small earns no cluster at any practical k, a pattern is explainable per Job, and it survives a
centroid refit unchanged — whereas a refit re-bases every centroid-derived series. A watched role
is counted **in addition to** its family, never instead of it, so family totals stay complete; the
endpoint therefore excludes `watch:` rows from the top-level view and from the share denominator,
and surfaces them under their declared parent via `?family=X&split=roles`.

**The chart states its own window**: a time axis (first / middle / last measurement) and a scope
line carrying the elapsed span, so "19 measurements" can no longer be mistaken for a long-run
trend. The delta averages the first and last up-to-three measured points instead of comparing two
single runs.

### Rejected alternatives

- **Semantic proximity for watched roles** — embed a query, count rows within a threshold. Catches
  variants a title never spells out, but the threshold has no principled value, moving it moves the
  whole series, and it cannot explain per-Job why something counted.
- **Refit at higher k so fine roles emerge** — most faithful to ADR-0040's design, but a refit
  re-bases every series and cuts off all existing history, and a role at 0.17% of the clustered
  population would need a K far above the 72 the version-2 fit already raised it to (itself a
  deliberate step up from the sweep's K=40) before it separated out.
- **Normalising by Board count rather than by index total** — closer to "per unit of coverage", but
  Boards differ hugely in size, so it trades a clean denominator for a noisy one.
- **Per-run flow instead of a rolling week** — cheaper, but the run cadence is irregular by design,
  so the series would encode pipeline scheduling as market movement. The exact mistake being fixed.

## Consequences

**The old numbers are not comparable to the new ones.** Share and count answer different questions,
and anyone who read "+2.1% AI/ML" from before was reading index growth. That is the point of the
change, but it does mean the visible history changes meaning under readers who saw it.

**`new` is blind before ADR-0031.** Rows indexed before `first_seen` existed carry no stamp and are
never counted as new. They age out of the 7-day window's relevance quickly, but the first week of
the series under-reports.

**A watched role can double-count against its own parent's drill.** By design — `watch:fde` rows sit
alongside the band rows for `software-engineering`, so the two splits of one family sum
differently. The endpoint keeps them in separate views for exactly this reason, and the share
denominator ignores `watch:` rows entirely.

**A loose pattern is a silent inflation.** `config/role_watchlist.json` is validated for duplicate
names, unknown parents and bad regexes, but nothing can tell that a pattern is too broad — it will
simply carry a wrong number forever. Patterns are curated content, reviewed like the family map.

**Still not addressed**, and worth naming so it is not rediscovered: trends are global, not scoped
to the reader's filters (remote, location, experience), so they cannot answer "is *my* slice
growing"; there is no per-company dimension ("who is hiring in AI/ML"); and pipeline artifacts —
notably ADR-0046's collapse guard freezing a Board's rows after a truncated scrape — still read as
market movement with nothing marking them.
