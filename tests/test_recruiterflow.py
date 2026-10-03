"""Recruiterflow public career-page data captured 2026-10-03.

Listing includes all eight rfcareers Jobs and the provider's duplicate grouping
views. Detail166 retains the fields read by the scraper, excluding application forms.
"""

import json
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http
from headstart.scrapers.base import BoardUnreadable
from headstart.scrapers.pacer import Pacer
from headstart.scrapers.recruiterflow import RecruiterflowScraper, listed_jobs

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-10-03T00:00:00+00:00"


@pytest.fixture(autouse=True)
def no_network_wait(monkeypatch):
    monkeypatch.setattr(RecruiterflowScraper, "pacer", Pacer(0))


def load(name):
    return json.loads((FIXTURES / f"recruiterflow_rfcareers_{name}.json").read_text())


def test_grouped_listing_emits_each_job_once_with_every_location():
    scraper = RecruiterflowScraper("rfcareers", "Recruiterflow")
    jobs = scraper.parse({"listing": load("listing"), "details": {}}, SCRAPED_AT)
    assert len(jobs) == 8
    by_id = {job.id.rsplit(":", 1)[1]: job for job in jobs}
    consultant = by_id["161"]
    assert consultant.id == "recruiterflow:rfcareers:161"
    assert consultant.title == "Product Consultant"
    assert consultant.department == "Customer Success"
    assert set(consultant.location.split("; ")) == {"Bengaluru", "Gurgaon", "New Delhi"}
    assert consultant.url == "https://recruiterflow.com/rfcareers/jobs/161"
    assert consultant.description is None
    assert consultant.employment_type == "Full time"
    assert consultant.posted_at == "2026-09-27T07:18:36+00:00"
    assert consultant.scraped_at == SCRAPED_AT


def test_detail_adds_description_and_the_displayed_experience_range():
    raw = {"listing": load("listing"), "details": {"166": load("detail")}}
    jobs = RecruiterflowScraper("rfcareers", "Recruiterflow").parse(raw, SCRAPED_AT)
    engineer = next(j for j in jobs if j.title == "Backend Engineer")
    assert engineer.department == "Engineering"
    assert "Strong proficiency in Python" in engineer.description
    assert "<div" not in engineer.description
    # The public page displays 2–8 above prose which inconsistently says 5–9.
    assert engineer.experience == "2-8 years"
    assert engineer.salary is None


def test_fetch_reads_public_page_data_and_preserves_jobs_when_details_fail():
    def route(_method, url, _kwargs):
        if url == "https://recruiterflow.com/rfcareers/jobs":
            return FakeResponse(
                text=(
                    '<meta property="og:title" content="Recruiterflow is hiring! Apply now.">'
                    "<script>window.jobsList = "
                    + json.dumps(load("listing"))
                    + ";</script>"
                )
            )
        if url.endswith("/166"):
            return FakeResponse(
                text="var convertedToJSON = " + json.dumps(load("detail")) + ";"
            )
        return FakeResponse(503, "temporarily unavailable")

    scraper = RecruiterflowScraper("rfcareers", fetcher=FakeFetcher(route))
    jobs = scraper.fetch()
    assert len(jobs) == 8
    assert all(j.company == "Recruiterflow" for j in jobs)
    engineer = next(j for j in jobs if j.title == "Backend Engineer")
    assert engineer.description and engineer.experience == "2-8 years"
    assert scraper.detail_losses["HTTP 503"] == 7
    assert scraper.truncated is None  # failed details do not hide the known listing


@pytest.mark.parametrize(
    ("tenant", "url"),
    [
        ("RFCAREERS", ""),
        ("old-spelling", "https://recruiterflow.com/RFCAREERS/jobs/166?source=x"),
        ("https://recruiterflow.com/rfcareers/jobs?department=Engineering", ""),
    ],
)
def test_slug_uses_the_public_board_path_and_discards_job_and_filter_coordinates(
    tenant, url
):
    assert RecruiterflowScraper.slug_from(tenant, url) == "rfcareers"


def test_missing_page_data_does_not_confirm_an_empty_board():
    scraper = RecruiterflowScraper(
        "rfcareers", fetcher=FakeFetcher(lambda *_: FakeResponse(text="maintenance"))
    )
    with pytest.raises(BoardUnreadable):
        scraper.fetch_raw()


def test_an_experience_ceiling_without_a_floor_stays_unknown():
    detail = {**load("detail"), "experience_range_start": "", "experience_range_end": 3}
    raw = {"listing": load("listing"), "details": {"166": detail}}
    jobs = RecruiterflowScraper("rfcareers").parse(raw, SCRAPED_AT)
    assert next(j for j in jobs if j.title == "Backend Engineer").experience is None


def test_real_empty_board_is_successful_without_detail_requests():
    page = (FIXTURES / "recruiterflow_empty.html").read_text()
    fetcher = FakeFetcher(lambda *_: FakeResponse(text=page))
    scraper = RecruiterflowScraper("cyrten", fetcher=fetcher)
    assert scraper.fetch() == []
    assert len(fetcher.requests) == 1
    assert scraper.truncated is None


def test_real_inactive_template_is_gone_instead_of_a_confirmed_empty_board():
    page = (FIXTURES / "recruiterflow_inactive.html").read_text()
    scraper = RecruiterflowScraper(
        "nearshorebusinesssolutions",
        fetcher=FakeFetcher(lambda *_: FakeResponse(text=page)),
    )
    with pytest.raises(http.RequestsError, match="gone"):
        scraper.fetch_raw()


def test_missing_department_view_cannot_erase_jobs_still_present_in_other_views():
    raw = load("listing")
    raw["department"] = []
    with pytest.raises(BoardUnreadable):
        listed_jobs(raw)


def test_detail_company_name_resolves_a_board_with_no_name_meta():
    def route(_method, url, _kwargs):
        if url.endswith("/jobs"):
            return FakeResponse(
                text="window.jobsList = " + json.dumps(load("listing")) + ";"
            )
        if url.endswith("/166"):
            return FakeResponse(
                text="var convertedToJSON = " + json.dumps(load("detail")) + ";"
            )
        return FakeResponse(503, "unavailable")

    jobs = RecruiterflowScraper("rfcareers", fetcher=FakeFetcher(route)).fetch()
    assert {j.company for j in jobs} == {"Recruiterflow"}
