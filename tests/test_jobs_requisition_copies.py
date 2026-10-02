"""Which served Jobs are one posting on two Boards — `headstart.jobs.requisition_copies` (ADR-0274,
ADR-0323, ADR-0331, ADR-0332, ADR-0338, ADR-0365).

Contracts: the same title, brackets included, on another Board of one employer; its name or
another spelling of it (the same words once legal and generic words drop) with one first place
and the same countries; a name whose words begin the other's only with the same countries and the
same stated pay range; never two rows of one Board, nor rows naming no company; each group led by
its first row, in page order, holding at most one row of each Board.
"""

from __future__ import annotations

import pytest

from headstart.jobs.requisition_copies import posting_groups, requisition_groups

_EVERSOURCE_FRONT = (
    "Berlin, CT, United States of America; Westwood, Massachusetts, United States; "
    "Manchester, New Hampshire, United States"
)
_EVERSOURCE_WORKDAY = (
    "Berlin, CT; Westwood, MA; Manchester, NH; United States of America"
)


def _row(
    n, title="Data Engineer", company="Acme", location="London", board="lever:acme"
):
    return {
        "id": f"{board}:{n}",
        "title": title,
        "company": company,
        "location": location,
    }


_CAPITAL_ONE_WORKDAY = "workday:capitalone/Capital_One"
_CAPITAL_ONE_FRONT = "radancy:www.capitalonecareers.com"


def test_one_posting_on_its_workday_board_and_its_radancy_front_is_one_group():
    rows = [
        _row(
            1,
            "Machine Learning Engineer 5",
            "Capital One",
            "McLean, VA; United States of America",
            _CAPITAL_ONE_WORKDAY,
        ),
        _row(
            2,
            "Machine Learning Engineer 5",
            "Capital One",
            "McLean, Virginia, United States",
            _CAPITAL_ONE_FRONT,
        ),
    ]
    assert posting_groups(rows) == [[0, 1]]


def test_same_titled_requisitions_on_one_board_are_each_a_posting_with_its_own_copy():
    """The round-5 critique's s04: four Workday requisitions titled alike in McLean, each with a
    Radancy twin, were one group of nine; they are pairs, one row of each Board apiece."""
    workday = "McLean, VA; United States of America"
    front = "McLean, Virginia, United States"
    title = "Machine Learning Engineer 5"
    rows = [
        _row("R1001855", title, "Capital One", workday, _CAPITAL_ONE_WORKDAY),
        _row(101274361760, title, "Capital One", front, _CAPITAL_ONE_FRONT),
        _row("R1001268", title, "Capital One", workday, _CAPITAL_ONE_WORKDAY),
        _row(100950230848, title, "Capital One", front, _CAPITAL_ONE_FRONT),
    ]
    assert posting_groups(rows) == [[0, 1], [2, 3]]


@pytest.mark.parametrize(
    ("title", "other_title", "place", "other_place"),
    [
        # s04's "also #5": a bracket names another requisition.
        (
            "Machine Learning Engineer 5",
            "Machine Learning Engineer 5 (Senior Manager, IC)",
            "McLean, VA",
            "McLean, Virginia, United States",
        ),
        # The same title in another city.
        (
            "Machine Learning Engineer 5",
            "Machine Learning Engineer 5",
            "McLean, VA",
            "Chicago, Illinois, United States",
        ),
        # One requisition per country is a requisition each (ADR-0274's copies, ADR-0365).
        ("Backend Developer (Peru)", "Backend Developer (Chile)", "Lima", "Santiago"),
    ],
)
def test_another_title_or_city_is_another_posting(
    title, other_title, place, other_place
):
    rows = [
        _row(1, title, "Capital One", place, _CAPITAL_ONE_WORKDAY),
        _row(2, other_title, "Capital One", other_place, _CAPITAL_ONE_FRONT),
    ]
    assert posting_groups(rows) == [[0], [1]]


