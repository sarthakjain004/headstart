# Source: ever-jobs/ever-jobs

- **URL:** https://github.com/ever-jobs/ever-jobs (branch `develop`)
- **Author/repo:** ever-jobs/ever-jobs
- **Retrieved:** 2026-06-23
- **Access:** public, full repo tarball downloaded (codeload, branch `develop`, ~5.6 MB)
- **License:** not stated in tree root (no LICENSE file observed at top level) — treat as all-rights-reserved; for intel/reference only.

## What it is
A large NestJS plugin-monorepo job-scraper. Two plugin families under `packages/plugins/`:
- **`source-ats-*` (175 providers):** one ATS-*adapter* per provider. Each carries a richly
  documented `*.constants.ts` + `*.types.ts` giving the **exact public job-feed endpoint / host
  template, pagination model, wire-field names, and a live-verification note naming ~1 known
  public tenant**. This is integration intelligence ("how to scrape provider X"), NOT a bulk
  company list.
- **`source-company-*` (636 companies):** one plugin per specific company, each wired to that
  company's ATS. **622 of 636 are Greenhouse**; only 2 others in operative code
  (`openai`→Ashby, `zoom`→Eightfold). So the company family is effectively a 622-slug
  **Greenhouse** roster.

## ATS providers (relevance to HeadStart)
Covers EVERY HeadStart niche/expansion target as a `source-ats-*` adapter, including all India-tier
TODO providers and several not on HeadStart's list:
- HeadStart TODO present: **Trakstar Hire** (`trakstar`), **SenseHQ/Skillate** (`sense`),
  **Oracle Cloud HCM** (`oracle`). (Param.ai, Kula, CareerSiteManager, Recruitee-whitelabel: NOT present.)
- India-tier present: darwinbox, keka, peoplestrong, freshteam, greythr, hrone, hron, zimyo,
  pyjamahr, turbohire, zwayam, snaphunt, talentera, oorwin, manatal, recooty, zohorecruit.
- Mid-market present: avature, cornerstone, eightfold, gem, joincom, paylocity, phenom, pinpoint,
  polymer, icims, jobvite, jazzhr, successfactors, taleo, rippling, comeet, smartrecruiters,
  workable, recruitee, bamboohr, personio, teamtailor, mokahr, beisen, +more.

## Approx counts
- 175 ATS-provider integration specs (host template + endpoint each).
- 622 unique Greenhouse company slugs (real, from operative API_URLs).
- ~1 verified example tenant per ATS provider (seed only, NOT a list).

## Files saved (artifacts/)
- `ALL_ATS_PROVIDERS.txt` — all 175 `source-ats-*` provider names.
- `greenhouse_company_slugs.csv` — 622 company→greenhouse-slug rows.
- `non_greenhouse_company_slugs.csv` — the 2 non-GH company-direct plugins.
- `company_ats_registry.json` — raw machine-extracted company→ats:slug (greenhouse-dominated).
- `ats_tenants.json` — per-provider host templates + api hosts (all 175).
- `ats_example_tenants.json` — example/known tenants harvested per target provider (noisy; real ones noted below).
- `constants/*.constants.ts` — 31 verbatim ATS adapter constants files for HeadStart target providers
  (the integration recipes: endpoints, pagination, wire shapes, verification notes).

## Real example tenants worth seeding (from adapter verification notes)
avature=`bloomberg`; beisen=`mengniu` (mengniu.zhiye.com); darwinbox=`dbox`; greythr=`greytip`;
icims=`facebook`; keka=`algoworks`; mokahr=`tesla` (Tesla China); oorwin=`purpledrive`;
oracle=`eeho`(us2); peoplestrong=`exlcareers` (EXL); pyjamahr=`jobscubicle`; sense=`sensehr`;
talentera=`careerroyaljet` (RoyalJet); turbohire=`tatamotors` (Tata Motors).

## One-line
175-ATS integration-spec monorepo (endpoints + 1 verified tenant each, covering all HeadStart
niche/India targets) plus a 622-slug Greenhouse company roster.
