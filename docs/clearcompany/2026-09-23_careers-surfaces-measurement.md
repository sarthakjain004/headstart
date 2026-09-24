# ClearCompany careers surfaces: measurement

**Date:** 2026-09-23. Every figure below was measured this day against live hosts. The probe
scripts and raw captures are kept locally, not committed; each number a reader needs is inlined
here.

## The finding

ClearCompany's public Board isn't on `clearcompany.com`. It lives on **HRM Direct**, the career-site
product ClearCompany owns, at `https://{slug}.hrmdirect.com/employment/`. A ClearCompany job link
(`{slug}.clearcompany.com/careers/jobs/{guid}`) resolves in the browser through
`/api/v1/careers/jobs/{guid}/posting-url` to `{slug}.hrmdirect.com/employment/job-opening.php?req=N`
(captured in headless Chrome on `kingarthurbaking`). Both third-party sources pointed at the wrong
surface:

- `Francis1998/agentic-career-search` describes per-tenant HTML boards on
  `{slug}.clearcompany.com`. That host serves a 1,989-byte SPA shell, byte-identical for a real
  tenant and an invented one. Rendered in Chrome, it is the ClearCompany login page.
- `ever-jobs/ever-jobs` describes a JSON feed, `GET careers-page.clearcompany.com/api/v1/careers/jobs`
  with `API-ShortName: {slug}`. The endpoint is real, but it isn't the Board (see "Listing" below).

**The sample** is 244 tenant labels from one Common Crawl index (`CC-MAIN-2026-39`, hosts
`*.clearcompany.com` and `*.hrmdirect.com`), minus the vendor's own `www`/`blog`/`offers`/`info`.
It covers 176 hiring Boards and 6,377 postings.

## Identity

1. **Slug = the subdomain label**, one label for two hosts. ClearCompany's own webapp builds
   `API-ShortName` from `window.location.host.split(".")[0]`. The same label names the HRM Direct
   Board: 158 of 162 labels seen only on `hrmdirect.com` answered the ClearCompany feed, and 53 of
   60 seen only on `clearcompany.com` had a hiring HRM Direct Board. DNS is case-insensitive
   (`KingArthurBaking.hrmdirect.com` serves the same 35,855 bytes), so the slug is lowercased.
   There is no regional pod, and the TLS certificate is a wildcard (`crt.sh`: 7 names, all
   vendor), so it is no roster. A native id is a numeric `req`, with no `:`.
   **One tenant can run two labels.** `cmalliance` and `cmallianceuswork` serve the identical 157
   reqs. That is a duplicate Board, to be caught at the ledger (step 5).
2. **Spellings from discovery.** A host from either domain maps to its label.

## Listing

3. **Surfaces**, cheapest-complete first:

   | surface | verdict | evidence |
   | --- | --- | --- |
   | `GET {slug}.hrmdirect.com/employment/xml.php` | **chosen** | Syndication XML listing every posting with `title`, `department`, `office`, `city`/`state`/`country`, `date`, `referencenumber` (the req), `company`, and a truncated `descriptionrich`. Its distinct-req count equals the rendered Board's on 155 of 161 Boards. On the other 6, the Board uses a template the counting regex could not read (it counted 0); xml.php had their rows. |
   | ClearCompany JSON feed (`careers-page.clearcompany.com/api/v1/careers/jobs`) | rejected | **Short**: below the public Board on 97 of 161 Boards (3,597 vs 4,138 postings, 86.9%). King Arthur Baking: feed 11, Board 16, and the 5 missing are ordinary current postings. **Zombies**: 14 of 244 tenants have no public Board (HRM Direct 404) yet the feed lists postings (Linden Lab's newest is 2011-07-25, Defy Media's 2018-10-30, the most recent any of them has is 2025-12-03). **Silent empty**: 11 hiring Boards return `[]`. `robots.txt` on `careers-page.clearcompany.com` and every `{slug}.clearcompany.com` checked (6 of 6) is `Disallow: /`. |
   | Board HTML `job-openings.php?search=true` | rejected | Templates vary per tenant (one grouped-by-department layout defeated the parser). It paginates: `oakmontmanagement` shows 266 reqs where xml.php has 695. Division tenants 302 to a `cust_sort1` view (31 of 244). |
   | `/employment/rss.php` | rejected | 0 items on 140 of 140 hiring Boards. |
   | `robots.txt`, `sitemap.xml`, `llms.txt` on hrmdirect | none | 404 on 6 of 6 tenants checked. HRM Direct states no crawler policy. |

