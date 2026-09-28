# Happydance career fronts: sitemap and job-page measurement (2026-09-28)

What the Happydance scraper (`src/headstart/scrapers/happydance.py`, ADR-0264) was built on. All
numbers were taken live on 2026-09-28. The probe scripts and captures are kept locally in
`experiment/happydance-sitemap/` (not committed); every figure a reader needs is below.

## Identity and discovery

- Happydance is Ph.Creative's career-site platform (it also runs "Gem Career Sites"). A front is a
  **Career front** on the customer's own host.
- 40 fronts found. Candidates came from urlscan.io scans loading `happydance.love`, a CNAME sweep
  of 24,651 Indeed apply hosts, hosts third-party scrapers read `a#js-apply-external` on, and
  customer labels on crt.sh's `%.happydance.website` certificates (20 customers). All 40 CNAME
  to `careers.happydance.website` (38) or `phcreative-dzavffavg8eafgdq.z01.azurefd.net` (2:
  `www.grab.careers`, `www.lloydsbankinggrouptalent.com`). `/phb/app.js.v{hash}` is on 16 of
  the first 35 only, so the CNAME is the fingerprint. `scripts/discover/mine_happydance.py`
  reruns the sieve.
- The Board is the host. A posting is listed once per locale under its req id
  (`careers.cognizant.com`: 2,042 reqs x 18 locales), every locale measured lists the same reqs
  (Cognizant, Caterpillar, Hilti, J&J, Coupang; Richemont's non-English locales list subsets of
  `en`), and the first URL listed per req is on an English locale on every front.

## Listing

- `/sitemap.xml` is a `<urlset>` on 27 of the first 35 fronts (classic template) and a
  `<sitemapindex>` on 8 (Next.js template: children `/pages.xml` + `/jobs/sitemap.xml`, or per
  locale). No other listing surface: `/jobs/sitemap.xml`, `/en/jobs/sitemap.xml`,
  `/sitemap_index.xml`, `/jobs.xml`, `/feed/`, `/en/jobs/rss/` all 404 on Cognizant, Hilti and
  Caterpillar. robots.txt allows `*` (Cognizant, Hilti).
- Job URL: `/[{prefix}/[{prefix}/]]{word}/{req}/{slug}/`, the word localised (`empleos`,
  `offres-d-emploi`, `职位`, `仕事`) and the req carrying a digit (`00064794373`, `r0000269418`,
  `13139-de`, `jr-007731`). Against the listing page's own count (`data-results`, 7 fronts):
  Cognizant 2,042/2,042, Caterpillar 936/936, Richemont 1,442/1,442, Coupang 702/702, MGM
  467/467, Centene 291/291, J&J 1,703/1,704.
- Six fronts use other shapes and read no job: `/jobs/job/{slug}/` (Gartner, Assurant, Tipico),
  `/en/jobs/{n}/{JOBn}/{slug}/` (Intuitive), `/en/jobs/{n}/` (Uber), Lloyds.

## Job pages

736 pages requested evenly over 34 fronts' sitemaps through the spare egress at 2 req/s; 579
answered 200, 0 answered 429, the rest were spare-egress connection errors.

- Classic template: JSON-LD `JobPosting` on every page (442 of 442 on 22 fronts); the type is
  written `application/ld&#x2B;json` on 13 fronts, `application/ld+json` on 9.
- Next.js template: JSON-LD on open pages of Prisma Health (18), Republic Airways (21),
  Warburtons (4), SAP (3); none on Aristocrat (9), National Grid (11), Verizon (25), whose data is
  only in the React payload. 17 of 108 Next.js pages are the site's shell with neither JSON-LD nor
  `<meta name="JobIdentifier">`: closed postings the sitemap still lists (Warburtons' own listing
  names 49 postings, including 7880, which has JSON-LD, and not 8250 or 8786, which are shells).
  The meta tag is on every other page sampled except Equifax's 25 (all with JSON-LD).
