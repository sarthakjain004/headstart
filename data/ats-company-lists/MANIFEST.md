# MANIFEST — harvested ATS company lists

Harvested 2026-06-23 by a fan-out of research subagents across GitHub, package registries,
technographic sites, public datasets, and community lists. Every source has a `SOURCE.md` with
its exact URL and provenance; nothing here was invented (one repo shipping a synthetic-slug
generator was found and deliberately excluded).

- **93 sources** across 8 lanes · **92 `SOURCE.md`** · ~38 MB raw.
- Consolidated into **`by-provider/<ats>.csv`**: **132,338 unique `(ats, slug)` pairs across 35 ATS providers**.
- Regenerate the consolidation anytime with `python scripts/merge/consolidate_harvested_lists.py`
  (idempotent; re-run as more sources are added).

## Per-provider coverage (`by-provider/`)

| ATS | unique slugs | | ATS | unique slugs |
| --- | ---: | --- | --- | ---: |
| join | 24,197 | | smartrecruiters | 2,517 |
| bamboohr | 15,601 | | softgarden | 2,366 |
| workday | 14,652 | | rippling | 2,320 |
| icims | 10,687 | | zohorecruit | 1,778 |
| greenhouse | 10,529 | | teamtailor | 1,756 |
| paylocity | 10,350 | | freshteam | 1,220 |
| lever | 5,016 | | trakstar | 839 |
| breezy | 4,794 | | successfactors | 604 |
| jazzhr | 4,647 | | pinpoint | 533 |
| ashby | 4,461 | | jobvite | 516 |
| workable | 4,424 | | gem | 494 |
| personio | 3,288 | | oracle | 438 |
| recruitee | 3,033 | | comeet / cornerstone / taleo | 254 / 252 / 244 |
| | | | manatal / avature / eightfold | 193 / 131 / 93 |
| | | | oorwin / keka / darwinbox / ripplehire | 83 / 19 / 8 / 1 |

Covers HeadStart's full supported set **and** its entire `CLAUDE.md` TODO list with real bulk
data (trakstar 839 — was 4; oracle-cloud-hcm in `oracle`; zoho 1,778), plus ~20 ATS providers
HeadStart doesn't yet support (join, softgarden, manatal, oorwin, avature, cornerstone, gem,
paylocity, pinpoint, rippling, …).

## Flagship sources

- **Masterjx9/OpenPostings** — a shipped 40 MB SQLite whose `companies` table is a **61,610-row** company→ATS→URL registry across ~80 providers; sliced here into 24 per-provider slug CSVs. The single richest niche source. `sources/github-niche-ats-wave2/Masterjx9-OpenPostings/`
- **kalil0321/ats-scrapers** — clean `name,slug,url` CSVs for **26 ATS** (~63k companies). HeadStart's own origin project. `sources/github-workday-sr-workable-recruitee-misc/kalil0321-ats-companies/`
- **Feashliaa/job-board-aggregator** — ~60k entries over 7 ATS, incl. **12.8k Workday `slug|wdN|site` tuples** and large iCIMS / BambooHR / Paylocity lists. `sources/registries-and-gists/feashliaa-job-board-aggregator/`
- **slowloris-98/HireShire** — 15,533 Greenhouse/Lever/Ashby slugs. `sources/github-leads-wave2/HireShire/`

## Complete source index (93 sources)

**github-greenhouse-lever-ashby** — kalpthakkar/JobSniper, outscal/OpenJobs, axm0/jobwatcher, Nandish02/portfolio, vishal-yadav111/Job-Finder, haxsysgit/Haxjobs, ambicuity/New-Grad-Jobs (output only), remoteintech/remote-jobs, mshen1019/Argus.

**github-workday-sr-workable-recruitee-misc** — kalil0321/ats-scrapers ★, outscal/OpenJobs, andreasasprou/claude-code-ats-finder, Ches-ctrl/Cheddar, Nandish02/portfolio, mkemaldurmus/agentic-crawler, remotebear, killerfrost598/workday-scraper, kayden-vs/ats-finder, christopherlam888/workday-scraper, the-cockroaches/remote-jobs, cswala/awesome-career-pages.

