# PyjamaHR career API — measurement log

Live measurement of `api.pyjamahr.com` and `jobs.pyjamahr.com` before building
`headstart.scrapers.pyjamahr`. The write-up is `docs/pyjamahr/2026-09-22_career-api-measurement.md`;
the decisions are ADR-0175. This log is the run record.

## 2026-09-22

- **Prior art checked.** `kalil0321/ats-scrapers` has no PyjamaHR scraper (274 paths, none match).
  GitHub code search for `api.pyjamahr.com` finds four independent implementations
  (`strelov1/freehire` Go, `ever-jobs/ever-jobs` TS, `areebahmeddd/jobdex` Python,
  `jobscraper_hourly` TS). All key on `company_slug`; two believed the detail needed the UUID
  (it does not), all serve `published_internally` rows (the board hides them), one links jobs as
  `?job_uuid={id}` (never 404s). The July research note keyed on `company_uuid` and did not know
  about `company_slug`, `limit`, or the sitemap.
- **Bundle read.** `jobs.pyjamahr.com`'s Next.js chunks: the `[company]` route builds listing
  requests with `company_slug` + `page` + filter params; `_app` uses `company_uuid` for the
  embed; the `[company]` chunk filters `published_internally` rows before rendering and turns a
  `?job_uuid=` query into a client-side push to `/{company}/{slug}`. Build manifest names
  `/sitemap-jobs.xml`.
- **Roster.** `sitemap-jobs.xml`: 7,801 URLs, 680 tenants (2.4 MB). Wayback CDX for
  `jobs.pyjamahr.com/*`: 413 captures, 144 slugs (3 junk paths). Union 757. Census: all 757 board
  pages 200, all 757 API envelopes 200, `count == len(results)` on every one, 682 hiring,
  8,897 rows. `artifacts/2026-09-22_tenant_census.csv` is the per-tenant table.
- **Pagination.** Default page 10; `page_size` ignored; `limit` honoured with no ceiling
  (5,000 / 100,000 / 999,999,999 all return the 643-row `hunarstreet-technologies` whole);
  `page=0`/`page=999` → 404 `{"detail":"Invalid page."}`; `next` URLs absolute; ids descending;
  page 1 stable across re-fetch.
- **Dead vs empty.** Unknown slug → 200 `count: 0`. Board page for a non-tenant → real 404
  (`404: This page could not be found`). Every census tenant's page → 200 with
  `__NEXT_DATA__.props.pageProps.companyDetails`; `<title>` == `companyDetails.name` on 757/757.
- **Detail.** Requires the company key (bare → 404; another tenant's slug → 404; nonexistent id →
  404, same body). 1,741 details fetched across 10 tenants: description 100% non-empty (p50
  ~2.2k chars); `remote` false on all 1,741 (102 REMOTE); `job_type` FULLTIME 1,667 /
  CONTRACT-BASED 48 / PART-TIME 16 / INTERN 8 / FREELANCER 2; `salary_type` ANNUAL 1,022 /
  MONTHLY 641 / HOURLY 78; currency INR 1,577, USD 160, MYR/HKD/EUR/AED 1 each; bounds present
  iff `is_salary_visible` (1,236 visible all with both, 505 hidden with none);
  `valid_through` in the past on 1,408 while still listed.
- **Listing census fields (8,897 rows).** `workplace_type` ON_SITE 6,824 / REMOTE 1,172 / HYBRID
  850 / null 51; no ON_SITE or HYBRID row names a remote location (0 of 7,674); `country` India
  6,941, United States 737 + USA 409, then Nepal, UAE, Australia…; `other_locations` non-empty
  1,396; `department_name` 4,924; experience bounds never null, integral, never inverted;
  `slug` missing on 6; 2 duplicate slugs within a tenant; `published_internally` 112 across 39
  tenants; `product` always null.
- **Rate limit.** 145 slugs × 2 at conc 16: 290 req / 8.3 s, all 200. 255 details at conc 16:
  29.8 req/s, all 200. 757 × 2 census at conc 16: 36 s, all 200. 1,486 details at conc 32:
  84.4 req/s, p50 239 ms, p95 924 ms, all 200. Liveness prober at 432 workers: 680 boards in
  3.8 s, zero unknown. UA-agnostic (bare / curl / python-requests / Mozilla all 200).
- **Tech share.** `tech_filter.is_tech(title, department_name)` over the 8,785 public rows:
  2,236 (25.5%); of those India 1,834.
- **Landed.** Pool 680 (sitemap) → ledger 680 live / 679 hiring / 8,766 jobs. Then
  `wayback_pages.py pyjamahr` (1 CDX page, 142 slugs) folded +78 into the pool; the re-probe of
  those 78 took 1.7 s → ledger **758 rows: 757 live, 1 dead** (`images`, a path the feeder's
  validity filter admits and the prober correctly kills off the board page's 404), **682 hiring,
  8,894 postings**. Then `CC_ONLY_ATS=pyjamahr cc_miner.py` on `CC-MAIN-2026-39` (1 page): 22
  tenants, 2 new to the pool (`actgrants`, `allstarconsulting1`), both probed live with 0 jobs →
  final ledger **760 rows: 759 live, 1 dead, 682 hiring, 8,894 postings**.
