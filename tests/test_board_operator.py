"""The operator label, and the five real companies a looser rule mislabelled.

Every "stays an employer" case below is a Board that a rejected matching rule actually demoted
when run over the served population on 2026-09-21 — not a hypothetical. They are the regression
surface for anyone tempted to widen the matching back to substrings or prefixes.
"""

from __future__ import annotations

import pytest

from headstart.ingest.board_operator import (
    AGGREGATORS,
    SERVICES,
    STAFFING,
    classify,
    tenant,
)


@pytest.mark.parametrize(
    ("board", "company"),
    [
        # Prefix matching read these as Ibex (BPO), Toptal and Zensar. They are three other
        # companies: a medical-imaging firm, a recruiter of a different name, and a different
        # IT firm entirely.
        ("recruitee:ib1", "Ibex Medical Analytics"),
        ("smartrecruiters:TopTalents1", "TopTalents"),
        ("smartrecruiters:ZensarkTecnologiesPvtLtd", "Zensark Tecnologies Pvt Ltd"),
        # Hyatt's Taleo career section is *named* infosys_intl. Reading the whole board_key
        # instead of its tenant made Hyatt an Infosys board.
        (
            "taleo_enterprise:https://hyatt.taleo.net/careersection/infosys_intl",
            "Hyatt",
        ),
        # A law firm, not the BPO — `eversheds-sutherland` splits to a segment that matches.
        ("successfactors:esi-vacancies.eversheds-sutherland.com", None),
        # Substring matching flagged every "…Manufacturing" board, because it contains "turing".
        ("greenhouse:acme-manufacturing", "Acme Manufacturing"),
        # The name-vocabulary rule measured at 52% precision demoted all three of these.
        ("ashby:cerebras", "Cerebras Systems"),
        ("ashby:skylo", "Skylo Technologies"),
        ("workday:juliusbaer/Technology", None),
    ],
)
def test_real_employers_are_not_demoted(board: str, company: str | None) -> None:
    assert classify(board, company) == "employer"


@pytest.mark.parametrize(
    ("board", "company", "expected"),
    [
        ("successfactors:careers.hcltech.com", "hcltech", "services"),
        ("successfactors:careers.wipro.com", None, "services"),
        ("zoho:agileengine.zohorecruit.com", "AgileEngine", "services"),
        (
            "smartrecruiters:AvanceConsultingServices2",
            "Avance Consulting Services",
            "staffing",
        ),
        # The tenant carries it even though the site names a different brand.
        ("workday:accenture/AvanadeCareers", None, "services"),
        # SmartRecruiters appends a disambiguating digit to a slug it has seen before.
        ("smartrecruiters:Collabera6", "Collabera", "staffing"),
        ("lever:jobgether", "Jobgether", "aggregator"),
        ("smartrecruiters:JobsForLebanon", "Jobs for Lebanon", "aggregator"),
        ("smartrecruiters:jobsforhumanity", "Jobs for Humanity", "aggregator"),
    ],
)
def test_known_operators_are_labelled(
    board: str, company: str | None, expected: str
) -> None:
    assert classify(board, company) == expected


def test_an_exception_beats_a_real_entry_they_collide_with() -> None:
    """Both sides are real: `greenhouse:turing` is the marketplace, ATI is a research institute.

    The collision is at whole-segment granularity, so no matching rule separates them — only
    knowing the two organisations does, which is what EXCEPTIONS is for.
    """
    assert classify("greenhouse:turing", "Turing") == "staffing"
    assert classify("greenhouse:acme", "Alan Turing Institute") == "employer"
    assert (
        classify("greenhouse:alan-turing-institute", "The Alan Turing Institute")
        == "employer"
    )


def test_unknown_board_defaults_to_employer() -> None:
    """The default is recall-biased on purpose — see the module docstring's measurement."""
    assert (
        classify("greenhouse:some-company-nobody-curated", "Some Company") == "employer"
    )


def test_company_name_alone_is_enough() -> None:
    """A slug that names nothing still resolves when the Board states its company."""
    assert classify("workable:opaque-slug-1234", "Randstad") == "staffing"


