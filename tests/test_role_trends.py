"""Tests for the trends-ledger step (headstart.ingest.role_trends, ADR-0040/ADR-0051).

Contracts: served rows are counted into (metric, family, band) groups with non-tech held
apart; `new` counts only rows first seen inside the flow window; watched roles are counted by
title in addition to their family; a pre-ADR-0120 CSV ledger of any of its three shapes is
folded into the Parquet ledger without losing a row; the ledger accumulates run over run; and
every degenerate input (missing classifier head, missing family list, empty table, zero-byte or torn
CSV) exits without writing garbage — trends must never sink a run that already scraped and
embedded, nor silently look healthy while accruing nothing.
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
pq = pytest.importorskip("pyarrow.parquet")

from datetime import UTC

import lancedb

from headstart import roles, tech_filter
from headstart.embedding_conventions import PROD_TABLE
from headstart.ingest import index_plan, role_family_classifier, role_trends
from headstart.ingest.doc_prep import DERIVATIONS_VERSION

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
    for r in rows:
        if r.get("vector") is not None and r.get("title"):
            family = _HEAD_FAMILIES[int(np.argmax(r["vector"][: len(_HEAD_FAMILIES)]))]
            _FAMILY_OF_TITLE[role_family_classifier.normalise(r["title"])] = family
    lancedb.connect(db_dir).create_table(
        PROD_TABLE, pa.Table.from_pylist(rows, schema=schema)
    )


# The classifier head the tests run (ADR-0220): three trained families, confident on a one-hot
# title vector. Tests still state each row's family through its `vector`, as they did when a
# nearest centroid decided it: `_table` records that choice against the row's title, and the stub
# encoder below hands the head the matching one-hot vector.
_HEAD_FAMILIES = ("software-engineering", "ai-ml-data-science", roles.NON_TECH)
_FAMILY_OF_TITLE: dict[str, str] = {}
_AMBIGUOUS = "ambiguous"  # a title the head cannot place: it lands in unclassified-tech


@pytest.fixture(autouse=True)
def _stub_title_encoder(monkeypatch):
    """JobBERT is never downloaded in tests: a title encodes to its family's one-hot vector."""
    _FAMILY_OF_TITLE.clear()

    def encode(titles, model, revision):
        rows = []
        for title in titles:
            family = _FAMILY_OF_TITLE.get(title, "software-engineering")
            if family == _AMBIGUOUS:
                rows.append([1.0] * len(_HEAD_FAMILIES))
            else:
                rows.append([float(family == f) for f in _HEAD_FAMILIES])
        return np.array(rows, dtype=np.float32)

    monkeypatch.setattr(role_family_classifier, "encode", encode)


def _taxonomy(
    head: Path, families_path: Path, extra_families: tuple[str, ...] = ()
) -> None:
    """The head (cutoff 0.6, so a one-hot title is placed and an ambiguous one is not) and the
    curated family list it must agree with."""
    head.mkdir(parents=True, exist_ok=True)
    np.savez(
        head / "head.npz",
        weights=np.eye(len(_HEAD_FAMILIES), dtype=np.float32) * 10,
        bias=np.zeros(len(_HEAD_FAMILIES), dtype=np.float32),
    )
    (head / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "model": "stub",
                "model_revision": "stub",
                "families": list(_HEAD_FAMILIES),
                "cutoff": 0.6,
            }
        ),
        encoding="utf-8",
    )
    families_path.parent.mkdir(parents=True, exist_ok=True)
    listed = [
        "software-engineering",
        "ai-ml-data-science",
        *extra_families,
        "unclassified-tech",
    ]
    families_path.write_text(
        json.dumps({"families": [{"name": name} for name in listed]}), encoding="utf-8"
    )


