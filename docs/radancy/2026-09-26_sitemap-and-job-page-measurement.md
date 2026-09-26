# Radancy TalentBrew career fronts: sitemap, job-page and rate-limit measurement (2026-09-26)

Everything the Radancy scraper (`src/headstart/scrapers/radancy.py`, ADR-0246) rests on, measured
live on 2026-09-26. The probe scripts and raw captures were kept locally under
`experiment/radancy-talentbrew/` (not committed); every number a reader needs is below.

**Radancy is not an ATS.** It is a recruitment-marketing vendor; TalentBrew is its career-site
CMS. A TalentBrew site is a **Career front** (CONTEXT.md): a branded
job site that mirrors one or more **Backing Boards** on the company's real ATS and hands its Apply
button off to them. It stores no applications. `jobs.intuit.com` is Intuit's only public listing:
its Apply buttons go to `intuit.avature.net/externalCareers/JobApplication?pipelineId=…`, an
Avature tenant that lists nothing itself.

Samples:

- **17 fronts** found first (Intuit, Arm, NetApp, Sanofi, UnitedHealth Group, Cargill, Amgen,
  Alexion, Chevron, Moody's, Synopsys, Citi, Mattel, Palo Alto Networks, STEMCELL, Barclays,
  Takeda): 60 job pages each, 990 read.
- **1,078 candidate hosts** (urlscan, a CNAME sieve over every ledger's hosts, the old harvest
  pool): each host's CNAME chain, `/sitemap.xml` and, where needed, `/search-jobs`.
