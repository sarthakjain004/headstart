# lukebarousse/data_jobs

- **Dataset URL:** https://huggingface.co/datasets/lukebarousse/data_jobs
- **Platform:** Hugging Face Datasets
- **Publisher:** Luke Barousse (data-analytics educator)
- **ATS provider(s):** None. `job_via` holds the posting *board* (e.g. "via LinkedIn",
  "via ZipRecruiter"), NOT the backend ATS, and there is no career-page URL/domain column.
- **Approx row count:** 786,000 rows (CSV + Parquet; 100K-1M size bucket)
- **Access:** web (public)
- **Date checked:** 2026-06-23
- **License:** Apache-2.0
- **Gated:** No

## One-line description
786K data-related job postings (titles, locations, skills, salary) scraped via SerpAPI/Google
Jobs — board-of-origin only, no source-ATS tagging.

## Verified schema
`job_title_short`, `job_title`, `job_location`, `job_via`, `job_schedule_type`,
`job_work_from_home`, `search_location`, `job_posted_date`, `job_no_degree_mention`,
`job_health_insurance`, `job_country`, `salary_rate`, `salary_year_avg`, `salary_hour_avg`,
`company_name`, `job_skills`, `job_type_skills`.

## Verdict for HeadStart
NOT useful for ATS enumeration. No ATS field, no career-board URL; `company_name` is a flat
list and the corpus is data/analytics-roles only (not general SWE/tech).
