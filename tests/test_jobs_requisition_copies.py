"""Which served Jobs copy one requisition — `headstart.jobs.requisition_copies` (ADR-0274,
ADR-0323, ADR-0331).

Contracts: one requisition per country on one Board is grouped by company and title stem; one on
two Boards of its employer needs the same stem and first place under two spellings of the company;
nothing else is grouped, and a group is led by its first Job.
"""

from __future__ import annotations

from headstart.jobs import requisition_copies


def _job(title, company, location="Berlin, Germany"):
    return {"title": title, "company": company, "location": location}


def test_a_title_stem_drops_brackets_and_case():
    assert requisition_copies.title_stem("Backend Developer (Peru) [Remote]") == (
        "backend developer"
    )


def test_one_requisition_per_country_is_one_group_led_by_its_first_job():
    jobs = [
        _job("Backend Developer (Peru)", "Anyone AI", "Lima"),
        _job("Frontend Developer", "Anyone AI"),
        _job("Backend Developer (Chile)", "Anyone AI", "Santiago"),
    ]
    assert requisition_copies.groups(jobs) == [[0, 2], [1]]


def test_one_requisition_on_two_boards_needs_its_title_and_first_place():
    same = [
        _job("Data Engineer", "EVERSOURCE", "Berlin, CT"),
        _job("Data Engineer", "Eversource Energy", "Berlin, CT; Westwood, MA"),
    ]
    assert requisition_copies.groups(same) == [[0, 1]]
    elsewhere = [
        _job("Data Engineer", "EVERSOURCE", "Berlin, CT"),
        _job("Data Engineer", "Eversource Energy", "Hartford, CT"),
    ]
    assert requisition_copies.groups(elsewhere) == [[0], [1]]


def test_jobs_naming_no_company_are_never_one_requisition():
    jobs = [_job("Data Engineer", ""), _job("Data Engineer", None)]
    assert requisition_copies.groups(jobs) == [[0], [1]]
