# Public datasets — ATS company lists / ATS-tagged job postings

Lane: public datasets on NON-GitHub repos (Kaggle, HuggingFace, data.world, Google Dataset
Search, Zenodo, figshare, Socrata). Date: 2026-06-23. Access method: WebSearch + WebFetch.

## Headline finding
There is **no freely-downloadable public dataset that enumerates companies on ATS boards or
tags job postings by their source ATS**. Every dataset on the open repos that I could inspect
exposes at most a flat `company_name` (or `organization`) column and a job *board* of origin
(LinkedIn/ZipRecruiter), never the backend ATS nor the company career-page/ATS URL. The data
that genuinely retains ATS identity / source URL lives exclusively in **paid B2B APIs**
(recorded as LEADS).

## Datasets actually inspected (schema retrieved)
| Dataset | Platform | URL | ATS-tagged? | Rows | Outcome |
|---|---|---|---|---|---|
| fantastic-jobs/7-million-jobs | HuggingFace | https://huggingface.co/datasets/fantastic-jobs/7-million-jobs | No (id/title/org/location only) | 6,986,143 | Saved SOURCE + sample. Marginal: flat company list. |
| lukebarousse/data_jobs | HuggingFace | https://huggingface.co/datasets/lukebarousse/data_jobs | No (`job_via`=board, not ATS) | 786,000 | Saved SOURCE. Not useful. |
| Crisp-Unimib/JobSet | HuggingFace | https://huggingface.co/datasets/Crisp-Unimib/JobSet | No (synthetic LLM job ads + ESCO) | 15,469 | Rejected: synthetic, no source. |
| Cyleux/job-stateapi-sky-all1 | HuggingFace | https://huggingface.co/datasets/Cyleux/job-stateapi-sky-all1 | No (single `text` col, synthetic agent dialogues) | 5,465 | Rejected. |
| fantastic-jobs/linkedin-industry-list | HuggingFace | https://huggingface.co/datasets/fantastic-jobs/linkedin-industry-list | N/A (industry taxonomy) | 7,606 | Rejected: not companies. |

## Best LEADS (paid; gated — see leads-paid-jobpost-apis/SOURCE.md)
1. **TheirStack** — https://theirstack.com/en/jobs-dataset — 210M postings / 12M companies /
   346k sources; filterable BY ATS data source (per-ATS pages e.g. .../data-source/greenhouse);
   returns original source URL. The single best ATS-tagged source found. Paid API.
2. **PredictLeads** — https://predictleads.com/job_listings — ~9.2M active jobs from company
   websites/career pages/ATS integrations; history since 2016. Paid.
3. **Fantastic Jobs API** — https://fantastic.jobs/ — 200k+ career sites across 54 ATS
   platforms; paid API carries source ATS (the free 7M HF dump does not).
4. Revelio Labs COSMOS (reveliolabs.com/job-postings-cosmos), Coresignal (~460M),
   Bright Data (brightdata.com/products/datasets/jobs) — large paid postings sets, career-page
   sourced, free sample on request for Bright Data.

## Dead-ends (do not re-run)
- **Google Dataset Search**: zero results for "job postings ATS greenhouse lever career page".
- **data.world**: Open Data Community is retiring 2026-07-11; job-postings collection page no
  longer browsable without sign-in; the 3 generic "job-postings" sets are not ATS-tagged.
- **Zenodo / figshare / Socrata**: no ATS-tagged or ATS-company-enumeration datasets surfaced;
  hits were academic labor-market papers (Burning Glass, ESCO) and patents, not usable datasets.
- **Kaggle**: many job-posting sets (LinkedIn Job Postings 2023-24 `arshkon`, `asaniczka`
  data-science postings, generic/synthetic "Job Dataset"s, "Tech Job Postings 7-Day Rolling
  Window") — all are board/synthetic data without a source-ATS column; pages are JS/login-gated
  so schemas couldn't be confirmed via WebFetch, but none advertise ATS-source tagging.
- **Apify** results throughout are SCRAPER TOOLS, not datasets — out of lane (and several index
  Greenhouse/Lever/Ashby/Workday; relevant to the tooling lane, not the dataset lane).
- HuggingFace `?search=jobs` full scan: remaining hits are Steve-Jobs fine-tuning sets, generic
  job-description/skills corpora, or NER sets — none ATS-tagged.

## Note on overlap with other lanes
The GitHub repos surfaced incidentally (Feashliaa/job-board-aggregator — 1M+ jobs/20k+ companies
across Greenhouse/Lever/Ashby/Workday; Kayvan-Zahiri/state-of-ats-2026 — 743 Fortune-500
employers + their ATS) are GitHub-hosted and belong to the GitHub lane, not this one.
