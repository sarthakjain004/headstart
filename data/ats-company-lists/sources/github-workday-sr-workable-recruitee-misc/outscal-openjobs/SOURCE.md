# outscal/OpenJobs — data/companies_v2.json (gaming/tech company list with ats_links)

- **Source URL:** https://github.com/outscal/OpenJobs (file: data/companies_v2.json)
- **Raw:** https://raw.githubusercontent.com/outscal/OpenJobs/main/data/companies_v2.json
- **Author/repo:** outscal (Outscal — gaming/tech careers community)
- **ATS providers (recognized in ats_links):** greenhouse 775, lever 336, smartrecruiters 304,
  workable 251, ashby 234, bamboohr 218, workday 183, jobvite 112, recruitee 71, breezy 59,
  zohorecruit 46, teamtailor 45, personio 40, applytojob(jazzhr) 39, join.com 33,
  trakstar-hire 14, keka 13, workday(other) 2, icims 1, eightfold 1, darwinbox 1.
- **Approx count:** 12,144 companies total; 7,007 have >=1 ats_links entry; 2,445 have a
  RECOGNIZED ATS host. THIS LANE: smartrecruiters 304, workable 251, workday 183+2, recruitee 71;
  secondary: bamboohr 218, teamtailor 45, breezy 59, personio 40, zoho 46, trakstar 14, keka 13.
- **Access method:** raw.githubusercontent.com (curl, saved full 4.24MB)
- **Date retrieved:** 2026-06-23
- **License:** see repo
- **Schema:** array of {name, website, industry_category, type, game_genre[], tech_stack[],
  ats_links[], list_urls[], countries[]}. ats_links holds the careers/ATS URL(s). NOTE: dirty
  rows exist (placeholder strings like "aaaa...", bare company-domain /careers/ URLs); filter to
  recognized ATS hosts for clean company->ATS mapping.
- **Description:** Independent of kalil0321. Gaming-heavy (8350 gaming, 2534 tech) but strong
  for the project's TODO ATS gaps — carries Trakstar Hire, Keka, Zoho Recruit, Darwinbox hosts
  that other sources here lack. Good India + global coverage.

## Files
- `companies_v2.json` — 12,144-element JSON array (company objects with ats_links)
