# ADR-0269: Every Trends control is answered from the browser

**Status:** accepted · **Date:** 2026-09-29 · **Amends:** [ADR-0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md) (a preset window is now asked ahead; the Space keeps 512 answers), [ADR-0261](0261-a-trends-view-nobody-has-asked-for-is-cheap-to-work-out-and-asked-for-ahead.md) (more views are answered ahead, in the background), [ADR-0262](0262-a-caller-with-no-session-reads-the-public-routes-sixty-times-a-minute.md) (a kept Trends answer is not counted) · **Relates to:** [ADR-0075](0075-ats-becomes-a-trends-ledger-dimension.md) (the Source picker)

## Context

The owner reported that every Trends control was still slow after ADR-0251 and ADR-0261. Measured
on 2026-09-29, in headless Chromium driving the page's own code against the live Space (225 ms
round trip from here), the page draws a view in 3-5 ms, and a view the browser already holds is on
screen in about 20 ms. Every other click paid the network. A 7-, 30- or 90-day preset cost a round
trip on every click (230-440 ms), because its `since` was measured back from the click's own
millisecond. That made each click a new URL, which the browser could not keep and the page could
not ask ahead for. Job sites, Role and a Source untick paid the same, and a view nobody had asked
for this boot cost the Space 1-9 s. Locally that answer takes 50-190 ms. The Space's own boot log
puts an uncontended one at ~0.25 s, and the rest was queueing behind other work: its log showed a
steady stream of `POST /mcp`. Each Source box fired its own request. The page cancelled all but
the last, but the Space still worked out every one, one at a time. Table view built a
1,004-column grid behind a fold most readers never open (~110 ms).

## Decision

1. **A date preset is measured back from the history's newest tick**, which the page gets as
   `trends_newest_tick` in its config, not from the click's moment. A preset is then one URL for
   the whole boot.
2. **The Space answers every top-level view a click away, and each charted category's levels
   under each, in a background thread after the opening views.** That is both Measures, both Job
   sites and every preset: 16 views and 128 drills, 90 distinct answers (9 MB), since a preset
   wider than the history keys as All. The Space starts serving without waiting for them. It
   works them out one at a time under the one lock, so a reader's own new question waits behind
   at most one. The Space now keeps 512 answers, up from 128.
3. **The page asks ahead for a drawn view's neighbours, all at once**: the other Measure, every
   other preset, the other Job sites and, inside a category with named roles, the other
   breakdown. A view narrowed by a company, a Source or a typed date asks only for the other
   Measure, after a second, as before, since the Space has to work out each of those. None under
   Save-Data.
4. **A `/trends` answer already kept is not counted against the caller's read limit.** The limit
   bounds what a question costs the Space, and a kept answer costs a lookup, as a static file
   does, which nothing limits. Counted, the prefetches of a reader clicking briskly passed 60 a
   minute and answered their own click `429`, which the harness showed.
5. **A Source box asks at once; the boxes that follow it within 300 ms ask once, when they stop.**
   A burst that ends on a view the browser holds asks at once.
6. **The every-measurement grid is built only while its fold is open.**
7. **An ATS selection holding most index rows is answered as every ATS's cells less the rows left
   out.** Unticking one Source went from ~180 to ~95 ms here. Answers hash the same as main's on
   60 random ATS, window, family and metric questions over the real history.

## Options not taken

- **Rounding the page's clock** (ADR-0251 rejected it) moves the window's edge by the rounding.
  Anchoring on the newest tick moves it by the time since the last run, an hour or so, and is
  the stronger reading: a paused pipeline no longer shortens "7 days" of data.
- **A `days=` query parameter resolved by the Space** would give the same stable URL, but it
  changes the route's contract that the MCP tools share, for no gain over item 1.
- **Answering every view at boot, synchronously**: every second of boot is a second the Space
  does not serve after each pipeline run.
- **A plain debounce on the Source picker** delayed every single untick by its wait (measured:
  +300 ms). The leading edge keeps a single click immediate.
- **A larger read limit for signed-in Accounts, or a separate bucket for prefetches**: the first
  weakens the bound for each Account created, and the second is a header anyone can send. Item 4
  exempts only what costs the Space nothing.
- **Asking ahead for a Source box the pointer rests on**: a pointer sweeping 50 boxes would spend
  the reader's limit on answers the Space must work out, and a refused click is worse than a slow
  one.
- **Asking ahead for every charted drill**: still left to the pointer resting on a row
  (ADR-0261). With the drills now kept on the Space, that costs one round trip.

## Consequences

Measured in the same harness, with the Space's app booted locally over the real history (1,004
ticks), its 225 ms round trip and 3x slower CPU simulated, three visitors each after a boot (ms,
median):

| Control | before | after |
| --- | ---: | ---: |
| 7 / 30 / 90 days | 262 / 255 / 255 | 20 / 21 / 20 |
| Job sites: Tracked from start | 255 (738 first) | 20 |
| Break down by Role | 254 (579 first) | 21 |
| Table view on | 107 | 12 |
| Drill (pointer resting 300 ms) | 29 | 19 |
| Source untick, first visitor after a boot | 1,039-1,129 | 756-786 |
| Source untick, a view already asked for | 252-255 | 253-256 |
| Measure, Unit, Back, re-tick, company pick | 12-22 | 12-22 |

What it costs: ~18 s of the Space's CPU after each boot for the background answers. About 0.3 MB
a drawn top-level view for the neighbours, mostly read back from the browser as the reader moves
between them. Kept answers can be read without limit, as the page's scripts can. A preset's
window starts up to one pipeline run earlier than "N days before now". A Source untick is still
a round trip, and a view a company or a Source narrows still costs the Space one answer the
first time anyone asks for it.
