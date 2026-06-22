# kalil0321/ats-scrapers — ats-companies/ (FLAGSHIP MULTI-ATS DATASET, 26 providers, ~63.5k companies)

- **Source URL:** https://github.com/kalil0321/ats-scrapers (dir: ats-companies/)
- **Raw base:** https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/{ats}.csv
- **Author/repo:** kalil0321 (powers the "jobhive" scraper)
- **ATS providers (26):** ashby, avature, bamboohr, breezy, cornerstone, eightfold, gem,
  greenhouse, icims, jazzhr, join_com, lever, mercor, oracle, personio, phenom, pinpoint,
  recruitee, recruiterbox, rippling, smartrecruiters, successfactors, taleo, teamtailor,
  workable, workday
- **Approx count:** 63,485 company rows total (data rows, header excluded). Per-ATS:
  join_com 23547, bamboohr 5632, greenhouse 4966, workable 4269, ashby 2856, jazzhr 2689,
  workday 2604, personio 2463, smartrecruiters 2214, lever 2113, rippling 1923, breezy 1384,
  icims 1363, successfactors 1271, teamtailor 1010, recruitee 888, gem 496, oracle 442,
  pinpoint 350, recruiterbox 314, cornerstone 297, taleo 150, avature 87, phenom 85,
  eightfold 71, mercor 1.
- **THIS LANE (Workday/SmartRecruiters/Workable/Recruitee + secondary):** workday 2604,
  smartrecruiters 2214, workable 4269, recruitee 888; teamtailor 1010, bamboohr 5632,
  breezy 1384, personio 2463, icims 1363, oracle 442, successfactors 1271, taleo 150.
- **Access method:** raw.githubusercontent.com (curl), all 26 CSVs saved in full
- **Date retrieved:** 2026-06-23
- **License:** see repo
- **Schema:** canonical `name,slug,url`. `slug` = the scraper/API identifier (lowercase,
  deterministic) — the column to prefer. `url` = canonical public careers URL. A few legacy
  files use 2-col `name,url`. For Workday the slug embeds tenant+board (e.g.
  `3m/search`, host `3m.wd1.myworkdayjobs.com/search`).
- **Description:** The single most valuable find: a maintained, per-ATS tenant list with
  deterministic slugs and careers URLs, one CSV per ATS, auto-published via GitHub Actions.
  Covers EVERY provider in this lane plus 20 more. URL-format table for each ATS is in README.md.

## Files (all in full)
- 26 `{ats}.csv` files (name,slug,url) + `README.md` (schema + per-ATS URL formats)
