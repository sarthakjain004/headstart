"""Reproduce a baseline window using its preserved rule code and Board ledgers.

The replay implementation comes from the reviewed branch; production rule modules,
configuration and ledgers come from the captured archive. Inputs and outputs remain
in the runner and never modify the live HF dataset.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

import pyarrow.parquet as pq

from headstart.ingest.board_dormancy import PostedDates
from headstart.ingest.index_plan import duplicate_ranks
from headstart.ingest.restate_baseline import committed_baseline


def main():
    root = Path.cwd()
    facts = root / "data/facts"
    state = root / "data/state"
    baseline = committed_baseline(facts, state)
    if baseline is None:
        raise ValueError(
            "no committed baseline; cannot certify exact starting coverage"
        )
    metadata = pq.read_schema(baseline).metadata
    fingerprint = json.loads(metadata[b"methodology"])["rules_fingerprint"]
    archive = facts / "reference_rules" / f"{fingerprint}.zip"
    with tempfile.TemporaryDirectory(prefix="frozen-trends-rules-") as tmp:
        frozen = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("unsafe rule archive member")
            z.extractall(frozen)
        for module in (root / "src/headstart/ingest").glob("restate_*.py"):
            shutil.copy2(module, frozen / "src/headstart/ingest" / module.name)
        # These replay adapters expose existing frozen rule helpers; they do not replace
        # the captured implementations that decide groups, ranks or Dormancy.
        planner = frozen / "src/headstart/ingest/index_plan.py"
        if "def duplicate_ranks(" not in planner.read_text():
            with planner.open("a") as stream:
                stream.write("\n\n" + inspect.getsource(duplicate_ranks))
        dormancy = frozen / "src/headstart/ingest/board_dormancy.py"
        if "    def newest(" not in dormancy.read_text():
            with dormancy.open("a") as stream:
                stream.write(
                    "\n\n"
                    + textwrap.dedent(inspect.getsource(PostedDates.newest))
                    + "\nPostedDates.newest = newest\n"
                )
        env = os.environ | {"PYTHONPATH": str(frozen / "src")}
        command = [
            sys.executable,
            "-u",
            "-m",
            "headstart.ingest.restate_run",
            "--facts",
            str(facts),
            "--baseline",
            str(baseline),
            "--board-failures",
            str(state / "board_failures.csv"),
            "--title-cache",
            str(state / "role_title_families.parquet"),
            "--db",
            str(root / "data/lancedb"),
            "--descriptions",
            str(root / "data/descriptions"),
            "--out",
            str(root / "data/restated"),
            "--encode-budget-seconds",
            os.environ.get("ENCODE_BUDGET_SECONDS", "1800"),
        ]
        print(
            f"Frozen rule fingerprint {fingerprint}; baseline {baseline.name}",
            flush=True,
        )
        return subprocess.run(command, cwd=frozen, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
