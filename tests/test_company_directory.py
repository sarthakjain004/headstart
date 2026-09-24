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
    for company in company_directory.group(set(boards), names or {}):
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
    assert len(company_directory.group(set(boards), {})) == 2


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
    assert len(company_directory.group(set(boards), names)) == len(boards)


def test_a_company_is_named_by_its_stated_spelling() -> None:
    got = _companies(
        [
            "workday:nvidia/NVIDIAExternalCareerSite",
            "workday:nvidia/nvidiaexternalcareersite",
        ],
        {"workday:nvidia/nvidiaexternalcareersite": "NVIDIA"},
    )
    assert list(got) == ["NVIDIA"]


def _snapshot(path: Path, rows: list[tuple[str, str, str, int]]) -> None:
    pq.write_table(
        pa.table(
            {
                "board": [r[0] for r in rows],
                "metric": [r[1] for r in rows],
                "family": [r[2] for r in rows],
                "band": ["all"] * len(rows),
                "ats": [r[0].split(":", 1)[0] for r in rows],
                "count": [r[3] for r in rows],
            }
        ),
        path,
    )


def test_only_boards_with_tech_stock_are_listed(tmp_path: Path) -> None:
    """A non-tech-only Board has no series to chart; `watch:` and `new` rows are not stock."""
    path = tmp_path / "counts.parquet"
    _snapshot(
        path,
        [
            ("greenhouse:acme", "stock", "software-engineering", 3),
            ("greenhouse:bakery", "stock", "non-tech", 9),
            ("greenhouse:watched", "stock", "watch:rust", 2),
            ("greenhouse:fresh", "new", "software-engineering", 1),
        ],
    )
    assert company_directory.tech_boards(path) == {"greenhouse:acme"}


def test_an_unchanged_set_of_boards_writes_identical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No counts and no timestamp inside, so republishing an unchanged file costs nothing."""
    counts = tmp_path / "counts.parquet"
    _snapshot(
        counts,
        [
            ("greenhouse:acme", "stock", "software-engineering", 3),
            ("workday:hpe/ACJobSite", "stock", "data", 5),
        ],
    )
    monkeypatch.setattr(
        company_directory, "board_names", lambda db, table: {"greenhouse:acme": "Acme"}
    )
    out = tmp_path / "company_directory.json"
    argv = ["company_directory", "--board-counts", str(counts), "--out", str(out)]
    monkeypatch.setattr("sys.argv", argv)
    assert company_directory.main() == 0
    first = out.read_bytes()
    _snapshot(
        counts,
        [
            ("greenhouse:acme", "stock", "software-engineering", 40),
            ("workday:hpe/ACJobSite", "stock", "data", 1),
        ],
    )
    assert company_directory.main() == 0
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
    """An all-slug directory would rewrite the whole file and name every company worse."""
    counts = tmp_path / "counts.parquet"
    _snapshot(counts, [("greenhouse:acme", "stock", "software-engineering", 3)])
    out = tmp_path / "company_directory.json"
    out.write_text('{"companies": []}', encoding="utf-8")
    monkeypatch.setattr(company_directory, "board_names", lambda db, table: {})
    argv = ["company_directory", "--board-counts", str(counts), "--out", str(out)]
    monkeypatch.setattr("sys.argv", argv)
    assert company_directory.main() == 0
    assert out.read_text(encoding="utf-8") == '{"companies": []}'
