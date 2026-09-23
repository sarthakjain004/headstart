"""Verification gate for the tech filter (ADR-0017).

The recall block is the important one: every title here is a real software/tech role and MUST be
kept — a failure means the filter would drop a tech job, which the spec forbids. The precision block
is a sanity check (some non-tech creep is *allowed*, so it holds only clearly non-tech titles).
"""

from __future__ import annotations

import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from headstart import tech_filter
from headstart.tech_filter import (
    _STRONG,
    TECH_FILTER_VERSION,
    classify,
    filter_jobs,
    filter_jobs_and_report,
    is_tech,
    report,
)

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


def _write_corpus(src, sizes):
    """One {ats}.jsonl per entry, `n` rows each — half tech, half not."""
    src.mkdir(parents=True, exist_ok=True)
    for ats, n in sizes.items():
        rows = [
            {
                "id": f"{ats}:a:{i}",
                "title": "Backend Engineer" if i % 2 else "Chef de Partie",
            }
            for i in range(n)
        ]
        (src / f"{ats}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )


def test_filter_jobs_is_identical_pooled_and_inline(tmp_path):
    """The process pool must not change a single byte of the answer — same stats, same rows.

    The pre-existing single-file test only ever exercised the inline path (one input file skips
    the pool), so without this the parallel branch ships untested.
    """
    sizes = {"workday": 40, "greenhouse": 25, "lever": 10, "apple": 5}
    src = tmp_path / "jobs"
    _write_corpus(src, sizes)

    inline = filter_jobs(src, tmp_path / "inline", workers=1)
    pooled = filter_jobs(src, tmp_path / "pooled", workers=4)

    assert inline == pooled
    assert pooled == {ats: (n // 2, n) for ats, n in sizes.items()}
    for ats in sizes:
        name = f"{ats}.jsonl"
        assert (tmp_path / "pooled" / name).read_text() == (
            tmp_path / "inline" / name
        ).read_text()


def test_filter_jobs_submits_largest_file_first(tmp_path, monkeypatch):
    """LPT ordering contributes to the speed-up (not the whole of it — parallelism across the
    other files does most of the work; see `filter_jobs`'s docstring), and it is invisible in
    the output, so it needs its own guard.

    The files are deliberately named so that alphabetical order is the *reverse* of size order:
    a regression to `sorted(glob(...))` would start the largest file last and straggle on it,
    while every assertion about stats and rows still passed.
    """
    src = tmp_path / "jobs"
    _write_corpus(src, {"aaa": 4, "mmm": 20, "zzz": 60})
    seen: list[str] = []

    def recording(pair):
        seen.append(pair[0].stem)
        return real(pair)

    real = tech_filter._filter_file
    monkeypatch.setattr(tech_filter, "_filter_file", recording)
    # workers=1 keeps it inline, so the recorded order is the submission order.
    filter_jobs(src, tmp_path / "tech", workers=1)
    assert seen == ["zzz", "mmm", "aaa"]


class _RecordingPool:
    """Stands in for `ProcessPoolExecutor` in `test_filter_jobs_submits_largest_file_first_to_the_pool`.

    Records the order `filter_jobs` calls `pool.submit(...)` in, then actually runs the work on a
    thread pool — a real subprocess isn't needed to prove *what order submission happened in*, and
    threads keep the test fast. `mp_context` is accepted and ignored, matching the kwarg
    `filter_jobs` always passes.
    """

    def __init__(self, max_workers=None, mp_context=None):
        del mp_context
        self._inner = ThreadPoolExecutor(max_workers=max_workers)
        self.submitted: list[str] = []

    def submit(self, fn, pair):
        self.submitted.append(pair[0].stem)
        return self._inner.submit(fn, pair)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._inner.shutdown(wait=True)
        return False


def test_filter_jobs_submits_largest_file_first_to_the_pool(tmp_path, monkeypatch):
    """The order test above only proves the *inline* (workers=1) path, which computes the same
    `pairs` list the pooled path does but never calls `pool.submit` — a regression that re-sorted
    only inside the pool branch would still pass it. This drives the real `workers=4` branch and
    inspects what actually reached `pool.submit`.
    """
    src = tmp_path / "jobs"
    _write_corpus(src, {"aaa": 4, "mmm": 20, "zzz": 60})

    pools: list[_RecordingPool] = []

    def factory(max_workers=None, mp_context=None):
        pool = _RecordingPool(max_workers=max_workers, mp_context=mp_context)
        pools.append(pool)
        return pool

    monkeypatch.setattr(tech_filter, "ProcessPoolExecutor", factory)
    filter_jobs(src, tmp_path / "tech", workers=4)
    assert len(pools) == 1
    assert pools[0].submitted == ["zzz", "mmm", "aaa"]


def test_report_logs_per_ats_table_and_grand_total(caplog):
    logger = logging.getLogger("test_tech_filter.report")
    stats = {"greenhouse": (2, 3), "lever": (5, 5)}
    with caplog.at_level(logging.INFO):
        report(stats, "data/jobs/tech", logger)
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(m.startswith("greenhouse") for m in infos)
    assert any(m.startswith("lever") for m in infos)
    total_lines = [m for m in infos if m.startswith("TOTAL")]
    assert len(total_lines) == 1
    assert "dropped 1 non-tech" in total_lines[0]
    assert total_lines[0].endswith("-> data/jobs/tech")
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_report_warns_on_an_ats_that_contributed_zero_rows(caplog):
    """An ATS present in `stats` with zero rows is named in a WARNING regardless of *why* it
    contributed nothing — a failed Board, a budget-deferred one, or a genuinely empty one all
    reach `report` as the same `(0, 0)`, which is exactly why the warning cannot and does not
    distinguish them (see the comment in `report`). An ATS absent from `stats` altogether — not
    in this run's slice at all — is a different case and must not be named.
    """
    logger = logging.getLogger("test_tech_filter.report")
    stats = {"greenhouse": (2, 3), "jazzhr": (0, 0)}
    with caplog.at_level(logging.INFO):
        report(stats, "data/jobs/tech", logger)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "jazzhr" in message
    assert "greenhouse" not in message  # scraped rows this run, not one of the zeros
    assert (
        "jobvite" not in message
    )  # never in this run's slice at all — no file, no mention


def test_report_errors_when_the_whole_corpus_is_zero(caplog):
    """Every ATS in the slice scraped nothing: the corpus-wide zero gets its own ERROR line, on
    top of (not instead of) the per-ATS zero WARNING — the two questions ("is this ATS broken"
    and "is the whole run broken") are answered separately, and no TOTAL line fires."""
    logger = logging.getLogger("test_tech_filter.report")
    stats = {"jazzhr": (0, 0), "jobvite": (0, 0)}
    with caplog.at_level(logging.INFO):
        report(stats, "data/jobs/tech", logger)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        "no rows at all reached the tech filter -> data/jobs/tech is empty"
    ]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "jazzhr" in warnings[0] and "jobvite" in warnings[0]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert not any(m.startswith("TOTAL") for m in infos)


def test_report_logs_through_the_callers_logger_so_the_tag_is_preserved(caplog):
    """`report` must not open a logger of its own: the `[filter_tech]` tag (ADR-0039) belongs to
    the pipeline-stage entry point, and this module keeps it only by logging through whatever
    logger that entry point hands it."""
    logger = logging.getLogger("headstart.ingest.filter_tech")
    with caplog.at_level(logging.INFO):
        report({"greenhouse": (1, 1)}, "data/jobs/tech", logger)
    assert caplog.records
    assert all(r.name == "headstart.ingest.filter_tech" for r in caplog.records)


def test_filter_jobs_and_report_filters_then_reports(tmp_path, caplog):
    src = tmp_path / "jobs"
    src.mkdir()
    rows = [
        {"id": "greenhouse:a:1", "title": "Backend Engineer"},
        {"id": "greenhouse:a:2", "title": "Registered Nurse"},
    ]
    (src / "greenhouse.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    logger = logging.getLogger("test_tech_filter.combined")
    with caplog.at_level(logging.INFO):
        stats = filter_jobs_and_report(src, tmp_path / "tech", logger)
    assert stats["greenhouse"] == (1, 2)
    assert (tmp_path / "tech" / "greenhouse.jsonl").exists()
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any(m.startswith("TOTAL") for m in infos)


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


# --- TECH_FILTER_VERSION 2: the title decides ------------------------------------------------


@pytest.mark.parametrize(
    "title,department",
    [
        # The case this change was decided on: the department says who they sit with, the title
        # says what they do, and on this question the title wins.
        ("Administrative Assistant", "Software Engineering"),
        ("Sales Executive", "Technology"),
        ("Accountant", "IT Services"),
        ("Customer Service Agent - Remote Data Entry", "Data Entry"),
        ("Content Creator", "Software development"),
        # `_STRONG` used to read `title + department`, so "Software development" matched
        # "software dev" and scored these a strong software signal.
        ("Receptionist", "Software Development"),
        ("Recruiter", "Engineering"),
    ],
)
def test_a_technical_department_does_not_promote_another_profession(title, department):
    assert is_tech(title, department=department) is False


@pytest.mark.parametrize(
    "title,department",
    [
        # `_GENERIC` also read `title + department`, so the word "Engineering" in a facilities
        # org made every trade a generic tech token.
        ("Plumber", "Engineering & Facilities"),
        ("Painter", "Engineering & Facilities"),
        ("Carpenter", "Engineering and Maintenance"),
        ("Electrician", "Hotel-Engineering"),
        # rule 4's `security` promoted physical guards; 1,338 on one sweep.
        ("Security Officer", "Security Officers"),
        ("Loss Prevention Officer", "Loss Prevention & Security"),
        ("Armed Security Officer", "Safety and Security"),
    ],
)
def test_a_trade_or_guard_is_not_promoted_by_its_org_label(title, department):
    assert is_tech(title, department=department) is False


def test_the_gate_stays_recall_biased_for_a_genuinely_vague_title():
    """Only titles naming a *different profession* are refused. A vague one still passes."""
    assert is_tech("Analyst", department="Software Engineering") is True
    assert is_tech("Associate", department="Technology") is True
    assert is_tech("Intern", department="Platform Engineering") is True


def test_a_software_title_still_passes_inside_a_non_software_org():
    """The department is blanked, not turned into a veto — rules 1-3 still decide on the title."""
    assert is_tech("Software Engineer", department="Facilities Systems") is True
    assert is_tech("Data Engineer", department="Hotel Engineering") is True
    assert is_tech("Backend Developer", department="Security Officers") is True


@pytest.mark.parametrize(
    "title",
    [
        # Titles the department used to have to carry — each found by reading the postings rule 4
        # rescued over the 2026-09-17 corpus, not invented.
        "Solution Architect",
        "Cloud Solution Architect",
        "Senior Java Architect",
        "AI/ML Architect",
        "Oracle APEX Architect",
        "Network Architect",
        "Penetration Tester",
        "Senior Software Tester",
        "Scrum Master",
        "Systems Analyst",
        "System Administrator",
        "IT System Administrator",
        "IT Manager",
        "IT Support Specialist",
        "Help Desk Technician",
        "Desktop Support Technician",
        "Data Analyst",
        "SOC Analyst",
        "Information Systems Manager",
        "Technical Writer",
        "Technical Project Manager",
        "Technology Support Lead",
        "Power BI Analyst",
        "SAP ABAP Consultant",
        "Salesforce Administrator",
        "Database Administrator",
    ],
)
def test_titles_the_department_used_to_carry_now_stand_alone(title):
    assert is_tech(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Junior Architect",  # a building architect
        "Landscape Architect",
        "Program Manager Non Tech",  # says so outright; Oracle really posts this
        "Project Manager Non Tech",
        "Financial Analyst",
        "Marketing Analyst",
    ],
)
def test_the_widened_patterns_did_not_swallow_their_neighbours(title):
    assert is_tech(title) is False


@pytest.mark.parametrize(
    "title,department",
    [
        # Each of these was a real recall loss in the first draft of `_NON_TECH_ROLE`, found by
        # diffing against the pre-change filter rather than by reading the regex. A gate whose
        # contract is "dropping a real tech job is not acceptable" cannot afford any of them.
        (
            "Windows Server Administrator",
            "IT Infrastructure",
        ),  # bare `server` (the restaurant one)
        ("SQL Server Specialist", "Information Technology"),
        ("Server Support Specialist", "Technology"),
        ("Technician - Software", "Technology"),  # `technician - \w+`
        ("Technician - Network Operations", "IT"),
        ("HR Technology Manager", "Technology"),  # `\bhr\b`
        ("Specialist", "Guardian Data Platform"),  # bare `guard` in the department list
    ],
)
def test_the_non_tech_role_list_does_not_refuse_a_real_software_role(title, department):
    assert is_tech(title, department=department) is True


def test_a_non_software_department_still_vetoes_a_generic_title():
    """ADR-0068's veto must survive rule 4's new guard.

    An earlier draft *blanked* an uninformative department before every rule, which also removed
    it from `_NON_SOFTWARE`'s reach — so "Installation Engineer" in "HVAC & Facilities" flipped
    from non-tech to tech. The guard is scoped to rule 4; the veto at rule 2 is untouched."""
    assert is_tech("Installation Engineer", department="HVAC & Facilities") is False
    assert is_tech("Software Engineer", department="HVAC & Facilities") is True


def test_the_version_counter_moved_with_the_line():
    """`role_trends` reads this to tell "we changed who counts" from "the market moved"."""
    assert TECH_FILTER_VERSION == 4, (
        "bump this and its comment together — the comment carries the commit range and the "
        "measured effect, and a bump without one is what CLAUDE.md's DERIVATIONS_VERSION rule "
        "exists to stop"
    )


@pytest.mark.parametrize(
    "title,department",
    [
        # ADR-0068's veto, which rule 4 has to apply itself now that rules 1-2 read the title.
        # While they read `title + department`, each of these tripped rule 2's generic token off
        # the department's own "engineering" and was vetoed there; reading the title only closed
        # that path, and rule 4 promoted them instead until it gained the same guard.
        ("Civil Designer", "Engineering"),
        ("Welding Inspector", "Engineering"),
        ("HVAC Journeyman Chiller Mechanic", "Engineering"),
        ("Structural EIT/Coordinator", "Building Engineering"),
        ("Mechanical Department Manager", "Mechanical Engineering"),
    ],
)
def test_rule_four_does_not_promote_another_engineering_discipline(title, department):
    assert is_tech(title, department=department) is False


def test_the_two_not_software_lists_read_different_inputs():
    """`_NOT_TECH_DEPT` reads departments, `_NON_TECH_ROLE` reads titles, and their shared members
    earn their place — trimming the five overlapping ones lets 581 rows back in, on labels like
    "Campus Safety & Security" whose titles ("PRIA Specialist") the role list does not match."""
    assert is_tech("PRIA Specialist", department="Safety & Security") is False
    assert (
        is_tech("Regional Security Manager", department="Security & Life Safety")
        is False
    )
    # ...and the title list still works where the department says nothing either way.
    assert is_tech("Security Officer", department="Corporate") is False


# --- TECH_FILTER_VERSION 3: the families still outside the gate ------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Member of Technical Staff",
        "Senior Member of Technical Staff, Post-Training",
        "Lead Member of Technical Staff, Inference Infrastructure",
        "Forward Deployed Engineer",
        "AI Research Scientist",
        "Staff ML Research Scientist, Co-Folding and Affinity",
        "Applied Scientist",
        "Analytics Engineer",
        "Business Intelligence Analyst",
        "BI Analyst",
        "Bioinformatics Analyst",
        "Computational Biologist",
        "ASIC Architect",
        "FPGA Engineer",
        "RTL Design Engineer",
        "Security Operations Analyst",
        "SOC Specialist II - Cyber Security",
        "SOC Manager",
        "QA Lead",
        "QA Analyst",
        "Test Analyst",
        "Manual Tester",
    ],
)
def test_version_3_families_are_kept(title):
    """Each was measured as dropped by version 2 over the 332,383-posting snapshot."""
    assert is_tech(title) is True, f"RECALL VIOLATION: tech job dropped -> {title!r}"


