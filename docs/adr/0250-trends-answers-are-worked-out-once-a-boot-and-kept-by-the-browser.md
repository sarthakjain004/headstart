# ADR-0250: Trends answers are worked out once a boot and kept by the browser

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:** [ADR-0020](0020-free-tier-deployment.md) (the free-tier Space), [ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) (the history the Space reads once at boot), [ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the reading every answer carries)

## Context

Every Trends interaction that asks the Space was slow, and the time was not where it looked
(issue #755, measured 2026-09-28 against the freshly pulled history: 990 ticks, 7.56M index rows,
1.37M Board deltas):

- **The answer is worked out on every request.** The index-wide opening view costs ~0.4 s of
  this Mac's CPU (`unnetted_answer` builds and walks 456k row dicts); the Space's CPU is ~3.6x
  slower (it ranked Hot in 137.0 s at its 05:36 boot against 37.6 s here), so ~1.5 s — for every
  visitor, every time, although the history cannot change until the next boot.
- **Nothing is compressed.** The Space's proxy passes responses on as the app sends them
  (`/` answered 13,266 bytes with no `Content-Encoding` to a gzip-accepting request). The opening
  view is 483,729 bytes of JSON, 112,802 gzipped; the page's scripts and stylesheets 0.92 MB.
- **Nothing is cached.** `/trends` carries no `Cache-Control`, so going back to a view already
  seen costs a full round trip (~650 ms from here, `/me` TTFB) plus the work again.
- **Hot's boot ranking rebuilt a question-independent array per company.** `_replay_rows` looked
  up every Board's `new` hold (~45k lookups) on each of 2,341 questions — a third of the ranking.

## Decision

1. **A `/trends` body is worked out once a boot and kept** (`_served_trends` in the Space's
   `app.py`): JSON and gzip bytes, keyed on the history and the question, at most 128 kept,
   oldest out first. A history never changes once loaded, and every pipeline publication and
   deploy restarts the Space, so nothing kept can go stale. Answers are worked out one at a time
   under one lock, and a kept answer is read without it: under the GIL two at once finish no
   sooner, and a click that repeats a prefetch still in flight waits for that answer instead of
   working it out beside it. A question that raises is not kept.
2. **The opening view is answered at boot, under both Measures** (~3 s of the Space's CPU).
3. **Answers are versioned by boot.** The page gets `answers_version` (a boot token) in
   `window.CFG` and sends it as `v=` on `/trends`, `/hot` and `/companies/suggest`; an answer
   asked for under the current version is `Cache-Control: private, max-age=31536000, immutable`.
   The next boot gives the page a new version, so the browser can keep answers for good without
   ever serving one from another boot to a page from this one. A request without it, or with an
   old one, is answered as before, uncached.
4. **JSON answers and static scripts and stylesheets are gzipped** where the browser accepts it;
   a static file's ETag is weakened, so a revalidation still answers 304.
5. **The page asks ahead for what the reader is about to ask for**: the opening view once the
   page has loaded, and the other Measure of the view just drawn. Only after a second, and taken
   back by the next load, because the Space works out one answer at a time and a prefetch being
   worked out would otherwise sit in front of the reader's own click. Never for a preset window,
   whose URL is measured back from each click's moment and so never repeats.
6. **`TrendHistory` reads each Board's `new` hold as a tick index once** (`_hold_ticks`), keyed
   on the mapping it was read from, rather than once per question.

## Consequences

Measured locally over the same history, the Space's round trip and CPU simulated (every response
held back by 650 ms plus 2.64x its own server time; 20 Mbit/s down), median of 3 runs, byte-
identical figures on screen before and after:

| | before | after |
|---|---:|---:|
| cold load of `#trends` to a drawn chart | 4.52 s | 2.84 s |
| opening the Trends tab from Search | 2.56 s | 0.08 s |
| Measure toggle (a reader who stayed 1.5 s) | 2.34 s | 0.07 s |
| returning to any view already seen | 2.2–2.5 s | 0.06 s |
| a view nobody has asked for this boot | 1.6–2.4 s | unchanged |
| a view another visitor already asked for | 1.6–2.4 s | 0.7 s |
| opening view on the wire | 484 KB | 113 KB |
| Hot's boot ranking (this Mac) | 37.6 s | ~23 s |

Every served figure is unchanged: the served `/trends` payloads of 118 questions, the company
suggestions and Hot's whole ranking dump byte-identical before and after, and the tests assert a
kept or gzipped answer is the answer worked out afresh.

What it costs: up to ~100 MB of kept answers at the worst, ~3 s more boot for the two opening
views (Hot's ranking gives back far more), and the prefetched other Measure (~100 KB gzipped) on
views the reader leaves without toggling. A first view of a question nobody has asked for this
boot — a drill, a company, a range — still costs the Space's work once; making that cheaper is the
index path's own work (456k row dicts per index-wide question), not a cache's.

Tests that change a loaded history in place must forget the kept answers
(`tests/test_space_app.py`'s `_forget_trends_answers`), since the Space never does.
