# ADR-0175: A PyjamaHR Board is its company slug, discovered from the vendor's own jobs sitemap

**Status:** accepted · **Date:** 2026-09-22 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-detail-fetch-when-description-held.md) (skipping a held detail — deliberately not applied here), [ADR-0114](0114-company-name-from-the-board-page-title.md) (the name comes off the board page)

## Context

PyjamaHR was on CLAUDE.md's build list with an endpoint verified live in July 2026 and a key:
`GET api.pyjamahr.com/api/career/jobs/?company_uuid={UUID}`, the UUID being a ten-character code
that lives only in the board page's server-rendered `__NEXT_DATA__` payload. The research called
that "the trivial two-step" — read the page once for the UUID, then call the API. Discovery was
left open: the UUID namespace is opaque, and `wayback_feeder.py` listed pyjamahr among the ATSes
with "no enumerable host namespace".

Measuring the API before building it (2026-09-22; `docs/pyjamahr/2026-09-22_career-api-measurement.md`)
changed three of those premises.

1. **The API takes the slug.** `?company_slug={slug}` returns the same envelope as
   `?company_uuid=`, on the listing and on the detail endpoint, and the board's own `[company]`
   route chunk builds its requests with `company_slug`. The slug is the path segment of the public
   board (`jobs.pyjamahr.com/{slug}`), so it is the URL, the API key and — see below — the
   discovery key at once. Four public implementations found on GitHub all key on it; one of them
   (`jobscraper_hourly`) skips the detail pass entirely because it believed the detail needed the
   UUID. It does not.
2. **The vendor publishes the roster.** `jobs.pyjamahr.com/sitemap-jobs.xml` lists every
   published posting on the platform, `/{slug}/{job-slug}`, across every tenant: 7,801 URLs
   naming 680 tenants, refreshed daily, one 2.4 MB fetch. Every one of those 680 answered a live
   board page. The Wayback Machine's CDX index for the same host adds 77 more (3 of them hiring),
   so the two together are the roster; Common Crawl's newest index (CC-MAIN-2026-39) names 22,
   of which 2 are in neither — both live, both with nothing open.
3. **An unknown slug is not an error.** The listing answers HTTP 200 with `count: 0` for a slug
   that is not a tenant — byte-identical to a live Board with nothing open (75 of the census).
   The board page is what tells them apart: a real 404 for a non-tenant, 200 for every tenant.

Two more measured facts shape the scraper rather than the identity. The API returns rows flagged
`published_internally` (112 of 8,897, on 39 tenants) that the board's own page filters out before
rendering; and the detail payload is the only source of `description`, `job_type`, the salary
quartet and `created_at`, while the detail's `remote` boolean is `false` on every one of 1,741
postings including the 102 whose `workplace_type` is `REMOTE`.

## Decision

- **The Board's `slug` is the company slug**, the path segment of `jobs.pyjamahr.com/{slug}`. The
  scraper keys both endpoints with `company_slug`; the UUID is never fetched or stored. The
  ledger, the pool and the Job ids (`pyjamahr:{slug}:{id}`) all carry that slug, lowercase as
  the platform spells it (it is case-sensitive: `PYJAMAHR` answers an empty envelope).
- **Discovery is the vendor's sitemap first, Wayback and Common Crawl second.**
  `scripts/discover/mine_pyjamahr.py` reads `sitemap-jobs.xml` and folds the tenant slugs into
  the pool additively (source `sitemap`); `wayback_feeder.py` and `cc_miner.py` each gain a
  path-slug entry for `jobs.pyjamahr.com`. The sitemap's per-tenant URL counts are **not** used
  as job counts — they lag the API on 164 of 680 tenants — so the liveness prober reads `count`
  from the API.
- **The liveness prober asks two questions, cheapest first.** `?company_slug={slug}&limit=1`
  gives the Board's total in a ~200-byte envelope; a positive count is proof of a tenant. A zero
  goes to the board page: 200 is a live empty Board, 404 is dead. Neither host is seeded in the
  prober's gate table — ~3,800 requests at up to 84 req/s found no rate limit.
- **`published_internally` rows are dropped**, in `parse` and before the detail pass, because the
  board hides them: they are internal postings the API happens to serve.
- **No ADR-0048 skip; the ADR-0166 tech gate, as an exact site.** The ADR-0048 fork oracle,
  jazzhr and zoho already resolved the same way: skipping the already-described is safe only
  where the detail supplies the description and nothing else, and here it also supplies
  `employment_type`, `salary` and `posted_at` — skipping it would blank three fields that had
  values. The tech gate is a different skip and is taken: `parse` reads `title` and
  `department_name` off the listing row and the detail overrides neither, which is ADR-0166's
  exact case (the shape workday, smartrecruiters and zwayam have), so no posting the filter would
  keep can be gated out. A gated posting still ships as a Job without a description. At ~25% tech
  that is roughly three of every four detail fetches not made.
- **`remote` reads `workplace_type`, never the boolean.** The stated type wins outright; the
  location guess is consulted only for the 51 rows that state nothing, since of 7,674 rows stating
  `ON_SITE` or `HYBRID` none names a remote location.

## Alternatives considered

- **Key on `company_uuid`, as the research assumed.** Rejected: it costs one board-page fetch per
  tenant just to learn the key, it makes the pool unreadable (an opaque code beside each row),
  and it buys nothing — the slug is accepted everywhere the UUID is. `fingerprint_careers.py`'s
  existing `ATS_PROBES["pyjamahr"]` still spells its URL with `company_uuid=`; it is left as is,
  because that probe's slug capture never yields a tenant for this ATS in the first place
  (its regexes capture the `app.`/`jobs.` host label), and repointing a probe nothing reaches is
  a separate fix.
- **Enumerate tenants from Common Crawl and Wayback alone**, the way every other path-style ATS
  is discovered. Rejected as the primary source: it finds what was once archived, and the vendor
  already publishes what is live today. Both stay as second sources because they name tenants
  whose postings are not in the sitemap (77 and 2 on the day, respectively).
- **Read the listing with DRF's default page of 10 and walk `next`.** The measured `limit`
  parameter has no ceiling (`999999999` returns the 643-row Board whole), so the scraper asks for
  1,000 and reads every known Board in one call — but it still follows `next` whenever the API
  sets it, so a future cap cannot silently shorten a Board.
- **Serve `published_internally` rows.** They are in the API's `count` and its `results`, and all
  four public implementations serve them. Rejected: the vendor's own page does not show them, and
  a job the employer chose not to publish is not an opening.

## Consequences

- One new scraper, one probe, one miner, one feeder entry, one company-name pattern, one ledger
  (760 rows: 759 live, 1 dead, 682 hiring, 8,894 postings on the day it landed). At ~25% tech by
  the post-hoc gate this is a small provider; India is 78% of its rows.
- `count` includes the internal rows the scraper drops, so the ledger's `jobs` figure and the
  shortfall check both count them. A Board whose every posting is internal is "hiring" in the
  ledger and yields zero Jobs — harmless, and honest about what the API says.
- Two Boards publish two postings under one job slug, so one of the two links opens the other
  posting; six rows of 8,897 carry no slug and link to the board page. Neither is worth a
  mechanism.
- The `valid_through` field is ignored on purpose: it was in the past on 1,408 of 1,741 postings
  the board still lists and shows, so it is a default 60 days after `created_at`, not a closing
  date. Whether a posting is open is the listing's say.
