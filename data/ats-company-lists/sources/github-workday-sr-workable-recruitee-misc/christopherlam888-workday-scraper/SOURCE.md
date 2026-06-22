# christopherlam888/workday-scraper — configs/*.txt (WORKDAY)

- **Source URL:** https://github.com/christopherlam888/workday-scraper (dir: configs/)
- **Author/repo:** christopherlam888
- **ATS provider:** Workday
- **Approx count:** 12 unique companies across 8 config files (42 total lines w/ dupes).
  Companies: Nvidia, Intel, Marvell, Microchip, NXP, Analog Devices, Cadence, Ciena,
  RBC, CIBC, BMO, Manulife.
- **Access method:** raw.githubusercontent.com (curl)
- **Date retrieved:** 2026-06-23
- **License:** see repo LICENSE
- **Description:** Per-user scraper configs, each line "CompanyName,<full myworkdayjobs URL
  with faceted query params>". Semiconductor + Canadian-bank focused. Value is the full
  worker/location/jobFamily facet query strings (working examples of Workday faceted URLs).

## Files
- `configs/{ahmed,alex,brennan,chris,harris,jeff,seamus,umair}.txt` — CompanyName,URL per line
