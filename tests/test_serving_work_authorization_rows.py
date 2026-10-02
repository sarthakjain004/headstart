"""Tests for headstart.serving.work_authorization_rows: each work-authorisation stance's Jobs,
read once from a real LanceDB table (ADR-0333). A real table, because the read's prefilter runs
in LanceDB's own regex engine, not Python's."""

from __future__ import annotations

import pytest

from headstart.jobs import work_authorization
from headstart.serving.work_authorization_rows import WorkAuthorizationRows

_ROWS = [
    ("lever:a:1", "Visa sponsorship is available for this role."),
    ("lever:a:2", "Must be a U.S. person as defined by ITAR."),
    ("lever:b:3", "Relocation assistance provided. We sponsor H-1B visas."),
    ("lever:b:4", "Build distributed systems in Go."),
    ("lever:o'c:5", "We cannot sponsor visas. Relocation support is available."),
    ("lever:c:6", None),
]


def _table(tmp_path, rows, *, with_description=True):
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")
    fields = [
        ("id", pa.string()),
        ("title", pa.string()),
        ("location", pa.string()),
        ("employment_type", pa.string()),
    ]
    if with_description:
        fields.append(("description", pa.string()))
    data = [
        {
            "id": i,
            "title": "Software Engineer",
            "location": "Austin, TX",
            "employment_type": etype[0] if etype else "Full time",
            **({"description": d} if with_description else {}),
        }
        for i, d, *etype in rows
    ]
    db = lancedb.connect(tmp_path)
    return db.create_table("jobs", data=pa.Table.from_pylist(data, pa.schema(fields)))


def _read(table):
    rows = WorkAuthorizationRows(table)
    rows.start()
    assert rows.wait(30)
    return rows


def _kept(table, clause):
    return sorted(r["id"] for r in table.search().where(clause).to_list())


def test_each_stance_names_its_jobs(tmp_path):
    table = _table(tmp_path, _ROWS)
    rows = _read(table)
    assert _kept(table, rows.clause(work_authorization.OFFERS_SPONSORSHIP)) == [
        "lever:a:1",
        "lever:b:3",
    ]
    assert _kept(table, rows.clause(work_authorization.REFUSES_SPONSORSHIP)) == [
        "lever:a:2",
        "lever:o'c:5",
    ]
    assert _kept(table, rows.clause(work_authorization.OFFERS_RELOCATION)) == [
        "lever:b:3",
        "lever:o'c:5",
    ]


def test_may_offer_keeps_the_hedged_the_out_of_reach_and_the_firm_offers(tmp_path):
    # ADR-0353: a hedged offer, and one scoped to a country the job's place does not name,
    # may offer; the filter keeps the firm offers with them. One scoped to another country only
    # offers nothing here.
    table = _table(
        tmp_path,
        [
            ("lever:a:1", "Visa sponsorship is available for this role."),
            ("lever:a:2", "Sponsorship for this role is not guaranteed."),
            ("lever:a:3", "We can sponsor visas to Germany."),
        ],
    )
    rows = _read(table)
    assert _kept(table, rows.clause(work_authorization.OFFERS_SPONSORSHIP)) == [
        "lever:a:1"
    ]
    assert _kept(table, rows.clause(work_authorization.MAY_OFFER_SPONSORSHIP)) == [
        "lever:a:1",
        "lever:a:2",
    ]


def test_each_kept_job_says_whether_it_offers_or_why_it_only_may(tmp_path):
    """R5-P2-2 (ADR-0367): read with the stances, so a page tags its rows by a lookup."""
    table = _table(
        tmp_path,
        [
            ("lever:a:1", "Visa sponsorship is available for this role."),
            ("lever:a:2", "Sponsorship for this role is not guaranteed."),
            ("lever:a:3", "Visa sponsorship: H-1B transfer sponsorship available."),
        ],
    )
    rows = _read(table)
    assert rows.sponsorship("lever:a:1") == {
        "stance": work_authorization.OFFERS_SPONSORSHIP,
        "because": [],
    }
    assert rows.sponsorship("lever:a:2") == {
        "stance": work_authorization.MAY_OFFER_SPONSORSHIP,
        "because": [work_authorization.HEDGED],
    }
    assert rows.sponsorship("lever:a:3")["because"] == [
        work_authorization.TRANSFER_ONLY
    ]


def test_an_offer_is_read_against_each_jobs_type(tmp_path):
    # ADR-0368: "for full-time positions" offers an internship nothing.
    offer = "We sponsor work visas for full-time positions."
    table = _table(
        tmp_path,
        [("lever:a:1", offer, "Full time"), ("lever:a:2", offer, "Intern")],
    )
    rows = _read(table)
    assert _kept(table, rows.clause(work_authorization.OFFERS_SPONSORSHIP)) == [
        "lever:a:1"
    ]


def test_a_stance_no_job_holds_keeps_nothing(tmp_path):
    table = _table(tmp_path, [("lever:a:1", "Build distributed systems in Go.")])
    rows = _read(table)
    assert _kept(table, rows.clause(work_authorization.OFFERS_SPONSORSHIP)) == []


def test_a_table_without_descriptions_is_read_at_once_and_holds_none(tmp_path):
    table = _table(tmp_path, [("lever:a:1", None)], with_description=False)
    rows = WorkAuthorizationRows(table)
    rows.start()
    assert rows.wait(0)
    assert _kept(table, rows.clause(work_authorization.OFFERS_SPONSORSHIP)) == []


def test_a_read_not_started_is_not_ready(tmp_path):
    rows = WorkAuthorizationRows(_table(tmp_path, _ROWS))
    assert rows.wait(0.01) is False


def test_starting_twice_reads_once(tmp_path, monkeypatch):
    table = _table(tmp_path, _ROWS)
    rows = WorkAuthorizationRows(table)
    reads = []
    monkeypatch.setattr(rows, "_read", lambda: reads.append(1) or rows._ready.set())
    rows.start()
    rows.start()
    assert rows.wait(5) and reads == [1]


def test_a_failed_read_is_ready_and_says_it_failed(tmp_path):
    table = _table(tmp_path, _ROWS)

    class Broken:
        schema = table.schema

        def search(self):
            raise OSError("the table went away")

    rows = WorkAuthorizationRows(Broken())
    rows.start()
    assert rows.wait(5) and rows.failed
