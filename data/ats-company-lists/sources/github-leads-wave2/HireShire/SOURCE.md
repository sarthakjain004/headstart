# HireShire

- **URL:** https://github.com/slowloris-98/HireShire
- **Author / repo:** slowloris-98/HireShire
- **License:** none declared (no LICENSE file) — treat as all-rights-reserved; data
  are public ATS slugs.
- **Fetched:** 2026-06-23 (via `gh api .../contents/config/*.json`)
- **One-line:** Largest single haul — three per-ATS slug lists totalling **15,533 slugs**.

> NB: the brief named `config.py`; the actual committed data are the three
> `config/*_companies.json` slug lists. `config.py` is just loader code.

## What was extracted (verbatim JSON arrays of slugs)
| File | ATS | slug count |
|---|---|---|
| `greenhouse_companies.json` | greenhouse | 8,180 |
| `lever_companies.json` | lever | 4,368 |
| `ashby_companies.json` | ashby | 2,985 |

- Plain JSON string arrays of board slugs, e.g. ashby `1password`, `abridge`, `acorns`;
  lever `15five`, `1password`, `360learning`; greenhouse `10xgenomics`, `1stdibscom`.
- The greenhouse list contains some numeric/junk-looking IDs (e.g. `103644278`,
  `1456754456yhgbhfg`) mixed in with real slugs — light cleaning recommended before
  use, but kept verbatim as retrieved.
