# ADR-0261: A Trends view nobody has asked for is cheap to work out, and asked for ahead

**Status:** accepted, amended by [ADR-0269](0269-every-trends-control-is-answered-from-the-browser.md) (every view a click away answered ahead, in the background) · **Date:** 2026-09-28 · **Amends:** [ADR-0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md) (its answers are now kept by `answer_key`, not the question, and asked for ahead on intent as well as after a second) · **Relates to:** [ADR-0251](0251-trends-answers-are-worked-out-once-a-boot-and-kept-by-the-browser.md) (answers kept for the boot, by the Space and the browser), [ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) (the history), [ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (company picks and the picker), [ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) (retired families renamed into their successors)

## Context

ADR-0251 made every view already answered this boot near-instant, and left the rest as they
were: a drill, a range, a coverage change or a company nobody had asked for yet still cost the
Space ~1.6-2.4 s, and a repeat visit still revalidated every script (issue #755, round 2).
Measured 2026-09-28 against the pulled history (992 ticks, 7.56M index rows, 1.37M Board deltas),
the time went here:

- **Rows nobody reads.** `unnetted_answer` walks the index as one dict per `(tick, metric,
  family, band)`: 457k for the opening view. Only a band drill reads a band, and only its own
  family's; summed over bands the same view is 77.5k rows. A company's replay: 108k and 24k.
- **The same sum, per question.** Every question with no ATS picked summed the 7.56M index rows
  into cells again (~100 ms here).
- **Sorts of every delta** in the replay behind a company or comparable view: a lexsort and a
  `np.unique` over ~1.3M deltas (~80 ms), only to order ~100 groups.
- **A picker scan of all ~38,700 companies** per keystroke pause (38 ms here, ~140 ms on the Space).
- **A preset window never repeats.** "30 days" is measured back from the click's own millisecond,
  so ADR-0251 kept each answer under a key no later click could hit.
- **The round trip.** ~650 ms of every first view is the network, which no server speed-up can
  remove; only asking earlier can.

## Decision

1. **A family no band drill reads is one row a tick and metric** (`banded` in `_index_rows` and
   `_counts_at_charted_ticks`). A band drill keeps its family's bands, and its lineage's
   (`_lineage`): every name a successor link joins it to, since the rename can move rows into
   or out of it.
2. **The index's cells summed over every ATS are kept from load** (`_every_ats_cells`, ~7 MB); a
   no-ATS window is a slice of them.
3. **The replay orders groups without sorting every delta:** a dense key lookup in place of
   `np.unique`, each group's least `(tick, held first, held Board's first, file)` key found a key
   at a time, and `bincount` for the levels.
4. **The picker files candidates by the first letter of each word of their names**
   (`CandidatesByInitial`): every match starts a word with the query's first letter (a typo is
   never in the first letter), and an alias is read under its own.
5. **Kept answers are keyed by what the answer reads of a question** (`answer_key`): the ticks
   `since`, `until` and `base` fall between, and under New the tick a week before `since`, which
   its counting-change echo reads. Clicks of one preset between the same two ticks share one
   answer. `unnetted_answer` notes that a new reading of those three must be read there too.
6. **The boot also answers each charted category's levels** under both Measures: what a click on
   the opening view opens.
7. **The page asks earlier.** A bare `#trends`, or `#hot`, preloads its first answer from the
   head, beside `app.js`. Hiring now's ranking is prefetched a second after any page loads, as
   the opening Trends view already was. A category row the pointer or the focus rests on for
   100 ms has its levels asked for, and the picker's top company for a first pick (with no
   Source narrowed) has its trend asked for as the suggestions arrive, since that is what Enter
   picks. One such guess is out at a time, the latest wanted next: the Space works out one
   answer at a time (ADR-0251), so a queue of guesses would stand in front of the click.
8. **Every static file is named under the boot's version and kept for the boot**, like the
   answers: only a deploy changes one, and a deploy restarts the Space. It neither re-signs the
   session cookie nor varies by it. A repeat visit then reads its scripts from the browser,
   where each was revalidated before, a round trip every visit.

