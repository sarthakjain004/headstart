#!/usr/bin/env python3
"""Retrospective proof that Jobs change role family without closing (ADR-0057).

ADR-0057 records `id -> family` going forward, so from its second run the transition ledger
answers this directly. This script answers it for the runs that *already happened*, using
LanceDB's table versions: check out two versions of the served table, assign both to the same
frozen centroids, and diff the family of every id present in both.

    same id, both versions, different family  ->  a reassignment, not a closure

That is the distinction the stock series cannot express, and the reason a 622-row
"software-engineering decline" was read as jobs disappearing when much of it was redistribution.

**Run this on CI, not a laptop.** It needs the whole `data/lancedb` table (~1 GB) plus the
centroid store; the repo's convention is that large HF pulls and index reads run in Actions
(see `docs/agents/deployment.md`). It is read-only: it opens versions and writes nothing.

Run: python scripts/eval/diff_family_assignments.py --versions 40 48
     python scripts/eval/diff_family_assignments.py --list
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np  # noqa: E402

from headstart import roles  # noqa: E402

_DB = Path("data/lancedb")
_CENTROIDS = Path("data/state/role_centroids")
_FAMILIES = Path("config/role_families.json")


def _assign_at(table, version: int, centroids, families) -> dict[str, str]:
    """`id -> family` for one table version, using the same centroids as the trends step."""
    table.checkout(version)
    rows = table.search().select(["id", "vector"]).limit(table.count_rows()).to_arrow()
    vectors = np.stack(rows["vector"].to_numpy(zero_copy_only=False))
    clusters = roles.assign(vectors, centroids)
    out: dict[str, str] = {}
    for job_id, cluster in zip(rows["id"].to_pylist(), clusters, strict=True):
        family = families[int(cluster)]
        if family is not None:
            out[job_id] = family
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=_DB)
    ap.add_argument("--centroids", type=Path, default=_CENTROIDS)
    ap.add_argument("--families", type=Path, default=_FAMILIES)
    ap.add_argument("--versions", type=int, nargs=2, metavar=("OLD", "NEW"))
    ap.add_argument("--list", action="store_true", help="list available table versions")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    import lancedb

    from headstart.search import PROD_TABLE

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    versions = [v["version"] for v in table.list_versions()]
    if args.list or not args.versions:
        print(f"{len(versions)} versions available: {versions[:5]} ... {versions[-5:]}")
        print("pick two with --versions OLD NEW")
        return 0

    old_v, new_v = args.versions
    centroids, manifest = roles.load(args.centroids)
    families = roles.load_families(args.families, manifest)
    print(f"centroid version {manifest['version']}, k={manifest['k']}", flush=True)

    old = _assign_at(table, old_v, centroids, families)
    print(f"v{old_v}: {len(old):,} rows in a family", flush=True)
    new = _assign_at(table, new_v, centroids, families)
    print(f"v{new_v}: {len(new):,} rows in a family", flush=True)

    both = old.keys() & new.keys()
    moved = collections.Counter((old[i], new[i]) for i in both if old[i] != new[i])
    total = sum(moved.values())
    print(
        f"\nids in both versions: {len(both):,}"
        f"\n  changed family:     {total:,} ({100 * total / max(len(both), 1):.2f}%)"
        f"\n  only in v{old_v} (closed):  {len(old.keys() - new.keys()):,}"
        f"\n  only in v{new_v} (opened):  {len(new.keys() - old.keys()):,}",
        flush=True,
    )
    if moved:
        print(f"\ntop {args.top} transitions:", flush=True)
        for (a, b), n in moved.most_common(args.top):
            print(f"   {a:28} -> {b:28} {n:6,}", flush=True)
        net: dict[str, int] = collections.defaultdict(int)
        for (a, b), n in moved.items():
            net[a] -= n
            net[b] += n
        print(
            "\nnet reassignment by family (negative = lost rows to other families):",
            flush=True,
        )
        for fam, n in sorted(net.items(), key=lambda kv: kv[1]):
            if n:
                print(f"   {fam:28} {n:+7,}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
