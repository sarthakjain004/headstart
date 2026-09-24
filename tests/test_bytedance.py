"""Tests for `headstart.scrapers.bytedance`.

The fixture is three real postings from `jobs.bytedance.com`'s `/search/job/posts`, captured
2026-09-11 — a Regular role, an Intern role, and one whose `city_info` states "Singapore" at all
three levels of its parent chain (city/state/country), chosen to exercise the location dedupe.
Measurements behind every assertion are in `docs/bytedance/2026-09-11_api-measurement.md`.

What ByteDance shares with TikTok — the walk, the envelope check, the parse rules — is tested once
per brand in `test_supplier_search.py` (ADR-0198); this file keeps ByteDance's identity and its
real postings.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.bytedance import ByteDanceScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "jobs.bytedance.com"
REGULAR_ID = "7673941558289205509"
INTERN_ID = "7670329439609850165"
SINGAPORE_ID = "7673510574963738885"


def _posts() -> list[dict]:
    with open(FIXTURES / "bytedance_search.json", encoding="utf-8") as fh:
        return json.load(fh)["data"]["job_post_list"]


def _scraper() -> ByteDanceScraper:
    return get_scraper("bytedance", SLUG)


def _jobs():
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(_posts(), SCRAPED_AT)}


# --------------------------------------------------------------------------- identity


def test_the_slug_is_the_fixed_host():
    """No discovery for a Single source scraper (ADR-0139) — the slug is always this one host."""
    scraper = _scraper()
    assert scraper.slug == SLUG
    assert scraper.board_key() == f"bytedance:{SLUG}"


def test_company_defaults_to_bytedance():
    scraper = get_scraper("bytedance", SLUG)
    assert scraper.company == "ByteDance"


# --------------------------------------------------------------------------- parse


def test_parses_id_title_and_url():
    job = _jobs()[REGULAR_ID]
    assert job.id == f"bytedance:{SLUG}:{REGULAR_ID}"
    assert job.ats == "bytedance"
    assert (
        job.title == "Research Scientist Graduate (DPU & AI Infra) - 2027 Start (PhD)"
    )
    assert job.url == f"https://jobs.bytedance.com/en/position/{REGULAR_ID}"


def test_description_joins_description_and_requirement():
    """The listing's `description` and `requirement` fields are both full text (`supplier_search`'s
    module docstring) — this scraper keeps both rather than dropping the requirements section."""
    job = _jobs()[REGULAR_ID]
    assert job.description is not None
    assert "About the Team" in job.description
    assert "Minimum Qualifications" in job.description


def test_posted_at_is_always_none():
    """No date field exists anywhere in this API's payload (`supplier_search`'s module
    docstring) — this is a measured fact about the source, not a missed field."""
    for job in _jobs().values():
        assert job.posted_at is None


def test_employment_type_from_recruit_type():
    assert _jobs()[REGULAR_ID].employment_type == "Regular"
    assert _jobs()[INTERN_ID].employment_type == "Intern"


def test_department_from_job_category():
    job = _jobs()[REGULAR_ID]
    assert job.department == "R&D"


def test_location_includes_city_and_country():
    job = _jobs()[REGULAR_ID]
    assert job.location == "San Jose, California, United States of America"


def test_a_name_stated_at_every_level_is_named_once():
    """Singapore states the identical name at city, state (province) and country level — a
    naive join would read "Singapore, Singapore, Singapore"."""
    assert _jobs()[SINGAPORE_ID].location == "Singapore"
