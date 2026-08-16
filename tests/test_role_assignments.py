"""Tests for the family-assignment snapshot and transition ledger (ADR-0057).

The module exists because counting stock per tick cannot tell a closure apart from a
reassignment. Every case here is one of the ways that distinction can be lost: a row that moved,
a row that only appears on one side, a centroid refit that makes the comparison meaningless, and
a corrupt snapshot that must degrade rather than sink the run.
"""

from __future__ import annotations

import csv

import pytest

pytest.importorskip("pyarrow")

from headstart.ingest import role_assignments as ra  # noqa: E402


def test_transitions_counts_only_rows_present_on_both_sides():
    previous = {
        "a": "software-engineering",
        "b": "ai-ml",
        "gone": "software-engineering",
    }
    current = {"a": "hardware-embedded", "b": "ai-ml", "brand-new": "web-development"}
    moved = ra.transitions(previous, current)
    # 'a' moved; 'b' stayed; 'gone' was evicted; 'brand-new' was added — only 'a' is a transition.
    assert moved == {("software-engineering", "hardware-embedded"): 1}


def test_no_previous_snapshot_yields_no_transitions_not_an_empty_diff():
    """The first run must report nothing moved, and must not claim everything stayed put."""
    assert ra.transitions(None, {"a": "ai-ml"}) == {}


def test_transitions_aggregates_identical_moves():
    previous = {f"j{i}": "software-engineering" for i in range(5)}
    current = {f"j{i}": "systems-engineering" for i in range(5)}
    assert ra.transitions(previous, current) == {
        ("software-engineering", "systems-engineering"): 5
    }


def test_snapshot_round_trips(tmp_path):
    path = tmp_path / "role_assignments.parquet"
    assignments = {"ats:board:1": "ai-ml", "ats:board:2": "software-engineering"}
    ra.save(path, assignments, version=2)
    assert ra.load_previous(path, version=2) == assignments


def test_a_centroid_refit_makes_the_previous_snapshot_incomparable(tmp_path):
    """A refit re-bases every assignment, so transitions across versions are meaningless.

    Returning None (not the stale mapping) is what stops a re-base from being reported as every
    job in the corpus changing family at once.
    """
    path = tmp_path / "role_assignments.parquet"
    ra.save(path, {"ats:board:1": "ai-ml"}, version=2)
    assert ra.load_previous(path, version=3) is None
    assert ra.load_previous(path, version=2) is not None


def test_an_unstamped_snapshot_is_not_treated_as_comparable(tmp_path):
    """A snapshot with no provenance is the least vouchable kind, so it must not be diffed.

    Written by hand rather than via `save`, because `save` always stamps — the case this guards
    is a file from another tool, an older build, or a partially-recovered artifact.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "role_assignments.parquet"
    pq.write_table(
        pa.table({"id": pa.array(["x"]), "family": pa.array(["ai-ml"])}), path
    )
    assert ra.load_previous(path, version=2) is None


def test_a_corrupt_snapshot_degrades_instead_of_raising(tmp_path):
    path = tmp_path / "role_assignments.parquet"
    path.write_bytes(b"not a parquet file")
    assert ra.load_previous(path, version=2) is None


def test_missing_snapshot_is_not_an_error(tmp_path):
    assert ra.load_previous(tmp_path / "absent.parquet", version=2) is None


def test_save_is_atomic_leaving_no_partial_file(tmp_path):
    """A killed run must leave the previous snapshot, never a half-written one."""
    path = tmp_path / "role_assignments.parquet"
    ra.save(path, {"a": "ai-ml"}, version=2)
    ra.save(path, {"a": "web-development"}, version=2)
    assert not list(tmp_path.glob("*.tmp"))
    assert ra.load_previous(path, version=2) == {"a": "web-development"}


def test_ledger_writes_a_header_once_then_appends(tmp_path):
    ledger = tmp_path / "role_reassignments.csv"
    n = ra.append_ledger(
        ledger, {("a", "b"): 3}, version=2, ts="2026-08-16T00:00:00+00:00"
    )
    assert n == 1
    ra.append_ledger(ledger, {("b", "c"): 1}, version=2, ts="2026-08-16T02:00:00+00:00")
    rows = list(csv.reader(ledger.open()))
    assert rows[0] == list(ra._COLUMNS)
    assert rows[1][2:] == ["a", "b", "3"]
    assert rows[2][2:] == ["b", "c", "1"]
    assert len(rows) == 3  # exactly one header


def test_ledger_writes_nothing_when_nothing_moved(tmp_path):
    ledger = tmp_path / "role_reassignments.csv"
    assert ra.append_ledger(ledger, {}, version=2, ts="2026-08-16T00:00:00+00:00") == 0
    assert not ledger.exists()  # a quiet tick leaves no row to misread as data
