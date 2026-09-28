# WP Job Openings: REST route, job page and rate-limit measurement (2026-09-28)

Everything the WP Job Openings scraper (`src/headstart/scrapers/wp_job_openings.py`, ADR-0266)
rests on, measured live on 2026-09-28. The probe scripts and raw captures were kept locally under
`experiment/wp-job-openings/` (not committed); every number a reader needs is below.

**What it is.** WP Job Openings is a WordPress plugin by AWSM Innovations: wordpress.org slug
`wp-job-openings`, version 4.1.0, 30,000 active installs, last updated 2026-09-22, renamed
"HireZoot" in its version 4 (the slug, the post type and every URL fingerprint kept the old
name). A company installs it on its own WordPress site. It came to this repo's attention through
careers-page fingerprinting: of 600 employer apply hosts the fingerprinter rated as having no
ATS, 32 loaded this plugin, 8 of them with its Pro pack, more than any other unrecognised job
platform in that set.

**It is an ATS, not a Career front.** The plugin stores applications: its own form posts them into
a private post type, `awsm_job_application`, reviewed in the site's WordPress dashboard. Every one
of the first 187 job pages sampled (28 sites) carried that form. A **Career front** (CONTEXT.md)
stores none and hands its Apply button to another ATS.

Samples:

- **32 seed sites** (the fingerprinting above): every listing surface, and 187 job pages.
- **481 candidate hosts** (the seeds plus Common Crawl's newest crawl, `CC-MAIN-2026-39`): the REST
  route asked four ways (two URL forms × two TLS fingerprints), and each non-answer inspected.
- **The first pool's 427 Hiring Boards** (755 hosts probed; 602 `live`): every Board's REST
  listing walked whole (18,126 postings on the 417 read) and up to 30 random job pages from each
  (4,384 pages).
- **58 random Hiring Boards** for bytes and date stability; **117** for `robots.txt`.

## What the plugin's source says (read first, then measured)

From the plugin's own source (`wp-job-openings.php`, `inc/class-awsm-job-openings-core.php`):

- the post type `awsm_job_openings` is `public` with `show_in_rest`, so WordPress's REST API serves
  it at `/wp/v2/awsm_job_openings`;
- its permalink base is a site setting (`awsm_permalink_slug`, default `jobs`), so a job's URL is
  `/{base}/{slug}/` — the seeds alone used `/jobs/`, `/career/`, `/current-openings/`,
  `/blog/jobs/`, `/job-post/` and `/index.php/careers/`;
- the job specs (category, type, location, and any a site adds) are custom taxonomies registered
  **without** `show_in_rest`, so no REST route names them;
- a closed posting moves to a custom status `expired`, registered `public` — which WordPress's feeds
  include and the REST route does not;
- every single job page prints a JSON-LD `JobPosting` in its footer (title, raw post content,
  `datePosted`, `hiringOrganization` from a site setting, `employmentType` mapped from the job-type
  terms, one `Place` per location term) — except a page whose status is `expired`.

## Identity

**A Board is one WordPress site, keyed by its host.** The REST route, every job's link and the
application form all live on the site's own host. A site answers under one host: of 326 sites
that listed postings, 2 answered on a second host whose rows linked to the first
(`www.adridgemedia.com` → `adridgemedia.com`), and some redirect (`acude.uy` → `www.acude.uy`,
`www.inspironlabs.com` → `inspironlabs.com`). So the pool is built under the host the route itself
names — its rows' link host, else the REST index's `home`, else the host a redirect lands on
(`mine_wp_job_openings.py pool`) — and the probe reads the other spelling as an alias, DEAD.
Three `www`/apex pairs with nothing published (`dstc.sa`, `ooptiq.com`, `penplusbytes.org`) each
state their own `home`, so nothing tells them apart until one publishes; they serve no posting.

A native id is WordPress's integer post id, with no colon (so `board_of` splits a Job id cleanly).

## Listing

| Surface | What it serves | Verdict |
| --- | --- | --- |
| REST `?rest_route=/wp/v2/awsm_job_openings` | Every **published** posting: id, title, full body, dates, link, and (WordPress 6.5+) the terms as `class_list` slugs; `X-WP-Total` | **Chosen** |
| REST `/wp-json/wp/v2/awsm_job_openings` | The same route through the pretty-permalink path | Lost: 404 or a page on 19 sites that list through `rest_route` |
| RSS `/feed/?post_type=awsm_job_openings` | 10 items a page (`posts_per_rss`; 3 on one site), `&paged=N` walks on | Rejected: lists `expired` postings too |
| Sitemaps (WordPress core, Yoast) | The published links, plus the archive URL | Rejected: absent on 11 of 32 seeds |
| Archive page and `admin-ajax.php?action=jobfilter` | Listing HTML, a site-set number a page, specs only where the site shows them on the listing | Not needed |

Measured on the 32 seeds (`/wp-json/` form, the default Chrome fingerprint):

- **REST**: a JSON list with `X-WP-Total` on 30 of 32; the other 2 answered 403 from WordPress
  itself (`wp_die`) to every User-Agent tried (`headstart/0.1`, Chrome's, `python-requests`).
  `a5econsulting.com` listed none: its WP Job Openings posts are all `expired` (from 2022; its live
  listing runs on another plugin), which is the right answer.
- **Feed**: 200 on 31 of 32, but it lists the `expired` status: on `finac.io` 75 ids against the
  REST route's 64, and all 11 extras render the plugin's `awsm-expired-message` and no JSON-LD (4 of
  4 opened); all 4 on `a5econsulting.com`'s feed are expired.
- **Sitemaps**: present on 21 of 32. Walked to completion on 12 sites, each lists exactly the REST
  route's links plus the archive URL (`heptarc.com` 272 = 272, `alligoric.com` 629 = 629).

**Why `rest_route`.** Over the 481 candidates (Firefox fingerprint, see below), the `/wp-json/` form
listed on 311 sites and the `?rest_route=` form on 326; 19 list only through `rest_route` (plain
permalinks, or a server rewrite that sends `/wp-json/` to a page) and 4 only through `/wp-json/`
(one security plugin that refuses the query form, one site now serving pages, one 403, one bot
challenge). `rest_route` reaches 326 of the 330 sites either form lists. Of the 307 sites both
forms listed, 306 stated the same `X-WP-Total`; one stated 4 and 5 on reads minutes apart.

**Pagination.** `per_page` tops out at 100 (`per_page=101` answers 400 `rest_invalid_param`); a page
past the end answers 400 `rest_post_invalid_page_number`, so the walk stops at `X-WP-TotalPages`.
`orderby=id&order=asc` is accepted, and keeps a posting published mid-walk from shifting pages.
The rows walked equalled `X-WP-Total` on every site walked whole (12 seeds; 0 of 417 Hiring
Boards short).

**The body is whole.** The REST `content.rendered` is the post body after WordPress's filters (the
plugin adds its specs and form only on the single page itself). The JSON-LD `description` is the
raw source, block-editor comments included.

**Two answers need care.** 2 of 427 Hiring Boards send a UTF-8 byte-order mark ahead of the JSON
(`career.icepoweraudio.com`, `lacura.co.uk`), so the list is decoded from bytes. One
(`www.metzgerei-schneider.de`) prints an Elementor `<style>` block ahead of the JSON whenever the
body is rendered; it is left unread and fails the Board rather than emptying it.

## Dead versus empty

`p_wp_job_openings` asks the route for one row (`&per_page=1&_fields=link`). The 481 candidates
answered, under the Firefox fingerprint, by the probe's own rules:

| Answer | Sites | Verdict | Evidence |
| --- | ---: | --- | --- |
| a JSON list with `X-WP-Total`, its row on the same host | 321 | LIVE at that count (0 is a live empty site) | — |
| … whose row links another host | 2 | DEAD (an alias of that host) | `www.adridgemedia.com` |
| 404 `rest_no_route` | 76 | DEAD | 24 of 25 sampled had no `awsm_job_openings` route in the REST index: WordPress without the plugin (46 of the 76 were wordpress.org's own localised plugin pages, which the pool no longer takes) |
| 200 that is not JSON | 28 | DEAD | 30 of 30 such sites inspected no longer ran the plugin; 27 no longer ran WordPress (Framer, Astro, Discourse) |
| a redirect to another host | 9 | DEAD (the Board is the target, which the pool holds) | `acude.uy` → `www.acude.uy` |
| DNS failure | 2 | DEAD | own domains, no wildcard zone; on 1.1.1.1 one is NXDOMAIN and one SERVFAIL |
| 401/403 with a REST refusal (`rest_cannot_access`, `itsec_rest_api_access_restricted`, `no_rest_api_sorry`, `rest_not_logged_in`, `wp_die`) | 11 | UNKNOWN | 10 of 11 inspected still ran the plugin: Boards the route cannot read |
| a certificate that does not verify | 13 | UNKNOWN | the scraper verifies; 7 of 427 first-pool Hiring Boards failed every read on theirs |
| 403 pages, 5xx, 429, timeouts, bot challenges | 19 | UNKNOWN | — |

The probe asks with `verify=True`, which `check_liveness._fetch` now lets a probe choose (every other
probe keeps its unverified default).

## Transport: the TLS fingerprint

The repo's HTTP session impersonates Chrome (`curl_cffi`, `impersonate="chrome"`). Hostinger's CDN
(`server: hcdn`) answers **403 to every Chrome, Safari and Edge fingerprint curl_cffi offers**
(`chrome`, `chrome99`…`chrome136`, `safari17_0`, `edge101`), whatever the User-Agent, and 200 to
`firefox133`, to plain curl and to `requests` (2 hosts, 10 targets each). Across the 481 candidates
**22 sites answered 403 on every path under Chrome and listed postings under Firefox**; 2 sites
(`ddat.org.uk`, `www.lifesafeacademy.com`) did the reverse, a JavaScript challenge under Firefox
only. So the scraper and its probe ask with `impersonate="firefox"`, a target curl_cffi has named
since 0.9 (the dependency floor moved there).

## Detail: the job page

The REST row carries no location, type or employer name: the specs are taxonomies without
`show_in_rest`, and `class_list` names only their slugs (`job-location-delhi-gurgaon` for "Delhi -
Gurgaon", `job-category-dataai` for "Data&AI") and only on WordPress 6.5+ — 17,576 of 18,126 rows;
21 of the 417 Boards carry none. The page carries:

- the plugin's JSON-LD `JobPosting` on 4,117 of 4,384 sampled pages. 216 answered 200 without it —
  every page of some sites (`abatec.co.uk` 27 of 30, `europeobserver.net` 12 of 12), some pages of
  others (`aqjobs.com` 14 of 30, where the rest carry it) — and those opened still showed the
  specifications block, which the scraper now reads on its own; the other 51 were lost to 404s,
  timeouts and refused connections;
- the specifications block (`awsm-job-specification-*`) on the pages of 263 of 417 Boards — a site
  can hide it — naming every spec the site defines: `job-location` 2,144, `job-type` 1,971,
  `job-category` 1,893, then site-made ones (`salary` 224, `industry` 88, `experience` 76, …).

Field shares over the 4,117 pages read: location 3,063 (470 of them several places, each kept,
"; "-joined), department 2,762, employment type 2,932, a `hiringOrganization` on 3,868. The
employer name agreed on at 0.9 names 371 of the 417 Boards; the rest are served under the host
spelled as a name.

**What the detail needs.** A plain GET of the row's `link`, `Accept: text/html`, the Firefox
fingerprint. It adds bytes: a job page is 140,884 B at the median (mean 169,523; 58 Boards), a
REST row 3,665 B (mean 4,989).

## The tech gate

`is_tech(title, department)` is asked before the detail with the title and the humanised
`job-category` slug off the REST row; `parse` serves the category's own name off the page where the
site shows it. Over the 4,384 sampled postings the page-side verdict called 857 tech; the gate
would have skipped **9 of them (1.1%)** — a vague title under a category the slug spells lossily
("R&D" as `rd`, "Data&AI" as `dataai`) or with no `class_list` at all ("Business Analyst" under
"IT"). It would have fetched 2 postings the page-side verdict calls non-tech. A measured
approximation, recorded in CONTEXT.md's **Detail pass** entry.

## Fields

- **Dates.** `posted_at` is the REST row's `date_gmt`, the WordPress publish time. On 58 Boards read
  twice, 2 s apart, every row's date was identical. Age over 18,126 postings: 8.2% posted within
  30 days, 19.0% within 90, 51.5% within a year, 72.4% within two. Many sites never close a
  posting; a Board whose newest posting is over two years old is **Dormant** (ADR-0250).
- **Remote.** No field states it; a site may name a location term "Remote" (97 of 3,063 located
  postings), which `is_remote` reads. JSON-LD `jobLocationType` never appears.
- **Salary.** None. The plugin has no pay field and its JSON-LD states no `baseSalary` (0 of 187).
  20 of 417 Boards define a pay spec of their own, under a dozen names and in free text
  ("Negotiable" 39, "Thoả thuận", "400万円～500万円"); the description is read for pay downstream.
- **Experience.** No field; 24 Boards define one ("Mid-Level", "3-5 Years", "Від 1 року"), left to
  the description.
- **Employment type.** The `job-type` terms as the site names them: "Full Time" 1,539, "Part Time"
  79, "Contract" 73, "Vollzeit" 65, "Apprenticeship" 33, …; 2,175 of 2,932 typed postings (74.2%)
  reach a search filter, the rest are other languages' words or site inventions.
- **Location.** One JSON-LD `Place` per location term; every one kept.
- **Department.** The `job-category` terms, which some sites fill with skills ("AWS Security;
  CloudFormation; …").
- **Company.** The JSON-LD `hiringOrganization`, a site-wide setting.

## Operating limits

- **Rate.** Concurrent job-page GETs on one site (`alligoric.com`): 1.2 req/s at 1, 3.1 at 4, 3.5
  at 8, 2.9 at 16, 2.5 at 32 while the median latency rose from 0.5 s to 11.2 s; `heptarc.com`
  1.9, 2.7, 2.9, 3.4 at 1/4/8/16. No refusal at any width. An uncached WordPress page is CPU-bound
  on the site's own server, so the Detail pass runs 4 wide. Each site is its own origin: no gate
  spans Boards.
- **User-Agent.** `headstart/0.1` is answered wherever any client is; the 403s above are the TLS
  fingerprint and the sites' own REST refusals, neither of which a User-Agent changes.
- **robots.txt** allows the route on 116 of 117 Hiring Boards and the job pages on all 117; the
  one exception is Yoast's "block REST API crawling" option (`www.neokraftmedical.com`).
- **Cost.** A run reads every row (~5.0 KB each) and the page of each posting the gate keeps
  (~170 KB): over the first pool, 18,126 × 5.0 KB + ~3,200 × 170 KB ≈ 0.63 GB for 3,183 tech
  postings, about **0.2 MB per tech Job**, a tenth of ADR-0158's 2 MB bar.

## Population

- **Tech share** (title and slug department): 3,183 of 18,126 postings (17.6%) on 417 Boards.
- **Language** (`doc_prep.is_english` over title and body): 15,126 of 18,126 (83.4%) English.
- **Volume** is skewed: `findmyjob.lk`, a Sri Lankan job board re-posting other employers' roles,
  lists 7,040 (1,332 tech); the next largest are staffing and recruitment firms (`angelandgenie.com`
  788, `laboral.perceptual.cl` 717, `alligoric.com` 629). No Operator label is set for any of them
  here; `boards/board_operator.py` is curated separately.
- **Overlap with other ATSes**: none by construction — a site running the plugin stores its own
  applications. A company can still post the same role on another ATS the repo holds; nothing here
  joins them.

## Discovery

- **Common Crawl's columnar index** (`mine_wp_job_openings.py mine`): the plugin has no vendor host,
  so the CDX API has nothing to sweep, but the Parquet index can be filtered on `url_path` and
  `url_query` across every host (DuckDB over HTTPS reads those two columns, ~63 MB of each 370 MB
  part file). A fingerprint only a site running the plugin serves — the `awsm_job_openings` post
  type in a guid link, REST route or sitemap name, or the plugin's asset directory — kept 2,689 of
  2,887 captured URLs in the first two crawls and dropped 123 hosts (90 of them wordpress.org's
  localised plugin pages). Hosts per crawl, and new over the newer crawls: `CC-MAIN-2026-39` 552,
  `CC-MAIN-2026-34` 540 (326 new), `CC-MAIN-2026-30` (171 new on its first 60%).
- **urlscan.io** (`mine_wp_job_openings.py urlscan`): `filename:wp-job-openings` returns the scans
  whose page loaded a file of the plugin's, 100 a query, paged with `search_after`; the search
  reports 10,000+ such scans.
- **The fingerprinter** (`fingerprint_careers.py`) now recognises the plugin's asset path and names
  the page's own host as the Board.
- **Wayback** has no fit: its CDX API cannot search a path across hosts.
