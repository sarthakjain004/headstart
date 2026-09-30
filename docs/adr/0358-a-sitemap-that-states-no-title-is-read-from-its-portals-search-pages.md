# ADR-0358: A sitemap that states no posting's title is read from its portals' search pages

**Status:** accepted · **Date:** 2026-09-29 · **Amends:** [ADR-0245](0245-an-avature-board-is-its-tenant-host-read-through-its-portal-sitemaps.md) (the sitemap is the listing and its slug the gate's title; the `SearchJobs` walk it rejected is now read where the sitemap states no title) · **Relates to:** [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (truncation leaves a Board out of eviction scope), [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a negligible shortfall stays authoritative), [ADR-0145](0145-the-value-gate-reads-the-measurement-that-kept-up.md) (a measured zero freezes a Board for 14 days), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

ADR-0245 read every Avature Board through its portals' sitemaps and gated the job pages on the
title in each URL's slug. It rejected the `SearchJobs` walk: one sitemap fetch lists every posting,
and the search page serves 6 to 12 rows a request. The audit of 2026-09-29 counted 30 Boards whose
last scrape returned no Job although the ledger holds their postings. A live read of all 30
(`docs/avature/2026-09-29_zero-read-boards.md`) found that three read zero because the sitemap
states no title for the gate:

- **The sitemap lists page names, not postings** (Siemens). Its `externaljobs` sitemap lists 58 URLs
  and no `JobDetail/{id}`, while its search page states "1 - 6 of 999+ results" and links a posting
  on each row. Its postings are `…/JobDetail/524237`, with no slug either. The sitemap does name a
  bare `…/JobDetail` page, the job template, which a portal for onboarding or events (Epic's
  fifteen) does not.
- **The slug is no title** (mt, ucsf, pomerleau). mt's 531 postings are `…/JobDetail/1/{id}`, ucsf's
  949 are slugged by location (81% "San Francisco CA United States"), pomerleau's by a number.
  The gate reads each as a non-tech title, so 1,812 postings read as zero Jobs.

The other causes are not this ADR's to fix and the doc records each: hosts behind a WAF challenge
(IBM, CBRE, Jacobs), Boards whose listing is readable and holds no tech title, Boards frozen by
ADR-0145's 14-day rule on a zero measured before #880, and a listing that is not `SearchJobs`
(Two Sigma's custom `OpenRoles` page, whose `SearchJobs` answers 404).

## Decision

**When the slugs of a Board's sitemaps state no title, the search page of each of its public job
portals is paged.** A job portal is one whose sitemaps list postings or name a bare `/JobDetail`
page. The slugs state no title when fewer than half have a letter, or when one slug names half or
more of a Board of 20 postings or more. The 50% threshold was validated on 27 Boards only, the
ones probed with 10 postings or more: the rule held for 24 and failed for exactly mt, pomerleau
and ucsf. A titled Board wrongly judged untitled costs search requests and nothing else, since
the sitemap's rows stand where the search yields none. A Board whose slugs state titles never asks,
and neither does one that read an empty body (#880's rule settles that: unread, not empty). Where
the search page yields nothing, the sitemap's rows stand.

- **Paging follows the page's own next link.** The paging item is `paginationNextLink`, on a
  wrapping `<li>` (Siemens), on the `<a>` itself (Two Sigma), and with its `href` before or after
  the class (mt); the offset parameter is the tenant's own (`folderOffset`, `jobOffset`) and so is
  the page size (Siemens serves 6 whatever is asked). A page with no new id ends the listing.
- **A result's title is its header's.** The row is `{id, url, title}` from the `__text__title`
  header's link, the two templates measured (`article__header__text__title` on Siemens, Two Sigma
  and a2milkkf; `list__item__text__title` on mt), and the title goes where the slug's went, into the
  tech gate.
- **`robots.txt` is read the way Avature writes it.** A tenant whitelists each portal
  (`Allow: /careers`, `Allow: /*/careers`) and ends on `Disallow: /`, so a request is allowed by
  the longest matching rule, `*` and a closing `$` as wildcards. `urllib.robotparser` takes the
  first match and has no wildcard, and would refuse the locale path a search page lands on. Each
  page is checked against the robots.txt of the host it is fetched from: a vanity host
  (`careers.mt.com`, `jobs.siemens.com`) states its own, read once on first use (one request), and
  the tenant host's does not speak for it. A host with no robots.txt (a 4xx) states no rule; one
  that did not answer (a 5xx, a challenge) is read as disallowing everything. A page robots.txt
  disallows is not asked, first or later, and truncates the Board: one that may not be read is not
  one with nothing open.
- **A listing that cannot be known complete is truncated (ADR-0053).** A total stated with a plus
  ("999+ results"; Avature serves no page past the 2,000th result), a total read short of by more
  than ADR-0121's tolerance, a later page that failed, and a listing still paging at 400 pages. A
  first page that did not answer (`406`, `429`, `5xx`, `202`) leaves the Board unread, as #880
  does for an empty sitemap; a gone (`404`) page or one landing on a login lists nothing.

## Alternatives considered

- **Walk `SearchJobs` on every Board** (ADR-0245's rejected alternative). Still rejected: 30
  requests for Bloomberg's 352 against 1, for titles its slugs already state.
- **Fetch every job page of a Board whose slugs state no title.** mt's 531 pages at the Board's pace
  against 53 search pages for the titles, and ucsf's 949 against about 95.
- **Page the search whenever a sitemap read empty.** #880's rule is that an empty body says nothing;
  a page read then would make a flake look like a complete listing.
- **A per-Board listing page for Two Sigma** (`OpenRoles`, 10 a page, about 52 postings). It is one
  Board and one tenant's page name, the `_FIXED_FACETS_BY_SLUG` shape; left to the owner.
- **A browser for the WAF hosts.** A challenge is not a client-shaped wall to be routed around
  (IBM, CBRE and Jacobs answer `202` with `x-amzn-waf-action: challenge` to a plain client), so
  nothing here reads them.

## Consequences

Measured on the live Boards on 2026-09-29 through the scraper's own listing code, with the detail
pass stubbed out (`docs/avature/2026-09-29_zero-read-boards.md`):

| Board | Rows read | Pass the tech gate | Requests | Truncated |
|---|---:|---:|---|---|
| `siemens` | 2,001 | 446 | 335 search + 16 | yes, "999+ results" |
| `mt` | 520 | 72 | 54 search + 11 | yes, 520 of 531 |
| `pomerleau` | 332 | 12 | 56 search + 9 | no |
| `ucsf` | none (949 sitemap rows stand) | 0 | 1 search + 15 | no |

- **530 postings pass the gate where none did, and 446 of them are Siemens, which is not one of the
  audit's 30 zero-read Boards** (its ledger row says `live`, 0 postings). Within the 30, only mt (72)
  and pomerleau (12) recover: 84 tech-gated postings against the 12,913 the ledger holds there. ucsf
  reads nothing: its search page renders no result on the server, so its 949 location-slugged
  postings stay unread, and fetching their job pages is the owner's call. The cost is 445 search
  requests over the three Boards (~3.5 s each, about 20 minutes for Siemens's 334 pages at the
  Board's pace), and none for a Board whose slugs state titles.
- **A Board that reads zero costs one search request per portal that names a job page.** Of a
  random 60 of the ledger's 187 other zero-posting Boards, 9 name one (11 portals): 11 requests, or
  about 35 over a rotation of the 187, and each answers `404`, a login or a page with no result
  (none of the 9 reads through a search page). The 53 of the 60 that name no job page cost nothing.
- **Siemens is truncated every run**, since Avature serves no page past the 2,000th result (offset
  2000 answered, 2004 and every offset after did not). Nothing evicts, and a Siemens posting that
  leaves the newest 2,000 stays served: ADR-0053's no-drain cost, accepted because a missing row
  cannot be told from a closed one. mt is truncated by 11 postings its search pages did not list
  (98%, under ADR-0121's 99%).
- **Siemens's job pages are 1.3 to 2.4 MB each**, against 20 to 60 KB elsewhere, and 446 pass
  the gate; the value gate (ADR-0064) judges the Board on its first run's cost.
- **Four frozen rows stay frozen.** `deloitteus`, `emiratesjobs`, `mantech` and `loa` list tech
  titles today and are gated on a `0` measured before #880 (merged 2026-09-29 04:50 UTC), until their
  `board_cost.csv` row is blanked or ADR-0145's 14 days lapse, which is 2026-10-10 for `deloitteus`
  (cost row written 09-26 20:50) and `emiratesjobs` (09-26 19:20), 2026-10-12 for `mantech` (09-28
  12:19) and 2026-10-13 for `loa` (09-29 00:20). The doc proposes the edit; this change makes none.
- **Unbuilt follow-up: IBM has a public JSON search API** beside its WAF-challenged sitemap
  (`POST https://www-api.ibm.com/search/api/v2`, `appId=careers`, 1,936 hits on 2026-09-29 per the
  coverage probe, `experiment/radancy-403-and-coverage-2026-09-29/coverage_a/coverage_a.csv`, local
  and uncommitted). It would need its own reader, since IBM's job URLs are `JobDetail?jobId=…`, a
  shape this scraper does not read; nothing here touches it.
