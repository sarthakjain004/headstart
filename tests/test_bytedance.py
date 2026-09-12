"""Tests for `headstart.scrapers.bytedance`.

The fixture is three real postings from `jobs.bytedance.com`'s `/search/job/posts`, captured
2026-09-11 — a Regular role, an Intern role, and one whose `city_info` states "Singapore" at all
three levels of its parent chain (city/state/country), chosen to exercise the location dedupe.
Measurements behind every assertion are in `docs/bytedance/2026-09-11_api-measurement.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.bytedance import ByteDanceScraper, _location
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
    """No discovery for a single-company board (ADR-0139) — the slug is always this one host."""
    scraper = _scraper()
    assert scraper.slug == SLUG
    assert scraper.board_key() == f"bytedance:{SLUG}"


def test_company_defaults_to_bytedance():
    scraper = get_scraper("bytedance", SLUG)
    assert scraper.company == "ByteDance"


def test_alias_key_is_its_own_slug():
    """A single-company board has no sibling host to alias against (ADR-0139's consequence)."""
    assert _scraper().alias_key() == SLUG


def test_has_no_detail_pass():
    """The listing carries description+requirement in full — measured 0/100 sampled postings
    truncated or HTML-tagged (module docstring)."""
    assert ByteDanceScraper.has_detail_pass is False


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
    """The listing's `description` and `requirement` fields are both full text (module
    docstring) — this scraper keeps both rather than dropping the requirements section."""
    job = _jobs()[REGULAR_ID]
    assert job.description is not None
    assert "About the Team" in job.description
    assert "Minimum Qualifications" in job.description


def test_posted_at_is_always_none():
    """No date field exists anywhere in this API's payload (module docstring) — this is a
    measured fact about the source, not a missed field."""
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


# --------------------------------------------------------------------------- _location dedupe


def test_location_dedupes_a_repeated_name_across_levels():
    """Singapore states the identical name at city, state (province) and country level — a
    naive join would read "Singapore, Singapore, Singapore"."""
    singapore_city_info = next(
        p["city_info"] for p in _posts() if p["id"] == SINGAPORE_ID
    )
    assert _location(singapore_city_info) == "Singapore"


def test_location_keeps_distinct_levels():
    city_info = {
        "en_name": "Hong Kong (China)",
        "parent": {
            "en_name": "Hong Kong Island",
            "parent": {"en_name": "Hong Kong, China", "parent": None},
        },
    }
    assert (
        _location(city_info) == "Hong Kong (China), Hong Kong Island, Hong Kong, China"
    )


def test_location_none_for_missing_city_info():
    assert _location(None) is None
    assert _location({}) is None


# --------------------------------------------------------------------------- skip rules


def test_skips_a_post_with_no_title_or_no_id():
    posts = _posts()
    broken = [{**posts[0], "title": ""}, {**posts[0], "id": None}]
    jobs = _scraper().parse(broken, SCRAPED_AT)
    assert jobs == []
