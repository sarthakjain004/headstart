"""Tests for the shard merge (headstart.ingest.embed_merge, ADR-0025 Phase 1).

The merge is a concatenation, but two integrity properties must hold: a fragment left partial by a
timed-out shard is reconciled (its half-written meta tail and the extra vector row are dropped), and
the final store is consistent (vector bytes == rows × dim × 4) so `index sync` can trust it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import headstart.ingest.embed_merge as ms

_DIM = 4


def _write_store(
    d: Path, ids: list[str], *, extra_vec_rows: int = 0, bad_tail: bool = False
) -> None:
    """A store/fragment dir: meta.jsonl + embeddings.f32 + manifest.json (dim 4). ``extra_vec_rows``
    simulates vectors written past the last meta line; ``bad_tail`` appends an unparseable meta line."""
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"id": i}) for i in ids]
    meta = "\n".join(lines) + ("\n" if lines else "")
    if bad_tail:
        meta += '{"id": "half-written'  # a crash mid-write leaves this
    (d / "meta.jsonl").write_text(meta, encoding="utf-8")
    n_vec = len(ids) + extra_vec_rows
    # fake vectors: raw f32 bytes of the right length (merge concatenates bytes; the tests assert
    # row/byte alignment, never vector values — so no numpy needed, and this runs in the base CI job)
    (d / "embeddings.f32").write_bytes(bytes(n_vec * _DIM * 4))
    (d / "manifest.json").write_text(
        json.dumps({"dim": _DIM, "count": len(ids)}), encoding="utf-8"
    )


def _store_ids(d: Path) -> list[str]:
    return [
        json.loads(line)["id"]
        for line in (d / "meta.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _run(store: Path, fragments: Path) -> None:
    old = sys.argv
    # --ids-out is pinned into the tmp store: it defaults to the repo's real
    # data/state/embedded_ids.txt.gz, and a test run must never overwrite the developer's.
    sys.argv = [
        "embed_merge",
        "--store",
        str(store),
        "--fragments",
        str(fragments),
        "--ids-out",
        str(store / "embedded_ids.txt.gz"),
    ]
    try:
        assert ms.main() == 0
    finally:
        sys.argv = old


def _assert_consistent(store: Path, expected_rows: int) -> None:
    rows = _store_ids(store)
    assert len(rows) == expected_rows
    assert (store / "embeddings.f32").stat().st_size == expected_rows * _DIM * 4
    assert json.loads((store / "manifest.json").read_text())["count"] == expected_rows


def test_merge_appends_fragments_onto_prior_store(tmp_path):
    store = tmp_path / "store"
    _write_store(store, ["prior:1", "prior:2"])
    frags = tmp_path / "frags"
    _write_store(frags / "shard-0", ["a:1"])
    _write_store(frags / "shard-1", ["b:1", "b:2"])

    _run(store, frags)

    _assert_consistent(store, 5)
    assert set(_store_ids(store)) == {"prior:1", "prior:2", "a:1", "b:1", "b:2"}


def test_merge_reconciles_a_timed_out_fragment(tmp_path):
    store = tmp_path / "store"
    _write_store(store, ["prior:1"])
    frags = tmp_path / "frags"
    # a shard killed mid-batch: 2 good meta lines + a half-written tail, and 3 vector rows
    _write_store(frags / "shard-0", ["ok:1", "ok:2"], extra_vec_rows=1, bad_tail=True)

    _run(store, frags)

    _assert_consistent(
        store, 3
    )  # prior:1 + the 2 good rows; the partial tail is dropped
    assert set(_store_ids(store)) == {"prior:1", "ok:1", "ok:2"}


def test_merge_first_run_no_prior_store(tmp_path):
    store = tmp_path / "store"  # does not exist yet
    frags = tmp_path / "frags"
    _write_store(frags / "shard-0", ["a:1", "a:2"])

    _run(store, frags)

    _assert_consistent(store, 2)


def test_merge_no_fragments_is_a_noop_reconcile(tmp_path):
    store = tmp_path / "store"
    _write_store(store, ["prior:1", "prior:2"])
    frags = tmp_path / "frags"  # empty / absent

    _run(store, frags)

    _assert_consistent(store, 2)


def test_write_embedded_ids_publishes_one_id_per_line(tmp_path):
    """The scrape stage asks only "have we embedded this Job?", so it gets ids, not the 226 MB
    of metadata that answers the same question (ADR-0048)."""
    import gzip
    import json as _json

    from headstart.ingest.embed_merge import write_embedded_ids

    meta = tmp_path / "meta.jsonl"
    meta.write_text(
        "".join(
            _json.dumps({"id": f"eightfold:acme:{i}", "title": "x" * 500}) + "\n"
            for i in range(3)
        ),
        encoding="utf-8",
    )
    out = tmp_path / "state" / "embedded_ids.txt.gz"
    assert write_embedded_ids(meta, out) == 3
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert [line.strip() for line in fh] == [
            "eightfold:acme:0",
            "eightfold:acme:1",
            "eightfold:acme:2",
        ]
    assert out.stat().st_size < meta.stat().st_size  # the whole point: far smaller
