"""The hot-list ranking, and the four traps that each produce a plausible wrong list."""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from headstart.ingest import hot_boards

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def _counts(rows: list[tuple[str, str, str, int]]) -> pa.Table:
    return pa.table(
        {
            "board": [r[0] for r in rows],
            "metric": [r[1] for r in rows],
            "family": [r[2] for r in rows],
            "band": ["all"] * len(rows),
            "ats": [r[0].split(":", 1)[0] for r in rows],
            "count": [r[3] for r in rows],
        }
    )


def _deltas(ts: str, rows: list[tuple[str, str, str, int]]) -> pa.Table:
    return pa.table(
        {
            "ts": [ts] * len(rows),
            "board": [r[0] for r in rows],
            "metric": [r[1] for r in rows],
            "family": [r[2] for r in rows],
            "band": ["all"] * len(rows),
            "ats": [r[0].split(":", 1)[0] for r in rows],
            "delta": [r[3] for r in rows],
        }
    )


def test_watch_families_are_excluded_from_levels(tmp_path: Path) -> None:
    """`watch:` families double-count against centroid families (ADR-0051)."""
    path = tmp_path / "counts.parquet"
    pq.write_table(
        _counts(
            [
                ("greenhouse:acme", "stock", "software-engineering", 10),
                ("greenhouse:acme", "stock", "watch:rust", 7),
                ("greenhouse:acme", "new", "software-engineering", 3),
                ("greenhouse:acme", "new", "watch:rust", 2),
            ]
        ),
        path,
    )
    new, stock = hot_boards.read_levels(path)
    assert stock["greenhouse:acme"] == 10
    assert new["greenhouse:acme"] == 3


def test_baseline_tick_is_not_a_change(tmp_path: Path) -> None:
    """The ledger's first file dumps every Board's whole stock; summing it invents growth."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    pq.write_table(
        _deltas("2026-09-13T12:00:00+00:00", [("greenhouse:acme", "stock", "se", 500)]),
        deltas / "2026-09-13T12-00-00+00-00.parquet",
    )
    pq.write_table(
        _deltas("2026-09-14T12:00:00+00:00", [("greenhouse:acme", "stock", "se", 4)]),
        deltas / "2026-09-14T12-00-00+00-00.parquet",
    )
    moved, stamps = hot_boards.read_stock_change(deltas)
    assert moved["greenhouse:acme"] == 4, (
        "the 500-row baseline must not count as growth"
    )
    assert stamps == ["2026-09-14T12:00:00+00:00"]


def test_the_window_is_bounded_so_it_cannot_outgrow_the_new_metric(
    tmp_path: Path,
) -> None:
    """Expansion and Volume print on one row, so they must span the same number of days.

    An unbounded sum grows by a run every run: within a day of shipping, "net roles" would have
    covered a longer period than "opened this week" under one heading.
    """
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    ticks = [
        ("2026-09-01T12:00:00+00:00", 500),  # baseline dump, always dropped
        ("2026-09-02T12:00:00+00:00", 7),  # inside the ledger, outside the window
        ("2026-09-20T12:00:00+00:00", 3),  # inside the window
        ("2026-09-21T12:00:00+00:00", 4),
    ]
    for ts, delta in ticks:
        pq.write_table(
            _deltas(ts, [("greenhouse:acme", "stock", "se", delta)]),
            deltas / f"{ts.replace(':', '-')}.parquet",
        )
    moved, stamps = hot_boards.read_stock_change(deltas)
    assert moved["greenhouse:acme"] == 7, "only the two ticks inside the 7-day window"
    assert min(stamps) == "2026-09-20T12:00:00+00:00"


def test_a_refit_segments_the_window_by_centroid_version(tmp_path: Path) -> None:
    """A refit starts a new version with its own full-stock baseline (ADR-0040/ADR-0143).

    Summed, that baseline reads as every Board having just been discovered, so each version's
    first tick is dropped; the real changes on either side of the refit are summed.
    """
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    ticks = [
        ("2026-09-20T12:00:00+00:00", 2, 500),  # v2 baseline
        ("2026-09-21T12:00:00+00:00", 2, 5),  # v2 change: still a change in stock
        ("2026-09-22T12:00:00+00:00", 3, 505),  # the refit's v3 baseline
        ("2026-09-23T12:00:00+00:00", 3, 3),
    ]
    for ts, version, delta in ticks:
        table = _deltas(ts, [("greenhouse:acme", "stock", "se", delta)])
        table = table.replace_schema_metadata({"centroid_version": str(version)})
        pq.write_table(table, deltas / f"{ts.replace(':', '-')}.parquet")
    moved, stamps = hot_boards.read_stock_change(deltas)
    assert moved["greenhouse:acme"] == 8
    assert stamps == ["2026-09-21T12:00:00+00:00", "2026-09-23T12:00:00+00:00"]


def test_only_a_baseline_means_no_measured_window(tmp_path: Path) -> None:
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    pq.write_table(
        _deltas("2026-09-13T12:00:00+00:00", [("greenhouse:acme", "stock", "se", 500)]),
        deltas / "2026-09-13T12-00-00+00-00.parquet",
    )
    moved, stamps = hot_boards.read_stock_change(deltas)
    assert not stamps and not moved


def _rank(new: dict, stock: dict, moved: dict, names: dict | None = None):
    return hot_boards.rank(
        collections.Counter(new),
        collections.Counter(stock),
        collections.Counter(moved),
        names or {},
    )


def test_newly_discovered_boards_are_excluded_not_ranked() -> None:
    """A Board whose whole stock arrived in the window is new to us, not newly hiring."""
    lenses, excluded = _rank(
        new={"greenhouse:found": 90, "greenhouse:real": 20},
        stock={"greenhouse:found": 100, "greenhouse:real": 200},
        moved={"greenhouse:found": 100, "greenhouse:real": 30},
    )
    assert [r["board"] for r in lenses["expansion"]] == ["greenhouse:real"]
    assert excluded["newly_discovered"] == 1


def test_small_boards_are_not_ranked() -> None:
    """One posting on a three-posting Board is a 33% hiring rate and pure noise."""
    lenses, excluded = _rank(
        new={"greenhouse:tiny": 1}, stock={"greenhouse:tiny": 3}, moved={}
    )
    assert lenses["rate"] == []
    assert excluded["below_min_stock"] == 1


def test_expansion_separates_growth_from_churn() -> None:
    """Amazon's measured shape: ~1,400 roles opened in a week at a near-zero net change."""
    lenses, _ = _rank(
        new={"a:churner": 1396, "b:grower": 100},
        stock={"a:churner": 9081, "b:grower": 500},
        moved={"a:churner": -3, "b:grower": 200},
    )
    assert [r["board"] for r in lenses["expansion"]] == ["b:grower"]
    assert [r["board"] for r in lenses["volume"]] == ["a:churner", "b:grower"]


