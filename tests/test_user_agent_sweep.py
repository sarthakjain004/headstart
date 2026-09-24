"""Tests for scripts/validate/user_agent_sweep.py's sample of ledger rows (ADR-0203)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from headstart.config import CompanyRef

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate" / "user_agent_sweep.py"
)


@pytest.fixture(scope="module")
def user_agent_sweep():
    spec = importlib.util.spec_from_file_location("user_agent_sweep", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_reads_each_boards_slug_through_its_scraper(
    user_agent_sweep, tmp_path, monkeypatch
):
    """The sample used to pass the raw `tenant` as the slug, which is not the slug on workday,
    personio, zoho or taleo_be — so every such Board failed under both agents and read UNREACHED,
    evidence about nothing."""
    ledger = "ats,tenant,url,status,jobs,checked_at\n"
    (tmp_path / "workday.csv").write_text(
        ledger + "workday,acme,https://acme.wd3.myworkdayjobs.com/External/,live,4,"
        "2026-09-24\n"
    )
    (tmp_path / "personio.csv").write_text(
        ledger
        + "personio,acme,https://acme.jobs.personio.com/job/1?language=de,live,3,"
        "2026-09-24\n"
    )
    monkeypatch.setattr(user_agent_sweep, "LIVENESS", tmp_path)
    assert user_agent_sweep._sample(seed=1, per_ats=1) == [
        CompanyRef("personio", "acme.jobs.personio.com", "acme"),
        CompanyRef("workday", "https://acme.wd3.myworkdayjobs.com/External", "acme"),
    ]
