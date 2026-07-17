"""Sync the production ``jobs`` LanceDB table from the embedding store (ADR-0014, ADR-0019).

Reads the committed store under ``data/embeddings/jobs/`` (``embeddings.f32`` + ``meta.jsonl`` +
``manifest.json``) and the current corpus snapshot (default ``data/jobs/tech/``), then reconciles
the index incrementally via ``index_sync``: fresh ids are the corpus ids that have a vector, and the
scraped-Board set is taken from the *full* scrape (``data/jobs/``), not the tech subset — so a Board
that was scraped but dropped to zero *tech* jobs still has its closed postings evicted (a Board only
in the tech snapshot would leave those rows stranded). A posting that vanished from a scraped Board
is evicted; Boards absent from the scrape are never touched (partial-harvest safety). On the first
run the table is created empty and the plan is all-add; the identical path does true incremental
add/evict on every later run — no overwrite-rebuild (ADR-0019).

Corpus ids without a vector (non-English, or not yet embedded) are reported and skipped — run
``embed_jobs.py --resume`` first to close that gap.

Run:  python scripts/embed/embed_jobs.py --resume && python scripts/embed/sync_index.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

from headstart.corpus import board_of, iter_jobs
from headstart.index_sync import apply_sync, plan_sync
from headstart.search import PROD_TABLE

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "data" / "embeddings" / "jobs"
_SOURCE = _ROOT / "data" / "jobs" / "tech"
_SCRAPED = _ROOT / "data" / "jobs"  # full (pre-tech-filter) scrape — the true scraped-Board set
_DB = _ROOT / "data" / "lancedb"

_ADD_CHUNK = 2048  # rows per add batch — bounds peak memory and streams progress


# One row per Job: canonical typed metadata (ADR-0007) + inline experience numbers (ADR-0019) +
# the vector. min_years/max_years are nullable ints — null for the Jobs no number was found for.
def _schema(dim: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("ats", pa.string()),
            pa.field("company", pa.string()),
            pa.field("title", pa.string()),
            pa.field("location", pa.string()),
            pa.field("remote", pa.bool_()),
            pa.field("employment_type", pa.string()),
            pa.field("experience", pa.string()),  # raw string for display ("5+")
            pa.field("min_years", pa.int32()),  # parsed, filterable
            pa.field("max_years", pa.int32()),
            pa.field(
                "experience_source", pa.string()
            ),  # "field" | "regex" | "seniority" | null
            pa.field("salary", pa.string()),  # raw string for display (ADR-0019)
            pa.field("department", pa.string()),
            pa.field("url", pa.string()),
            pa.field("posted_at", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
        ]
    )


def _load_store() -> tuple[list[dict], np.ndarray]:
    """The committed embedding store: row-aligned metadata + vectors, or a hard error telling
    the user how to repair it (``embed_jobs.py --resume`` reconciles and re-commits)."""
    manifest_path = _STORE / "manifest.json"
    if not manifest_path.exists():
        sys.exit(
            f"no committed store at {_STORE} (manifest.json missing) — "
            "run scripts/embed/embed_jobs.py first (--resume finishes an interrupted run)"
        )
    dim = json.loads(manifest_path.read_text())["dim"]
    metas = [
        json.loads(line) for line in (_STORE / "meta.jsonl").open(encoding="utf-8")
    ]
    vectors = np.fromfile(_STORE / "embeddings.f32", dtype="float32")
    if vectors.size != len(metas) * dim:
        sys.exit(
            f"store is inconsistent ({vectors.size} floats for {len(metas)} metadata rows, dim {dim}) — "
            "run scripts/embed/embed_jobs.py --resume to reconcile it"
        )
    return metas, vectors.reshape(-1, dim)


def _scraped_boards(scraped: str | Path, corpus_ids: set[str]) -> set[str]:
    """The Boards this run actually scraped, in ``board_of`` key space, read from the *full* scrape
    (``data/jobs/{ats}.jsonl`` — a non-recursive glob, so the ``tech/`` subdir is not double-counted).

    This is the eviction scope: a Board here but absent from the tech corpus was scraped and simply
    has no tech jobs now, so its stale tech rows are correctly evicted. Falls back to the corpus ids'
    Boards when the scrape dir has no ``.jsonl`` (a Wellfound-CSV or unit-test sync), keeping those
    paths working. (A Board scraped that yields *zero* jobs of any kind writes no ids and so isn't
    covered here — that rarer case is handled by the dead/absent-Board prune, ADR-0023.)"""
    path = Path(scraped)
    if path.is_dir() and any(path.glob("*.jsonl")):
        return {board_of(job["id"]) for job in iter_jobs(path)}
    return {board_of(job_id) for job_id in corpus_ids}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        default=str(_SOURCE),
        help="corpus snapshot: a {ats}.jsonl directory or a Wellfound CSV (default: data/jobs/tech)",
    )
    ap.add_argument(
        "--scraped",
        default=str(_SCRAPED),
        help="full-scrape {ats}.jsonl dir defining the scraped-Board eviction scope "
        "(default: data/jobs); falls back to --source's Boards when it has no .jsonl files",
    )
    args = ap.parse_args()

    metas, vectors = _load_store()
    row_of = {meta["id"]: i for i, meta in enumerate(metas)}
    dim = vectors.shape[1]
    print(f"store: {len(metas)} embedded Jobs (dim {dim})", flush=True)

    corpus_ids = {job["id"] for job in iter_jobs(args.source)}
    boards = _scraped_boards(args.scraped, corpus_ids)
    fresh = corpus_ids & row_of.keys()
    unembedded = len(corpus_ids) - len(fresh)
    print(
        f"corpus: {len(corpus_ids)} Jobs on {len(boards)} Boards; {len(fresh)} have vectors"
        + (
            f" — {unembedded} without (non-English, or run embed_jobs.py --resume)"
            if unembedded
            else ""
        ),
        flush=True,
    )

    db = lancedb.connect(_DB)
    if PROD_TABLE in db.list_tables().tables:
        table = db.open_table(PROD_TABLE)
        n = table.count_rows()
        index_ids = [
            r["id"] for r in table.search().select(["id"]).limit(max(n, 1)).to_list()
        ]
    else:
        table = db.create_table(PROD_TABLE, schema=_schema(dim))
        index_ids = []
    print(f"index: {len(index_ids)} rows in table '{PROD_TABLE}'", flush=True)

    plan = plan_sync(index_ids, fresh, boards)
    print(f"plan: add {len(plan.add)}, evict {len(plan.delete)}", flush=True)

    apply_sync(table, [], plan.delete)  # evictions first (chunked internally)
    add_ids = sorted(plan.add)
    for start in range(0, len(add_ids), _ADD_CHUNK):
        chunk = add_ids[start : start + _ADD_CHUNK]
        rows = []
        for job_id in chunk:
            row = dict(metas[row_of[job_id]])
            row["vector"] = vectors[row_of[job_id]].tolist()
            rows.append(row)
        apply_sync(table, rows, ())
        print(
            f"[sync] added {min(start + _ADD_CHUNK, len(add_ids))}/{len(add_ids)}",
            flush=True,
        )

    print(
        f"done: table '{PROD_TABLE}' now holds {table.count_rows()} rows at {_DB}",
        flush=True,
    )


if __name__ == "__main__":
    main()
