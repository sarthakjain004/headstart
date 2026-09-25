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

Versions are stitched into one history. A *span* is a run of consecutive ticks counted at one
version (`headstart.version_spans`, shared by the Space and the Hot stage). A new span starts at
a tick holding a version the running span has not seen, which is the refit's own tick. Each tick
keeps the rows of its span's version (`_stitch_versions`), and each span's Board deltas are
replayed from that span's first tick (`_replay_rows` → `_replay_span`). A stray row of another
version is dropped, so two versions never share a tick. A version that returns after a newer one
(a classifier head rolled back) is a new span with a fresh re-write, not a stop to the history.

The refit tick is not hidden. It carries an epoch (ADR-0164), so the chart marks it. Under a
company pick, the step it makes is taken out of the lines like any other counting change: by its
size in openings, with the run after it. A category line can still jump at a refit, because the
refit re-sorts families. The marker says why.

Two things are read across every version:

- **Board arrivals** are each Board's first tick in any version, so a refit is not a "board found
  later", and `new` holds end a week after a Board was first read.
- **Comparable coverage's cohort** uses each Board's first stock tick in any version.

The live version, whose counts are current, is the one that began last, not the last row read.

`hot_boards` sums every span's ticks, except each span's first, which is a baseline re-write.
It also leaves out counting changes, with the run after each, located on the ticks as written:
the refit's change is its own baseline tick.

A family-rules or taxonomy change is still taken out of a whole company's line, although a
critic asked that it not be. Such a change can move rows to or from the non-tech family, so a
company's tech total can step at it.

## Consequences

- A refit costs the chart one marked step instead of its whole history.
- The index view (no pick) shows the step too. It is marked, and its figures are not netted, as
  before.
- A category's line across a refit compares two taxonomies. That comparison is only as good as
  the taxonomies' overlap, and the marker is the only warning.
- A whole company's tech line can move at a refit too, where rows move to or from the non-tech
  family. That move is the marked step, and a company's line takes it out. The Jobs served do
  not change with a version, so the counts on either side are of the same openings.
