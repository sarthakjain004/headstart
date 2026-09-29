# ADR-0309: Hiring now's Rate ranks only companies that grew

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) (Hot and its
Lenses), [ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
(Opened and Closed), [ADR-0230](0230-trends-keeps-one-board-delta-history-and-decides-rules-when-reading-it.md)
(Rate as Opened over openings now), [ADR-0321](0321-an-agent-reads-hiring-now-by-opened-less-closed-and-every-trend-says-its-turnover-span.md)
(the Opened less closed Lens) · **Issue:** #835

## Context

The Rate Lens ranks a company by the jobs it **Opened** in the trailing week as a share of its
openings now. Issue #835 found it led by churn: New York Life ranked first at 1,665% on a net
change of −26. The issue named three ways out:

1. require net growth;
2. require a minimum size;
3. show opened and closed beside the rate.

#855 shipped a fourth rule and called it option 1: it left out a company whose closures were not
counted. That removed New York Life, whose closures were not counted. It left every company whose
closures were counted, however little it grew. Measured on the Space's `/hot` for the week to
2026-09-29 06:19 (the local ranking on the same HF state matched it exactly):

- **30 of Rate's 100 rows had a net change of 0 or less.** CSB was second at 60% (27 opened, 27
  closed, net 0). Jobgether was fifth at 45% (715 opened, 893 closed, net −32). Bluelight
  Consulting was ninth at 41% (505 opened, net −101). Clera was tenth at 35% (net −13).
- None of those 30 had its closures uncounted, so #855's rule could not reach them.
- The MCP `hiring_now` answer already showed opened and closed on every row (option 3), and Rate
  already had the site's 25-opening floor (option 2), with rows under 50 flagged as a small base.
  Neither kept churn off the top.

## Decision

1. **Rate ranks only a company whose net change over the week was above 0** (option 1). The net
   change is the row's `net`: the company's own trend line with the steps netting can size taken
   out, the figure Expansion ranks by. Expansion already ranks only a net above 0, so Rate is now
   a re-ordering of the companies that grew.
2. **#855's rule stays.** A company whose closures were not counted is still left out, since its
   Opened may be the same jobs listed again. Option 1 alone would bring back 26 companies that grew
   with their closures uncounted, Cboe first: +16 net on 16 opened against 38 open now, a 42%
   rate.
3. **Both exclusions are counted, apart.** `/hot`'s `counts` gains `not_growing`, the companies
   with a rate whose net change was 0 or less. `closures_uncounted` stays as it was, and the two
   are disjoint: a company whose closures were not counted has no rate. The companies stay on
   Volume, and on Expansion where they grew.
4. **Said as a part of the companies ranked.** `hiring_now` said "Ranked 2,196 companies; not
   ranked: … 334 whose closures were not counted", but the 2,196 already held the 334. On Rate it
   now says "Of those 2,196, rate leaves out 334 whose closures were not counted …; 446 whose
   net change was 0 or less …". `opened_less_closed` (ADR-0321) said its own exclusions the same
   wrong way, and now says them the same way. The Hot tab's note says Fastest leaves out
   companies that didn't grow.

## Measured

`/hot` ranked with and without the rule on the HF state of 2026-09-29 09:25, which the Space
served (its `/hot` matched the ranking without the rule, row for row):

- Rate: the 31 rows with a net of 0 or less left (CSB, Jobgether, Envision Employment Solutions,
  Bluelight Consulting, Clera and 26 more), and 31 companies with rates of 10–13% entered at the
  bottom. No row with a net of 0 or less is left. The new top five are Starbucks (70%, +50), RadNet
  (48%, +10), Twenty (46%, +5), AgileEngine (44%, +100) and o-reilly-auto-parts (43%, +14).
- `not_growing` is 457 of the 2,221 companies ranked, and `closures_uncounted` 345.
- Expansion, Opened less closed and Volume are identical, row for row.

## Alternatives considered

- **Option 2, a larger minimum size.** Churn is not a small-company effect: Jobgether (1,578
  openings) and Bluelight (1,220) churned. A larger floor would drop the small companies Rate
  exists to surface.
- **Option 3 alone, opened and closed beside the rate.** The MCP answer and the page already
  show them. The order was still led by churn.
- **Rank by opened less closed above 0 instead of the net.** It needs closures counted on every
  Board, or a partial closed count lifts it. Bluelight read +404 on it (505 opened, 101 closed)
  against a net of −101, so it would have stayed. ADR-0321 ranks that figure as a Lens of its
  own, `opened_less_closed`.
- **Option 1 in place of #855's rule.** Cboe (+16 net, closures not counted) would return, with 25 more.

## Consequences

- Rate can list fewer than 100 rows when fewer companies grew in a week.
- A company can grow its net by re-counting that netting could not size and still rank. AgileEngine
  read +100 on 311 opened and 326 closed. That is Expansion's weakness too, and `hiring_now` flags
  such a row as not backed by its postings opened.
