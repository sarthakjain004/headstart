"""`dedupe_boards.py --apply` must refuse an alias ledger another script writes.

It rewrites the whole file from what redirects say, and redirects find none of the rows
ClearCompany's `shared-reqs` (ADR-0182), Taleo Enterprise's `subset-reqs` (ADR-0186) or
Eightfold's `backing-reqs` (ADR-0191) signal writes — nor a hand-written row such as Jibe's — so an
apply would replace every one of them with nothing, and report success.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("ats", ["clearcompany", "taleo_enterprise", "eightfold"])
def test_apply_refuses_a_ledger_another_script_writes(ats, tmp_path):
    """Run from a copy rooted in ``tmp_path`` with an empty liveness ledger: the script finds its
    ledgers from its own path, so were the refusal missing, the apply would probe nothing and
    write into ``tmp_path`` rather than over the committed alias ledger."""
    pytest.importorskip("curl_cffi")
    script = tmp_path / "scripts" / "validate" / "dedupe_boards.py"
    script.parent.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "validate" / "dedupe_boards.py", script)
    ledger = tmp_path / "data" / "validate" / "liveness" / f"{ats}.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("ats,tenant,url,status,jobs,checked_at\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(script), "--ats", ats, "--apply"],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert done.returncode != 0
    assert "would erase it" in done.stderr
    assert not (tmp_path / "data" / "validate" / "aliases").exists()


def test_apply_refuses_a_ledger_holding_a_row_no_redirect_finds(tmp_path):
    """Jibe's one row was written by hand (signal `shared-listing`), so no script owns the file
    and only its content says an apply would erase something."""
    pytest.importorskip("curl_cffi")
    script = tmp_path / "scripts" / "validate" / "dedupe_boards.py"
    script.parent.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "validate" / "dedupe_boards.py", script)
    ledger = tmp_path / "data" / "validate" / "liveness" / "jibe.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("ats,tenant,url,status,jobs,checked_at\n", encoding="utf-8")
    alias = tmp_path / "data" / "validate" / "aliases" / "jibe.csv"
    alias.parent.mkdir(parents=True)
    row = "jibe,rentokil-initial,rentokil,shared-listing,rentokil,2026-09-24\n"
    alias.write_text("ats,duplicate,canonical,signal,resolved_to,checked_at\n" + row)
    done = subprocess.run(
        [sys.executable, str(script), "--ats", "jibe", "--apply"],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert done.returncode != 0
    assert "would erase" in done.stderr
    assert alias.read_text().endswith(row)
