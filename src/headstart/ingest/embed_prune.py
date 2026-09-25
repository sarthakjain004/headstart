#!/usr/bin/env python3
"""Drop the embedding store's rows for Jobs the index no longer serves (ADR-0190).

``embed_merge`` appends each run's vectors to ``data/embeddings/jobs`` and removes only a row it
is replacing (ADR-0050). ``index sync`` and ``index prune`` evict closed and off-Board Jobs from the
served table, but nothing took their vectors out of the store, so it only ever grew: on
2026-09-24 it held 1,020,676 vectors against 520,566 served rows. Every merge job downloads the
whole store and re-uploads it whole, so that dead half cost ~1.9 GB each way on every run.

A row is kept when its id is in the served table or in this run's tech corpus, and dropped
otherwise. The corpus half matters: a Job scraped this run that ``prune`` just took out of the
table would otherwise lose its vector, be re-planned by the next ``embed_plan``, re-added by
``sync`` and pruned again — one re-embed per run, forever.

The price is that a Job evicted and later seen again is re-embedded rather than re-added from a
retained vector. Across the five runs of 2026-09-24 that was 43-150 adds a run.

Run after ``index prune --apply`` and before the store is uploaded. Dry-run by default; ``--apply``
rewrites the store.

Run: python -m headstart.ingest.embed_prune [--apply]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import lancedb

from headstart import log
from headstart.board_identity import ats_of
from headstart.corpus import iter_jobs
from headstart.embedding_conventions import PROD_TABLE
from headstart.ingest import REPO_ROOT, observability
from headstart.ingest.embed_merge import _FLOAT_BYTES, _dim_from_manifest, evict_ids
from headstart.ingest.index import _all_ids, check_base

_log = log.get(__name__, __spec__)

_STORE = REPO_ROOT / "data" / "embeddings" / "jobs"
_DB = REPO_ROOT / "data" / "lancedb"
_SOURCE = REPO_ROOT / "data" / "jobs" / "tech"


def _served_ids(db: Path) -> set[str] | None:
    """Every id in the served table, or ``None`` when there is no table to trust."""
    conn = lancedb.connect(db)
    if PROD_TABLE not in conn.list_tables().tables:
        return None
    return set(_all_ids(conn.open_table(PROD_TABLE)))


def main() -> int:
    log.setup()
    log.context("embed_prune")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--store", default=str(_STORE), help="default: data/embeddings/jobs"
    )
    ap.add_argument("--db", default=str(_DB), help="default: data/lancedb")
    ap.add_argument(
        "--source",
        default=str(_SOURCE),
        help="this run's tech corpus, whose ids are kept too (default: data/jobs/tech)",
    )
    ap.add_argument(
        "--apply", action="store_true", help="rewrite the store (default: dry run)"
    )
    args = ap.parse_args()

    store = Path(args.store)
    meta_path, vec_path = store / "meta.jsonl", store / "embeddings.f32"
    manifest_path = store / "manifest.json"
    if not manifest_path.exists():
        _log.info(f"no store at {store} — nothing to prune")
        return 0

    served = _served_ids(Path(args.db))
    # An empty or missing table is never evidence that every vector is dead: refuse rather than
    # empty the store, which would re-embed the whole corpus.
    if not served:
        _log.error(
            f"ABORT: no served rows in table '{PROD_TABLE}' at {args.db}; refusing to prune"
        )
        return 1
    # The table must be the one `prune` just left, or this would prune against a rolled-back one.
    if not check_base(args.db, len(served)):
        return 1
    corpus_ids = {job["id"] for job in iter_jobs(args.source)}
    keep = served | corpus_ids

    with meta_path.open(encoding="utf-8") as fh:
        stored = [json.loads(line)["id"] for line in fh if line.strip()]
    drop = set(stored) - keep
    by_ats = Counter(ats_of(job_id) for job_id in drop)
    ranked = ", ".join(f"{ats} {n}" for ats, n in by_ats.most_common(5))
    _log.info(
        f"store: {len(stored)} vectors | {len(served)} served + {len(corpus_ids)} corpus ids "
        f"-> keep {len(stored) - len(drop)}, drop {len(drop)}"
        + (f" ({ranked})" if drop else "")
    )
    if not args.apply:
        _log.info("dry-run — pass --apply to rewrite the store")
        return 0

    dim = _dim_from_manifest(store)
    dropped = evict_ids(meta_path, vec_path, dim, drop)
    # Counted from the rewritten meta, not `len(stored) - dropped`: `evict_ids` also counts any
    # vector rows past the last meta line as dropped, so the subtraction would come out short.
    with meta_path.open(encoding="utf-8") as fh:
        remaining = sum(1 for line in fh if line.strip())
    manifest = json.loads(manifest_path.read_text())
    manifest["count"] = remaining
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if vec_path.stat().st_size != remaining * dim * _FLOAT_BYTES:
        log.fail(
            _log,
            f"store inconsistent after prune: {vec_path.stat().st_size} bytes for {remaining} rows",
        )
    _log.info(
        f"done: dropped {dropped} vectors; store now holds {remaining} -> {store}"
    )
    observability.summary(
        "Embedding store prune",
        [
            f"- dropped **{dropped:,}** vectors of unserved Jobs; store holds **{remaining:,}**"
        ],
    )
    return 0


if __name__ == "__main__":
    log.run_logging_crash(
        _log,
        main,
        "embed_prune failed — the store keeps its unserved vectors this run and uploads "
        "larger, not wrong",
    )
