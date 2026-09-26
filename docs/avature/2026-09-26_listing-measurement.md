# Avature: listing, job-page and rate-limit measurement (2026-09-26)

Everything the Avature scraper (`src/headstart/scrapers/avature.py`, ADR-0245) rests on, measured
live against real tenants on 2026-09-26. The probe scripts and raw captures were kept locally
under `experiment/avature-listing/` (not committed); every number a reader needs is below.

Samples: the 140-label seed pool (kalil0321/ats-scrapers and OpenPostings seed lists), of which
77 tenants were surveyed portal by portal; 257 job pages from 55 portals of those tenants; and
Bloomberg (`bloomberg.avature.net`, 352 public postings) for everything that needed one large
Board.

## Identity

**A tenant is its host label** (`bloomberg` → `bloomberg.avature.net`). A tenant runs several
**Portals**, each a path on its host (`/careers`, `/internalcareers`, `/oldcareersportal`, events
and scheduling pages), and its `robots.txt` names each one's `sitemap_index.xml`. Bloomberg's
names 18; most are not job portals (their sitemaps list no `JobDetail` URL).

**Job ids are tenant-wide.** Bloomberg's `careers` (352 ids) and `internalcareers` (450) share
257 ids, and each shared id is one posting (same slug, same title) under both. bmcrecruit's
`Careers` (76) and `oldcareersportal` (74) share 69; fonterra's `careers` and `examplePathName`
(a template leftover) list the same 28. So the Board is the tenant, and its listing is the union
of its portals' sitemaps by id: duplicate portals collapse without an alias ledger, and a new
portal is picked up without a ledger change. Chosen over a Board per portal by the owner.

**Sitemap URLs must be read verbatim.** Some tenants serve portals from a vanity host named in
their robots.txt: `fb` (Fletcher Building) → `careers.fbcareers.com`, `bmcrecruit` →
`jobs.bmc.com`, `loa` (L'Oréal) → `career.loreal.com`. Rebuilding the URL from the label missed
Fletcher Building's 202 postings in the survey.

## Listing

| Surface | What it serves | Verdict |
| --- | --- | --- |
| `/{portal}/SearchJobs/` HTML | 12 rows a page; `jobRecordsPerPage=500` still 12; rows state title and location, no department | Rejected: 30 requests for bloomberg's 352, nothing the sitemap lacks |
| `/{portal}/SearchJobs/feed/` RSS | 20 items, any parameters | Rejected: capped |
| `/{portal}/SearchJobsData/` | Empty 254-byte shell: the Google Maps marker feed, off on the portals seen | Rejected |
| `/{portal}/sitemap_index.xml` → per-locale `sitemap.xml` | Every `JobDetail` URL in one fetch | **Chosen** |

Bloomberg's `careers` sitemap held 352 `JobDetail` URLs against its search page's "352
results". **A sitemap can keep closed postings**: bupaanz's `careersau` sitemap lists 1,701 ids
while its search page reads "of 999", and 6 of 6 sampled ids redirect to `/careersau/Error`
(they are old ids, 23238–53463, where the search page lists 66xxx). Those ids fail under the
sibling portal `careersnz` too (4 of 4), so they are closed, not misfiled.

## Job pages

- **Closed and private postings redirect.** A closed posting's page answers `302 → /{portal}/Error`
  (a 404 once followed). An internal portal's pages answer `302 → /{portal}/Login/` (12 of 12
  on bloomberg and broadinstitute), then on to the tenant's SSO host. Job pages are therefore
  fetched with redirects off and the `Location` read; both are counted as "not public", never as
  a lost detail, so a Board's closed postings cannot truncate it.
- **A login-walled portal is settled by one probe.** If a private-named portal's first own job
  page redirects to `/Login/`, its other own ids are dropped unread. On Bloomberg that cut a
  gated run from 191 job pages (103 of them internal-only) and 227 s to 88 pages and 126 s, for
  the same 88 Jobs.
- **A job page can move to another job page**: cyclecarriage's sitemap names `/en_US/careers/…`
  URLs that 302 to the same path without the locale (6 of 6). Those are followed once.
