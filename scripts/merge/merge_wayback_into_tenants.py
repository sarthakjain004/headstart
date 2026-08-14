#!/usr/bin/env python3
"""Fold the Wayback ATS harvest into the merged candidate pool, additively.

Reads ``data/wayback-ats/{ats}.csv`` (``ats,tenant,url``, written by
``scripts/discover/wayback_feeder.py``) and folds it into
``data/ats-tenants-merged/{ats}.csv`` (schema ``ats,tenant,url,source``), the candidate pool
``scripts/validate/check_liveness.py`` probes.

**Additive, never destructive.** A pool row this harvest re-confirms gets ``+wayback2026``
appended to its source; a tenant the pool lacks is appended with source ``wayback2026``. Nothing
is dropped, no URL is overwritten. This is deliberately *not* what ``merge_tenants.py`` does —
that one rebuilds each file from cc ∪ wayback and so erases every row sourced ``harvest`` /
``cc2026`` / ``fingerprint`` (measured 2026-08-14: a rebuild would lose 26,824 rows to gain
20,926, and ignore the 8 ATSes missing from its ``ATSES`` list).

Identity is per-ATS, because the pool and the harvest key tenants differently for Workday: the
harvest emits ``{company}/{site}`` while the pool carries a display slug, so the two are deduped
on the board URL (host+site) instead. Every other ATS already agrees on the slug/label — verified
2026-08-14, 17 of 18 with ≥90% key overlap. Same reconciliation as
``merge_cc_into_tenants.py``.

Leaves ``active/`` untouched. Idempotent. Run from the repo root:
    python scripts/merge/merge_wayback_into_tenants.py
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WAYBACK = ROOT / "data" / "wayback-ats"
MERGED = ROOT / "data" / "ats-tenants-merged"
TAG = "wayback2026"


def board_url(u: str) -> str:
    """Host + path, no scheme, no trailing slash, lowercased — Workday's board identity."""
    return (u or "").strip().lower().split("://", 1)[-1].rstrip("/")


def pool_key(ats: str, tenant: str, url: str) -> str:
    """What identifies one Board in the pool. Workday goes by board URL, the rest by Slug.

    Empty when a Workday row carries no URL — such a row has no identity here, and callers must
    skip it rather than let every url-less row collide on ``""``. The pool holds 398 of them.
    """
    if ats == "workday":
        return board_url(url)
    return tenant.strip().lower()


def load_harvest(path: Path) -> dict[str, tuple[str, str]]:
    """``pool_key -> (tenant, url)`` for one ATS; first capture per key wins."""
    out: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tenant, url = (r.get("tenant") or "").strip(), (r.get("url") or "").strip()
            k = pool_key(path.stem, tenant, url)
            if tenant and k:
                out.setdefault(k, (tenant, url))
    return out


def main() -> int:
    if not WAYBACK.is_dir():
        raise SystemExit(f"no wayback harvest at {WAYBACK}")
    MERGED.mkdir(parents=True, exist_ok=True)

    print(
        f"{'ATS':<18}{'existing':>9}{'re-tagged':>10}{'+new':>7}{'total':>8}  note",
        flush=True,
    )
    added_total = retagged_total = new_files = 0
    for src in sorted(WAYBACK.glob("*.csv")):
        ats = src.stem
        harvest = load_harvest(src)
        if not harvest:  # header-only file (e.g. turbohire) — leave the pool alone
            continue

        path = MERGED / f"{ats}.csv"
        is_new = not path.exists()
        rows: list[list[str]] = []  # [tenant, url, source]
        if not is_new:
            with path.open(encoding="utf-8") as f:
                rows = [
                    [r.get("tenant", ""), r.get("url", ""), r.get("source", "")]
                    for r in csv.DictReader(f)
                ]
        existing = {k for t, u, _ in rows if (k := pool_key(ats, t, u))}

        retagged = 0
        for row in rows:
            # A url-less Workday row has no key, so it can never be "confirmed" by the harvest.
            # Without the falsy guard all 398 of them would match a single url-less harvest row
            # and take a `+wayback2026` tag this harvest never earned.
            k = pool_key(ats, row[0], row[1])
            if k and k in harvest and TAG not in row[2].split("+"):
                row[2] = f"{row[2]}+{TAG}" if row[2] else TAG
                retagged += 1

        added = 0
        for k, (tenant, url) in harvest.items():
            if k not in existing:
                rows.append([tenant, url, TAG])
                added += 1

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ats", "tenant", "url", "source"])
            for tenant, url, source in sorted(rows):
                w.writerow([ats, tenant, url, source])

        note = (
            "NEW file"
            if is_new
            else ("deduped on board url" if ats == "workday" else "")
        )
        print(
            f"{ats:<18}{len(rows) - added:>9}{retagged:>10}{added:>7}{len(rows):>8}  {note}",
            flush=True,
        )
        added_total += added
        retagged_total += retagged
        new_files += is_new

    print(
        f"\n+{added_total} tenants new to the pool, {retagged_total} re-tagged "
        f"({new_files} new CSVs). active/ untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
