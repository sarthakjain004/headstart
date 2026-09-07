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
the plot, it puts all nine families on a footing where their shapes are directly comparable, and it
makes the chart answer the heading above it.

**What indexing costs, and where the cost is paid.** An indexed line cannot tell you that
software-engineering is a fifth of the index while java-development is a fortieth — the two now
start in the same place. That magnitude does not disappear; it moves to the surfaces that were
already carrying it and carry it better as text: the legend's share column, the table view, and the
tooltip. A chart that shows nine shapes and a legend that shows nine levels between them say
everything the old single chart was trying to say in one channel and failing.

**Share and Count remain.** Indexing is a third unit alongside them, not a replacement. A reader who
wants levels selects them and gets the old axis, empty band and all — which is the honest chart for
that question.

### "Other" leaves the plot and stays in the legend

It is a reconciliation row, not a category. It keeps its place in the legend and the table so the
27% it accounts for is never silently dropped, and it stops deciding the scale for eight series that
are real. Nothing about what is *counted* changes — only what is drawn.

## Consequences

The empty band is gone and the six smaller families become readable, which is the point.

Indexing is sensitive to its base. A family whose first measurement is unusually low or high will
show an exaggerated line for the whole window, and a family with no measurement at the first stamp
has no base at all — its line starts from its own first measured point, indexed there, which is
correct but means two lines can begin at 100 in different places. The window control makes this
adjustable rather than fixed, and the caveat block says so.

**The ADR-0057 caveat gets more load-bearing, not less.** A family's line moving is still not the
same as jobs opening or closing: a job can move between families when its text is filled in later,
which looks exactly like one closing here and another opening there. Indexing makes those movements
*more* visible, so the note that explains them matters more than it did. It stays, verbatim.

This changes no ledger, no stored column, and no API response. `/trends` returns the same payload;
the third unit is computed in the browser from the points it already sends.
