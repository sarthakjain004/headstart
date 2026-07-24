"""Tests for the scrape-shard join (scripts/pipeline/join_shards.py, ADR-0026).

The union invariant: every shard's ``{ats}.jsonl`` is concatenated per ATS into one snapshot, so
sync sees the full scraped-Board set. Streaming concat; downstream dedups by id.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "join_shards", _ROOT / "scripts" / "pipeline" / "join_shards.py"
)
js = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(js)


def _shard(frags: Path, k: int, files: dict[str, list[str]]) -> None:
    d = frags / f"shard-{k}"
    d.mkdir(parents=True, exist_ok=True)
    for name, lines in files.items():
        (d / name).write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _run(shards: Path, out: Path) -> None:
    old = sys.argv
    sys.argv = ["join_shards", "--shards", str(shards), "--out", str(out)]
    try:
        assert js.main() == 0
    finally:
        sys.argv = old


def test_join_unions_per_ats_across_shards(tmp_path):
    frags = tmp_path / "frags"
    _shard(
        frags,
        0,
        {
            "lever.jsonl": ['{"id":"lever:a:1"}', '{"id":"lever:a:2"}'],
            "greenhouse.jsonl": ['{"id":"gh:x:1"}'],
        },
    )
    _shard(frags, 1, {"lever.jsonl": ['{"id":"lever:b:1"}']})
    out = tmp_path / "jobs"

    _run(frags, out)

    lever = out.joinpath("lever.jsonl").read_text().splitlines()
    assert len(lever) == 3  # 2 from shard-0 + 1 from shard-1
    assert set(lever) == {
        '{"id":"lever:a:1"}',
        '{"id":"lever:a:2"}',
        '{"id":"lever:b:1"}',
    }
    assert out.joinpath("greenhouse.jsonl").read_text().splitlines() == [
        '{"id":"gh:x:1"}'
    ]


def test_join_no_shards_is_empty(tmp_path):
    out = tmp_path / "jobs"
    _run(tmp_path / "absent", out)
    assert list(out.glob("*.jsonl")) == []
