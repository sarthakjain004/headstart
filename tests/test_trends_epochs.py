"""Tests for the trends methodology-epoch ledger (ADR-0164).

The centroid version already marks the one methodology break `role_trends` knows how to
segment on. This module marks the others — a family-map curation edit, a tech-filter version
bump, a derivations-version bump — so a level shift in the chart caused by a definition change
is distinguishable from one caused by real hiring movement. Every case here is a way that
distinction could otherwise be lost: nothing changed, one thing changed, everything changed at
once, and a first run with nothing to compare against.
"""

from __future__ import annotations

import csv

from headstart.ingest import trends_epochs


def _stamp(**overrides):
    base = {
        "centroid_version": 1,
        "family_map_fingerprint": "abc123",
        "tech_filter_version": 1,
        "derivations_version": 12,
        "dedup_version": 1,
        "family_rules_fingerprint": "def456",
    }
    base.update(overrides)
    return base


def test_first_stamp_always_writes(tmp_path):
    path = tmp_path / "trends_epochs.csv"
    wrote = trends_epochs.append_if_changed(
        path, "2026-09-16T00:00:00+00:00", **_stamp()
    )
    assert wrote is True
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [
        list(trends_epochs._COLUMNS),
        ["2026-09-16T00:00:00+00:00", "1", "abc123", "1", "12", "1", "def456"],
    ]


def test_an_unchanged_stamp_writes_nothing(tmp_path):
    path = tmp_path / "trends_epochs.csv"
    trends_epochs.append_if_changed(path, "2026-09-16T00:00:00+00:00", **_stamp())
    wrote = trends_epochs.append_if_changed(
        path, "2026-09-16T02:00:00+00:00", **_stamp()
    )
    assert wrote is False
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2  # header + the one real row, the repeat never landed


def test_a_changed_field_writes_a_new_row(tmp_path):
    path = tmp_path / "trends_epochs.csv"
    trends_epochs.append_if_changed(path, "2026-09-16T00:00:00+00:00", **_stamp())
    wrote = trends_epochs.append_if_changed(
        path, "2026-09-16T02:00:00+00:00", **_stamp(tech_filter_version=2)
    )
    assert wrote is True
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 3
    assert rows[-1] == [
        "2026-09-16T02:00:00+00:00",
        "1",
        "abc123",
        "2",
        "12",
        "1",
        "def456",
    ]


def test_every_field_can_trigger_a_row_independently(tmp_path):
    path = tmp_path / "trends_epochs.csv"
    trends_epochs.append_if_changed(path, "t0", **_stamp())
    assert trends_epochs.append_if_changed(path, "t1", **_stamp(centroid_version=2))
    assert trends_epochs.append_if_changed(
        path, "t2", **_stamp(family_map_fingerprint="xyz")
    )
    assert trends_epochs.append_if_changed(path, "t3", **_stamp(derivations_version=13))
    assert trends_epochs.append_if_changed(path, "t4", **_stamp(dedup_version=2))
    assert trends_epochs.append_if_changed(
        path, "t5", **_stamp(dedup_version=2, family_rules_fingerprint="ghi789")
    )
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 7  # header + 6 real boundaries


def test_a_missing_file_is_the_same_as_no_prior_stamp(tmp_path):
    path = tmp_path / "does" / "not" / "exist.csv"
    assert trends_epochs.append_if_changed(path, "t0", **_stamp()) is True


def test_a_malformed_file_heals_by_rebuilding_rather_than_appending_beneath_it(
    tmp_path,
):
    path = tmp_path / "trends_epochs.csv"
    path.write_text("not,the,right,header\n1,2,3,4\n", encoding="utf-8")
    wrote = trends_epochs.append_if_changed(path, "t0", **_stamp())
    assert wrote is True
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    # rebuilt from scratch, not appended beneath the garbage — otherwise every future tick
    # would keep failing to parse a "previous" stamp and would write on every run forever
    assert rows == [
        list(trends_epochs._COLUMNS),
        ["t0", "1", "abc123", "1", "12", "1", "def456"],
    ]