def test_member_of_technical_staff_needs_no_department():
    """It names no discipline, so once rules 1-2 read the title only nothing else could keep it.

    124 rows, on boards whose departments read Modeling, Research, Inference, Technical Staff.
    """
    for dept in ("Modeling", "Research", "Inference", "Technical Staff", ""):
        assert is_tech("Member of Technical Staff", department=dept) is True


def test_forward_deployed_engineer_survives_a_sales_department():
    """An engineering role that reports into GTM, so rule 2 would otherwise veto it.

    Only the engineer spelling: "Forward Deployed Creative" is not an engineering job.
    """
    assert is_tech("Forward Deployed Engineer, GTM", department="Sales") is True
    assert is_tech("Forward Deployed Creative", department="Sales") is False


def test_the_platform_arm_tolerates_a_module_name_in_between():
    """ "SAP FICO Consultant" is the common shape; requiring adjacency left 589 rows out."""
    assert is_tech("SAP FICO Consultant") is True
    assert is_tech("SAP Basis Migration Consultant") is True
    assert is_tech("Salesforce Pre-Sales Architect") is True
    assert is_tech("ServiceNow Business Analyst") is True
    # reverse order, which the adjacent form could not express at all
    assert is_tech("Business Analyst - ServiceNow") is True
    # the product name alone, with no technical role beside it, is still not enough
    assert is_tech("Marketing Manager | B2B Growth | Salesforce & AI") is False


