# Source: outscal/OpenJobs

- **Source URL:** https://github.com/outscal/OpenJobs
- **File pulled:** https://raw.githubusercontent.com/outscal/OpenJobs/main/data/companies_v2.json (~4.2 MB, **12,144 companies**)
- **Author/repo:** Outscal (org). Description: "Harvest growth/tech jobs from 2,000+ gaming/tech companies via public ATS APIs into CSV. Fork of santifer/career-ops with Outscal harvester layer." 22 stars.
- **ATS provider(s) covered:** Greenhouse, Lever, Ashby (the JSON's `ats_links`/`list_urls` also include direct career-site URLs, Workday, etc. — only the 3 in-lane providers extracted).
- **Approx entry count (this lane, unique slugs):**
  - Greenhouse: **618**
  - Lever: **307**
  - Ashby: **227**
  - (1,152 total greenhouse+lever+ashby)
- **How accessed:** `curl` of raw `data/companies_v2.json`. Each company record has a `name` plus `ats_links`/`list_urls` arrays. Slugs extracted by regex over those URLs (greenhouse `(?:boards|job-boards)(.eu)?.greenhouse.io/{slug}`, lever `jobs.(eu.)?lever.co/{slug}`, ashby `jobs.ashbyhq.com/{slug}`), URL-decoded + lowercased, paired with the company name.
- **Date accessed:** 2026-06-23
- **License:** MIT
- **Description:** A large curated dataset of gaming + tech companies with their career/ATS links and metadata (industry_category, tech_stack, game_genre, countries). Strong because slugs come WITH company names and the dataset is broad (12k companies, global). Slugs are sourced from the project's own ATS link list (real boards), though not every one was liveness-checked at extraction time. Minor noise: one lever entry `a` and one greenhouse `113134` (job id, not a slug).
- **NB:** This is a fork of `santifer/career-ops` — see _FOUND.md leads to check the upstream for an even larger/cleaner dataset.

## Files saved (CSV: `slug,company_name`)
- `greenhouse_slugs.csv` — 618 rows
- `lever_slugs.csv` — 307 rows
- `ashby_slugs.csv` — 227 rows
