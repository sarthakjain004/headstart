# ADR-0184: A Pinpoint Board is read from its listing and dated from its posting pages

**Status:** accepted · **Date:** 2026-09-23 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail — deliberately not applied here), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (the name comes off the board page), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

Pinpoint (`{slug}.pinpointhq.com`) was the strongest tech share of the candidate ATSes in a
20-ATS evaluation of a third-party scraper library's dataset (14.6% of 11,272 jobs, on an older
tech filter). A third-party
implementation (kalil0321/ats-scrapers) and its 406-slug seed list existed. Measuring before
building (2026-09-23; `docs/pinpoint/2026-09-23_postings-api-measurement.md`) confirmed its
endpoint and falsified several of its assumptions. Most importantly, the `first_published_at`
date it reads does not exist on any of 13,419 listing rows.

## Decision

1. **The slug is the lowercased subdomain label.** There is one host and no regional pod. The
   board is case-insensitive (`CINVEN` serves `cinven`), and the seed list spells one tenant
   `Cinven`, so `slug_from` lowercases. The native id is the posting UUID: the listing's numeric
   `id` addresses no page. Links are built on the vendor host. 158 of 692 Boards with postings
   send a browser from there to their vanity host at the same path, which serves the posting.

2. **The listing is the Board.** `GET /postings.json` returns every posting in one array, with no
   pagination, no parameters and no stated total. It matched the tenant's own sitemap on the
   largest Boards (932 of 932). It carries the full body in four HTML sections, which are exactly
   what the posting page's JSON-LD concatenates, with no cap.

3. **The posting page is fetched for `posted_at` and the country, on every run.** The date exists
   only in the page's JSON-LD `datePosted`. It was present on 76 of 76 pages and stable across
   refetch. The sitemap's `<lastmod>` disagreed with it on 36 of 76, always later, so it is not
   used. **ADR-0048's skip of the already-described is declined** because the description comes
   from the listing. A description-store hit therefore says nothing about the date, and skipping
   the page would blank `posted_at` on every run after the first. The page is asked for as
   `text/html`. The shared `_get`'s `application/json, text/html` gets a 406 from every page.

4. **The ADR-0166 tech gate runs before the page pass, as an exact site.** `title` and
   `job.department.name` are listing fields on 13,419 of 13,419 rows, and the page overrides
   neither. At 12.1% tech it spares seven of every eight page fetches. On the rare spurious empty
   listing (12 of 6,030 fetches returned `{"data":[]}` for a Board with postings), the scraper
   asks once more, because a Board read as empty puts all of its Jobs one absence from eviction.

5. **Liveness asks what a user's click would get.** The JSON is served whether or not a user can
   open the postings, and the pages content-negotiate: they render for `Accept: */*`, but for
   `text/html` (a browser) they can 404 or redirect away. 17 Boards with postings (1,200
   postings) 404 a browser, and 3 more (73 postings) redirect their postings to a company page. So
   `p_pinpoint` rules:
   - A listing 404 is **dead**.
   - A listing 301 to another `{label}.pinpointhq.com` (a renamed tenant) is **dead**.
   - An empty listing is asked once more before it is believed.
   - With postings, the first and last postings' pages are asked as `text/html` without following
     redirects. Two are asked because one posting can close between the listing and its page. The
     Board is **live** with the listing's count if either lands: a 200, or a redirect that keeps
     the posting's path (the vanity host, 158 Boards). Otherwise it is **dead**. `/` is not asked
     here: `kharon` 404s a browser on `/` while its postings render.
   - Empty: `/` decides. A 200 is **live** 0. A 404, or a redirect to another ATS or a company
     site, is **dead**.

   This rule goes beyond the standing decisions the build started from, and the coordinator
   signed it off on 2026-09-23 on one principle: serving postings whose links a user cannot open
   is worse than not serving them. Measured kills: **20 Boards with postings (1,273 postings)**
   whose postings 404 a browser (17 Boards, 1,200 postings) or redirect to a page that is not
   the posting (3 Boards, 73 postings); and **387 empty Boards** whose `/` returns 404 (282) or
   redirects off the platform (105).

   `pinpointhq.com` is a spanning gate at 16 in flight. A 256-wide burst across tenants drew
   connection refusals that then held against every tenant for minutes. Paced load up to 50 req/s
   was clean.

6. **It ships active.** The committed ledger holds 1,465 rows: 817 live, 646 dead, 2 unknown.
   After six confirmed test tenants go into `config.EXCLUDED_BOARDS`, 666 Hiring Boards remain,
   with 18,345 postings. A full walk is 140.1 MB of listings plus 2,212 tech pages × 131.9 KB
   (291.7 MB). That is **~432 MB for 2,212 tech Jobs, ~195 KB per tech Job**, about a tenth of
   ADR-0158's ~2 MB bar.

## Alternatives considered

- **Listing only, no page pass.** At ~63 KB per tech Job it is cheaper still, and every field but
  the date and country arrives with the listing (140.1 MB / 2,212). Rejected because Pinpoint Jobs would have had no
  `posted_at` at all, so they could never sort or filter by date. The page pass costs ~0.29 GB per
  full walk and stays far under the bar.
- **Page pass only for postings not yet described (ADR-0048).** Rejected: after the first run it
  fetches nothing, and `posted_at` goes blank (Decision §3).
- **`<lastmod>` from the tenant sitemap as the date.** One extra request per Board instead of one
  per tech posting. Rejected: it is a modification date (wrong on 36 of 76).
- **Liveness on `Accept: */*` alone, as the census first measured.** Rejected: it calls 20 Boards
  live whose links are dead to users, and it would call their 1,273 postings servable.
- **Liveness on `/` asked as a browser** (this PR's first version). Rejected: `/` and the postings
  disagree. `kharon` 404s on `/` while its postings render, and `10kai` answers `/` with a 302 to
  its programme page, like any vanity host.

## Consequences

- Pinpoint joins the exact-gate list in CONTEXT.md's **Detail pass** entry.
- The date and country depend on one extra request per tech posting. A failed page is a counted
  detail gap; the Job still ships, undated.
- Common Crawl was **not measured**: `index.commoncrawl.org` was unreachable for the whole
  discovery slot on 2026-09-23. The pool comes from the harvest, the seed list, a full Wayback
  sweep (+904 new tenants) and 6 redirect targets. The `cc_miner` pattern is wired, so a later
  sweep needs no code.
- The connection wall's exact trigger is not pinned down. It was seen after the 256-wide burst
  and once, briefly, after a gated pass. If a scrape shard meets it, the listing fails as a Board
  failure, which evicts nothing, and pages fail as detail gaps.
