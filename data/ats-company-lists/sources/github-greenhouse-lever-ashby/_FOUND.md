# GitHub — Greenhouse / Lever / Ashby company-slug lists (lane: github-greenhouse-lever-ashby)

Harvested 2026-06-23 via `gh` code/repo search + raw.githubusercontent.com / codeload fetches.
All slugs below were extracted from REAL files actually retrieved from GitHub. No slugs invented.

## Deduplicated grand totals (unique slugs, union across all 9 sources)
| ATS | Unique slugs |
|-----|--------------|
| **Greenhouse** | **2,453** |
| **Lever** | **1,201** |
| **Ashby** | **1,791** |
| **Total unique (all 3)** | **5,156** |

## Sources saved (9)

| # | Source (repo) | URL | ATS covered | Count (gh / lever / ashby) | License | Folder |
|---|---------------|-----|-------------|-----------------------------|---------|--------|
| 1 | **kalpthakkar/JobSniper** | https://github.com/kalpthakkar/JobSniper | GH, Lever, Ashby | 1,711 / 819 / 1,407 | none | `kalpthakkar-jobsniper/` |
| 2 | **outscal/OpenJobs** | https://github.com/outscal/OpenJobs | GH, Lever, Ashby | 618 / 307 / 227 | MIT | `outscal-openjobs/` |
| 3 | **axm0/jobwatcher** (GleanJobs) | https://github.com/axm0/jobwatcher | GH, Lever, Ashby | 196 / 16 / 118 | MIT | `axm0-jobwatcher/` |
| 4 | **Nandish02/portfolio** | https://github.com/Nandish02/portfolio | GH, Lever, Ashby (+SR) | 305 / 135 / 180 | none | `nandish02-portfolio/` |
| 5 | **vishal-yadav111/Job-Finder-main-app** | https://github.com/vishal-yadav111/Job-Finder-main-app | GH, Lever, Ashby | 30 / 30 / 30 | none | `vishal-jobfinder/` |
| 6 | **haxsysgit/Haxjobs** | https://github.com/haxsysgit/Haxjobs | GH, Lever, Ashby | 16 / 10 / 60 | none | `haxsysgit-haxjobs/` |
| 7 | **ambicuity/New-Grad-Jobs** (OUTPUT only) | https://github.com/ambicuity/New-Grad-Jobs | GH, Lever, Ashby | 46 / 2 / 18 | n/a | `ambicuity-newgradjobs/` |
| 8 | **remoteintech/remote-jobs** | https://github.com/remoteintech/remote-jobs | GH, Lever, Ashby | 9 / 15 / 4 | NOASSERTION | `remoteintech-remote-jobs/` |
| 9 | **mshen1019/Argus** | https://github.com/mshen1019/Argus | GH, Lever, Ashby | 25 / 4 / 6 | none | `mshen1019-argus/` |

**Best sources by quality:** #1 JobSniper (largest — slugs from real apply URLs) and #3 jobwatcher (highest confidence — production roster, each entry board_type-labeled + liveness-verified, includes non-derivable slugs). #2 OpenJobs is broad with company names attached (MIT).

Each subfolder has a `SOURCE.md` with full provenance. CSV files carry `slug,company_name`; `.txt`/`.py` files are saved verbatim from the repo.

## Integrity notes / things excluded
- **ambicuity/New-Grad-Jobs config is FABRICATED** — its `scripts/generate_companies.py` combinatorially invents ~7,200 fake greenhouse/lever/workday slugs from word lists (e.g. "Smart Pay Tech"→`smartpaytech`). Its `config.yml` slug roster was DELIBERATELY NOT harvested. Only `docs/jobs.json` (the scraper OUTPUT = boards that actually returned jobs) was used → 66 real slugs. See its SOURCE.md.
- **Nandish02 / vishal** lists include name-derivation GUESS variants (`ramp1`, `roblox2`, lever `zomato`/`swiggy`) that are NOT verified live — flagged as candidate-only in their SOURCE.md. (Per HeadStart's own notes, Zomato's real board is `smartrecruiters:Zomato1`, not `lever:zomato`.)
- **decal/bounty-targets `greenhouse-domains.txt`** — DEAD END. Contains Greenhouse's OWN corporate subdomains (app/api/boards.greenhouse.io), not customer company slugs.
- **kalpthakkar/JobSniper `data/depreciated_tokens.txt`** — tokens that FAILED liveness (expired boards). Not saved as a live list; useful only as a negative set.

