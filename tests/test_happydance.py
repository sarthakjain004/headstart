"""Tests for the Happydance career-front scraper (headstart.scrapers.happydance).

The fixture is real captures from 2026-09-28: three fronts' sitemaps cut to a few job URLs (in
several locales) beside non-job entries, and each job page cut to what the scraper reads — the
`<title>`, the `JobIdentifier` meta tag, the `js-apply` anchors and the JSON-LD block, its
description shortened. `careers.cognizant.com` is the classic template with the type attribute
escaped; `careers.hilti.group` the classic template with a plain type and apply by class;
`careers.warburtons.co.uk` the Next.js template, whose sitemap is an index and whose sitemap
still lists a closed posting (8250).
"""

from __future__ import annotations

import json
import logging
import pathlib
import re

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.jobs.salary import from_field
from headstart.scrapers import front_duplication
from headstart.scrapers.front_duplication import ScrapableBoardIndex
from headstart.scrapers.happydance import HappydanceScraper, sitemap_rows
from headstart.scrapers.pacer import Pacer
from headstart.scrapers.registry import get_scraper

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "happydance_fronts.json").read_text()
)
_COGNIZANT, _HILTI, _WARBURTONS = (
    _FIXTURE["cognizant"],
    _FIXTURE["hilti"],
    _FIXTURE["warburtons"],
)
_SCRAPED_AT = "2026-09-28T00:00:00+00:00"
_HELD = ScrapableBoardIndex(frozenset({"avature:hilticareers"}))


@pytest.fixture(autouse=True)
def _held_boards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(front_duplication, "scrapable_boards", lambda: _HELD)


def _router(front: dict, **pages: FakeResponse):
    """Answers the front's sitemap, its index children and its job pages by req."""

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url == f"https://{front['host']}/sitemap.xml":
            return FakeResponse(content=b"\xef\xbb\xbf" + front["sitemap_xml"].encode())
        if url in front.get("children", {}):
            return FakeResponse(text=front["children"][url])
        req = re.search(r"/([^/]*\d[^/]*)/[^/]+/?$", url).group(1)
        if req in pages:
            return pages[req]
        return FakeResponse(text=front["pages"][req])

    return route


def _scraper(front: dict, **pages: FakeResponse) -> HappydanceScraper:
    scraper = get_scraper(
        "happydance", front["host"], fetcher=FakeFetcher(_router(front, **pages))
    )
    scraper.pacer = Pacer(0)  # the real one spaces requests half a second apart
    return scraper


# --- the listing ---------------------------------------------------------------------------


def test_sitemap_rows_keep_one_url_per_req_across_locales_and_skip_content_pages() -> (
    None
):
    rows = sitemap_rows(_COGNIZANT["sitemap_xml"], _COGNIZANT["host"])
    assert rows == [
        (
            "00064794373",
            "https://careers.cognizant.com/us-en/jobs/00064794373/databricks-with-aws-pyspark/",
        ),
        (
            "00070210791",
            (
                "https://careers.cognizant.com/us-en/jobs/00070210791/"
                "client-relationship-manager-crm-hyperscaler-data-center-ai-infrastructure/"
            ),
        ),
    ]


@pytest.mark.parametrize(
    "loc",
    [
        # Real job URLs: a localised word, 0-2 prefix segments, a req with a digit.
        "https://careers.caterpillar.com/zh/职位/r0000269418/monteur-motorenmontage-mwd/",
        "https://jobs.centene.com/us/en/jobs/1641747/vice-president-clinical-technology/",
        "https://careers.rjet.com/jobs/jr-007731/material-handler/",
        "https://careers.hilti.group/fr/offres-d-emploi/13139-de/business-partner-finance/",
    ],
)
def test_sitemap_rows_read_every_measured_job_url_shape(loc: str) -> None:
    host = loc.split("/")[2]
    assert len(sitemap_rows(f"<urlset><url><loc>{loc}</loc></url></urlset>", host)) == 1


@pytest.mark.parametrize(
    "loc",
    [
        "https://careers.cognizant.com/global-en/blog/life-at-cognizant/",
        "https://jobs.assurant.com/en/blog/authors/fiona-desenberg/",
        "https://careers.cognizant.com/us-en/jobs/",
        # Another host's job URL is another Board's.
        "https://careers.caterpillar.com/en/jobs/r0000269418/monteur-motorenmontage-mwd/",
    ],
)
def test_sitemap_rows_skip_what_is_not_this_fronts_job(loc: str) -> None:
    assert (
        sitemap_rows(
            f"<urlset><url><loc>{loc}</loc></url></urlset>", "careers.cognizant.com"
        )
        == []
    )


# --- the Jobs ------------------------------------------------------------------------------