4. **Pagination.** There is none. xml.php returns the whole tenant in one response, with no page
   parameter and no total to check it against. The largest Board that answered,
   `oakmontmanagement`, came back whole: 1,312 rows / 695 reqs, 3.68 MB, 20.7 s. **The hard limit
   is the server's own timeout.** On `heartlandbehavior` (3,036 reqs on its rendered Board, the
   largest seen) xml.php answers **500 after ~104 s** (2 of 2 attempts). One of its divisions
   alone streamed 21.9 MB / 9,623 rows in 150 s without finishing. That Board is unreadable
   through this surface. It is 1 of 244.
   **Narrowing works but is not used.** xml.php honours the Board search form's own `state=`,
   `city=` and `dept=` parameters: on `kingarthurbaking`, `state=VT` returned only its 11 Norwich
   and White River Junction rows, `state=VA` its 3 Alexandria rows, and `state=NC` none. On
   `heartlandbehavior`, `state=KS` returned 1,149 rows / 437 reqs in 16 s and `state=NE` 3,821
   rows / 1,284 reqs in 51 s. Reading all 16 states its board offers (3-12 s each, none failed)
   gives **4,558 distinct reqs, and `is_tech(title, department)` keeps 0 of them**, so the state
   split is not built (ADR-0182). Its sibling labels (`kansasbehavior`, `ncbehavior`, `georgiabehavior`, `bkbehavior`,
   `aba360`, `cbsupports`) time out the same way and sit UNKNOWN in the ledger.
5. **A filter, not an address.** xml.php accepts `cust_sort1={division}`. An unknown value answers
   **200 with 0 jobs**. The scraper never sends it: the bare URL is the whole tenant (division
   tenants' xml.php ≥ their default division's Board view on 31 of 31).
6. **The listing's description is truncated.** `descriptionrich` is capped at **exactly 1,000
   chars**: 6,601 of 8,335 rows sit at 1,000 and none is longer. `description` (plain) is empty on
   49.4% of rows. The full body is only on the detail page.
7. **Hidden rows.** None found. xml.php and the rendered Board agree on 155 of 155 readable
   Boards.

## Dead versus empty

8. **xml.php's status decides it.** An unknown slug gets 404 with an 86-byte Apache page
   (`zzqqnotreal123`, and all 3 feed-400 slugs). A live Board with no openings gets 200 with zero
   `<job>` (16 Boards). Every tenant whose xml.php is 404 has a 404 Board page too (51 of 51).
   ClearCompany's feed cannot make this call. It answers 200 `[]` for 34 of those 51 dead tenants
   and 200 with postings for 14 more.
9. **A departed tenant** gets a 404 on its HRM Direct host (51 of 51 above). There is no redirect
   to the vendor site, so the prober needs no `allow_redirects=False`.

## Detail

10. **`GET {slug}.hrmdirect.com/employment/job-opening.php?req={req}`** is server-rendered HTML.
    Across 510 postings on 176 Boards: 510 of 510 answered 200, and 509 of 510 carried
    `<div class="jobDesc">`, with a p50 of 5,269 chars of HTML (max 18,336). There is no JSON-LD.
    A `viewFields` table holds tenant-configured labels: `Location:` 485, `Department:` 447,
    `Office:` 30, `Job ID:` 21, **`Salary:` 15**, and the rest ≤ 9 each (`Workplace Type:` 3,
    `Employee Type:` 3, `Job Status:` 3, …).
11. **What the detail needs.** Nothing: no header, token or cookie. **Encoding trap**: 443 of 510
    detail pages are not valid UTF-8, and 0 of 120 of those carry any UTF-8 sequence — they are
    cp1252 (`0x92` apostrophes). The feeds are worse: **one xml.php mixes three encodings**
    under its `encoding="UTF-8"` declaration — cp1252 bytes, UTF-8, and cp1252 double-encoded
    through Latin-1 (`C2 96` for an en dash; `achievementcenters` carries all three). Over 174
    hiring feeds (16,676 titles and departments): a whole-document UTF-8-else-cp1252 decode left
    174 fields as mojibake on 46 Boards ("CafÃ©", "Specialist Â–"); reading each
    byte UTF-8 rejects as cp1252 left 0 mojibake but 116 C1 controls; mapping those C1 controls
    through cp1252 as well left 0 broken fields. A closed or unknown `req` on a live tenant
    answers **200 with no `<h2>` and no posting**, a soft 404.
