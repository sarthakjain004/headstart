"""Tests for the Lever Wayback miner's fold of its regional hosts (scripts/discover/mine_lever.py).

It is a script under `scripts/discover`, so we put that directory on the path and import it by
name, the way `test_merge_harvest_into_tenants.py` does.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_lever as ml


def test_the_fold_keeps_each_slug_in_the_casing_it_was_captured_in(
    tmp_path, monkeypatch
):
    """Lever reads a slug case-sensitively (`CesiumAstro` lists 309 postings, `cesiumastro` is
    "Document not found", 2026-09-28). The fold rewrote `lever.csv` with every tenant lowercased,
    so each mixed-case Board it touched was probed `dead`. One spelling per Board, first wins."""
    (tmp_path / "lever.csv").write_text(
        "ats,tenant,url\nlever,CesiumAstro,https://jobs.lever.co/CesiumAstro\n",
        encoding="utf-8",
    )
    (tmp_path / "lever_eu.csv").write_text(
        "ats,tenant,url\n"
        "lever_eu,cesiumastro,https://jobs.eu.lever.co/cesiumastro\n"
        "lever_eu,GoToGroup,https://jobs.eu.lever.co/GoToGroup\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ml, "WB", tmp_path)
    monkeypatch.setattr(ml, "mine", lambda label, host: None)
    ml.main()
    with (tmp_path / "lever.csv").open(encoding="utf-8") as f:
        rows = {r["tenant"]: r["url"] for r in csv.DictReader(f)}
    assert rows == {
        "CesiumAstro": "https://jobs.lever.co/CesiumAstro",
        "GoToGroup": "https://jobs.eu.lever.co/GoToGroup",
    }
