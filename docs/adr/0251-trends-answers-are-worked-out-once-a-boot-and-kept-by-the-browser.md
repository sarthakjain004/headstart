# ADR-0251: Trends answers are worked out once a boot and kept by the browser

**Status:** accepted, amended by [ADR-0261](0261-a-trends-view-nobody-has-asked-for-is-cheap-to-work-out-and-asked-for-ahead.md) (answers kept by what they read of a question; asked for ahead on intent too), and by [ADR-0269](0269-every-trends-control-is-answered-from-the-browser.md) (a preset window is one URL a boot and asked ahead; 512 answers kept) · **Date:** 2026-09-28 · **Relates to:** [ADR-0020](0020-free-tier-deployment.md) (the free-tier Space), [ADR-0042](0042-signed-in-ui-saved-sets.md) (the sign-in wall and its session cookie), [ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md) (the history the Space reads once at boot), [ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the reading every answer carries)

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
  seen costs a full round trip (~650 ms from here, `/me` TTFB) plus the work again. And behind
  the sign-in wall nothing *could* be: Flask re-signs a permanent session's cookie on every
  response and marks it `Vary: Cookie`, so a browser keying a kept answer on a cookie that changes
  each second never finds it again.
- **Hot's boot ranking rebuilt a question-independent array per company.** `_replay_rows` looked
  up every Board's `new` hold (~45k lookups) on each of 2,341 questions — a third of the ranking.

## Decision

1. **A `/trends` body is worked out once a boot and kept** (`_served_trends` in the Space's
   `app.py`): JSON and gzip bytes, keyed on the history and the question, the 128 most recently
   asked for. A history never changes once loaded, and every pipeline publication, index cleanup
   and deploy restarts the Space, so nothing kept can go stale. Answers are worked out one at a
   time under one lock, and a kept answer is read without it: under the GIL two at once finish no
   sooner, and a click that repeats a prefetch still in flight waits for that answer instead of
   working it out beside it. A question that raises is not kept.
2. **The opening view is answered at boot, under both Measures** (~3 s of the Space's CPU).
3. **Answers are versioned by boot.** The page gets `answers_version` (a boot token) in
   `window.CFG` and sends it as `v=` on `/trends`, `/hot` and `/companies/suggest`; an answer
   asked for under the current version is `Cache-Control: private, max-age=31536000, immutable`,
   and neither re-signs the session cookie nor varies by it (`_AnswersLeaveTheSessionAlone`): it
   is the same for every Account, and every other response still slides the session forward. The
   next boot gives the page a new version. A request without it, or with an old one, is answered
   as before.
4. **JSON answers and static scripts and stylesheets are gzipped** where the browser accepts it;
   a static file's ETag is weakened, so a revalidation still answers 304.
5. **The page asks ahead for what the reader is about to ask for**: the opening view a second
   after any page loads, and the other Measure of the view just drawn a second after it is drawn.
   A load before that second is up drops the prefetch unsent, since the Space works out one answer
   at a time and a prefetch being worked out would sit in front of the reader's own click. Never
   for a preset window, whose URL is measured back from each click's moment and so never repeats.
6. **`TrendHistory` reads each Board's `new` hold as a tick index once** (`_hold_ticks`), keyed
   on the mapping it was read from, rather than once per question.

## Options not taken

- **Revalidation by ETag** (`no-cache`) instead of a boot version: every revisit still costs a
  round trip, ~650 ms, which was most of what a revisit cost.
- **A client-side map of answers**: works only within one page view, and needs its own bound and
  invalidation; the browser's cache does both and survives a reload.
- **Oldest-out-first eviction** (an `lru_cache` or plain FIFO): a preset window's `since` is a new
  millisecond on every click, so presets would push out the opening views everyone asks for. And
  `lru_cache` works an answer out twice when a click races the prefetch of the same view: the
  server log showed both reaching it.
- **Keeping preset windows** by canonicalising `since` to the first tick at or after it: exact
  for All openings, not for New, whose counting changes are filtered on `since` minus a week.
  Rounding the page's clock instead would move a window's edge, which is a change of meaning.
- **Prefetching at `requestIdleCallback`**: it fired at paint, and a reader's quick next click
  then waited behind the prefetch being worked out (a drill 2.1 s → 2.6 s); after a second, it
  costs a quick clicker nothing.
- **A compressing middleware or a new dependency**: the Space's requirements are pinned on
  purpose, and the two places that compress are a few lines each.
- **Precomputing every drill at boot**, or **vectorising the index path**: the first is ~90 s of
  the Space's CPU per boot for views few open; the second is the real cure for a view nobody has
  asked for this boot, and a larger change to the numbers' own code than this one.

## Consequences

Measured locally over the same history, the sign-in wall on, the Space's round trip and CPU
simulated (every response held back by 650 ms plus 2.64x its own server time; 20 Mbit/s down),
figures on screen identical before and after:

| | before | after |
| --- | ---: | ---: |
| cold load of `#trends` to a drawn chart | 4.54 s | 2.74 s |
| opening the Trends tab from Home | 2.37 s | 0.09 s |
| Measure toggle (a reader who stayed 1.5 s) | 2.50 s | 0.06 s |
| Measure toggle, clicked at once, first visitor after a boot | 2.37 s | 1.09 s |
| returning to any view already seen | 2.2–2.6 s | 0.06 s |
| a view asked for already this boot, by anyone | 1.7–2.2 s | 0.71 s |
| a view nobody has asked for this boot | 1.7–2.2 s | unchanged |
| bytes on the wire for a cold load | 1.47 MB | 0.38 MB |
| Hot's boot ranking (this Mac) | 36.4 s | 24.9 s |

Every served figure is unchanged: the served `/trends` payloads of 118 questions, the company
suggestions and Hot's whole ranking dump byte-identical before and after, and the tests assert a
kept or gzipped answer is the answer worked out afresh.

What it costs: up to ~100 MB of kept answers at the worst; ~3 s more boot for the two opening
views, which Hot's ranking more than gives back; ~113 KB once a boot per browser for the
prefetched opening view, on whatever tab the page opens on; the other Measure (~100 KB) on views
the reader leaves without toggling. A tab left open across a restart shows the old boot's answers
for views it has already seen, beside the new boot's for the rest, until it reloads. A first view
of a question nobody has asked for this boot — a drill, a company, a range — still costs the
Space's work once.

Tests that change a loaded history in place must forget the kept answers
(`tests/test_space_app.py`'s `_forget_trends_answers`), since the Space never does.
