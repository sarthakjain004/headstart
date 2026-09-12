"""Tests for `headstart.scrapers.uber`.

`uber_listing.json` is 8 real postings, captured 2026-09-11 from a full 517-job sweep of
`GET https://jobs.uber.com/api/jobs/search/`, curated (not the first N) to cover the edge cases
the measurement found: an Intern posting whose `ContractType` and `WorkPattern` disagree (300864),
a blank `ContractType` (300286), a job with no `Teams` and a blank `WorkPattern` (301178), and a
multi-location posting (302269). The measurements behind every assertion are in
`docs/uber/2026-09-11_api-measurement.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.registry import (
    DISABLED_ATS,
    SCRAPERS,
    detail_pass_atses,
    get_scraper,
)
from headstart.scrapers.uber import UberScraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "jobs.uber.com"


def _listing() -> dict:
    with open(FIXTURES / "uber_listing.json", encoding="utf-8") as fh:
        return json.load(fh)


def _scraper() -> UberScraper:
    # Passing a company name on purpose: it must be ignored (Single source scraper, no
    # per-tenant name resolution needed or wanted).
    return get_scraper("uber", SLUG, "not uber")


def _jobs() -> dict:
    return {
        j.id.rsplit(":", 1)[1]: j
        for j in _scraper().parse(_listing()["jobs"], SCRAPED_AT)
    }


# --------------------------------------------------------------------------- the URL contract


def test_the_listing_url_is_the_search_api_with_a_page_size():
    url = _scraper().url()
    assert url.startswith("https://jobs.uber.com/api/jobs/search/?")
    assert "page=1" in url
    assert "pagesize=" in url


def test_the_job_url_is_the_default_entry_resolved_against_the_jobs_origin():
    job = _jobs()["301347"]
    assert job.url == "https://jobs.uber.com/en/jobs/301347/"


# ------------------------------------------------------------------------------ identity is fixed


def test_the_company_is_always_uber_regardless_of_what_is_passed():
    """There is exactly one board and its name is always known — unlike every multi-tenant ATS,
    nothing here needs `resolve_company`'s slug-guessing."""
    assert _scraper().company == "Uber"
    assert UberScraper(SLUG, "Uber Technologies Inc").company == "Uber"
    assert UberScraper(SLUG).company == "Uber"


def test_the_board_key_is_ats_colon_slug():
    assert _scraper().board_key() == f"uber:{SLUG}"


def test_alias_key_resolves_to_its_own_slug():
    """No sibling board exists to alias against (ADR-0139) — the base default's
    redirect-following would make a live network call for no reason here."""
    assert _scraper().alias_key() == SLUG


# ------------------------------------------------------------------------------ field mapping


def test_the_core_identity_fields_come_from_the_listing():
    job = _jobs()["301347"]
    assert job.id == f"uber:{SLUG}:301347"
    assert job.ats == "uber"
    assert job.company == "Uber"
    assert job.title == "Sr Software Engineer - Web - AV Labs"
    assert job.posted_at == "2026-09-11T16:55:11Z"
    assert job.description and "<" not in job.description  # HTML stripped


def test_location_joins_city_region_country_of_the_first_entry():
    job = _jobs()["302269"]  # multi-location: Brussels first
    assert job.location == "Bruxelles, Belgium"


def test_department_is_the_first_team():
    assert _jobs()["301347"].department == "Engineer"
    assert _jobs()["302269"].department == "Sales"


def test_department_is_none_when_no_team_is_stated():
    assert _jobs()["301178"].department is None


def test_remote_is_read_as_the_stated_boolean():
    """A real, always-present field (module docstring) — not overridden by a location-text
    guess even where it reads False for every sampled posting."""
    assert _jobs()["301347"].remote is False


def test_employment_type_joins_contract_type_and_work_pattern_when_they_disagree():
    """The measured case this exists for: an Intern posting states `ContractType="Full time"`
    (hours) and `WorkPattern="Intern"` (arrangement) at once. Preferring either alone would drop
    the other's signal."""
    assert _jobs()["300864"].employment_type == "Full time / Intern"


def test_employment_type_falls_back_to_whichever_field_is_blank():
    # ContractType is "" on 300286; WorkPattern ("Regular") is all there is.
    assert _jobs()["300286"].employment_type == "Regular"
    # WorkPattern is "" on 301178; ContractType ("Full time") is all there is.
    assert _jobs()["301178"].employment_type == "Full time"


