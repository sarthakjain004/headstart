#!/usr/bin/env python3
"""Publish the LanceDB table and the grace set that belongs to it, in ONE commit (ADR-0083).

    python -m headstart.ingest.index_publish "$HF_DATASET"

``index sync`` writes ``unconfirmed_ids.txt`` for the table it just wrote. Uploaded apart — the
table in its own commit, the grace set later with the rest of ``data/state`` — a failure between
the two left the Hub pairing the new table with the previous run's set. An id absent, then
present, then absent again was then read as twice-absent and evicted on a single miss: the false
eviction ADR-0083 exists to prevent (run 35329189508 published the table, then lost ``data/state``
to a 403). One commit lands both or neither.

Additive, like the ``hf upload`` it replaces, for the reason the workflow step states — with one
exception (ADR-0244): the Search index files the table's latest version no longer references.
``refresh-indexes`` replaces every index each run and the old ones stayed on the Hub, so on
2026-09-26 the dataset held 391 index directories, 9.34 GB, of which the latest version read 17,
0.41 GB. Every merge and every Space boot downloaded the rest. This commit leaves them out and
deletes them. ``data/state`` still uploads the same file last; an identical file there is a no-op.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from headstart import log
from headstart.ingest import EVICTION_QUEUE_PATH, REPO_ROOT, UNCONFIRMED_PATH

_log = log.get(__name__, __spec__)

_TABLE = "data/lancedb"


def superseded_index_dirs(table_root: Path) -> set[str]:
    """``{table}.lance/_indices/{uuid}`` for each index directory under ``table_root`` that the
    table's latest version does not reference, relative to ``table_root`` (ADR-0244).

    Read from the manifest through ``lance``: every segment of every index the version serves,
    since one index can span several directories.
    A table that will not open yields nothing, so a publish never deletes on a read it could not
    make; nor does a table whose manifest names an index this copy does not hold.
    """
    superseded: set[str] = set()
    for table in sorted(table_root.glob("*.lance")):
        held = {d.name for d in (table / "_indices").glob("*") if d.is_dir()}
        if not held:
            continue
        import lance  # the index extra; only a table that holds indexes needs it

        try:
            referenced = {
                segment.uuid
                for index in lance.dataset(str(table)).describe_indices()
                for segment in index.segments
            }
        except Exception as exc:  # noqa: BLE001 - a table we cannot read keeps every index
            _log.warning(
                f"could not read {table.name}'s indexes ({exc}) — deleting none"
            )
            continue
        if not referenced <= held:
            _log.warning(
                f"{table.name}'s latest version names {len(referenced - held)} index(es) this "
                "copy does not hold — deleting none"
            )
            continue
        superseded |= {f"{table.name}/_indices/{uuid}" for uuid in held - referenced}
    return superseded


def publish(repo: str, token: str | None, root: Path = REPO_ROOT) -> None:
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi
    from huggingface_hub.utils import DEFAULT_IGNORE_PATTERNS, filter_repo_objects

    table = root / _TABLE
    # The same file set `hf upload data/lancedb data/lancedb` commits: every file, minus the
    # library's own default ignores.
    files = filter_repo_objects(
        sorted(p.relative_to(root).as_posix() for p in table.rglob("*") if p.is_file()),
        ignore_patterns=DEFAULT_IGNORE_PATTERNS,
    )
    # Superseded Search indexes neither go up nor stay up (ADR-0244).
    superseded = {f"{_TABLE}/{d}/" for d in superseded_index_dirs(table)}
    paths = [p for p in files if not p.startswith(tuple(superseded))]
    api = HfApi(token=token)
    doomed = (
        [
            f
            for f in api.list_repo_files(repo, repo_type="dataset")
            if f.startswith(tuple(superseded))
        ]
        if superseded
        else []
    )
    # The grace set sync wrote for this table (ADR-0083), and the evictions it queued for Trends
    # (ADR-0227): the queue must reach the Hub whenever the table does, or a failed `data/state`
    # upload would lose the closures this run made.
    beside = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in (UNCONFIRMED_PATH, EVICTION_QUEUE_PATH)
    ]
    included = [p for p in beside if (root / p).is_file()]
    paths += included
    # sync writes the grace set on every run, so a table published without it pairs the new
    # table with the previous run's set on the Hub — the false eviction this commit prevents.
    unconfirmed = UNCONFIRMED_PATH.relative_to(REPO_ROOT).as_posix()
    if unconfirmed not in included:
        _log.warning(
            f"{unconfirmed} absent — publishing the table without its grace set; the Hub keeps "
            "the previous run's, which the next sync will read as this table's (ADR-0083)"
        )
    size = sum((root / p).stat().st_size for p in paths)
    # Said before the commit too: a multi-GB upload runs for minutes, and a hang in it was
    # otherwise indistinguishable from the step never starting.
    _log.info(f"publishing {len(paths)} file(s), {size / 1e9:.2f} GB …")
    if doomed:
        _log.info(
            f"deleting {len(doomed)} file(s) of {len(superseded)} Search index(es) the table's "
            "latest version no longer references (ADR-0244)"
        )
    api.create_commit(
        repo_id=repo,
        repo_type="dataset",
        operations=[
            CommitOperationAdd(path_in_repo=p, path_or_fileobj=root / p) for p in paths
        ]
        + [CommitOperationDelete(path_in_repo=f) for f in doomed],
        commit_message="nightly: lancedb index + unconfirmed ids + eviction queue",
    )
    _log.info(
        f"published {len(paths)} file(s), {size / 1e9:.2f} GB: {_TABLE}/ + "
        f"{', '.join(included) or 'nothing beside it'} in one commit"
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
