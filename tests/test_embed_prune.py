"""Tests for the embedding-store prune (headstart.ingest.embed_prune, ADR-0190).

The store keeps a vector only for a Job the served table holds or this run's corpus carries. What
must hold: the survivors' vectors stay row-aligned with their metadata, the manifest count follows,
and a table that cannot be trusted never empties the store.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("lancedb")  # the index runtime, which the [dev] extra installs
pytest.importorskip("numpy")

import lancedb
import numpy as np

import headstart.ingest.embed_prune as ep
from headstart.ingest.index import write_base
from headstart.search import PROD_TABLE

_DIM = 4


def _write_store(d: Path, ids: list[str]) -> None:
    """A store whose row ``i`` holds the vector ``[i, i, i, i]``, so alignment is checkable."""
    d.mkdir(parents=True)
    (d / "meta.jsonl").write_text(
        "".join(json.dumps({"id": i}) + "\n" for i in ids), encoding="utf-8"
    )
    vectors = np.repeat(np.arange(len(ids), dtype="float32"), _DIM)
    vectors.tofile(d / "embeddings.f32")
    (d / "manifest.json").write_text(
        json.dumps({"dim": _DIM, "count": len(ids)}), encoding="utf-8"
    )


def _read_store(d: Path) -> dict[str, float]:
    """``{id: the value its vector was written with}``."""
    ids = [json.loads(x)["id"] for x in (d / "meta.jsonl").read_text().splitlines()]
    vectors = np.fromfile(d / "embeddings.f32", dtype="float32").reshape(-1, _DIM)
    assert len(vectors) == len(ids)
    return {i: float(v[0]) for i, v in zip(ids, vectors, strict=True)}


def _setup(tmp_path: Path, served: list[str], corpus: list[str]) -> Path:
    _write_store(tmp_path / "store", ["a:1", "a:2", "b:1", "b:2", "c:1"])
    db = tmp_path / "db"
    if served:
        lancedb.connect(db).create_table(PROD_TABLE, [{"id": i} for i in served])
    (tmp_path / "tech").mkdir()
    (tmp_path / "tech" / "a.jsonl").write_text(
        "".join(json.dumps({"id": i}) + "\n" for i in corpus), encoding="utf-8"
    )
    return db


def _run(tmp_path: Path, *extra: str) -> int:
    old = sys.argv
    sys.argv = [
        "embed_prune",
        "--store",
        str(tmp_path / "store"),
        "--db",
        str(tmp_path / "db"),
        "--source",
        str(tmp_path / "tech"),
        *extra,
    ]
    try:
        return ep.main()
    finally:
        sys.argv = old


def test_keeps_served_and_corpus_ids_and_their_own_vectors(tmp_path):
    _setup(tmp_path, served=["a:1", "b:2"], corpus=["c:1"])

    assert _run(tmp_path, "--apply") == 0

    assert _read_store(tmp_path / "store") == {"a:1": 0.0, "b:2": 3.0, "c:1": 4.0}
    manifest = json.loads((tmp_path / "store" / "manifest.json").read_text())
    assert manifest == {"dim": _DIM, "count": 3}


def test_dry_run_leaves_the_store_alone(tmp_path):
    _setup(tmp_path, served=["a:1"], corpus=[])
    before = (tmp_path / "store" / "embeddings.f32").read_bytes()

    assert _run(tmp_path) == 0

    assert (tmp_path / "store" / "embeddings.f32").read_bytes() == before
    assert len(_read_store(tmp_path / "store")) == 5


def test_a_missing_table_never_empties_the_store(tmp_path):
    _setup(tmp_path, served=[], corpus=[])

    assert _run(tmp_path, "--apply") == 1

    assert len(_read_store(tmp_path / "store")) == 5


def test_a_table_that_moved_since_its_last_writer_is_not_pruned_against(tmp_path):
    db = _setup(tmp_path, served=["a:1"], corpus=[])
    write_base(db, 400, "prune")  # the last writer left 400 rows; this table holds 1

    assert _run(tmp_path, "--apply") == 1

    assert len(_read_store(tmp_path / "store")) == 5
