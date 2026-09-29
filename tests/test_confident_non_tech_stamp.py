"""Tests for the stamp a Trends tick writes on the served table (ADR-0349).

Contracts: the column ends up true on exactly the rows named and false on every other, whether or
not the table had it; nothing else about a row changes; a tick that decides nothing new writes
nothing; a stamp that would hide an implausible share of the table is refused and leaves the column
as it was; and a write that fails partway never leaves the table without the column.
"""

from __future__ import annotations

import pytest

pytest.importorskip("lancedb")
pa = pytest.importorskip("pyarrow")

import lancedb

from headstart.ingest import confident_non_tech_stamp as stamp_module
from headstart.search_filters.confident_non_tech_filter import COLUMN

_IDS = [f"greenhouse:acme:{n}" for n in range(20)]


def _table(tmp_path, *, with_column: bool = True, flagged: tuple[str, ...] = ()):
    fields = [
        pa.field("id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), 2)),
    ]
    rows = [
        {"id": i, "title": f"Job {n}", "vector": [float(n), 1.0]}
        for n, i in enumerate(_IDS)
    ]
    if with_column:
        fields.append(pa.field(COLUMN, pa.bool_()))
        for row in rows:
            row[COLUMN] = row["id"] in flagged
    return lancedb.connect(tmp_path).create_table(
        "jobs", pa.Table.from_pylist(rows, schema=pa.schema(fields))
    )


def _stamped(table) -> set[str]:
    rows = (
        table.search()
        .where(f"{COLUMN} = true")
        .select(["id"])
        .limit(table.count_rows())
        .to_list()
    )
    return {row["id"] for row in rows}


def test_the_named_rows_are_stamped_and_every_other_row_is_not(tmp_path):
    table = _table(tmp_path)
    result = stamp_module.stamp(table, {_IDS[1], _IDS[5]})
    assert _stamped(table) == {_IDS[1], _IDS[5]}
    assert table.count_rows(filter=f"{COLUMN} = false") == 18
    assert table.count_rows(filter=f"{COLUMN} IS NULL") == 0
    assert (result.stamped, result.newly, result.cleared, result.written) == (
        2,
        2,
        0,
        True,
    )


def test_a_table_from_before_the_column_gets_it(tmp_path):
    table = _table(tmp_path, with_column=False)
    stamp_module.stamp(table, {_IDS[3]})
    assert COLUMN in table.schema.names
    assert _stamped(table) == {_IDS[3]}


def test_a_retitled_row_that_is_no_longer_confident_is_unhidden(tmp_path):
    """The stamp is recomputed whole each tick, so a row the head stops calling non-tech, or a
    row that left the served table, is cleared with the rest (ADR-0349)."""
    table = _table(tmp_path, flagged=(_IDS[1], _IDS[2]))
    result = stamp_module.stamp(table, {_IDS[2], _IDS[7]})
    assert _stamped(table) == {_IDS[2], _IDS[7]}
    assert (result.stamped, result.newly, result.cleared) == (2, 1, 1)


def test_an_empty_decision_clears_every_stamp(tmp_path):
    table = _table(tmp_path, flagged=(_IDS[1], _IDS[2]))
    stamp_module.stamp(table, set())
    assert _stamped(table) == set()
    assert table.count_rows(filter=f"{COLUMN} = false") == len(_IDS)


def test_a_tick_that_decides_nothing_new_writes_nothing(tmp_path):
    table = _table(tmp_path, flagged=(_IDS[1],))
    before = table.version
    result = stamp_module.stamp(table, {_IDS[1]})
    assert table.version == before
    assert result.written is False and result.stamped == 1


def test_the_rows_keep_every_other_value(tmp_path):
    table = _table(tmp_path)
    stamp_module.stamp(table, {_IDS[4]})
    row = table.search().where(f"id = '{_IDS[4]}'").limit(1).to_list()[0]
    assert row["title"] == "Job 4" and row["vector"] == [4.0, 1.0]
    assert table.count_rows() == len(_IDS)


def test_an_id_with_a_quote_in_it_is_stamped_like_any_other(tmp_path):
    odd = "workday:acme:R-1' OR '1'='1"
    table = lancedb.connect(tmp_path).create_table(
        "jobs",
        pa.Table.from_pylist(
            [{"id": odd, COLUMN: False}]
            + [{"id": f"workday:acme:R-{n}", COLUMN: False} for n in range(2, 12)]
        ),
    )
    stamp_module.stamp(table, {odd})
    assert _stamped(table) == {odd}


def test_a_stamp_hiding_an_implausible_share_is_refused_and_leaves_the_column(tmp_path):
    """A healthy head calls about a quarter of the rows non-tech at any probability and 12%
    at this one; more than MAX_STAMPED_SHARE means the head or its inputs are broken, and the
    column stays as the last healthy tick left it."""
    table = _table(tmp_path, flagged=(_IDS[0],))
    too_many = set(_IDS[: int(len(_IDS) * stamp_module.MAX_STAMPED_SHARE) + 2])
    with pytest.raises(stamp_module.StampRefused):
        stamp_module.stamp(table, too_many)
    assert _stamped(table) == {_IDS[0]}


def test_a_failed_write_never_leaves_the_table_without_the_column(
    tmp_path, monkeypatch
):
    table = _table(tmp_path, flagged=(_IDS[1],))
    real = table.add_columns
    calls = []

    def add_columns(transforms):
        calls.append(transforms)
        if len(calls) == 1:
            raise OSError("disk full")
        return real(transforms)

    monkeypatch.setattr(table, "add_columns", add_columns)
    with pytest.raises(OSError):
        stamp_module.stamp(table, {_IDS[2]})
    # the column is back, every row visible: the safe direction
    assert COLUMN in table.schema.names
    assert _stamped(table) == set()
    assert table.count_rows(filter=f"{COLUMN} = false") == len(_IDS)
