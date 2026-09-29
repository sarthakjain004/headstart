# ADR-0281: A Lever Board whose hosted pages are off serves nothing

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (Unauthoritative Boards),
[ADR-0058](0058-consecutive-gone-quarantine.md) (gone quarantine),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (grace period),
[ADR-0200](0200-a-board-scraped-empty-is-in-the-eviction-scope.md) (an empty Board is in scope) ·
**Issue:** #700

## Context

Lever's postings API can go on listing a Board's openings after the company has switched its
hosted pages off. Then `jobs.lever.co/{slug}` and every posting's `hostedUrl` answer 404. We serve
`hostedUrl` as the Job's link, so every row of such a Board is a dead link. There is no working
link to swap in: `applyUrl` 404s too (12 of 12 postings tried, across 6 Boards).

Measured 2026-09-29 on served table v298 (16,270 Lever rows on 1,309 Boards). One served posting
URL was fetched per Board, and the board page was fetched wherever that posting did not answer 200.
**55 Boards / 325 served rows answered 404 on both.** The biggest were `latitudeinc` (69 rows),
`aircall` (35) and `fresha` (23). Six of the 55 are mixed-case slugs, and their pages 404 in both
casings. `veeva` is the exception: its board page 404s, but its postings answer 200 (932 listed).
`jobgether` is the reverse: one served posting 404s, but its board page answers 200.

Issue #700 counted 79 Boards / 368 rows on 2026-09-25, from the 223 Lever Boards then serving a slug
as their company. The per-Board row counts agree (`latitudeinc` 69, `aircall` 35, `fresha` 23), so
the gap is in which Boards are still served. ADR-0250 evicted Dormant Boards' rows on 2026-09-28,
and hosted pages off is common among them: 43 of the 104 Lever Boards the latest run judged
Dormant. `momenti-inc` (11 rows in the issue) fits: its API still lists 14 postings, the newest
from 2023-06-28, and it serves no row. The issue did not record its 79 Boards by name, so the rest
of the gap cannot be traced Board by Board.

## Decision

`LeverScraper.fetch_raw` checks every scrape. It asks for the board page, and only when that
answers 404 does it ask for the first listed posting's `hostedUrl`. If both answer 404, it logs
`hosted pages disabled` for the Board and returns `[]`.

- **`[]`, not a raise.** The listing did answer. So the Board lands in `boards_ok` and in the
  eviction scope (ADR-0200), and its rows evict through the normal path: Unconfirmed on the first
  such scrape, evicted on the second (ADR-0083). The Board is not marked truncated, because an
  Unauthoritative Board keeps its rows served (ADR-0053). It is not a gone-strike either. A raise
  keeps the Board out of the eviction scope, and ADR-0058 quarantine would stop scraping a Board
  whose API still answers.
- **Only a 404 on both pages counts.** A 5xx, a 429 or a request that raises reads as enabled,
  so the Board is served as before. Each page gets one attempt (`_fetch_once`, the company-name
  fetch). A single false 404 costs nothing, because eviction needs two consecutive scrapes.
- **It comes back on its own.** The check runs on every scrape. A Board that turns its pages back
  on is served again the next time it is in a run's Slice.
- **Reuses the company-name fetches.** `resolve_company` already asks for the board page on
  every Board still named by its slug, and for a posting page when that 404s. `LeverScraper`
  asks each hosted page once per scrape (`_hosted_page`), and its `_fetch_once` answers a plain
  GET from there, so those Boards spend no extra request. Any other request (a stream, another
  method or Accept) is not answered from it. A Board that already has a real name (a mixed-case
  or curated one) spends one more GET per scrape, or two if its board page 404s.

## Rejected

- **Serve `applyUrl` instead.** It 404s too.
- **Ask every posting, or more than one.** Asking every posting is one request per Job, and
  hosted pages are a per-Board setting. One posting stands for the Board once the board page has
  already 404'd. Measured 2026-09-29: every served Lever Board's board page was fetched (1,310
  Boards, served table version 45), and so was each of the 104 Lever Boards the latest run
  judged Dormant (ADR-0250). 97 Boards had a board page that 404s and a listing that answers. For each,
  the first listed posting and up to four more spread across the listing were fetched: 310
  postings. On 96 Boards every one answered 404. On `veeva` every one answered 200. No Board had
  a first posting that 404s while another answered, so a second posting would change nothing.
- **Park the Board or mark its ledger row dead.** Nothing would bring it back when the company
  turns its pages on again, and the liveness probe reads the API, which still answers.

## Consequences

- The patched scraper, run live on the 55 Boards on 2026-09-29, served nothing from 53 of them:
  **312 served rows** (v298) leave the index over each Board's next two scrapes. The other two,
  `autoroboto` (11 rows) and `menlovc` (2), now answer 404 at the API on both instances, so the
  scraper already raises them as gone (ADR-0058). This change does not touch them.
- The controls are unchanged: `veeva` 932 Jobs, `spotify` 81, `palantir` 321. So is the request
  count on a Board named by its slug.
- Each such Board logs one INFO line per scrape (ADR-0039) naming both URLs and the listed count.
- `scripts/eval/verify_filters.py` already failed a run on a 404 link, but it probed the top three
  rows of one query per ATS. Run live on 2026-09-29, that probed 3 Lever links on 2 Boards and
  found none dead. It now probes one row per Lever Board across its whole query battery: 243 of
  the 1,310 served Lever Boards, 15 of them dead. That catches this failure class, for example the
  scraper's check regressing, but not every such Board: a single Board is in the sample only if
  it ranks for one of the queries.