@pytest.mark.parametrize(
    "title",
    [
        "Oracle HCM Cloud Consultant",
        "Oracle Cloud Senior Consultant - SCM",
        "Oracle EPM Consultant",
        "Consultant MS Dynamics 365 F&O",
    ],
)
def test_every_platform_in_the_arm_is_actually_in_both_arms(title):
    """`oracle` fell out of the list when main's own spelling of it was adopted — 48 rows.

    `dynamics` was in the forward arm but not the reverse one, which cost 3 more. Both were
    found by review; neither is visible from the totals, which went up either way.
    """
    assert is_tech(title) is True, f"RECALL VIOLATION: tech job dropped -> {title!r}"


@pytest.mark.parametrize(
    "title", ["ServiceNow Developers", "Higher Education Workday Consultants"]
)
def test_the_platform_arm_still_matches_a_plural_role_word(title):
    r"""Regression: the form this replaced had no closing boundary and kept these.

    Adding `\b` to tighten the new arm dropped both — caught by measuring the change against
    the corpus rather than only counting what it gained.
    """
    assert is_tech(title) is True


@pytest.mark.parametrize(
    "title",
    [
        # bare `\brtl\b` matches the broadcaster; `security specialist` matches physical security
        "Media Consultant (Mensch) RTL / Veltins",
        "EHS and Security Specialist",
    ],
)
def test_the_narrowed_patterns_do_not_fire_on_their_near_misses(title):
    assert is_tech(title) is False


