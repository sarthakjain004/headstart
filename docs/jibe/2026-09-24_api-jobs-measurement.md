# Jibe `/api/jobs`: what the career-site API actually does

Measured 2026-09-24, before any `headstart.scrapers.jibe` code was written, against live hosts.
Jibe is iCIMS's career-site layer (iCIMS bought it in 2019): an employer's branded career site
(`careers.costco.com`, `careers.rm.com`) is a Jibe site, and its postings come from the employer's
ATS, usually an iCIMS tenant. The probe scripts, raw captures and census tables are kept locally,
not committed; every number a reader needs is stated here.

No upstream implementation in `kalil0321/ats-scrapers` covers Jibe. Four others were read:
`career-ops-hq/career-ops` (`providers/jibeapply.mjs`), `datascry/openroles`
(`scraper/src/ats/jibeapply.ts`), `Masterjx9/OpenPostings` (`server/ats/jibeapply/service.js`) and
`Fighter90/career-ops-ui`. Where they disagree with a measurement below, the measurement wins.

**Sample.**

- **Seed walk.** The 278 Jibe vanity hosts the Indeed sweep traced (#576), each walked to the end
  of its listing at one request per 5 s: 2,250 requests, 151,619 listing rows. 276 hosts serve
  the Jibe schema, 1 serves a legacy iCIMS schema (`careers.edgewoodproperties.com`) and 1 is a
  vendor demo (`demo281.jibeapply.com`).
- **Client probe.** 1,244 candidate client labels, from the seed, Wayback, Common Crawl and a DNS
  sweep, each probed once at `{client}.jibeapply.com`: robots.txt, `/`, the `/jobs` board page
  and `/api/jobs` page 1. That is about 4,500 requests.
- **Targeted probes.** Listing against detail on 90 postings from 30 hosts. Search overrides on 78
  sites. Full walks of 4 Boards to check for page overlap. Window, sort and facet probes on the 2
  capped Boards. robots.txt of 85 backing iCIMS tenants, then robots.txt and sitemap of the 789
  held iCIMS tenants behind the probed clients.

## Identity

**Q1: what is the slug?** The **Jibe client id**, served at `{client}.jibeapply.com`. Every
vanity career site is one client's site, and the same client answers at its own
`jibeapply.com` host:

- **The client id.** The rows' `client_code` field names it, on all rows of 211 of the 277
  walked hosts. On the other 66 hosts, the board page's `_jibe = {"cid": "…"}` object is the only
  source.
- **Where the two agree.** They agree on 207 of 211 hosts. The 4 exceptions are page-template
  leftovers: `careers.arcusfm.com`, `jobs.tufts.edu` and `careers.xactlycorp.com` carry
  `cid: "demant"`, and `careers.colliersengineering.com` carries the typo
  `colliersengingeering`. Their `client_code` is right in all 4 cases.
- **Same Board at both hosts.** `{client}.jibeapply.com/api/jobs` returns the same `totalCount` as
  the vanity host on all 276 hosts whose client resolved. That is 272 of 276 through the page cid,
  plus the 4 corrected through `client_code`.
- **One client, several vanity sites.** Rollins runs 11 vanity sites, MasTec 8 and TKC 3. Each
  site's `/api/jobs` returns the whole client's set (Q5). So the client is the Board, and a vanity
  host is one spelling of it.
- **Ids.** `slug`/`req_id` is the backing ATS's requisition id. It is all digits on 138,304 rows
  and digits plus hyphen or letters on the rest (Oracle and Cadient ids), and never contains a
  `:`.
- **Case.** DNS is case-insensitive. `client_code` is lowercase on every row seen.

**Q2: which spellings does discovery produce?** Two kinds of host:

- **The client host, `{client}.jibeapply.com`.** Wayback, Common Crawl and the DNS sweep emit
  these labels directly.
- **A vanity host.** The Indeed sweep and the careers-page fingerprinter find a vanity host, whose
  CNAME is `{vanity}.jibeapply.com` or `{vanity}.career.page`.

A vanity host resolves to its client with one listing request: read `client_code`, else the page
cid. `*.career.page` hosts (26 seen) are vanity hosts too: `concentra.career.page` is client
`concentrahealthservices`, and `mofo.career.page` is client `mofo`. `*.staging.jibeapply.com`
hosts (2 in Common Crawl) are test hosts.

## Listing

**Q3: which surface?** `GET /api/jobs?page={n}&limit=100&internal=false` on the client host. It
is the JSON the board's own Angular bundle calls (`app.jibecdn.com/prod/search/4.11.219/main.js`
builds `/api/jobs` with `internal: "false"` plus the site's `searchOverride`). The alternatives
lose:

- **`/api/jobs/{slug}/{lang}` (detail).** It adds nothing (Q6).
- **`/sitemap.xml`.** robots.txt names it, but it carries URLs only, no fields.
- **The server-rendered `/jobs/{slug}` page.** 480 KB of HTML per posting.

**Q4: how does pagination work?** `page` is 1-based.

- **Page size.** `limit` is capped at 100: 100 answers 200, while 101, 150, 200, 250, 500 and
  1000 answer **422** `{"error":"An unexpected error occurred"}`. So upstream's "limit is the page
  size" holds only up to 100. career-ops's default of 10 rows per page is the server's own default
  when `limit` is omitted.
- **Totals match.** On 271 of 275 walked Boards, the rows served equal `totalCount`. On 2 others
  the count moved by 1 during the walk.
- **No page overlap.** Walking 4 Boards end to end gave 7,559 unique `(slug, language)` pairs in
  7,560 rows, against a `totalCount` of 7,560. The default order does not overlap or skip pages.
- **`totalCount` counts language rows, not postings.** One requisition published in two languages
  is two rows with the same `slug`. `careers.flyporter.com` serves 61 rows for 59 postings (2 in
  `fr-ca`), and Publicis 3,164 rows for 3,002 postings. The `count` field is neither figure
  (Publicis 2,524), so it is not used.
- **There is a hard window on some Boards.** Past row 5,100, every page repeats one fixed page:
  - **Costco.** Pages 51 through 202 at `limit=100` return the same 100 ids. At `limit=50`,
    pages 199 and 200 return that same set.
  - **UHS.** The same pattern: 5,092 unique ids read of a `totalCount` of 6,056.
  - **Boards over the window with no cap.** PetSmart (10,943) and JCPenney (6,925) walk to their
    full totals.
  - **So the cap varies by Board.** Of the 1,116 live clients, 8 are over 5,100: costco 20,093,
    hrblock 16,924, greatclips 11,858, petsmart 10,943, ulta 9,985, jcpenney 6,925, commonspirit
    6,199 and uhs 6,056. Four of the 8 are measured, and 2 of those 4 are capped.
- **The window is per query, so facets can subdivide it.** On Costco:
  - `state=California` answers `totalCount` 4,257. Its page 43 holds 57 rows, and page 51 is
    empty.
  - Costco's `facetList.state` terms sum to exactly 20,093, the Board's total.
  - UHS has no `state` facet. Its 106 `categories` sum to 11,990, because a posting can carry
    several categories, and the largest is 1,490.
- **Sorting cannot reach past the window.** `sortBy=posted_date` returns the same first ids for
  `descending=true` and `false`.
- **Terminators.** Stop when the rows read reach `totalCount`, when a page comes back empty, or
  when a page brings no new `(slug, language)` pair, which is the window signal. The earliest
  upstream stops at a fixed `MAX_PAGES` of 25, 50 or 60 pages, which would cut PetSmart at 6,000
  rows.

**Q5: is any parameter a filter rather than an address?**

- **`searchOverride`.** A site's board page can carry one, for example `{"tags2": "MasTec Utility
  Services|…"}` or `{"brand": "JCPenney|Brooks Brothers"}`. The board sends it as query
  parameters, so it filters: `jobs.mastec.com` shows 383 of MasTec's 1,346 rows, and
  `careers.rollins.com` shows 9 of Rollins's 1,274. **Without it, `/api/jobs` returns the
  client's union.**
- **Is the union public?** 72 of Rollins' 1,274 rows fall outside the 10 `tags3` overrides its
  sites use. 15 are Clark Pest, which `careers.clarkpest.com` selects by `brand`. The other 57
  belong to brands whose sites we have not found: Fox Pest 34, IFC 12, Bug House 8 and PermaTreat 3.
  Their `/jobs/{slug}` pages on the client host answer 200 with the posting: 3 of 3 checked (2 Fox
  Pest, 1 IFC). The union is the client's public set, so the scraper sends no override.
- **`internal`.** It is a filter. `internal=true` returns a different set: 15 internal-only rows
  on RM, where the public board shows 16. `internal=false` is what every board sends, and every
  walked row carries `internal: false`.

**Q6: does the listing carry the description?** Yes, in full. It is present on 151,619 of 151,619
rows.

- **Against the detail.** `html_to_text(listing description)` equals the detail's
  (`/api/jobs/{slug}/{lang}`) on 77 of 90 postings from 30 random hosts. The other 13 differ by
  1 to 7 characters of whitespace or entities.
- **What the detail adds.** `benefits` and `additional_locations`, both empty on all 90. The
  listing already carries additional places when they exist, 8,499 rows.
- **The separate fields.** `qualifications` and `responsibilities` are already inside
  `description`.

So there is **no detail pass**. openroles is right on this, and the raw-length comparison that
first suggested otherwise was markup, not text.

**Q7: does the API return rows the public board hides?**

- **Internal rows.** Only with `internal=true`, which the scraper never sends.
- **Override-hidden rows.** The union rows a site's override hides are public on the client host
  (Q5).
- **The other flags.** `searchable`, `applyable` and `jps_is_public` are true on every walked row.

## Dead versus empty

**Q8: what does an unknown slug return?** **No A record.** `jibeapply.com` answers an unknown
label NOERROR with an empty answer section, not NXDOMAIN (1.1.1.1, 8.8.8.8 and 9.9.9.9 alike), so
only a provisioned client resolves:

- **Invented labels.** `zzzzqqq.jibeapply.com` has no A record; curl reports "could not resolve
  host".
- **Wayback's historical labels.** Of the first 563 candidate labels, 101 have no A record. Among
  them are `att`, `comcast`, `celgene`, `acelity` and `cenveo`, Jibe customers before 2019.
- **Resolve on a public resolver.** Under a 64-thread sweep, the macOS system resolver answered
  "no such host" for live clients (`uhs`). Every label was therefore re-resolved on public
  resolvers with dnspython: 31,514 labels, 1,143 with an A record, and no disagreement with the
  earlier, lighter-load sweeps.

A live Board with no openings answers `200 {"jobs":[],"totalCount":0,…}`. There are 254 of them;
many have an "Apply" board title and use Jibe for the apply flow only. So `totalCount` alone
separates hiring from empty, and DNS separates dead.

- **Resolving labels that are not Boards.** 27 of 1,143 resolving labels are not a readable
  Board. 21 answer `/api/jobs` with 404 `{"error":"An unexpected error occurred"}` (among them
  `dycom`, `realmanage`, `teksystems` and `test3`), 2 answer non-JSON (`fedex` is an Okta SSO
  form, and `ehhi` an IIS page), 1 answers 500, 2 fail TLS (`email`, `mail`) and 1 redirects in a
  loop (`pcgus`). The prober calls these UNKNOWN, not dead.
- **Carrefour.** `carrefour.jibeapply.com` serves `Disallow: /` in robots.txt (below).

**Q9: where does a departed tenant go?** Its DNS label loses its A record (above). Nothing
redirects to the vendor's site. `/` redirects on 493 client hosts: 478 to a relative path on the
same host (`/careers-home`, `/jobs`, `/{client}/`), and 15 to the employer's own careers domain
(`www.github.careers`, `careers.hrblock.com`), whose `/api/jobs` on the client host still
answers.

## robots.txt

Two hosts are involved, and each has its own robots.txt.

- **The Jibe host.** This covers every `{client}.jibeapply.com`, the vanity host and the
  `*.career.page` host. It serves `User-agent: * / Allow: / / Sitemap: … / crawl-delay: 5` on
  1,138 of 1,143 resolving client hosts and 275 of 276 walked vanity hosts. The exceptions:
  - `carrefour.jibeapply.com`: `Disallow: /` with `crawl-delay: 5`. The client probe read its
    robots.txt but did not act on it, so it made one `/api/jobs` request (930 rows) before this
    was noticed. Nothing reads it again.
  - `www.jibeapply.com`: iCIMS's own marketing robots.
  - `email`, `mail` and `uri`: not Boards.
  - The legacy vanity host: 404.
- **The backing iCIMS tenant.** The host behind `apply_url` (`careers-rmeducation.icims.com`).
  - **Behind the #576 seed.** `User-agent: * / Disallow: /` on 82 of 85 distinct sampled
    tenants: 79 of a random 80, plus goauto, concorde and uti, whose ledger rows say live. The
    other 3 are Cordis's tenants (`careerschi-`, `careersger-`, `careersjap-cordis`), which allow
    crawling.
  - **Behind the whole client pool.** Of the 789 tenants our iCIMS ledger holds live behind the
    1,116 clients, **774 allow crawling and serve a readable sitemap**. The other 15 now serve
    `Disallow: /` and answer 403 on the sitemap. `careersen-goauto`, `careers-concorde` and
    `careers-uti` flipped since their 2026-09-08 ledger rows.

A Jibe reader requests only the Jibe host: robots.txt, `/api/jobs` and, for the job link,
`/jobs/{slug}` on the same host. It never requests the iCIMS tenant.

## Detail

**Q10–Q12: is there a detail pass?** There is none (Q6), so there is no per-Job request, token or
charset issue, and no tech gate.

## Fields (151,619 walked rows unless stated)

**Q13: are dates real?** `posted_date` is present on 99.4% of rows.

- **Formats.** ISO `YYYY-MM-DDTHH:MM:SS+0000` on 147,276 rows. `"Month D, YYYY"` on 3,541 rows, all
  on one client (`se`, Schneider Electric).
- **Stable.** It did not move on 9,410 of 9,410 postings refetched about 1 hour apart.
- **Real.** It equals iCIMS's own `primary_posted_site_object.datePosted` on 9,166 of 9,166.
- **Ages.** 57,013 are under 30 days old, 43,894 are 30 to 90 days, 23,364 are 90 to 365 days and
  23,005 are older. No date is in the future.
- **`create_date`/`update_date`.** These are Jibe's ingestion times, not posting dates. For
  example, `se` rows posted in 2025 carry a `create_date` of 2026-06-01.

**Q14: which field states remote?** There is no native remote field.

- **`location_type`.** `ANY` on 2,966 rows is not "remote". It marks a vague location (507 are
  "United States"), and only 82 of the 2,966 name remote in the location.
- **Location text.** Remote appears in `full_location` on 144 rows in all, and in the title on
  427.
- **Tags.** Remote and hybrid appear in tenant-defined `tags*` values ("Not Remote", "Hybrid",
  "Onsite 100%"), each spelt by one tenant.
- **Decision.** `remote` falls back to `is_remote(location)`.

**Q15: salary.** Structured fields are populated on only 3 hosts: petsmart, pepsicojobs and
smoothieking, which are all non-iCIMS feeds (12,035 rows).

- **The fields.** `salary_min_value`/`salary_max_value` are 0 when unstated, the sentinel openroles
  names. `salary_frequency` is HOURLY on 10,069 rows, YEARLY on 1,733 and WEEKLY on 206.
  `salary_currency` is `USD` on 1,300 rows and empty on the rest. PetSmart states none even on its
  CA rows.
- **Shapes.** 11,307 rows give both bounds. 507 give a single `salary_value` with a frequency. 194
  give a ceiling alone, which must not be served as a floor. 79 say `HOURLY` over 79,000–130,000,
  a period that contradicts the figure.
- **iCIMS-backed Boards.** The salary lives in the description text or in tenant tags, where
  Tier-2 extraction reads it.

**Q16: experience and employment type.**

- **Employment type.** `employment_type` is present on 75.7% of rows, as schema.org enum values:
  FULL_TIME 84,722, PART_TIME 22,960, OTHER_EMPLOYMENT_TYPE 2,421, TEMPORARY 1,630, PER_DIEM 1,461,
  INTERN 955, CONTRACTOR 635 and CONTRACT_TO_HIRE 25. `employment_type.flags()` reads FULL_TIME,
  PART_TIME, INTERN, CONTRACTOR and CONTRACT_TO_HIRE correctly. TEMPORARY, PER_DIEM and OTHER set
  no flag, which is right, since none of them is one of the four filters.
- **Experience.** `experience_levels` is present on 0.4% of rows, so there is no usable native
  experience field.

**Q17: location.** `full_location` is present on 99.8% of rows. When `multipleLocations` is true
(8,499 rows), it already joins every place with `"; "`: its part count is 1 plus
`len(additional_locations)` on 8,501 of 8,501 rows. It repeats places ("Raleigh, North Carolina;
…; Raleigh, North Carolina"), so the scraper de-duplicates the parts.

**Q17a: department.** `department` is non-empty on 0.5% of rows. `categories[0].name` is present on
95.9%, as the board's own grouping ("Digital & Technology", "Nursing - (RN)"). openroles's fallback
from `department` to `categories[0]` is right.

**Q18: company name.**

- **`hiring_organization`.** Present on 77.8% of rows. It is single-valued on 200 of 277 hosts and
  missing on 9. On 68 hosts it varies per posting between brands and subsidiaries: Reyes names
  Reyes Beverage Group, Reyes Coca-Cola Bottling and Martin Brower, and Vibra names Vibra
  Healthcare, Vibra Travels and VibraLife. On UHS it is empty on 4,832 of 5,092 rows.
- **The board page `<title>`.** Present, but noisy: "X Careers" on 200 client hosts, "X Job Search
  - Jobs", "X Apply", "Home | Arcfield Careers" and a bare "Company".
- **The slug.** A readable word (`costco`, `rollins`, `thecheesecakefactory`).

## Operating limits

**Q19: rate limit.** Every request in this measurement was paced at 5 s per host, following the
robots.txt `crawl-delay: 5`, with up to 32 hosts in parallel. That was about 7,000 requests at 6 to
7 req/s across tenants.

- **Response codes.** The seed walk got 2,249 responses of 200 and 1 of 404. The client probe got
  1,118 responses of 200 on `/api/jobs`, and its only non-200s were the 21 404s and 1 500 of the
  resolving labels that are not Boards.
- **Latency.** `/api/jobs` p50 is 0.6 s and p95 is 0.93 s, with a maximum of 4.1 s over 1,694
  pages.
- **No per-host ramp.** Running one would break the `crawl-delay: 5` the hosts publish, so the
  single-tenant ceiling is deliberately **not measured**. The scraper and prober pace at 5 s per
  host by design, not by a measured knee.
- **Shared edge.** The clients sit on a few AWS address pools per region (`eu` clients on
  eu-central-1), but no cross-tenant refusal appeared at 32 parallel hosts.

**Q20: User-Agent.** `headstart/0.1`, curl's default, `python-requests/2.32` and no UA at all each
get the same 200 and body (1 host, `aarp`). The walk and all passes used `headstart/0.1`.

**Q21: response size.** 2.73 GB for 152,780 walked rows, or **17.9 KB per row** (per-Board median
17.7 KB). The iCIMS `config_keys` block, repeated on every row, is a large share of that. A
100-row page is 0.65 to 1.1 MB.

## Population

**Q22: tech share and volume.**

- **Seed walk.** After collapsing language rows (English first) and vanity aliases, the 249
  hiring clients hold **120,285 postings**, a median of 148 per Board. `is_tech(title,
  categories[0])` keeps **9,488 (7.9%)**. The largest tech holders are amd (1,051 of 1,251), se
  (962), mcdean (400), costco (340 of the first 5,099) and keysight (318).
- **Whole client pool.** 862 hiring clients and 254 empty ones. `totalCount` sums to 338,461
  rows, language rows included.

**Q23: language.** 95.2% of seed-walk postings are English (`en-*`). Next are `de` 2,224, `fr`
969, `es` 766 and `zh` 690.

**Q24: overlap with Boards already held.** This is the decisive finding. Each row's `apply_url`
names its backing Board. Joined against the liveness ledgers on the client pool's page 1 (100 rows
per client, scaled by `totalCount`):

| backing Board of a row | est. rows | share | est. tech |
|---|---:|---:|---:|
| iCIMS tenant our ledger holds **dead** (robots `Disallow: /`) | 219,845 | 65.0% | 16,592 |
| iCIMS tenant our ledger holds **live**, sitemap readable today | 90,608 | 26.8% | 6,112 |
| another ATS or feed we do not scrape (Cadient, PeopleSoft, ADP, Paradox, …) | 18,972 | 5.6% | 114 |
| a Board another of our ledgers holds live (Workday, Oracle) | 5,253 | 1.6% | 256 |
| an iCIMS tenant not in our ledger | 3,782 | 1.1% | 148 |

- **The seed was not representative.** It found 290 overlapping rows in 151,531, 0.2%, but it was
  Indeed's sample of disallowed tenants. The DNS sweep that grew the pool was keyed on our own
  iCIMS ledger's labels, so it found the clients of readable tenants too.
- **Same posting, two ids.** A Jibe row on a readable tenant is the same posting the iCIMS scraper
  already serves as `icims:{tenant}:{id}`. The Jibe `slug` is that id.
- **Clients by where most of their rows sit.** 496 on dead iCIMS tenants, 330 on readable
  held ones, 18 on unheld iCIMS tenants, 11 on other backends and 7 on other held ATSes.
- **Workday and Oracle collisions, re-measured live.** fedexfreight (Jibe 684, Workday 673),
  spglobal (Jibe 293, Workday 292), stjude, mercy, aidt and mortonfinancial are Workday; mountsinai
  and marriott are Oracle.
- **The iCIMS ledger has gone stale here.** 3 of its live rows (goauto, concorde, uti) now 403,
  and the iCIMS reads of those Boards fail.

## Discovery

- **Seed (#576).** 278 vanity hosts, resolving to 250 client ids.
- **Wayback.** A `jibeapply.com` host sweep found 412 single-label client hosts. The `career.page`
  sweep found 15 vanity hosts.
- **Common Crawl.** Read off `data.commoncrawl.org`, all 33 crawls from `CC-MAIN-2023-40` to
  `CC-MAIN-2026-39`, for `jibeapply.com` and `career.page` (66 reads, 0 failures). It found 136
  single-label client hosts, 6 of them new, plus 11 `career.page` hosts and 2 staging hosts.
- **DNS sweep.** Every label of the iCIMS ledger was split into candidate client labels: the part
  after the first hyphen, each hyphen component, and the label with its hyphens removed. That is
  31,418 labels, and **1,112 resolve** at `{label}.jibeapply.com`. They include 431 of the 462
  resolving labels already known, so this channel alone has about 93% recall.
- **Wider DNS sweep.** Every other ledger's tenant labels and vanity registrable names: 225,055
  labels, resolved on public resolvers, **29 with an A record**, 23 of them new. Jibe's clients
  are iCIMS customers, so a non-iCIMS label almost never names one.
- **Vanity hosts.** `mine_jibe.py --vanity` mapped 11 of the 15 `career.page` hosts to clients (1
  new); 3 redirect robots.txt off-host and stay unresolved, and 1 has no listing.
- **Union: 1,268 candidate labels.** By source, with the count only that source found: Indeed
  sweep 250 (0), Wayback 412 (85), Common Crawl 136 (0), DNS 1,166 (692), vanity 11 (1).
- **Ledger (`check_liveness.py jibe`).** 1,137 live, 104 dead, 27 unknown. The unknowns: 22
  listing 404s, 1 robots `Disallow` (carrefour), 4 robots unreachable or TLS failures.

## What the ledger found that the pool did not show

- **One Board under two labels.** `rentokil` and `rentokil-initial` serve identical listings (749
  rows, same ids). The second is in `data/validate/aliases/jibe.csv`.
- **Not boards of openings (excluded).** `fedex` lists 136,186 rows from
  `ats_code: fedex-prod-historical-jobs-feed`: page 1 is 98 postings from 2024 and 2 from 2025, and
  its board page redirects to an Okta login. `hexdigital` is Jibe's demo client: 906 rows,
  page 1 all "Software Engineer" with `hiring_organization: Jibe` or none, and no `apply_url` on 98
  of 100. `launch` is another demo: 25 stock titles with no dates. `mortonfinancial` is test data
  (`ats_code: test-bank`, `jobs-notacustomereutest.icims.com`, dates from 2018). `testaxa` and
  `axatest` are AXA's UAT site. `discovery1` lists one "TEST REQ APRIL- DO NOT APPLY". `icims` and
  `template` are the vendor's own labels.
- **Wholly on a held Board (parked).** Each client's whole listing was walked and every `apply_url`
  host joined to the ledgers: stjude 163 of 163, spglobal 292 of 292, fedexfreight 677 of 677 and
  mercy 2,257 of 2,257 sit on live Workday rows; mountsinai 1,821 of 1,821 and marriott 1 of 1 on
  live Oracle rows. `aidt`, flagged from its page 1, has 1 of 25 on Workday and is kept.

## The largest Boards, end to end

The real scraper on the four largest readable clients, at 5 s per request:

| client | totalCount | Jobs | time | split by |
|---|---:|---:|---:|---|
| costco | 20,093 | 20,093 | 1,145 s | 49 `state` terms |
| hrblock | 16,924 | 16,924 | 1,014 s | 54 `state` terms |
| greatclips | 11,858 | 11,856 | 642 s | 12 categories (no `state` facet) |
| uhs | 6,056 | 5,774 | 977 s | 106 categories; 273 postings dropped as iCIMS-covered |

None was truncated. uhs's Jobs are fewer than its rows because rows are per language and 273
postings sit on its two readable iCIMS tenants.

Every field on these four is set on every Job except `department` (costco 19,359 of 20,093),
`employment_type` (costco 589, uhs 5,117) and `salary` (none; these are iCIMS-backed or unstated).

**Job links.** `https://{client}.jibeapply.com/jobs/{slug}` answers 200 with the posting's own
title on 60 of 60 random clients; 13 of them reach it through an on-host redirect
(`/careers-home/jobs/…`, `/booking/jobs/…`). `pepsico`'s letter-and-hyphen ids do too
(`/jobs/P1-6651123-2` redirects to `/main/jobs/P1-6651123-2`).
