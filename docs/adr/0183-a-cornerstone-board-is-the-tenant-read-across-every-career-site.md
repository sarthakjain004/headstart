# ADR-0183: A Cornerstone Board is the tenant, read across every career site

**Status:** accepted · **Date:** 2026-09-23 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-detail-fetch-when-description-held.md) (skipping a held detail — taken here), [ADR-0114](0114-company-name-from-the-board-page-title.md) (no name surface here), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

Cornerstone OnDemand (`csod`) was the fourth-largest ATS in a 20-ATS candidate evaluation
(30,149 jobs, 6.4% tech, 334 companies, a 564-row seed list from `kalil0321/ats-scrapers`). The
upstream scraper keyed each Board on `{corp}.csod.com` plus a career-site id defaulting to 1, read
a token off the career-site page, and posted to a default API host `na.api.csod.com`.

Measured live on 2026-09-23 (`docs/cornerstone/2026-09-23_careersite-api-measurement.md`: all 404
seed tenants walked in full, 24,800 listing rows, 924 job ads), four of those premises were wrong:
the default host does not resolve; the site id is a filter, and a wrong one answers 200 with
`totalCount: 0`; upstream's token regex never matches; and the listing's `externalDescription` is
a fragment of the posting. A tenant runs many career sites (up to 114 ids, 53 active), and one
requisition is often posted to several of them.

## Decision

1. **The Board is the tenant; the slug is the `{corp}.csod.com` label, lowercased.** The scraper
   walks `careersites/{id}` from 1 to the first 404, searches every site not marked
   `active: false`, and unions the postings by `requisitionId`. Each posting keeps the lowest
   site it is listed on; its job ad is read from that site and its URL names it (a job page on a
   site the posting is not on renders no posting). A per-site Board was rejected: 12,798 postings
   sit on more than one site of 119 of the 368 hiring seed tenants, so per-site Boards would serve
   15,549 rows twice under different Board keys, and `evict_duplicate` groups only within a Board.
   Keeping the seed's own site ids was rejected too: they reached 31,545 of 42,534 postings.
2. **One token per Board, refreshed once on a 401.** The career-site page's `csod.context` gives
   an anonymous JWT and the API pod (`endpoints.cloud`). The token is per corp and pod-bound, lives
   21 minutes to 24 hours, and expires into a 401 with an empty body — never a 200-empty, so an
   expired token cannot read as an empty Board. US-pod tenant hosts also need `ASP.NET_SessionId`,
   whose value is the JWT's `aud`; the page's own cookies are dropped from the pooled session the
   moment it is read, because with them in the jar the explicit header answers 401.
3. **Dead versus empty.** DEAD is DNS failure (no wildcard DNS) or every career-site page on ids
   1–3 redirecting to `/ui/error` — what a corp with no career site answers (`csod.com` hosts the
   vendor's LMS on the same labels, so most discovered labels are LMS customers). Three ids,
   because `myhr-ece` has no site 1. A live tenant with nothing open answers the page, walks its
   sites and searches to zero. A search that answers 404 `ResourceNotFound` reads as an empty site:
   the page itself lists no openings (metso, in Chrome). The liveness probe counts the scraper's
   own union (`CornerstoneScraper.listing`), because summing each site's `totalCount` would have
   read 58,083 where the union is 42,534.
4. **The description is the job ad**, fetched per posting: it was more than 20% longer than the
   listing text on 750 of 924 postings (median 3.24x), and the listing text deletes every `&`.
   An empty ad falls back to the listing text; a text under 30 word characters once `<<…>>`
   template tokens are removed is a placeholder and no description.
5. **The tech gate is exact, and ADR-0048's skip is taken.** No surface states a department, and
   the ad's `title` equals the listing's (58 of 58), so `is_tech(title, None)` asks what
   `filter_tech` will ask; the ad supplies nothing but the description.
6. **No company name.** Nothing at Board level names the employer — no `<title>` on 30 of 30
   tenants probed, `csod.context` carries only the corp, and `careersites/{id}` only language
   names. The job page's JSON-LD `HiringOrganization` does, but per posting (the phenom subsidiary
   trap) and on a page the steady-state scrape never fetches. The slug stays the name; most are
   readable (`aswatsoneurope`, `apollotyres`). The known limitation is the short ones: 101 of the
   544 Hiring Boards have a slug of four characters or fewer, some recognisable (`bbva`, `otis`,
   `iata`, `upmc`) and many not — listed in full under Consequences.
