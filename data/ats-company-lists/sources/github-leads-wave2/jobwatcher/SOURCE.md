# jobwatcher

- **URL:** https://github.com/axm0/jobwatcher
- **Author / repo:** axm0/jobwatcher
- **License:** MIT
- **Fetched:** 2026-06-23 (shallow clone)
- **One-line:** `research/*.md` per-company dossiers, each stating the live ATS +
  slug + XHR endpoint, hand-verified via a Chrome connector.

## What was extracted
Parsed all 256 `research/*.md` dossiers (excluded 2 `_batch`/`_deferred` index
files). Most reliable signal = the live ATS endpoint URL embedded in the prose
(greenhouse boards-api, lever v0, ashby posting-api, workday cxs/myworkdayjobs,
etc.); slug derived from that URL. Where no standard ATS endpoint exists, the
stated platform label is preserved.

- `jobwatcher_companies.jsonl` / `.csv` — 256 rows: company, ats, slug, endpoint,
  platform_label, careers_url, file.

## ATS breakdown (256 companies)
| ATS | count |   | ATS | count |
|---|---|---|---|---|
| greenhouse | 64 | | radancy | 2 |
| ashby | 54 | | smartrecruiters | 1 |
| workday | 32 | | paradox | 1 |
| eightfold | 6 | | workable | 1 |
| avature | 5 | | peoplefluent | 1 |
| lever | 5 | | successfactors | 1 |
| phenom | 4 | | ukg | 1 |
| talentbrew | 4 | | (UNKNOWN / first-party) | 67 |
| icims | 4 | | | |
| oracle | 3 | | | |

- **189 / 256 have an ATS; 179 have a slug; 168 have both ats + slug.**
- The 67 UNKNOWN are genuinely *not* on a standard ATS (first-party React/Next SPAs,
  Phenom/TalentBrew/Radancy careers microsites, Oracle Recruiting Cloud, Gem) — the
  descriptive `platform_label` is kept for each. These are not parser failures.
- Many big enterprises here (Workday tenants, Eightfold) overlap state-of-ats-2026.
