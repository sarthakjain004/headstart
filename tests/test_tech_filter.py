"""Verification gate for the tech filter (ADR-0017).

The recall block is the important one: every title here is a real software/tech role and MUST be
kept — a failure means the filter would drop a tech job, which the spec forbids. The precision block
is a sanity check (some non-tech creep is *allowed*, so it holds only clearly non-tech titles).
"""

from __future__ import annotations

import json

import pytest

from headstart.tech_filter import _STRONG, classify, filter_jobs, is_tech

# Real software/tech roles — recall gate: ALL of these must be kept.
_TECH = [
    "Software Engineer",
    "Senior Software Engineer - Simulation Orchestration",
    "Staff Software Engineer",
    "Backend Engineer",
    "Frontend Developer",
    "Full Stack Developer",
    "AI Engineer",
    "AI/ML Engineer",
    "Machine Learning Engineer",
    "Machine Learning Scientist",
    "Generative AI Engineer",
    "LLM Engineer",
    "Data Scientist",
    "Data Engineer",
    "DevOps Engineer",
    "Site Reliability Engineer",
    "Platform Engineer",
    "Infrastructure Engineer",
    "Cloud Architect",
    "Security Engineer",
    "QA Automation Engineer",
    "Middle General QA Engineer",
    "SDET",
    "Embedded Software Engineer",
    "Firmware Engineer",
    "iOS Developer",
    "Android Engineer",
    "Mobile Developer",
    "Web Developer",
    "Programmer",
    "Computer Vision Engineer",
    "NLP Engineer",
    "Blockchain Developer",
    "Game Developer",
    "Engineering Manager",
    "Director of Engineering",
    "Linux Systems Administrator (Cloud)",
    "Database Administrator",
    "Python Developer",
    "React Engineer",
    "Solutions Architect",
    "Developer Advocate",
    "Software Development Engineer",
    "MLOps Engineer",
]

# Clearly non-tech — precision sanity (some non-tech creep is allowed elsewhere).
_NON_TECH = [
    "Staff Nurse - Oncology Unit",
    "Sales Representative",
    "Special Equipment Mechanical Engineer",
    "Electrical Engineer",
    "Civil Engineer",
    "Chemical Engineer",
    "Sales Engineer",
    "Growth Marketing Specialist",
    "English Language Teacher",
    "AP Accountant",
    "General Laborer",
    "Wedding Coordinator",
    "SEO Content Writer",
    "Registered Nurse",
    "Truck Driver",
    "Key Account Manager UK",
    "Physical Therapist Assistant",
    "HVAC Technician",
]


@pytest.mark.parametrize("title", _TECH)
def test_recall_keeps_every_tech_role(title):
    assert is_tech(title) is True, f"RECALL VIOLATION: tech job dropped -> {title!r}"


@pytest.mark.parametrize("title", _NON_TECH)
def test_precision_drops_clear_non_tech(title):
    assert is_tech(title) is False, f"non-tech kept -> {title!r}"


@pytest.mark.parametrize("title", _TECH)
def test_strong_signal_implies_tech(title):
    # invariant: anything matching a strong signal is always classified tech (never disqualified)
    if _STRONG.search(title):
        assert is_tech(title) is True


def test_reasons_are_meaningful():
    assert classify("Software Engineer").reason == "strong-software-signal"
    assert classify("Mechanical Engineer").reason == "generic-token-but-non-software"
    assert (
        classify("Flight Control Law Engineer").reason == "generic-tech-token"
    )  # recall-kept
    assert classify("Wedding Coordinator").reason == "no-tech-signal"


def test_hardware_department_cannot_veto_a_tech_title():
    """ADR-0068: a hardware *org* employs engineers whose work is code.

    None of these matches a strong signal — which is why they fall to the generic tier, and why
    ADR-0017's self-consistency gate ("no dropped job may match a strong signal") is structurally
    blind to the whole class. Before the fix the department alone decided them.
    """
    assert (
        is_tech("Design Verification Engineer", department="Hardware Engineering")
        is True
    )
    assert is_tech("RTL Design Engineer", department="Hardware") is True
    assert is_tech("Physical Design Engineer", department="Hardware") is True


def test_a_real_discipline_still_vetoes_from_the_department():
    """Only the org-only word is stripped; a discipline beside it still decides."""
    assert is_tech("Engineer", department="Mechanical Engineering") is False
    assert (
        is_tech("Engineer", department="Hardware and Mechanical Engineering") is False
    )


