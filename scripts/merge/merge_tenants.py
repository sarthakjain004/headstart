#!/usr/bin/env python3
"""Rebuild the merged ATS tenant lists from Common Crawl ∪ Wayback alone. **Dry-run by default.**

Sources:
  - CC, India tier:  data/discover/india_ats_tenants.csv     (ats,host)       tenant = host's first label
  - CC, global four: data/ats-companies/{ats}.csv    (name,slug,url)  tenant = slug   [greenhouse,lever,ashby,workday]
  - Wayback:         data/wayback-ats/{ats}.csv       (ats,tenant,url)

Output: data/ats-tenants-merged/{ats}.csv  with columns  ats,tenant,url,source
where source is cc | wayback | both.

**This is a destructive from-scratch rebuild, not an update.** It opens each file with ``"w"`` and
writes only what the two sources above contain, so every row the pool holds from anywhere else —
``harvest``, ``cc2026``, ``fingerprint``, ``dns``, ``careers-scan``, ``wayback2026`` — is erased,
and its ``ATSES`` list covers 13 of the 19 ATSes that now have harvests. Measured 2026-08-14 it
would have dropped 26,824 rows to gain 20,926, a net loss of ~5,900, with workday alone going
24,275 -> 7,539.

So it refuses to write unless you pass ``--force``. Without it, it prints exactly what each file
would lose and exits non-zero. To *add* a source's findings to the pool, use the additive fold for
that source instead (``merge_wayback_into_tenants.py``, ``merge_cc_into_tenants.py``,
``merge_harvest_into_tenants.py``, ``merge_fingerprint_into_tenants.py``) — those never drop a row.

Run:  python scripts/merge/merge_tenants.py            # dry run: report what a rebuild would cost
      python scripts/merge/merge_tenants.py --force    # actually rebuild, discarding the rest
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
CC_INDIA = DATA / "discover" / "india_ats_tenants.csv"
CC_GLOBAL_DIR = DATA / "ats-companies"
WB_DIR = DATA / "wayback-ats"
OUT = DATA / "ats-tenants-merged"

GLOBAL = {"greenhouse", "lever", "ashby", "workday"}
# dropped freshteam (Freshworks sunset, renewals end 2026-03), greythr (HR/payroll login
# portals, not public job boards), jobsoid (active but non-tech SMB tenants; tech cos migrated
# off), and peoplestrong (candidate portals are login-walled .jsf; can't read public jobs) —
# all useless for tech-role coverage.
ATSES = [
    "zoho",
    "darwinbox",
    "keka",
    "ripplehire",
    "turbohire",
    "qandle",
    "beehive",
    "workable",
    "recruitee",
    "greenhouse",
    "lever",
    "ashby",
    "workday",
]


def load_cc():
    cc = defaultdict(dict)  # ats -> {tenant: url}
    if CC_INDIA.exists():
        with CC_INDIA.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ats = (row.get("ats") or "").strip().lower()
                host = (row.get("host") or "").strip().lower()
                if ats and host:
                    cc[ats].setdefault(host.split(".")[0], f"https://{host}")
    for ats in GLOBAL:
        p = CC_GLOBAL_DIR / f"{ats}.csv"
        if p.exists():
            with p.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    t = (row.get("slug") or "").strip().lower()
                    if t:
                        cc[ats].setdefault(t, (row.get("url") or "").strip())
    return cc


def load_wb():
    wb = defaultdict(dict)
    for ats in ATSES:
        p = WB_DIR / f"{ats}.csv"
        if p.exists():
            with p.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    t = (row.get("tenant") or "").strip().lower()
                    if t:
                        wb[ats].setdefault(t, (row.get("url") or "").strip())
    return wb


def existing_tenants(ats):
    """What the pool holds for one ATS today: ``tenant -> source``."""
    path = OUT / f"{ats}.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {
            (r.get("tenant") or "").strip().lower(): (r.get("source") or "")
            for r in csv.DictReader(f)
            if (r.get("tenant") or "").strip()
        }


def main():
    force = "--force" in sys.argv[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    cc, wb = load_cc(), load_wb()

    print(f"{'ATS':<13}{'cc':>7}{'wayback':>9}{'both':>7}{'union':>8}{'LOST':>7}")
    grand = lost_total = 0
    losses = {}
    for ats in ATSES:
        c, w = cc.get(ats, {}), wb.get(ats, {})
        tenants = sorted(set(c) | set(w))
        both = len(set(c) & set(w))
        lost = set(existing_tenants(ats)) - set(tenants)
        if lost:
            losses[ats] = lost
        if force:
            with (OUT / f"{ats}.csv").open("w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                wr.writerow(["ats", "tenant", "url", "source"])
                for t in tenants:
                    in_c, in_w = t in c, t in w
                    src = "both" if in_c and in_w else ("cc" if in_c else "wayback")
                    wr.writerow([ats, t, w.get(t) or c.get(t), src])
        print(
            f"{ats:<13}{len(c):>7}{len(w):>9}{both:>7}{len(tenants):>8}{len(lost):>7}",
            flush=True,
        )
        grand += len(tenants)
        lost_total += len(lost)
    print(f"{'TOTAL':<13}{'':>7}{'':>9}{'':>7}{grand:>8}{lost_total:>7}")

    skipped = sorted({p.stem for p in WB_DIR.glob("*.csv")} - set(ATSES))
    if skipped:
        print(
            f"\nATSes this script does not know about, left untouched: {', '.join(skipped)}"
        )

    if force:
        print(
            f"\nrebuilt {len(ATSES)} ATSes from cc ∪ wayback; {lost_total} rows discarded."
        )
        return 0

    # Dry run. Refuse rather than silently do the destructive thing.
    if lost_total:
        print(
            f"\nREFUSING: a rebuild would discard {lost_total} rows the pool holds today."
        )
        by_source = defaultdict(int)
        for ats, tenants in losses.items():
            pool = existing_tenants(ats)
            for t in tenants:
                by_source[pool[t] or "(blank)"] += 1
        top = sorted(by_source.items(), key=lambda kv: -kv[1])[:6]
        print("  they came from: " + ", ".join(f"{s}={n}" for s, n in top))
        print(
            "\nTo ADD a source's findings without losing anything, use its additive fold instead:\n"
            "  merge_wayback_into_tenants.py · merge_cc_into_tenants.py ·\n"
            "  merge_harvest_into_tenants.py · merge_fingerprint_into_tenants.py\n"
            "Pass --force only if you really mean to rebuild from scratch and discard the rest."
        )
        return 1
    print("\nnothing would be lost; pass --force to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
