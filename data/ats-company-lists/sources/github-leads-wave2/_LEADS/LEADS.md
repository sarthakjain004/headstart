# Dead-ends / LEADS (no committed data retrieved)

Date: 2026-06-23

## 1. dtunkelang/bag-of-documents — LEAD (generators only)
- **URL:** https://github.com/dtunkelang/bag-of-documents (MIT)
- **Reason:** Has `download/fetch_{breezy,recruitee,smartrecruiters,workable}.py` +
  `build_slug_text_bag.py`, but **no committed slug list**. The builders read from
  `unified_jobs/metadata.jsonl` / `unified_jobs/slug_text_bag.csv`, both **gitignored**.
  Committed JSON under `evaluation/results/` is IR benchmark output (retrieval metrics),
  not company slugs.
- **To harvest:** would require running the fetch_*.py crawlers (Breezy/Recruitee/
  SmartRecruiters/Workable) — explicitly out of scope (heavy crawl). Leave to a run that
  executes the generators.

## 2. jakemercure28/job-search-automation — LEAD (empty template)
- **URL:** https://github.com/jakemercure28/job-search-automation (Go backend + TS scraper-service)
- **Reason:** Brief named `config.ts`; repo is Go. The actual company config files
  `data.example/companies.json` and `internal/dashboard/templates/companies.json` are
  **empty schema stubs** — all arrays `[]`. No seed data committed.
- **Useful byproduct (schema only):** confirms the ATS it supports:
  `GREENHOUSE_COMPANIES, LEVER_COMPANIES, WORKABLE_COMPANIES, ASHBY_COMPANIES,
  WORKDAY_COMPANIES, WELLFOUND_ROLES, RIPPLING_COMPANIES`. The Go `internal/ats/`
  package (canonicalize/extract/parse/resolve) is fingerprinter logic, not data.

## 3. peviitor-ro org — partial (representative pull done)
- ~38 of ~40 scraper repos not pulled. See `peviitor-ro/SOURCE.md` for the list.
  Each likely hardcodes more EU-company ATS slugs. Future full sweep.
