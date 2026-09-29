"""Which served Jobs copy one requisition — `headstart.jobs.requisition_copies` (ADR-0274,
ADR-0323, ADR-0331, ADR-0332, ADR-0338).

Contracts: the same company and title stem, brackets aside, anywhere; rows naming no company only
on one Board; another spelling of the company only with the same words once legal and generic
words drop, the same first city and the same countries; a name whose words begin the other's
only with the same countries and the same stated pay range; each group led by its first row, in page
order.
"""

from __future__ import annotations

import pytest

from headstart.jobs.requisition_copies import groups, title_stem

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


def test_one_company_and_title_stem_is_one_posting_wherever_it_is_placed():
    rows = [
        _row(1, "Backend Developer (Peru)", "Anyone AI", "Lima"),
        _row(2, "Python Developer", "GoML"),
        _row(3, "Backend Developer (Chile)", "Anyone AI", "Santiago"),
        _row(4, "backend developer", "anyone ai", "Lima"),
    ]
    assert groups(rows) == [[0, 2, 3], [1]]


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
    assert groups(rows) == [[0, 1]]


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
    assert groups(rows) == [[0, 1]]


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
    assert groups(rows) == [[0], [1]]


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
    assert groups(rows) == [[0, 1]]


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
    assert groups(rows) == [[0], [1]]


def test_a_short_and_a_long_name_need_the_same_countries():
    rows = [
        {**_row(1, company="TSMC", location="Vancouver, WA, US"), **_TSMC_PAY},
        {
            **_row(2, company="TSMC Arizona", location="Hsinchu", board="wd:x/y"),
            **_TSMC_PAY,
        },
    ]
    assert groups(rows) == [[0], [1]]


def test_rows_naming_no_company_are_copies_only_on_one_board():
    """Oracle per-country copies share their pod; two unnamed pods are not one company."""
    rows = [
        _row(1, "Developer (Peru)", "", "Lima", "oracle:a.fa.us2.oraclecloud.com"),
        _row(
            2, "Developer (Chile)", None, "Santiago", "oracle:a.fa.us2.oraclecloud.com"
        ),
        _row(3, "Developer", "", "Lima", "oracle:b.fa.us2.oraclecloud.com"),
    ]
    assert groups(rows) == [[0, 1], [2]]


def test_a_title_with_nothing_but_brackets_groups_with_nothing():
    rows = [_row(1, "(Remote)"), _row(2, "(Remote)")]
    assert groups(rows) == [[0], [1]]


def test_a_title_stem_drops_brackets_and_case():
    assert title_stem("Backend Developer (Peru) [Remote]") == "backend developer"
