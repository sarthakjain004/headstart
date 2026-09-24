"""Tests for `headstart.scrapers.tiktok`.

`tiktok_jobs.json` is four real postings from `api.lifeattiktok.com`'s public search endpoint,
captured 2026-09-11 (`docs/tiktok/2026-09-11_api-measurement.md`) — chosen to cover a US, two
Singapore and an Australia posting, an "Intern" and a "Regular" `recruit_type`, and a
`job_subject` that is present on some rows and absent on others. None of the four states any kind
of date, matching what was measured across the wider 100-row sample: this API has no posted-date
field at all, so `posted_at` is always None here (asserted below rather than assumed).

What TikTok shares with ByteDance — the walk, the envelope check, the parse rules — is tested once
per brand in `test_supplier_search.py` (ADR-0198); this file keeps TikTok's identity and its real
postings.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.models import html_to_text
from headstart.scrapers.registry import get_scraper
from headstart.scrapers.tiktok import TikTokScraper

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
    # Measured across a 100-row sample of the live API (`supplier_search`'s module docstring):
    # zero non-null date of any kind. Every fixture job must parse to None, not a fabricated value.
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
