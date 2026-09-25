# ADR-0221: A refit is a step in one Trends history, not its end

**Status:** accepted · **Date:** 2026-09-25 · **Amends:**
[ADR-0040](0040-role-trend-ledger.md) (the Space charts the newest version only) ·
**Relates to:** [ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md) (the Board-delta
ledger), [ADR-0164](0164-mark-when-the-definition-changed-not-just-the-data.md) (epochs),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (company trends)

## Context

`role_trends` stamps every row, and every Board-delta tick, with a series version. A refit or a
new generation of title rules starts a new version. Its first tick re-writes every series, and
every Board's stock, from scratch. The Space kept only the newest version, on the reasoning that
two versions must never share an axis. `hot_boards` did the same: it summed only the newest
version's ticks.

That was tolerable while refits were rare and the history was short. On 2026-09-24 at 21:19 a
family-rules refit (series version 2001) started a new version. The morning after:

- every Trends chart read "8 measurements over 6 hours";
- every Board had "arrived" at the refit, so "New this week" held all openings for another week;
- Hot measured "this week" over 4.4 hours.

A critic scored the company tab 4/10 on this alone. Eleven days of history were in the ledger;
the Space had thrown them away.

## Decision

Versions are stitched into one history. Each version keeps its rows from its first tick up to
the next version's first tick (`_stitch_versions`). Its Board deltas are replayed over that span
from its own first tick (`_replay_rows` → `_replay_span`). A row a version wrote after the next
one began is dropped, so two versions never share a tick.

The refit tick is not hidden. It carries an epoch (ADR-0164), so the chart marks it. Under a
company pick, the step it makes is taken out of the lines like any other counting change: by its
size in openings, with the run after it. A category line can still jump at a refit, because the
refit re-sorts families. The marker says why.

Two things are read across every version:

- **Board arrivals** are each Board's first tick in any version, so a refit is not a "board found
  later", and `new` holds end a week after a Board was first read.
- **Comparable coverage's cohort** uses each Board's first stock tick in any version.

The live version, whose counts are current, is the one that began last, not the last row read.

`hot_boards` sums every version's ticks, except each version's first, which is a baseline
re-write. It also leaves out counting changes, the refit's among them, with the run after each.

## Consequences

- A refit costs the chart one marked step instead of its whole history.
- The index view (no pick) shows the step too. It is marked, and its figures are not netted, as
  before.
- A category's line across a refit compares two taxonomies. That comparison is only as good as
  the taxonomies' overlap, and the marker is the only warning.
- A whole company's tech line can move at a refit too, where rows move to or from the non-tech
  family. That move is the marked step, and a company's line takes it out. The Jobs served do
  not change with a version, so the counts on either side are of the same openings.
