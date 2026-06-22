# state-of-ats-2026

- **URL:** https://github.com/Kayvan-Zahiri/state-of-ats-2026
- **Author / repo:** Kayvan-Zahiri/state-of-ats-2026
- **License:** NOASSERTION on the repo; the data file header states "Republish
  individual stats with attribution to ResumeAI." Underlying source: ResumeAI —
  "State of ATS 2026" (https://withresumeai.com/reports/state-of-ats-2026).
- **Fetched:** 2026-06-23 (shallow clone)
- **One-line:** 743 large/Fortune-500-tier employers mapped to their ATS.

## What was extracted
Copied the single data file `data/companies.csv` verbatim.

- `companies.csv` — 743 rows. Columns:
  `name, slug, industry, ats_system, hiring_volume_tier, top_roles, source_url, verified`.
- Per the header: 337 rows `verified=true` (re-checked against live careers portals
  2026-06-15); 406 `verified=false` (unconfirmed).

## ATS breakdown (743 companies)
| ATS | count |   | ATS | count |
|---|---|---|---|---|
| Workday | 471 | | Eightfold | 14 |
| Greenhouse | 104 | | USAJobs | 13 |
| SuccessFactors | 25 | | SmartRecruiters | 11 |
| Internal ATS | 24 | | Avature | 8 |
| Oracle Cloud HCM | 23 | | Lever | 8 |
| Taleo | 16 | | Ashby | 7 |
| iCIMS | 15 | | Jobvite / Internal (Google/MS) | 4 |

- Heavily Workday-skewed (enterprise reality). `slug` is provided per row but for
  Workday it is the short company token, **not** the full tenant.instance/jobsite
  path — treat Workday slugs here as a starting hint, not a ready endpoint.
- Highest-value complement to the startup-skewed Greenhouse/Ashby/Lever lists.
