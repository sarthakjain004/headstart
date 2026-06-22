# Source: mshen1019/Argus

- **Source URL:** https://github.com/mshen1019/Argus
- **File pulled:** https://raw.githubusercontent.com/mshen1019/Argus/main/config/profiles/default/companies.yaml
- **Author/repo:** mshen1019. Description: "An AI-powered job search agent that crawls career pages and matches jobs based on your preferences."
- **ATS provider(s) covered:** Greenhouse, Lever, Ashby (file also tags `custom`, `amazon`, `google`, `meta`, `tiktok`, `uber`).
- **Approx entry count (this lane):**
  - Greenhouse: **25** companies
  - Ashby: **6** companies
  - Lever: **4** companies
  - (35 total greenhouse+lever+ashby; ~80 companies overall in file)
- **How accessed:** direct `curl` of the raw YAML; saved verbatim.
- **Date accessed:** 2026-06-23
- **License:** None declared.
- **Description:** A hand-maintained, alphabetically-sorted company config where each entry has `name`, `career_url`, and an explicit `ats_type`. Small but clean and well-labeled — mostly big/well-known tech + quant firms (Airbnb, Anthropic, Databricks, Coinbase, Cohere, Confluent, Flexport, Hudson River Trading, etc.). Useful as a high-confidence cross-check set; slugs are derivable directly from the `career_url`. Repo's `fix_ats_config.py` is a script that auto-detects/repairs the ats_type + direct URL per company.

## Files saved
- `companies.yaml` (167 lines, ~80 companies; 35 on greenhouse/lever/ashby)