12. **Tech gate: exact.** `title` and `department` come from xml.php, and the scraper never takes
    either from the detail, so `tech_detail_wanted` runs before the fan-out with no recall loss.

## Fields

13. **Dates are real.** xml.php `<date>` is identical across two passes minutes apart (7,523 of
    7,523 rows present in both). Every one of the 8,331 rows read reads `04:00:00` or `05:00:00` with a literal `+0100`
    offset: local midnight US Eastern, mislabelled. The calendar date matches ClearCompany's
    `OpenDate` (King Arthur, 11 of 11). The scraper keeps the date, not the clock time.
14. **Remote.** There is no native field. The `Workplace Type:` label appears on 1 tenant of 176,
    and 182 of 6,377 reqs say "remote" in city, office or title. The scraper reads `is_remote()`
    over the location and the office name (Fisher Phillips files its remote reqs under an office
    named "Remote"); the title is not read.
15. **Salary.** No listing field. The detail's `Salary:` label is on 15 of 510 (2.9%), mostly
    templated `$68970 - $108199 Per Year` / `$27.09 - $41.02 Per Hour`, with some free text
    (`Salary Range: $30.06 - $31.33 hourly`) and a `$0 - $0` placeholder. Everything else
    lives in description prose, where the Tier-2 extractor reads it.
16. **Experience and employment type.** Neither has a native field. Employment-type labels are
    tenant-custom and rare (`Job Status:` 3, `Employee Type:` 3, `Status:` 3 of 510).
17. **Location.** xml.php emits one row per req *per location*. 434 of 6,377 reqs (6.8%) span
    2–30 rows, and only `city`/`state`/`country` differ between them (title, department and date
    never do). The scraper joins every location with "; ". `city` is present on 90.8% of rows,
    `state` 95.7%, `country` 100%.
17a. **Department:** `<department>` on 100% of xml rows.
18. **Company name:** `<company>` is on 100% of rows and constant within a Board (176 of 176), and
    it names the employer (`King Arthur Baking Company`, `Oakmont Management Group`). The detail
    `<title>` says `… - Careers At {company}`.

## Operating limits

19. **Rate limit: none found.** One tenant's detail pages: conc 1 → 3.6 req/s, 4 → 13.9, 16 →
    40.4, 32 → 90.5, 64 → 68.6, 128 → 71.5, all 200 (676 requests). The knee is 32. Across
    tenants, xml.php at conc 32/64 was 128 of 128 200 each time. The first cross-tenant pass lost
    5 and 121 of 128 to `ConnectionError`, right after the 128-wide burst. It did not come back:
    384 more cross-tenant requests at 16/16/32, and a 400-request 128-wide burst followed by 30
    polls over 108 s, were all 200. The cause is not known. The scraper ships at 16 detail
    workers, half the knee.
20. **User-Agent:** `headstart/0.1`, curl's default and `python-requests/2.32` all get 200 with
    identical bytes, on both surfaces.
21. **Response size.** xml.php: p50 47 KB per hiring Board, 2,533 bytes per row, max 3.68 MB.
    Detail page: p50 45 KB, max 73 KB, p50 latency 0.9 s.

## Population

22. **Tech share and volume.** Over 176 hiring Boards, 6,377 postings: p50 17, mean 36.2 per
    Board. `is_tech(title, department)` keeps **556 (8.7%)**, clustered on a few federal-IT
    contractors (`adhoc`, `dci`) among bakeries, clinics and municipalities.
23. **Language.** `country` is UNITED STATES on 6,145 of 6,377 (96.4%), then CANADA 80 and UNITED
    KINGDOM 23. The corpus is effectively English.
