"""Tests for the trends-ledger step (headstart.ingest.role_trends, ADR-0040).

Contracts: served rows are counted into (family, band) groups with non-tech held apart; the
ledger appends run over run with one header; and every degenerate input (missing centroids,
missing family map, empty table) exits 0 without writing — trends must never sink a run that
already scraped and embedded, nor silently look healthy while accruing nothing.
"""

from __future__ import annotations

import csv
import json
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


def _centroids(store: Path, families_path: Path) -> None:
    """Three orthogonal clusters + the curated map: 0,1 are tech families, 2 is non-tech."""
    centroids = np.eye(3, _DIM, dtype=np.float32)
    roles.save(
        store,
        centroids,
        {
            "version": 1,
            "k": 3,
            "dim": _DIM,
            "clusters": [{"id": i, "label": f"raw {i}"} for i in range(3)],
        },
    )
    families_path.parent.mkdir(parents=True, exist_ok=True)
    families_path.write_text(
        json.dumps(
            {
                "centroid_version": 1,
                "families": [
                    {"name": "software-engineering", "clusters": [0]},
                    {"name": "data-science", "clusters": [1]},
                ],
                "non_tech": {"clusters": [2]},
            }
        ),
        encoding="utf-8",
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
            "--families",
            str(tmp_path / "families.json"),
            "--ledger",
            str(ledger),
        ],
    )
    assert role_trends.main() == 0
    return ledger


def test_counts_rows_by_family_and_band_and_isolates_non_tech(tmp_path, monkeypatch):
    _centroids(tmp_path / "rc", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]  # -> cluster 0, family software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> cluster 1, family data-science
    z = [0.0, 0.0, 1.0, 0.0]  # -> cluster 2, NON-TECH
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
                "title": "Data Entry Clerk",
                "employment_type": None,
                "min_years": 2,
                "vector": z,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["family"], r["band"]): r["count"] for r in csv.DictReader(ledger.open())}
    assert (
        rows[("software-engineering", "senior")] == "2"
    )  # 5 and 6 years band together
    assert rows[("data-science", "intern")] == "1"
    # the non-tech row is the diagnostic: one unbanded number, never a chart series
    assert rows[("non-tech", "all")] == "1"
    assert ("data-science", "mid") not in rows  # only non-empty groups


def test_ledger_appends_with_one_header(tmp_path, monkeypatch):
    _centroids(tmp_path / "rc", tmp_path / "families.json")
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
    assert lines[0] == "ts,version,family,band,count"
    assert len(lines) == 5  # header + (one group + the non-tech diagnostic) per run
    assert sum(1 for line in lines if line.startswith("ts,")) == 1


def test_missing_centroid_store_degrades_to_noop(tmp_path, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)  # no _centroids(), no table — must not matter
    assert not ledger.exists()
    assert any("skipping trends" in r.getMessage() for r in caplog.records)


def test_missing_family_map_degrades_to_noop(tmp_path, monkeypatch, caplog):
    """The centroids ride the HF state artifact but the map ships in git, so they go missing
    for different reasons — and the workflow step is continue-on-error, which would turn an
    unguarded FileNotFoundError into a green run that never accrues a row."""
    import logging

    _centroids(tmp_path / "rc", tmp_path / "families.json")
    (tmp_path / "families.json").unlink()
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
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("families.json" in r.getMessage() for r in caplog.records)


def test_empty_served_table_degrades_to_noop(tmp_path, monkeypatch, caplog):
    # np.stack has no empty case, so an empty table must be caught before the count
    import logging

    _centroids(tmp_path / "rc", tmp_path / "families.json")
    _table(tmp_path / "db", [])
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("is empty" in r.getMessage() for r in caplog.records)


def test_stale_family_map_errors_visibly_instead_of_silently(
    tmp_path, monkeypatch, caplog
):
    """A refit shipped without re-curating the map is routine (ADR-0040). The workflow step is
    continue-on-error, so an unguarded ValueError would crash into a green run with no
    annotation — it must surface as ERROR (an ::error:: under Actions) and exit non-zero."""
    import logging

    _centroids(tmp_path / "rc", tmp_path / "families.json")
    spec = json.loads((tmp_path / "families.json").read_text())
    spec["centroid_version"] = 99  # the map now describes a different fit
    (tmp_path / "families.json").write_text(json.dumps(spec), encoding="utf-8")
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
            "--families",
            str(tmp_path / "families.json"),
            "--ledger",
            str(ledger),
        ],
    )
    caplog.set_level(logging.ERROR, logger="headstart.ingest.role_trends")
    assert role_trends.main() == 1
    assert not ledger.exists()
    assert any("taxonomy unusable" in r.getMessage() for r in caplog.records)


def test_half_landed_centroid_store_degrades_to_noop(tmp_path, monkeypatch, caplog):
    # manifest without vectors: roles.load would crash on the missing file
    import logging

    _centroids(tmp_path / "rc", tmp_path / "families.json")
    (tmp_path / "rc" / "centroids.f32").unlink()
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("centroids.f32" in r.getMessage() for r in caplog.records)