def _rows(ledger: Path) -> list[dict]:
    """The Parquet ledger's rows as dicts, with `ts` rendered to the stamp string the
    assertions below compare against (ADR-0120 stores it as a real timestamp)."""
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
            "--classifier",
            str(tmp_path / "head"),
            "--title-cache",
            str(tmp_path / "title_cache.parquet"),
            "--families",
            str(tmp_path / "families.json"),
            "--ledger",
            str(ledger),
            "--board-ledger",
            str(tmp_path / "liveness"),
            "--board-counts",
            str(tmp_path / "board_counts.parquet"),
            "--board-deltas",
            str(tmp_path / "board_deltas"),
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
            # Pinned too (ADR-0164): defaults to the repo's real data/state/trends_epochs.csv.
            "--epochs",
            str(tmp_path / "trends_epochs.csv"),
            # Pinned too (ADR-0222): these default to this run's real prune and scrape hand-offs.
            "--pruned-ids",
            str(tmp_path / "pruned_ids.txt"),
            "--unauthoritative-boards",
            str(tmp_path / "unauthoritative_boards.json"),
        ],
    )
    assert role_trends.main() == 0
    return ledger


def test_counts_rows_by_family_and_band_and_isolates_non_tech(tmp_path, monkeypatch):
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> ai-ml-data-science
    z = [0.0, 0.0, 1.0, 0.0]  # -> non-tech
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
    assert rows[("ai-ml-data-science", "intern")] == 1
    # the non-tech row is the diagnostic: one unbanded number, never a chart series
    assert rows[("non-tech", "all")] == 1
    assert ("ai-ml-data-science", "mid") not in rows  # only non-empty groups


def test_a_tick_records_one_epoch_row_then_stays_quiet_while_unchanged(
    tmp_path, monkeypatch
):
    """End-to-end (ADR-0164): the centroid column's `none`, the family-list fingerprint, the
    tech-filter, derivations and dedup versions and the classifier head's version all reach the
    epoch file through main() unchanged, and a second tick with nothing different writes no second
    row."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": None,
                "min_years": 5,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    epochs = tmp_path / "trends_epochs.csv"
    _run(tmp_path, monkeypatch)
    rows = list(csv.reader(epochs.open(encoding="utf-8", newline="")))
    assert len(rows) == 2  # header + exactly one boundary
    (
        _,
        centroid_version,
        fingerprint,
        tech_filter_version,
        derivations_version,
        dedup_version,
        family_classifier_version,
    ) = rows[1]
    assert centroid_version == "none"  # no centroid fit decides anything (ADR-0220)
    assert fingerprint  # a real hash, not asserting its exact value
    assert tech_filter_version == str(tech_filter.TECH_FILTER_VERSION)
    assert derivations_version == str(DERIVATIONS_VERSION)
    assert dedup_version == str(index_plan.DEDUP_VERSION)
    assert (
        family_classifier_version == "1"
    )  # the head's own version, not the series version

    _run(tmp_path, monkeypatch)  # nothing about the taxonomy or the code changed
    rows_again = list(csv.reader(epochs.open(encoding="utf-8", newline="")))
    assert rows_again == rows


def test_a_tick_is_stamped_with_the_run_stamp_prune_used(tmp_path, monkeypatch):
    """ADR-0210: the dedup eviction ledger `index prune` writes and this ledger carry the same
    `ts`, so Trends joins a removal to the tick it happened in."""
    from headstart.ingest import RUN_TS_ENV

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [
            {
                "id": "a",
                "title": "Backend Dev",
                "employment_type": None,
                "min_years": 5,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00+00:00")
    ledger = _run(tmp_path, monkeypatch)
    stamps = {str(ts) for ts in pq.read_table(ledger).column("ts").to_pylist()}
    assert stamps == {"2026-09-25 06:00:00+00:00"}


def test_ats_becomes_its_own_column_and_splits_same_family_band_rows(
    tmp_path, monkeypatch
):
    """ADR-0075: two rows in the same (family, band) but different ats each get their own
    ledger row, and the non-tech diagnostic is always ats='all' — never split, same as it's
    never banded."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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


def test_missing_classifier_head_degrades_to_noop(tmp_path, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)  # no _taxonomy(), no table — must not matter
    assert not ledger.exists()
    assert any("skipping trends" in r.getMessage() for r in caplog.records)