7. **It lands active.** The ledger holds 544 Hiring Boards and 54,838 postings once seven vendor
   demo/sandbox corps (379 postings) go to `excluded_and_parked.EXCLUDED_BOARDS`. A run fetches the listing for
   every posting (~2.1 KB each: 2,106,187 bytes for a 1,000-row page) and a job ad only for the
   tech ones (p50 8.2 KB, mean 10.0 KB). At 6.7% tech that is ~3,674 tech Jobs for listings of
   54,838 × 2.1 KB ≈ 115 MB, ads of 3,674 × 10.0 KB ≈ 37 MB, and pages and site answers of 544
   Boards × ~15 KB ≈ 8 MB (a mean 9.4 site ids and 3.8 active sites per tenant): ~160 MB,
   **~44 KB per tech Job** against ADR-0158's accepted ~2 MB (jazzhr: ~10.7 GB for ~5,100).
   About 45x under the bar.

## Alternatives considered

- **Listing only, no detail pass.** Cheapest, and a description could never go missing. Rejected:
  it serves a fragment on 81% of postings and mangles every `R&D`.
- **`jobDetails` as the detail.** Its `externalDescription` is the same field as the listing's
  (with the HTML kept); it adds `additionalLocations`, which the listing already carries in full
  (923 of 923), and a position org unit, not a department.
- **The job page's JSON-LD.** It carries the ad's description, a date and an employer name, but is
  absent on tenants with SEO off and is a 10.7 KB HTML page per posting, against the ad's JSON.
- **Company name from `HiringOrganization`.** See decision 6.

## Consequences

- The ledger (`data/validate/liveness/cornerstone.csv`) is 3,210 rows: 735 live, 2,363 dead, 112
  unknown. Most dead rows are LMS-only labels from the Wayback `*.csod.com` sweep. The unknowns are
  hosts that never answer the career-site page: 34 CNAME to a legacy Akamai wildcard whose origin
  DNS fails, 18 to `maintenance.csod.com` (a Cornerstone-branded page with no context), 27 to bare
  IPs and the rest to other vendor infrastructure; 11 are ordinary tenant hosts that fail — TLS
  failures, 504s and one tenant (`fda`) that redirects to the separate federal cloud `csodfed.com`,
  which this scraper does not read. None is called dead without a measured dead answer. All were
  re-probed once more before the ledger was committed (112 of 118 stayed unknown; six became
  live-empty under the `ResourceNotFound` rule), and liveness re-probes them whenever the 3-day
  unknown TTL comes due.
- Common Crawl was **not measured**: `index.commoncrawl.org` returned empty replies for the whole
  session, so the sweep stopped at its 10-minute cap. The pool is the 404-tenant seed, the 252-row
  existing pool (all in the seed) and 3,206 Wayback labels (2,806 found only there).
- A Board with many sites is slow to walk — one `careersites` request and one search per site,
  sequentially (alamo, 38 ids: ~44 s). Measured acceptable; a parallel walk is a later option.
- 58.7% of listing rows are English, 78.0% of tech rows; the rest are scraped and held out of the
  English-only index.
- **Short, often opaque slugs serve as company names** (decision 6). The 101 Hiring Boards whose
  slug is four characters or fewer: `aak`, `afd`, `artc`, `bba`, `bbva`, `bc`, `bci`, `bcie`,
  `beca`, `benu`, `bil`, `bkam`, `btc`, `ccac`, `ceva`, `cmpc`, `cmsu`, `cod`, `cqc`, `csd`,
  `cwgc`, `duq`, `dvwp`, `ecms`, `eip`, `esj`, `esu`, `fbfs`, `fci`, `fwb`, `gmv`, `gqg`, `gsb`,
  `hec`, `helm`, `hpm`, `hps`, `hubo`, `iata`, `imec`, `iso`, `isq`, `isu`, `krka`, `lm`, `lmcc`,
  `mcap`, `mctc`, `mmsd`, `mopt`, `mpar`, `mrv`, `msc`, `mta`, `mvcc`, `mxns`, `myhr`, `njit`,
  `nlb`, `nne`, `nnit`, `oebb`, `otis`, `ouc`, `pphe`, `pret`, `puc`, `rb`, `rbd`, `rfs`, `rkl`,
  `ros`, `ruba`, `sbk`, `scu`, `seok`, `sgb`, `sgn`, `sgu`, `sjp`, `sobi`, `stm`, `tbr`, `tdc`,
  `tmg`, `tpa`, `trs`, `tvnz`, `ubci`, `uci`, `ufcu`, `uic`, `uis`, `unco`, `unm`, `upmc`, `usm`,
  `wcaa`, `wwt`, `ymca`, `yvw`.