- Fields: `industry` states the department on every JobPosting (a string, or a list on 6
  fronts); `employmentType` FULL_TIME / PART_TIME / OTHER / INTERN / CONTRACTOR; `TELECOMMUTE` on
  12 fronts; `datePosted` a date or a full timestamp; `baseSalary` with an amount on 3 pages
  (Coupa, Warburtons, SAP), all `unitText: YEAR`; `hiringOrganization` the same name on every
  page of each front. The 6 plain-type fronts (Equifax, Hilti, Thrivent, Flutter's three) give a
  Place a list of addresses (105 of 105 pages).
- Apply: the first `<a>` whose class or id holds `js-apply` (`js-apply-external` on most,
  `js-apply-internal-1` on Coupa, `js-apply-now` on Flutter UKI). Box, Dropbox, Pinterest, Coupang,
  Warburtons, Fidelity and Fidelity TalentSource take the application on the front.
- Tech gate on the URL slug: `is_tech(slug)` missed 30 of 117 postings `is_tech(title,
  industry)` keeps (25.6%, 442 pages). No gate.
- Bytes per page: Cognizant 73,657, Hilti 66,276, Warburtons 675,622 (averages over 25, 22, 9).

## Rate limit

- One front, direct egress, curl_cffi Chrome TLS: 1 worker 15/15 at 1.6 req/s; 2 workers 30/30 at
  3.3; 4 workers 60/60 at 5.8; 8 workers 120/120 at 15.0; 16 workers 98 answered 200 then 102
  answered `429` with `cf-mitigated: challenge` ("Just a moment...") at 33.6 req/s.
- The wall then covered every front's HTML, including fronts never requested (`jobs.gartner.com`,
  `careers.prismahealth.org`, `careers.rjet.com`, `jobs.sap.com`) and the Next.js child sitemaps,
  from ~16:20 to 17:20:36 IST, polled every 30 s. The root `/sitemap.xml` answered 200 throughout.
  The spare egress (WARP) answered 200 during the wall.
- Plain curl with `headstart/0.1` gets the 403 challenge page on a job page even unwalled; Chrome
  TLS gets 200 with either User-Agent.

## Backing Boards

Each sampled page's apply URL was resolved through `front_duplication.backing_board` against the
committed ledgers (157,274 Scrapable Boards). For fronts that apply on the front, the backing was
settled by Greenhouse job ids (`boards-api.greenhouse.io`: Box 101/101 on `boxinc`, Dropbox 42/42,
Pinterest 150/150, Coupang 702/702) or by employer. Hilti applies on `avature:hilticareers`, whose
own listing (read through the Avature scraper) names 954 ids, 811 of the front's 815. The
per-front table is in ADR-0264. Three fronts have no held Backing Board: Cognizant (18 of 25
apply on `cognizant.taleo.net` Lateral/cam sections, all `unknown` in the Taleo ledger; 7 on
`talent.cognizant.com`, unsupported), Fidelity TalentSource (a Beamery form on the front; 3 of 24
sampled titles, all "Full Stack Engineer", are also on Fidelity's Workday), and SAP (3 of 4 on
`smartrecruiters:SAPITBusinessSysteme`, which lists the same 4 and no ledger holds).

## Liveness

`p_happydance` over the 40-host pool: 34 live, 6 unknown (the other-shape fronts), 0 dead. No
departed front was found, so DEAD rests only on DNS failure or a 404/410 sitemap.

## Whole-Board fetch

`get_scraper("happydance", "careers.cognizant.com").fetch()` on direct egress: 2,040 Jobs in
1,082 s, no page lost, no 429. Non-null share: title, company, department, url, posted_at,
description and employment_type 100%, location and remote 99.9%, salary 0%. 1,486 of 2,040 are
tech by `is_tech(title, department)`. Front duplication: 0 of 2,040 apply on a Scrapable Board.
