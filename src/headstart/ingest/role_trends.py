#!/usr/bin/env python3
"""Append this run's role-group counts to the trends ledger (ADR-0040) — merge stage.

Runs after ``index sync`` and ``index prune``, so it counts the **served stock**: every row
still in the ``jobs`` table is assigned to its nearest frozen role-family centroid
(``headstart.roles``) and banded by the experience columns the table already carries, and one
``(ts, version, cluster, label, band, count)`` row per non-empty group is appended to
``data/state/role_trends.csv``. Series identity keys on ``(version, cluster)`` — ``label`` is
display text that may be polished later without breaking a line, and ``version`` changes only
on an explicit centroid refit (a re-base, ADR-0040).

Degrades rather than dies: with no centroid store on disk (the fit hasn't shipped, or the
join's state artifact was lost) it logs a warning and exits 0 — trends must never sink a run
that already scraped and embedded successfully.

Run: python -m headstart.ingest.role_trends
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from headstart import log, roles
from headstart.ingest import REPO_ROOT

_log = log.get(__name__, __spec__)

_DB = REPO_ROOT / "data" / "lancedb"
_CENTROIDS = REPO_ROOT / "data" / "state" / "role_centroids"
_LEDGER = REPO_ROOT / "data" / "state" / "role_trends.csv"

_COLUMNS = ("ts", "version", "cluster", "label", "band", "count")


def count_groups(
    rows, centroids, manifest
) -> dict[tuple[int, str], int]:  # (cluster, band) -> count
    """Assign every served row to (family centroid, experience band) and count."""
    import numpy as np

    vectors = np.stack(rows["vector"].to_numpy(zero_copy_only=False)).astype(
        "float32", copy=False
    )
    families = roles.assign(vectors, centroids)
    min_years = rows["min_years"].to_pylist()
    titles = rows["title"].to_pylist()
    employment = rows["employment_type"].to_pylist()

    counts: dict[tuple[int, str], int] = {}
    for family, years, title, etype in zip(families, min_years, titles, employment):
        key = (int(family), roles.band(years, title, etype))
        counts[key] = counts.get(key, 0) + 1
    return counts


def append_ledger(
    ledger: Path, counts: dict[tuple[int, str], int], manifest: dict, ts: str
) -> int:
    """Append one row per non-empty group, header on first write. Returns rows written."""
    labels = {c["id"]: c["label"] for c in manifest["clusters"]}
    fresh = not ledger.exists()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if fresh:
            writer.writerow(_COLUMNS)
        for (cluster, band), n in sorted(counts.items()):
            writer.writerow(
                [ts, manifest["version"], cluster, labels.get(cluster, ""), band, n]
            )
        fh.flush()
    return len(counts)


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--centroids", type=Path, default=_CENTROIDS)
    ap.add_argument("--ledger", type=Path, default=_LEDGER)
    args = ap.parse_args()

    if not (args.centroids / "manifest.json").exists():
        _log.warning(
            f"no centroid store at {args.centroids} — skipping trends this run "
            "(fit one with the cluster-roles workflow, ADR-0040)"
        )
        return 0

    import lancedb

    from headstart.search import PROD_TABLE

    centroids, manifest = roles.load(args.centroids)
    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    rows = (
        table.search()
        .select(["vector", "min_years", "title", "employment_type"])
        .limit(max(n, 1))
        .to_arrow()
    )
    _log.info(
        f"assigning {n} served rows to {manifest['k']} families "
        f"(centroid version {manifest['version']})"
    )

    counts = count_groups(rows, centroids, manifest)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    groups = append_ledger(args.ledger, counts, manifest, ts)
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    labels = {c["id"]: c["label"] for c in manifest["clusters"]}
    _log.info(
        f"appended {groups} group rows @ {ts} -> {args.ledger} | top: "
        + ", ".join(f"{labels.get(c, c)}/{band} {n}" for (c, band), n in top)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
