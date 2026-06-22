# GitHub ATS company-list harvest — Workday / SmartRecruiters / Workable / Recruitee + misc

Lane: exhaust GitHub for published lists/datasets of company ATS boards (Workday, SmartRecruiters,
Workable, Recruitee primary; Teamtailor/BambooHR/Breezy/Personio/iCIMS/etc. secondary; multi-ATS
combined datasets top priority). Retrieved 2026-06-23. All data fetched via `gh` + raw.githubusercontent.com.

12 sources saved. 1 flagship multi-ATS dataset (kalil0321, ~63.5k companies across 26 ATS),
3 mid-size multi-ATS datasets (outscal 12k, andreasasprou, agentic-crawler, remotebear, Nandish02),
plus several focused Workday/SR slug lists and 2 careers-URL discovery seeds.

## Sources (subfolder — provider(s) — count — path)

1. **kalil0321-ats-companies** — FLAGSHIP MULTI-ATS (26 providers) — ~63,485 companies (name,slug,url)
   - https://github.com/kalil0321/ats-scrapers (ats-companies/*.csv)
   - In-lane: workday 2604, smartrecruiters 2214, workable 4269, recruitee 888; teamtailor 1010,
     bamboohr 5632, breezy 1384, personio 2463, icims 1363, oracle 442, successfactors 1271, taleo 150.
   - Out-of-lane (saved): join_com 23547, greenhouse 4966, ashby 2856, jazzhr 2689, lever 2113,
     rippling 1923, gem 496, pinpoint 350, recruiterbox 314, cornerstone 297, avature 87, phenom 85, eightfold 71.
   - Path: `kalil0321-ats-companies/` (26 CSVs + README.md)

2. **outscal-openjobs** — MULTI-ATS via ats_links — 12,144 companies, 2,445 with recognized ATS host
   - https://github.com/outscal/OpenJobs (data/companies_v2.json, 4.24MB)
   - In-lane: smartrecruiters 304, workable 251, workday 185, recruitee 71; bamboohr 218, teamtailor 45,
     breezy 59, personio 40, zoho 46, trakstar-hire 14, keka 13 (+ greenhouse 775, lever 336, ashby 234, jobvite 112).
   - Gaming-heavy but strong for TODO ATS gaps (Trakstar/Keka/Zoho/Darwinbox). Path: `outscal-openjobs/companies_v2.json`

3. **andreasasprou-ats-finder** — company->ATS detection (~100 ATS types) — 396 companies
   - https://github.com/andreasasprou/claude-code-ats-finder (companies_with_ats.csv)
   - domain,ats,confidence,url,explanation. In-lane: workday 35, smartrecruiters 24, workable 10, recruitee 1;
     icims 14, successfactors 9, breezy 3, teamtailor 2, bamboohr 2, taleo 1, trakstar 2, eightfold 1.
   - Exceptional ATS-type breadth (ADP, Paylocity, BrassRing, NEOGOV, Dayforce, Comeet...). Path: `andreasasprou-ats-finder/companies_with_ats.csv`

4. **ches-ctrl-cheddar** — direct ATS-host URL lists — 1,529 careers URLs + 175 job URLs
   - https://github.com/Ches-ctrl/Cheddar (storage/new/company_urls.csv, job_posting_urls.csv)
   - host dist: greenhouse 571, lever 332, bamboohr 107, workable 101, ashby 93, jazzhr 68,
     myworkdayjobs 68, breezy 65, recruitee 37, jobvite 23, smartrecruiters 16, teamtailor 7.
   - Nearly all rows already on an ATS host => directly parseable. Path: `ches-ctrl-cheddar/`

5. **nandish02-portfolio** — MULTI-ATS job config — workday 93 tenant pairs + smartrecruiters 49
   - https://github.com/Nandish02/portfolio (jobs/config/companies/, branch master)
   - workday.json: {label, tenant, wdN-domain, board} — solves Workday wd-server+board resolution.
   - Also saved: greenhouse 329, lever 149, ashby 189. Path: `nandish02-portfolio/`

6. **mkemaldurmus-agentic-crawler** — MULTI-ATS company->ATS map — 50 real boards (gh/lever/ashby)
   - https://github.com/mkemaldurmus/agentic-crawler (config/sources/*.yaml, consolidated)
   - Each YAML: ats_platform + ats_slug + endpoint. greenhouse 35, lever 9, ashby 6 (+19 aggregators excluded).
   - Some cos have dual-ATS rows (notion gh+ashby) = slug ground truth. Path: `mkemaldurmus-agentic-crawler/ats_company_map.tsv`

7. **remotebear-companies-data** — MULTI-ATS company->ATS map — 91 companies
   - https://github.com/remotebear-io/remotebear (packages/data/companies/companies-data.json)
   - scrapingStrategy field = ATS: greenhouse 56, lever 21, custom 7, smartrecruiters 2, recruitee 2,
     workable 1, personio 1, workday 1. Path: `remotebear-companies-data/companies-data.json`

8. **killerfrost598-workday-scraper** — WORKDAY — 255 tenant hostnames
   - https://github.com/killerfrost598/Workday-Scraper (workday_parallel/Iurls.txt)
   - All wd5; broad US-company coverage (incl. non-tech). Path: `killerfrost598-workday-scraper/Iurls.txt`

9. **kayden-vs-ats-finder** — India fintech company->ATS probe — 340 companies (strong Freshteam)
   - https://github.com/kayden-vs/ats-finder (ats_report.csv)
   - company_name,detected_ats,slug_used,careers_url,status. freshteam 225, greenhouse 45, ashby 22,
     smartrecruiters 15, lever 11, bamboohr 5, personio 4, teamtailor 4, recruitee 3, workable 2.
   - Best in-repo Freshteam source. Path: `kayden-vs-ats-finder/ats_report.csv`

