"""Tests for the trends-ledger step (headstart.ingest.role_trends, ADR-0040/ADR-0051).

Contracts: served rows are counted into (metric, family, band) groups with non-tech held
apart; `new` counts only rows first seen inside the flow window; watched roles are counted by
title in addition to their family; a pre-ADR-0051 ledger migrates in place; the ledger appends
run over run with one header; and every degenerate input (missing centroids, missing family
map, empty table, zero-byte ledger) exits without writing garbage — trends must never sink a
run that already scraped and embedded, nor silently look healthy while accruing nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("lancedb")  # [embed] extra — not installed in CI's quality job
np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

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
        pa.field("ats", pa.string()),
    ]
    if any("first_seen" in r for r in rows):
        fields.append(pa.field("first_seen", pa.string()))
    schema = pa.schema(fields)
    # Every served row carries an ats; tests that don't care which get one shared default so
    # their (family, band) assertions still map to exactly one row (ADR-0075).
    rows = [{"ats": "greenhouse", **r} for r in rows]
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


def _rows(ledger: Path) -> list[dict]:
    """The Parquet ledger's rows as dicts, with `ts` and `count` as the strings/ints the
    assertions below compare against (ADR-0120 stores `ts` as a real timestamp)."""
    table = pq.read_table(ledger)
    out = table.to_pylist()
    for r in out:
        r["ts"] = r["ts"].isoformat(timespec="seconds")
    return out


def _run(tmp_path: Path, monkeypatch) -> Path:
    ledger = tmp_path / "role_trends.parquet"
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

    rows = {(r["family"], r["band"]): r["count"] for r in _rows(ledger)}
    assert rows[("software-engineering", "senior")] == 2  # 5 and 6 years band together
    assert rows[("data-science", "intern")] == 1
    # the non-tech row is the diagnostic: one unbanded number, never a chart series
    assert rows[("non-tech", "all")] == 1
    assert ("data-science", "mid") not in rows  # only non-empty groups


def test_ats_becomes_its_own_column_and_splits_same_family_band_rows(
    tmp_path, monkeypatch
):
    """ADR-0075: two rows in the same (family, band) but different ats each get their own
    ledger row, and the non-tech diagnostic is always ats='all' — never split, same as it's
    never banded."""
    _centroids(tmp_path / "rc", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    z = [0.0, 0.0, 1.0, 0.0]  # -> NON-TECH
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": "full_time",
                "min_years": 5,
                "vector": x,
                "ats": "greenhouse",
            },
            {
                "id": "b",
                "title": "Backend Dev",
                "employment_type": "full_time",
                "min_years": 5,
                "vector": x,
                "ats": "lever",
            },
            {
                "id": "c",
                "title": "Data Entry Clerk",
                "employment_type": None,
                "min_years": 2,
                "vector": z,
                "ats": "lever",
            },
        ],
    )
    ledger = _run(tmp_path, monkeypatch)

    rows = {(r["family"], r["band"], r["ats"]): r["count"] for r in _rows(ledger)}
    assert rows[("software-engineering", "senior", "greenhouse")] == 1
    assert rows[("software-engineering", "senior", "lever")] == 1
    assert rows[("non-tech", "all", "all")] == 1  # never split by ats


def test_ledger_accumulates_rows_across_runs(tmp_path, monkeypatch):
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

    rows = _rows(ledger)
    # Parquet has no repeated header to guard against, so what matters is that the second
    # run's rows are ADDED to the first's rather than replacing them — the property the old
    # single-header assertion was really protecting. (Both runs can share a stamp: they land
    # inside the same second, so the stamp is not what distinguishes them.)
    assert [f.name for f in pq.read_schema(ledger)] == list(role_trends._COLUMNS)
    assert len(rows) == 4  # (one stock group + the non-tech diagnostic) per run


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
    ledger = tmp_path / "role_trends.parquet"
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

    rows = {(r["metric"], r["family"], r["band"]): r["count"] for r in _rows(ledger)}
    assert rows[("stock", "software-engineering", "mid")] == 3
    assert rows[("new", "software-engineering", "mid")] == 1  # only the 1-day-old row


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

    rows = {(r["metric"], r["family"], r["band"]): r["count"] for r in _rows(ledger)}
    assert rows[("stock", "watch:fde", "mid")] == 1
    assert rows[("stock", "watch:fde", "senior")] == 1
    # the watched rows still count in their assigned families — the watchlist observes, never moves
    assert rows[("stock", "software-engineering", "mid")] == 2
    assert rows[("stock", "data-science", "senior")] == 1


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
    ledger = tmp_path / "role_trends.parquet"
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


def test_pre_metric_csv_is_folded_into_the_parquet_ledger(tmp_path, monkeypatch):
    """The ledger predates the metric AND ats columns and is append-only on HF, so the
    migration happens where the appends do — old rows become metric=stock, ats=all exactly,
    never a guess."""
    ledger = tmp_path / "role_trends.parquet"
    ledger.with_suffix(".csv").write_text(
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

    rows = _rows(ledger)
    assert [f.name for f in pq.read_schema(ledger)] == list(role_trends._COLUMNS)
    assert rows[0] == {
        "ts": "2026-08-11T00:00:00+00:00",
        "version": 2,
        "metric": "stock",
        "family": "software-engineering",
        "band": "mid",
        "ats": "all",
        "count": 10,
    }
    # every row — folded-in and freshly appended alike — lands on the one schema
    assert all(
        r["metric"] in ("stock", "new") and isinstance(r["count"], int) for r in rows
    )


def test_pre_ats_csv_is_folded_into_the_parquet_ledger(tmp_path, monkeypatch):
    """A ledger already on the ADR-0051 six-column shape (has metric, not ats) gets only
    ats=all stamped — the metric it already carries is trusted, not re-derived."""
    ledger = tmp_path / "role_trends.parquet"
    ledger.with_suffix(".csv").write_text(
        "ts,version,metric,family,band,count\n"
        "2026-08-11T00:00:00+00:00,2,stock,software-engineering,mid,10\n"
        "2026-08-11T00:00:00+00:00,2,new,software-engineering,mid,4\n",
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

    rows = _rows(ledger)
    assert [f.name for f in pq.read_schema(ledger)] == list(role_trends._COLUMNS)
    assert [
        (r["metric"], r["family"], r["band"], r["ats"], r["count"]) for r in rows[:2]
    ] == [
        ("stock", "software-engineering", "mid", "all", 10),
        ("new", "software-engineering", "mid", "all", 4),
    ]
    assert all(r["ats"] for r in rows)  # folded-in and freshly-appended rows alike


def test_a_zero_byte_legacy_csv_does_not_sink_the_run(tmp_path, monkeypatch):
    """A pre-ADR-0120 run killed between `open("a")` and its first write left a 0-byte CSV,
    and that file still arrives from HF. It has no header to read, so folding it in must
    yield nothing rather than raising StopIteration on the empty reader.

    The Parquet ledger itself has no such case: it is written to a temp file and renamed, so
    a killed run leaves the previous ledger intact, never a 0-byte one."""
    ledger = tmp_path / "role_trends.parquet"
    ledger.with_suffix(".csv").touch()
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

    rows = _rows(ledger)
    assert [f.name for f in pq.read_schema(ledger)] == list(role_trends._COLUMNS)
    assert len(rows) == 2  # the one group + the non-tech diagnostic


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
                "ats": "greenhouse",
            },
            {
                "id": "ats:b:ai",
                "title": "ML Engineer",
                "employment_type": "full-time",
                "min_years": 3,
                "vector": [0.0, 1.0, 0.0, 0.0],
                "ats": "greenhouse",
            },
            {
                "id": "ats:b:nontech",
                "title": "Data Entry Clerk",
                "employment_type": "full-time",
                "min_years": 0,
                "vector": [0.0, 0.0, 1.0, 0.0],
                "ats": "greenhouse",
            },
        ],
        schema=pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("title", pa.string()),
                pa.field("employment_type", pa.string()),
                pa.field("min_years", pa.int32()),
                pa.field("vector", pa.list_(pa.float32(), _DIM)),
                pa.field("ats", pa.string()),
            ]
        ),
    )
    _counts, non_tech, assigned = role_trends.count_groups(
        rows, centroids, families, watchlist, "2026-01-01T00:00:00+00:00"
    )
    assert non_tech == 1
    assert assigned == {"ats:b:tech": "software-engineering", "ats:b:ai": "ai-ml"}
    assert not any(k.startswith(roles.WATCH_PREFIX) for k in assigned.values())


def test_top_line_distinguishes_two_atses_sharing_a_family_and_band(
    tmp_path, monkeypatch, caplog
):
    """The counts are keyed on (metric, family, band, ats) but the log dropped `ats` back out.

    Two ATSes' rows for the same family and band then rendered as the same label with different
    numbers — "software-engineering/senior 7044, software-engineering/senior 6637" appeared in
    every run of the 2026-08-25 review — which reads as a double-count in the one ledger this
    repo has already been burned on double-counting. The ledger was always right (ADR-0075); only
    the label was ambiguous.
    """
    _centroids(tmp_path / "rc", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]
    _table(
        tmp_path / "db",
        [
            {
                "id": "greenhouse:b:1",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "ats": "greenhouse",
            },
            {
                "id": "workday:b:2",
                "title": "Dev",
                "employment_type": None,
                "min_years": 3,
                "vector": x,
                "ats": "workday",
            },
        ],
    )
    with caplog.at_level("INFO"):
        _run(tmp_path, monkeypatch)

    line = next(r.message for r in caplog.records if " | top: " in r.message)
    top = line.split(" | top: ")[1].split(" | new in ")[0]
    labels = [entry.rsplit(" ", 1)[0] for entry in top.split(", ")]
    assert len(labels) == len(set(labels)), f"top-5 labels are not unique: {top}"
    assert "software-engineering/mid/greenhouse" in top
    assert "software-engineering/mid/workday" in top
