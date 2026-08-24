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
from headstart.ingest import PENDING_UPGRADES_PATH, REPO_ROOT, read_id_list
from headstart.search import DOC_PREFIX, MODEL

_log = log.get(__name__, __spec__)

_STORE = REPO_ROOT / "data" / "embeddings" / "jobs"
_FRAGMENTS = REPO_ROOT / "data" / "embeddings" / "fragments"
# Written by embed_plan; consumed here and by `index sync` (ADR-0050).
_UPGRADES = PENDING_UPGRADES_PATH
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
            log.fail(
                _log,
                f"prior store corrupt: {size} vector bytes for {n} rows (dim {dim})",
            )
    return n


def _fragment_ids(frags: list[Path]) -> set[str]:
    """Every id carried by the fragments that arrived — the ids a drop can safely be paired with.

    Reads only up to each fragment's last fully-parseable meta line, the same truncation the
    merge itself applies, so an id in a shard's half-written tail is not counted as having
    arrived when its vector will not be appended.
    """
    return {
        json.loads(line)["id"]
        for f in frags
        for line in _good_meta_lines(f / "meta.jsonl")
    }


def evict_ids(meta_path: Path, vec_path: Path, dim: int, ids: set[str]) -> int:
    """Drop ``ids`` from the store, rewriting meta and vectors in lockstep. Returns rows dropped.

    Runs before the merge so an upgraded Job (ADR-0050) ends the run with exactly one row. Leaving
    the stale one in place would be worse than untidy: ``index._load_store`` keys ``row_of`` last
    -wins and would pick the good vector, but ``embed_plan._prior_rows`` scans every line, so the
    old ``has_description: false`` row would keep marking the Job degraded and it would re-embed on
    every run forever.
    """
    if not ids or not meta_path.exists():
        return 0
    # Imported here, not at module scope: every other path in this module treats vectors as raw
    # bytes, which is what keeps it importable on CI's base-deps-only install (no numpy). A
    # top-level import broke collection of this module's own tests.
    import numpy as np

    vectors = np.fromfile(vec_path, dtype="float32").reshape(-1, dim)
    kept_meta: list[str] = []
    kept_rows: list[int] = []
    with meta_path.open(encoding="utf-8") as fh:
        for row, line in enumerate(fh):
            if json.loads(line)["id"] in ids:
                continue
            kept_meta.append(line)
            kept_rows.append(row)
    dropped = len(vectors) - len(kept_rows)
    if not dropped:
        return 0
    tmp_vec, tmp_meta = (
        vec_path.with_suffix(".f32.tmp"),
        meta_path.with_suffix(".jsonl.tmp"),
    )
    vectors[kept_rows].tofile(tmp_vec)
    tmp_meta.write_text("".join(kept_meta), encoding="utf-8")
    # Meta first, then vectors — the same order the store is *appended* in, inverted, and for the
    # same reason. `EmbeddingStore` writes vectors before their metadata so the vector file is
    # always at least as long as meta; shrinking has to shorten meta first to preserve that. The
    # other order leaves a window where vectors are short and meta is long, and a SIGTERM there
    # (merge has a 48 min job timeout) is exactly the state `_reconcile_store` refuses to open —
    # every later run's merge would die on "prior store corrupt" until a human repaired it.
    # Crashing between these two replaces now costs only re-dropping the vector rows, which
    # `EmbeddingStore`'s resume truncation already does.
    tmp_meta.replace(meta_path)
    tmp_vec.replace(vec_path)
    return dropped


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
    ap.add_argument(
        "--evict-ids",
        default=str(_UPGRADES),
        help="file of Job ids whose stale row to drop before merging — the embed planner's "
        "upgrade list (ADR-0050); missing or empty is a no-op",
    )
    ap.add_argument(
        "--expect-shards",
        type=int,
        default=0,
        help="how many shard fragments the embed planner fanned out; a shortfall is warned "
        "about rather than merged silently. 0 (default) disables the check",
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

    upgrades = Path(args.evict_ids)
    if dim is not None and upgrades.exists():
        upgrade_ids = read_id_list(upgrades)
        # An upgrade is a *replace*: drop the stale vector, merge the fresh one. Only the ids
        # that actually arrived get dropped, because the drop is only safe once its replacement
        # is in hand. Evicting the whole list instead is a delete for every id that did not come
        # back, and the Job then leaves the served index — `index._take_upgrades` removes its row
        # regardless, and `plan_sync` cannot re-add a Job with no vector.
        #
        # Both halves of that are reachable. `merge` runs `if: always()`, so it gets here with no
        # fragments at all whenever `embed` was skipped — which one failed scrape shard used to
        # cause, and on 2026-08-13 that cost 10,144 vectors and 11,083 served rows in one run.
        # And `embed` is `fail-fast: false` with a `continue-on-error` download, so 14 of 15
        # fragments is an ordinary outcome that would leak the same bug at a fifteenth the scale.
        # An id held back keeps its old vector and stays on the next run's upgrade list.
        stale = upgrade_ids & _fragment_ids(frags)
        held = len(upgrade_ids) - len(stale)
        if stale:
            dropped = evict_ids(meta_path, vec_path, dim, stale)
            _log.info(f"upgrades: dropped {dropped} stale rows for {len(stale)} ids")
        if held:
            _log.info(
                f"upgrades: holding {held} id(s) whose replacement did not arrive"
                f"{' (no fragments at all)' if not frags else ''}"
            )

    prior_rows = _reconcile_store(meta_path, vec_path, dim)
    _log.info(
        f"prior store: {prior_rows} vectors; {len(frags)} fragment(s) under {frag_root}"
    )
    # A fragment count with nothing to compare it against cannot answer the one question that
    # matters here: did every shard's vectors arrive? The download is `continue-on-error` and the
    # matrix is `fail-fast: false`, so 14 of 15 is an ordinary, green-looking outcome — and this
    # file's own upgrade comment records the day that shape cost 10,144 vectors and 11,083 served
    # rows. Those Docs are not lost forever (they carry no vector, so `plan_sync` cannot add them
    # and `embed_plan` re-plans them next run), but they are missing from the served index until
    # it comes round again, and nothing said so at the time.
    if args.expect_shards and len(frags) < args.expect_shards:
        _log.warning(
            f"only {len(frags)} of {args.expect_shards} shard fragment(s) arrived — the missing "
            "shard(s)' Docs are not in this merge and will not be indexed until a later run "
            "re-plans them"
        )
    elif not frags:
        # Distinct from the shortfall above: with no expectation passed we cannot call it a
        # shortfall, but zero fragments is never a healthy merge and used to read as `merged 0
        # new vectors` at info.
        _log.warning(
            f"no fragments under {frag_root} — nothing to merge; the embed stage produced "
            "nothing, was skipped, or its artifacts did not download"
        )

    appended = 0
    with meta_path.open("a", encoding="utf-8") as mf, vec_path.open("ab") as vf:
        for f in frags:
            fdim = _dim_from_manifest(f) or dim
            if fdim is None:
                log.fail(_log, f"fragment {f} has no manifest and no dim is known")
            good = _good_meta_lines(f / "meta.jsonl")
            nrows = len(good)
            want = nrows * fdim * _FLOAT_BYTES
            raw = (f / "embeddings.f32").read_bytes()
            if len(raw) < want:
                log.fail(
                    _log,
                    f"fragment {f} corrupt: {len(raw)} vector bytes for {nrows} rows (dim {fdim})",
                )
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
        log.fail(
            _log,
            f"store inconsistent after merge: {vbytes} bytes for {total} rows (dim {dim})",
        )
    _log.info(
        f"merged {appended} new vectors — store now holds {total} (dim {dim}) -> {store}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
