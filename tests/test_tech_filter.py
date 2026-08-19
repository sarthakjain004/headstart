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
