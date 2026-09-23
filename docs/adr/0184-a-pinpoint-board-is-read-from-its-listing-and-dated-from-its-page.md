# ADR-0184: A Pinpoint Board is read from its listing and dated from its posting pages

**Status:** accepted · **Date:** 2026-09-23 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail — deliberately not applied here), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (the name comes off the board page), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

Pinpoint (`{slug}.pinpointhq.com`) was the strongest tech share of the candidate ATSes evaluated
in `experiment/ats-scraper-candidates/LOG.md` (14.6% on an older filter). A third-party
implementation (kalil0321/ats-scrapers) and its 406-slug seed list existed. Measuring before
building (2026-09-23; `docs/pinpoint/2026-09-23_postings-api-measurement.md`) confirmed its
endpoint and falsified several of its assumptions. Most importantly, the `first_published_at`
date it reads does not exist on any of 13,419 listing rows.

## Decision

1. **The slug is the lowercased subdomain label.** There is one host and no regional pod. The
   board is case-insensitive (`CINVEN` serves `cinven`), and the seed list spells one tenant
   `Cinven`, so `slug_from` lowercases. The native id is the posting UUID: the listing's numeric
   `id` addresses no page. Links are built on the vendor host. 160 of 691 hiring Boards send a
   browser from there to their vanity host at the same path, which serves the posting.

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
   neither. At 12.0% tech it spares seven of every eight page fetches.

5. **Liveness asks the board page the way a browser does.** The JSON is served whether or not a
   careers site is published. `/` content-negotiates: it renders for `Accept: */*`, but for
   `text/html` it can 404 or redirect off the platform. 17 hiring Boards (1,224 postings) list
   postings that 404 for every browser. So `p_pinpoint` rules:
   - A listing 404 is **dead**.
   - A listing 301 to another `{label}.pinpointhq.com` (a renamed tenant) is **dead**.
   - Otherwise `/` is asked as `text/html`, without following redirects:
     - 200 is **live** with the listing's count.
     - 404 is **dead**.
     - A redirect is **live** on a Board with postings (its vanity host) and **dead** on an empty
       one, whose target is usually another ATS or a company site.

   `pinpointhq.com` is a spanning gate at 16 in flight. A 256-wide burst across tenants drew
   connection refusals that then held against every tenant for minutes. Paced load up to 50 req/s
   was clean.

6. **It ships active.** The committed ledger holds 1,465 rows: 819 live, 644 dead, 2 unknown.
   After six confirmed test tenants go into `config.EXCLUDED_BOARDS`, 668 Hiring Boards remain,
   with 18,364 postings. A full walk is 139.4 MB of listings plus 2,205 tech pages × 131.9 KB
   (290.8 MB). That is **~430 MB for 2,205 tech Jobs, ~195 KB per tech Job**, about a tenth of
   ADR-0158's ~2 MB bar.

## Alternatives considered

- **Listing only, no page pass.** At ~63 KB per tech Job it is cheaper still, and every field but
  the date and country arrives with the listing. Rejected because Pinpoint Jobs would have had no
  `posted_at` at all, so they could never sort or filter by date. The page pass costs ~0.29 GB per
  full walk and stays far under the bar.
- **Page pass only for postings not yet described (ADR-0048).** Rejected: after the first run it
  fetches nothing, and `posted_at` goes blank (Decision §3).
- **`<lastmod>` from the tenant sitemap as the date.** One extra request per Board instead of one
  per tech posting. Rejected: it is a modification date (wrong on 36 of 76).
- **Liveness on `Accept: */*` alone, as the census first measured.** Rejected: it calls 17 Boards
  live whose links are dead to users, and it would call their 1,224 postings servable.

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
