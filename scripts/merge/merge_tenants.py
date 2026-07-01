#!/usr/bin/env python3
"""Merge the Common Crawl and Wayback ATS tenant lists into one deduped file per provider.

Sources:
  - CC, India tier:  data/discover/india_ats_tenants.csv     (ats,host)       tenant = host's first label
  - CC, global four: data/ats-companies/{ats}.csv    (name,slug,url)  tenant = slug   [greenhouse,lever,ashby,workday]
  - Wayback:         data/wayback-ats/{ats}.csv       (ats,tenant,url)

Output: data/ats-tenants-merged/{ats}.csv  with columns  ats,tenant,url,source
where source is cc | wayback | both.

Run:  python scripts/merge/merge_tenants.py
"""

import csv
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cc, wb = load_cc(), load_wb()
    print(f"{'ATS':<13}{'cc':>7}{'wayback':>9}{'both':>7}{'union':>8}")
    grand = 0
    for ats in ATSES:
        c, w = cc.get(ats, {}), wb.get(ats, {})
        tenants = sorted(set(c) | set(w))
        both = len(set(c) & set(w))
        with (OUT / f"{ats}.csv").open("w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["ats", "tenant", "url", "source"])
            for t in tenants:
                in_c, in_w = t in c, t in w
                src = "both" if in_c and in_w else ("cc" if in_c else "wayback")
                wr.writerow([ats, t, w.get(t) or c.get(t), src])
        print(f"{ats:<13}{len(c):>7}{len(w):>9}{both:>7}{len(tenants):>8}")
        grand += len(tenants)
    print(f"{'TOTAL':<13}{'':>7}{'':>9}{'':>7}{grand:>8}")


if __name__ == "__main__":
    main()
