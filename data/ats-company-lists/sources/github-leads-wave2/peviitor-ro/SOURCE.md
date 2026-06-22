# peviitor-ro (org sample)

- **URL:** https://github.com/peviitor-ro  (org; ~40+ scraper repos)
- **Repos sampled (2):**
  - https://github.com/peviitor-ro/Scrapy_peviitor_jobs (MIT not declared / none)
  - https://github.com/peviitor-ro/scrapers.js (MIT)
- **Fetched:** 2026-06-23 (shallow clones)
- **One-line:** Per-company scrapers (one file per company) that hardcode the
  company's ATS endpoint — EU/Romania-heavy, including non-derivable Workday tenants.

## What was extracted
Each spider/site file targets one company; company = filename stem, ATS + slug
derived from the embedded endpoint. Only files with a recognizable standard-ATS
endpoint were emitted (the rest use first-party/custom boards).

- `peviitor_scrapy_companies.jsonl` — **15** companies from `Scrapy_peviitor_jobs`
  (40+ spiders; 15 on standard ATS).
- `peviitor_scrapersjs_companies.jsonl` — **16** companies from `scrapers.js`
  (95 site scrapers; 16 on standard ATS).
- **Total: 31 companies.**

## ATS breakdown (31)
| ATS | count | examples |
|---|---|---|
| workday | 15 | airbus(`ag`/Airbus), crowdstrike, maersk, philips, michelin(`michelinhr`), nxp, flex(`flextronics`), kone, valeo, viavi(`viavisolutions`), adient, borgwarner, plexus, dynata, finastra |
| workable | 4 | arkadium(`arkadium-1`), creativechaos, tecknoworks, testronic |
| bamboohr | 3 | digitain(`digitainsoftware`), ding, heimdal(`heimdalsecurity`) |
| greenhouse | 3 | kinaxis, fortisgames, boomi(`boomilp`) |
| smartrecruiters | 2 | metgroup, wns(`WNSGlobalServices144`) |
| ashby | 1 | lilt |
| lever | 1 | jellysmack |
| teamtailor | 1 | signicat |
| personio | 1 | regnology |

- Spot-verified the non-obvious Workday tenants against the raw files (Airbus→`ag`,
  Flex→`flextronics`, Michelin→`michelinhr` all confirmed). Nothing guessed.

## Lead (remaining)
~38 other peviitor-ro scraper repos not pulled (Scrapers_Cristi_Olteanu,
JobsScrapers, scrapers_python_iurie, Advanced_scrapers, PeViitor_Scrapers_Melania,
Scrapy/JMeter variants, per-contributor repos, …). Each likely hardcodes more
EU-company ATS slugs. Representative pull done per brief; full sweep is future work.