# A file from before ``dedup_version`` (ADR-0188) is also from before ``family_rules_fingerprint``.
_HEADER_WITHOUT_DEDUP_VERSION = ",".join(trends_epochs._COLUMNS[:-2]) + "\n"
_HEADER_WITHOUT_FAMILY_RULES = ",".join(trends_epochs._COLUMNS[:-1]) + "\n"
# The rules column's value on rows written before it existed. A stamp carrying it is the same
# methodology those rows had, which isolates the dedup upgrade from the rules boundary below.
_NO_RULES = trends_epochs._FAMILY_RULES_AT_ADDITION


def test_a_file_from_before_dedup_version_is_upgraded_not_rebuilt(tmp_path):
    """The rows written before ``dedup_version`` existed are real boundaries the Space still
    marks. Rebuilding on the header mismatch, as a corrupt file is, would silently erase them;
    they carry the version the rules had when the column was added instead."""
    path = tmp_path / "trends_epochs.csv"
    path.write_text(
        _HEADER_WITHOUT_DEDUP_VERSION + "t0,1,abc123,1,11\nt1,1,abc123,1,12\n",
        encoding="utf-8",
    )
    wrote = trends_epochs.append_if_changed(
        path, "t2", **_stamp(family_rules_fingerprint=_NO_RULES)
    )
    assert wrote is False  # the same methodology as t1: an upgrade is not a boundary
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [
        list(trends_epochs._COLUMNS),
        ["t0", "1", "abc123", "1", "11", "1", _NO_RULES],
        ["t1", "1", "abc123", "1", "12", "1", _NO_RULES],
    ]
    assert list(tmp_path.iterdir()) == [
        path
    ]  # renamed into place, nothing left beside it


def test_a_dedup_change_on_an_upgraded_file_appends_after_the_old_rows(tmp_path):
    path = tmp_path / "trends_epochs.csv"
    path.write_text(
        _HEADER_WITHOUT_DEDUP_VERSION + "t0,1,abc123,1,12\n", encoding="utf-8"
    )
    assert trends_epochs.append_if_changed(
        path, "t1", **_stamp(dedup_version=2, family_rules_fingerprint=_NO_RULES)
    )
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [
        list(trends_epochs._COLUMNS),
        ["t0", "1", "abc123", "1", "12", "1", _NO_RULES],
        ["t1", "1", "abc123", "1", "12", "2", _NO_RULES],
    ]


def test_title_rules_arriving_on_a_file_from_before_them_are_one_boundary(tmp_path):
    """ADR-0215: the tick that first runs title rules finds a file without their column. Its old
    rows are kept, stamped as having had no rules, and the rules' first tick is a boundary the
    Space marks; the tick after it is not."""
    path = tmp_path / "trends_epochs.csv"
    path.write_text(
        _HEADER_WITHOUT_FAMILY_RULES + "t0,2,abc123,1,12,1\n", encoding="utf-8"
    )
    assert trends_epochs.append_if_changed(path, "t1", **_stamp(centroid_version=2))
    assert not trends_epochs.append_if_changed(path, "t2", **_stamp(centroid_version=2))
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [
        list(trends_epochs._COLUMNS),
        ["t0", "2", "abc123", "1", "12", "1", _NO_RULES],
        ["t1", "2", "abc123", "1", "12", "1", "def456"],
    ]


def test_a_blank_line_in_an_old_file_is_not_upgraded_into_a_false_boundary(tmp_path):
    path = tmp_path / "trends_epochs.csv"
    path.write_text(
        _HEADER_WITHOUT_DEDUP_VERSION + "t0,1,abc123,1,12\n\n", encoding="utf-8"
    )
    assert (
        trends_epochs.append_if_changed(
            path, "t1", **_stamp(family_rules_fingerprint=_NO_RULES)
        )
        is False
    )
    with path.open(encoding="utf-8", newline="") as fh:
        assert list(csv.reader(fh))[1:] == [
            ["t0", "1", "abc123", "1", "12", "1", _NO_RULES]
        ]


def test_a_failed_upgrade_leaves_the_old_file_and_no_staged_copy(tmp_path, monkeypatch):
    path = tmp_path / "trends_epochs.csv"
    old = _HEADER_WITHOUT_DEDUP_VERSION + "t0,1,abc123,1,12\n"
    path.write_text(old, encoding="utf-8")

    def fail(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(type(path), "replace", fail)
    try:
        trends_epochs.append_if_changed(path, "t1", **_stamp())
    except OSError:
        pass
    assert path.read_text(encoding="utf-8") == old
    assert list(tmp_path.iterdir()) == [path]
