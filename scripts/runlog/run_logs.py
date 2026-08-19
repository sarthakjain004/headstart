#!/usr/bin/env python3
"""Fetch and clean an Actions run's job logs — the shared surface every analyser here reads.

**Why logs and not the shard reports.** `headstart.ingest.observability.write_shard` already
records each shard's numbers as JSON — predicted vs actual, retry classes, the error map, the
*names* of deferred boards — which is strictly richer than what the log lines carry. It is not
what these scripts read, for one measured reason: that JSON rides inside the shard's fragment
artifact, each of which is ~70 MB, and the Actions artifact API has no partial extraction. Reading
15 shards' telemetry means downloading ~1 GB to reach a few KB. The log line is the cheap surface,
so it is the one these tools use. Reach for the artifacts when you need a field the logs drop
(deferred board names, the full error map) and accept the download.

**The echo filter is the whole reason this module exists.** `set -x` plus GitHub's `[36;1m`-wrapped
source echo means the workflow's own script text appears in every job log. A grep for
`degrading to direct` matched 15/15 shards when the truth was 4/15, and `walled the current IP`
looked like a new phenomenon when it was only new *logging*. Every read goes through
:func:`clean`, which drops those lines before anything else sees them. Do not bypass it.

Used as a library by the analysers beside it:

    from run_logs import Run
    run = Run(32272854468)
    for shard, text in run.stage("scrape"):
        ...

Also runs standalone to dump a stage's cleaned log:

    python scripts/runlog/run_logs.py 32272854468 --stage scrape --shard 13
    python scripts/runlog/run_logs.py 32272854468 --list
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO = "sarthakjain004/headstart"

# GitHub wraps echoed workflow source in this SGR sequence. Drop those lines before reading
# anything, or you measure the script text instead of the run.
ECHO = "\x1b[36;1m"

_SHARDED = re.compile(r"^(.*?) \((\d+)\)$")
# Strip the ISO timestamp GitHub prefixes to every raw line, keeping the app's own clock.
_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")


def gh(path: str) -> str:
    out = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        sys.exit(f"gh api {path} failed: {out.stderr.strip()}")
    return out.stdout


def clean(text: str) -> str:
    """Drop the echoed workflow source. Every log read in this package goes through here."""
    return "".join(ln for ln in text.splitlines(keepends=True) if ECHO not in ln)


def unstamp(line: str) -> str:
    return _STAMP.sub("", line).rstrip()


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Run:
    """One Actions run's jobs and their cleaned logs, fetched lazily and cached on disk."""

    def __init__(self, run_id: int, repo: str = REPO, cache: Path | None = None):
        self.id = run_id
        self.repo = repo
        self.cache = cache or Path(tempfile.gettempdir()) / f"runlog-{run_id}"
        self.cache.mkdir(parents=True, exist_ok=True)
        meta = json.loads(gh(f"repos/{repo}/actions/runs/{run_id}"))
        self.head = meta["head_sha"][:7]
        self.created = meta["created_at"]
        self.jobs = [j for j in self._jobs() if j["started_at"] and j["completed_at"]]

    def _jobs(self) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            batch = json.loads(
                gh(
                    f"repos/{self.repo}/actions/runs/{self.id}/jobs?per_page=100&page={page}"
                )
            )["jobs"]
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    @staticmethod
    def stage_of(name: str) -> str:
        m = _SHARDED.match(name)
        return m.group(1) if m else name

    @staticmethod
    def shard_of(name: str) -> int | None:
        m = _SHARDED.match(name)
        return int(m.group(2)) if m else None

    def seconds(self, job: dict) -> float:
        return (ts(job["completed_at"]) - ts(job["started_at"])).total_seconds()

    def wall_minutes(self) -> float:
        return (
            max(ts(j["completed_at"]) for j in self.jobs)
            - min(ts(j["started_at"]) for j in self.jobs)
        ).total_seconds() / 60

    def log(self, job: dict) -> str:
        path = self.cache / f"{job['id']}.log"
        if not path.exists():
            path.write_text(
                clean(gh(f"repos/{self.repo}/actions/jobs/{job['id']}/logs"))
            )
        return path.read_text()

    def stage_jobs(self, stage: str) -> list[dict]:
        jobs = [j for j in self.jobs if self.stage_of(j["name"]) == stage]
        return sorted(jobs, key=lambda j: self.shard_of(j["name"]) or 0)

    def stage(self, stage: str, workers: int = 8) -> list[tuple[int | None, dict, str]]:
        """`(shard, job, cleaned_log)` for a stage, fetched concurrently, shard-ordered.

        Concurrent with `as_completed` rather than a blocking map so one slow log cannot hold
        up the rest, per the repo's streaming rule.
        """
        jobs = self.stage_jobs(stage)
        got: list[tuple[int | None, dict, str]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.log, j): j for j in jobs}
            for fut in as_completed(futures):
                job = futures[fut]
                got.append((self.shard_of(job["name"]), job, fut.result()))
        return sorted(got, key=lambda r: (r[0] is None, r[0] or 0))

    def stages(self) -> list[str]:
        seen: list[str] = []
        for j in sorted(self.jobs, key=lambda j: ts(j["started_at"])):
            s = self.stage_of(j["name"])
            if s not in seen:
                seen.append(s)
        return seen


def resolve_ids(args: argparse.Namespace) -> list[int]:
    """Run ids from positionals and/or `--latest`, in the order given."""
    ids = list(args.run_ids)
    if args.latest:
        runs = json.loads(
            gh(
                f"repos/{args.repo}/actions/workflows/{args.latest}/runs?per_page={args.n}"
            )
        )["workflow_runs"]
        ids += [r["id"] for r in runs]
    return ids


def common_args(description: str) -> argparse.ArgumentParser:
    """The argument surface every analyser in this package shares."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument(
        "run_ids", nargs="*", type=int, help="run ids; more than one compares them"
    )
    ap.add_argument(
        "--latest", metavar="WORKFLOW", help="newest runs of this workflow file"
    )
    ap.add_argument(
        "-n", type=int, default=1, help="how many with --latest (default 1)"
    )
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--cache-dir", help="reuse downloaded logs across invocations")
    return ap


def runs_from(args: argparse.Namespace) -> list[Run]:
    ids = resolve_ids(args)
    if not ids:
        sys.exit("give at least one run id, or --latest WORKFLOW")
    cache = Path(args.cache_dir) if args.cache_dir else None
    return [Run(i, args.repo, cache / str(i) if cache else None) for i in ids]


def main() -> None:
    ap = common_args(__doc__.split("\n")[0])
    ap.add_argument("--stage", help="dump this stage's cleaned log")
    ap.add_argument("--shard", type=int, help="restrict --stage to one shard")
    ap.add_argument("--list", action="store_true", help="list stages and jobs")
    args = ap.parse_args()

    for run in runs_from(args):
        print(f"\n===== run {run.id} head={run.head} =====", flush=True)
        if args.list or not args.stage:
            for s in run.stages():
                jobs = run.stage_jobs(s)
                secs = [run.seconds(j) for j in jobs]
                print(
                    f"  {s:14} n={len(jobs):<3} max={max(secs) / 60:6.1f}m "
                    f"Σ={sum(secs) / 60:7.1f}m",
                    flush=True,
                )
            continue
        for shard, _job, text in run.stage(args.stage):
            if args.shard is not None and shard != args.shard:
                continue
            print(f"--- {args.stage} shard {shard} ---", flush=True)
            for line in text.splitlines():
                print(unstamp(line), flush=True)


if __name__ == "__main__":
    main()
