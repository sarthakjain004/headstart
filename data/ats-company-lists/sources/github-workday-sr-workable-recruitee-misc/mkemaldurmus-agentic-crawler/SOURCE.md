# mkemaldurmus/agentic-crawler — config/sources/*.yaml (MULTI-ATS company->ATS map)

- **Source URL:** https://github.com/mkemaldurmus/agentic-crawler (dir: config/sources/)
- **Author/repo:** mkemaldurmus
- **ATS providers:** Greenhouse, Lever, Ashby (+ 19 "custom" aggregator sources excluded: indeed, linkedin, hn, remoteok, etc.)
- **Approx count:** 69 source YAMLs carry ats_platform; 50 are real company ATS boards
  (greenhouse 35, lever 9, ashby 6). Consolidated to ats_company_map.tsv.
- **Access method:** gh api (tree listing) + raw.githubusercontent.com per file (curl), fields parsed
- **Date retrieved:** 2026-06-23
- **License:** see repo
- **Description:** Each per-company YAML declares ats_platform + ats_slug + endpoint
  (e.g. anthropic -> greenhouse:anthropic, netflix_lever -> lever:netflix,
  figma -> ashby:figma). Some companies appear twice with alternate ATS (notion greenhouse
  AND notion_ashby; ramp/vercel similarly) — useful as ground-truth slug verification.
  Out-of-primary-lane (gh/lever/ashby) but kept as a clean multi-ATS company->slug map.

## Files
- `ats_company_map.tsv` — columns: name, ats_platform, ats_slug, endpoint (69 rows; filter ats_platform!=custom for 50 real boards)