def test_missing_family_list_degrades_to_noop(tmp_path, monkeypatch, caplog):
    """The workflow step is continue-on-error, which would turn an unguarded FileNotFoundError
    into a green run that never accrues a row."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(tmp_path / "db", [])
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("is empty" in r.getMessage() for r in caplog.records)


def test_a_head_deciding_an_unlisted_family_errors_visibly_instead_of_silently(
    tmp_path, monkeypatch, caplog
):
    """A head trained for families the curated list no longer names (ADR-0220). The workflow
    step is continue-on-error, so an unguarded ValueError would crash into a green run with no
    annotation — it must surface as ERROR (an ::error:: under Actions) and exit non-zero."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    (tmp_path / "families.json").write_text(
        json.dumps(
            {
                "families": [
                    {"name": "software-engineering"},
                    {"name": "unclassified-tech"},
                ]
            }
        ),
        encoding="utf-8",
    )
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
            "--classifier",
            str(tmp_path / "head"),
            "--title-cache",
            str(tmp_path / "title_cache.parquet"),
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


def test_half_landed_classifier_head_degrades_to_noop(tmp_path, monkeypatch, caplog):
    # manifest without weights: Head() would crash on the missing file
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    (tmp_path / "head" / "head.npz").unlink()
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("head.npz" in r.getMessage() for r in caplog.records)


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

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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


