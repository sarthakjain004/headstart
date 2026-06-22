# ever-jobs

- **URL:** https://github.com/ever-jobs/ever-jobs
- **Author / repo:** ever-jobs/ever-jobs (NestJS/Nx monorepo job-board aggregator)
- **License:** MIT
- **Fetched:** 2026-06-23 (shallow clone)
- **One-line:** 636 per-company source plugins + 175 per-ATS provider plugins; each
  company plugin hardcodes its live ATS endpoint.

## What was extracted
Parsed every `packages/plugins/source-company-*/src/<name>.service.ts`. The
authoritative field is the `API_URL` const (or, for 13 custom plugins, the
`<name>.constants.ts` endpoint). Company display name comes from the
`@SourcePlugin({ name })` decorator.

- `ever-jobs_companies.jsonl` / `.csv` — 636 rows: company, ats, slug, endpoint,
  plugin_token, site_enum.

## ATS breakdown (636 companies)
| ATS | count |
|---|---|
| greenhouse | 622 |
| first-party (amazon.jobs, jobs.apple.com, ibm careers, microsoft, boeing, tiktok, cursor, uber, meta, netflix, google) | 11 |
| eightfold (nvidia, zoom) | 2 |
| ashby (openai) | 1 |

- **625 / 636 have a concrete slug** (the 11 first-party ones have no third-party slug).
- Slug == plugin token for all but **robinhood → `robinhoodjobs`** (one non-derivable slug).
- Slugs are byte-for-byte from the repo; nothing guessed.

## Bonus metadata (not saved as data, noted here)
`packages/plugins/source-ats-*` = **175 distinct ATS provider scrapers**, including
several on HeadStart's TODO list: `trakstar`, `peoplestrong`, `darwinbox`, `keka`,
`sense`, plus `phenom`, `radancy`, `talentsoft`, `successfactors`, `taleo`, `icims`,
`avature`, `eightfold`, `smartrecruiters`, `workday`, etc. Useful as a fingerprinter
reference. The provider plugins are scraper code, not company lists.
