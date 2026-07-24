"""Tests for the scrape planner (scripts/pipeline/plan_scrape.py, ADR-0026).

The per-Board cost weighting (detail-fetch ATSes cost more) and the partition invariant — every
selected Board lands in exactly one shard — are the logic worth locking down. ``main`` is run with a
monkeypatched active-list so the test doesn't couple to the liveness-ledger CSV format.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from headstart.config import CompanyRef

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "plan_scrape", _ROOT / "scripts" / "pipeline" / "plan_scrape.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


def test_board_cost_weights_detail_fetchers():
    assert (
        ps.board_cost("workday", 10.0) == 10.0 * ps._DETAIL_WEIGHT
    )  # detail-fetch ATS
    assert ps.board_cost("lever", 10.0) == 10.0  # list-only ATS, weight 1
    assert (
        ps.board_cost("greenhouse", 0.0) == ps._EXPLORE_BASELINE
    )  # unscored -> baseline floor


def test_main_partitions_every_selected_board(tmp_path, monkeypatch):
    boards = [
        CompanyRef("workday", "big", "Big"),
        CompanyRef("lever", "acme", "Acme"),
        CompanyRef("greenhouse", "co", "Co"),
        CompanyRef("keka", "startup", "Startup"),
        CompanyRef("lever", "other", "Other"),
    ]
    monkeypatch.setattr(
        ps, "load_active_companies", lambda ledger, min_jobs=0: list(boards)
    )
    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan_scrape",
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
            "--max-boards",
            "0",
            "--max-shards",
            "3",
            "--target-boards",
            "2",
        ],
    )
    assert ps.main() == 0

    plan = json.loads((out / "plan.json").read_text())
    assert plan["count"] == 5
    assert plan["shards"] == [0, 1, 2]  # ceil(5/2) = 3 shards
    assert sum(plan["per_shard_boards"]) == 5

    seen = []
    for k in plan["shards"]:
        for line in (out / f"shard-{k}.jsonl").read_text().splitlines():
            rec = json.loads(line)
            assert set(rec) == {"ats", "slug", "name"}
            seen.append(f"{rec['ats']}:{rec['slug']}")
    # every selected board assigned exactly once
    assert sorted(seen) == sorted(f"{c.ats}:{c.slug}" for c in boards)


def test_main_empty_plan_when_no_boards(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "load_active_companies", lambda ledger, min_jobs=0: [])
    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan_scrape",
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
        ],
    )
    assert ps.main() == 0
    assert json.loads((out / "plan.json").read_text()) == {
        "shards": [],
        "count": 0,
        "per_shard_boards": [],
    }
