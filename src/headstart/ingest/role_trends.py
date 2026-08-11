#!/usr/bin/env python3
"""Append this run's role-group counts to the trends ledger (ADR-0040) — merge stage.

Runs after ``index sync`` and ``index prune``, so it counts the **served stock**: every row
still in the ``jobs`` table is assigned to its nearest frozen centroid, that cluster is mapped
to a curated role family (``config/role_families.json``), the row is banded by the experience
columns the table already carries, and one ``(ts, version, family, band, count)`` row per
non-empty group is appended to ``data/state/role_trends.csv`` — plus one unbanded
``(non-tech, all)`` diagnostic row. Series identity is ``(version, family)``; ``version``
changes only on an explicit centroid refit (a re-base, ADR-0040).

Degrades rather than dies: without the centroid store or the family map on disk (the fit
hasn't shipped, or the join's state artifact was lost) it logs a warning and exits 0 — trends
must never sink a run that already scraped and embedded successfully.

Run: python -m headstart.ingest.role_trends
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

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
    # to_numpy on the (possibly chunked) vector column yields one array per row; stacking is
    # row-aligned with the other columns' to_pylist across chunk boundaries.
    vectors = np.stack(rows["vector"].to_numpy(zero_copy_only=False))
    clusters = roles.assign(vectors, centroids)
    min_years = rows["min_years"].to_pylist()
    titles = rows["title"].to_pylist()
    employment = rows["employment_type"].to_pylist()

    counts: dict[tuple[str, str], int] = {}
    non_tech = 0
    for cluster, years, title, etype in zip(
        clusters, min_years, titles, employment, strict=True
    ):
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

    # Both inputs are checked, not just the centroids: the map ships in git while the
    # centroids ride the state artifact, so they go missing for different reasons — and the
    # step is `continue-on-error`, which would turn an unguarded FileNotFoundError into a
    # green run that silently never accrues a row.
    missing = [
        str(p)
        for p in (
            args.centroids / "manifest.json",
            args.centroids
            / "centroids.f32",  # a half-landed store must not reach roles.load
            args.families,
        )
        if not p.exists()
    ]
    if missing:
        _log.warning(
            f"skipping trends this run — missing {', '.join(missing)} (fit centroids with "
            "the cluster-roles workflow; the family map ships in git, ADR-0040)"
        )
        return 0

    import lancedb

    from headstart.search import PROD_TABLE

    try:
        centroids, manifest = roles.load(args.centroids)
        families = roles.load_families(args.families, manifest)
    except ValueError as exc:
        # An unusable taxonomy is a real defect, not a missing prerequisite — most likely a
        # refit shipped without re-curating the map, which ADR-0040 treats as routine. The
        # workflow step is `continue-on-error`, so without this it would crash into a green
        # run with no annotation at all; ERROR + exit 1 makes it visible and still non-fatal.
        _log.error(f"role taxonomy unusable, no trends this run: {exc}")
        return 1

    table = lancedb.connect(args.db).open_table(PROD_TABLE)
    n = table.count_rows()
    if not n:  # nothing to count, and np.stack has no empty case
        _log.warning(f"served table '{PROD_TABLE}' is empty — no trend rows this run")
        return 0
    # Logged before the read, not after: pulling the 768-d vector column for the whole table is
    # the slow, memory-hungry part of this step, so it should not run unnarrated.
    named = len({f for f in families.values() if f is not None})
    _log.info(
        f"assigning {n} served rows to {named} families via {manifest['k']} clusters "
        f"(centroid version {manifest['version']})"
    )
    rows = (
        table.search()
        .select(["vector", "min_years", "title", "employment_type"])
        .limit(n)
        .to_arrow()
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
