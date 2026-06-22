# Source: remoteintech/remote-jobs

- **Source URL:** https://github.com/remoteintech/remote-jobs (powers https://remoteintech.company)
- **Files pulled:** full repo tarball `https://codeload.github.com/remoteintech/remote-jobs/tar.gz/refs/heads/main`; parsed all **878** company markdown files under `src/companies/*.md`.
- **Author/repo:** remoteintech (community-maintained). **40,494 stars.**
- **ATS provider(s) covered:** Greenhouse, Lever, Ashby (most companies link to their own career page; only a minority link directly to an ATS host, which is what is extracted here).
- **Approx entry count (this lane, where careers_url points at an ATS host):**
  - Greenhouse: **9**
  - Lever: **15**
  - Ashby: **4**
  - (28 total greenhouse+lever+ashby)
- **How accessed:** downloaded the repo tarball via codeload, extracted, then regex-scanned each `src/companies/*.md` front-matter `careers_url` (and body) for `(?:boards|job-boards).greenhouse.io/{slug}`, `jobs.lever.co/{slug}`, `jobs.ashbyhq.com/{slug}`. URL-decoded, lowercased, paired with the file's `title`.
- **Date accessed:** 2026-06-23
- **License:** NOASSERTION (no standard SPDX license detected by GitHub; community directory — verify before redistribution).
- **Description:** A large, well-known community directory of remote-friendly tech companies, one markdown file per company with structured front-matter (`title`, `website`, `careers_url`, `region`, `technologies`, etc.). VALUE for this lane is small in count but high in quality: the few ATS-hosted ones include **non-derivable slugs** the name→slug heuristic would miss, e.g. Zup → `greenhouse:zupinnovation`, Appen → `lever:appen-2`, Theorem → `lever:theoremonellc`.
- **Caveat:** Most of the 878 companies link to a custom careers domain (e.g. `careers.hotjar.com`), not directly to greenhouse/lever/ashby — so this repo is better as a company-name directory to feed a careers-page embed scan than as a direct slug source.

## Files saved (CSV: `slug,company_name`)
- `greenhouse_slugs.csv` — 9 rows
- `lever_slugs.csv` — 15 rows
- `ashby_slugs.csv` — 4 rows
