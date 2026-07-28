#!/usr/bin/env python3
"""Fetch pipeline state from the HF dataset, failing loudly when it does not arrive.

    python -m headstart.ingest.state_fetch 'data/embeddings/jobs/*' 'data/lancedb/*'

``snapshot_download(local_dir=…)`` does **not** raise when the Hub is unreachable: it warns
(``Returning existing local_dir … as remote repo cannot be accessed``) and returns the local path.
Every state dir is gitignored, so on a CI runner that fallback yields an *empty* state — which is
indistinguishable, to everything downstream, from a legitimate first run.

That is how run 30304173982 (2026-07-27) lost its prior state to a transient ``429 Too Many
Requests``: the fetch step logged success in 1s (20s and 14s either side), ``embed_merge`` wrote a
fresh manifest over the 11,558 vectors it had just embedded instead of 324,485, and ``index sync``
bootstrapped an empty table. Only a *second* 429, on the upload 80s later, stopped a 95%-empty index
from replacing the served one. Nothing in the chain was wrong on its own; no step ever asked whether
the state it was building on had actually been fetched.

So ask. The remote listing is the missing fact, and it fails closed where the download does not:
``list_repo_files`` raises on a 429 rather than falling back. Requiring exactly what the Hub reports
also needs no bootstrap opt-out — a first run matches nothing, requires nothing, and proceeds.
Retries mirror ``up()`` in ``pipeline.yml``: transient Hub failures should cost a wait, not state.

Exit: 0 once every expected file is on disk, 1 when the state could not be fetched (ADR-0030).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from fnmatch import fnmatch
from pathlib import Path

from headstart.ingest import REPO_ROOT

_ATTEMPTS = 3
_BACKOFF = (
    30  # seconds, multiplied by the attempt number — 30s then 60s, as `up()` does
)


def remote_matches(remote_files: list[str], patterns: list[str]) -> set[str]:
    """The repo-relative files the Hub reports for these patterns. ``fnmatch`` is what
    ``snapshot_download`` filters ``allow_patterns`` with, so ``*`` spans ``/`` here too — that is
    what lets ``data/lancedb/*`` reach the table's nested fragment files."""
    return {f for f in remote_files if any(fnmatch(f, p) for p in patterns)}


def absent_locally(wanted: set[str], root: str | Path) -> list[str]:
    """Which wanted files are not on disk under ``root`` — empty means the fetch landed."""
    return sorted(f for f in wanted if not (Path(root) / f).exists())


def fetch_state(repo: str, patterns: list[str], token: str | None) -> int:
    from huggingface_hub import list_repo_files, snapshot_download

    for attempt in range(1, _ATTEMPTS + 1):
        try:
            wanted = remote_matches(
                list_repo_files(repo, repo_type="dataset", token=token), patterns
            )
            snapshot_download(
                repo,
                repo_type="dataset",
                local_dir=str(REPO_ROOT),
                allow_patterns=patterns,
                token=token,
            )
            absent = absent_locally(wanted, REPO_ROOT)
            if not absent:
                print(
                    f"fetched {len(wanted)} file(s): {' '.join(patterns)}", flush=True
                )
                return 0
            reason = (
                f"{len(absent)} of {len(wanted)} expected file(s) did not land, "
                f"e.g. {absent[0]}"
            )
        except Exception as exc:  # noqa: BLE001 — any Hub failure is retried the same way
            reason = f"{type(exc).__name__}: {exc}"
        if attempt < _ATTEMPTS:
            print(
                f"::warning::state fetch attempt {attempt} failed ({reason}); "
                f"retrying in {attempt * _BACKOFF}s",
                flush=True,
            )
            time.sleep(attempt * _BACKOFF)

    print(
        f"[state_fetch] ABORT: could not fetch {' '.join(patterns)} from {repo} — {reason}.\n"
        "Refusing to continue: the state dirs are gitignored, so proceeding would rebuild and "
        "publish from an empty store as if this were a first run.",
        file=sys.stderr,
        flush=True,
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "patterns", nargs="+", help="allow_patterns to fetch (repo-relative globs)"
    )
    args = ap.parse_args()
    repo = os.environ.get("HF_DATASET")
    if not repo:
        ap.error("no dataset repo — set HF_DATASET")
    return fetch_state(repo, args.patterns, os.environ.get("HF_TOKEN"))


if __name__ == "__main__":
    raise SystemExit(main())
