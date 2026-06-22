# BOARD

- **URL:** https://github.com/binaryshrey/BOARD
- **Author / repo:** binaryshrey/BOARD
- **License:** none declared (no LICENSE file) — treat as all-rights-reserved; data
  are public ATS slugs.
- **Fetched:** 2026-06-23 (via `gh api .../contents`)
- **One-line:** Per-ATS discovery pipelines (`BOARD/{ASHBYHQ,GREENHOUSE,WORKDAY,...}`);
  most `sources/` are runtime discoverers, but the committed **seed lists** are useful.

## What was extracted (committed seeds only)
| File | ATS | content | count |
|---|---|---|---|
| `greenhouse_seeds.py` → `greenhouse_seed_slugs.json` | greenhouse | known board slugs | 113 |
| `workday_seeds.py` → `workday_seed_tenants.json` | workday | **full `{name, tenant, instance, jobsite}`** | 60 |
| `ashby_customers.py` → `ashby_known_seed_slugs.json` | ashby | `_KNOWN_SEED_SLUGS` set | 49 |

- The **Workday seeds are the standout**: each carries the hard-to-derive
  `tenant` + `wdN instance` + `jobsite` path (e.g. NVIDIA → `nvidia` / `wd5` /
  `NVIDIAExternalCareerSite`; Airbus-style mappings). Derived endpoint added:
  `https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{jobsite}/jobs`.
- `*.py` originals kept alongside the cleaned `*.json`.

## Not saved (lead)
`sources/{apify,commoncrawl,github_search,hn_hiring,urlscan,wayback,google_*}.py` are
*generators* that discover slugs at crawl time — no committed output. Not run
(heavy crawl, and a parallel agent covers code-search).