- **The 188 live fronts** of the first ledger: `/sitemap.xml` against the front's own stated total,
  and 40 random job pages from each (7,141 pages from 185 fronts; three fronts' samples were empty).

## Identity

**A front is its host.** Every front measured CNAMEs its host to `{host-with-dashes}.talentbrew.com`
(`jobs.intuit.com` → `jobs-intuit-com.talentbrew.com` → Akamai); the dashed host itself answers
400. So the Board is the host, lowercased, like Phenom's and iCIMS's, and the ledger's `tenant` and
`url` both carry it.

**A front can carry several TalentBrew company ids.** Job URLs end `/{companyId}/{jobId}`. 23 of
the 227 hosts whose sitemaps listed jobs carried more than one company id (`careers.munichre.com`
7, `careers.adeccogroup.com` 4, `careers.tuigroup.com` 4). The applicant sees one site, and job ids
are TalentBrew-wide integers of 11–12 digits (on five multi-company fronts, 5,202 job URLs held
5,202 distinct ids), so the Board is the host and the native id is the job id alone.

**Vanity and country hosts redirect to a canonical front.** `www.takedajobs.com` → `jobs.takeda.com`;
`jobs.citi.tw`, `emplois.citi.ca`, `www.praca.citi.pl` and five more → `jobs.citi.com`;
`teletechjobs.com` → `www.ttecjobs.com`; 36 of the 224 hosts listing jobs were such aliases. Their
`/sitemap.xml` redirects to the canonical front's, whose job URLs carry the canonical host. So a
Board's jobs are the job URLs **on its own host**; an alias reads none of its own.

**Filtered-view vanity hosts are not Boards either.** `disneytech.com`, `espncareers.com`,
`emplois.vinci-construction.com` and 11 more redirect `/search-jobs` to another front's results
with a filter (`www.disneycareers.com/en/search-jobs?acm=26715,…`), stating that filter's count.

**Radancy's QA estate mirrors real fronts.** `*.runmytests.com` / `*.runmytests.eu`
(`barclays-pb.runmytests.com`: 802 postings, Barclays' own count; `usaa-v1.runmytests.com`) — 15
hosts, all dropped from the pool.

## Listing

| Surface | What it serves | Verdict |
| --- | --- | --- |
| `/sitemap.xml` (redirects to the default language, e.g. `/en/sitemap.xml`) | Every job URL, with facet and content pages beside them; no title, no department | **Chosen** |
| `/search-jobs/results?…` (JSON wrapping HTML) | The paginated result list | Rejected: `robots.txt` disallows `/search-jobs/` on 151 of 188 fronts |
| `/search-jobs` (the page itself) | First page of results, and `data-total-job-results="N"` | Read once per Board, for its count only |
| `/category/{slug}/{companyId}/{categoryId}/{page}` | A category's jobs, paged | Not needed: the sitemap already names every job it can |

`robots.txt` on 16 of the 17 first fronts: `User-agent: *` / `Disallow:/search-jobs/` on 14,
nothing disallowed on 2 (`jobs.stemcell.com`, `search.jobs.barclays`). None names a `Sitemap:`
line; `/sitemap.xml` exists on every front measured.

**The sitemap is complete up to a cap.** On the 17 first fronts it matched `data-total-job-results`
on 16 and read 527 of 529 on the 17th. Across all 188 live fronts it listed 175,485 postings against
205,214 stated: 160 fronts matched exactly, 169 to within five, and 14 were short by more — **12 of
them at exactly 500** (`jobs.walgreens.com`: 500 of 22,544; `careers.walmart.ca`: 500 of 5,439)
and **one at 10,000** (`jobs.greatclips.com`: 10,000 of 11,989). No paging reaches past the cap:
`?page=2`, `?p=2`, `?pageNumber=2` and `sitemap-2.xml` return the same 500; `sitemap2.xml`,
`sitemap_index.xml` and `jobs-sitemap.xml` redirect away. 7 of the 13 capped fronts (all at 500) do not
disallow `/search-jobs/`; reading their results endpoint is the follow-up that would recover
most of the ~29,700 postings the fronts state beyond their sitemaps.

**Every language's sitemap lists the same ids.** `jobs.veolia.com`: `/fr/sitemap.xml` 2,919 ids,
`/en/sitemap.xml` 2,919, the same ones; `/search-jobs` states 2,919 in both.

**A job URL is known by its shape.** `[/{lang}]/{word}/{place}/{title}/{companyId}/{jobId}`, where
`{word}` is localised: `/job/`, `/emploi/` (`carrieres.walmart.ca`), `/banen/`
(`www.werkenbijdji.nl`), `/trabajo/` (`www.syscocareers.cr`), CJK (`jobs.jabil.cn`). An
English-only `/job/` pattern read 0 jobs on 17 live fronts. The facet pages beside the jobs
(`/employment/{place}-jobs/27595/68354/{geo}/4`, `/business/custom_fields.reqid/334718/23251/5`,
`/category/…/1`) end in a one-digit page number, and every job id measured is 11–12 digits, so the
pattern requires a 6+-digit last segment after a non-numeric title segment.

## Dead versus empty

| Case | `/sitemap.xml` | `/search-jobs` | Verdict |
| --- | --- | --- | --- |
| Live front | job URLs on its host | — | LIVE n |
| Empty front (`empregos.allianceautomotive.eu`, `www.attjobs.com.mx`, `www.careersataspa.com`) | `<urlset>` of content pages only | 200, own host, `data-total-job-results="0"` | LIVE 0 |
| Not a front (285 other hosts with a `/sitemap.xml`) | no job URL | no attribute (271), or lands on another front (14) | DEAD |
| Departed front (`www.tmp.com`, `www.aia.co.uk`) | redirect to `www.radancy.com` | same | DEAD |
| Decommissioned front (`careers-us.primark.com`, `jobs.raymondjames.com` and 9 more) | CNAME to `redirect.talentbrew.com`, which has no A record | — | DEAD (DNS) |
| Alias host (`www.takedajobs.com`) | the canonical front's job URLs | lands on the canonical front | DEAD |

A DNS failure is DEAD: fronts sit on customers' own hosts, not a wildcard zone. Everything else
(5xx, 403, timeouts) is UNKNOWN.

## Detail

**Every field is on the job page.** Of 7,141 sampled pages: 7,138 answered 200 and 3 404 (postings
closed between the sitemap read and the page read). 6,816 carried a JSON-LD `JobPosting`; of the other
322, 320 were every page of the eight fronts below, whose templates write none, and 2 were on
`jobs.mayoclinic.org`:

| Field | Source | Share |
| --- | --- | --- |
| title | JSON-LD `title` | 6,816 / 6,816 |
| description (>200 chars) | JSON-LD `description` | 6,813 |
| location | JSON-LD `jobLocation`, every place "; "-joined (676 name more than one) | 6,720 |
| department | `<meta name="gtm_tbcn_jobcategory">` | 6,612 |
| posted_at | JSON-LD `datePosted` | 6,816 |
| employment_type | JSON-LD `employmentType` | 4,765 |
| salary | JSON-LD `baseSalary` with an amount | 130 |
| remote | JSON-LD `jobLocationType: TELECOMMUTE` | 36 (else `is_remote(location)`) |
| Backing Board | the Apply button's `apply-url` | 6,775 |

**Eight fronts' templates write no JSON-LD** (every sampled page of `jobs.jabil.com`,
`jobs.cancer.org`, `jobs.greatclips.com`, `jobs.advancedtech.com`, `jobs.scjohnson.com`,
`www.perduecareers.com` and the two internal fronts). TalentBrew writes its own meta tags on every
job page — `gtm_tbcn_jobtitle`, `gtm_tbcn_location` (`City~Region~Country`, places on `|`),
`gtm_tbcn_jobcategory`, `gtm_firstindex`, `search-job-apply-url` — and the body sits in
`<div class="ats-description">`. Read from those on 195 pages of 40 fronts that also carry
JSON-LD: title, category and apply URL agreed on 195; the body was present on 190 and within 10% of
JSON-LD's length on 157; `gtm_firstindex` (month first, 151 of 195) equalled `datePosted` on 151
and otherwise differed by re-indexing. Places come as full names ("Québec" where JSON-LD says "QC").

**The tech gate cannot run before the page.** The sitemap states only the URL; its title slug is
the one listing signal. On the 990 first pages, `is_tech(slug)` matched `is_tech(title, category)`
except for 51 of the 337 tech postings (15.1%) — each a vague title promoted by a technical category
("Senior Architect" under Technology, "Data Governance Lead" under Information Technology). A
title-only `is_tech` over the real title lost exactly the same 51, so the loss is the missing
department, not the slug's spelling. No gate: every page is fetched.

**Company name.** No one page names the company in one template (17 home-page titles, 17
phrasings). Job pages do: the page title's "{Title} at {Company}" agreed on every page of 14 of the
first 17 fronts, and `hiringOrganization` names a division or legal entity on some (Citi's "Early
Career", Palo Alto Networks' subsidiaries, Mattel's "MattelInc"). Title first at 90% agreement,
`hiringOrganization` second: 164 of 177 fronts named; the other 13 (multi-brand fronts such as
`careers.munichre.com`, and `jobs.citi.com`) keep their host.

## Fields

- **Dates.** `datePosted` is date-only and written unpadded as often as not (`2026-9-3`: 224 of
  990). Median age 23 days over 6,816 pages, 185 older than a year, 17 a few days in the future
  (a posting scheduled ahead). Being date-only, the two-fetch fabrication test has nothing to move;
  the spread of ages shows it is not the fetch date.
- **Salary.** 130 of 6,816 pages state an amount, all USD, on 4 fronts (`jobs.biolifeplasma.com`,
  `www.commonspirit.careers`, `www.schwabjobs.com`, `jobs.takeda.com`); many more state a currency
  and no amount. `unitText` is `""` on every one, and the amounts fall in two clusters: hourly up
  to 117.4, annual from 51,300. Period from magnitude, split at 1,000; all 130 read back through
  `salary.from_field`.
- **Employment type.** 219 distinct values. "Full time"/"Full Time"/"Full-time"/"FULL_TIME" and
  "Permanent" reach the full-time filter; "Regular" (511), "CDI" (69), "Hourly" (59), "Seasonal",
  "PRN" and "Per Diem" reach none. Kept as stated.
- **Language.** `langdetect` over titles alone called 1,788 of 6,816 (26%) non-English — titles
  are short, so this is a rough bound; the index's own gate reads the description.

## Operating limits

- **Rate limit.** One front (`careers.unitedhealthgroup.com`), job pages at concurrency 1/4/16/32/64
  (64 requests each): 1.4 → 36.7 req/s, 320 of 320 200s. Then 500 requests at 32 and at 128: 58.0 and
  91.5 req/s, 1,000 of 1,000 200s. Twelve fronts at once, 600 requests at 64 and at 128: 68.5 and
  86.8 req/s, 1,200 of 1,200 200s. No refusal at any width; p95 latency stayed under 2 s. The
  scraper runs 16 detail workers.
- **User-Agent.** `headstart/0.1`, no `-A`, and `python-requests/2.32` all 200 on `jobs.citi.com`
  and `careers.arm.com`.
- **Size.** Job page: mean 170 KB, median 85 KB over 7,141 (`jobs.intuit.com` ~580 KB,
  `jobs.stemcell.com` ~45 KB). Sitemap: 347 KB for Intuit's 543 jobs, 5.4 MB for UHG's 5,623.
- **Encoding.** The sitemap is `text/xml` with no charset and a UTF-8 BOM; job pages declare UTF-8.

## Population

- **Tech share.** `is_tech(title, department)` kept 961 of 6,816 sampled postings (14.1%).
- **Front duplication.** Of the 6,816 sampled postings, 4,289 (62.9%) apply on a Board a
  Scrapable Board already serves, resolved through each ATS's own `slug_from` (the scraper's
  `backing_board`, run over the ledgers of main at 1a97fd82). Weighted by each front's ledger
  count, 58.3%. By front: 94 fully duplicated, 57 not at all, 26 partly. By Backing ATS: Workday
  2,534, iCIMS 553, Oracle 432, Avature 262, SmartRecruiters 212, SuccessFactors 120, Taleo
  Enterprise 60, Greenhouse 43, Lever 40, Eightfold 33. This is an undercount: SuccessFactors' own
  apply form (`career2.successfactors.eu/…?company=cargill`) names a company id rather than the
  RMK host its Board is keyed by (107 sampled postings).
- **Cross-ATS collisions.** `scripts/validate/cross_ats_duplicates.py radancy` finds 14 domains
  held by another ledger too. Four are the **same host**: `careers.alexion.com`,
  `careers.chevron.com`, `careers.moodys.com` and `jobs.stemcell.com` sit in the SuccessFactors
  ledger as live, the Radancy fronts CLAUDE.md's SuccessFactors landing rule warns about.

## Discovery

A front sits on its customer's own host, so there is no vendor namespace for the Wayback or Common
Crawl feeders to sweep (`*.talentbrew.com` is reachable only as a CNAME target: the dashed host
answers 400, and Common Crawl's index API returned 502 for `*.talentbrew.com` on the day), and no
upstream seed list exists. The pool came instead from:

- **urlscan.io**, `domain:tbcdn.talentbrew.com`, month by month: 1,073 page hosts, 212 of the 220
  pool rows, 187 found by nothing else (`scripts/discover/mine_radancy.py`);
- **a CNAME sieve** (`scripts/discover/sf_cname_probe.py`, whose output keeps every chain) over
  the 10,071 hosts the committed ledgers and earlier harvests hold: 14 fronts;
- the same sieve over `careers./jobs./search.jobs.{label}.{com,net,org,co.uk}` for the Avature
  pool's 1,009 labels, since many Avature tenants list nothing and front their postings elsewhere:
  18 fronts, 3 new;
- the six-row harvest pool (`radancy.csv`/`talentbrew.csv`), of which `careers.upstart.com` and
  `careers.zoom.us` turned out to be Greenhouse sites and probe DEAD.

A sieve over `careers./jobs./career.` hosts of ~250,000 top domains was still running at landing,
with 118 `talentbrew.com` CNAMEs found in its first ~704,000 of 731,717 hosts; those land in a
follow-up.

## Cost

Per step 6 of the build: bytes fetched per tech Job. A posting costs one ~170 KB page (the
sitemap and the one `/search-jobs` read are per Board and negligible beside it), and 14.1% of
postings are tech: **~1.2 MB per tech Job**, under ADR-0158's ~2 MB bar. Over the ledger's
postings that is roughly 30 GB of pages per full scrape of every front, for ~25,000 tech Jobs.