# --- TECH_FILTER_VERSION 4: boundaries, spellings and the "…engineer" trades ------------------


@pytest.mark.parametrize(
    "title",
    [
        # `_` is a word character, so `\b` never fired beside it
        "IN_Senior Associate_Azure Devops_Bengaluru",
        "Application Developer_5",
        # a level glued to the acronym
        "SDE3",
        "SDE2, Amazon",
        # plurals and abbreviations of the role word
        "PHP Developers",
        "Engineers – .NET & React",
        "Software Engr II",
        "Senior Software Engg - Systems",
        "Delv Senior Software Eng",
        "SW Engineer",
        "iOS Dev",
        "Java Lead",
        "Lead Dev",
        "Systems Development Engineer, AWS Hardware",
        # families with no signal before
        "DevSecOps Specialist",
        "Cyber Manager - Cloud DevSecOps",
        "IT Project Manager",
        "IT Site Engineer - Japan",
        "Senior Cybersecurity Incident Responder",
        "Cybersecurity Consultant - IAM / Saviynt Specialist",
        "Principal Analyst Cyber Security Defense Center",
        "Quant Researcher",
        "Computer Scientist",
        "Test Lead",
        "Oracle-ERP Tester",
        "Research Scientist, AI",
        "Citrix Administrator / Consultant",
        "System and Application Administrator",
        "MES Engineer",
        "Process Mining Developer",
        "Data Mining Engineer",
    ],
)
def test_version_4_titles_are_kept(title):
    assert is_tech(title) is True, f"RECALL VIOLATION: tech job dropped -> {title!r}"


