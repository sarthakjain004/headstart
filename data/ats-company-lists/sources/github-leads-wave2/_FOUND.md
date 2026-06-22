# GitHub Leads — Wave 2 — FOUND

Date: 2026-06-23
Method: direct `git clone --depth 1` / `gh api .../contents` / raw fetch (no heavy
code-search; a parallel agent covers `gh search code`). All slugs are byte-for-byte
from the repos — nothing guessed. Unfetchable → recorded in `_LEADS/LEADS.md`.

## Sources retrieved (8 with data + 2 leads)

| # | Source | URL | Primary ATS | Companies/slugs | Path |
|---|---|---|---|---|---|
| 1 | ever-jobs | github.com/ever-jobs/ever-jobs | greenhouse (622) | 636 (625 w/ slug) | `ever-jobs/` |
| 2 | jobwatcher | github.com/axm0/jobwatcher | gh 64 / ashby 54 / wd 32 | 256 (189 ats, 179 slug) | `jobwatcher/` |
| 3 | state-of-ats-2026 | github.com/Kayvan-Zahiri/state-of-ats-2026 | workday (471) | 743 | `state-of-ats-2026/` |
| 4 | bag-of-documents | github.com/dtunkelang/bag-of-documents | — | LEAD (generators only) | `_LEADS/` |
| 5a | HireShire | github.com/slowloris-98/HireShire | gh/lever/ashby | **15,533 slugs** | `HireShire/` |
| 5b | BOARD | github.com/binaryshrey/BOARD | gh/workday/ashby seeds | 222 (113+60+49) | `BOARD/` |
| 5c | job-pilot | github.com/SampreethAvvari/job-pilot | mixed (explicit URLs) | ~120 (18 explicit) | `job-pilot/` |
| 5d | AutoApply | github.com/Liam-Frost/AutoApply | gh 7 / lever 4 | 11 | `AutoApply/` |
| 5e | job-search-automation | github.com/jakemercure28/job-search-automation | — | LEAD (empty template) | `_LEADS/` |
| 5f | career-ops | github.com/santifer/career-ops | gh 37 / ashby 34 | 111 (82 w/ slug) | `career-ops/` |
| 6 | peviitor-ro (2 repos) | github.com/peviitor-ro | workday (15) | 31 | `peviitor-ro/` |

## Grand totals by ATS (≈17,532 rows; HireShire dominates)
| ATS | rows |   | ATS | rows |
|---|---|---|---|---|
| greenhouse | 9,123 | | icims | 19 |
| lever | 4,392 | | taleo | 16 |
| ashby | 3,131 | | smartrecruiters | 14 |
| workday | 578 | | avature | 13 |
| successfactors | 26 | | usajobs | 13 |
| internal/first-party | ~38 | | eightfold | 22 |
| oracle (+HCM) | 26 | | workable | 7 |
| (long tail) | phenom 4, talentbrew 4, bamboohr 3, radancy 2, teamtailor 1, personio 1, paradox 1, peoplefluent 1, ukg 1, jobvite 1 | | | |

Counts double-count companies that appear in multiple sources (e.g. NVIDIA, Stripe).
De-dup happens at the merge stage downstream — these are raw per-source extractions.

## Highest-value findings
- **HireShire** — by far the biggest: 8,180 Greenhouse + 4,368 Lever + 2,985 Ashby slugs.
- **state-of-ats-2026** — 743 enterprise employers, Workday-heavy; complements the
  startup-skewed slug lists with the Fortune-500 long tail.
- **BOARD workday_seeds** — 60 full `{tenant, wdN instance, jobsite}` tuples (the part
  of a Workday URL you can't derive from the company name).
- **Non-derivable slugs captured** (name→slug can't guess these): robinhood→`robinhoodjobs`,
  HRT→`wehrtyou`, DRW→`drweng`, Optiver→`optiverus`, Airbus→`ag`, Flex→`flextronics`,
  Michelin→`michelinhr`, Viavi→`viavisolutions`, Digitain→`digitainsoftware`,
  Heimdal→`heimdalsecurity`, Boomi→`boomilp`, WNS→`WNSGlobalServices144`.
- **ever-jobs `source-ats-*`** = 175 ATS providers (fingerprinter reference; includes
  TODO-list providers trakstar/peoplestrong/darwinbox/keka/sense).

## Data format
Per source: extracted file(s) + `SOURCE.md`. Company extractions are JSONL/CSV with
`company, ats, slug, endpoint` (+ source-specific fields). Slug-only lists kept as the
original JSON arrays. Originals (`.py`/`.yml`) kept where the data lives in code.
