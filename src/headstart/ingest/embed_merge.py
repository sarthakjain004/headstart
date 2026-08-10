#!/usr/bin/env python3
"""Merge embed-shard fragments into the store — the single-writer merge of ADR-0025 Phase 1.

Each embed shard wrote a fragment (``embeddings.f32`` + ``meta.jsonl`` + ``manifest.json``) into
its own dir; the merge job downloads them all and this concatenates them onto the prior store the
merge job pulled from the dataset. It is a pure append: the store is row-major and append-only with
the id carried in each meta row (ADR-0004), and the planner made the shards' ids disjoint from each
other and from the prior store, so there is nothing to reconcile between shards — only to stack.

Two integrity guards, mirroring the crash-safe store (ADR-0004):
- **Per-fragment reconcile.** A shard killed by its safety-net ``timeout`` mid-batch can leave a
  half-written final meta line and one extra vector row. Each fragment is read only up to its last
  fully-parseable meta line, and only that many vector rows are appended — the same truncation
  ``EmbeddingStore._reconcile`` does on ``--resume``. The dropped Docs simply reappear as "new" in
  the next run's plan.
- **Whole-store consistency check** at the end (vector bytes == rows × dim × 4), so ``index sync``
  never opens a torn store.

After this the merge job runs ``index sync`` → ``index prune`` → ``index compact`` → upload →
restart.

Run: python -m headstart.ingest.embed_merge [--store DIR] [--fragments DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from headstart import log
from headstart.ingest import REPO_ROOT
from headstart.search import DOC_PREFIX, MODEL

_log = log.get(__name__, __spec__)

_STORE = REPO_ROOT / "data" / "embeddings" / "jobs"
_FRAGMENTS = REPO_ROOT / "data" / "embeddings" / "fragments"
_FLOAT_BYTES = 4  # float32


def _dim_from_manifest(path: Path) -> int | None:
    manifest = path / "manifest.json"
    if not manifest.exists():
        return None
    return int(json.loads(manifest.read_text())["dim"])


def _fragment_dirs(root: Path) -> list[Path]:
    """Fragment dirs under ``root`` (each holding an ``embeddings.f32``), or ``[root]`` if it is
    itself one fragment. Sorted for deterministic merge order (order is irrelevant to sync, which
    keys on id, but determinism keeps runs reproducible)."""
    if (root / "embeddings.f32").exists():
        return [root]
    return sorted(
        d for d in root.iterdir() if d.is_dir() and (d / "embeddings.f32").exists()
    )


def _good_meta_lines(meta_path: Path) -> list[str]:
    """Meta lines up to the first unparseable one — a shard killed mid-batch leaves a partial tail."""
    good: list[str] = []
    with meta_path.open(encoding="utf-8") as fh:
        for line in fh:
            s = line.rstrip("\n")
            if not s:
                continue
            try:
                json.loads(s)
            except json.JSONDecodeError:
                break
            good.append(s)
    return good


def _reconcile_store(meta_path: Path, vec_path: Path, dim: int | None) -> int:
    """Align the prior store before appending (it should already be clean; be safe on top of any
    state). Returns its row count; truncates a too-long vector tail, fails on a too-short one."""
    if not meta_path.exists():
        return 0
    good = _good_meta_lines(meta_path)
    n = len(good)
    if n < sum(1 for line in meta_path.open(encoding="utf-8") if line.strip()):
        meta_path.write_text(
            "".join(s + "\n" for s in good), encoding="utf-8"
        )  # drop the bad tail
    if dim is not None and vec_path.exists():
        want = n * dim * _FLOAT_BYTES
        size = vec_path.stat().st_size
        if size > want:
            with vec_path.open("r+b") as vf:
                vf.truncate(want)  # drop the extra in-flight vector row(s)
        elif size < want:
            _log.error(
                f"prior store corrupt: {size} vector bytes for {n} rows (dim {dim})"
            )
            raise SystemExit(1)
    return n


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--store",
        default=str(_STORE),
        help="the store to append into (default: data/embeddings/jobs)",
    )
    ap.add_argument(
        "--fragments",
        default=str(_FRAGMENTS),
        help="dir of shard fragment dirs (default: data/embeddings/fragments)",
    )
    args = ap.parse_args()

    store = Path(args.store)
    store.mkdir(parents=True, exist_ok=True)
    meta_path = store / "meta.jsonl"
    vec_path = store / "embeddings.f32"

    frag_root = Path(args.fragments)
    frags = _fragment_dirs(frag_root) if frag_root.exists() else []

    # dim: prior store first, else the first fragment that has a manifest.
    dim = _dim_from_manifest(store)
    for f in frags:
        if dim is not None:
            break
        dim = _dim_from_manifest(f)

    prior_rows = _reconcile_store(meta_path, vec_path, dim)
    _log.info(
        f"prior store: {prior_rows} vectors; {len(frags)} fragment(s) under {frag_root}"
    )

    appended = 0
    with meta_path.open("a", encoding="utf-8") as mf, vec_path.open("ab") as vf:
        for f in frags:
            fdim = _dim_from_manifest(f) or dim
            if fdim is None:
                _log.error(f"fragment {f} has no manifest and no dim is known")
                raise SystemExit(1)
            good = _good_meta_lines(f / "meta.jsonl")
            nrows = len(good)
            want = nrows * fdim * _FLOAT_BYTES
            raw = (f / "embeddings.f32").read_bytes()
            if len(raw) < want:
                _log.error(
                    f"fragment {f} corrupt: {len(raw)} vector bytes for {nrows} rows (dim {fdim})"
                )
                raise SystemExit(1)
            vf.write(
                raw[:want]
            )  # trim any in-flight partial row past the last good meta line
            vf.flush()
            mf.write("".join(s + "\n" for s in good))
            mf.flush()
            appended += nrows
            _log.info(f"+{nrows} from {f.name} (running total {prior_rows + appended})")

    total = prior_rows + appended
    if dim is None:  # no prior store and no fragments — a degenerate empty first run
        _log.info("no store and no fragments — nothing to merge")
        return 0

    (store / "manifest.json").write_text(
        json.dumps(
            {
                "model": MODEL,
                "dim": int(dim),
                "doc_prefix": DOC_PREFIX,
                "normalized": True,
                "source": "sharded-merge (ADR-0025)",
                "vectors_file": "embeddings.f32",
                "dtype": "float32",
                "count": total,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    vbytes = vec_path.stat().st_size
    if vbytes != total * dim * _FLOAT_BYTES:
        _log.error(
            f"store inconsistent after merge: {vbytes} bytes for {total} rows (dim {dim})"
        )
        raise SystemExit(1)
    _log.info(
        f"merged {appended} new vectors — store now holds {total} (dim {dim}) -> {store}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
