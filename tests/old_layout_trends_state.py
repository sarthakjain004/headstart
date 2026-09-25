"""A small Trends `data/state` in the layout before ADR-0230 step 6, shared by the tests of
`history_migration`, the migration script and `trend_history`'s reading of it.

It is written the way the old writer wrote it: a Board-delta file per tick, counted against the
running total **at its own series version**, so a new version's first tick is a baseline; the
aggregate ledger and the Board-count snapshot as sums of the same levels; and an epoch row only
where the methodology moved. Two ticks come before per-Board counting, which only the aggregate
holds.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

T = [f"2026-09-{day:02d}T00:00:00+00:00" for day in range(10, 16)]
ARCHIVED = {
    T[0]: {("stock", "backend", "mid", "all"): 5, ("stock", "non-tech", "all", "all"): 2},
    T[1]: {("stock", "backend", "mid", "all"): 6, ("stock", "non-tech", "all", "all"): 2},
}  # fmt: skip
ACME, BETA = "greenhouse:acme", "lever:beta"
# Board levels at each later tick, and the series version the old writer counted it at. The
# version-2001 tick moves acme's jobs to another family: a re-base.
LEVELS = {
    T[2]: ("2", {(ACME, "stock", "backend", "mid"): 3, (ACME, "new", "backend", "mid"): 1,
                 (BETA, "stock", "non-tech", "all"): 2}),
    T[3]: ("2", {(ACME, "stock", "backend", "mid"): 4, (ACME, "new", "backend", "mid"): 2,
                 (BETA, "stock", "non-tech", "all"): 2}),
    T[4]: ("2001", {(ACME, "stock", "software", "mid"): 4, (ACME, "new", "software", "mid"): 2,
                    (BETA, "stock", "non-tech", "all"): 3}),
    T[5]: ("2001", {(ACME, "stock", "software", "mid"): 5, (ACME, "new", "software", "mid"): 3,
                    (BETA, "stock", "non-tech", "all"): 3}),
}  # fmt: skip
# One turnover row (ADR-0227) on the re-base tick: a count of its tick, not a change in a level.
OPENED = (ACME, "opened", "software", "mid", 1)
EPOCH_HEADER = (
    "ts,centroid_version,family_map_fingerprint,tech_filter_version,derivations_version,"
    "dedup_version,family_classifier_version\n"
)
EPOCHS = EPOCH_HEADER + f"{T[3]},2,abc,1,13,1,none\n{T[4]},2,abc,1,13,1,3b5cc5d9183c\n"


def tick_file(root: Path, ts: str) -> Path:
    return root / "role_trend_board_deltas" / f"{ts.replace(':', '-')}.parquet"


def index_levels(levels: dict) -> dict:
    """Board levels summed as the aggregate ledger holds them: non-tech in one row."""
    out: dict = {}
    for (board, metric, family, band), n in levels.items():
        key = (
            (metric, family, "all", "all")
            if family == "non-tech"
            else (metric, family, band, board.split(":")[0])
        )
        out[key] = out.get(key, 0) + n
    return out


def _write_tick(root: Path, ts: str, version: str, rows: list[tuple]) -> None:
    names = ("ts", "board", "metric", "family", "band", "ats", "delta")
    full = [(ts, board, metric, family, band, board.split(":")[0], delta)
            for board, metric, family, band, delta in rows]  # fmt: skip
    columns = list(zip(*full)) if full else [()] * len(names)
    table = pa.table(
        {name: list(col) for name, col in zip(names, columns)},
        schema=pa.schema(
            [(n, pa.int64() if n == "delta" else pa.string()) for n in names],
            metadata={b"centroid_version": version.encode()},
        ),
    )
    tick_file(root, ts).parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, tick_file(root, ts))


def write(root: Path) -> Path:
    """The old-layout state under ``root``, which it returns."""
    by_version: dict[str, dict] = {}
    for ts, (version, levels) in LEVELS.items():
        before = by_version.get(version, {})
        rows = [
            (*key, levels.get(key, 0) - before.get(key, 0))
            for key in sorted(before.keys() | levels.keys())
            if levels.get(key, 0) != before.get(key, 0)
        ]
        if ts == T[4]:
            rows.append(OPENED)
        _write_tick(root, ts, version, rows)
        by_version[version] = levels
    aggregate = [
        (ts, version, *key, n)
        for ts, version, index in [
            *((ts, "2", level) for ts, level in ARCHIVED.items()),
            *((ts, v, index_levels(levels)) for ts, (v, levels) in LEVELS.items()),
        ]
        for key, n in index.items()
    ]
    ts, version, metric, family, band, ats, count = zip(*aggregate)
    pq.write_table(
        pa.table({
            "ts": pa.array([datetime.fromisoformat(t) for t in ts],
                           pa.timestamp("ms", tz="UTC")),
            "version": pa.array([int(v) for v in version], pa.int64()),
            "metric": list(metric), "family": list(family), "band": list(band),
            "ats": list(ats), "count": pa.array(count, pa.int64()),
        }),
        root / "role_trends.parquet",
    )  # fmt: skip
    rows = sorted((*key, key[0].split(":")[0], n) for key, n in LEVELS[T[5]][1].items())
    names = ("board", "metric", "family", "band", "ats", "count")
    pq.write_table(
        pa.table(
            {name: [r[i] for r in rows] for i, name in enumerate(names)},
            metadata={b"centroid_version": b"2001", b"as_of": T[5].encode()},
        ),
        root / "role_trend_board_counts.parquet",
    )
    (root / "trends_epochs.csv").write_text(EPOCHS)
    return root