def test_watch_role_counts_by_title_regardless_of_family(tmp_path, monkeypatch):
    """The pattern is the definition (ADR-0051): an FDE posting counts under watch:fde whatever
    family the classifier gave it."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> ai-ml-data-science: an FDE filed there still counts
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
    assert rows[("stock", "ai-ml-data-science", "senior")] == 1


def test_the_classifier_decides_each_family_and_watch_roles_count_tech_only(
    tmp_path, monkeypatch
):
    """ADR-0220: a row's family is its title's verdict under the classifier head, a title the
    head cannot place is counted as unclassified-tech, a watch role skips a row the head called
    non-tech, every ledger row carries the head's series version, and the next run reuses the
    title cache instead of encoding again."""
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _watchlist(
        tmp_path,
        [
            {
                "name": "frontend",
                "label": "Frontend",
                "parent": "software-engineering",
                "match": ["\\bfront[\\s-]?end\\b"],
            }
        ],
    )
    x = [1.0, 0.0, 0.0, 0.0]  # -> software-engineering
    y = [0.0, 1.0, 0.0, 0.0]  # -> ai-ml-data-science
    z = [0.0, 0.0, 1.0, 0.0]  # -> non-tech
    _table(
        tmp_path / "db",
        [
            {"id": "a", "title": "Frontend Engineer", "vector": x},
            {"id": "b", "title": "ML Engineer", "vector": y},
            {"id": "c", "title": "Front End Clerk", "vector": z},
            # a vector like every served row; the head is told below it cannot place this title
            {"id": "d", "title": "Vague Title", "vector": [0.0, 0.0, 0.0, 1.0]},
        ],
    )
    _FAMILY_OF_TITLE["vague title"] = _AMBIGUOUS
    ledger = _run(tmp_path, monkeypatch)

    written = _rows(ledger)
    rows = {(r["metric"], r["family"]): r["count"] for r in written}
    assert rows[("stock", "software-engineering")] == 1
    assert rows[("stock", "ai-ml-data-science")] == 1
    assert rows[("stock", "unclassified-tech")] == 1  # the head could not place it
    assert rows[("stock", "non-tech")] == 1  # the clerk
    assert rows[("stock", "watch:frontend")] == 1  # the engineer; the clerk is not tech
    assert {r["version"] for r in written} == {role_trends.series_version(1)}

    cache = role_family_classifier.load_cache(tmp_path / "title_cache.parquet", 1)
    assert len(cache.decisions) == 4
    encoded = []
    monkeypatch.setattr(
        role_family_classifier, "encode", lambda t, m, r: encoded.append(t)
    )
    _run(tmp_path, monkeypatch)
    assert encoded == []  # every title was already decided under this head


def test_trends_wait_while_a_new_heads_title_cache_warms_up(
    tmp_path, monkeypatch, caplog
):
    """A new head starts with an empty cache. A run whose budget cannot cover the served titles
    fills what it can and counts nothing, rather than charting the backlog as unclassified."""
    import logging

    _taxonomy(tmp_path / "head", tmp_path / "families.json")
    _table(
        tmp_path / "db",
        [{"id": "a", "title": "Backend Dev", "vector": [1.0, 0.0, 0.0, 0.0]}],
    )
    epochs = tmp_path / "trends_epochs.csv"
    epochs.write_text(
        "ts,centroid_version,family_map_fingerprint,tech_filter_version,"
        "derivations_version,dedup_version,family_rules_fingerprint\n"
        "2026-09-24T21:19:12+00:00,2,f,5,15,4,3b5cc5d9183c\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.WARNING, logger="headstart.ingest.role_trends")
    monkeypatch.setattr(role_trends, "_CLASSIFY_BUDGET_SECONDS", -1.0)
    ledger = _run(tmp_path, monkeypatch)
    assert not ledger.exists()
    assert any("warming up" in r.getMessage() for r in caplog.records)
    # the epoch file is in its current shape even though the run counted nothing
    assert (
        epochs.read_text(encoding="utf-8")
        .splitlines()[0]
        .endswith("family_classifier_version")
    )


def test_watchlist_with_unknown_parent_errors_visibly(tmp_path, monkeypatch, caplog):
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
            "--classifier",
            str(tmp_path / "head"),
            "--title-cache",
            str(tmp_path / "title_cache.parquet"),
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
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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


def test_current_shape_csv_is_folded_into_the_parquet_ledger(tmp_path, monkeypatch):
    """The seven-column CSV is the shape the real HF ledger is in, so this is the branch the
    production cutover actually takes — the other two migrate shapes that were already gone.

    Its rows carry over verbatim: `ats` is whatever the row said, not the `all` sentinel the
    older shapes get stamped with."""
    ledger = tmp_path / "role_trends.parquet"
    ledger.with_suffix(".csv").write_text(
        "ts,version,metric,family,band,ats,count\n"
        "2026-08-11T00:00:00+00:00,2,stock,software-engineering,mid,greenhouse,10\n"
        "2026-08-11T00:00:00+00:00,2,new,ai-ml,senior,lever,4\n",
        encoding="utf-8",
    )
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
    # both carried rows, then this run's group + the non-tech diagnostic
    assert len(rows) == 4
    assert [
        (r["metric"], r["family"], r["band"], r["ats"], r["count"]) for r in rows[:2]
    ] == [
        ("stock", "software-engineering", "mid", "greenhouse", 10),
        ("new", "ai-ml", "senior", "lever", 4),
    ]


def test_a_torn_legacy_csv_row_fails_loudly_rather_than_shifting_columns(tmp_path):
    """The pre-ADR-0120 writer appended without a temp-file rename, so a killed run could leave
    a truncated final line. Folding that in must not quietly produce a mis-shaped table.

    It does not: the short row makes `zip(*rows)` yield fewer than seven columns, and the
    unpack raises. Pinned as a test because it is the reason `_legacy_rows` needs no arity
    check of its own — the guard already exists, one layer down."""
    csv_ledger = tmp_path / "role_trends.csv"
    csv_ledger.write_text(
        "ts,version,metric,family,band,ats,count\n"
        "2026-08-11T00:00:00+00:00,2,stock,software-engineering,mid,all,10\n"
        "2026-08-11T00:00:00+00:00,2,stock,ai-ml\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not enough values to unpack"):
        role_trends._to_table(role_trends._legacy_rows(csv_ledger))


def test_a_zero_byte_legacy_csv_does_not_sink_the_run(tmp_path, monkeypatch):
    """A pre-ADR-0120 run killed between `open("a")` and its first write left a 0-byte CSV,
    and that file still arrives from HF. It has no header to read, so folding it in must
    yield nothing rather than raising StopIteration on the empty reader.

    The Parquet ledger itself has no such case: it is written to a temp file and renamed, so
    a killed run leaves the previous ledger intact, never a 0-byte one."""
    ledger = tmp_path / "role_trends.parquet"
    ledger.with_suffix(".csv").touch()
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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
    family_of = {
        "Backend Engineer": "software-engineering",
        "ML Engineer": "ai-ml-data-science",
        "Data Entry Clerk": None,  # non-tech
    }.get
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
            {"software-engineering", "ai-ml-data-science"},
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
        rows, family_of, watchlist, "2026-01-01T00:00:00+00:00"
    )
    assert non_tech == 1
    assert assigned == {
        "ats:b:tech": "software-engineering",
        "ats:b:ai": "ai-ml-data-science",
    }
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
    _taxonomy(tmp_path / "head", tmp_path / "families.json")
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


def _tick_rows(path: Path) -> list[dict]:
    return pq.read_table(path).to_pylist()


def test_a_second_tick_books_turnover_beside_the_level_changes(tmp_path, monkeypatch):
    """ADR-0222 end to end. The first tick writes the snapshot that turnover diffs. The second
    books a new posting as opened, an evicted one as closed, a pruned duplicate as recounted,
    and one marker for an Unauthoritative Board. All of it goes in the tick's own delta file,
    and the Board counts carry levels only."""
    from headstart.ingest import RUN_TS_ENV

    _taxonomy(tmp_path / "head", tmp_path / "families.json")

    def row(job_id: str, seen: str) -> dict:
        return {
            "id": f"greenhouse:acme:{job_id}",
            "title": "Backend Dev",
            "employment_type": None,
            "min_years": 5,
            "vector": [1.0, 0.0, 0.0, 0.0],
            "first_seen": seen,
        }

    early = "2026-09-20T00:00:00+00:00"
    _table(
        tmp_path / "db", [row("stays", early), row("closes", early), row("dup", early)]
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T05:00:00+00:00")
    _run(tmp_path, monkeypatch)
    first = _tick_rows(tmp_path / "board_deltas" / "2026-09-25T05-00-00+00-00.parquet")
    assert {r["metric"] for r in first} == {"stock", "new"}, (
        "no snapshot to diff yet, so the first tick books no turnover"
    )

    lancedb.connect(tmp_path / "db").drop_table(PROD_TABLE)
    _table(
        tmp_path / "db",
        [row("stays", early), row("opens", "2026-09-25T05:30:00+00:00")],
    )
    (tmp_path / "pruned_ids.txt").write_text("greenhouse:acme:dup\n", encoding="utf-8")
    (tmp_path / "unauthoritative_boards.json").write_text(
        json.dumps({"greenhouse:acme": "truncated"}), encoding="utf-8"
    )
    monkeypatch.setenv(RUN_TS_ENV, "2026-09-25T06:00:00+00:00")
    _run(tmp_path, monkeypatch)

    tick = _tick_rows(tmp_path / "board_deltas" / "2026-09-25T06-00-00+00-00.parquet")
    flows = {
        r["metric"]: r["delta"] for r in tick if r["metric"] not in ("stock", "new")
    }
    assert flows == {"opened": 1, "closed": 1, "recounted_out": 1, "unscoped": 1}
    stock = sum(r["delta"] for r in tick if r["metric"] == "stock")
    assert stock == flows["opened"] - flows["closed"] - flows["recounted_out"]
    counts = pq.read_table(tmp_path / "board_counts.parquet").to_pylist()
    assert {r["metric"] for r in counts} <= {"stock", "new"}


def test_recovering_board_counts_skips_a_ticks_flow_rows(tmp_path):
    """A tick's delta file carries its flows too (ADR-0222). Replayed as level changes after a
    failed counts save, an `opened` row would have become a Board count of its own."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    key = ("greenhouse:acme", "software-engineering", "senior", "greenhouse")
    table = pa.table(
        {
            "ts": ["2026-09-25T06:00:00+00:00"] * 2,
            "board": [key[0]] * 2,
            "metric": ["stock", "opened"],
            "family": [key[1]] * 2,
            "band": [key[2]] * 2,
            "ats": [key[3]] * 2,
            "delta": [1, 1],
        },
        metadata={b"centroid_version": b"3001"},
    )
    pq.write_table(table, deltas / "2026-09-25T06-00-00+00-00.parquet")
    recovered = role_trends._recover_board_counts(
        {}, "2026-09-25T05:00:00+00:00", deltas, 3001
    )
    assert recovered == {(key[0], "stock", *key[1:]): 1}
