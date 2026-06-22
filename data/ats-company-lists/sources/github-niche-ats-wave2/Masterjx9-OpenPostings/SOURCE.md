# Source: Masterjx9/OpenPostings  ★ HIGHEST-VALUE SOURCE THIS WAVE

- **URL:** https://github.com/Masterjx9/OpenPostings (file: `jobs.db`, branch `main`)
- **Author/repo:** Masterjx9/OpenPostings
- **Retrieved:** 2026-06-23
- **Access:** public; 40 MB SQLite `jobs.db` downloaded via repo tarball (raw CDN copy was byte-
  identical but opened cleanly only from the tarball). Parsed locally with sqlite3.
- **License:** not confirmed in tree root; reference use only.
- **ATS providers:** ~80, including nearly every HeadStart niche/expansion target.

## What it is
A job-aggregator app shipping a SQLite DB whose **`companies` table = 61,610-row global
company → ATS → career-URL registry** (columns: `company_name`, `url_string`, `ATS_name`). The
`Postings` table is empty (no job rows shipped), but the company/ATS mapping is the valuable asset
and is exactly HeadStart's `resolve/` (company → ats:slug) need. Companies are global (US/EU/India/
APAC), not India-scoped. The repo's `server/ats/<provider>/service.js` confirms 89 supported ATS
adapters.

## ATS_name distribution (top, of 61,610 rows)
ycombinator 5872 · bamboohr 5144 · workday 4621 · breezyhr 4317 · applytojob(JazzHR) 3467 ·
recruitee 2734 · greenhouse 2680 · hrmdirect 2404 · **icims 2392** · softgarden 2367 ·
**zoho(Recruit) 1751** · personio 1691 · applicantpro 1554 · join 1543 · ashby 1444 ·
**rippling 1381** · applitrack 1323 · **manatal 1238** · teamtailor 1019 · **freshteam 987** ·
ultipro 937 · **trakstar 727** · paycor 615 · taleo 547 · adp 546 · prismhr 523 · isolved 494 ·
**jobvite 454** · **pinpointhq 416** · **gem 364** · ukg 363 · homerun 323 · **paylocity 294** ·
dover 291 · careerplug 257 · hireology 257 · lever 236 · **comeet 184** · talentlyft 188 ·
**avature 92** · **oraclecloud 90** · **oorwin 83** · eightfold 56 · … (full list in
`_ats_name_distribution.json`).

## HeadStart niche/target coverage — UNIQUE SLUGS EXTRACTED (per-provider CSVs)
| provider | unique slugs | file |
|---|---|---|
| **Trakstar Hire** (HeadStart TODO) | **726** | `slugs_trakstar.csv` |
| iCIMS | 2386 | `slugs_icims.csv` |
| Rippling | 1378 | `slugs_rippling.csv` |
| Zoho Recruit | 1751 | `slugs_zohorecruit.csv` |
| Recruitee | 2733 | `slugs_recruitee.csv` |
| Personio | 1691 | `slugs_personio.csv` |
| Join.com | 1543 | `slugs_join.csv` |
| Ashby | 1440 | `slugs_ashby.csv` |
| Freshteam | 986 | `slugs_freshteam.csv` |
| SoftGarden | 2366 | `slugs_softgarden.csv` |
| BambooHR | 5138 | `slugs_bamboohr.csv` |
| BreezyHR | 4316 | `slugs_breezyhr.csv` |
| JazzHR (applytojob) | 3463 | `slugs_jazzhr.csv` |
| Teamtailor | 1008 | `slugs_teamtailor.csv` |
| Jobvite | 454 | `slugs_jobvite.csv` |
| Pinpoint | 416 | `slugs_pinpoint.csv` |
| Paylocity | 294 | `slugs_paylocity.csv` |
| Taleo | 243 | `slugs_taleo.csv` |
| Manatal | 193 (careers-page.com) | `slugs_manatal.csv` |
| Comeet | 184 | `slugs_comeet.csv` |
| Avature | 92 | `slugs_avature.csv` |
| **Oracle Cloud HCM** (HeadStart TODO) | **90** | `slugs_oracle.csv` |
| Oorwin | 83 | `slugs_oorwin.csv` |
| Eightfold | 56 | `slugs_eightfold.csv` |

Host patterns confirmed from the data (useful for the fingerprinter):
- Trakstar `{slug}.hire.trakstar.com` · Zoho `{slug}.zohorecruit.com|.in` · Freshteam
  `{slug}.freshteam.com` · Pinpoint `{slug}.pinpointhq.com` · Manatal `{slug}.careers-page.com` ·
  SoftGarden `{slug}.softgarden.io/vacancies` · Comeet `www.comeet.com/jobs/{slug}/...` ·
  **Oracle Cloud HCM** `{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/...`
  (e.g. `epwk`, `eipn`, `estm`, plus the `fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com` ocs variant —
  exactly HeadStart's Icertis-class TODO) · Oorwin `{slug}.oorwin.com` · Eightfold `{slug}.eightfold.ai`.

NB: **No Darwinbox / Keka / SenseHQ / PeopleStrong** rows in this DB (0 each) — for those, the
Chennai + Tazril sources in this wave are the slug data.

## Files saved (artifacts/)
- `companies_full.csv` — all 61,610 rows (company\turl\tats), TSV.
- `slugs_<provider>.csv` — 24 per-provider deduped slug lists (niche/target providers).
- `_ats_name_distribution.json` — full ATS_name → count.
- `_provider_slug_counts.json` — provider → unique-slug count.
- `supported_ats_providers.txt` — 89 `server/ats/*` adapter names.

## One-line
61,610-company global ATS registry (SQLite) — the single biggest niche-provider slug source this
wave: 726 Trakstar, 1751 Zoho, 1378 Rippling, 90 Oracle Cloud HCM, 2386 iCIMS, +20 more providers.
