# Nandish02/portfolio — job-config company lists (MULTI-ATS)

- **Source URL:** https://github.com/Nandish02/portfolio (branch: master, path: jobs/config/companies/)
- **Author/repo:** Nandish02
- **ATS providers:** Workday, SmartRecruiters, Greenhouse, Lever, Ashby
- **Approx counts (this lane = Workday + SmartRecruiters):**
  - workday.json — 93 Workday tenant/board pairs (label, tenant, wd-domain, board)
  - smartrecruiters.txt — 49 SmartRecruiters slugs
  - (also: greenhouse.txt 329, lever.txt 149, ashby.txt 189 — saved for completeness)
- **Access method:** raw.githubusercontent.com (curl)
- **Date retrieved:** 2026-06-23
- **License:** none specified in repo
- **Description:** Personal job-scraper config. workday.json is a high-value Workday
  tenant+board+wd-domain map (resolves the hardest Workday discovery problem: which
  wdN server + board name per company). SmartRecruiters file is plain slugs for the
  api.smartrecruiters.com/v1/companies/{slug}/postings endpoint.

## Files
- `workday.json` — 93 Workday {label, tenant, domain(wdN), board} objects
- `smartrecruiters.txt` — 49 SmartRecruiters slugs (one per line, #-comment header)
- `greenhouse.txt`, `lever.txt`, `ashby.txt` — adjacent ATS slug lists (out-of-lane, saved for the orchestrator)
