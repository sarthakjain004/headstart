# ATS company lists — lane: package registries + GitHub gists/code

Harvested 2026-06-23. All data was actually fetched and verified to contain real company/slug
data before saving. Counts below are exact (parsed from the saved files).

## Saved sources

| # | Source (folder) | Origin | ATS provider(s) | Entries | Access |
|---|-----------------|--------|-----------------|---------|--------|
| 1 | `feashliaa-job-board-aggregator/` | GitHub repo Feashliaa/job-board-aggregator (raw `/data`) | Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS, Paylocity | **60,422** | curl raw.githubusercontent.com |
| 2 | `jd-intel-npm/` | npm package `jd-intel@0.7.0` (bundled `registry/*.json`) | Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Teamtailor, Workday | **315** | npm tarball -> extract |
| 3 | `mikeym88-job-board-scraper/` | GitHub repo mikeym88/job-board-scraper | Greenhouse | **89** | curl raw |
| 4 | `vishal-job-finder/` | GitHub repo vishal-yadav111/Job-Finder-main-app | Greenhouse, Lever, Ashby, Workday | **~115** | curl raw |
| 5 | `akabbas-jobpulse/` | GitHub repo akabbas/JobPulse | Greenhouse | **~10** | curl raw |
| 6 | `colearninglounge-ai-companies/` | GitHub repo colearninglounge/co-learning-lounge | MIXED career URLs (some Greenhouse/Lever/Workday/Workable/iCIMS) | **~206 career links** | curl raw |

## Totals per ATS (clean slug lists only; sources 1-5)

- **Greenhouse:** 8,333 (Feashliaa) + 129 (jd-intel) + 89 (mikeym88) + ~30 (vishal) + ~10 (akabbas) = **~8,591** (heavy overlap; Feashliaa dominates)
- **Lever:** 4,368 (Feashliaa) + 30 (jd-intel) + ~30 (vishal) = **~4,428**
- **Ashby:** 3,161 (Feashliaa) + 47 (jd-intel) + ~30 (vishal) = **~3,238**
- **Workday:** 12,884 (Feashliaa, with instance+site) + 27 (jd-intel, with config) + ~25 (vishal, with base_url) = **~12,936**
- **SmartRecruiters:** 28 (jd-intel only)
- **Recruitee:** 23 (jd-intel only)
- **Teamtailor:** 31 (jd-intel only)
- **BambooHR:** 11,316 (Feashliaa only)
- **iCIMS:** 10,108 (Feashliaa only)
- **Paylocity:** 10,252 (Feashliaa only — guid+name+jobcount)

**Grand total saved: ~60,951 ATS slug/company entries + ~206 career-page links.**

## Headline finding
`Feashliaa/job-board-aggregator` (MIT) is the single most valuable artifact in this lane:
**60,422 entries across 7 ATS** in dedicated per-provider JSON files. Highlights:
- **12,884 Workday entries** encoded as `slug|instance|site` — Workday tenants are otherwise very
  expensive to enumerate, so this is a large coverage win.
- **11,316 BambooHR + 10,108 iCIMS + 10,252 Paylocity** slugs — three ATS with little other public
  slug coverage (iCIMS and Paylocity in particular). BambooHR and iCIMS are explicit targets on the
  project's ATS list.
`jd-intel` (npm) is the best complement: the only source here with SmartRecruiters / Recruitee /
Teamtailor slugs, and it carries company names + sectors.

## Dead-ends (searched, nothing usable)
- npm `gatsby-source-*` plugins (greenhouse/lever/ashby/smartrecruiters/teamtailor), `job-hoarder`,
  `@idriszade/v9-job-board-aggregator`, `@joblist/job-board-providers`, various `@bull-board/*`,
  `@mergeapi/*`, MCP servers (`ashby-mcp`, `jd-intel-mcp`, keyword scanners): all ship **only code /
  API clients**, no bundled company or slug lists. (`@joblist`, `job-hoarder`, `idriszade` tarballs
  inspected — confirmed no data files.)
- PyPI: `jobspy` (the name squats a Redis job-IO lib, NOT the scraper), `python-jobspy`,
  `JobFunnel`, `jobscraper`, `jobspider` — these scrape aggregator sites (LinkedIn/Indeed/Glassdoor)
  at runtime and **do not bundle ATS company/slug lists**. Not downloaded/extracted further.
- GitHub gist-specific search: the GitHub API has **no gist content search endpoint**; gist bodies
  are not indexed by `gh search code`. Could not enumerate "all-greenhouse-companies" gists this way.
  (Code search over regular repos was used instead and was productive.)
- Hit GitHub code-search **rate limits (HTTP 403)** mid-run on the authenticated token; recovered
  after a short wait. Some query variants returned nothing before the limit and were not all retried.

## Best LEADS for follow-up (not in this lane, or not yet fetched)
1. **`santifer/career-ops`** (https://github.com/santifer/career-ops) — `scan-ats-full.mjs` pulls
   from Feashliaa (already captured). Its own `providers/*.mjs` + `portals.yml` may list additional
   tracked companies. Worth a direct repo browse.
2. **Re-run GitHub code search** for more slug-list files once rate limit is fresh — promising
   un-fetched hits seen in result lists: `Liam-Frost/AutoApply` (`config/companies.yaml.example`),
   `SampreethAvvari/job-pilot` (`scripts/seed_companies.py`), `jakemercure28/job-search-automation`
   (`scraper-service/src/lib/config.ts`), `slowloris-98/HireShire` (`hireshire/config.py`),
   `d4551/baobuildbuddy` (`jobIntelligence.ts`), `binaryshrey/BOARD` (`ASHBYHQ/pipeline.py`).
3. **Feashliaa `/data` dir fully enumerated** (via contents API) and all 7 company datasets fetched
   (greenhouse, lever, ashby, workday, bamboohr, icims, paylocity). No SmartRecruiters / Recruitee /
   Workable / Teamtailor files exist there. The only other files are `locations.json` (29 MB geo data,
   not companies) and empty `salary/` + `trends/` dirs. RESOLVED — nothing further to pull here.
4. **PyPI `python-jobspy`** is a live scraper, not a list — but its GitHub repo (Bunsly/JobSpy) is a
   different lane's target if anyone bundles company seeds there.
