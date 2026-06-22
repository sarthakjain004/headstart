# fantastic-jobs/7-million-jobs

- **Dataset URL:** https://huggingface.co/datasets/fantastic-jobs/7-million-jobs
- **Platform:** Hugging Face Datasets
- **Publisher:** Fantastic Jobs (https://fantastic.jobs/) — a paid job-postings API vendor
- **ATS provider(s):** None tagged in the free dataset. (Vendor's *paid* pipeline ingests
  200,000+ company career sites across 54 ATS platforms incl. Workday, Greenhouse, Lever,
  BambooHR, SAP SuccessFactors, Oracle Recruiting — but the free HF release does NOT expose
  the source ATS or any source URL/domain.)
- **Approx row count:** 6,986,143 rows (~739 MB; auto-converted to Parquet; also CSV)
- **Access:** web (publicly downloadable, not gated)
- **Date checked:** 2026-06-23
- **License:** WTFPL
- **Gated:** No (free download), but see below.

## One-line description
~7M job postings with only id / title / organization / location — no ATS source field.

## Verified schema (via datasets-server first-rows API)
Exactly 4 columns:
- `id` (int64)
- `title` (string)
- `organization` (string)
- `matched_locations` (string; a brace-wrapped set of "City, Region, Country")

## Sample rows (actually retrieved)
```
id=703103653  title="Senior Data Engineer"  organization="DXC Technology"  matched_locations="{\"Mechelen, Flanders, Belgium\"}"
id=703103695  title="Lead Solution Architect – Data&AI (f/m/d)"  organization="DXC Technology"  matched_locations="{\"Eschborn, Hesse, Germany\",\"Hamburg, Hamburg, Germany\",...}"
```

## Verdict for HeadStart
NOT directly useful for ATS enumeration: no source-ATS column, no career-page URL. The
`organization` column is just a flat company-name list (would still need name->ats:slug
resolution, with all the brand/legal-variant problems). The ATS-tagged version is the
vendor's paid API => recorded as a LEAD in ../leads-paid-jobpost-apis/SOURCE.md.