24. **Overlap.** HRM Direct is ClearCompany's own career site, not a skin over another ATS: apply
    links go to `apply.hrmdirect.com`. The slug is a vendor-subdomain label, so
    `cross_ats_duplicates.py` has no host to join on. **The duplicate risk is inside the ATS,
    and it is large.** xml.php returns the whole account from every label the account owns, and
    req ids are platform-wide integers, so two labels sharing any req are one account. Across
    all 1,304 hiring Boards of the ledger, 1,881 label pairs share reqs, every one of them
    **identically** (0 partial overlaps). They form 131 accounts over 453 labels — the largest
    36 labels (a behavioral-health group), then 33 (Wellhaven's veterinary clinics), and
    Flexjet under `flexjet`/`fxair`/`fxaireu`/`corporatewings`/`sentient`. Left in, 322 labels
    would re-serve 30,209 postings: the ledger's 61,043 postings are **30,834 distinct** (the
    ledger's own counts; one account's labels were read a posting apart, 186 vs 187). Each
    label's `<company>` is its own brand (`chicagosteel` says Chicago Steel, `wirtzcorp` says
    Chicago Blackhawks, over the same 9 reqs), so the label kept names every posting of the
    account. It is elected per account: a label whose board does not redirect to a division
    (the account-level site; 63 of 131 accounts have one), else the lowest default-division id
    (the account's first division), alphabetical on a tie.

## Discovery

- **Common Crawl, one index** (`CC-MAIN-2026-39`, `*.clearcompany.com` + `*.hrmdirect.com`):
  245 labels — the 244-label measurement sample above, plus `offers` (a vendor host).
- **Wayback CDX** (`wayback_pages.py clearcompany`, 66 + clearcompany.com pages): 4,166 labels,
  3,936 of them new to the pool.
- **Common Crawl, 33 indexes** (`CC-MAIN-2026-39` back to `CC-MAIN-2023-40`, every crawl of the
  last three years). `index.commoncrawl.org` answered every request with an empty reply (curl 52)
  on 2026-09-23, so the sweep read the same index files off `data.commoncrawl.org`
  (`scripts/discover/cc_data_host.py`). It found 1,265 labels. Five are vendor hosts
  (`careers-content`, `careers-performance`, `cc-client-cdn`, `learning-marketplace`, `offers`):
  each answers 404 on the board and 400 on the feed, the same as an invented label. That leaves
  1,260 labels. **79 are new to the pool**; the other 1,181 were already there from Wayback or the
  one-index pass. Yield: 245 labels from the newest crawl, then 7 to 91 new per crawl (median about 25),
  with no crawl adding zero. The 12 crawls from 2024 and 2023 still added 243.
- No vendor roster exists: the TLS certificate is a wildcard (`crt.sh`: 7 names, all vendor)
  and DNS answers almost any label (the one NXDOMAIN in the pool is the vendor's `preview`). No
  upstream seed list exists.

**The 79 Common Crawl labels (2026-09-24 fold):** 35 live (31 hiring, 1,076 postings), 43 dead,
1 unknown (`apply`, which answers 200 with something other than a feed). After the alias re-run,
10 of the 31 hiring labels are buried under an account the pool already held (496 postings).
Two more, `hometownservices` and `martinventures`, are now the kept label of an account we already
held; they replace `airassurance` and `reimaginecare`, which are buried in their place. The other
19 are new accounts, with 495 postings. Accounts sharing reqs: 134 across 468 labels, 334 buried,
up from 322. Scrapable hiring Boards: 1,001, up from 982, with 31,329 distinct postings, up from
30,834.

At first landing, before that fold, the figures were as follows. Pool: 4,181 labels. Ledger: 1,679 live (1,304 hiring, 61,043 postings), 2,474 dead, 28 unknown
— labels answering 200 with something other than a feed (vendor infrastructure such as
`cssstatic1`, `resume`, `talent`) and labels that time out, among them the behavioral-health
account above. A spot check of 5 live and 5 dead rows against
the live host agreed with every verdict. Connection errors never reach DEAD: the probe's only
DEAD answer is a 404 — 2,473 of the 2,474 dead rows re-read 404 afterwards, the other being
`preview`, which does not resolve. A DNS failure reads UNKNOWN: with a wildcard record it is the
local resolver failing, which is how breezy's prober once wrote 41 live Boards dead.

## Storage cost, for step 6

From the committed ledger, after the alias ledger buries the 322 duplicate labels: **982
scrapable hiring Boards, 30,834 postings**. At the sample's 3,309 feed bytes per posting (2,533
bytes a row × 1.306 rows a req) the feeds cost ~102.0 MB a run. At the sample's 8.7% tech share
that is ~2,688 tech postings, whose 45 KB detail pages add ~121.3 MB — **~223 MB a run, ~83 KB
per tech Job**. Ungated it would be ~556 KB. Both are far under ADR-0158's ~2 MB per tech Job
bar (jazzhr: ~10.7 GB for ~5,098), so ClearCompany ships enabled.
