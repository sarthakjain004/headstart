"""Tests for the trends-ledger step (headstart.ingest.role_trends, ADR-0040/ADR-0051).

Contracts: served rows are counted into (metric, family, band) groups with non-tech held
apart; `new` counts only rows first seen inside the flow window; watched roles are counted by
title in addition to their family; a pre-ADR-0051 ledger migrates in place; the ledger appends
run over run with one header; and every degenerate input (missing centroids, missing family
map, empty table, zero-byte ledger) exits without writing garbage — trends must never sink a
run that already scraped and embedded, nor silently look healthy while accruing nothing.
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

from datetime import UTC

import lancedb

from headstart import roles
from headstart.ingest import role_trends
from headstart.search import PROD_TABLE

_DIM = 4


def _table(db_dir: Path, rows: list[dict]) -> None:
    fields = [
        pa.field("id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("employment_type", pa.string()),
        pa.field("min_years", pa.int32()),
        pa.field("vector", pa.list_(pa.float32(), _DIM)),
    ]
    if any("first_seen" in r for r in rows):
        fields.append(pa.field("first_seen", pa.string()))
    schema = pa.schema(fields)
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
            # Pinned into tmp_path: it defaults to the repo's real config/role_watchlist.json,
            # and these tests must control exactly which roles are watched.
            "--watchlist",
            str(tmp_path / "watchlist.json"),
            # Pinned too (ADR-0057): these default to the repo's real data/state files, and a
            # test run must not write the production snapshot or append to its ledger.
            "--assignments",
            str(tmp_path / "role_assignments.parquet"),
            "--reassignments",
            str(tmp_path / "role_reassignments.csv"),
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
    assert lines[0] == "ts,version,metric,family,band,count"
    assert (
        len(lines) == 5
    )  # header + (one stock group + the non-tech diagnostic) per run
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
            # Pinned even though this path errors before writing (ADR-0057): the isolation
            # must not depend on the error path staying an error path.
            "--assignments",
            str(tmp_path / "role_assignments.parquet"),
            "--reassignments",
            str(tmp_path / "role_reassignments.csv"),
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


def _watchlist(tmp_path: Path, roles_spec: list[dict]) -> None:
    (tmp_path / "watchlist.json").write_text(
        json.dumps({"roles": roles_spec}), encoding="utf-8"
    )


def test_new_metric_counts_only_rows_first_seen_inside_the_window(
    tmp_path, monkeypatch
):
    """Stock answers "how big is this field"; new answers "is it hiring this week" (ADR-0051).
    A row without a stamp (pre-ADR-0031) is stock but never new — absence of evidence."""
    from datetime import datetime, timedelta

    _centroids(tmp_path / "rc", tmp_path / "families.json")
    now = datetime.now(UTC)
    fresh = (now - timedelta(days=1)).isoformat(timespec="seconds")
    stale = (now - timedelta(days=30)).isoformat(timespec="seconds")
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "first_seen": fresh,
            },
            {
                "id": "b",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "first_seen": stale,
            },
            {
                "id": "c",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "first_seen": None,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {
        (r["metric"], r["family"], r["band"]): r["count"]
        for r in csv.DictReader(ledger.open())
    }
    assert rows[("stock", "software-engineering", "mid")] == "3"
    assert rows[("new", "software-engineering", "mid")] == "1"  # only the 1-day-old row


def test_watch_role_counts_by_title_regardless_of_cluster(tmp_path, monkeypatch):
    """The pattern is the definition (ADR-0051): an FDE posting counts under watch:fde even
    when the embedding filed it in a general cluster — that smear across clusters is exactly
    why a ~1% role needs a watchlist rather than a centroid of its own."""
    _centroids(tmp_path / "rc", tmp_path / "families.json")
    _watchlist(
        tmp_path,
        [
            {
                "name": "fde",
                "label": "Forward Deployed Engineer",
                "parent": "software-engineering",
                "match": ["forward[ -]deployed", "\\bFDE\\b"],
            }
        ],
    )
    x = [1.0, 0.0, 0.0, 0.0]  # cluster 0 -> software-engineering
    y = [
        0.0,
        1.0,
        0.0,
        0.0,
    ]  # cluster 1 -> data-science: a "mis-filed" FDE still counts
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Forward Deployed Engineer",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
            },
            {
                "id": "b",
                "title": "Senior FDE, Enterprise",
                "employment_type": None,
                "min_years": 6,
                "vector": y,
            },
            {
                "id": "c",
                "title": "Backend Engineer",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {
        (r["metric"], r["family"], r["band"]): r["count"]
        for r in csv.DictReader(ledger.open())
    }
    assert rows[("stock", "watch:fde", "mid")] == "1"
    assert rows[("stock", "watch:fde", "senior")] == "1"
    # the watched rows still count in their assigned families — the watchlist observes, never moves
    assert rows[("stock", "software-engineering", "mid")] == "2"
    assert rows[("stock", "data-science", "senior")] == "1"


def test_watchlist_with_unknown_parent_errors_visibly(tmp_path, monkeypatch, caplog):
    _centroids(tmp_path / "rc", tmp_path / "families.json")
    _watchlist(
        tmp_path,
        [{"name": "fde", "parent": "no-such-family", "match": ["fde"]}],
    )
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
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
            "--watchlist",
            str(tmp_path / "watchlist.json"),
            # Pinned even though this path errors before writing (ADR-0057): the isolation
            # must not depend on the error path staying an error path.
            "--assignments",
            str(tmp_path / "role_assignments.parquet"),
            "--reassignments",
            str(tmp_path / "role_reassignments.csv"),
        ],
    )
    assert (
        role_trends.main() == 1
    )  # visible error, non-fatal to the run (continue-on-error)
    assert not ledger.exists()


def test_old_ledger_is_migrated_in_place_before_the_first_append(tmp_path, monkeypatch):
    """The ledger predates the metric column and is append-only on HF, so the migration happens
    where the appends do — old rows become metric=stock exactly, never a guess."""
    ledger = tmp_path / "role_trends.csv"
    ledger.write_text(
        "ts,version,family,band,count\n"
        "2026-08-11T00:00:00+00:00,2,software-engineering,mid,10\n"
        "2026-08-11T00:00:00+00:00,2,non-tech,all,3\n",
        encoding="utf-8",
    )
    _centroids(tmp_path / "rc", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
            }
        ],
    )
    _run(tmp_path, monkeypatch)

    lines = ledger.read_text().splitlines()
    assert lines[0] == "ts,version,metric,family,band,count"
    assert lines[1] == "2026-08-11T00:00:00+00:00,2,stock,software-engineering,mid,10"
    assert sum(1 for line in lines if line.startswith("ts,")) == 1
    # every row — migrated and appended alike — parses under the one header
    rows = list(csv.DictReader(ledger.open()))
    assert all(r["metric"] in ("stock", "new") and r["count"].isdigit() for r in rows)


def test_a_zero_byte_ledger_does_not_sink_the_run(tmp_path, monkeypatch):
    """A run killed between `open("a")` and the first write leaves a 0-byte file. It has no
    header to migrate, and `append_ledger` writes one only for a file that does not exist —
    so without this the step raises StopIteration and the ledger never recovers."""
    ledger = tmp_path / "role_trends.csv"
    ledger.touch()
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
    _run(tmp_path, monkeypatch)

    lines = ledger.read_text().splitlines()
    assert lines[0] == "ts,version,metric,family,band,count"
    assert len(lines) == 3  # header + the one group + the non-tech diagnostic


def test_count_groups_returns_assignments_excluding_non_tech_and_watch_roles():
    """The third return value feeds ADR-0057's transition diff, so what it omits is load-bearing.

    Non-tech rows carry no family to compare, and watch roles are title matches layered over the
    taxonomy — a row "moving" between those is a title edit, not a reassignment. Either one
    leaking into the snapshot would manufacture transitions out of nothing.
    """
    centroids = np.eye(3, _DIM, dtype=np.float32)
    families = {
        0: "software-engineering",
        1: "ai-ml",
        2: None,
    }  # 2 is the non-tech cluster
    watchlist = (
        roles.load_watchlist_from_spec(  # type: ignore[attr-defined]
            {
                "roles": [
                    {
                        "name": "backend",
                        "parent": "software-engineering",
                        "pattern": "backend",
                    }
                ]
            },
            {"software-engineering", "ai-ml"},
        )
        if hasattr(roles, "load_watchlist_from_spec")
        else []
    )

    rows = pa.Table.from_pylist(
        [
            {
                "id": "ats:b:tech",
                "title": "Backend Engineer",
                "employment_type": "full-time",
                "min_years": 3,
                "vector": [1.0, 0.0, 0.0, 0.0],
            },
            {
                "id": "ats:b:ai",
                "title": "ML Engineer",
                "employment_type": "full-time",
                "min_years": 3,
                "vector": [0.0, 1.0, 0.0, 0.0],
            },
            {
                "id": "ats:b:nontech",
                "title": "Data Entry Clerk",
                "employment_type": "full-time",
                "min_years": 0,
                "vector": [0.0, 0.0, 1.0, 0.0],
            },
        ],
        schema=pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("title", pa.string()),
                pa.field("employment_type", pa.string()),
                pa.field("min_years", pa.int32()),
                pa.field("vector", pa.list_(pa.float32(), _DIM)),
            ]
        ),
    )
    _counts, non_tech, assigned = role_trends.count_groups(
        rows, centroids, families, watchlist, "2026-01-01T00:00:00+00:00"
    )
    assert non_tech == 1
    assert assigned == {"ats:b:tech": "software-engineering", "ats:b:ai": "ai-ml"}
    assert not any(k.startswith(roles.WATCH_PREFIX) for k in assigned.values())
