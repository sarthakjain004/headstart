"""Recruiterflow discovery preserves Board identity and excludes vendor pages."""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def miner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/discover"))
    spec = importlib.util.spec_from_file_location(
        "mine_recruiterflow", ROOT / "scripts/discover/mine_recruiterflow.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_variants_name_one_unfiltered_board(miner):
    text = (
        "https://recruiterflow.com/RFCAREERS/jobs/166?widget=1\n"
        "https://recruiterflow.com/rfcareers/jobs-page-widget?department=Engineering\n"
        '{"url":"https://recruiterflow.com/db_123abc/jobs/7"}\n'
        "https://recruiterflow.com/blog/ats\n"
        "https://recruiterflow.com/about\n"
    )
    assert miner.candidates(text) == {
        "rfcareers": "https://recruiterflow.com/rfcareers/jobs",
        "db_123abc": "https://recruiterflow.com/db_123abc/jobs",
    }


def test_merge_preserves_existing_url_and_adds_source_evidence(miner, tmp_path):
    path = tmp_path / "pool.csv"
    path.write_text(
        "ats,tenant,url,source\nrecruiterflow,rfcareers,https://recruiterflow.com/RFCAREERS/jobs,search\n"
    )
    assert (
        miner.merge(
            path,
            {
                "rfcareers": "https://recruiterflow.com/rfcareers/jobs",
                "new": "https://recruiterflow.com/new/jobs",
            },
            "wayback",
        )
        == 1
    )
    with path.open() as file:
        rows = {r["tenant"]: r for r in csv.DictReader(file)}
    assert rows["rfcareers"]["url"] == "https://recruiterflow.com/RFCAREERS/jobs"
    assert rows["rfcareers"]["source"] == "search;wayback"
    assert rows["new"]["source"] == "wayback"


def test_failed_source_does_not_checkpoint_or_create_pool(miner, monkeypatch, tmp_path):
    monkeypatch.setattr(miner, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(miner, "POOL", tmp_path / "pool.csv")
    monkeypatch.setattr(sys, "argv", ["mine_recruiterflow.py", "wayback"])

    def fail(*_args, **_kwargs):
        raise miner.SourceUnavailable("HTTP 429 Retry-After:60")

    monkeypatch.setattr(miner, "fetch", fail)
    with pytest.raises(SystemExit) as raised:
        miner.main()
    assert raised.value.code == 2
    assert not (tmp_path / "archive-checkpoints.json").exists()
    assert not (tmp_path / "pool.csv").exists()


def test_a_200_error_page_is_not_an_empty_archive_page(miner, monkeypatch, tmp_path):
    monkeypatch.setattr(miner, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(miner, "POOL", tmp_path / "pool.csv")
    monkeypatch.setattr(sys, "argv", ["mine_recruiterflow.py", "wayback"])
    responses = iter(["1", "<html>Temporarily unavailable</html>"])
    monkeypatch.setattr(miner, "fetch", lambda *_a, **_k: next(responses))
    with pytest.raises(SystemExit):
        miner.main()
    assert not (tmp_path / "archive-checkpoints.json").exists()
    assert not (tmp_path / "pool.csv").exists()