10. **christopherlam888-workday-scraper** — WORKDAY — 12 companies w/ full faceted URLs
    - https://github.com/christopherlam888/workday-scraper (configs/*.txt)
    - "CompanyName,<full myworkdayjobs faceted URL>". Semiconductor + Canadian banks. Path: `christopherlam888-workday-scraper/configs/`

11. **the-cockroaches-remote-jobs** — careers-URL seed — 937 URLs (~13 direct ATS hosts)
    - https://github.com/the-cockroaches/remote-jobs (job-links.txt)
    - Mostly company root domains; low direct-ATS yield but useful discovery seed. Path: `the-cockroaches-remote-jobs/job-links.txt`

12. **cswala-awesome-career-pages** — India careers-page seed — 611 entries (~18 direct ATS hosts)
    - https://github.com/CSwala/awesome-career-pages (Portal.json)
    - Low direct yield but the ATS subset hits TODO gaps: darwinbox 3, peoplestrong 2, skillate 2, freshteam 2, zoho 1. Path: `cswala-awesome-career-pages/Portal.json`

## Approx aggregate per-ATS (this lane), summed across sources (raw, NOT deduped — heavy overlap likely)

| ATS | kalil0321 | outscal | Cheddar | andreasasprou | kayden | Nandish02 | killerfrost | chris | remotebear | agentic | cockroaches | cswala |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Workday | 2604 | 185 | 70 | 35 | 0 | 93 | 255 | 12 | 1 | 0 | 0 | 1 |
| SmartRecruiters | 2214 | 304 | 16 | 24 | 15 | 49 | 0 | 0 | 2 | 0 | 0 | 0 |
| Workable | 4269 | 251 | 101 | 10 | 2 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| Recruitee | 888 | 71 | 37 | 1 | 3 | 0 | 0 | 0 | 2 | 0 | 3 | 1 |
| Teamtailor | 1010 | 45 | 7 | 2 | 4 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| BambooHR | 5632 | 218 | 107 | 2 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Breezy | 1384 | 59 | 65 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |
| Personio | 2463 | 40 | 0 | 0 | 4 | 0 | 0 | 0 | 1 | 0 | 1 | 0 |
| iCIMS | 1363 | 1 | 0 | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |
| Oracle HCM | 442 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SuccessFactors | 1271 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Taleo | 150 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

**kalil0321 dominates every in-lane ATS** — it is the dataset to ingest first; the others add
geographic/vertical breadth (India fintech, gaming), TODO-gap providers (Freshteam, Trakstar, Keka,
Zoho, Darwinbox, PeopleStrong, Skillate), Workday wd-server+board resolution (Nandish02), and
cross-source slug verification.

## Dead-ends / excluded (no usable list)
- `andrew-shwetzer/career-ops-plugin` (references/ats-endpoints.md) — URL-pattern REFERENCE doc, no company list. Useful as ATS-detection rules only.
- `cboyd0319/JobSentinel` (jobSourceOfficialApiCorpus.ts) — ATS endpoint-pattern corpus with only "representative example" company NAMES, no slugs. Reference, not dataset.
- `riobits/ats-scraper-bun` — constants.ts = NL region names; benchmark.csv = NL company names w/o ATS slugs. No slug list.
- `peviitor-ro/generic_jobs` (generic.csv) — "generic/open-application" placeholder URLs, not company boards.
- `dtunkelang/bag-of-documents` — has fetch_{breezy,recruitee,smartrecruiters,workable}.py + slug-bag BUILDER scripts, but no committed raw slug CSV/list. LEAD only (see below).

## Best LEADS for follow-up
- **dtunkelang/bag-of-documents** — `download/build_slug_text_bag.py`, `build_top_slugs_for_labeling.py`,
  `build_unclassified_slugs_for_labeling.py`, and `fetch_{breezy,recruitee,smartrecruiters,workable}.py`.
  The slug bags appear generated at runtime (not committed). Running these scripts (or finding their
  output artifacts / an associated HF dataset) could yield large per-ATS slug lists. Reason unfetchable now: no committed list file.
- **peviitor-ro org** (github.com/peviitor-ro) — large Romanian job-search engine with ~40+ scraper repos
  (`JobsScrapers`, `based_scraper_*`, `Advanced_scrapers`, per-site scrapers under `sites/`). Each scraper
  hardcodes a company's ATS+slug; no single consolidated list, but mining the `sites/` dirs across repos
  would extract many EU (esp. Romania) company->ATS pairs. High effort, GitHub-fetchable.
- **kalil0321/ats-scrapers** publisher (`.github/scripts/publish_companies.py` + workflow) aggregates all
  26 CSVs to an R2 bucket with schema `ats,name,slug,url`. If that published combined file is reachable,
  it's the single-file version of source #1.
- **andrew-shwetzer/career-ops-plugin** + **cboyd0319/JobSentinel** — keep as ATS URL-pattern/detection
  references (not lists) for the fingerprinter; both enumerate endpoint shapes for many ATS incl. TODO ones.
- Non-GitHub (out of this lane, for the web-search lane): several repos point at ATS *fingerprinter* logic
  (`mkemaldurmus/agentic-crawler` discovery/fingerprints.py, `andrewcrenshaw/strata-harvest`
  ats_fingerprints.py) — pattern sources, not company lists.

## Integrity notes
- Every file above was actually fetched (curl from raw.githubusercontent.com or gh api) and saved in full.
- Counts are data-row counts (CSV headers excluded) or regex-recognized ATS-host counts as stated per source.
- No data invented or padded. Overlap between sources is expected and NOT deduplicated here (raw per-source).
- outscal Workday shown as 185 = 183 (myworkdayjobs.com) + 2 (other "workday" host strings).