@pytest.mark.parametrize(
    "title",
    [
        "Hardware Test and Validation Engineer",
        "Manufacturing Software Support Engineer",
        "Embedded Hardware Engineer",
    ],
)
def test_a_setting_word_does_not_veto_software_work(title):
    """`hardware`/`manufacturing` name where the code runs when the title also names the work."""
    assert is_tech(title) is True


@pytest.mark.parametrize("title", ["Hardware Engineer", "Manufacturing Engineer"])
def test_a_setting_word_still_vetoes_on_its_own(title):
    assert is_tech(title) is False


@pytest.mark.parametrize(
    "title",
    [
        "Site Engineer",
        "MEP Engineer",
        "QA/QC Engineer",
        "Senior Highway Engineer",
        "Business Developer",
        "Business Development Engineer",
        "Front End Manager",
        "Front End Lead Clerk",
        "CNC Programmer",
        "JD/LLM – Tax Analyst",
        "Mechanical Engineering Manager",
        "Mechanical Engineering Manager, Air Handling Systems",
    ],
)
def test_version_4_trades_are_refused(title):
    assert is_tech(title) is False, f"non-tech kept -> {title!r}"


@pytest.mark.parametrize(
    "title",
    [
        # the vetoes and exclusions above must not reach the software roles sharing their words
        "Site Reliability Engineer",
        "Web Site Developer",
        "Frontend Lead",
        "Front End Developer",
        "Software/ Electrical Engineering Manager",
        "CNC Programmer / Software Developer",
        "Engineering Manager",
        "LLM Engineer",
    ],
)
def test_version_4_exclusions_do_not_reach_their_software_neighbours(title):
    assert is_tech(title) is True, f"RECALL VIOLATION: tech job dropped -> {title!r}"