def test_sales_department_still_vetoes():
    """Deliberate: under Sales, "Solutions Engineer" is the pre-sales role `Sales Engineer` names."""
    assert is_tech("Solutions Engineer", department="Sales") is False


def test_non_software_title_is_still_dropped():
    """The disqualifier keeps its job when the qualifier is in the title, where it names the role."""
    assert is_tech("Sales Engineer", department="Sales") is False
    assert is_tech("Mechanical Engineer", department="Engineering") is False
    assert is_tech("Civil Engineer", department="Infrastructure") is False


def test_tech_department_rescues_vague_title():
    assert is_tech("Intern", department="Engineering") is True
    assert is_tech("Intern", department="Marketing") is False


def test_filter_jobs_writes_tech_only_and_leaves_source(tmp_path):
    src = tmp_path / "jobs"
    src.mkdir()
    rows = [
        {"id": "greenhouse:a:1", "title": "Backend Engineer"},
        {"id": "greenhouse:a:2", "title": "Registered Nurse"},
        {"id": "greenhouse:a:3", "title": "Data Scientist"},
    ]
    (src / "greenhouse.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    stats = filter_jobs(src, tmp_path / "tech")
    assert stats["greenhouse"] == (2, 3)  # 2 kept of 3
    out = [
        json.loads(x)
        for x in (tmp_path / "tech" / "greenhouse.jsonl").read_text().splitlines()
    ]
    assert {j["id"] for j in out} == {"greenhouse:a:1", "greenhouse:a:3"}
    # source file untouched
    assert len((src / "greenhouse.jsonl").read_text().splitlines()) == 3


def test_hiring_department_is_not_a_tech_department():
    """A department naming a hiring function says who recruits, not what the role is (ADR-0087).

    `\\bdata\\b` matching Prolific's "Human Data Recruitment" — the team that recruits humans to
    produce training data — promoted 2,427 crowdwork listings into the tech index off one Board.
    """
    dept = "Human Data Recruitment"
    assert is_tech("AI Trainer - Fluent Serbian Speaker", department=dept) is False
    assert is_tech("Cardiologists (Freelance - Remote)", department=dept) is False
    assert is_tech("Fluent Russian Speakers - UK", department=dept) is False
    # the same vague title keeps its promotion under a genuinely technical department
    assert (
        is_tech("AI Trainer - Fluent Serbian Speaker", department="Data & Analytics")
        is True
    )


@pytest.mark.parametrize(
    "department",
    ["Data Recruiting", "Platform Staffing", "Talent Acquisition Technology"],
)
def test_every_hiring_term_actually_reaches_the_veto(department):
    """Each term must be exercised on the rule-4 path, not merely present in the pattern.

    Review caught the first draft's cases resolving at rules 1-3 ("Backend Engineer" is a strong
    signal, "Engineer" a generic one), so they never reached the veto and proved nothing. These
    titles carry no signal of their own, so only the department can decide them.
    """
    assert is_tech("Intern", department=department) is False


def test_sourcing_is_not_treated_as_a_hiring_department():
    """Deliberate exclusion: in Department labels `sourcing` overwhelmingly means procurement.

    All 10 live occurrences in a 418-Board survey were supply-chain, so vetoing on it would fire
    on the wrong meaning of the word.
    """
    assert is_tech("Intern", department="Technology Sourcing") is True


def test_hiring_department_only_withdraws_the_department_booster():
    """Scoped to rule 4, not a disqualifier — a title that names the role still wins.

    This is why 193 Jobs on that same Board keep passing: their titles say so outright.
    """
    assert (
        is_tech("AI Engineer - Senior Developers", department="Human Data Recruitment")
        is True
    )
    assert is_tech("Backend Engineer", department="Technical Recruiting") is True
    assert is_tech("Engineer", department="Talent Acquisition") is True  # rule 3


@pytest.mark.parametrize(
    "department",
    [
        "Data",
        "Data & Analytics",
        "Information Technology",
        "R&D",
        "Technology",
        "Security",
        "Platform Products",
        "Cloud Infrastructure & Operations",
    ],
)
def test_real_tech_departments_still_promote_a_vague_title(department):
    """The 51 departments rule 4 promoted from in the survey; the veto matched exactly one."""
    assert is_tech("Intern", department=department) is True