## Dead-ends (fetched, no usable in-lane slug list)
- `decal/bounty-targets/greenhouse-domains.txt` — Greenhouse's own subdomains, not customers.
- `marcuswd/scrap_positions/ext/sources.txt` — list of ATS *domains*, no slugs.
- `devdattatalele/auto-apply/targets.example.txt` — empty placeholder template only.
- `Codesee-io/remote-companies/company-list.md` — big remote-company table but only ~5 greenhouse/lever links; rest are custom career domains (LEAD for embed-scan, not direct slugs).
- `dhrumilankola/Fillr_AutoApply .../greenhouse_companies.py` — scraper script fully commented out, no list.
- `andrew-shwetzer/career-ops-plugin/references/ats-endpoints.md` — API URL-pattern docs, not a company list.

## Best LEADS for a follow-up wave
1. **ever-jobs/ever-jobs** (https://github.com/ever-jobs/ever-jobs) — monorepo with **636 `source-company-*` plugins** (dir names = company tokens) + dedicated `source-ats-{greenhouse,lever,ashby,clearcompany,niceboard,...}` plugins. The per-company ATS+slug is inside each plugin's `*.constants.ts`/`*.service.ts` (not a central registry), so extraction needs reading ~636 files or cloning + a TS-aware scan. High value, higher effort. Also enumerates MANY ATS providers as plugins.
2. **axm0/jobwatcher `research/*.md`** (258 files) — one verified dossier per company (exact ATS, slug/tenant, live API endpoint + counts). Already harvested the roster YAML; the research dir is an even richer per-company verification corpus if individual confirmation is wanted.
3. **outscal/OpenJobs is a fork of `santifer/career-ops`** (MIT) — career-ops is the skill framework; check its sibling/downstream forks for other bundled `companies_v2.json`-style datasets.
4. **Codesee-io/remote-companies** + **remoteintech/remote-jobs** (878 companies) — large company-name/career-URL directories; feed the non-ATS-host entries (most of them) into HeadStart's careers-page embed scan to resolve slugs.
5. **New-ATS-provider signal (out of lane but valuable):** jobwatcher's `board_type` histogram across 671 companies confirms real companies on providers NOT in HeadStart's current fingerprinter TODO: **gem** (jobs.gem.com), **getro**, **consider** (consider.com), **phenom** (22 cos), **eightfold** (14), **jibe** (13), **talentbrew** (15), **avature** (9), plus rippling_ats, hibob, pinpoint, comeet, breezy_hr, clinch, jazzhr. Worth feeding to the fingerprinter-expansion track.
6. **Unfetched code-search hits worth a targeted look:** `Infrasity-Labs/developer-marketing-jobs` (skills/expand-greenhouse-companies.md), `MabudAlam/JobsScraper`, `peviitor-ro/*` scrapers (multiple repos, EU-focused), `connorgiles/job-hoarder`, `skyforce77/jobtracker`, `job-hunter-toolkit/job-hunter-toolkit`.

## Method notes (for reproducibility)
- `gh search code` is rate-limited (~10/min) and **fails silently when run concurrently** — run code searches strictly sequentially (one at a time).
- `gh search repos` treats space-separated terms as AND (very strict); use 1-2 broad terms.
- Bulk extraction of slugs from job-listing dumps (JobSniper data.json, OpenJobs companies_v2.json, jobwatcher YAML) is far higher-yield than per-file fetches. Download big files via raw/codeload, regex the live board host out of apply/board URLs, URL-decode + lowercase.