@pytest.mark.parametrize(
    "title",
    [
        # `cyber` names the market these roles sell to, and "AI Specialist" is the crowdwork
        # labelling role ADR-0087 keeps out — neither may become a strong signal
        "Business Development Representative - Cybersecurity",
        "Marketing Specialist - Cybersecurity",
        "Legal AI Specialist - Litigation",
        "Field Applications Engineer",
    ],
)
def test_version_4_domain_words_stay_qualified(title):
    assert not tech_filter._STRONG.search(title), title


def test_a_set_aside_trade_goes_to_rule_4_and_its_guards():
    """Rule 4's own guards keep the trades out; a technical department keeps a front-end manager.

    "Frontend Manager" on a streaming platform's board is a software role (review of #573).
    """
    assert is_tech("CNC Programmer", department="Engineering") is False
    assert is_tech("Mechanical Engineering Manager", department="Engineering") is False
    assert is_tech("Front End Manager", department="Retail, Store Ops") is False
    assert is_tech("Front End Manager", department="Technology") is True


@pytest.mark.parametrize(
    "title,department",
    [
        # review of #573: v3 kept each of these and the first cut of v4 dropped them
        ("Frontend Manager", ""),
        ("Front End Manager - React", ""),
        ("Mechanical Engineering Manager - Software", ""),
        ("Process Engineering Manager - Automation Software", ""),
        ("Industrial Engineering Manager - MES", ""),
        ("Civil Engineering Manager - GIS", ""),
        ("CNC Programmer - CAM Software", ""),
        ("Software, Electrical Engineering Manager", ""),
        ("Site Engineer - Fiber", ""),
        ("Site Engineer - 5G RAN", ""),
        ("Field Site Engineer - Wireless", ""),
        ("Site Engineer - Mission Critical", ""),
        ("Site Engineer", "DCO"),
        ("Business Development Engineer, AWS", ""),
        # glued levels beyond SDE
        ("Developer3", ""),
        ("SRE2", ""),
        ("DevOps3", ""),
    ],
)
def test_version_4_review_losses_are_kept(title, department):
    assert is_tech(title, department or None) is True, title


@pytest.mark.parametrize(
    "title",
    ["Business Dev Manager", "Biz Dev Lead", "Web Lead", "Game Lead", "Mobile Lead"],
)
def test_version_4_dev_and_lead_arms_stay_tied_to_a_discipline(title):
    assert not tech_filter._STRONG.search(title), title


def test_a_law_degree_is_not_a_language_model():
    assert is_tech("LLM Tax Associate") is False
    assert is_tech("JD/LLM – Tax Analyst") is False
    assert is_tech("LLM Engineer") is True


# --- the labelled evaluation set -------------------------------------------------------------
#
# 971 English titles (+ department) from our own data, not a third-party corpus: 400 drawn from
# the served table, 300 the version-3 gate dropped from the pre-filter snapshot, and 300 whose
# verdict version 4 changed. Each was labelled blind by two independent labellers (973 of
# the 1,000 sampled rows agreed, before 29 non-English rows were dropped) and the 27 disagreements settled by hand; `ambiguous` rows are kept for reference
# and scored by neither test. Stratified, so these are regression gates, not population rates.

_EVAL = Path(__file__).parent / "fixtures" / "tech_filter_eval.tsv"

