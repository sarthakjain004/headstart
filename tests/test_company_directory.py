"""The company directory: which Boards are one company, and a file that only changes when they do."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headstart.ingest import company_directory

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def _companies(
    boards: list[str], names: dict[str, str] | None = None
) -> dict[str, list]:
    """``{name: [boards, ...] per company}``, so a name two companies share stays visible."""
    out: dict[str, list] = {}
    for company in company_directory.companies(set(boards), names or {}):
        out.setdefault(company["name"], []).append(company["boards"])
    return out


def test_one_tenant_split_into_sites_is_one_company() -> None:
    """Workday sites, Taleo career sections and TBE `cws` sites of one account merge."""
    got = _companies(
        [
            "workday:hpe/ACJobSite",
            "workday:hpe/Jobsathpe",
            "taleo_enterprise:https://hdr.taleo.net/careersection/ex",
            "taleo_enterprise:https://hdr.taleo.net/careersection/aviation",
            "taleo_be:https://phg.tbe.taleo.net/phg04/ats/careers/v2/searchResults?org=ALLETE&cws=43",
            "taleo_be:https://phg.tbe.taleo.net/phg04/ats/careers/v2/searchResults?org=ALLETE&cws=44",
        ],
        {
            "taleo_be:https://phg.tbe.taleo.net/phg04/ats/careers/v2/searchResults?org=ALLETE&cws=43": "ALLETE INC"
        },
    )
    assert len(got["Hpe"]) == 1 and len(got["Hpe"][0]) == 2
    assert len(got["Hdr"]) == 1 and len(got["Hdr"][0]) == 2
    assert len(got["ALLETE INC"]) == 1 and len(got["ALLETE INC"][0]) == 2


def test_a_shared_taleo_be_pod_is_not_a_shared_tenant() -> None:
    """TBE's host is a pod many orgs share; the `org` parameter is the tenant."""
    boards = [
        "taleo_be:https://phf.tbe.taleo.net/phf03/ats/careers/v2/searchResults?org=NBF1199&cws=41",
        "taleo_be:https://phf.tbe.taleo.net/phf04/ats/careers/v2/searchResults?org=B973N8&cws=40",
    ]
    assert len(company_directory.companies(set(boards), {})) == 2


def test_a_casing_duplicate_is_the_same_board() -> None:
    """ADR-0023's stale casing pairs fold into one company rather than two identical names."""
    got = _companies(
        ["smartrecruiters:AbhiBus", "smartrecruiters:abhibus"],
        {"smartrecruiters:AbhiBus": "AbhiBus"},
    )
    assert got == {"AbhiBus": [["smartrecruiters:AbhiBus", "smartrecruiters:abhibus"]]}


def test_a_curated_alias_joins_two_atses() -> None:
    got = _companies(
        [
            "eightfold:lockheedmartin.eightfold.ai",
            "successfactors:lockheed.jobs.hr.cloud.sap",
        ]
    )
    assert got == {
        "Lockheed Martin": [
            [
                "eightfold:lockheedmartin.eightfold.ai",
                "successfactors:lockheed.jobs.hr.cloud.sap",
            ]
        ]
    }


def test_an_alias_and_a_tenant_chain_into_one_company() -> None:
    """RTX's aliased site and its lowercase duplicate share a tenant but not an alias."""
    got = _companies(
        [
            "workday:globalhr/REC_RTX_Ext_Gateway",
            "workday:globalhr/rec_rtx_ext_gateway",
        ]
    )
    assert list(got) == ["RTX"] and len(got["RTX"][0]) == 2


@pytest.mark.parametrize(
    ("boards", "names"),
    [
        # A tidied slug agreeing with a real company is a collision: one Salesforce posting.
        (
            ["amazon:www.amazon.jobs", "trakstar:amazon"],
            {"amazon:www.amazon.jobs": "Amazon"},
        ),
        # A stated name agreeing across ATSes is not identity either: different startups.
        (
            ["ashby:pearl", "greenhouse:pearl"],
            {"ashby:pearl": "Pearl", "greenhouse:pearl": "Pearl"},
        ),
        # ...nor within one: two Ashby tenants whose pages both say "Clarity".
        (
            ["ashby:clarity", "ashby:hiive"],
            {"ashby:clarity": "Clarity", "ashby:hiive": "Clarity"},
        ),
    ],
)
def test_a_matching_name_never_merges(boards: list[str], names: dict[str, str]) -> None:
    assert len(company_directory.companies(set(boards), names)) == len(boards)


