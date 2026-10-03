"""Instahyre public marketplace responses, captured 2026-10-03."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fake_fetcher import FakeFetcher, FakeResponse

from headstart.ingest.doc_prep import to_meta
from headstart.network import http
from headstart.scrapers.instahyre import InstahyreScraper
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-10-03T00:00:00+00:00"


def test_instahyre_keeps_the_marketplace_employer_profile_and_public_job_url():
    raw = json.loads((FIXTURES / "instahyre_geoserve.json").read_text())

    jobs = get_scraper("instahyre", "global").parse(raw, SCRAPED_AT)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "instahyre:global:352689"
    assert job.company == "GeoServe"
    assert job.marketplace_employer_id == "48297"
    assert job.title == "Full Stack Engineer"
    assert job.location == "Bangalore"
    assert job.remote is False
    assert (
        job.url
        == "https://www.instahyre.com/job-352689-full-stack-engineer-at-geoserve-bangalore/"
    )
    assert re.fullmatch(get_scraper("instahyre", "global").url_shape, job.url)
    assert job.posted_at is None
    assert job.experience == "4-8 years"
    assert job.employment_type == "Full-time"
    assert job.salary is None
    assert "Designing, developing" in (job.description or "")


def test_instahyre_declares_the_marketplace_source_kind():
    scraper = get_scraper("instahyre", "global")
    assert scraper.source_kind == "marketplace"
    assert scraper.async_fanout is False
    assert scraper.detail_workers == 64
    assert scraper.egress_fallback_on == frozenset({429})


def test_instahyre_marks_its_work_from_home_location_as_remote():
    raw = json.loads((FIXTURES / "instahyre_geoserve.json").read_text())
    raw["objects"][0]["locations"] = "Work From Home"
    raw["details"]["352689"]["locations"] = ["Work From Home"]

    job = get_scraper("instahyre", "global").parse(raw, SCRAPED_AT)[0]

    assert job.remote is True


def test_instahyre_keeps_employment_type_unknown_when_the_detail_is_missing():
    raw = json.loads((FIXTURES / "instahyre_geoserve.json").read_text())
    raw["details"] = {}

    job = get_scraper("instahyre", "global").parse(raw, SCRAPED_AT)[0]

    assert job.employment_type is None


def test_instahyre_employer_profile_id_reaches_embedding_metadata():
    raw = json.loads((FIXTURES / "instahyre_geoserve.json").read_text())
    job = get_scraper("instahyre", "global").parse(raw, SCRAPED_AT)[0]

    assert to_meta(job.to_dict())["marketplace_employer_id"] == "48297"


def test_instahyre_walks_the_global_feed_and_keeps_listing_membership_over_detail_activity():
    first = {
        "objects": [
            {
                "id": 1,
                "title": "Backend Engineer",
                "public_url": "https://www.instahyre.com/job-1-backend-engineer/",
                "locations": "Bangalore",
                "employer": {"id": 7, "company_name": "Acme"},
            }
        ],
        "meta": {"total_count": 70, "next": "/api/v1/job_search?limit=35&offset=35"},
    }
    second = {
        "objects": [
            {
                "id": 2,
                "title": "Closed Engineer",
                "public_url": "https://www.instahyre.com/job-2-closed-engineer/",
                "locations": "Pune",
                "employer": {"id": 8, "company_name": "Gone Co"},
            }
        ],
        "meta": {"total_count": 70, "next": None},
    }

    second_listing_attempts = 0

    def route(_method, url, _kwargs):
        nonlocal second_listing_attempts
        if url == "https://www.instahyre.com/api/v1/job_search?limit=35&offset=35":
            second_listing_attempts += 1
            if second_listing_attempts == 1:
                return http.RequestsError("temporary listing failure")
        responses = {
            "https://www.instahyre.com/api/v1/job_search?limit=35&offset=0": first,
            "https://www.instahyre.com/api/v1/job_search?limit=35&offset=35": second,
            "https://www.instahyre.com/api/v1/employer_public_jobs/1": {
                "id": 1,
                "is_active": True,
                "is_internship": False,
                "locations": ["Bangalore"],
                "description": "<p>Build systems.</p>",
                "workex_min": 3,
                "workex_max": 5,
            },
            "https://www.instahyre.com/api/v1/employer_public_jobs/2": {
                "id": 2,
                "is_active": False,
                "is_internship": False,
                "locations": ["Pune"],
                "description": "<p>Already closed.</p>",
                "workex_min": 2,
                "workex_max": 4,
            },
        }
        return FakeResponse(text=json.dumps(responses[url]))

    fetcher = FakeFetcher(route)
    jobs = get_scraper("instahyre", "global", fetcher=fetcher).fetch()

    assert [job.id for job in jobs] == ["instahyre:global:1", "instahyre:global:2"]
    assert jobs[0].marketplace_employer_id == "7"
    assert second_listing_attempts == 2
    assert fetcher.urls() == [
        "https://www.instahyre.com/api/v1/job_search?limit=35&offset=0",
        "https://www.instahyre.com/api/v1/job_search?limit=35&offset=35",
        "https://www.instahyre.com/api/v1/job_search?limit=35&offset=35",
        "https://www.instahyre.com/api/v1/employer_public_jobs/1",
        "https://www.instahyre.com/api/v1/employer_public_jobs/2",
    ]


def test_instahyre_unions_function_slices_after_the_global_listing_cap():
    class SmallCapInstahyre(InstahyreScraper):
        _GLOBAL_OFFSET_CAP = 1
        _LISTING_PAGE_SIZE = 1
        _LISTING_WORKERS = 1

        def url(self) -> str:
            return "https://www.instahyre.com/api/v1/job_search?limit=1&offset=0"

    def row(job_id):
        return {
            "id": job_id,
            "title": f"Engineer {job_id}",
            "public_url": f"https://www.instahyre.com/job-{job_id}-engineer/",
            "locations": "Bangalore",
            "employer": {"id": job_id, "company_name": f"Company {job_id}"},
        }

    def route(_method, url, _kwargs):
        responses = {
            "https://www.instahyre.com/api/v1/job_search?limit=1&offset=0": {
                "objects": [row(1)],
                "meta": {
                    "total_count": 2,
                    "next": "/api/v1/job_search?limit=1&offset=1",
                },
            },
            "https://www.instahyre.com/api/v1/job_function/": {
                "objects": [{"id": 10}],
                "meta": {},
            },
            "https://www.instahyre.com/api/v1/job_search?limit=1&offset=0&job_functions=10": {
                "objects": [row(2)],
                "meta": {"total_count": 1, "next": None},
            },
            "https://www.instahyre.com/api/v1/employer_public_jobs/1": {
                "id": 1,
                "is_internship": False,
                "locations": ["Bangalore"],
                "description": "<p>One</p>",
                "workex_min": 1,
                "workex_max": 2,
            },
            "https://www.instahyre.com/api/v1/employer_public_jobs/2": {
                "id": 2,
                "is_internship": False,
                "locations": ["Bangalore"],
                "description": "<p>Two</p>",
                "workex_min": 1,
                "workex_max": 2,
            },
        }
        return FakeResponse(text=json.dumps(responses[url]))

    jobs = SmallCapInstahyre("global", fetcher=FakeFetcher(route)).fetch()

    assert [job.id for job in jobs] == ["instahyre:global:1", "instahyre:global:2"]