def test_entries_are_normalized_spellings() -> None:
    """Entries must be lowercase alphanumeric runs, or `_forms` can never match them.

    An entry written as "Tech Mahindra" or "ntt-data" is dead code that looks alive — it sits in
    the list, matches nothing, and the Board it was added for keeps ranking as an employer.
    """
    for token in SERVICES | STAFFING | AGGREGATORS:
        assert token.isalnum() and token.islower(), token


def test_an_entry_is_on_one_list() -> None:
    """An entry on two lists takes the first `classify` tests, and the other is dead code."""
    assert not SERVICES & STAFFING
    assert not (SERVICES | STAFFING) & AGGREGATORS


@pytest.mark.parametrize(
    ("board", "company"),
    [
        ("successfactors:careers.wipro.com", "Wipro"),
        ("successfactors:career.infosys.com", "Infosys"),
        ("workday:tcs/External", "Tata Consultancy Services"),
        ("successfactors:careers.capgemini.com", "Capgemini"),
    ],
)
def test_it_services_employers_are_services_not_staffing(
    board: str, company: str
) -> None:
    """The Hot tab hides staffing firms and job boards only (ADR-0238): an IT services firm
    employs the engineers it posts for, so it stays on the list, labelled."""
    assert classify(board, company) == "services"


@pytest.mark.parametrize(
    ("board", "company"),
    [
        ("smartrecruiters:mindlance2", "Mindlance"),
        ("smartrecruiters:DeegitInc3", "Deegit Inc"),
        ("smartrecruiters:sonomaconsultinginc", "Sonoma Consulting Inc."),
        ("smartrecruiters:USITSolutionsInc", "US IT Solutions Inc"),
        ("smartrecruiters:EProInc", "E*Pro Inc"),
        ("smartrecruiters:Info-Ways", "Info-Ways"),
        (
            "smartrecruiters:nextlevelbusinessservicesinc2",
            "Next Level Business Services, Inc.",
        ),
        ("smartrecruiters:PyramidIT1", "Pyramid IT"),
        ("teamtailor:urbanridgessupplies-1748849434", "Urban Ridge Supplies"),
        ("zoho:3coresystems.zohorecruit.com", "3Core Systems , Inc"),
        ("pyjamahr:7th-sky-technologies-llc", "7Th Sky technologies llc"),
        ("ashby:pragmatike", "Pragmatike"),
    ],
)
def test_hots_staffing_firms_are_staffing(board: str, company: str) -> None:
    """Adjudicated 2026-09-26 from Hot's employer-labelled top 50, each by its live postings
    (Mindlance, Deegit, Sonoma Consulting, US IT Solutions and E*Pro were the critic's)."""
    assert classify(board, company) == "staffing"


@pytest.mark.parametrize(
    ("board", "expected"),
    [
        ("workday:accenture/AvanadeCareers", "accenture"),
        (
            "taleo_enterprise:https://hyatt.taleo.net/careersection/infosys_intl",
            "hyatt.taleo.net",
        ),
        # A Taleo Business Edition host is a pod many companies share; the `org` is the company.
        (
            "taleo_be:https://phg.tbe.taleo.net/phg04/ats/careers/v2/searchResults?org=ALLETE&cws=43",
            "ALLETE",
        ),
        ("greenhouse:acme", "acme"),
    ],
)
def test_tenant_names_whose_board_it_is(board: str, expected: str) -> None:
    assert tenant(board) == expected


def test_hots_placement_agencies_are_staffing_and_their_near_names_are_not():
    """Adjudicated 2026-09-25 from Hot's employer-labelled head, each by its own postings."""
    assert classify("smartrecruiters:usm2", "USM") == "staffing"
    assert (
        classify("smartrecruiters:EndeavorItSolution9", "Endeavor it solution")
        == "staffing"
    )
    assert (
        classify(
            "smartrecruiters:squircleitconsultingservicespvtltd",
            "Squircle IT Consulting Services Pvt. Ltd",
        )
        == "staffing"
    )
    # "usm" alone is a university's slug; only USM's own SmartRecruiters slug is listed.
    assert (
        classify("workday:usm/careers", "University of Southern Mississippi")
        == "employer"
    )
