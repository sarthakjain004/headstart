"""Comparison must reject cancellation and pairing a missing tick to a later run."""

import importlib.util
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SPEC = importlib.util.spec_from_file_location(
    "comparison", Path(__file__).parents[1] / "scripts/eval/compare_restated_trends.py"
)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def write(root, rows):
    d = root / comparison.DELTAS
    d.mkdir(parents=True)
    t = pa.Table.from_pylist(rows).replace_schema_metadata(
        {b"ts": b"2026-10-02T00:00:00+00:00"}
    )
    pq.write_table(t, d / "tick.parquet")


def test_equal_totals_but_different_boards_fail(tmp_path, monkeypatch):
    base = {
        "metric": "stock",
        "family": "software-engineering",
        "band": "mid",
        "delta": 10,
    }
    write(tmp_path / "live", [base | {"board": "lever:a"}])
    write(tmp_path / "restated", [base | {"board": "lever:b"}])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare",
            "--live",
            str(tmp_path / "live"),
            "--restated",
            str(tmp_path / "restated"),
            "--report",
            str(tmp_path / "report.json"),
        ],
    )
    assert comparison.main() == 1


def test_missing_tick_cannot_pair_to_the_next_runs_live_tick():
    restated = ["2026-10-02T00:00:00+00:00", "2026-10-02T01:00:00+00:00"]
    live = ["2026-10-02T01:20:00+00:00"]
    assert comparison.pairs(restated, live, 3) == [(restated[1], live[0])]
