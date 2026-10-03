"""Measured legacy Manatal Jobs and advanced JobPosts, captured 2026-10-03."""

import json
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.manatal import ManatalScraper
from headstart.scrapers.pacer import Pacer

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-10-03T00:00:00+00:00"


@pytest.fixture(autouse=True)
def no_network_wait(monkeypatch):
    monkeypatch.setattr(ManatalScraper, "api_pacer", Pacer(0), raising=False)
    monkeypatch.setattr(ManatalScraper, "html_pacer", Pacer(0), raising=False)


def legacy():
    return json.loads((FIXTURES / "manatal_legacy.json").read_text())


def test_legacy_public_api_preserves_native_id_and_public_hash_without_inventing_date():
    raw = {"rows": legacy()["results"], "organization_is_department": True}
    jobs = ManatalScraper("manatal", "Manatal Co LTD").parse(raw, SCRAPED_AT)
    assert len(jobs) == 2
    first = jobs[0]
    assert first.id == "manatal:manatal:1333846"
    assert first.url == "https://www.careers-page.com/manatal/job/L8597V4V"
    assert first.company == "Manatal Co LTD"
    assert first.department == "Customer Success"
    assert (
        first.location == "Bangkok, Bangkok, Thailand"
    )  # city and province as published
    assert first.employment_type == "Full-time"
    assert first.description and "<p" not in first.description
    assert first.posted_at is None
    assert first.salary is None
    assert first.experience is None


def test_legacy_fetch_follows_all_pages_on_the_known_api_host():
    pages = json.loads((FIXTURES / "manatal_24mag_pages.json").read_text())

    def route(_method, url, kwargs):
        if "api.manatal.com" in url:
            return FakeResponse(text=json.dumps(pages[kwargs["params"]["page"] - 1]))
        return FakeResponse(
            text='<title> - 24-Mag | Career Page</title>const organization_singular_name = "client";'
        )

    scraper = ManatalScraper("24-mag", fetcher=FakeFetcher(route))
    raw = scraper.fetch_raw()
    assert len(raw["rows"]) == 344
    assert len({j["id"] for j in raw["rows"]}) == 344
    assert raw["organization_is_department"] is False
    assert scraper.company == "24-Mag"
    assert scraper.truncated is None


def test_advanced_and_legacy_urls_keep_different_board_identities():
    assert (
        ManatalScraper.slug_from(
            "x", "https://www.careers-page.com/manatal/job/L8597V4V"
        )
        == "manatal"
    )
    assert (
        ManatalScraper.slug_from(
            "x",
            "https://manatal.careers-page.com/jobs/8be30254-7422-4ae0-9aa2-3ea3c0453c0a",
        )
        == "manatal.careers-page.com"
    )


def test_a_later_legacy_page_failure_preserves_rows_but_marks_truncation():
    first = json.loads((FIXTURES / "manatal_24mag_pages.json").read_text())[0]

    def route(_method, url, kwargs):
        if "api.manatal.com" not in url:
            return FakeResponse(text="<title> - 24-Mag | Career Page</title>")
        if kwargs["params"]["page"] == 1:
            return FakeResponse(text=json.dumps(first))
        return FakeResponse(429, "slow down")

    scraper = ManatalScraper("24-mag", fetcher=FakeFetcher(route))
    assert len(scraper.fetch_raw()["rows"]) == 100
    assert scraper.truncated


def test_advanced_fetch_reads_both_pages_and_the_native_jobpost_detail():
    post_id = "8be30254-7422-4ae0-9aa2-3ea3c0453c0a"

    def route(_method, url, _kwargs):
        if "/jobs/" in url:
            return (
                FakeResponse(
                    text=(FIXTURES / "manatal_advanced_detail.html").read_text()
                )
                if post_id in url
                else FakeResponse(503, "unavailable")
            )
        name = (
            "manatal_advanced_page2.html"
            if "page=2" in url
            else "manatal_advanced_page1.html"
        )
        return FakeResponse(text=(FIXTURES / name).read_text())

    scraper = ManatalScraper("manatal.careers-page.com", fetcher=FakeFetcher(route))
    jobs = scraper.fetch()
    assert len(jobs) == 40
    engineer = next(j for j in jobs if j.id.endswith(post_id))
    assert engineer.title == "Senior Platform Engineer"
    assert engineer.company == "Manatal Co LTD"
    assert engineer.department == "Engineering"
    assert engineer.employment_type == "Full-Time"
    assert engineer.remote is False
    assert engineer.description and len(engineer.description) > 1000
    assert engineer.url == f"https://manatal.careers-page.com/jobs/{post_id}"
    assert scraper.truncated is None


def test_advanced_refusal_after_page_one_is_partial_instead_of_complete():
    def route(_method, url, _kwargs):
        if "page=2" in url or "/jobs/" in url:
            return FakeResponse(429, "challenge")
        return FakeResponse(text=(FIXTURES / "manatal_advanced_page1.html").read_text())

    scraper = ManatalScraper("manatal.careers-page.com", fetcher=FakeFetcher(route))
    assert len(scraper.fetch_raw()["rows"]) == 30
    assert scraper.truncated


def test_optional_company_page_failure_keeps_the_complete_public_jobs():
    body = legacy()
    body["count"], body["next"] = len(body["results"]), None

    def route(_method, url, _kwargs):
        return (
            FakeResponse(text=json.dumps(body))
            if "api.manatal.com" in url
            else FakeResponse(503, "unavailable")
        )

    scraper = ManatalScraper("manatal", fetcher=FakeFetcher(route))
    jobs = scraper.fetch()
    assert len(jobs) == 2
    assert all(j.department is None for j in jobs)
    assert all(j.description for j in jobs)
    assert scraper.truncated is None
