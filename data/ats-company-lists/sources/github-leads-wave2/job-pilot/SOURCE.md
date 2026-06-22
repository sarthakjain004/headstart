# job-pilot

- **URL:** https://github.com/SampreethAvvari/job-pilot
- **Author / repo:** SampreethAvvari/job-pilot
- **License:** MIT
- **Fetched:** 2026-06-23 (via `gh api .../contents/scripts/seed_companies.py`)
- **One-line:** Seed watchlist of ~120 companies; ~18 carry explicit ATS URLs
  (incl. the non-derivable quant/Workday/SmartRecruiters slugs).

## What was extracted
Saved `seed_companies.py` verbatim (181 lines). It is a Python list of
`(company_name, ats_url_or_"")` tuples. Most entries are name-only (slug derivable
from a clean Greenhouse/Lever/Ashby name); the load-bearing rows are the ones with
explicit URLs:

- **Greenhouse, non-derivable slugs:** Two Sigma→`twosigma`, Hudson River Trading→
  `wehrtyou`, Jump Trading→`jumptrading`, DRW→`drweng`, IMC→`imc`, Optiver→`optiverus`.
- **Workday (full path):** Nvidia `nvidia.wd5/NVIDIAExternalCareerSite`, Salesforce
  `salesforce.wd12/External_Career_Site`, Adobe `adobe.wd5/external_experienced`,
  Intel `intel.wd1/External`, AMD `amd.wd1/External`, Qualcomm `qualcomm.wd5/External`,
  Zoom `zoom.wd5/Zoom`, Workday `workday.wd5/Workday`, CrowdStrike
  `crowdstrike.wd5/crowdstrikecareers`, Dell `dell.wd1/External`.
- **SmartRecruiters:** ServiceNow, Palo Alto Networks (`careers.smartrecruiters.com/{slug}`).

Kept as the source `.py` (a tuple list, not a clean data file) so the exact mappings
are preserved without re-derivation.