# Tech titles the gate is known to drop. A new miss fails the recall test; so does fixing one of
# these, so the list only ever shrinks on purpose.
_KNOWN_MISSES = {
    # "Consultant" beside "Gen-AI" is also the crowdwork labelling role ADR-0087 keeps out.
    "IA- Consultant-Gen-AI/Agentic AI",
}

# Non-tech titles the gate keeps today — the recall-biased "…engineer" creep (field service,
# process and quality engineers). A change may lower this; raising it needs a reason.
_FALSE_POSITIVE_CEILING = 86


def _eval_rows(label: str) -> list[tuple[str, str]]:
    with _EVAL.open(encoding="utf-8", newline="") as fh:
        return [
            (row["title"], row["department"])
            for row in csv.DictReader(fh, delimiter="\t")
            if row["label"] == label
        ]


def test_the_labelled_set_keeps_every_tech_title_but_the_known_misses():
    tech = _eval_rows("tech")
    missed = {title for title, dept in tech if not is_tech(title, dept or None)}
    assert missed == _KNOWN_MISSES, (
        f"new misses: {sorted(missed - _KNOWN_MISSES)}; "
        f"fixed (drop from _KNOWN_MISSES): {sorted(_KNOWN_MISSES - missed)}"
    )


def test_the_labelled_set_admits_no_more_non_tech_than_before():
    non_tech = _eval_rows("not_tech")
    kept = [title for title, dept in non_tech if is_tech(title, dept or None)]
    assert len(kept) <= _FALSE_POSITIVE_CEILING, kept


@pytest.mark.parametrize(
    "title,department",
    [
        ("JD Edwards CNC Administrator", "Information Technology"),
        ("JDE CNC Admin - Long term Contract", "Information Technology"),
        ("CNC/JD Edward Admin", "Information Technology"),
        ("Edwards CNC", "IT Services"),
    ],
)
def test_jd_edwards_cnc_is_the_erp_not_the_machine_shop(title, department):
    """Review of #573: putting `cnc` in `_NON_TECH_ROLE` first dropped these, measured."""
    assert is_tech(title, department) is True


def test_a_machine_shop_title_in_a_mislabelled_it_department_is_refused():
    assert is_tech("CNC Turner", department="Information Technology") is False


# --- critique of version 4 (6/10): bugs, overrides, siblings, recall groups -------------------


@pytest.mark.parametrize(
    "title,department",
    [
        # regex bugs
        (".NET Lead", ""),
        ("Sr.Net Lead", ""),
        ("Senior Member Technical Staff", ""),
        ("SMTS, Silicon Validation", ""),
        ("Analyst, Identity Access Management", ""),
        ("SOC L3 Analyst", ""),
        ("CSOC Analyst", ""),
        ("IT & Cybersecurity Support Technician", ""),
        ("IT Infrastructure & Network Lead", ""),
        ("Chief Technology Officer", ""),
        ("LLM Program Manager", ""),
        ("Research Intern – Reinforcement Learning", "AI Research"),
        # the lower-risk groups from the Indeed rejects
        ("Kubernetes L3 Lead", ""),
        ("Snowflake Admin", ""),
        ("Mainframe SME", ""),
        ("Production Support Analyst", ""),
        ("L2 Application Support Engineer", ""),
        ("Threat Intelligence Analyst", ""),
        ("Vulnerability Management Specialist", ""),
        ("Cloud Consultant", ""),
        ("Splunk Administrator", ""),
        ("AI Team Lead", ""),
        ("Application Development Specialist", ""),
        ("Technical Delivery Manager", ""),
        # glued words and compounds the per-word-start matching must still reach
        ("SeniorSolution Architect - Public Sector", ""),
        ("Tier 1Technical Support Analyst (Hybrid)", ""),
        ("Outsystems Architect", ""),
        ("Senior GPU Memory Subsystem Architect", ""),
        ("CIAM Architect", ""),
        ("Masterdata Analyst III", ""),
        ("Hybrid and Multicloud Architect", ""),
        # the served-table read of the new vetoes: each of these was dropped by a first cut
        ("Rails Application Developer", ""),
        ("SD3 - Principal Engineer - Ruby & Rails (RoR) Application Development", ""),
        ("Lead Systems RMA Engineer --Air Traffic Control", ""),
        ("Epic Bridges EDI Developer", ""),
        ("Principal AI/ ML Robotics Engineer - Environmental Perception", ""),
        ("Senior Communications Engineer, Rail Systems, Seattle WA", ""),
        ("Lead Engineer - SW Design - RAIL", ""),
        ("LabVIEW Quality Engineer", "Aerospace"),
        ("PLM Administrator, Configuration Management", "Mechanical Engineering"),
        ("Senior Solutions Consultant", "Sales - Sales Engineering"),
        (
            "PC Technician - Edmonton",
            "Admin (Office, Sales, IT, Customs, Central Dispatch, etc.)",
        ),
        (
            "Sr. Specialist - L1 Integration Testing",
            "Vehicle Software & Electrical Engineering",
        ),
        ("IT Project Leader", "Manufacturing Engineering"),
        ("Front End Team Member", ""),
        ("Staff Design Quality Engineer (Software/Electrical)", ""),
    ],
)
def test_version_4_critique_keeps(title, department):
    assert is_tech(title, department or None) is True, f"RECALL VIOLATION -> {title!r}"


