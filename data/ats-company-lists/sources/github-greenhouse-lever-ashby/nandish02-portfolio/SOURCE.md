# Source: Nandish02/portfolio

- **Source URL:** https://github.com/Nandish02/portfolio/tree/master/jobs/config/companies
- **Files pulled:**
  - https://raw.githubusercontent.com/Nandish02/portfolio/master/jobs/config/companies/greenhouse.txt
  - https://raw.githubusercontent.com/Nandish02/portfolio/master/jobs/config/companies/ashby.txt
  - https://raw.githubusercontent.com/Nandish02/portfolio/master/jobs/config/companies/lever.txt
  - https://raw.githubusercontent.com/Nandish02/portfolio/master/jobs/config/companies/smartrecruiters.txt (bonus, out-of-lane provider — saved for the other lane)
- **Author/repo:** Nandish02 (GitHub), "Portfolio Website" repo
- **ATS provider(s) covered:** Greenhouse, Lever, Ashby (+ SmartRecruiters, + a workday.json not pulled)
- **Approx entry count (unique, comment/blank lines stripped):**
  - Greenhouse: **305** slugs
  - Ashby: **180** slugs
  - Lever: **135** slugs
  - SmartRecruiters: **49** slugs (out of lane)
- **How accessed:** direct `curl` of each raw `.txt`. Files saved verbatim (comments preserved).
- **Date accessed:** 2026-06-23
- **License:** None declared in repo.
- **Description:** A curated config list of ATS board slugs used by a personal job-tracking tool. Header comments state the lists are "Curated for H-1B-sponsoring SWE-intern-friendly companies." Endpoints documented in-file (greenhouse `boards-api.greenhouse.io/v1/boards/{slug}/jobs`, lever `api.lever.co/v0/postings/{slug}`, ashby `api.ashbyhq.com/posting-api/job-board/{slug}`).
- **CAVEAT — quality:** This is a hand-curated list and clearly includes **name-derivation GUESS variants** as fallbacks (e.g. `ramp1`, `roblox1`, `roblox2`, `unity`/`unity3d`, `glean`/`glean1`, `mem`/`mem0`/`mem0ai`). Many are well-known real boards; the numbered/variant entries are speculative and NOT individually verified-live. Treat as candidate slugs needing liveness validation, not a verified set.

## Files saved
- `greenhouse.txt` (333 lines / 305 unique slugs)
- `ashby.txt` (192 lines / 180 unique slugs)
- `lever.txt` (152 lines / 135 unique slugs)
- `smartrecruiters.txt` (51 lines / 49 unique slugs — out of lane, here for completeness)
