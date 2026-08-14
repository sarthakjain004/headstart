# Merged ATS tenant lists (Common Crawl ∪ Wayback)

One CSV per ATS — the deduplicated **union** of every tenant found by the Common Crawl miner and
the Wayback harvester, plus the careers-page harvest and the fingerprinter.

Columns: `ats,tenant,url,source`

- `tenant` — the board slug / subdomain label (lowercased), used as the dedupe key. **Workday is
  the exception**: its rows carry a display slug, so the board URL (host + site) is the identity
  the merge scripts dedupe on.
- `url` — a canonical board URL
- `source` — which harvester(s) found the tenant, `+`-joined when several agree (e.g. `cc+harvest`,
  `wayback+harvest+cc2026`): Common Crawl (`cc` / `cc2026`), Wayback (`wayback` / `wayback2026`),
  the careers-page harvest (`harvest`), and the fingerprinter (`fingerprint`)

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

## Folding in a new harvest

Each source has its own **additive** fold script. They re-tag a row the source re-confirms, append
a row the pool lacks, and never drop a row or overwrite a URL — so they are safe to re-run and safe
to run in any order:

| source | script |
| --- | --- |
| Wayback | `python scripts/merge/merge_wayback_into_tenants.py` |
| Common Crawl (2026) | `python scripts/merge/merge_cc_into_tenants.py` |
| careers-page harvest | `python scripts/merge/merge_harvest_into_tenants.py` |
| fingerprinter | `python scripts/merge/merge_fingerprint_into_tenants.py` |

> **Do not run `scripts/merge/merge_tenants.py` to refresh this directory.** It is the original
> builder and it *rebuilds* each file from Common Crawl ∪ Wayback alone, opening it with `"w"` — so
> every row sourced `harvest`, `cc2026` or `fingerprint` is erased. Measured 2026-08-14: a rebuild
> would drop **26,824 rows to gain 20,926**, and its hardcoded ATS list covers 13 of the 19 that now
> have harvests, silently ignoring eightfold, freshteam, personio, rippling, smartrecruiters,
> successfactors, teamtailor and trakstar. It is kept only for a from-scratch rebuild of those two
> original sources.
