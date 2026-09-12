"""Tests for `headstart.scrapers.tiktok`.

`tiktok_jobs.json` is four real postings from `api.lifeattiktok.com`'s public search endpoint,
captured 2026-09-11 (`docs/tiktok/2026-09-11_api-measurement.md`) — chosen to cover a US, two
Singapore and an Australia posting, an "Intern" and a "Regular" `recruit_type`, and a
`job_subject` that is present on some rows and absent on others. None of the four states any kind
of date, matching what was measured across the wider 100-row sample: this API has no posted-date
field at all, so `posted_at` is always None here (asserted below rather than assumed).
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.models import html_to_text
from headstart.scrapers.registry import get_scraper
from headstart.scrapers.tiktok import TikTokScraper, _location_of

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "lifeattiktok.com"


def _raw():
    with open(FIXTURES / "tiktok_jobs.json", encoding="utf-8") as fh:
        return json.load(fh)


def _scraper():
    return get_scraper("tiktok", SLUG, "TikTok")


def _jobs():
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(_raw(), SCRAPED_AT)}


# --------------------------------------------------------------------------- identity


def test_registered_under_tiktok():
    scraper = _scraper()
    assert isinstance(scraper, TikTokScraper)
    assert scraper.ats == "tiktok"
    assert scraper.board_key() == f"tiktok:{SLUG}"


def test_alias_key_is_its_own_slug():
    # ADR-0139: a Single source scraper has no sibling tenant to alias against, and the base
    # implementation's redirect probe would hit the 503 the module docstring measured.
    assert _scraper().alias_key() == SLUG


# --------------------------------------------------------------------------- parse


def test_every_fixture_job_is_kept():
    jobs = _jobs()
    assert len(jobs) == 4
    assert set(jobs) == {
        "7669925556293601541",
        "7665646635395188997",
        "7663386899855231237",
        "7660782103374530869",
    }


def test_job_url_is_the_search_id_link():
    job = _jobs()["7669925556293601541"]
    assert job.url == f"https://{SLUG}/search/7669925556293601541"


def test_title_location_department_and_employment_type():
    job = _jobs()["7669925556293601541"]
    assert job.title.startswith("Category Manager Intern")
    assert job.location == "Los Angeles, California, United States of America"
    assert job.department == "Operations"
    assert job.employment_type == "Intern"
    assert job.remote is False  # a place name, not "remote"


def test_description_concatenates_description_and_requirement():
    raw = next(j for j in _raw() if j["id"] == "7669925556293601541")
    job = _jobs()["7669925556293601541"]
    combined = html_to_text(raw["description"] + "\n\n" + raw["requirement"])
    assert job.description == combined
    assert "Minimum Qualifications" in job.description


def test_no_posted_date_field_exists_anywhere():
    # Measured across a 100-row sample of the live API (module docstring): zero non-null date of
    # any kind. Every fixture job must therefore parse to None, not a fabricated value.
    for job in _jobs().values():
        assert job.posted_at is None


def test_singapore_location_does_not_duplicate_the_country():
    # city_info for Singapore is {en_name: "Singapore", parent: {en_name: "Singapore"}} — the
    # city and country share a name, and the join must not repeat it.
    job = _jobs()["7665646635395188997"]
    assert job.location == "Singapore"


def test_regular_recruit_type_is_not_relabelled():
    job = _jobs()["7663386899855231237"]
    assert job.employment_type == "Regular"


def test_missing_title_or_id_drops_the_job():
    scraper = _scraper()
    raw = [
        {
            "id": "1",
            "title": "",
            "recruit_type": None,
            "job_category": None,
            "city_info": None,
        },
        {
            "id": None,
            "title": "Untitled but no id",
            "recruit_type": None,
            "job_category": None,
        },
        {
            "id": "2",
            "title": "Kept",
            "recruit_type": None,
            "job_category": None,
            "city_info": None,
        },
    ]
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert [j.id.rsplit(":", 1)[1] for j in jobs] == ["2"]


def test_missing_optional_fields_parse_to_none_not_an_exception():
    scraper = _scraper()
    raw = [
        {
            "id": "3",
            "title": "Bare-bones posting",
            "recruit_type": None,
            "job_category": None,
            "job_subject": None,
            "city_info": None,
            "description": None,
            "requirement": None,
        }
    ]
    jobs = scraper.parse(raw, SCRAPED_AT)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.location is None
    assert job.department is None
    assert job.employment_type is None
    assert job.description is None
    assert job.remote is None  # no location to judge from


# --------------------------------------------------------------------------- _location_of


def test_location_of_joins_the_parent_chain():
    city_info = {
        "en_name": "Los Angeles",
        "parent": {
            "en_name": "California",
            "parent": {"en_name": "United States of America", "parent": None},
        },
    }
    assert (
        _location_of(city_info) == "Los Angeles, California, United States of America"
    )


def test_location_of_drops_a_repeated_name():
    city_info = {
        "en_name": "Singapore",
        "parent": {"en_name": "Singapore", "parent": None},
    }
    assert _location_of(city_info) == "Singapore"


def test_location_of_none_is_none():
    assert _location_of(None) is None


# --------------------------------------------------------------------------- fetch_raw pagination


def test_normal_end_of_board_is_not_truncated(monkeypatch):
    # A legitimately finished board: two full pages (page size patched to 1 so a small fixture
    # can still exercise the "not yet at count" vs "reached count" branch) then a short one.
    import headstart.scrapers.tiktok as tiktok_module

    monkeypatch.setattr(tiktok_module, "_PAGE_SIZE", 1)
    pages = [
        {"code": 0, "data": {"job_post_list": [{"id": "1", "title": "A"}], "count": 2}},
        {"code": 0, "data": {"job_post_list": [{"id": "2", "title": "B"}], "count": 2}},
    ]
    scraper = get_scraper("tiktok", SLUG, "TikTok")
    monkeypatch.setattr(scraper, "_page", lambda offset: pages.pop(0))
    posts = scraper.fetch_raw()
    assert [p["id"] for p in posts] == ["1", "2"]
    assert scraper.truncated is None


def test_an_application_level_error_on_http_200_marks_truncated_not_the_end(
    monkeypatch,
):
    # Measured live 2026-09-12: a malformed request answers HTTP 200 with
    # {"code": -4000001, "data": null} — an error the transport layer's retry/raise never sees.
    # `data: null` would otherwise collapse to the same empty batch a finished board serves, so
    # this must be caught before it is read as "the board ended".
    import headstart.scrapers.tiktok as tiktok_module

    monkeypatch.setattr(tiktok_module, "_PAGE_SIZE", 1)
    scraper = get_scraper("tiktok", SLUG, "TikTok")
    pages = [
        {"code": 0, "data": {"job_post_list": [{"id": "1", "title": "A"}], "count": 5}},
        {"code": -4000001, "data": None},
    ]
    monkeypatch.setattr(scraper, "_page", lambda offset: pages.pop(0))
    posts = scraper.fetch_raw()
    # The one real posting read before the failure is kept — it is real, not absent.
    assert [p["id"] for p in posts] == ["1"]
    assert scraper.truncated is not None
    assert "code -4000001" in scraper.truncated
    assert "1 postings read so far" in scraper.truncated
