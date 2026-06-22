# Source: axm0/jobwatcher  (backend for GleanJobs / gleanjobs.com)

- **Source URL:** https://github.com/axm0/jobwatcher
- **File pulled:** https://raw.githubusercontent.com/axm0/jobwatcher/main/config/companies_supported.yaml (~286 KB, **671 companies**)
- **Author/repo:** axm0. Description: "Backend for GleanJobs (gleanjobs.com) — daily scraper aggregating ~500 company career boards into a US-focused public feed."
- **ATS provider(s) covered (this lane):** Greenhouse, Lever, Ashby. The roster's `board_type` field also enumerates MANY other providers (see leads below).
- **Approx entry count (this lane, unique slugs with company names):**
  - Greenhouse: **196**
  - Ashby: **118**
  - Lever: **16**
  - (330 total greenhouse+lever+ashby)
- **How accessed:** `curl` of raw `config/companies_supported.yaml`; parsed with PyYAML. Each company has `name`, `board_type`, `board_url`, `career_urls[]`, and a verification `notes` field. Slug extracted from `board_url`/`career_urls` per provider host, URL-decoded + lowercased, paired with `name`.
- **Date accessed:** 2026-06-23
- **License:** MIT
- **Description:** THE highest-confidence structured source found in this lane. It is the production roster of a live daily scraper: each entry is explicitly tagged with its `board_type` and a verified `board_url`, with `notes` describing how/when the board was confirmed (e.g. "verified via signed-out UI and posting API on 2026-04-24"). Slugs include non-derivable ones (e.g. Anduril → `greenhouse:andurilindustries`, Quizlet → `lever:quizlet-2`, Apex → `ashby:apex-technology-inc`). The repo additionally has `research/*.md` (258 files), one per company, each documenting the exact ATS, slug/tenant, and verified API endpoint + counts.
- **Bonus value (out of lane, logged for orchestrator):** the `board_type` histogram across the 671 companies is itself a provider-coverage map — it confirms real companies on `phenom` (22), `eightfold` (14), `jibe` (13), `talentbrew` (15), `getro` (5), `consider` (5), `gem` (7), `rippling_ats` (4), `avature` (9), `recruitee`, `workable`, `smartrecruiters` (10), `icims` (4), `successfactors`, `oracle_cloud` (16), `phenom`, etc. Several of these (gem, getro, consider, phenom, eightfold, jibe, talentbrew) are NOT in HeadStart's current fingerprinter TODO list and are good "new ATS provider" candidates.

## Files saved (CSV: `slug,company_name`)
- `greenhouse_slugs.csv` — 196 rows
- `ashby_slugs.csv` — 118 rows
- `lever_slugs.csv` — 16 rows
