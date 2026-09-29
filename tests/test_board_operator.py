"""The operator label, and the five real companies a looser rule mislabelled.

Every "stays an employer" case below is a Board that a rejected matching rule actually demoted
when run over the served population on 2026-09-21 — not a hypothetical. They are the regression
surface for anyone tempted to widen the matching back to substrings or prefixes.
"""

from __future__ import annotations

import pytest

from headstart.boards import board_operator
from headstart.boards.board_operator import (
    AGGREGATORS,
    SERVICES,
    STAFFING,
    classify,
    tenant,
    unverified,
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


@pytest.mark.parametrize(
    ("tier", "operator"),
    [(SERVICES, "services"), (STAFFING, "staffing"), (AGGREGATORS, "aggregator")],
    ids=["services", "staffing", "aggregators"],
)
def test_every_entry_of_each_list_labels_its_board(tier, operator) -> None:
    """Each entry, as a Board's own slug, gets its list's label: no entry is shadowed by an
    exception or by an earlier list."""
    for entry in sorted(tier):
        assert classify(f"greenhouse:{entry}", None) == operator, entry


@pytest.mark.parametrize(
    ("board", "company"),
    [
        # Each carries an entry's word and is another company (review of #731): a satellite-
        # optics maker, a robotics startup, a medical-device maker and a trade-union club.
        ("breezy:simera-sense", "Simera Sense"),
        ("workable:turing-machines-inc", "Turing Machines Inc"),
        ("workable:atos-medical-us", "Atos Medical US"),
        ("workable:sutherland-district-trade-union-club", None),
        # Left off the list: single words another company's name can carry.
        ("smartrecruiters:Info-Ways", "Info-Ways"),
        ("smartrecruiters:acme", "Acme Infoways Pvt Ltd"),
        ("smartrecruiters:LinkTag", "LinkTag"),
        ("workable:raydar", "Raydar"),
        ("workable:ag-technologies", "AG Technologies"),
        ("greenhouse:acme", "Maarut Drones"),
    ],
)
def test_another_company_carrying_an_entrys_word_stays_an_employer(
    board: str, company: str | None
) -> None:
    assert classify(board, company) == "employer"


def test_a_narrowed_entry_still_labels_its_own_board() -> None:
    assert classify("zoho:maarutinc.zohorecruit.com", "Maarut") == "staffing"
    assert classify("freshteam:simera-talent", "Simera") == "staffing"


@pytest.mark.parametrize(
    ("boards", "name", "flagged"),
    [
        # ADR-0335: a consultancy its postings did not settle, so on no list.
        (["pyjamahr:zorba-consulting-india"], "Zorba Consulting India", True),
        (["zoho:acme.zohorecruit.com"], "Acme Consultancy Services", True),
        (["jazzhr:acme"], "Acme HR Solutions", True),
        # A Board's tenant counts too, as HIKINEX's `breezy:recruiting` did.
        (["breezy:recruiting"], "Acme", True),
        # Only at the start of a word: none of these names says "consult" or "hr".
        (["greenhouse:cerebras"], "Cerebras Systems", False),
        (["greenhouse:shrine"], "Shrine Technologies", False),
        (["lever:talentsoft"], "Talentsoft", False),
        # Already labelled, or an exception to the lists: not a default, so checked.
        (["lever:bluelightconsulting"], "Bluelight Consulting", False),
        (["greenhouse:accenturefederalservices"], "Accenture Federal Services", False),
    ],
)
def test_an_employer_named_like_an_agency_is_unverified(
    boards: list[str], name: str, flagged: bool
) -> None:
    assert unverified(boards, name) is flagged


@pytest.mark.parametrize(
    ("board", "company", "operator"),
    [
        (
            "zoho:vrinda-international.zohorecruit.in",
            "Vrinda International",
            "staffing",
        ),
        ("zoho:flintex.zohorecruit.com", "Flintex Consulting Pte Ltd", "staffing"),
        ("workable:gramian", "Gramian Consulting Group", "staffing"),
        ("zoho:2coms.zohorecruit.in", "2COMS", "staffing"),
        ("zwayam:2coms.openings.co", "2COMS", "staffing"),
        ("ashby:clera", "Clera", "staffing"),
        ("zoho:astra-north.zohorecruit.ca", "Astra North Infoteck Inc.", "staffing"),
        ("jazzhr:inabia", "Inabia Software & Consulting Inc.", "staffing"),
        ("lever:tsmg", "TSMG", "staffing"),
        ("workable:weekday-1", "Weekday AI", "staffing"),
        ("breezy:recruiting", "HIKINEX", "staffing"),
        ("jazzhr:omegahires", "OmegaHires", "staffing"),
        ("zoho:technopride.zohorecruit.eu", "Technopride Ltd", "staffing"),
        ("wp_job_openings:dawninfotek.com", "Dawn InfoTek Inc.", "staffing"),
        ("wp_job_openings:acmehr.com", "ACME HR Consulting", "staffing"),
        ("wp_job_openings:angelandgenie.com", "Angel and Genie", "staffing"),
        ("smartrecruiters:AngelAndGenie1", "Angel and Genie", "staffing"),
        ("wp_job_openings:findmyjob.lk", "FindMyJob.lk", "aggregator"),
        ("wp_job_openings:board.vals.services", "Chimney Sweep Masters", "aggregator"),
        ("smartrecruiters:fusionconsulting", "Fusion Consulting", "services"),
        (
            "jazzhr:abeamconsultingsingapore",
            "ABeam Consulting (Singapore)",
            "services",
        ),
    ],
)
def test_hiring_nows_head_of_2026_09_29_is_labelled(
    board: str, company: str, operator: str
) -> None:
    """Adjudicated from the four Lenses' top 100s and the critic's DevOps sample, each by five
    live postings (ADR-0335)."""
    assert classify(board, company) == operator


@pytest.mark.parametrize(
    ("board", "company"),
    [
        ("wp_job_openings:digitalxnode.com", "DigitalXNode"),
        ("freshteam:remotestar-team", "RemoteStar"),
        ("teamtailor:sperton", "Sperton Global AS"),
        ("ashby:hirehangar", "Hire Hangar"),
        ("teamtailor:whyhirewrong", "WhyHireWrong?"),
        ("pyjamahr:umanist-staffing-llc", "Umanist Staffing LLC"),
        ("zoho:helixworkforce.zohorecruit.com", "Helix Workforce"),
        ("pyjamahr:viraaj-hr-solutions", "viraaj hr solutions"),
        ("workable:two95-international-inc-3", "Two95 International Inc."),
        ("pyjamahr:knowfinity-academy-llp", "Knowfinity Academy LLP"),
        ("pyjamahr:octorudra-hr-llp", "OctoRudra HR LLP"),
        ("zoho:zerotoonesearch.zohorecruit.eu", "Zero to One search"),
        ("zoho:stafide.zohorecruit.com", "STAFIDE"),
        ("zoho:sabenzait.zohorecruit.com", "Sabenza IT & Recruitment"),
        ("zoho:allaboutexpats.zohorecruit.com", "All About Expats"),
        ("zoho:govserviceshub.zohorecruit.in", "GovServicesHub"),
        ("teamtailor:worksterjobs", "Workster Jobs"),
        ("zoho:cliqhr.zohorecruit.in", "CLIQHR Recruitment Services (GTS Pvt Ltd.)"),
        ("workable:hunt-st", "Hunt St"),
        ("recruitee:decircletalentpartner", "deCircle"),
        ("workable:crossbordertalents", "Cross Border Talents"),
        ("smartrecruiters:CrossBorderTalents1", "Cross Border Talents"),
        ("zoho:redtech-recruit.zohorecruit.eu", "RedTech Recruitment Ltd."),
        ("teamtailor:onhiresnew", "OnHires"),
        ("ashby:onhires", "OnHires"),
        (
            "zoho:biztekpeople.zohorecruit.com",
            "BizTek People, Inc. | APA International Placement Consultants",
        ),
        ("zoho:flexondemand.zohorecruit.com", "Flex On-Demand Consultants"),
        ("pyjamahr:yo-hr-consultancy", "YO HR Consultancy"),
        ("wp_job_openings:pakistanrecruitment.com", "Pakistan Recruitment"),
        ("jazzhr:huntresstalent", "Huntress Talent"),
        ("greenhouse:attaintalent", "Attain Talent"),
        ("pyjamahr:the-corporate", "TheCorporate LLC"),
        ("zoho:nakunj.zohorecruit.com", "Nakunj Inc"),
        ("smartrecruiters:kgstechnologygroupinc", "KGS Technology Group Inc"),
        ("workable:joinremotely", "Remotely"),
        ("pyjamahr:hrbaires", "HRBaires"),
        ("workable:globaldevgroup", "Globaldev Group"),
        ("zoho:talproindia.zohorecruit.in", "Talpro India Private Limited"),
    ],
)
def test_the_mcp_critiques_round_4_staffing_firms_are_labelled(
    board: str, company: str
) -> None:
    """Staffing firms and recruiters that led search rows and requirements samples as
    employers, each adjudicated from its live postings (ADR-0335, 2026-09-29)."""
    assert classify(board, company) == "staffing"


@pytest.mark.parametrize(
    ("board", "company"),
    [
        # The joined forms leave the ordinary word, or the other company, alone.
        ("greenhouse:huntress", "Huntress"),
        ("greenhouse:attain", "Attain"),
        ("workable:remotely", "Remotely Works"),
        ("greenhouse:helix", "Helix"),
    ],
)
def test_round_4_entries_leave_their_parts_to_employers(
    board: str, company: str
) -> None:
    assert classify(board, company) == "employer"


@pytest.mark.parametrize(
    ("board", "company"),
    [
        (
            "icims:careers-odysseyconsult.icims.com",
            "Odyssey Systems Consulting Group, Ltd.",
        ),
        ("greenhouse:blackcanyonconsulting", "Black Canyon Consulting"),
        ("smartrecruiters:msxinternational", "MSX International"),
    ],
)
def test_adjudicated_employers_named_like_agencies_are_verified(
    board: str, company: str
) -> None:
    assert classify(board, company) == "employer"
    assert not unverified([board], company)


def test_verified_employers_are_spelled_as_entries_and_on_no_list() -> None:
    for token in board_operator.VERIFIED_EMPLOYERS:
        assert token.isalnum() and token.islower(), token
    assert not board_operator.VERIFIED_EMPLOYERS & (SERVICES | STAFFING | AGGREGATORS)


def test_a_verified_employer_is_not_unverified(monkeypatch) -> None:
    """An employer adjudicated from its postings is listed, and so no longer flagged."""
    name = "Omniscius Consulting"
    boards = ["jazzhr:omnisciusconsulting"]
    assert unverified(boards, name)
    monkeypatch.setattr(
        board_operator,
        "VERIFIED_EMPLOYERS",
        frozenset({"omnisciusconsulting"}),
    )
    assert not unverified(boards, name)
