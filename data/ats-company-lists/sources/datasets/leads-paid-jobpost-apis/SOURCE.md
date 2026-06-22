# LEADS — Paid job-postings APIs/datasets with ATS or career-page source data

These are NOT freely downloadable datasets. Each is a commercial product whose records are
sourced from company career pages / ATS boards and (per vendor copy) retain the source URL or
ATS identity. Recorded as LEADS with the precise contents/size claimed on their public pages.
Date checked: 2026-06-23. No data was retrieved (gated).

## 1. TheirStack — Jobs Dataset / Job Posting API
- URL: https://theirstack.com/en/jobs-dataset  (per-ATS: .../job-posting-api/data-source/greenhouse)
- 210M postings, 12M companies, 346,000+ sources, 195+ countries, since 2021.
- Filterable BY ATS data source; returns original source URL. Paid API. (See ../theirstack-greenhouse-api/.)
- Best of the leads for ATS-tagged data.

## 2. Fantastic Jobs — AI-enriched Job Posting API
- URL: https://fantastic.jobs/  (free HF teaser: huggingface.co/datasets/fantastic-jobs/7-million-jobs)
- Polls 200,000+ company career sites across 54 ATS platforms (Workday, Greenhouse, Lever,
  BambooHR, SAP SuccessFactors, Oracle Recruiting, ...). 3M+ ATS-site jobs/month + 11M+ board jobs/month.
- The PAID API carries the ATS source; the free 7M HF dump does NOT (id/title/org/location only).

## 3. Revelio Labs — Job Postings COSMOS
- URL: https://www.reveliolabs.com/job-postings-cosmos/
- 5B+ postings from 1M+ company websites + every major board + staffing firms, updated daily. Paid.

## 4. PredictLeads — Job Openings dataset
- URL: https://predictleads.com/job_listings
- Sourced directly from company websites, career pages, and ATS integrations. ~9.2M active jobs
  at any time; history since 2016. Paid (company-intelligence vendor).

## 5. Coresignal — Multi-source Job Postings
- URL: https://coresignal.com/alternative-data/job-postings-data/  (~460.5M records). Paid.

## 6. Bright Data — Jobs Dataset
- URL: https://brightdata.com/products/datasets/jobs  (also on Databricks Marketplace). Tens of
  millions of records worldwide. Paid; free sample on request.

## Notes for the orchestrator
- None of the above gives a clean "company -> ats:slug" enumeration for free.
- TheirStack and PredictLeads are the two that most explicitly retain ATS / career-page source
  per record and are the highest-value paid leads if licensing is on the table.