Every served figure is unchanged: 118 questions, the picker's suggestions and Hot's whole ranking
dump byte-identical to main's, 540 seeded random questions (companies, comparable coverage, ATS
filters, retired and unknown families, every stamp spelling) hash the same, 8,364 picker queries
suggest the same, and 204 pairs of questions keyed alike answer alike.

## Options not taken

- **A columnar rewrite of `unnetted_answer`.** After the above the opening view's answer takes
  ~55 ms here, of which ~20 ms is building rows; a rewrite of the answer's every pass for the rest
  is a larger change to the numbers' own code than the time left is worth next to a 650 ms round
  trip.
- **Cells per ATS kept from load:** 48 × 7 MB, for questions few ask.
- **Suggestions worked out in the browser:** the ranking (tiers, typos, aliases, twins) would live
  twice, in Python and JavaScript.
- **Filing candidates by two letters:** a typo may be in the second.
- **Rounding a preset's clock** so its URL repeats: it moves the window's edge, a change of what
  the window means. The answer key gives the Space the same sharing without it.
- **The opening answer inlined into the page:** it would ride every page load, whichever tab it
  opens on, and it cannot be drawn before `app.js` runs anyway. With the preload the answer
  already lands with `app.js` (measured: answer 1.33-1.53 s, `app.js` 1.35-1.54 s into the load).
  A stateful link (`#trends?company=…`) gets no preload: its query is `app.js`'s to build.
- **Aggregates kept per company:** dense cells by tick, family and band for ~38,700 companies
  are far past the Space's memory, and sparse ones are the delta ledger the replay already
  reads. A company's replay now takes ~20-30 ms here, and a company view once answered is kept.
- **Asking ahead for every charted drill on the page:** ~320 KB a visit for views few open; the
  boot answers them for everyone instead, and a pointer resting on a row asks for the one it will
  likely open.

## Consequences

Measured locally over the same history with the sign-in wall on, over HTTP/2 as the Space serves
it, its round trip and CPU simulated (every response held back by 650 ms plus 2.64x its own server
time; 20 Mbit/s down), main and this change run alternately:

Two fresh boots of each, the first visitor after the boot, figures on screen identical at
every step (ms; the machine was shared and loaded, so both sides are slow alike):

| | main | this change |
| --- | ---: | ---: |
| cold load of `#trends` to a drawn chart | 2,347 / 2,291 | 1,617 / 1,608 |
| a repeat visit to a drawn chart | 1,394 / 1,371 | 720 / 710 |
| first drill into a category | 3,243 / 2,388 | 720 / 712 |
| first drill into another, the pointer resting 300 ms | 2,750 / 2,610 | 487 / 477 |
| first 30-day window | 2,480 / 2,436 | 1,262 / 1,328 |
| first comparable-coverage view | 2,386 / 2,128 | 1,344 / 1,302 |
| company suggestions ("google") | 1,004 / 979 | 869 / 871 |
| first company picked | 1,057 / 980 | 788 / 794 |
| that company's New | 1,009 / 928 | 802 / 796 |
| Hiring now tab | 728 / 720 | 69 / 70 |
| bytes on the wire, cold load | 476 KB | 429 KB |

On this Mac's CPU alone (the Space's is ~3.6x slower), a first-time answer: 30-day window 352 to
64 ms, comparable 334 to 115, roles drill 469 to 89, one company 86 to 33, two companies 175 to
32; suggestions 33 to 3.4 ms. Boot 32.6 to 20.8 s, Hot's ranking 29.1 to 17.3 s, the opening
views and their drills 1.4 s (1.1 s for the views alone before). Peak memory 1,177 to 1,379 MB in
the boot process; the kept cells are 7.1 MB of it, and resident memory once the views are
answered was 1,027 MB before and 947-992 MB after, well inside the free tier's 16 GB.

What it costs: 7.1 MB of cells, ~200 MB more peak memory at boot, and the 16 drills at boot, which the
replay's own speed-up gives back several times over in Hot's ranking; ~11 KB a page load for the
prefetched ranking; one drill asked for per row a pointer rests on. A preset's answer is shared
only until a tick crosses its start, which is the point at which its figures really change.
