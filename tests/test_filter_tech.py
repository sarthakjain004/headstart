"""Tests for the tech-filter stage (headstart.ingest.filter_tech): the Dormant-Board hop (ADR-0250).

The gate itself is tested in ``test_jobs_tech_filter.py``; this is what the stage adds to it.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from headstart.ingest import board_dormancy, filter_tech


def _run(tmp_path: Path, verdict: Path) -> list[str]:
    src = tmp_path / "jobs"
    src.mkdir()
    rows = [
        {"id": "smartrecruiters:SonsoftInc:1", "title": "Java Lead"},
        {"id": "smartrecruiters:boschgroup:1", "title": "Java Lead"},
    ]
    (src / "smartrecruiters.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    old = sys.argv
    sys.argv = [
        "filter_tech",
        "--src",
        str(src),
        "--dst",
        str(src / "tech"),
        "--dormant-boards",
        str(verdict),
    ]
    try:
        assert filter_tech.main() == 0
    finally:
        sys.argv = old
    lines = (src / "tech" / "smartrecruiters.jsonl").read_text().splitlines()
    return [json.loads(line)["id"] for line in lines]


def test_the_stage_leaves_out_the_boards_scrape_join_judged_dormant(tmp_path):
    verdict = tmp_path / "dormant_boards.json"
    board_dormancy.write({"smartrecruiters:sonsoftinc": "2017-09-14"}, verdict)
    assert _run(tmp_path, verdict) == ["smartrecruiters:boschgroup:1"]


def test_with_no_verdict_the_stage_warns_and_leaves_nothing_out(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="headstart.ingest.filter_tech"):
        kept = _run(tmp_path, tmp_path / "absent.json")
    assert kept == ["smartrecruiters:SonsoftInc:1", "smartrecruiters:boschgroup:1"]
    assert any(
        "no readable Dormant-Board verdict" in r.getMessage() for r in caplog.records
    )
