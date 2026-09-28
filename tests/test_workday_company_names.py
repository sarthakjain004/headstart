"""The Workday name-cache script's `--new-since` scope (scripts/validate/workday_company_names.py).

A landing re-runs the script for the Boards it landed, not for every Board the cache lacks: on
2026-09-29 the cache lacked 3,981 Hiring Boards, of which 63 had landed since it was written.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate"
    / "workday_company_names.py"
)
_HEADER = "ats,tenant,url,status,jobs,checked_at\n"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("workday_company_names", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_held_at_reads_the_ledger_as_it_stood_at_the_ref(script, monkeypatch):
    asked = []

    def git_show(command, **_):
        asked.append(command)
        ledger = _HEADER + (
            "workday,2020-companies,https://2020companies.wd1.myworkdayjobs.com/"
            "external_careers,live,1271,2026-08-14\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=ledger, stderr="")

    monkeypatch.setattr(script.subprocess, "run", git_show)
    assert script._held_at("3c4dcdfe") == {"workday:2020companies/external_careers"}
    assert asked[0][-1] == "3c4dcdfe:data/validate/liveness/workday.csv"


def test_held_at_counts_a_board_whatever_it_listed_then(script, monkeypatch):
    """A Board that was held with no postings is not new when it starts hiring."""
    ledger = _HEADER + (
        "workday,2020-companies,https://2020companies.wd1.myworkdayjobs.com/"
        "external_careers,live,0,2026-08-14\n"
    )
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, ledger, ""),
    )
    assert script._held_at("HEAD") == {"workday:2020companies/external_careers"}
