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
import uuid
from pathlib import Path

from headstart import log
from headstart.ingest import EVICTION_QUEUE_PATH, REPO_ROOT, UNCONFIRMED_PATH
from headstart.ingest.state_fetch import remote_files

_log = log.get(__name__, __spec__)

_TABLE = "data/lancedb"


def _latest_manifest(table: Path) -> Path | None:
    """The manifest of ``table``'s latest version: Lance's V2 names count *down* from 2**64 - 1,
    so the latest is the smallest; V1 names count up, so it is the largest."""
    versions = {
        int(p.stem): p
        for p in (table / "_versions").glob("*.manifest")
        if p.stem.isdigit()
    }
    if not versions:
        return None
    v2 = min(versions) >= 2**63
    return versions[min(versions) if v2 else max(versions)]


def superseded_index_dirs(root: Path) -> set[str]:
    """``data/lancedb/{table}.lance/_indices/{uuid}/``, relative to ``root``, for each index
    directory the table's latest version does not reference (ADR-0244).

    A directory is referenced when its uuid's 16 bytes appear in the latest manifest, which
    carries every index segment the version serves. Read as bytes because the pipeline installs
    ``lancedb`` without ``pylance``, the only Python reader of a manifest's index section.
    Conservative by construction: on 8 of the Hub's manifests (2026-09-26) the rule found every
    segment ``lance`` lists and at most one more, so it can keep an index but not lose one. A
    table whose manifest finds fewer directories than it has indexes deletes nothing.
    """
    import lancedb

    superseded: set[str] = set()
    skipped: list[str] = []
    for table in sorted((root / _TABLE).glob("*.lance")):
        held = {d.name for d in (table / "_indices").glob("*") if d.is_dir()}
        manifest = _latest_manifest(table)
        if not held or manifest is None:
            continue
        raw = manifest.read_bytes()
        referenced = {d for d in held if _referenced(d, raw)}
        try:
            indexes = (
                lancedb.connect(str(table.parent)).open_table(table.stem).list_indices()
            )
        except Exception as exc:  # noqa: BLE001 - a table we cannot read keeps every index
            skipped.append(f"{table.name} ({exc})")
            continue
        if len(referenced) < len(indexes):
            skipped.append(
                f"{table.name} ({len(indexes)} index(es), {len(referenced)} found in {manifest.name})"
            )
            continue
        superseded |= {
            f"{_TABLE}/{table.name}/_indices/{d}/" for d in held - referenced
        }
    if skipped:
        _log.warning(
            f"deleting no superseded index of {log.named_sample(skipped)} — could not tell "
            "which indexes the latest version serves (ADR-0244)"
        )
    return superseded


def _referenced(name: str, manifest: bytes) -> bool:
    """Whether the manifest names this ``_indices/`` directory; one not named by a uuid is kept."""
    try:
        return uuid.UUID(name).bytes in manifest
    except ValueError:
        return True


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
    superseded = tuple(superseded_index_dirs(root))
    paths = [p for p in files if not p.startswith(superseded)]
    remote_superseded = (
        [f for f in remote_files(repo, token) if f.startswith(superseded)]
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
    if remote_superseded:
        _log.info(
            f"deleting {len(remote_superseded)} file(s) of {len(superseded)} Search index(es) the table's "
            "latest version no longer references (ADR-0244)"
        )
    HfApi(token=token).create_commit(
        repo_id=repo,
        repo_type="dataset",
        operations=[
            CommitOperationAdd(path_in_repo=p, path_or_fileobj=root / p) for p in paths
        ]
        + [CommitOperationDelete(path_in_repo=f) for f in remote_superseded],
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
