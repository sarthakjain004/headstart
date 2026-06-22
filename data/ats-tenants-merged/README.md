# Merged ATS tenant lists (Common Crawl ∪ Wayback)

One CSV per ATS provider — the deduplicated **union** of every tenant found by the Common
Crawl miner and the Wayback harvester. Built by
[`scripts/merge/merge_tenants.py`](../../scripts/merge/merge_tenants.py).

Columns: `ats,tenant,url,source`
- `tenant` — the board slug / subdomain label (lowercased), used as the dedupe key
- `url` — a canonical board URL
- `source` — `cc` | `wayback` | `both`: which harvester(s) found this tenant

**Why union:** neither source is complete and they have different blind spots, so together
they cover more. Example — zoho: 3,307 from CC + 5,262 from Wayback, only 2,181 shared →
**6,388** combined.

Inputs:
- CC India tier → `data/discover/india_ats_tenants.csv`
- CC global four → `data/ats-companies/` (greenhouse, lever, ashby, workday)
- Wayback → `data/wayback-ats/`

These are **candidate-grade** (historical + crawl noise); run
[`scripts/validate/check_liveness.py`](../../scripts/validate/check_liveness.py) to filter to live boards. Note:
`workday` shows 0 overlap because CC (jobhive slugs) and Wayback (subdomain labels) use
different tenant identifiers for it, so its union is just the sum.

Rebuild: `python scripts/merge/merge_tenants.py`
