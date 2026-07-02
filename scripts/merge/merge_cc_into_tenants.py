#!/usr/bin/env python3
"""Fold the June 2026 Common Crawl harvest into the merged candidate pool, honestly.

Reads ``data/discover/cc_ats_tenants.csv`` (``ats,tenant,url`` from the rewritten cc_miner) and
folds it into ``data/ats-tenants-merged/{ats}.csv`` (schema ``ats,tenant,url,source``, deduped on
``tenant``). The pool keys on a lowercased slug/label, but the new list stores the tenant the way
each scraper's ``slug_from`` wants it, which differs for a few ATSes — so we canonicalize to a
common per-ATS key *before* comparing, or the net-new count would be pure noise:

  - zoho / personio : pool keys on the subdomain LABEL; the new list has the full host -> key=label
  - workday         : pool tenant is a jobhive-style slug; dedupe on the board URL (host+site)
  - oracle          : pool keys on a company name but the URL column carries the oraclecloud host,
                      which the new list also has -> dedupe on that host
  - everything else : the slug/label already matches the pool key

Existing rows are preserved verbatim (a pool row re-seen in this crawl gets ``+cc2026`` appended to
its source); genuinely new tenants are appended with source ``cc2026``. A provider the pool lacks
(sensehq) gets a brand-new CSV. Leaves ``active/`` untouched. Idempotent. Run from repo root:
    python scripts/merge/merge_cc_into_tenants.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEW = ROOT / "data" / "discover" / "cc_ats_tenants.csv"
MERGED = ROOT / "data" / "ats-tenants-merged"
TAG = "cc2026"

_LABEL = {
    "zoho",
    "personio",
}  # pool keys on the subdomain label, new list has the full host


def norm_url(u: str) -> str:
    """Host + path, no scheme, no trailing slash, lowercased (Workday board identity)."""
    return (u or "").strip().lower().split("://", 1)[-1].rstrip("/")


def norm_host(u: str) -> str:
    """Just the host, no scheme/path (Oracle tenant identity)."""
    return (u or "").strip().lower().split("://", 1)[-1].split("/", 1)[0]


def pool_key(ats: str, tenant: str, url: str) -> str:
    if ats == "workday":
        return norm_url(url)
    if ats == "oracle":
        return norm_host(url)
    return tenant.strip().lower()


def new_key(ats: str, tenant: str, url: str) -> str:
    if ats in _LABEL:
        return tenant.strip().lower().split(".")[0]
    if ats == "workday":
        return norm_url(url)
    if ats == "oracle":
        return norm_host(tenant)
    return tenant.strip().lower()


def store_tenant(ats: str, tenant: str) -> str:
    """What to write in the pool's tenant column for a newly added row."""
    if ats in _LABEL:
        return tenant.strip().lower().split(".")[0]  # match the pool's label convention
    return tenant.strip()


def load_new() -> dict[str, dict[str, tuple[str, str]]]:
    """ats -> {canonical_key: (tenant, url)}; first capture per key wins."""
    out: dict[str, dict[str, tuple[str, str]]] = {}
    with NEW.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ats, tenant, url = r["ats"], r["tenant"], r.get("url", "")
            out.setdefault(ats, {}).setdefault(new_key(ats, tenant, url), (tenant, url))
    return out


def main() -> int:
    if not NEW.exists():
        raise SystemExit(f"no new list at {NEW}")
    new = load_new()

    print(f"{'ATS':<18}{'existing':>9}{'+new':>7}{'total':>8}  note")
    added_total = new_files = 0
    for ats in sorted(new):
        path = MERGED / f"{ats}.csv"
        is_new = not path.exists()
        rows: list[list[str]] = []  # [tenant, url, source]
        if not is_new:
            with path.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    rows.append(
                        [r.get("tenant", ""), r.get("url", ""), r.get("source", "")]
                    )
        existing_keys = {pool_key(ats, t, u) for t, u, _ in rows}

        # re-tag pool rows this crawl re-confirmed (safe in place — never drops a row)
        seen = new[ats]
        for row in rows:
            if pool_key(ats, row[0], row[1]) in seen and TAG not in row[2].split("+"):
                row[2] = f"{row[2]}+{TAG}" if row[2] else TAG

        added = 0
        for key, (tenant, url) in seen.items():
            if key not in existing_keys:
                rows.append([store_tenant(ats, tenant), url, TAG])
                added += 1

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ats", "tenant", "url", "source"])
            for tenant, url, source in sorted(rows):
                w.writerow([ats, tenant, url, source])

        note = (
            "NEW file"
            if is_new
            else ("reconciled" if ats in _LABEL | {"workday", "oracle"} else "")
        )
        print(f"{ats:<18}{len(rows) - added:>9}{added:>7}{len(rows):>8}  {note}")
        added_total += added
        new_files += is_new

    print(
        f"\n+{added_total} tenants new to the pool across {len(new)} ATSes ({new_files} new CSVs). active/ untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
