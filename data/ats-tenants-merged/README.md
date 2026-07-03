# Merged ATS tenant lists (Common Crawl ∪ Wayback)

One CSV per ATS provider — the deduplicated **union** of every tenant found by the Common
Crawl miner and the Wayback harvester. Built by
[`scripts/merge/merge_tenants.py`](../../scripts/merge/merge_tenants.py).

Columns: `ats,tenant,url,source`
- `tenant` — the board slug / subdomain label (lowercased), used as the dedupe key
- `url` — a canonical board URL
- `source` — which harvester(s) found the tenant, `+`-joined when several agree (e.g. `cc+harvest`, `wayback+harvest+cc2026`): Common Crawl (`cc` / `cc2026`), Wayback (`wayback`), and the careers-page harvest (`harvest`)

**Why union:** neither source is complete and they have different blind spots, so together
they cover more. Example — zoho: 3,307 from CC + 5,262 from Wayback, only 2,181 shared →
**6,388** combined.

Inputs: the Common Crawl harvest (`data/discover/cc_ats_tenants.csv`), the Wayback harvest, and the
careers-page harvest — folded in by the `scripts/merge/` scripts.

These are **candidate-grade** (historical + crawl noise).
[`scripts/validate/check_liveness.py`](../../scripts/validate/check_liveness.py) probes each board and
records a Live/Dead/Unknown verdict in the liveness ledger (`data/validate/liveness/`, ADR-0012) —
the source of truth for the live set. The committed `active/` subfolder here is the older live-list
form the ledger supersedes. (`workday` shows little tenant overlap because CC and Wayback identify
its tenants differently.)

Rebuild: `python scripts/merge/merge_tenants.py`
