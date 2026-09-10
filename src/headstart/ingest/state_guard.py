#!/usr/bin/env python3
"""Refuse a write to HF state that another workflow has changed since we read it.

Two workflows write ``data/lancedb`` and neither holds a lock. The pipeline's ``merge`` job
uploads it additively; ``cleanup-index`` rebuilds it and uploads with ``--delete "*"``, which
reaps every remote file its own local folder does not contain. Both do the same three things:
read the table, spend two to four minutes changing it, upload — and **nothing between step one
and step three ever asks whether the base moved.** Last writer wins, and the loser's work is
gone with no error anywhere.

That is not hypothetical. On 2026-09-10 it cost a completed run's entire index write::

    08:22:32  cleanup-index: "no pipeline in flight — compacting"   (one had run since 07:36:48)
    08:26:51  cleanup-index reads the table          409,810
    08:27:55  pipeline merge reads the table         409,810        (same base, independently)
    08:28:30  pipeline merge writes                  410,516        (+706)
    08:29:49  pipeline uploads  -> 410,516 lands on HF
    08:30:27  cleanup-index uploads --delete "*"  -> 409,810 overwrites it.  -706

The next run opened at 409,810 — the value compaction rebuilt, not the one the pipeline wrote.
215 evictions were resurrected (43 never re-evicted, so closed postings kept being served) and
327 of that run's 922 adds were dropped (178 never re-added).

**Asking beforehand cannot fix this, which is why this module exists rather than a better poll.**
``cleanup-index`` already waits in a loop for the pipeline to finish, and it did — 30 of its 45
attempts — before reading zero. The query it polls,
``gh run list --workflow=pipeline.yml --status in_progress``, returns a *stale* empty set: measured
2026-09-10 over 239 polls at 4s, it reported 0 once while an unfiltered listing of the same repo,
issued milliseconds later, showed the run ``in_progress``. One observation, so treat 0.4% as an
order of magnitude — but across the 45 polls one attempt makes, even that rate is roughly a 1-in-6
chance of proceeding early, daily. A guard that reads liveness from that endpoint cannot be made
correct by widening its predicate, because the endpoint is what is wrong. Worse, the check is
answered minutes before the write it authorises: compaction checked at 08:22:32, read at 08:26:51
and wrote at 08:30:27, so even a perfect answer would have been eight minutes stale.

So the write itself detects the collision: **compare, then swap.** :func:`record` takes a
fingerprint of the remote prefix at fetch time; :func:`verify` retakes it immediately before the
upload and refuses if it moved.

**The fingerprint is content, not a commit sha** — which is what the first sketch of this reached
for, and it does not work here for two measured reasons. The pipeline publishes four or five
commits per run (embedding store, lancedb, description store, state), so the repo head always
differs between its own fetch and its own upload and a sha-based guard would refuse every run.
And the daily super-squash (ADR-0071) rewrites every sha in the repo without changing a byte of
content — verified 2026-09-10, when ``list_repo_commits`` returned a single
``Super-squash branch 'main'`` commit. Git blob ids are hashes of file content, so they survive
a squash unchanged and ignore commits that touch other prefixes entirely.

Cost is one Hub request. ``repo_info(files_metadata=True)`` answers the whole listing — 569
siblings, 360 of them under ``data/lancedb/``, all 360 carrying a ``blob_id`` — in ~1.2s
regardless of file count, which is why :func:`_siblings` is reused from ``state_fetch`` rather
than paging ``list_repo_tree``.

**Empty is a verdict, not an error.** A prefix with no files is a legitimate first run and
fingerprints as such. What must never be read as "unchanged" is the Hub declining to list at
all — ``state_fetch._siblings`` already fails closed on that, and a sibling missing its
``blob_id`` is refused here for the same reason: a fingerprint computed over unknown content
would compare equal to anything.

Run::

    python -m headstart.ingest.state_guard record data/lancedb --file data/.write_guard.json
    python -m headstart.ingest.state_guard verify data/lancedb --file data/.write_guard.json

``verify`` exits non-zero when the base moved, which is the point: the upload it guards is the
next line of the workflow step, so a red run replaces a silent overwrite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from headstart import log

# Imported across modules inside this package on purpose. `_siblings` is private to keep
# `remote_files`' name-only callers free of the size-bearing shape (see its docstring), not to
# forbid intra-package use — and it carries the fail-closed guard on a Hub that omits `siblings`,
# which this module must not reimplement: a fingerprint over an empty listing would compare
# equal to an empty prefix and wave through exactly the overwrite this file exists to stop.
from headstart.ingest.state_fetch import _siblings

_log = log.get(__name__, __spec__)

#: Where the fingerprint is parked between `record` and `verify`. Under `data/` but not under any
#: directory the pipeline uploads (`data/embeddings/jobs`, `data/lancedb`, `data/descriptions`,
#: `data/state` are each uploaded by path), so the guard never ships to the Hub as state.
DEFAULT_FILE = "data/.write_guard.json"


def _under(repo: str, prefix: str, token: str | None) -> dict[str, str]:
    """``{repo-relative path: blob id}`` for every file under ``prefix``, from one Hub request."""
    prefix = prefix.rstrip("/") + "/"
    out: dict[str, str] = {}
    for sibling in _siblings(repo, token):
        name = sibling.rfilename
        if not name.startswith(prefix):
            continue
        blob = getattr(sibling, "blob_id", None)
        if not blob:
            # Refused rather than skipped or defaulted. A file whose content we cannot identify
            # is a file whose change we cannot detect, and silently dropping it from the map
            # would make the digest stable across exactly the edit it is meant to catch.
            raise RuntimeError(
                f"Hub listed {name} without a blob_id — refusing to fingerprint content "
                "it will not identify"
            )
        out[name] = blob
    return out


def digest_of(files: dict[str, str]) -> str:
    """A stable digest of a path→blob-id map. Sorted, so listing order cannot move it."""
    joined = "\n".join(f"{path}:{blob}" for path, blob in sorted(files.items()))
    return hashlib.sha256(joined.encode()).hexdigest()


def fingerprint(repo: str, prefix: str, token: str | None) -> dict[str, object]:
    files = _under(repo, prefix, token)
    return {
        "repo": repo,
        "prefix": prefix.rstrip("/") + "/",
        "files": files,
        "count": len(files),
        "digest": digest_of(files),
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def record(path: Path, repo: str, prefix: str, token: str | None) -> int:
    fp = fingerprint(repo, prefix, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fp))
    _log.info(
        f"recorded {fp['prefix']} at {fp['count']} file(s), digest {str(fp['digest'])[:12]} "
        f"-> {path}"
    )
    return 0


def changes(
    before: dict[str, str], after: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """``(added, removed, modified)`` — what the other writer did, for the failure message.

    The counts alone would say a collision happened; these say what it was. A compaction's
    ``--delete "*"`` shows as a large ``removed``, an additive pipeline upload as ``added``.
    """
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    return added, removed, modified


def verify(path: Path, repo: str, prefix: str, token: str | None) -> int:
    if not path.exists():
        # Fail closed. Reaching an upload with no recorded base means `record` never ran or its
        # output was lost, and proceeding would be the unguarded write this module replaces.
        _log.error(
            f"no recorded base at {path} — refusing to upload {prefix} unverified"
        )
        return 1
    before = json.loads(path.read_text())
    now = fingerprint(repo, prefix, token)
    if before.get("digest") == now["digest"]:
        _log.info(
            f"{now['prefix']} unchanged since {before.get('at')} "
            f"({now['count']} file(s), digest {str(now['digest'])[:12]}) — safe to upload"
        )
        return 0
    added, removed, modified = changes(before.get("files", {}), now["files"])  # type: ignore[arg-type]
    _log.error(
        f"{now['prefix']} changed under this run since {before.get('at')}: "
        f"{len(added)} added, {len(removed)} removed, {len(modified)} modified "
        f"({before.get('count')} file(s) -> {now['count']}). Another workflow wrote here while "
        "this one was working; uploading would discard its write. Refusing."
    )
    for label, paths in (
        ("added", added),
        ("removed", removed),
        ("modified", modified),
    ):
        if paths:
            _log.info(f"  {label}: {log.named_sample(paths)}")
    return 1


def main() -> int:
    log.setup()
    log.context("state_guard")
    ap = argparse.ArgumentParser(
        description="Compare-and-swap guard for an HF state prefix."
    )
    ap.add_argument("action", choices=("record", "verify"))
    ap.add_argument(
        "prefix", help="repo-relative directory to guard, e.g. data/lancedb"
    )
    ap.add_argument(
        "--file",
        default=DEFAULT_FILE,
        help=f"fingerprint file (default {DEFAULT_FILE})",
    )
    args = ap.parse_args()
    repo = os.environ.get("HF_DATASET")
    if not repo:
        ap.error("no dataset repo — set HF_DATASET")
    token = os.environ.get("HF_TOKEN")
    run = record if args.action == "record" else verify
    return run(Path(args.file), repo, args.prefix, token)


if __name__ == "__main__":
    raise SystemExit(main())