@pytest.mark.parametrize(
    "title,department",
    [
        # strong arms no longer override the sales/insurance/discipline vetoes
        ("Sales Engineer – AI", ""),
        ("Civil Engineer – ML", ""),
        ("Cyber Sales Manager", ""),
        ("Cybersecurity Sales Director", ""),
        ("Cyber Claims Specialist", ""),
        ("AI Lead Generation & Outreach Executive", ""),
        # rule 0's rescue reads code words only
        ("CNC Programmer/Tool & Die Maker", ""),
        ("Manufacturing Engineering Manager - Automation", ""),
        # highway's siblings
        ("Bridge Engineer", ""),
        ("Traffic Engineer", ""),
        ("Water Resources Engineer", ""),
        ("Transportation Engineer", ""),
        ("Environmental Engineer", ""),
        ("Substation Engineer", ""),
        ("Railway Engineer", ""),
        ("Facilities Engineer", ""),
        ("Supplier Quality Engineer", ""),
        # rule 4 reads a non-software discipline in the department
        ("Intern", "Building Engineering"),
        ("Intern", "Electrical Engineering"),
        # accidental v3 substring hits the per-word-start matching retires
        ("Geotechnical Project Manager", ""),
        ("Assoc Analyst Procurement", ""),
        ("Property Tax Protest Analyst", ""),
        ("Looking for Mechanical Engineer or Automobile Engineer", "Engineering"),
    ],
)
def test_version_4_critique_refuses(title, department):
    assert is_tech(title, department or None) is False, f"non-tech kept -> {title!r}"


# --- the blind hold-out ------------------------------------------------------------------------
#
# 800 English Indeed titles drawn at random AFTER version 4 was final — 500 it drops, 300 it
# keeps, none already in the labelled set above — and labelled blind by two labellers whose every
# output line echoed its title (a first pass had drifted a row out of alignment; this one
# verified 800/800). 783 agreed; the 17 disagreements were settled by hand. Weighted back to the
# 557,580-row population it puts recall at ~84.7% (77.6-89.7%) and precision at ~81.1%
# (docs/tech-filter/2026-09-23_spellings-and-trades.md).
#
# Nothing was tuned on it, and the titles are deliberately not listed here: its value is being an
# honest measurement. These tests only stop it getting worse. A change that lowers either count
# may lower the ceiling with it; one that raises either needs a reason, not a new ceiling.

_HOLDOUT = Path(__file__).parent / "fixtures" / "tech_filter_holdout.tsv"
_HOLDOUT_MISS_CEILING = 22  # tech titles dropped, of 241
_HOLDOUT_FALSE_POSITIVE_CEILING = 51  # non-tech titles kept, of 487


def _holdout(label: str) -> list[str]:
    with _HOLDOUT.open(encoding="utf-8", newline="") as fh:
        return [
            r["title"]
            for r in csv.DictReader(fh, delimiter="\t")
            if r["label"] == label
        ]


def test_the_hold_out_drops_no_more_tech_than_it_did():
    missed = [t for t in _holdout("tech") if not is_tech(t)]
    assert len(missed) <= _HOLDOUT_MISS_CEILING, missed


def test_the_hold_out_keeps_no_more_non_tech_than_it_did():
    kept = [t for t in _holdout("not_tech") if is_tech(t)]
    assert len(kept) <= _HOLDOUT_FALSE_POSITIVE_CEILING, kept
