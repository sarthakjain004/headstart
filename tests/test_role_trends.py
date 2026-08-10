"""Tests for the trends-ledger step (headstart.ingest.role_trends, ADR-0040).

Three contracts: served rows are counted into (centroid version, cluster, band) groups; the
ledger appends run over run with one header; and a missing centroid store degrades to a
warning + exit 0 — trends must never sink a run that already scraped and embedded.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

pytest.importorskip("lancedb")  # [embed] extra — not installed in CI's quality job
np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")

import lancedb  # noqa: E402

import headstart.ingest.role_trends as role_trends  # noqa: E402
from headstart import roles  # noqa: E402
from headstart.search import PROD_TABLE  # noqa: E402

_DIM = 4


def _table(db_dir: Path, rows: list[dict]) -> None:
    schema = pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("title", pa.string()),
            pa.field("employment_type", pa.string()),
            pa.field("min_years", pa.int32()),
            pa.field("vector", pa.list_(pa.float32(), _DIM)),
        ]
    )
    lancedb.connect(db_dir).create_table(
        PROD_TABLE, pa.Table.from_pylist(rows, schema=schema)
    )


def _centroids(store: Path) -> None:
    centroids = np.eye(
        2, _DIM, dtype=np.float32
    )  # family 0 = x-axis, family 1 = y-axis
    roles.save(
        store,
        centroids,
        {
            "version": 1,
            "k": 2,
            "dim": _DIM,
            "clusters": [
                {"id": 0, "label": "backend engineer"},
                {"id": 1, "label": "data scientist"},
            ],
        },
    )


def _run(tmp_path: Path, monkeypatch) -> Path:
    ledger = tmp_path / "role_trends.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "role_trends",
            "--db",
            str(tmp_path / "db"),
            "--centroids",
            str(tmp_path / "rc"),
            "--ledger",
            str(ledger),
        ],
    )
    assert role_trends.main() == 0
    return ledger


def test_counts_served_rows_into_version_cluster_band_groups(tmp_path, monkeypatch):
    _centroids(tmp_path / "rc")
    x, y = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": "full_time",
                "min_years": 5,
                "vector": x,
            },
            {
                "id": "b",
                "title": "Backend Dev",
                "employment_type": None,
                "min_years": 6,
                "vector": x,
            },
            {
                "id": "c",
                "title": "Data Sci Intern",
                "employment_type": None,
                "min_years": None,
                "vector": y,
            },
            {
                "id": "d",
                "title": "Data Scientist",
                "employment_type": None,
                "min_years": None,
                "vector": y,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = list(csv.DictReader(ledger.open()))
    groups = {
        (r["cluster"], r["band"]): (r["count"], r["label"], r["version"]) for r in rows
    }
    assert groups[("0", "senior")] == ("2", "backend engineer", "1")
    assert groups[("1", "intern")] == ("1", "data scientist", "1")
    assert groups[("1", "unspecified")] == ("1", "data scientist", "1")
    assert len(rows) == 3  # only non-empty groups


def test_ledger_appends_with_one_header(tmp_path, monkeypatch):
    _centroids(tmp_path / "rc")
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    ledger = _run(tmp_path, monkeypatch)
    _run(tmp_path, monkeypatch)  # second run appends

    lines = ledger.read_text().splitlines()
    assert lines[0] == "ts,version,cluster,label,band,count"
    assert len(lines) == 3  # header + one group per run
    assert sum(1 for line in lines if line.startswith("ts,")) == 1


def test_missing_centroid_store_degrades_to_noop(tmp_path, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)  # no _centroids(), no table — must not matter
    assert not ledger.exists()
    assert any("skipping trends" in r.getMessage() for r in caplog.records)
