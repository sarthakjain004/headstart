# Source: Feashliaa/job-board-aggregator

- **Source URL (repo):** https://github.com/Feashliaa/job-board-aggregator
- **Raw data dir:** https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/
- **Author:** Riley Dorrington (GitHub: Feashliaa)
- **ATS provider(s):** Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS, Paylocity
- **Access method:** `curl` to raw.githubusercontent.com (per-file)
- **Date retrieved:** 2026-06-23
- **License:** MIT (Copyright (c) 2026 Riley Dorrington)
- **Description:** Dedicated bulk slug datasets for four major ATS providers, one JSON array per provider. By far the largest single source found in this lane.

## Files & exact URLs

| File | URL | ATS | Count | Format |
|------|-----|-----|-------|--------|
| `greenhouse_companies.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json | Greenhouse | 8,333 | JSON array of slug strings |
| `lever_companies.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json | Lever | 4,368 | JSON array of slug strings |
| `ashby_companies.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json | Ashby | 3,161 | JSON array of slug strings |
| `workday_companies.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/workday_companies.json | Workday | 12,884 | JSON array of `slug\|instance\|site` strings |
| `bamboohr_companies.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/bamboohr_companies.json | BambooHR | 11,316 | JSON array of slug strings (`{slug}.bamboohr.com`) |
| `icims_companies.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/icims_companies.json | iCIMS | 10,108 | JSON array of slug strings |
| `paylocity_companies_clean.json` | https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/paylocity_companies_clean.json | Paylocity | 10,252 | JSON array of `{guid,name,jobs}` |

**Total: 60,422 entries across 7 ATS providers.**

## Other files in the repo `/data` dir (NOT saved, not company-slug lists)
- `locations.json` (29 MB) — geo/location reference data, not companies.
- `salary/`, `trends/` — empty directories.
- `README.md` — repo data doc.

## Notes
- Greenhouse/Lever/Ashby files are plain slug arrays (e.g. `"stripe"`, `"plaid"`, `"notion"`).
  Verified known slugs present: stripe, airbnb, cloudflare (GH); plaid, netflix (Lever);
  notion, linear, ramp (Ashby).
- Workday entries encode the full coordinates needed to build a board URL:
  `https://{slug}.{instance}.myworkdayjobs.com/{site}` — e.g. `23andme|wd5|23`,
  `nvidia|wd5|...`. Instance distribution: wd1 (8841), wd5 (1652), wd3 (1521), wd12 (276), ...
  This is the highest-value part of the source because Workday tenants are otherwise very hard
  to enumerate (the slug alone is not enough — you also need instance + site).
- Discovered indirectly: `santifer/career-ops` (`scan-ats-full.mjs`) consumes this repo as its
  upstream `DATASET_BASE`.
