"""Verification gate for the tech filter (ADR-0017).

The recall block is the important one: every title here is a real software/tech role and MUST be
kept — a failure means the filter would drop a tech job, which the spec forbids. The precision block
is a sanity check (some non-tech creep is *allowed*, so it holds only clearly non-tech titles).
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from headstart import tech_filter
from headstart.tech_filter import (
    _STRONG,
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


def test_the_version_counter_moved_with_the_line():
    """`role_trends` reads this to tell "we changed who counts" from "the market moved"."""
    from headstart.tech_filter import TECH_FILTER_VERSION

    assert TECH_FILTER_VERSION >= 2
