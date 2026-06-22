# Ches-ctrl/Cheddar — company_urls.csv + job_posting_urls.csv (direct ATS-host URL lists)

- **Source URL:** https://github.com/Ches-ctrl/Cheddar (dir: storage/new/, branch main)
- **Raw:** https://raw.githubusercontent.com/Ches-ctrl/Cheddar/main/storage/new/company_urls.csv
- **Author/repo:** Ches-ctrl
- **ATS providers (company_urls.csv host distribution):** greenhouse 571, lever 332, bamboohr 107,
  workable 101, ashby 93, applytojob(jazzhr) 68, myworkdayjobs 68, breezy 65, recruitee 37,
  jobvite 23, smartrecruiters 16, teamtailor 7, workday(other) 2, ukg 1.
- **Approx count:** company_urls.csv = 1,529 ATS careers URLs (one per line). job_posting_urls.csv
  = 175 individual job-posting URLs (same ATS hosts). THIS LANE: workday 68+2, smartrecruiters 16,
  workable 101, recruitee 37; secondary: bamboohr 107, teamtailor 7, breezy 65, jazzhr 68.
- **Access method:** raw.githubusercontent.com (curl)
- **Date retrieved:** 2026-06-23
- **License:** see repo
- **Schema:** single-column CSV (header `company_url`). Each row is a careers root on a known ATS
  host; slug = subdomain or path segment.
- **Description:** High direct-yield: nearly every row is already on a recognized ATS host
  (greenhouse/lever/workable/bamboohr/ashby/workday/recruitee/breezy), so company->ATS+slug is
  directly parseable without an embed scan. job_posting_urls.csv adds deep job links on the same hosts.

## Files
- `company_urls.csv` — 1,529 ATS careers URLs (header company_url)
- `job_posting_urls.csv` — 175 individual job-posting URLs on ATS hosts