@pytest.mark.parametrize(
    ("place", "other_place"),
    [
        ("India - Hyderabad", "Hyderabad, India"),
        ("Pune Maharashtra India", "Pune, Maharashtra, India"),
        ("Taguig Philippines", "Taguig, Philippines"),
    ],
)
def test_one_first_place_written_with_more_words_is_one_place(place, other_place):
    """Workday and Radancy spell one city with and without its region or country."""
    rows = [
        _row(1, "Data Engineer", "Amgen", place, "workday:amgen/Careers"),
        _row(2, "Data Engineer", "Amgen", other_place, "radancy:careers.amgen.com"),
    ]
    assert posting_groups(rows) == [[0, 1]]


@pytest.mark.parametrize(
    ("place", "other_place"),
    [
        # The round-5 review's SP6: one first place's word inside another's name.
        ("York, PA", "New York, NY"),
        ("Naples, FL, US", "East Naples, FL, US"),
        ("Amityville, NY, US", "North Amityville, NY, US"),
    ],
)
def test_a_place_inside_another_places_name_is_another_place(place, other_place):
    """A word that begins a place's name makes another place (ADR-0370)."""
    rows = [
        _row(1, "Data Engineer", "Amgen", place, "workday:amgen/Careers"),
        _row(2, "Data Engineer", "Amgen", other_place, "radancy:careers.amgen.com"),
    ]
    assert posting_groups(rows) == [[0], [1]]


def test_one_requisition_posted_per_country_is_one_requisition_but_not_one_posting():
    """A requirements sample counts a requisition once however many countries it was posted in
    (ADR-0332, kept by ADR-0370); a search page lists each country's posting (ADR-0365)."""
    rows = [
        _row(1, "Backend Developer (Peru)", "Anyone AI", "Lima", "lever:anyone"),
        _row(2, "Backend Developer (Chile)", "Anyone AI", "Santiago", "lever:anyone"),
        _row(3, "Backend Developer", "ANYONE AI", "Bogotá", "lever:anyone"),
        _row(4, "Frontend Developer (Peru)", "Anyone AI", "Lima", "lever:anyone"),
    ]
    assert posting_groups(rows) == [[0], [1], [2], [3]]
    assert requisition_groups(rows) == [[0, 1, 2], [3]]


def test_one_requisition_needs_one_company_and_unnamed_rows_one_board():
    rows = [
        _row(1, "Developer", "Acme", "Lima", "lever:acme"),
        _row(2, "Developer", "Acme Robotics", "Lima", "lever:acme-robotics"),
        _row(3, "Developer", "", "Lima", "oracle:a.fa.us2.oraclecloud.com"),
        _row(4, "Developer (Chile)", "", "Santiago", "oracle:a.fa.us2.oraclecloud.com"),
        _row(5, "Developer", "", "Lima", "oracle:b.fa.us2.oraclecloud.com"),
    ]
    assert requisition_groups(rows) == [[0], [1], [2, 3], [4]]


def test_one_posting_on_two_boards_is_one_requisition():
    rows = [
        _row(1, "Data Engineer", "EVERSOURCE", "Berlin, CT, US", "radancy:x"),
        _row(2, "Data Engineer", "Eversource Energy", "Berlin, CT", "workday:e/s"),
    ]
    assert requisition_groups(rows) == [[0, 1]]


def test_rows_on_one_board_are_never_copies():
    rows = [_row(1), _row(2), _row(3, "Data Engineer", "ACME", "london")]
    assert posting_groups(rows) == [[0], [1], [2]]


def test_one_posting_on_two_boards_under_two_spellings_is_one_group():
    """The round-2 critique's Eversource page: its Radancy front and its Workday Board."""
    rows = [
        _row(
            1,
            "IT Associate Software Engineer (Hybrid)",
            "EVERSOURCE",
            _EVERSOURCE_FRONT,
            "radancy:jobs.eversource.com",
        ),
        _row(
            2,
            "IT Associate Software Engineer (Hybrid)",
            "Eversource Energy",
            _EVERSOURCE_WORKDAY,
            "workday:eversource/externalsite",
        ),
    ]
    assert posting_groups(rows) == [[0, 1]]


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("L3Harris", "L3Harris Technologies"),
        ("Staples Inc.", "Staples, Inc."),
        ("The Toro Company", "Toro"),
        ("MasTec", "The MasTec Companies"),
    ],
)
def test_spellings_that_differ_by_legal_and_generic_words_are_one_company(one, other):
    rows = [
        _row(1, company=one, location="McLean, VA"),
        _row(2, company=other, location="McLean, Virginia", board="workday:x/y"),
    ]
    assert posting_groups(rows) == [[0, 1]]


