# ADR-0245: An Avature Board is its tenant host, read through its portals' sitemaps

**Status:** accepted · **Date:** 2026-09-26 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail — deliberately not applied here), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (truncation leaves a Board out of eviction scope), [ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the enable bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate), [ADR-0180](0180-an-adp-board-is-a-career-center-read-in-every-language-at-one-paced-budget.md) (a platform-wide paced budget), [ADR-0201](0201-a-scraper-states-its-detail-request-once-and-the-base-runs-the-pass.md) (the Detail pass seam)

## Context

Avature sat on CLAUDE.md's build list as one of the three ATSes the Indeed sweep resolved most
unsupported companies to. The only prior implementation, kalil0321/ats-scrapers' (MIT), walks each
tenant's `SearchJobs` HTML 12 rows at a time, scrapes each row's container by a chain of CSS
guesses, and escalates a `406` to a TLS-impersonating client and then a paid headless browser,
reading the `406` as a block on HTTP/2 clients.

Measured live on 2026-09-26 (`docs/avature/2026-09-26_listing-measurement.md`: 77 tenants
surveyed portal by portal, 257 job pages from 55 portals, and Bloomberg's 352-posting Board for
every rate and pagination question), most of that was wrong or unnecessary:

- The `406` is a **per-IP budget** across every tenant, not a client fingerprint: ~350 requests of
  burst refilling at ~1.2/s; past it every tenant answers `406` for 3–4 minutes, Chrome
  impersonation included, while another IP is served.
- `SearchJobs` ignores its page size (500 asked, 12 served), but every portal publishes a sitemap
  that lists every posting in one fetch (Bloomberg: 352 URLs, 352 results).
- A tenant runs several **Portals** under one host, and job ids are tenant-wide (Bloomberg's
  public and internal portals share 257 ids, each one posting under both).
- Job-page layouts are per-tenant templates: 9 of 26 readable portals carry JSON-LD, 19 carry
  label/value rows in one of two class schemes, labels are in the tenant's own words and language.

## Decision

**The Board is the tenant host label** (`bloomberg` for `bloomberg.avature.net`), keyed
lowercase, with `url` `https://{label}.avature.net`. Owner's choice over a Board per Portal. Each
run reads `robots.txt`, takes every `Sitemap:` line naming a portal's `sitemap_index.xml`
(verbatim — some are on vanity hosts, `jobs.bmc.com`), reads every per-locale child sitemap, and
unions `JobDetail` URLs by id. Portals whose names read `internal`, `employee` or `referral` are
read last, so a shared id keeps its public URL. A duplicate portal (fonterra's `examplePathName`,
bmcrecruit's `oldcareersportal`) collapses into the one before it with no alias ledger, and a new
portal is picked up without a ledger change.

**A private portal is settled by one request.** If a private-named portal's first own posting
redirects to `/Login/`, the portal's other own ids are dropped unread. Bloomberg: 191 job pages
and 227 s per gated run became 88 pages and 126 s, for the same 88 Jobs.

**Closed postings are not lost details.** A sitemap can keep closed ids (bupaanz `careersau`:
1,701 listed, "of 999" on its search page, 6 of 6 sampled old ids closed). Job pages are fetched
with redirects off; a `302` to `/{portal}/Error` or `/Login/` is labelled "not public (closed or
login-walled)" and subtracted before truncation is decided, so a Board with stale sitemap rows is
not scope-excluded every run (ADR-0053 has no drain). A `302` to another job page (cyclecarriage's
`/en_US/` URLs) is followed once.

**The tech gate reads the URL slug's title** — the only listing surface; none states a department.
A measured approximation: over 244 titled pages it kept 47 of 52 tech postings (90.4%) and let
through nothing the real verdict drops; the 5 misses are 3 department promotions (rule 4) and 2
postings retitled after their slug was minted. It skips 79% of job pages. No `skip_held`: the job
page supplies title, location and department, not only the description.

**One paced budget per process, then the spare egress.** Every request waits on a process-wide
`Pacer(1.0)` — under the ~1.2/s refill, so a shard never spends its burst — and a `406` moves the
Board onto the spare egress (`egress_fallback_on = {406}`). `detail_workers = 4` keeps the pace fed
at ~1.7 s per page; more only queues. Job pages go out with `discard_cookies`: one Avature session
serialises its requests (1.41/s shared-cookie at eight in flight, 2.41/s cookie-free at four).

**The page is read through its stable surfaces first**: `og:title` (else JSON-LD, else a "Name"
label), `og:site_name` for the company (else JSON-LD `hiringOrganization`), JSON-LD for date,
location and type, then the label rows through small most-specific-first vocabularies. Coverage
over the 257 pages: title 97%, description 91%, location 72%, company 65%, date 39%. The Board's
company is the name its fetched pages agree on; no extra request.

**Enabled.** Per tech Job it costs one ~60 KB job page (gated pages are ~88% tech: ~68 KB) plus
~340 bytes of sitemap per posting at a 21.3% tech share (~1.6 KB): ~0.07 MB, against ADR-0158's
bar of ~2 MB.

## Alternatives considered

- **A Board per Portal** (`bloomberg.avature.net/careers`, Workday's `{co}/{site}` shape). Makes
  a portal a ledger row and a duplicate portal a second Board, which then needs a subset-alias
  script re-run after every refresh (the ADP and Taleo Enterprise pattern, ADR-0186/0202). Since
  ids are tenant-wide the union costs nothing and needs no such script.
- **The `SearchJobs` HTML walk** (upstream's). 12 rows a page at a paced 1 req/s is 30 requests
  for Bloomberg's listing against 1, and its rows carry nothing the sitemap slug lacks for the gate.
- **No tech gate.** Exact, but ~5x the job pages under a per-IP budget of ~1.2 req/s: boozallen's
  2,401 postings alone would be ~40 minutes of one IP.
- **Following redirects on job pages.** A closed posting then reads as a 404 and an internal one
  lands on the tenant's SSO host, so neither is recognisable; both would count as lost details and
  truncate the Board.
- **Upstream's TLS-impersonation and headless-browser escalation.** It targets a client
  fingerprint the measurement ruled out: impersonated Chrome drew the same `406` inside a block.

## Consequences

- The Indeed sweep's Avature companies mostly sit behind vanity domains (`jobs.bmc.com`), and a
  vanity host does not name its label; resolving them is discovery work still to do, like
  Radancy's career fronts over Avature (Intuit), which this scraper cannot reach at all.
- Location is missing where a tenant states it outside any label (ea, lululemon, ecb, frequentis)
  or runs a fully custom template (L'Oréal's locale portals); each is a vocabulary or layout
  addition when measured, not a guess.
- The budget is per IP and shared by every Avature Board in a shard, so a slice heavy in Avature
  spends it; the spare egress and the per-shard cap (ADR-0047) are what absorb that, and a run's
  `406` rotations are the number to watch.