def test_one_company_on_two_atses_collapses_to_one_row() -> None:
    """EWOR reached the Rate lens twice, from Teamtailor and Recruitee."""
    lenses, _ = _rank(
        new={"teamtailor:eworgmbh": 163, "recruitee:eworgmbh": 153},
        stock={"teamtailor:eworgmbh": 164, "recruitee:eworgmbh": 169},
        moved={"teamtailor:eworgmbh": 16, "recruitee:eworgmbh": 26},
        names={"teamtailor:eworgmbh": "EWOR GmbH", "recruitee:eworgmbh": "EWOR GmbH"},
    )
    assert [r["board"] for r in lenses["rate"]] == ["teamtailor:eworgmbh"], (
        "best-ranked wins"
    )


def test_a_board_no_one_can_name_is_counted_not_ranked() -> None:
    """ADR-0212: an Oracle pod names nobody, and two such rows would collapse into one."""
    lenses, counts = _rank(
        new={
            "oracle:eeho.fa.us2.oraclecloud.com": 40,
            "oracle:eofd.fa.us6.oraclecloud.com": 30,
        },
        stock={
            "oracle:eeho.fa.us2.oraclecloud.com": 400,
            "oracle:eofd.fa.us6.oraclecloud.com": 300,
        },
        moved={
            "oracle:eeho.fa.us2.oraclecloud.com": 60,
            "oracle:eofd.fa.us6.oraclecloud.com": 50,
        },
        names={},
    )
    assert all(not lens for lens in lenses.values())
    assert counts["unnamed"] == 2


def test_rows_carry_their_operator_label() -> None:
    lenses, excluded = _rank(
        new={"lever:jobgether": 1634, "greenhouse:acme": 40},
        stock={"lever:jobgether": 1773, "greenhouse:acme": 400},
        moved={"lever:jobgether": 503, "greenhouse:acme": 60},
        names={"lever:jobgether": "Jobgether"},
    )
    labels = {r["board"]: r["operator"] for r in lenses["expansion"]}
    assert labels == {"lever:jobgether": "aggregator", "greenhouse:acme": "employer"}
    assert excluded["aggregator"] == 1


