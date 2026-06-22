# Source: ambicuity/New-Grad-Jobs

- **Source URL:** https://github.com/ambicuity/New-Grad-Jobs
- **File pulled:** https://raw.githubusercontent.com/ambicuity/New-Grad-Jobs/main/docs/jobs.json (~22 MB, 1,306 scraped job records) — the OUTPUT feed only.
- **Author/repo:** ambicuity. A new-grad job aggregator.
- **ATS provider(s) covered (this lane):** Greenhouse, Lever, Ashby (feed also has Workday, JobSpy/Indeed).
- **Approx entry count (this lane, unique slugs extracted from REAL job URLs in jobs.json):**
  - Greenhouse: **46**
  - Ashby: **18**
  - Lever: **2**
  - (66 total greenhouse+lever+ashby)
- **How accessed:** `curl` of raw `docs/jobs.json`; slugs extracted from each record's `url` field (the live apply URL) per provider host, deduped, paired with `company`.
- **Date accessed:** 2026-06-23
- **License:** (none verified — check repo)
- **Description:** A daily new-grad job feed. Slugs here are REAL because they come from the scraper's output (`docs/jobs.json` = jobs that were actually fetched and returned), e.g. OpenAI/ashby, Anthropic/greenhouse, Palantir/lever. All extracted slugs are recognizable real companies.

## ⚠️ INTEGRITY WARNING — most of this repo's "company list" is FABRICATED, DO NOT USE
- The repo's `config.yml` company roster is **synthetically generated** by `scripts/generate_companies.py`, which combinatorially invents fake company names + slugs from word lists:
  - `generate_greenhouse_companies(4000)` → names like "Smart Pay Tech" → slug `smartpaytech`, URL `boards-api.greenhouse.io/v1/boards/smartpaytech/jobs`
  - `generate_lever_companies(1900)` → "Software Saas 1" → slug `softwaresaas1`
  - `generate_workday_companies(1300)` → fake `*.wdN.myworkdayjobs.com` tenants
- These ~7,200 generated entries are NOT real boards — they are name-pattern fabrications. **The config.yml / `*_batch.txt` / `jobs.json` top-level config slug list was deliberately NOT harvested.**
- Only `docs/jobs.json` (the scraper OUTPUT — boards that actually returned jobs) was used here, which filters the fabrications down to real boards. The 66 slugs saved are trustworthy; the repo's config is not.

## Files saved (CSV: `slug,company_name`)
- `greenhouse_slugs.csv` — 46 rows
- `ashby_slugs.csv` — 18 rows
- `lever_slugs.csv` — 2 rows
