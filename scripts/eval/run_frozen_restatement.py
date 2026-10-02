"""Reproduce a baseline window using its preserved rule code and Board ledgers.

The replay implementation comes from the reviewed branch; production rule modules,
configuration and ledgers come from the captured archive. Inputs and outputs remain
in the runner and never modify the live HF dataset.
"""

from __future__ import annotations

import inspect
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
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
    diagnostics = root / "data/restate-diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
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
        process = subprocess.Popen(command, cwd=frozen, env=env)
        peak_mb = 0
        samples = (diagnostics / "resources.jsonl").open("w")
        while process.poll() is None:
            status = Path(f"/proc/{process.pid}/status")
            if status.exists():
                fields = dict(
                    line.split(":", 1)
                    for line in status.read_text().splitlines()
                    if ":" in line
                )
                resident_mb = int(fields.get("VmRSS", "0 kB").split()[0]) // 1024
                peak_mb = max(
                    peak_mb, int(fields.get("VmHWM", "0 kB").split()[0]) // 1024
                )
                samples.write(
                    json.dumps(
                        {
                            "elapsed_seconds": time.monotonic() - started,
                            "rss_mb": resident_mb,
                            "peak_mb": peak_mb,
                        }
                    )
                    + "\n"
                )
                samples.flush()
                print(
                    f"Replay resources: rss={resident_mb} MB peak={peak_mb} MB",
                    flush=True,
                )
                if resident_mb > 12 * 1024:
                    process.kill()
                    process.wait()
                    (diagnostics / "summary.json").write_text(
                        json.dumps(
                            {
                                "peak_mb": peak_mb,
                                "exit": process.returncode,
                                "watchdog_killed": True,
                            }
                        )
                    )
                    raise MemoryError(
                        f"replay exceeded 12 GB resident memory; peak={peak_mb} MB"
                    )
            time.sleep(5)
        samples.close()
        peak_mb = max(
            peak_mb, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
        )
        (diagnostics / "summary.json").write_text(
            json.dumps(
                {
                    "peak_mb": peak_mb,
                    "exit": process.returncode,
                    "watchdog_killed": False,
                    "elapsed_seconds": time.monotonic() - started,
                    "rules_fingerprint": fingerprint,
                    "baseline": baseline.name,
                    "memory_target_mb": 10 * 1024,
                },
                indent=2,
            )
        )
        print(
            f"Replay exit={process.returncode}; peak resident memory={peak_mb} MB",
            flush=True,
        )
        if process.returncode == 0 and os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write("completed=true\n")
        return process.returncode or int(peak_mb >= 10 * 1024)


if __name__ == "__main__":
    raise SystemExit(main())