def test_a_counting_change_and_the_run_after_it_are_not_hiring(tmp_path: Path) -> None:
    """Amazon's Sep 17 filter change: +308 at its tick, −439 at the next — neither is hiring."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    ticks = [
        ("2026-09-17T12:00:00+00:00", 500),  # baseline
        ("2026-09-17T13:00:00+00:00", 6),
        ("2026-09-17T15:26:29+00:00", 308),  # the change's tick
        ("2026-09-17T16:09:00+00:00", -439),  # it settling
        ("2026-09-17T17:00:00+00:00", 9),
    ]
    for ts, delta in ticks:
        pq.write_table(
            _deltas(ts, [("amazon:jobs", "stock", "se", delta)]),
            deltas / f"{ts.replace(':', '-')}.parquet",
        )
    epochs = tmp_path / "trends_epochs.csv"
    epochs.write_text(
        "ts,centroid_version,family_map_fingerprint,tech_filter_version,"
        "derivations_version,dedup_version\n"
        "2026-09-16T20:37:44+00:00,2,f,1,13,1\n"
        "2026-09-17T15:26:29+00:00,2,f,2,13,1\n"
        "2026-09-22T19:19:38+00:00,2,f,2,14,1\n",  # extraction: moves no count
        encoding="utf-8",
    )
    changes = hot_boards.counting_changes(epochs)
    assert changes == {"2026-09-17T15:26:29+00:00"}
    moved, _ = hot_boards.read_stock_change(deltas, changes)
    assert moved["amazon:jobs"] == 15
    assert hot_boards.counting_changes(tmp_path / "missing.csv") == set()


def test_a_family_assignment_change_is_a_counting_change(tmp_path: Path) -> None:
    """ADR-0215/ADR-0220: a new classifier head moves Jobs between families the way a family-list
    edit does. The upgraded file gives rows from before the title decided anything ``none``."""
    epochs = tmp_path / "trends_epochs.csv"
    epochs.write_text(
        "ts,centroid_version,family_map_fingerprint,tech_filter_version,"
        "derivations_version,dedup_version,family_classifier_version\n"
        "2026-09-24T16:23:40+00:00,2,f,5,15,3,none\n"
        "2026-09-25T01:00:00+00:00,none,f,5,15,3,2\n",
        encoding="utf-8",
    )
    assert hot_boards.counting_changes(epochs) == {"2026-09-25T01:00:00+00:00"}


def test_a_change_with_no_tick_of_its_own_lands_on_the_next(tmp_path: Path) -> None:
    """A skipped delta write: the change lands on the next tick and settles on the one after."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    for ts, delta in [
        ("2026-09-17T12:00:00+00:00", 500),  # baseline
        ("2026-09-17T13:00:00+00:00", 6),
        ("2026-09-17T16:00:00+00:00", 300),  # lands here
        ("2026-09-17T17:00:00+00:00", -400),  # settles here
        ("2026-09-17T18:00:00+00:00", 9),
    ]:
        pq.write_table(
            _deltas(ts, [("amazon:jobs", "stock", "se", delta)]),
            deltas / f"{ts.replace(':', '-')}.parquet",
        )
    moved, _ = hot_boards.read_stock_change(deltas, {"2026-09-17T15:26:29+00:00"})
    assert moved["amazon:jobs"] == 15


def test_non_tech_rows_are_not_hot_hiring(tmp_path: Path) -> None:
    """Hot counts tech roles, as the trend each row links to does."""
    path = tmp_path / "counts.parquet"
    pq.write_table(
        _counts(
            [
                ("amazon:jobs", "stock", "software-engineering", 90),
                ("amazon:jobs", "stock", "non-tech", 9),
                ("amazon:jobs", "new", "non-tech", 4),
            ]
        ),
        path,
    )
    new, stock = hot_boards.read_levels(path)
    assert stock["amazon:jobs"] == 90
    assert new["amazon:jobs"] == 0
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    for ts, rows in [
        ("2026-09-17T12:00:00+00:00", [("amazon:jobs", "stock", "se", 500)]),
        (
            "2026-09-18T12:00:00+00:00",
            [
                ("amazon:jobs", "stock", "se", 5),
                ("amazon:jobs", "stock", "non-tech", 40),
            ],
        ),
    ]:
        pq.write_table(_deltas(ts, rows), deltas / f"{ts.replace(':', '-')}.parquet")
    moved, _ = hot_boards.read_stock_change(deltas)
    assert moved["amazon:jobs"] == 5


