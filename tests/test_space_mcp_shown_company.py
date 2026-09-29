"""The company a job is shown under — `headstart.space_mcp.shown_company` (ADR-0323).

Contracts: a served name that is a name is kept and costs no lookup; an empty one, or one that
only echoes its Board's key or host, is replaced by the Company directory's name for its Board,
marked as the directory's, or said as naming no company when the directory holds the Board and
names none; a Board the Space could not be asked about keeps its served name.
"""

from __future__ import annotations

import pytest

from headstart.space_mcp import shown_company
from headstart.space_mcp import space_client as sc

R = sc.SpaceRoute

_AAH = "workday:aah/External"
_POD = "oracle:egud.fa.us2.oraclecloud.com"


class _Directory:
    """`/companies/lookup` over ``labels`` (a Board key -> its company's label), refusing an
    unknown Board as the Space does; or raising ``failure`` for every lookup."""

    def __init__(self, labels=None, failure=None):
        self.labels = labels or {}
        self.failure = failure
        self.asked = []

    def read(self, route, params=()):
        assert route is R.COMPANIES_LOOKUP
        boards = [value for key, value in params if key == "board"]
        self.asked.append(boards)
        if self.failure:
            raise self.failure
        if unknown := [b for b in boards if b not in self.labels]:
            raise sc.InvalidRequest(f"no directory company holds {', '.join(unknown)}")
        return {
            "companies": [
                {"key": b, "label": self.labels[b], "board_keys": [b], "openings": 1}
                for b in boards
            ]
        }


def _row(board, company, n=1):
    return {"id": f"{board}:{n}", "company": company}


def test_a_served_name_is_kept_and_nothing_is_looked_up():
    directory = _Directory()
    rows = [_row("ashby:checkout.com", "Checkout.com"), _row("lever:x", "Razorpay")]
    assert shown_company.named(directory, rows) is rows
    assert directory.asked == []
    assert shown_company.said(rows[0], 60) == '"Checkout.com"'


@pytest.mark.parametrize(
    "company", ["aah.wd5.myworkdayjobs.com/external", "", None, "  "]
)
def test_a_name_that_is_only_the_board_is_the_directorys_name(company):
    rows = shown_company.named(
        _Directory({_AAH: "Advocate Health"}), [_row(_AAH, company)]
    )
    assert shown_company.said(rows[0], 60) == '"Advocate Health" (directory name)'


def test_a_board_the_directory_lacks_names_no_company_and_the_rest_are_asked_alone():
    directory = _Directory({_AAH: "Advocate Health"})
    rows = shown_company.named(
        directory, [_row(_AAH, ""), _row(_POD, "egud.fa.us2.oraclecloud.com")]
    )
    assert [shown_company.said(row, 60) for row in rows] == [
        '"Advocate Health" (directory name)',
        "no company name",
    ]
    assert directory.asked == [[_AAH, _POD], [_AAH], [_POD]]


@pytest.mark.parametrize("company", ["egud.fa.us2.oraclecloud.com", "", None])
def test_a_directory_that_cannot_answer_leaves_the_served_name(company):
    row = _row(_POD, company)
    rows = shown_company.named(_Directory(failure=sc.SpaceFailed("down")), [row])
    assert rows == [row]
    assert shown_company.said(rows[0], 60) == (
        '"egud.fa.us2.oraclecloud.com"' if company else "no company name"
    )


def test_only_the_first_ten_boards_are_looked_up_and_the_rest_keep_their_names():
    boards = [f"oracle:e{n}.fa.us2.oraclecloud.com" for n in range(12)]
    directory = _Directory({board: f"Company {n}" for n, board in enumerate(boards)})
    rows = shown_company.named(directory, [_row(board, "") for board in boards])
    assert len(directory.asked[0]) == 10
    assert shown_company.said(rows[9], 60) == '"Company 9" (directory name)'
    assert rows[10] == _row(boards[10], "")
