"""Tests for the jobs-table CLI (headstart.ingest.index) — the ``first_seen`` stamp (ADR-0031).

``first_seen`` records when *we* first indexed a Job, which `posted_at` cannot: that is the
company's posting date, and boards are scraped on a rotating schedule, so indexing can lag posting
by days. Two properties carry the feature. The stamp must be written exactly once, when the row
enters the table and never again — otherwise "new in the last 2 hours" drifts every run. And the
column must reach a table created before it existed, because `_schema()` only applies at
`create_table` and `apply_sync` rejects rows that don't match the table's frozen schema.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("lancedb")  # [embed] extra — not installed in CI's quality job
pytest.importorskip("numpy")
pytest.importorskip("pyarrow")

import lancedb  # noqa: E402
import numpy as np  # noqa: E402

import headstart.ingest.index as idx  # noqa: E402

_DIM = 4


def _write_store(store: Path, ids: list[str]) -> None:
    """A committed embedding store: one meta row per id, row-aligned vectors, and a manifest."""
    store.mkdir(parents=True, exist_ok=True)
    (store / "meta.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": job_id,
                    "ats": job_id.split(":", 1)[0],
                    "company": "acme",
                    "title": "backend engineer",
                    "location": "remote",
                    "remote": True,
                    "employment_type": None,
                    "experience": None,
                    "min_years": None,
                    "max_years": None,
                    "experience_source": None,
                    "salary": None,
                    "department": None,
                    "url": f"https://example.test/{job_id}",
                    "posted_at": None,
                }
            )
            + "\n"
            for job_id in ids
        ),
        encoding="utf-8",
    )
    np.zeros((len(ids), _DIM), dtype="float32").tofile(store / "embeddings.f32")
    (store / "manifest.json").write_text(json.dumps({"dim": _DIM}), encoding="utf-8")


def _write_corpus(source: Path, ids: list[str]) -> None:
    source.mkdir(parents=True, exist_ok=True)
    (source / "greenhouse.jsonl").write_text(
        "".join(json.dumps({"id": job_id}) + "\n" for job_id in ids), encoding="utf-8"
    )


def _sync(tmp_path: Path, monkeypatch, ids: list[str]) -> int:
    """Run one `index sync` cycle over ``ids`` — store, corpus, and scrape scope all agree."""
    store, source, db = tmp_path / "store", tmp_path / "corpus", tmp_path / "db"
    _write_store(store, ids)
    _write_corpus(source, ids)
    monkeypatch.setattr(idx, "_STORE", store)
    return idx.sync(
        argparse.Namespace(source=str(source), scraped=str(source), db=str(db))
    )


def _rows(tmp_path: Path) -> dict[str, str | None]:
    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    return {r["id"]: r["first_seen"] for r in table.search().limit(100).to_list()}


def test_sync_stamps_every_row_it_adds(tmp_path, monkeypatch):
    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"]) == 0
    stamps = _rows(tmp_path)
    assert set(stamps) == {"greenhouse:a:1", "greenhouse:a:2"}
    assert all(s and s.startswith("20") for s in stamps.values())
    assert len(set(stamps.values())) == 1  # one stamp per run, not per row


def test_sync_does_not_restamp_rows_it_already_holds(tmp_path, monkeypatch):
    """The load-bearing property: a row's stamp is its *first* sighting, so a later run must leave
    it alone even though that run re-syncs the same Board."""
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    first = _rows(tmp_path)["greenhouse:a:1"]

    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])
    after = _rows(tmp_path)

    assert after["greenhouse:a:1"] == first  # untouched
    assert after["greenhouse:a:2"] is not None  # the new arrival is stamped


class _PinnedClock:
    """Stands in for `datetime` inside index.py so a stamp is a fact, not a race with the clock."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self, tz=None) -> datetime:  # noqa: ARG002 — mirrors datetime.now(tz)
        return self.moment


def test_each_run_stamps_with_its_own_time(tmp_path, monkeypatch):
    """Two runs, two times: a later run's arrivals must sort above an earlier run's, which is what
    makes "new in the last 2 hours" mean anything. Pinned rather than slept, so it can't flake on
    the one-second resolution of the stamp."""
    monkeypatch.setattr(
        idx, "datetime", _PinnedClock(datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc))
    )
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])

    monkeypatch.setattr(
        idx, "datetime", _PinnedClock(datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc))
    )
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"])

    stamps = _rows(tmp_path)
    assert stamps["greenhouse:a:1"] == "2026-01-01T09:00:00+00:00"
    assert stamps["greenhouse:a:2"] == "2026-01-01T11:00:00+00:00"
    assert stamps["greenhouse:a:2"] > stamps["greenhouse:a:1"]


def test_new_table_carries_the_column(tmp_path, monkeypatch):
    _sync(tmp_path, monkeypatch, ["greenhouse:a:1"])
    table = lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE)
    assert "first_seen" in table.schema.names


def test_sync_adds_the_column_to_a_table_that_predates_it(tmp_path, monkeypatch):
    """The migration. A table built before `first_seen` existed keeps its frozen schema, and
    `apply_sync` rejects rows that don't match it — so sync must widen the table before writing.
    The pre-existing row keeps a null stamp: we genuinely don't know when we first saw it."""
    import pyarrow as pa

    old_schema = pa.schema(
        [f for f in idx._schema(_DIM) if f.name != "first_seen"]
    )  # the table as it was
    db = lancedb.connect(str(tmp_path / "db"))
    table = db.create_table(idx.PROD_TABLE, schema=old_schema)
    # On a Board this run does not scrape, so the sync leaves it alone (ADR-0014 only evicts
    # within scraped Boards) and we observe the migration rather than an eviction.
    table.add([{"id": "greenhouse:untouched:0", "vector": [0.0] * _DIM}])
    assert "first_seen" not in table.schema.names

    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1"]) == 0

    stamps = _rows(tmp_path)
    assert stamps["greenhouse:untouched:0"] is None  # migrated, first sighting unknown
    assert stamps["greenhouse:a:1"] is not None  # newly added row, stamped


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """It runs on every sync forever, so the second call must be a no-op rather than an error."""
    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1"]) == 0
    assert _sync(tmp_path, monkeypatch, ["greenhouse:a:1", "greenhouse:a:2"]) == 0
    assert "first_seen" in (
        lancedb.connect(str(tmp_path / "db")).open_table(idx.PROD_TABLE).schema.names
    )
