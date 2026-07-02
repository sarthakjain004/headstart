#!/usr/bin/env python3
"""One-time migration: seed the liveness ledger (ADR-0012) from the existing active/ files.

The old checker wrote verdicts to data/ats-tenants-merged/active/{ats}.csv (live boards + job
count) and active/.{ats}_dead (a dotfile of dead tenants). This folds both into the new ledger at
data/validate/liveness/{ats}.csv (ats,tenant,url,status,jobs,checked_at) so the tens of thousands
of already-computed verdicts carry over instead of being re-probed from scratch:

  - live rows  -> status=live with their url + jobs
  - dead rows  -> status=dead, url backfilled from the merged pool (the dotfile has only the tenant)
  - checked_at -> the source file's modified date, so dead stays cached under DEAD_TTL and live
                  falls due for a refresh under LIVE_TTL

Skips an ATS whose ledger already exists (idempotent); pass --force to overwrite. Run from repo root:
    python scripts/validate/seed_liveness_ledger.py
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from headstart import liveness  # noqa: E402 - needs src on sys.path first

POOL = ROOT / "data" / "ats-tenants-merged"
ACTIVE = POOL / "active"


def _mtime_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def main() -> int:
    force = "--force" in sys.argv[1:]
    ledger_dir = liveness.dir_for(ROOT)
    ledger_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'ATS':<18}{'live':>7}{'dead':>7}{'total':>8}")
    for active_csv in sorted(ACTIVE.glob("*.csv")):
        ats = active_csv.stem
        if ats == "unresolved":
            continue  # the old combined unknown-set file, not an ATS
        out = ledger_dir / f"{ats}.csv"
        if out.exists() and not force:
            print(f"{ats:<18}{'(exists — skip; --force to overwrite)':>30}")
            continue

        live_date = _mtime_date(active_csv)
        verdicts: dict[str, liveness.Verdict] = {}
        with active_csv.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                jobs = (r.get("jobs") or "").strip()
                verdicts[r["tenant"]] = liveness.Verdict(
                    ats=ats,
                    tenant=r["tenant"],
                    url=r.get("url", ""),
                    status=liveness.LIVE,
                    jobs=int(jobs) if jobs else None,
                    checked_at=live_date,
                )

        dead_file = ACTIVE / f".{ats}_dead"
        if dead_file.exists():
            pool_url = {}
            pool_csv = POOL / f"{ats}.csv"
            if pool_csv.exists():
                with pool_csv.open(encoding="utf-8") as f:
                    pool_url = {
                        r["tenant"]: r.get("url", "") for r in csv.DictReader(f)
                    }
            dead_date = _mtime_date(dead_file)
            for tenant in dead_file.read_text(encoding="utf-8").split("\n"):
                tenant = tenant.strip()
                if not tenant or tenant in verdicts:
                    continue
                verdicts[tenant] = liveness.Verdict(
                    ats=ats,
                    tenant=tenant,
                    url=pool_url.get(tenant, ""),
                    status=liveness.DEAD,
                    jobs=None,
                    checked_at=dead_date,
                )

        liveness.write(out, verdicts.values())
        live = sum(1 for v in verdicts.values() if v.status == liveness.LIVE)
        dead = sum(1 for v in verdicts.values() if v.status == liveness.DEAD)
        print(f"{ats:<18}{live:>7}{dead:>7}{len(verdicts):>8}")

    print(
        f"\nseeded ledger at {ledger_dir.relative_to(ROOT)} (as of source-file dates)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
