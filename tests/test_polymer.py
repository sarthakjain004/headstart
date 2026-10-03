"""Polymer public API captures from Cedar, 2026-10-03."""

import json
from pathlib import Path

from headstart.jobs import salary
from headstart.scrapers.registry import get_scraper

RAW = json.loads((Path(__file__).parent / "fixtures/polymer_cedar.json").read_text())


def test_public_detail_adds_description_without_losing_listing_salary():
    (job,) = get_scraper("polymer", "cedar").parse(RAW, "2026-10-03T00:00:00Z")
    assert job.id == "polymer:cedar:38850"
    assert job.company == "Cedar"
    assert job.title == "Senior Software Engineer"
    assert job.remote is False
    assert job.location == "San Diego, CA, US"
    assert job.department == "Software Development"
    assert job.posted_at == "2026-02-19T16:17:06.862Z"
    assert job.description.startswith("Help reinvent how housing gets designed")
    assert job.url == "https://jobs.polymer.co/cedar/38850"
    assert salary.from_field(job.salary, ats="polymer").min_annual == 170000


class Backend:
    def __init__(self, responses):
        self.responses = responses

    def fetch(self, method, url, **kwargs):
        from types import SimpleNamespace

        status, body = self.responses[url]

        def raise_for_status():
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

        return SimpleNamespace(
            status_code=status,
            text=json.dumps(body),
            json=lambda: body,
            raise_for_status=raise_for_status,
            headers={},
        )

    async def fetch_async(self, session, method, url, **kwargs):
        return self.fetch(method, url, **kwargs)


def test_fetch_keeps_a_listed_job_when_its_detail_is_rate_limited():
    url = "https://api.polymer.co/v1/hire/organizations/cedar/jobs"
    scraper = get_scraper(
        "polymer",
        "cedar",
        fetcher=Backend(
            {
                url: (
                    200,
                    {
                        "items": RAW["items"],
                        "meta": {
                            "count": 1,
                            "total": 1,
                            "page": 1,
                            "is_last": True,
                            "next_page": None,
                        },
                    },
                ),
                url + "/38850": (429, {}),
            }
        ),
    )
    (job,) = scraper.fetch()
    assert job.description is None
    assert job.salary == "170000.0-220000.0 USD per-year"
    assert scraper.detail_losses["HTTP 429"] == 1


def test_listing_shortfall_is_truncated_and_pagination_never_chases_past_total():
    url = "https://api.polymer.co/v1/hire/organizations/cedar/jobs"
    scraper = get_scraper(
        "polymer",
        "cedar",
        fetcher=Backend(
            {
                url: (
                    200,
                    {
                        "items": RAW["items"],
                        "meta": {
                            "count": 5,
                            "total": 2,
                            "page": 1,
                            "is_last": False,
                            "next_page": 2,
                        },
                    },
                ),
                url + "?page=2": (
                    200,
                    {
                        "items": [],
                        "meta": {
                            "count": 5,
                            "total": 2,
                            "page": 2,
                            "is_last": False,
                            "next_page": 3,
                        },
                    },
                ),
                url + "/38850": (200, RAW["details"]["38850"]),
            }
        ),
    )
    (job,) = scraper.fetch()
    assert job.description
    assert scraper.truncated


def test_every_stated_remote_country_survives_and_monthly_salary_is_annualized():
    raw = json.loads(
        (Path(__file__).parent / "fixtures/polymer_remote_salary.json").read_text()
    )
    jobs = get_scraper("polymer", "sample").parse(raw, "2026-10-03T00:00:00Z")
    assert "Canada" in jobs[0].location
    assert "United States" in jobs[0].location
    assert salary.from_field(jobs[1].salary, ats="polymer").min_annual == 14400


def test_unreadable_envelopes_fail_and_hourly_salary_is_not_treated_as_annual():
    from copy import deepcopy

    import pytest

    with pytest.raises(TypeError):
        get_scraper("polymer", "cedar").parse({"errors": ["unavailable"]}, "now")
    raw = deepcopy(RAW)
    raw["items"][0]["salary_pretty"] = "13.50 USD an hour"
    (job,) = get_scraper("polymer", "cedar").parse(raw, "now")
    assert salary.from_field(job.salary, ats="polymer").min_annual == 28080


def test_a_failed_later_page_keeps_its_prefix_but_excludes_it_from_complete_boards():
    url = "https://api.polymer.co/v1/hire/organizations/cedar/jobs"
    scraper = get_scraper(
        "polymer",
        "cedar",
        fetcher=Backend(
            {
                url: (
                    200,
                    {
                        "items": RAW["items"],
                        "meta": {
                            "count": 100,
                            "total": 2,
                            "page": 1,
                            "is_last": False,
                            "next_page": 2,
                        },
                    },
                ),
                url + "?page=2": (429, {}),
                url + "/38850": (200, RAW["details"]["38850"]),
            }
        ),
    )
    (job,) = scraper.fetch()
    assert job.title == "Senior Software Engineer"
    assert scraper.truncated
