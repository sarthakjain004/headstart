"""Saved responses survive interruption before the candidate pool is updated."""

import importlib.util
import json
import sys
from pathlib import Path


def test_walk_replays_saved_response_without_fetching(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts" / "discover"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "mine_smartrecruiters_lookup", scripts / "mine_smartrecruiters_lookup.py"
    )
    miner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, miner)
    spec.loader.exec_module(miner)
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("ats,tenant,url,status,jobs,checked_at\n")
    pool = tmp_path / "pool.csv"
    capture = tmp_path / "experiment" / "artifacts" / "responses.jsonl"
    capture.parent.mkdir(parents=True)
    capture.write_text(
        json.dumps(
            {
                "q": "aa",
                "results": [{"identifier": "RealCompany", "name": "Real Company"}],
            }
        )
        + "\n"
    )
    monkeypatch.setattr(miner, "LEDGER", ledger)
    monkeypatch.setattr(miner, "POOL", pool)
    monkeypatch.setattr(miner, "WALK_LOG", capture)
    monkeypatch.setattr(miner, "EXPERIMENT", capture.parent.parent)
    monkeypatch.setattr(
        miner,
        "_lookup",
        lambda q: (_ for _ in ()).throw(
            AssertionError("saved response must not trigger a request")
        ),
    )
    miner.walk(2, 0)
    miner.walk(2, 0)
    assert pool.read_text().count("RealCompany") == 2  # tenant and URL, one row
    assert len(pool.read_text().splitlines()) == 2
