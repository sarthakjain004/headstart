"""JobScore's public feed, recorded 2026-10-03; personal hiring-team data omitted."""

import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper

RAW = json.loads(
    (Path(__file__).parent / "fixtures/jobscore_jobscore.json").read_text()
)


def test_full_feed_preserves_source_fields_without_a_detail_request():
    (job,) = get_scraper("jobscore", "jobscore").parse(RAW, "2026-10-03T00:00:00Z")
    assert job.id == "jobscore:jobscore:dpi9LtxFTluy3ZQtfTfYWo"
    assert job.company == "JobScore"
    assert job.title == "Senior Front-End Engineer"
    assert job.remote is True
    assert job.location == "Remote in Joinville, Santa Catarina, Brazil"
    assert job.posted_at == "2026-01-20T18:35:42.129Z"
    assert job.employment_type == "Full Time"
    assert job.department == "Engineering"
    assert "7+ years of commercial software engineering" in job.description
    assert job.url.endswith("/senior-front-end-engineer-dpi9LtxFTluy3ZQtfTfYWo")


def test_compensation_cents_become_an_hourly_dollar_range():
    from headstart.jobs import salary

    raw = json.loads(
        (Path(__file__).parent / "fixtures/jobscore_avispatechnology.json").read_text()
    )
    (job,) = get_scraper("jobscore", "avispatechnology").parse(
        raw, "2026-10-03T00:00:00Z"
    )
    span = salary.from_field(job.salary, ats="jobscore")
    assert span.min_annual == 83200
    assert span.max_annual == 83200
    assert span.currency == "USD"


def test_a_missing_jobs_envelope_fails_instead_of_certifying_an_empty_board():
    import pytest

    with pytest.raises(TypeError):
        get_scraper("jobscore", "jobscore").parse({"error": "unavailable"}, "now")
    assert (
        get_scraper("jobscore", "jobscore").parse(
            {"company_code": "jobscore", "jobs": []}, "now"
        )
        == []
    )


def test_a_lone_salary_ceiling_is_not_a_salary_floor():
    from copy import deepcopy

    raw = deepcopy(RAW)
    raw["jobs"][0].update(
        public_salary_minimum=None,
        public_salary_maximum=9000000,
        currency_code="USD",
        public_compensation_interval="per year",
    )
    (job,) = get_scraper("jobscore", "jobscore").parse(raw, "now")
    assert job.salary is None


def test_yen_compensation_is_already_in_whole_yen_not_cents():
    from headstart.jobs import salary

    raw = json.loads(
        (Path(__file__).parent / "fixtures/jobscore_imgix_jpy.json").read_text()
    )
    (job,) = get_scraper("jobscore", "imgix").parse(raw, "now")
    assert salary.from_field(job.salary, ats="jobscore").min_annual == 10000000