@pytest.mark.parametrize(
    ("one", "other", "place", "elsewhere"),
    [
        # The review's two: a name one word longer is another company.
        ("GE", "GE HealthCare", "Berlin, CT, US", "Berlin, CT, US"),
        ("Meta", "Meta Financial Group", "New York, NY", "New York, NY"),
        ("Meta", "Metaview", "London", "London"),
        ("Booz Allen", "Booz Allen Hamilton", "McLean, VA", "McLean, VA"),
        # One spelling-apart company, but a Berlin in another country.
        ("EVERSOURCE", "Eversource Energy", "Berlin, CT, US", "Berlin, Germany"),
        # Or another city.
        ("EVERSOURCE", "Eversource Energy", "Berlin, CT, US", "Hartford, CT, US"),
    ],
)
def test_other_companies_or_places_are_not_copies(one, other, place, elsewhere):
    rows = [
        _row(1, company=one, location=place),
        _row(2, company=other, location=elsewhere, board="workday:x/y"),
    ]
    assert posting_groups(rows) == [[0], [1]]


_TSMC_PAY = {
    "min_salary_annual": 90000.0,
    "max_salary_annual": 142200.0,
    "salary_currency": "USD",
}


def test_one_posting_under_a_short_and_a_long_name_is_one_group():
    """The round-3 critique's `ng02` rows 6 and 7: TSMC on SuccessFactors and on Avature."""
    title = "Software Engineer (New Graduate) - North America Software Center"
    long_name = "TSMC - Taiwan Semiconductor Manufacturing Company Limited"
    rows = [
        {
            **_row(1, title, "TSMC", "Vancouver, WA, US", "successfactors:ro.x"),
            **_TSMC_PAY,
        },
        {**_row(2, title, long_name, "USA-Washington", "avature:tsmc"), **_TSMC_PAY},
    ]
    assert posting_groups(rows) == [[0, 1]]


@pytest.mark.parametrize(
    ("one", "other"),
    [
        # No stated pay on either: a longer name is as often another company.
        ({}, {}),
        # Another range.
        (_TSMC_PAY, {**_TSMC_PAY, "max_salary_annual": 150000.0}),
        # The same figures in another currency.
        (_TSMC_PAY, {**_TSMC_PAY, "salary_currency": "CAD"}),
    ],
)
def test_a_short_and_a_long_name_need_one_stated_pay_range(one, other):
    rows = [
        {**_row(1, company="GE", location="Boston, MA, US"), **one},
        {
            **_row(2, company="GE HealthCare", location="Boston, MA", board="wd:x/y"),
            **other,
        },
    ]
    assert posting_groups(rows) == [[0], [1]]


def test_a_short_and_a_long_name_need_the_same_countries():
    rows = [
        {**_row(1, company="TSMC", location="Vancouver, WA, US"), **_TSMC_PAY},
        {
            **_row(2, company="TSMC Arizona", location="Hsinchu", board="wd:x/y"),
            **_TSMC_PAY,
        },
    ]
    assert posting_groups(rows) == [[0], [1]]


def test_rows_naming_no_company_are_never_copies():
    """Two unnamed Boards are not one company, and two rows of one Board are two postings."""
    rows = [
        _row(1, "Developer", "", "Lima", "oracle:a.fa.us2.oraclecloud.com"),
        _row(2, "Developer", None, "Lima", "oracle:a.fa.us2.oraclecloud.com"),
        _row(3, "Developer", "", "Lima", "oracle:b.fa.us2.oraclecloud.com"),
    ]
    assert posting_groups(rows) == [[0], [1], [2]]


def test_a_row_with_no_title_groups_with_nothing():
    rows = [_row(1, ""), _row(2, "", board="workday:x/y")]
    assert posting_groups(rows) == [[0], [1]]