def test_a_classic_front_with_an_escaped_jsonld_type_is_read_whole() -> None:
    jobs = {job.id: job for job in _scraper(_COGNIZANT).fetch()}

    assert set(jobs) == {
        "happydance:careers.cognizant.com:00064794373",
        "happydance:careers.cognizant.com:00070210791",
    }
    job = jobs["happydance:careers.cognizant.com:00064794373"]
    assert job.title == "Databricks with AWS & Pyspark"
    assert job.company == "Cognizant"
    assert job.location == "Hyderabad, Telangana, India"
    assert job.department == "Technology & Engineering"
    assert job.posted_at == "2026-01-08"
    assert job.employment_type == "FULL_TIME"
    assert job.remote is False
    assert job.description.startswith("Job Title: - Databricks with AWS & Pyspark")
    assert job.url == (
        "https://careers.cognizant.com/us-en/jobs/00064794373/databricks-with-aws-pyspark/"
    )
    assert re.fullmatch(HappydanceScraper.url_shape, job.url)


def test_a_classic_front_with_a_plain_type_reads_its_list_fields() -> None:
    jobs = {job.id: job for job in _scraper(_HILTI).fetch()}

    job = jobs["happydance:careers.hilti.group:13139-de"]
    assert job.title.startswith("Business Partner Finance")
    assert job.company == "Hilti Corporation"
    assert job.department == "Fertigung"
    assert job.location == "Jena, Thüringen, Deutschland"
    assert job.posted_at == "2025-11-24"
    assert "&#" not in job.description


def test_a_nextjs_front_reads_its_sitemap_index_and_skips_a_closed_posting() -> None:
    scraper = _scraper(_WARBURTONS)
    jobs = scraper.fetch()

    assert [job.id for job in jobs] == ["happydance:careers.warburtons.co.uk:7880"]
    job = jobs[0]
    assert job.title == "Health, Safety & Environment Manager"
    assert from_field(job.salary, "happydance").min_annual == 64000
    # 8250's page is the site's shell with no posting: closed, not lost (ADR-0053).
    assert not scraper.truncated


def test_a_job_page_without_its_posting_is_lost_and_marks_the_board_short() -> None:
    shell = '<meta id="js-job-identifier" name="JobIdentifier" content="00070210791" />'
    scraper = _scraper(_COGNIZANT, **{"00070210791": FakeResponse(text=shell)})
    jobs = scraper.fetch()

    assert len(jobs) == 1
    assert scraper.truncated
    assert scraper.detail_losses["job page without JobPosting JSON-LD"] == 1


def test_a_refused_job_page_marks_the_board_short() -> None:
    walled = FakeResponse(429, "<title>Just a moment...</title>")
    scraper = _scraper(_COGNIZANT, **{"00070210791": walled})
    assert len(scraper.fetch()) == 1
    assert scraper.truncated


def test_a_sitemap_with_no_job_url_reads_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    empty = {
        **_COGNIZANT,
        "sitemap_xml": "<urlset><url><loc>https://careers.cognizant.com/us-en/</loc></url></urlset>",
    }
    with caplog.at_level(logging.INFO):
        assert _scraper(empty).fetch() == []
    assert "Happydance job URLs in the sitemap" in caplog.text


# --- fields --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "salary"),
    [
        (
            {
                "currency": "GBP",
                "value": {"minValue": 64000, "maxValue": 72000, "unitText": "YEAR"},
            },
            "64000-72000 GBP yearly",
        ),
        (
            {"currency": "USD", "value": {"minValue": 25, "unitText": "HOUR"}},
            "25 USD hourly",
        ),
        # coupa: a name and no amount states no salary.
        (
            {"name": "The successful candidate's starting salary will be determined"},
            None,
        ),
        # A unit never measured is not guessed at.
        ({"currency": "USD", "value": {"minValue": 900, "unitText": "WEEK"}}, None),
    ],
)
def test_base_salary_is_spelt_for_the_field_reader(
    node: dict, salary: str | None
) -> None:
    scraper = get_scraper("happydance", "careers.cognizant.com")
    assert scraper._salary_field(node) == salary


# --- Front duplication and pacing ----------------------------------------------------------


def test_front_duplication_is_logged_and_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scraper = _scraper(_HILTI)
    with caplog.at_level(logging.INFO):
        jobs = scraper.fetch()

    assert len(jobs) == 2  # every posting is kept: duplication is measured, never gated
    assert scraper.telemetry["front_postings"] == 2
    assert scraper.telemetry["front_duplicated"] == 2
    assert (
        "happydance:careers.hilti.group: Front duplication at least 2/2 postings apply on a "
        "Scrapable Board (avature:hilticareers 2)"
    ) in caplog.text


def test_every_request_waits_on_the_shared_pacer() -> None:
    class CountingPacer(Pacer):
        waits = 0

        def wait(self) -> None:
            CountingPacer.waits += 1

        async def wait_async(self) -> None:
            CountingPacer.waits += 1

    fetcher = FakeFetcher(_router(_WARBURTONS))
    scraper = get_scraper("happydance", _WARBURTONS["host"], fetcher=fetcher)
    scraper.pacer = CountingPacer(0)
    scraper.fetch()
    # The index, its two children and two job pages.
    assert CountingPacer.waits == len(fetcher.requests) == 5
