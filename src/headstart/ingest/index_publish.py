#!/usr/bin/env python3
"""Publish the LanceDB table and the grace set that belongs to it, in ONE commit (ADR-0083).

    python -m headstart.ingest.index_publish "$HF_DATASET"

``index sync`` writes ``unconfirmed_ids.txt`` for the table it just wrote. Uploaded apart — the
table in its own commit, the grace set later with the rest of ``data/state`` — a failure between
the two left the Hub pairing the new table with the previous run's set. An id absent, then
present, then absent again was then read as twice-absent and evicted on a single miss: the false
eviction ADR-0083 exists to prevent (run 35329189508 published the table, then lost ``data/state``
to a 403). One commit lands both or neither.

Additive, like the ``hf upload`` it replaces — no deletions, for the reason the workflow step
states. ``data/state`` still uploads the same file last; an identical file there is a no-op.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from headstart import log
from headstart.ingest import EVICTION_QUEUE_PATH, REPO_ROOT, UNCONFIRMED_PATH

_log = log.get(__name__, __spec__)

_TABLE = "data/lancedb"


def publish(repo: str, token: str | None, root: Path = REPO_ROOT) -> None:
    from huggingface_hub import CommitOperationAdd, HfApi
    from huggingface_hub.utils import DEFAULT_IGNORE_PATTERNS, filter_repo_objects

    table = root / _TABLE
    # The same file set `hf upload data/lancedb data/lancedb` commits: every file, minus the
    # library's own default ignores.
    files = filter_repo_objects(
        sorted(p.relative_to(root).as_posix() for p in table.rglob("*") if p.is_file()),
        ignore_patterns=DEFAULT_IGNORE_PATTERNS,
    )
    paths = list(files)
    # The grace set sync wrote for this table (ADR-0083), and the evictions it queued for Trends
    # (ADR-0227): the queue must reach the Hub whenever the table does, or a failed `data/state`
    # upload would lose the closures this run made.
    beside = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in (UNCONFIRMED_PATH, EVICTION_QUEUE_PATH)
    ]
    paths += [p for p in beside if (root / p).is_file()]
    HfApi(token=token).create_commit(
        repo_id=repo,
        repo_type="dataset",
        operations=[
            CommitOperationAdd(path_in_repo=p, path_or_fileobj=root / p) for p in paths
        ],
        commit_message="nightly: lancedb index + unconfirmed ids + eviction queue",
    )
    _log.info(
        f"published {len(paths)} file(s): {_TABLE}/ + {', '.join(beside)} in one commit"
    )


def main() -> int:
    log.setup()
    log.context("index_publish")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="the HF dataset to publish into")
    args = ap.parse_args()
    publish(args.repo, os.environ.get("HF_TOKEN"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