def test_a_refit_leaves_out_its_own_run_and_the_next_only(tmp_path: Path) -> None:
    """The refit's change is its baseline tick; located after dropping that tick, it took out
    two ordinary runs instead of one."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    ticks = [
        ("2026-09-24T12:00:00+00:00", 2, 500),  # v2 baseline
        ("2026-09-24T13:00:00+00:00", 2, 4),
        (
            "2026-09-24T21:19:12+00:00",
            2001,
            480,
        ),  # the refit: v2001 baseline, a counting change
        ("2026-09-24T22:00:00+00:00", 2001, 7),  # settling: left out
        ("2026-09-24T23:00:00+00:00", 2001, 9),  # ordinary: kept
    ]
    for ts, version, delta in ticks:
        table = _deltas(ts, [("amazon:jobs", "stock", "se", delta)])
        table = table.replace_schema_metadata({"centroid_version": str(version)})
        pq.write_table(table, deltas / f"{ts.replace(':', '-')}.parquet")
    moved, _ = hot_boards.read_stock_change(deltas, {"2026-09-24T21:19:12+00:00"})
    assert moved["amazon:jobs"] == 13


def test_the_window_base_is_the_run_before_its_first_change(tmp_path: Path) -> None:
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    for ts in (
        "2026-09-24T10:00:00+00:00",
        "2026-09-24T11:00:00+00:00",
        "2026-09-24T12:00:00+00:00",
    ):
        pq.write_table(
            _deltas(ts, [("a:b", "stock", "se", 1)]),
            deltas / f"{ts.replace(':', '-')}.parquet",
        )
    assert (
        hot_boards.window_base(deltas, "2026-09-24T12:00:00+00:00")
        == "2026-09-24T11:00:00+00:00"
    )
    assert hot_boards.window_base(deltas, "2026-09-24T10:00:00+00:00") is None


def test_duplicate_removal_leaves_out_only_the_boards_it_can_touch(
    tmp_path: Path,
) -> None:
    """Hot read Google −27 where its trend, which keeps a duplicate-removal run at a Board that
    change cannot touch, read −42 (2026-09-25). Two Workday sites of one Tenant can lose rows to
    each other there; Google's one Board cannot."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    boards = ["google:careers", "workday:acme/a", "workday:acme/b", "workday:solo/x"]
    ticks = [
        ("2026-09-20T00:00:00+00:00", 0),  # the baseline
        ("2026-09-21T00:00:00+00:00", 5),
        ("2026-09-22T00:00:00+00:00", -40),  # duplicate removal changed here
        ("2026-09-22T06:00:00+00:00", 7),  # its run after
        ("2026-09-23T00:00:00+00:00", 3),
    ]
    for ts, delta in ticks:
        pq.write_table(
            _deltas(ts, [(b, "stock", "se", delta) for b in boards]),
            deltas / f"{ts.replace(':', '-')}.parquet",
        )
    epochs = tmp_path / "trends_epochs.csv"
    epochs.write_text(
        "ts,centroid_version,family_map_fingerprint,tech_filter_version,"
        "derivations_version,dedup_version\n"
        "2026-09-20T00:00:00+00:00,2,f,5,15,3\n"
        "2026-09-22T00:00:00+00:00,2,f,5,15,4\n",
        encoding="utf-8",
    )
    assert hot_boards.counting_changes(epochs) == set()
    assert hot_boards.dedup_changes(epochs) == {"2026-09-22T00:00:00+00:00"}
    touched = hot_boards.dedup_touches(boards + ["eightfold:micron"])
    assert touched == {"workday:acme/a", "workday:acme/b", "eightfold:micron"}
    moved, _ = hot_boards.read_stock_change(
        deltas,
        hot_boards.counting_changes(epochs),
        hot_boards.dedup_changes(epochs),
        touched,
    )
    assert moved["google:careers"] == 5 - 40 + 7 + 3, "an ordinary run it keeps"
    assert moved["workday:solo/x"] == -25, (
        "one Workday site has no sibling to lose rows to"
    )
    assert moved["workday:acme/a"] == 5 + 3, "the removal and its run after, left out"


def test_an_emptied_site_still_makes_its_sibling_touched(tmp_path: Path) -> None:
    """#603 can empty one of a Tenant's two Workday sites: it holds no stock now, but the ledger
    has read it, and duplicate removal can still move the site beside it."""
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    pq.write_table(
        _deltas(
            "2026-09-21T00:00:00+00:00",
            [
                ("workday:acme/a", "stock", "se", 5),
                ("workday:acme/b", "stock", "se", -9),
            ],
        ),
        deltas / "2026-09-21T00-00-00+00-00.parquet",
    )
    now = {"workday:acme/a"}  # b emptied, so it is gone from the current counts
    assert hot_boards.dedup_touches(now) == set()
    assert hot_boards.dedup_touches(now | hot_boards.ledger_boards(deltas)) == {
        "workday:acme/a",
        "workday:acme/b",
    }
