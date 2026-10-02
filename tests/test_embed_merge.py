"""Tests for the shard merge (headstart.ingest.embed_merge, ADR-0025 Phase 1).

The merge is a concatenation, but two integrity properties must hold: a fragment left partial by a
timed-out shard is reconciled (its half-written meta tail and the extra vector row are dropped), and
the final store is consistent (vector bytes == rows × dim × 4) so `index sync` can trust it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
    sys.argv = [
        "embed_merge",
        "--store",
        str(store),
        "--fragments",
        str(fragments),
        "--non-english-ids",
        str(store.parent / "pending_non_english.txt"),
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


def test_torn_fragments_warn_once_for_the_step_not_once_per_read(tmp_path, caplog):
    """Each fragment's meta is read once and used twice (the upgrade drop and the merge), and
    the torn tails cost one summary WARNING between them — not one per fragment per pass."""
    store = tmp_path / "store"
    _write_store(store, ["prior:1"])
    frags = tmp_path / "frags"
    _write_store(frags / "shard-0", ["a:1"], extra_vec_rows=1, bad_tail=True)
    _write_store(frags / "shard-1", ["b:1"], extra_vec_rows=1, bad_tail=True)
    upgrades = tmp_path / "upgrades.txt"
    upgrades.write_text("a:1\n", encoding="utf-8")

    with caplog.at_level("INFO", logger="headstart.ingest.embed_merge"):
        _run_with_upgrades(store, frags, upgrades)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [
        "2 fragment(s) had torn tails, 2 record(s) dropped: shard-0, shard-1"
    ]
    assert (
        sum("dropped 1 torn record(s)" in r.getMessage() for r in caplog.records) == 2
    )
    _assert_consistent(store, 3)


def test_shard_losses_from_the_manifests_are_named_in_one_warning(tmp_path, caplog):
    store = tmp_path / "store"
    _write_store(store, ["prior:1"])
    frags = tmp_path / "frags"
    _write_store(frags / "shard-0", ["a:1"])
    _write_store(frags / "shard-1", ["b:1"])  # a pre-field manifest reads as no loss
    manifest = frags / "shard-0" / "manifest.json"
    manifest.write_text(
        json.dumps({"dim": _DIM, "count": 1, "failed": 64, "unattempted": 210}),
        encoding="utf-8",
    )
    upgrades = tmp_path / "upgrades.txt"
    upgrades.write_text("", encoding="utf-8")

    with caplog.at_level("INFO", logger="headstart.ingest.embed_merge"):
        _run_with_upgrades(store, frags, upgrades)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [
        (
            "embed shards lost Docs, which are re-planned next run: "
            "shard-0 (64 failed, 210 unattempted)"
        )
    ]


def test_a_fragment_without_a_manifest_is_named_as_a_loss(tmp_path, caplog):
    """A shard `timeout` kills before its commit marker banks rows but never counts what it
    did not reach — the routine time-budget exit, which the loss line used to skip."""
    store = tmp_path / "store"
    _write_store(store, ["prior:1"])
    frags = tmp_path / "frags"
    _write_store(frags / "shard-0", ["a:1", "a:2"])
    (frags / "shard-0" / "manifest.json").unlink()
    upgrades = tmp_path / "upgrades.txt"
    upgrades.write_text("", encoding="utf-8")

    with caplog.at_level("INFO", logger="headstart.ingest.embed_merge"):
        _run_with_upgrades(store, frags, upgrades)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [
        (
            "embed shards lost Docs, which are re-planned next run: shard-0 (no manifest — "
            "stopped before finishing, e.g. its time budget; 2 rows banked, unattempted unknown)"
        )
    ]


def test_a_missing_upgrade_list_with_fragments_warns(tmp_path, caplog):
    store = tmp_path / "store"
    _write_store(store, ["prior:1"])
    frags = tmp_path / "frags"
    _write_store(frags / "shard-0", ["a:1"])

    with caplog.at_level("INFO", logger="headstart.ingest.embed_merge"):
        _run_with_upgrades(store, frags, tmp_path / "absent.txt")

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and warnings[0].startswith("upgrade list missing at ")


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


def _run_with_upgrades(store: Path, fragments: Path, upgrades: Path) -> None:
    old = sys.argv
    sys.argv = [
        "embed_merge",
        "--store",
        str(store),
        "--fragments",
        str(fragments),
        "--evict-ids",
        str(upgrades),
        "--non-english-ids",
        str(store.parent / "pending_non_english.txt"),
    ]
    try:
        assert ms.main() == 0
    finally:
        sys.argv = old


def test_an_upgrade_holds_its_stale_row_when_nothing_arrives_to_replace_it(tmp_path):
    """An ADR-0050 upgrade is a *replace*. `merge` runs `if: always()`, so it reaches the store
    with no fragments whenever `embed` was skipped or failed — and dropping the stale rows there
    is a plain delete: the Jobs leave the served index until some later run re-embeds them. On
    2026-08-13 one such run cost 10,144 vectors and 11,083 served rows.
    """
    store, frags = tmp_path / "store", tmp_path / "frags"
    _write_store(store, ["a", "b", "c"])
    frags.mkdir()  # embed was skipped, so no fragment dirs landed
    upgrades = tmp_path / "pending_upgrades.txt"
    upgrades.write_text("a\nb\n", encoding="utf-8")

    _run_with_upgrades(store, frags, upgrades)

    assert _store_ids(store) == ["a", "b", "c"]
    _assert_consistent(store, 3)


def test_an_upgrade_drops_only_the_ids_whose_replacement_arrived(tmp_path):
    """`embed` is `fail-fast: false` and its download is `continue-on-error`, so 14 of 15
    fragments is an ordinary outcome — not just the all-or-nothing skip. Evicting the whole
    upgrade list there deletes the vectors of every id the missing shard held, and
    `index._take_upgrades` drops their rows regardless, so `plan_sync` cannot re-add them.
    """
    pytest.importorskip("numpy")
    store, frags = tmp_path / "store", tmp_path / "frags"
    _write_store(store, ["arrived", "missing", "untouched"])
    _write_store(
        frags / "embed-fragment-0", ["arrived"]
    )  # the other shard never uploaded
    upgrades = tmp_path / "pending_upgrades.txt"
    upgrades.write_text("arrived\nmissing\n", encoding="utf-8")

    _run_with_upgrades(store, frags, upgrades)

    # `missing` keeps its old vector rather than losing it; `arrived` was replaced.
    assert _store_ids(store) == ["missing", "untouched", "arrived"]
    _assert_consistent(store, 3)


def test_an_upgrade_still_replaces_its_stale_row_when_a_fragment_does_arrive(tmp_path):
    """The guard must not disarm the upgrade itself: with a fragment present, the stale rows go
    and the fresh ones take their place.

    Needs numpy, which CI's base-deps job does not install — `evict_ids` rewrites the vectors.
    The held-rows test above deliberately does not, because the guard means it never gets there,
    so the regression this file exists for stays visible in CI.
    """
    pytest.importorskip("numpy")
    store, frags = tmp_path / "store", tmp_path / "frags"
    _write_store(store, ["a", "b", "c"])
    _write_store(frags / "embed-fragment-0", ["a", "b"])
    upgrades = tmp_path / "pending_upgrades.txt"
    upgrades.write_text("a\nb\n", encoding="utf-8")

    _run_with_upgrades(store, frags, upgrades)

    # c survives untouched; a and b were dropped and re-appended from the fragment
    assert _store_ids(store) == ["c", "a", "b"]
    _assert_consistent(store, 3)


def test_eviction_failure_never_reassigns_survivors_vectors(tmp_path, monkeypatch):
    """A failed second rename must not pair surviving ids with the old vector prefix."""
    np = pytest.importorskip("numpy")
    store = tmp_path / "store"
    _write_store(store, ["a", "b", "c", "d"])
    np.repeat(np.arange(4, dtype="float32"), _DIM).tofile(store / "embeddings.f32")
    real_replace = Path.replace
    failed = False

    def fail_vector_once(self, target):
        nonlocal failed
        if Path(target).name == "embeddings.f32" and not failed:
            failed = True
            raise OSError("failed vector replacement")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_vector_once)
    with pytest.raises(OSError, match="failed vector replacement"):
        ms.evict_ids(store / "meta.jsonl", store / "embeddings.f32", _DIM, {"b", "c"})
    ms._reconcile_store(store / "meta.jsonl", store / "embeddings.f32", _DIM)
    ids = _store_ids(store)
    values = np.fromfile(store / "embeddings.f32", dtype="float32").reshape(-1, _DIM)
    assert dict(zip(ids, values[:, 0], strict=True)) == {"a": 0.0, "d": 3.0}
    _assert_consistent(store, 2)


def test_a_non_english_drop_needs_no_replacement(tmp_path):
    """ADR-0286: unlike an upgrade, nothing re-embeds these, so they leave the store even with no
    fragments at all, and `index sync` then evicts their rows."""
    store, frags = tmp_path / "store", tmp_path / "frags"
    _write_store(store, ["a", "b", "c"])
    frags.mkdir()
    (tmp_path / "pending_non_english.txt").write_text("b\n", encoding="utf-8")

    _run(store, frags)

    assert _store_ids(store) == ["a", "c"]
    _assert_consistent(store, 2)


@pytest.mark.parametrize(
    "target_name", [ms._REWRITE, "meta.jsonl", "embeddings.f32", "manifest.json"]
)
def test_interrupted_eviction_recovers_before_append_tail_reconciliation(
    tmp_path, monkeypatch, target_name
):
    """Simulate abrupt termination after each rename; no catch/cleanup executes."""
    np = pytest.importorskip("numpy")
    store = tmp_path / "store"
    _write_store(store, ["a", "b", "c", "d"])
    np.repeat(np.arange(4, dtype="float32"), _DIM).tofile(store / "embeddings.f32")
    real_replace = Path.replace

    def interrupt_after_replace(self, target):
        result = real_replace(self, target)
        if Path(target).name == target_name:
            raise KeyboardInterrupt("killed after rename")
        return result

    monkeypatch.setattr(Path, "replace", interrupt_after_replace)
    with pytest.raises(KeyboardInterrupt):
        ms.evict_ids(store / "meta.jsonl", store / "embeddings.f32", _DIM, {"b", "c"})
    assert (store / ms._REWRITE).exists()
    monkeypatch.setattr(Path, "replace", real_replace)
    assert (
        ms._reconcile_store(store / "meta.jsonl", store / "embeddings.f32", _DIM) == 2
    )
    values = np.fromfile(store / "embeddings.f32", dtype="float32").reshape(-1, _DIM)
    assert dict(zip(_store_ids(store), values[:, 0], strict=True)) == {
        "a": 0.0,
        "d": 3.0,
    }
    _assert_consistent(store, 2)
    assert not (store / ms._REWRITE).exists()
    ms.check_store(store)  # recovery is idempotent


def test_publication_guard_refuses_an_unresolved_rewrite(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    store = tmp_path / "store"
    _write_store(store, ["a", "b", "c"])
    real_replace = Path.replace

    def fail_vector(self, target):
        if Path(target).name == "embeddings.f32":
            raise OSError("disk unavailable")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_vector)
    with pytest.raises(OSError):
        ms.evict_ids(store / "meta.jsonl", store / "embeddings.f32", _DIM, {"b"})
    with pytest.raises(OSError):
        ms.check_store(store)
    assert (store / ms._REWRITE).exists()


@pytest.mark.parametrize("damage", ["manifest", "metadata", "vectors"])
def test_publication_guard_refuses_inconsistent_store(tmp_path, damage):
    store = tmp_path / "store"
    _write_store(store, ["a", "b"])
    if damage == "manifest":
        (store / "manifest.json").write_text(json.dumps({"dim": _DIM, "count": 7}))
    elif damage == "metadata":
        with (store / "meta.jsonl").open("a") as fh:
            fh.write('{"id":')
    else:
        (store / "embeddings.f32").write_bytes(bytes(_DIM * 4))
    with pytest.raises(RuntimeError, match="refusing publication"):
        ms.check_store(store)


def test_publication_cli_recovers_after_process_exit_without_cleanup(tmp_path):
    """os._exit bypasses every Python handler, as a killed nonfatal prune process does."""
    import os
    import subprocess
    import textwrap

    np = pytest.importorskip("numpy")
    store = tmp_path / "store"
    _write_store(store, ["a", "b", "c", "d"])
    np.repeat(np.arange(4, dtype="float32"), _DIM).tofile(store / "embeddings.f32")
    source = Path(ms.__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(source)}
    crash = textwrap.dedent(
        """
        import os, sys
        from pathlib import Path
        from headstart.ingest.embed_merge import evict_ids
        store = Path(sys.argv[1])
        real_replace = Path.replace
        def killed(self, target):
            result = real_replace(self, target)
            if Path(target).name == 'meta.jsonl':
                os._exit(73)
            return result
        Path.replace = killed
        evict_ids(store / 'meta.jsonl', store / 'embeddings.f32', 4, {'b', 'c'})
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", crash, str(store)], env=env, check=False
    )
    assert result.returncode == 73
    assert (store / ms._REWRITE).exists()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "headstart.ingest.embed_merge",
            "--check-store",
            "--store",
            str(store),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = np.fromfile(store / "embeddings.f32", dtype="float32").reshape(-1, _DIM)
    assert dict(zip(_store_ids(store), values[:, 0], strict=True)) == {
        "a": 0.0,
        "d": 3.0,
    }
    _assert_consistent(store, 2)
    assert not (store / ms._REWRITE).exists()
