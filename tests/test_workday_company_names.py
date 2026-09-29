"""The Workday name-cache script's `--new-since` scope (scripts/validate/workday_company_names.py).

A ledger change re-runs the script for the Boards it made Hiring, not for every Board the cache
lacks: on 2026-09-29 the cache lacked 3,981 Hiring Boards, of which 63 had landed since it was
written.
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
_ROW = (
    "workday,2020-companies,https://2020companies.wd1.myworkdayjobs.com/"
    "external_careers,live,{jobs},2026-08-14\n"
)


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("workday_company_names", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_show(files: dict[str, str], asked: list | None = None):
    """A `git show REF:path` that knows ``files`` and fails, as git does, on any other path."""

    def run(command, check=False, **_):
        if asked is not None:
            asked.append(command)
        path = command[-1].split(":", 1)[1]
        if path in files:
            return subprocess.CompletedProcess(command, 0, files[path], "")
        if check:
            raise subprocess.CalledProcessError(128, command)
        return subprocess.CompletedProcess(command, 128, "", "fatal: path not in ref")

    return run


def test_hiring_at_reads_the_ledger_as_it_stood_at_the_ref(script, monkeypatch):
    asked = []
    ledger = {script.LEDGER: _HEADER + _ROW.format(jobs=1271)}
    monkeypatch.setattr(script.subprocess, "run", _git_show(ledger, asked))
    assert script._hiring_at("3c4dcdfe") == {"workday:2020companies/external_careers"}
    assert asked[0][-1] == "3c4dcdfe:data/validate/liveness/workday.csv"


def test_a_board_held_with_no_postings_is_read_once_it_hires(script, monkeypatch):
    """24 Workday Boards landed at 0 postings between the cache's first write and 2026-09-29.
    Keyed on "held at REF", a later `--new-since` never read one of them once it started
    hiring."""
    ledger = {script.LEDGER: _HEADER + _ROW.format(jobs=0)}
    monkeypatch.setattr(script.subprocess, "run", _git_show(ledger))
    assert script._hiring_at("HEAD") == set()


def test_hiring_at_reads_the_alias_file_as_it_stood_at_the_ref(script, monkeypatch):
    """A Board buried at REF was not Hiring there. The alias file is found beside the liveness
    directory, so it has to be written into the same copy of the repo's layout."""
    files = {
        script.LEDGER: _HEADER + _ROW.format(jobs=1271),
        script.ALIASES: "ats,duplicate,canonical,signal,resolved_to,checked_at\n"
        "workday,https://2020companies.wd1.myworkdayjobs.com/external_careers,"
        "https://2020companies.wd1.myworkdayjobs.com/careers,redirect,,2026-08-14\n",
    }
    monkeypatch.setattr(script.subprocess, "run", _git_show(files))
    assert script._hiring_at("HEAD") == set()
