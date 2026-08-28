#!/usr/bin/env python3
"""A published record of which state directories exist, so an empty fetch can be told apart
from a first run.

    python -m headstart.ingest.state_witness publish

`state_fetch` (ADR-0030) asks the Hub what files exist and asserts they landed, which closes the
case where the listing *fails*. It cannot close the case where the listing *succeeds and matches
nothing* — that is a genuine first run, and it is also a mistyped or emptied ``HF_DATASET``. The
ADR says so plainly and leaves the hole open: closing it "needs a witness that survives a failed
fetch, and the obvious candidate (the committed liveness ledger) is present on a fresh fork too,
so it would reject exactly the bootstrap this allows."

A file published *only into the dataset* is that witness. Every state root is gitignored
(`.gitignore:56-62`), so this file is absent from a fresh checkout and present on a repo the
pipeline has ever written — exactly the asymmetry the liveness ledger lacked.

**It can only ever under-claim.** `publish` records the roots that exist locally and hold at least
one file, at the moment of upload, from one writer. A root it omits costs nothing: the fetch
behaves as it does today. A root it wrongly claimed would fail every later fetch closed, which is
an outage rather than a degradation — so nothing here ever unions in a root it did not just see.
That asymmetry is the whole reason this is a *presence* witness and not a file manifest: a manifest
would have to stay exact across all seven of the dataset's write points to avoid that outage.

Reading it costs **zero Hub API-bucket requests** — measured 2026-08-28, eight `hf_hub_download`
calls of known paths against `ratelimit: "api";r=…` moved the counter not at all, because a
``.../resolve/...`` fetch is metered in the Resolvers bucket. It is also read only when the
listing matched nothing, so the happy path spends nothing at all. See ADR-0095.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from headstart import log
from headstart.ingest import REPO_ROOT

_log = log.get(__name__, __spec__)

# Where the witness lives *in the dataset*. Under `data/state/` so the pipeline's existing
# `data/state` upload carries it — no new commit, no new write point to keep in lockstep.
# Deliberately not named `manifest.json`: that name is already taken twice, by the embedding
# store's dim/count manifest and by the role-centroid provenance manifest, and neither describes
# files (CLAUDE.md's rule on near-homograph names).
WITNESS_PATH = "data/state/published_dirs.json"

# The roots the pipeline's `merge` job uploads, and the only things this witness speaks about.
# `data/state/role_centroids` is deliberately absent: `cluster-roles.yml` writes it on its own
# schedule, so a run that never touches it must not be read as having lost it.
ROOTS: tuple[str, ...] = (
    "data/descriptions",
    "data/embeddings/jobs",
    "data/lancedb",
    "data/state",
)


def pattern_root(pattern: str) -> str:
    """The directory a fetch pattern draws from — everything before the last ``/``.

    ``data/state/*`` and ``data/embeddings/jobs/meta.jsonl`` both name a root this witness knows;
    ``data/state/role_centroids/*`` names one it does not, and abstaining there is the point.
    """
    return pattern.rsplit("/", 1)[0] if "/" in pattern else pattern


def holds_files(directory: Path) -> bool:
    """Whether a directory exists and holds at least one file, at any depth."""
    return directory.is_dir() and any(p.is_file() for p in directory.rglob("*"))


def witnessed(root: Path = REPO_ROOT) -> list[str]:
    """The roots that exist under ``root`` and hold files — what `publish` is about to record."""
    return [r for r in ROOTS if holds_files(root / r)]


def published_roots(repo: str, token: str | None) -> set[str] | None:
    """The roots the dataset says it holds, or ``None`` when it carries no witness at all.

    ``None`` is the first-run answer and the only one that permits a bootstrap. Every other
    failure — an unreachable Hub, a missing repo — propagates, because a witness we could not read
    is not a witness that says nothing. That is the same fail-closed stance `remote_files` takes,
    and the reason this uses ``hf_hub_download`` rather than ``snapshot_download``: the former
    raises where the latter warns and hands back an empty directory (ADR-0030).
    """
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError

    try:
        path = hf_hub_download(repo, WITNESS_PATH, repo_type="dataset", token=token)
    except EntryNotFoundError:
        return None
    return set(json.loads(Path(path).read_text(encoding="utf-8"))["dirs"])


def unwitnessed(patterns: list[str], roots: set[str] | None) -> list[str]:
    """Roots these patterns draw from that the dataset claims to hold — empty when it is silent.

    Called only once the listing has matched nothing, so a non-empty answer means the Hub is
    reporting an empty repo that the pipeline itself last recorded as full.
    """
    if not roots:
        return []
    return sorted({pattern_root(p) for p in patterns} & roots)


def publish(root: Path = REPO_ROOT) -> list[str]:
    """Write the witness next to the state it witnesses. Returns the roots recorded."""
    dirs = witnessed(root)
    path = root / WITNESS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"dirs": dirs}, indent=2) + "\n", encoding="utf-8")
    _log.info(f"witness: {len(dirs)} published dir(s) — {' '.join(dirs) or '(none)'}")
    return dirs


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["publish"])
    ap.add_argument("--root", default=str(REPO_ROOT), help="repo root to record from")
    args = ap.parse_args()
    publish(Path(args.root))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
