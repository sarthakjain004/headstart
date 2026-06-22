# Source: mluggy/techmap

- **URL:** https://github.com/mluggy/techmap (dir: `jobs/*.csv`)
- **Author/repo:** mluggy/techmap (Michael Luggy — Israeli tech-jobs map)
- **Retrieved:** 2026-06-23
- **Access:** public, all 19 category CSVs fetched via raw.githubusercontent.com
- **License:** none stated in file; CSVs carry `utm_source=techmap` attribution. Reference use only.
- **ATS providers:** primarily **Comeet** (Israeli tech standard), plus a little Lever.

## What it is
A daily-updated dataset of Israeli/global tech job openings, split into 19 role-category CSVs
(software.csv 294 KB, data-science, hardware, devops, qa, product, …). Columns: company, category,
size, title, level, city, url, updated. Almost every `url` is a Comeet apply link
(`www.comeet.com/jobs/{slug}/...`), so it is a live, deduped Comeet-company source.

## Counts
- 853 Comeet job-URL rows across all categories → **106 unique Comeet company slugs**.
- 2 unique Lever companies.

## Why it matters to HeadStart
Comeet is under-represented elsewhere (OpenPostings had 184). This adds 106 freshly-verified
(daily-updated) Comeet slugs, heavily Israeli tech — good global, non-India coverage. Examples:
aidoc, cellebrite, claroty, solaredge, aquasec, biocatch, atera, ceva, ceragon, 365scores, 8fig.

## Files saved (artifacts/)
- `comeet_slugs.csv` — 106 unique slug,company rows (`{slug}.comeet.com` / `comeet.com/jobs/{slug}`).
- `lever_slugs.csv` — 2 slug,company rows.

## One-line
Daily-updated Israeli tech-jobs dataset; yields 106 unique live Comeet company slugs (best Comeet
coverage this wave).
