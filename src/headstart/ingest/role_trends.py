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
_FAMILIES = REPO_ROOT / "config" / "role_families.json"  # curated, in git (ADR-0040)
_LEDGER = REPO_ROOT / "data" / "state" / "role_trends.csv"

_COLUMNS = ("ts", "version", "family", "band", "count")


def count_groups(
    rows, centroids, families: dict[int, str | None]
) -> tuple[dict[tuple[str, str], int], int]:
    """Count served rows into ``(family, band)`` groups; non-tech rows are counted apart.

    Returns ``(counts, non_tech)``. Rows whose cluster maps to None are the tech filter's
    known creep (ADR-0017 is recall-biased on purpose) — kept out of the role groups, but
    returned as one number so the ledger carries a filter-health series (ADR-0040)."""
    import numpy as np

    vectors = np.stack(rows["vector"].to_numpy(zero_copy_only=False)).astype(
        "float32", copy=False
    )
    clusters = roles.assign(vectors, centroids)
    min_years = rows["min_years"].to_pylist()
    titles = rows["title"].to_pylist()
    employment = rows["employment_type"].to_pylist()

    counts: dict[tuple[str, str], int] = {}
    non_tech = 0
    for cluster, years, title, etype in zip(clusters, min_years, titles, employment):
        family = families[int(cluster)]
        if family is None:
            non_tech += 1
            continue
        key = (family, roles.band(years, title, etype))
        counts[key] = counts.get(key, 0) + 1
    return counts, non_tech


def append_ledger(
    ledger: Path,
    counts: dict[tuple[str, str], int],
    non_tech: int,
    version: int,
    ts: str,
) -> int:
    """Append one row per non-empty group + the non-tech diagnostic. Header on first write.

    The diagnostic rides the same file as ``(non-tech, all)`` — one number per run, unbanded
    because a band on a Data Entry Clerk means nothing. The chart filters it out; its trend is
    the tech filter's health over time. Returns rows written."""
    fresh = not ledger.exists()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if fresh:
            writer.writerow(_COLUMNS)
        for (family, band), n in sorted(counts.items()):
            writer.writerow([ts, version, family, band, n])
        writer.writerow([ts, version, roles.NON_TECH, "all", non_tech])
        fh.flush()
    return len(counts) + 1


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--centroids", type=Path, default=_CENTROIDS)
    ap.add_argument("--families", type=Path, default=_FAMILIES)
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
    families = roles.load_families(args.families, manifest)
    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    rows = (
        table.search()
        .select(["vector", "min_years", "title", "employment_type"])
        .limit(max(n, 1))
        .to_arrow()
    )
    named = len({f for f in families.values() if f is not None})
    _log.info(
        f"assigning {n} served rows to {named} families via {manifest['k']} clusters "
        f"(centroid version {manifest['version']})"
    )

    counts, non_tech = count_groups(rows, centroids, families)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = append_ledger(args.ledger, counts, non_tech, manifest["version"], ts)
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    _log.info(
        f"appended {written} rows @ {ts} -> {args.ledger} | top: "
        + ", ".join(f"{family}/{band} {c}" for (family, band), c in top)
    )
    _log.info(
        f"non-tech: {non_tech} of {n} served rows ({100 * non_tech / n:.1f}% — the "
        "ADR-0017 filter's creep) excluded from the chart"
        if n
        else "non-tech: 0 of 0 served rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