- **No one layout.** Of 26 readable portals, 9 carry JSON-LD, 19 carry label/value rows in one of
  two class schemes (`article__content__view__field__label`/`__value`, and
  `article--details__label`/`__value` spans), astellasjapan uses `<strong>` label paragraphs, and
  2 carry neither. Labels are the tenant's own words and language: location is "Location",
  "Advertising location", "Job Posting Location - City, State", "Región", "Country/Region",
  "Site", "勤務地". One tenant's JSON-LD does not parse (frequentis: a missing delimiter).
- **Field coverage** of the shipped parser over the 257 pages: title 97%, description 91%
  (median 4,958 characters), location 72%, company 65%, posted date 39% (JSON-LD `datePosted`
  only), department 34%, employment type 32%, remote 19%. The misses are tenant templates: L'Oréal's
  locale portals (26 pages, fully custom), and tenants that state location outside any label
  (ea, lululemon, ecb, frequentis).
- **Company name**: `og:site_name` names the employer on 15 of 26 portals ("Bloomberg",
  "Electronic Arts", "Deloitte Italia"); JSON-LD `hiringOrganization` covers some of the rest.
  The scraper takes the name its fetched pages agree on — no extra request.

## Tech gate

No listing surface states a department, so the pre-detail gate reads the title in the URL slug
(`Senior-Software-Engineer-VAULT` → "Senior Software Engineer VAULT"). Against the real verdict
(`is_tech(page title, department label or JSON-LD occupationalCategory)`) over 244 titled
pages: 52 tech (21.3%); the gate kept 47 (**90.4% recall**) and passed no page the real verdict
drops. The 5 misses: 3 promoted by department (Bloomberg "Engineering and CTO", Lenovo
"Hardware Engineering" and "Information Technology") and 2 retitled postings whose slug keeps
the original title (BMC: slug "Staff Specialist Technology Solutions Specialist India", title
"Senior AI Engineer"). It skips 79% of job pages.

## Rate limit

**One budget per client IP, across every tenant.** Past it every request to every tenant
answers `406 Not Acceptable` (a 172-byte nginx page) for 3–4 minutes (four blocks timed: ~4 min,
244 s, ~3 min, and one still standing at 2 min), while the same URLs answered 200 over WARP.
Chrome TLS impersonation does not get through a block (406 with and without it).

| Run | Requests before the first 406 | Rate |
| --- | --- | --- |
| Serial, 1 per 3 s | none in 300 | 0.33/s |
| Rising concurrency c=2, 4, 8, 16 (60 each) | none in 240 | 1.2 → 7.4/s |
| Sustained c=16 then c=32 (400 each), straight after | none in 800 | 8.2, 5.5/s |
| Paced, one start every 0.5 s | **892** clean, 893rd refused, 446 s in | 2.0/s |

A token bucket fits the two clean runs: ~350 requests of burst refilling at ~1.2/s. So the
scraper spaces every request process-wide at 1.0 s and moves to the spare egress on a 406.
Concurrency past the pace buys nothing: latency is a flat ~1.7 s up to c=16, and c=32 served
fewer requests per second (5.5) than c=16 (8.2).

**One session's requests are serialised.** Eight concurrent job-page fetches on the repo's own
async transport, sharing the `ScustomPortal-4` cookie, ran at 1.41/s; four without cookies ran
at 2.41/s. Job pages are fetched with `discard_cookies`.

**User-Agent**: `headstart/0.1` is served (no UA wall seen). `avature.net` hosts CNAME to
`iatsapp-prod-*.avature.net` clusters with A records only, so the spare egress leaves over WARP's
IPv4 pool.

## Population

- Pool: 1,008 labels — Wayback 981 (620 only there), Common Crawl across 33 crawls 364 (14 only
  there), seed lists 140 (12 only there).
- Largest Boards seen: boozallen 2,401, bupaanz 1,701 (sitemap, closed ids included), deloittece
  492, advocateaurorahealth 660.
- Tech share 21.3% of 244 pages; language is mixed (German, Spanish, Japanese and Chinese pages in
  the sample), and the index's language gate holds the non-English ones out.
- **Intuit is not reachable here.** `intuit.avature.net` names portals whose sitemaps list no
  job pages and whose search page 404s; its public listing is `jobs.intuit.com`, a Radancy
  career front whose Apply button hands off to this tenant.