def test_employment_type_is_not_duplicated_when_both_fields_agree():
    (job,) = _scraper().parse(
        [
            {
                "Id": "1",
                "Title": "X",
                "ContractType": "Regular",
                "WorkPattern": "Regular",
                "Locations": [],
                "Urls": [],
                "Teams": [],
            }
        ],
        SCRAPED_AT,
    )
    assert job.employment_type == "Regular"


def test_a_job_missing_an_id_or_title_is_dropped():
    jobs = _scraper().parse(
        [
            {"Id": "", "Title": "X"},
            {"Id": "1", "Title": ""},
            {"Id": "1", "Title": "  ", "Locations": [], "Urls": [], "Teams": []},
        ],
        SCRAPED_AT,
    )
    assert jobs == []


def test_a_job_with_no_urls_falls_back_to_the_jobs_origin():
    (job,) = _scraper().parse(
        [{"Id": "1", "Title": "X", "Locations": [], "Urls": [], "Teams": []}],
        SCRAPED_AT,
    )
    assert job.url == "https://jobs.uber.com"


# -------------------------------------------------------------------------------- pagination


class _FakeSearch:
    """Serves pages from a canned job list, recording every `page` requested — mirrors the
    style `test_oracle.py::_FakeListing` uses for the same purpose."""

    def __init__(self, total, page_size, reported_total=None):
        self.jobs = [{"Id": str(i), "Title": f"job {i}"} for i in range(total)]
        self.page_size = page_size
        self.reported_total = total if reported_total is None else reported_total
        self.pages_asked = []

    def __call__(self, url):
        page = int(url.split("page=")[1].split("&")[0])
        self.pages_asked.append(page)
        start = (page - 1) * self.page_size
        batch = self.jobs[start : start + self.page_size]
        return json.dumps({"jobs": batch, "totalJobs": self.reported_total})


def _paged(monkeypatch, fake):
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_get", fake)
    return scraper


def test_pagination_walks_every_page_until_the_total_is_met(monkeypatch):
    fake = _FakeSearch(total=450, page_size=200)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 450
    assert fake.pages_asked == [1, 2, 3]
    assert scraper.truncated is None


def test_a_page_past_the_end_ends_the_walk_on_an_empty_batch(monkeypatch):
    """Measured live: `page=999` answers `{"jobs": [], "totalJobs": 517, ...}` — an empty batch
    with the real total still echoed, not a blanked envelope (contrast Oracle's offset ceiling)."""
    fake = _FakeSearch(total=10, page_size=200)
    scraper = _paged(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert len(raw) == 10
    assert scraper.truncated is None


def test_a_board_short_of_its_own_total_is_marked_truncated(monkeypatch):
    fake = _FakeSearch(total=150, page_size=200, reported_total=900)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated
    assert "150 of 900" in scraper.truncated


def test_hitting_the_page_cap_marks_truncated(monkeypatch):
    fake = _FakeSearch(total=10**6, page_size=1)
    scraper = _paged(monkeypatch, fake)
    scraper.fetch_raw()
    assert scraper.truncated
    assert "page cap" in scraper.truncated


def test_duplicate_ids_across_pages_are_not_double_counted(monkeypatch):
    def served(url):
        page = int(url.split("page=")[1].split("&")[0])
        if page == 1:
            return json.dumps({"jobs": [{"Id": "1", "Title": "X"}], "totalJobs": 1})
        return json.dumps({"jobs": [], "totalJobs": 1})

    scraper = _paged(monkeypatch, served)
    raw = scraper.fetch_raw()
    assert len(raw) == 1


# ------------------------------------------------------------------------- registry wiring


def test_registered_in_the_scraper_registry():
    assert SCRAPERS["uber"] is UberScraper


def test_not_disabled_and_has_no_detail_pass():
    """Ships live-wired, not arrival-disabled (ADR-0139's `DISABLED_ATS` consequence): unlike
    jazzhr/jobvite, cost and tech yield are unknown per Single source scraper until measured, so
    it is judged on its own numbers rather than pre-gated. And the listing carries the full
    description already (module docstring: 3/3 sampled matched the detail-page JSON-LD), so no
    detail pass exists to register."""
    assert "uber" not in DISABLED_ATS
    assert UberScraper.has_detail_pass is False
    assert "uber" not in detail_pass_atses()