**registries-and-gists** — Feashliaa/job-board-aggregator ★, jd-intel (npm), mikeym88/job-board-scraper, vishal/job-finder, akabbas/jobpulse, colearninglounge/ai-companies.

**github-leads-wave2** — HireShire ★, ever-jobs/ever-jobs, axm0/jobwatcher, Kayvan-Zahiri/state-of-ats-2026, binaryshrey/BOARD, SampreethAvvari/job-pilot, santifer/career-ops, Liam-Frost/AutoApply, peviitor-ro (2 of ~40 repos).

**github-niche-ats-wave2** — Masterjx9/OpenPostings ★, ever-jobs/ever-jobs (175-ATS adapter recipes), madhan0153/productcompanies, mluggy/techmap, Kajaasfaq/Company-list-Chennai, Tazril/career-scrap, BigChrisCooke/atlassian-job-board, cboyd0319/JobSentinel (fingerprint ruleset).

**technographics** — TheirStack (21 ATS pages) + Bloomberry (12 ATS pages) — named companies + vendor-stated totals. BuiltWith / Enlyft / efficient.app recorded as leads (JS-rendered / 403).

**community-and-awesome** — Bloomberry directory, technologychecker.io, ATS vendor "Customers" pages (Ashby/Lever/Greenhouse/SmartRecruiters/Teamtailor/Keka), Remotive "900+ startups" sheet, Ask-HN-Who-is-Hiring (Jun 2026), Apify actor examples (×2), TheirStack free preview.

**datasets** — HuggingFace fantastic-jobs/7M & lukebarousse/data_jobs (samples; not ATS-tagged), TheirStack greenhouse API page, and a `leads-paid-jobpost-apis` dossier. **Finding: no free public dataset tags postings by source ATS** — that data lives only in paid B2B APIs.

## Leads not yet fetched (need paid access or a headless browser)

- **BuiltWith** `trends.builtwith.com/websitelist/<Tech>` — largest untapped volume; JS-rendered, needs headless.
- **Enlyft** per-ATS customer tables — HTTP 403 bot wall.
- **TheirStack** full lists (30,429 Greenhouse / 11,602 Lever / 12,678 Ashby …) — 10 rows free, rest paywalled. Also **PredictLeads, Fantastic Jobs (54 ATS), Coresignal, Revelio** — paid, ATS-tagged.
- **~38 more peviitor-ro scraper repos** — each hardcodes EU company+ATS pairs.
- **ever-jobs `source-ats-*` (175 providers)** — exact job-feed endpoints + 1 example tenant each; a fingerprinter/expansion reference, not a company list.

## Caveats

- **Workday** slugs are the full board host (+ first path segment); some carry a `/en-US` locale rather than the site path — good enough to identify the tenant, may need site normalization before scraping.
- **Paylocity** identifies companies by GUID (the slug column holds guids; company names are in the raw source file).
- **Technographic** rows (TheirStack/Bloomberry) are company *names*, not slugs — they need resolution before use.
- **HireShire** Greenhouse mixed in some numeric IDs — the consolidator drops bare numeric junk.
- **darwinbox / keka / ripplehire** stay thin: no bulk public list exists for the India-tier ATSes; only small per-company sources do, and those are captured.

## New ATS providers surfaced (HeadStart expansion candidates)

Beyond the TODO list: **MyNextHire** (sharechat, swiggy), **TurboHire** (flipkart), plus high-volume Western/global ATSes with ready slug lists — **join.com, softgarden, manatal, oorwin, paylocity, pinpoint, rippling, avature, cornerstone, gem, recooty, snaphunt, pyjamahr** — and India HRMS suites from the ever-jobs adapter set (beisen, greythr, hrone, mokahr, talentera, zimyo, zwayam).
