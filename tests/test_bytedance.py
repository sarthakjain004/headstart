"""Tests for `headstart.scrapers.bytedance`.

The fixture is three real postings from `jobs.bytedance.com`'s `/search/job/posts`, captured
2026-09-11 — a Regular role, an Intern role, and one whose `city_info` states "Singapore" at all
three levels of its parent chain (city/state/country), chosen to exercise the location dedupe.
Measurements behind every assertion are in `docs/bytedance/2026-09-11_api-measurement.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.bytedance import (
    _MAX_PAGES,
    _PAGE_SIZE,
    ByteDanceScraper,
    _location,
)
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


def test_alias_key_is_its_own_slug():
    """A Single source scraper has no sibling host to alias against (ADR-0139's consequence)."""
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


# --------------------------------------------------------------------------- pagination
#
# Live-verified 2026-09-12: a `limit=5` call returned an array of 5 posts against
# `data.count: 1396` — a genuine stated total, distinct from the array length, not "array length
# doubling as the total". `fetch_raw` below reads `count` for exactly that reason, so these tests
# exercise the pagination/truncation loop the way `test_oracle.py`'s `_paged` harness does for
# Oracle's analogous walk, rather than trusting a single big-`limit` call (which happened to be
# ≥ the live board's size on the day it was measured, but is not a documented API contract).


class _FakeSearch:
    """Stands in for `ByteDanceScraper._search`, paging over `real_ids` at `_PAGE_SIZE` per call
    and reporting `total` as `count` — independently of how many ids actually exist, so a walk
    that runs out of real postings before reaching `total` is exactly reproducible."""

    def __init__(self, real_ids: range, total: int) -> None:
        self.real_ids = list(real_ids)
        self.total = total
        self.offsets: list[int] = []

    def __call__(self, offset: int) -> dict:
        self.offsets.append(offset)
        batch = self.real_ids[offset : offset + _PAGE_SIZE]
        return {
            "job_post_list": [{"id": str(i), "title": f"Job {i}"} for i in batch],
            "count": self.total,
        }


def test_pagination_walks_every_page_until_the_total_is_met(monkeypatch):
    fake = _FakeSearch(range(450), total=450)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search", fake)
    posts = scraper.fetch_raw()
    assert len(posts) == 450
    assert fake.offsets == [0, 200, 400]
    assert scraper.truncated is None


def test_a_short_first_page_does_not_end_the_walk_early(monkeypatch):
    """The exact defect the coordinator's live question was about: a page shorter than the
    stated total must not be read as "that's everything" just because the array came back."""
    fake = _FakeSearch(range(450), total=450)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search", fake)
    posts = scraper.fetch_raw()
    # The first page alone (200 rows) is far short of the stated total (450) — proving the walk
    # continued past it is proving `count`, not array length, drives termination.
    assert len(posts) > _PAGE_SIZE
    assert len(posts) == 450


def test_a_board_short_of_its_own_total_is_marked_truncated(monkeypatch):
    """Only 200 postings actually exist behind a board stating 300 — the empty page at offset
    200 ends the walk, and the shortfall is reported rather than silently served as complete."""
    fake = _FakeSearch(range(200), total=300)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search", fake)
    posts = scraper.fetch_raw()
    assert len(posts) == 200
    assert scraper.truncated
    assert "200 of 300" in scraper.truncated


def test_a_negligible_shortfall_is_not_marked_truncated(monkeypatch):
    """991 of a stated 1,000 is 99.1% — at/above `MIN_AUTHORITATIVE_SHARE` (ADR-0121), so the
    list stays authoritative and the missing 9 ids are left to ADR-0083's grace period."""
    fake = _FakeSearch(range(991), total=1000)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search", fake)
    scraper.fetch_raw()
    assert scraper.truncated is None


def test_hitting_the_page_cap_marks_truncated(monkeypatch):
    """A backstop no real Board reaches today (1,396 postings against a 10,000-postings ceiling
    at `_PAGE_SIZE=200`), kept because an un-clamped `limit` measured live is not a guarantee the
    API keeps that shape — an unbounded pagination loop is not something to leave to that."""
    fake = _FakeSearch(range(10**6), total=10**6)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search", fake)
    scraper.fetch_raw()
    assert scraper.truncated
    assert "page cap" in scraper.truncated
    assert len(fake.offsets) == _MAX_PAGES


def test_an_empty_page_ends_the_walk_when_no_total_is_stated(monkeypatch):
    """`total and len(posts) >= total` is load-bearing: without the `total and` guard, a missing
    `count` (0) makes `len(posts) >= 0` true and the walk stops after one page."""
    fake = _FakeSearch(range(250), total=0)
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_search", fake)
    posts = scraper.fetch_raw()
    assert len(posts) == 250
    assert fake.offsets == [0, 200, 400]
    assert scraper.truncated is None
