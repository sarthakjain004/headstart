# ADR-0118: The trends chart plots change, not level

**Status:** accepted · **Date:** 2026-09-07 · **Refines the panel ADR-0040 introduced and ADR-0051 split by role; changes no ledger, no API field, and no stored data**

## Context

The Trends panel asks one question in its own heading — *which tech roles are growing* — and then
draws a chart that answers a different one: what share of the index each role family currently
holds. Those come apart badly on this data.

Measured against the live ledger on 2026-09-07 (471 measurements over 27 days, 24 families, the
top 8 charted with the remainder folded into one "Other" bucket):

| series | latest share |
|---|---|
| Other (16 smaller categories) | 27% |
| software-engineering | 20% |
| ai-ml | 7.8% |
| systems-engineering | 6.5% |
| web-development | 4.9% |
| hardware-embedded | 4.9% |
| java-development | 2.8% |
| security-engineering | 2.8% |
| product-management | 2.8% |

On a shared linear axis from zero to the maximum, **the band between 8.3% and 19.7% holds no marks
at all — 42% of the plot height, 180px of nothing at a 2000px viewport.** The six smaller families
are compressed into the bottom fifth, where four of them sit within 0.1 percentage points of each
other and are mutually indistinguishable. The reader who came to find out whether their own role is
growing cannot see their own role.

The chart was not badly drawn. It was drawing the wrong quantity: a *level* axis, where the data's
whole spread is a rounding error next to its largest member, for a question about *movement*.

Two further facts sharpened the choice.

**The axis was being set by a bucket.** "Other" is the topmost line at 27%, and because the maximum
is taken across everything drawn, it alone decided the scale that squashed the eight real
categories. It is also the one line that is deliberately inert — clicking it cannot drill, because
it names no entity — so it took the most visual weight on the plot while carrying the least
meaning.

**The information the reader actually wants was already on screen, in the legend.** Each row
carries the family's current share and its movement over the window (`+80.8%`, `-22.0%`). The chart
was the one place that movement *wasn't* legible.

## Decision

### The chart plots each family indexed to 100 at the start of the window

Every series begins at 100 on the left edge and the line traces its change from there. A family
that has grown a fifth reaches 120; one that has fallen a fifth reaches 80. The y-axis is
`index (100 = <first stamp>)`, not a percentage of anything.

This is the standard remedy for several series of different magnitude sharing one axis, and it is
what the dataviz skill prescribes over the alternative that tempts everyone — a second y-scale,
which invents a correlation that is not in the data. Here it does three things at once: it fills
the plot, it puts every family on a footing where their shapes are directly comparable, and it
makes the chart answer the heading above it.

### It indexes the raw count, not the share

The base is a family's count of live openings at the window's first measurement. The alternative —
indexing the *share* — was considered and rejected, and the choice matters enough to record, because
the two answer different questions:

- **Index of count** answers *are there more of these jobs than there were.* That is the literal
  reading of "which tech roles are growing", and it is the question a person searching for work is
  actually asking.
- **Index of share** answers *did this family gain ground on the others.* It is immune to the index
  itself growing, which is a real advantage, but it is a relative measure wearing the clothes of an
  absolute one.

**The cost is real and is not hidden.** Our index grows as scraping coverage grows: a run that adds
a board lifts every family's count at once, without a single job having been posted. On a count
index that shows up as every line stepping up together. The caption says so in as many words, and
says what to do about it — read a family against the others rather than on its own. The Share unit
remains one click away for the reader who wants the confound removed rather than disclosed.

**What indexing costs, and where the cost is paid.** An indexed line cannot tell you that
software-engineering holds tens of thousands of openings while java-development holds a few
thousand — the two now start in the same place. That magnitude does not disappear; it moves to the
surfaces that were already carrying it and carry it better as text: the legend's value column, the
table view, and the tooltip, which under Change reads level and index together. Because both the
plot and the legend delta now stand on counts, the sign and rough size of a line's movement and the
number printed beside its name agree, which they would not if the two used different bases.

**Share and Count remain.** Indexing is a third unit alongside them, not a replacement. A reader who
wants levels selects them and gets the old axis, empty band and all — which is the honest chart for
that question.

### "Other" leaves the plot and stays in the legend

It is a reconciliation row, not a category. It keeps its place in the legend and the table so the
27% it accounts for is never silently dropped, and it stops deciding the scale for eight series that
are real. Nothing about what is *counted* changes — only what is drawn.

## Consequences

The empty band is gone and the six smaller families become readable, which is the point.

Indexing is sensitive to its base, and the base is a family's first **measured** level in the
window — not its first non-zero one. A family with no measurement at the first stamp therefore
starts from its own first measured point, indexed there, so two lines can begin at 100 at
different x positions. The window control makes the base adjustable rather than fixed.

**A base under five openings is not a base.** Below that floor a family is drawn as a gap and its
legend row reads "not indexed", rather than turning one posting into +50% or, at a base of zero,
an unbounded spike that drags the axis with it. The same floor gates the KPI tiles, so a tile can
never headline a family the chart is refusing to draw — the two ran on different quantities at
first (the first measured level against the mean of the first three), and a family based at four
openings was simultaneously a gap on the plot and "Biggest riser +233.3%" above it.

Because the base is a count, **the chart inherits the index's own growth** — the confound the Share
unit exists to remove. This is the deliberate trade above, not an oversight, but it means a rising
line is never on its own evidence that a role is hiring more: it is evidence that its count rose,
which a coverage change can also produce. Anyone quoting a number off this chart should say which
unit it came from.

**The ADR-0057 caveat gets more load-bearing, not less.** A family's line moving is still not the
same as jobs opening or closing: a job can move between families when its text is filled in later,
which looks exactly like one closing here and another opening there. Indexing makes those movements
*more* visible, so the note that explains them matters more than it did. It stays, verbatim.

This changes no ledger, no stored column, and no API response. `/trends` returns the same payload;
the third unit is computed in the browser from the points it already sends.
