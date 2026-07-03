"""Tests for the incremental, board-scoped index sync (ADR-0014)."""

from __future__ import annotations

from headstart.index_sync import apply_sync, plan_sync


def test_plan_add_and_evict_within_scraped_board():
    # board greenhouse:a was scraped; a:2 vanished from the fresh output -> evict; a:3 is new -> add
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "greenhouse:a:2"],
        fresh_ids=["greenhouse:a:1", "greenhouse:a:3"],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.add == frozenset({"greenhouse:a:3"})
    assert plan.delete == frozenset({"greenhouse:a:2"})


def test_plan_never_evicts_an_unscraped_board():
    # lever:b wasn't scraped this run, so its rows stay even though they're absent from fresh_ids
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "lever:b:9"],
        fresh_ids=["greenhouse:a:1"],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.delete == frozenset()  # lever:b:9 protected
    assert plan.add == frozenset()


def test_plan_id_only_leaves_reseen_ids():
    # an id present in both index and fresh is neither re-embedded nor evicted (id-only, v1)
    plan = plan_sync(
        index_ids=["greenhouse:a:1"],
        fresh_ids=["greenhouse:a:1"],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.add == frozenset()
    assert plan.delete == frozenset()


def test_plan_dead_board_evicts_all_its_rows():
    # a scraped board that yields nothing (dead) -> every one of its rows drops out
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "greenhouse:a:2"],
        fresh_ids=[],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.delete == frozenset({"greenhouse:a:1", "greenhouse:a:2"})


def _ids(table) -> set[str]:
    return set(table.to_arrow().column("id").to_pylist())


def test_apply_sync_round_trip(tmp_path):
    import lancedb
    import pyarrow as pa

    schema = pa.schema(
        [pa.field("id", pa.string()), pa.field("vector", pa.list_(pa.float32(), 2))]
    )
    table = lancedb.connect(str(tmp_path)).create_table(
        "jobs",
        data=[
            {"id": "greenhouse:a:1", "vector": [0.1, 0.2]},
            {"id": "greenhouse:a:2", "vector": [0.3, 0.4]},
        ],
        schema=schema,
    )
    apply_sync(
        table,
        add_rows=[{"id": "greenhouse:a:3", "vector": [0.5, 0.6]}],
        delete_ids=["greenhouse:a:2"],
    )
    assert _ids(table) == {"greenhouse:a:1", "greenhouse:a:3"}
