# ADR-0266: A WP Job Openings site is an ATS Board, read through its own REST route

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0048](0048-skip-details-we-already-hold.md) (the held-detail skip, not taken here), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) and [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a short Board's eviction scope), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the enable bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate), [ADR-0246](0246-a-radancy-career-front-is-a-board-keyed-by-its-host-scraped-in-full.md) (a Board keyed by its host), [ADR-0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md) (Dormant Boards)

## Context

Careers-page fingerprinting of 600 employer apply hosts that the fingerprinter rated as having no
ATS found the WordPress plugin **WP Job Openings** (AWSM Innovations; wordpress.org slug
`wp-job-openings`, 30,000 active installs, renamed "HireZoot" in its version 4) on 32 of them,
more than any other unrecognised job platform in that set. A company installs it on its own
WordPress site; there is no vendor host.

Measured live on 2026-09-28 (`docs/wp_job_openings/2026-09-28_rest-and-job-page-measurement.md`:
32 seed sites, 481 candidate hosts asked four ways, the first pool's 427 Hiring Boards walked
whole with 4,384 job pages sampled, a rate ramp to 32 concurrent requests):

- the plugin takes applications through its own form into a private post type
  (`awsm_job_application`); every one of the first 187 job pages carried that form;
- WordPress's REST API serves the post type (the plugin registers it with `show_in_rest`) with the
  Board's count in `X-WP-Total`, listing only published postings; the RSS feed also lists the
  plugin's public `expired` status, and a sitemap is absent on 11 of 32 seeds;
- the job specs (category, type, location) are taxonomies without `show_in_rest`: the REST row
  names them only as `class_list` slugs, and only on WordPress 6.5+; the job page carries their
  names in the plugin's JSON-LD `JobPosting` and its specifications block;
- Hostinger's CDN refuses the Chrome TLS fingerprint the repo's HTTP session impersonates on 22 of
  481 candidates, and serves Firefox's.

## Decision

**It is an ATS, and a Board is one WordPress site keyed by its host** —
`wp_job_openings:finac.io`. It stores applications, which a **Career front** by definition does
not, so it is not one. The ATS key is the plugin's wordpress.org slug, not the "HireZoot" brand:
the slug, the post type and every URL fingerprint discovery keys on kept the old name through the
rebrand. The pool holds each site under the host its own REST API names (its postings' link
host, else its REST index's `home`), and the probe reads another spelling as an alias, DEAD.

**The listing is the REST route, asked as `?rest_route=/wp/v2/awsm_job_openings`**, walked by id
at 100 a page to `X-WP-TotalPages`, a shortfall against `X-WP-Total` truncated unless negligible
(ADR-0121). The query form over `/wp-json/`: it lists on 326 of the 330 sites either form lists,
against 311, because it needs no pretty permalinks. A 200 that is not a JSON list fails the Board
(`BoardUnreadable`) rather than emptying it.

**Every request asks with curl_cffi's Firefox fingerprint** (`impersonate="firefox"`), scraper and
probe alike; the dependency floor moves to curl_cffi 0.9, the first to name it.

**Each tech posting's page is read; the gate is a measured approximation.** The page's JSON-LD
gives the places ("; "-joined) and the employer (`hiringOrganization`, agreed at 0.9 across the
Board's pages); its specifications block gives the category and type names, and stands in for the
JSON-LD where a page lacks it. The REST row keeps the title, body and date. The ADR-0166 gate reads
the title and the humanised category slug off the REST row; it cost 9 of 857 tech postings
(1.1%) on 4,384 sampled. The held-detail skip is not taken: the page supplies more than the
description.

**Dead versus empty.** A JSON list is LIVE at `X-WP-Total` (0 is a live, empty site); 404
(`rest_no_route`: WordPress without the plugin), a 200 that is not JSON (30 of 30 such sites no
longer ran the plugin), a redirect to another host and a DNS failure (every Board is its own
domain) are DEAD; a REST refusal (401/403), a certificate that does not verify, 5xx, 429 and bot
challenges are UNKNOWN. The probe verifies certificates, as the scraper does, through a `verify`
argument `check_liveness._fetch` now accepts.

**Discovery** reads Common Crawl's columnar index (DuckDB over HTTPS, filtering `url_path` and
`url_query` for the plugin's own vocabulary), urlscan.io's scans whose page loaded a plugin file,
and the careers-page fingerprinter, which now recognises the plugin's asset path
(`scripts/discover/mine_wp_job_openings.py`). The vendor's demo sites (`demo.hirezoot.com`,
`demo.wpjobopenings.com`: 16 and 17 sample postings, 2018-2022) are excluded; its own hiring site
(`awsm.in`) is a real employer and stays.

**It lands active.** A run reads every REST row (~5.0 KB) and the page of each posting the gate
keeps (~170 KB): ~0.63 GB for 3,183 tech postings on the first pool, ~0.2 MB per tech Job, a tenth
of ADR-0158's ~2 MB bar.

## Alternatives considered

- **A Career front, keyed under the vendor.** Rejected: the plugin stores applications, and there
  is no vendor host or Backing Board to key under.
- **"hirezoot" as the ATS key.** Rejected: the rebrand renamed only the product; discovery and
  every URL still carry `wp-job-openings`/`awsm_job_openings`.
- **The RSS feed as the listing.** Rejected: it serves `expired` postings (11 of 75 ids on
  `finac.io`), and telling them apart costs a page fetch per posting.
- **The sitemap as the listing.** Rejected: absent on 11 of 32 seeds, and where present it lists
  exactly the REST route's links.
- **No detail pass, the specs read off `class_list` slugs.** Cheaper by ~30x in bytes, but slugs
  lose spelling ("Delhi - Gurgaon" as `delhi-gurgaon`), are absent before WordPress 6.5 (21 of 417
  Boards), and name no employer; the page costs a tenth of the enable bar.
- **A sitemap-and-page fallback for sites that refuse the REST route.** 13 of 481 candidates
  refuse anonymous REST calls (a security plugin) while still running the plugin, and some of
  them publish a sitemap. Deferred: a second listing path for ~4% of Boards, left UNKNOWN in the
  ledger so a later build can find them.

## Consequences

- A site that switches its REST API off, or lets its certificate lapse, leaves the scrape (UNKNOWN)
  rather than serving stale rows.
- Many sites never close a posting: half of the first pool's postings are over a year old. The
  Dormant rule (ADR-0250) is what takes a silent site's rows out of the tech subset.
- Job boards and staffing firms run the plugin too (`findmyjob.lk` lists 7,040 postings, 1,332 of
  them tech). Nothing here sets their **Operator** label; `boards/board_operator.py` is curated
  separately.
- Three `www`/apex pairs with nothing published state their own `home`, so both spellings stay
  live at 0 until one publishes and its rows' links name one host.
- One Yoast option disallows `/?rest_route=` in `robots.txt`; 1 of 117 Hiring Boards sampled sets
  it.