def test_a_company_is_named_by_its_stated_spelling() -> None:
    got = _companies(
        [
            "workday:nvidia/NVIDIAExternalCareerSite",
            "workday:nvidia/nvidiaexternalcareersite",
        ],
        {"workday:nvidia/nvidiaexternalcareersite": "NVIDIA"},
    )
    assert list(got) == ["NVIDIA"]


def _ledger(directory: Path, ticks: list[tuple[str, int, list[tuple]]]) -> Path:
    """Write delta ticks ``(stamp, centroid_version, [(board, metric, family, delta)])``."""
    directory.mkdir(exist_ok=True)
    for ts, version, rows in ticks:
        table = pa.table(
            {
                "ts": [ts] * len(rows),
                "board": [r[0] for r in rows],
                "metric": [r[1] for r in rows],
                "family": [r[2] for r in rows],
                "band": ["all"] * len(rows),
                "ats": [r[0].split(":", 1)[0] for r in rows],
                "delta": [r[3] for r in rows],
            }
        ).replace_schema_metadata({"centroid_version": str(version)})
        pq.write_table(table, directory / f"{ts.replace(':', '-')}.parquet")
    return directory


def test_a_board_that_stopped_hiring_keeps_its_place(tmp_path: Path) -> None:
    """Its history is part of its company's line, and a company that stopped can be picked."""
    deltas = _ledger(
        tmp_path / "deltas",
        [
            (
                "2026-09-13T12:00:00+00:00",
                2,
                [("workday:acme/closed", "stock", "data", 4)],
            ),
            (
                "2026-09-14T12:00:00+00:00",
                2,
                [("workday:acme/closed", "stock", "data", -4)],
            ),
        ],
    )
    assert company_directory.ledger_boards(deltas) == {"workday:acme/closed"}


def test_only_tech_stock_at_the_live_version_is_listed(tmp_path: Path) -> None:
    """`non-tech` and `watch:` have no series; a refit re-bases every series (ADR-0040)."""
    deltas = _ledger(
        tmp_path / "deltas",
        [
            ("2026-09-13T12:00:00+00:00", 2, [("greenhouse:old", "stock", "se", 3)]),
            (
                "2026-09-14T12:00:00+00:00",
                3,
                [
                    ("greenhouse:acme", "stock", "software-engineering", 3),
                    ("greenhouse:bakery", "stock", "non-tech", 9),
                    ("greenhouse:watched", "stock", "watch:rust", 2),
                    ("greenhouse:fresh", "new", "software-engineering", 1),
                ],
            ),
        ],
    )
    assert company_directory.ledger_boards(deltas) == {"greenhouse:acme"}


def _run(monkeypatch: pytest.MonkeyPatch, deltas: Path, out: Path, names: dict) -> int:
    monkeypatch.setattr(company_directory, "board_names", lambda db, table: names)
    argv = ["company_directory", "--board-deltas", str(deltas), "--out", str(out)]
    monkeypatch.setattr("sys.argv", argv)
    return company_directory.main()


def test_the_file_names_boards_and_carries_no_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counts come from the delta ledger, and the same Boards always write the same bytes."""
    deltas = _ledger(
        tmp_path / "deltas",
        [
            (
                "2026-09-13T12:00:00+00:00",
                2,
                [
                    ("greenhouse:acme", "stock", "software-engineering", 3),
                    ("workday:hpe/ACJobSite", "stock", "data", 5),
                ],
            )
        ],
    )
    out = tmp_path / "company_directory.json"
    assert _run(monkeypatch, deltas, out, {"greenhouse:acme": "Acme"}) == 0
    first = out.read_bytes()
    assert _run(monkeypatch, deltas, out, {"greenhouse:acme": "Acme"}) == 0
    assert out.read_bytes() == first
    assert json.loads(first) == {
        "companies": [
            {"name": "Acme", "boards": ["greenhouse:acme"]},
            {"name": "Hpe", "boards": ["workday:hpe/ACJobSite"]},
        ]
    }


def test_unreadable_names_keep_the_previous_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An all-slug directory would name every company worse for a run."""
    deltas = _ledger(
        tmp_path / "deltas",
        [("2026-09-13T12:00:00+00:00", 2, [("greenhouse:acme", "stock", "se", 3)])],
    )
    out = tmp_path / "company_directory.json"
    out.write_text('{"companies": []}', encoding="utf-8")
    assert _run(monkeypatch, deltas, out, {}) == 0
    assert out.read_text(encoding="utf-8") == '{"companies": []}'


def test_no_ledger_skips_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """role_trends is `continue-on-error`; a run it skipped leaves no ledger to read."""
    out = tmp_path / "company_directory.json"
    assert _run(monkeypatch, tmp_path / "absent", out, {"x": "X"}) == 0
    assert not out.exists()
