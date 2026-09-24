"""`dedupe_boards.py --apply` must refuse an alias ledger another script writes.

It rewrites the whole file from what redirects say, and redirects find none of the rows
ClearCompany's `shared-reqs` (ADR-0182) or Taleo Enterprise's `subset-reqs` (ADR-0186) signal
writes — so an apply would replace every one of them with nothing, and report success.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("ats", ["clearcompany", "taleo_enterprise"])
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
