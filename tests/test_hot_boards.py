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

    Summed across versions, that baseline reads as every Board having just been discovered.
    """
    deltas = tmp_path / "deltas"
    deltas.mkdir()
    ticks = [
        ("2026-09-20T12:00:00+00:00", 2, 500),  # v2 baseline
        ("2026-09-21T12:00:00+00:00", 2, 5),  # v2 change: an older version, not summed
        ("2026-09-22T12:00:00+00:00", 3, 505),  # the refit's v3 baseline
        ("2026-09-23T12:00:00+00:00", 3, 3),
    ]
    for ts, version, delta in ticks:
        table = _deltas(ts, [("greenhouse:acme", "stock", "se", delta)])
        table = table.replace_schema_metadata({"centroid_version": str(version)})
        pq.write_table(table, deltas / f"{ts.replace(':', '-')}.parquet")
    moved, stamps = hot_boards.read_stock_change(deltas)
    assert moved["greenhouse:acme"] == 3
    assert stamps == ["2026-09-23T12:00:00+00:00"]


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


@pytest.mark.parametrize(
    ("company", "board", "expected"),
    [
        ("", "amazon:www.amazon.jobs", "Amazon"),
        # The company is the *second* label here, because every part of the first is noise
        # (the real case is `careers-inc.nttdata.com`).
        ("", "successfactors:careers-inc.examplecorp.com", "Examplecorp"),
        # ...but the first label elsewhere, which is why the registrable domain is not used.
        ("", "successfactors:acmeco.jobs.hr.cloud.sap", "Acmeco"),
        ("", "workday:micron/External", "Micron"),
        ("", "icims:jobs-bylight.icims.com", "Bylight"),
        # Taleo Enterprise's slug is a whole URL; it used to tidy to "Https:".
        ("", "taleo_enterprise:https://hdr.taleo.net/careersection/ex", "Hdr"),
        # SuccessFactors rows carry the host's first label as the company; it names the host.
        ("www", "successfactors:www.afuturewithus.com", "Afuturewithus"),
        ("apply", "successfactors:apply.careers.hsbc.com", "Hsbc"),
        ("join", "successfactors:join.cnh.com", "Cnh"),
        ("opportunities", "successfactors:opportunities.vodafone.com", "Vodafone"),
        # A Workday site name in the company column is cased, but it is not the company.
        ("EXTERNAL_CAREERS", "workday:boeing/EXTERNAL_CAREERS", "Boeing"),
        ("CorporateCareers", "workday:mastercard/CorporateCareers", "Mastercard"),
        # ...but a site worded as a brand is often the best name there is.
        ("JioStar", "workday:jiostar/JioStar", "JioStar"),
        ("g-research", "workday:gresearch/g-research", "G Research"),
        # A cased name equal to the whole slug is still the Board's own spelling.
        ("AbhiBus", "smartrecruiters:AbhiBus", "AbhiBus"),
        # A stated, cased name is never re-cased or trimmed.
        ("CI&T", "lever:ciandt", "CI&T"),
        ("Qantas Group", "smartrecruiters:QantasGroup", "Qantas Group"),
    ],
)
def test_display_name(company: str, board: str, expected: str) -> None:
    assert hot_boards.display_name(company, board) == expected


def test_derivation_never_invents_an_expansion() -> None:
    """The *derivation* only tidies. An unaliased `swa` stays "Swa", never "Southwest Airlines".

    Expanding an abbreviation is human input, and it arrives as an explicit DISPLAY_ALIASES
    entry (below) rather than as a guess the code makes from three letters.
    """
    assert (
        hot_boards.display_name("", "workday:unknownabbrev/external") == "Unknownabbrev"
    )


def test_a_curated_alias_beats_both_the_slug_and_a_stated_name() -> None:
    """The alias map is the one place a name is asserted rather than derived."""
    assert hot_boards.display_name("", "workday:swa/external") == "Southwest Airlines"
    assert (
        hot_boards.display_name("Lockheed", "successfactors:lockheed.jobs.hr.cloud.sap")
        == "Lockheed Martin"
    )


def test_aliases_unify_one_company_across_two_atses() -> None:
    """Lockheed Martin reached Expansion on Eightfold *and* SuccessFactors, ranking 1 and 2."""
    both = {
        "eightfold:lockheedmartin.eightfold.ai",
        "successfactors:lockheed.jobs.hr.cloud.sap",
    }
    assert {hot_boards.display_name("", b) for b in both} == {"Lockheed Martin"}
