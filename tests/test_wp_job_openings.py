"""Tests for the WP Job Openings scraper (headstart.scrapers.wp_job_openings).

The fixtures are real captures (2026-09-28): `aspiringit.com` (WordPress 6.5 or later, so its
REST rows carry `class_list`) and `www.heptarc.com` (older, so they do not). Each holds REST rows
cut to the fields the scraper asks for, bodies cut to 1,200 characters, and each posting's page
cut to what the scraper reads: the plugin's JSON-LD block and its specifications block.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.base import BoardUnreadable
from headstart.scrapers.registry import get_scraper

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / f"wp_job_openings_{name}.json").read_text())


def _route(fixture: dict[str, Any], *, per_page: int = 100):
    """Answer the REST route with the fixture's rows, `per_page` a page, and each link with
    its page."""
    rows = fixture["rows"]
    pages = {row["link"]: fixture["pages"][str(row["id"])] for row in rows}
    total_pages = max(1, -(-len(rows) // per_page))

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if "rest_route=" in url:
            page = int(url.split("&page=")[1].split("&")[0])
            batch = rows[(page - 1) * per_page : page * per_page]
            return FakeResponse(
                text=json.dumps(batch),
                headers={
                    "x-wp-total": str(len(rows)),
                    "x-wp-totalpages": str(total_pages),
                },
            )
        return FakeResponse(text=pages[url])

    return route


def test_fetch_reads_the_rest_rows_and_each_postings_page() -> None:
    fetcher = FakeFetcher(_route(_fixture("aspiringit")))
    jobs = {
        job.id: job
        for job in get_scraper(
            "wp_job_openings", "aspiringit.com", fetcher=fetcher
        ).fetch()
    }

    assert set(jobs) == {
        "wp_job_openings:aspiringit.com:861",
        "wp_job_openings:aspiringit.com:3906",
        "wp_job_openings:aspiringit.com:3913",
    }
    job = jobs["wp_job_openings:aspiringit.com:3913"]
    assert job.title == "Senior AI / Machine Learning Technical Leader (LLMs & GenAI)"
    assert job.company == "AspiringIT"
    # One JSON-LD Place per location term, every one kept.
    assert job.location == "Plano; Tx (Hybrid)"
    assert job.employment_type == "Full Time"
    assert job.remote is False
    assert job.url == (
        "https://aspiringit.com/jobs/senior-ai-machine-learning-technical-leader-llms-genai/"
    )
    assert job.posted_at == "2026-07-30T17:25:40+00:00"
    assert job.description.startswith("Position Summary")
    assert "<" not in job.description
    assert job.salary is None
    remote = jobs["wp_job_openings:aspiringit.com:861"]
    assert remote.location == "Remote - Canada"
    assert remote.remote is True
    assert remote.employment_type is None  # the site set no job type on this posting


def test_the_listing_walks_every_page_the_route_states_by_id() -> None:
    fetcher = FakeFetcher(_route(_fixture("aspiringit"), per_page=1))
    scraper = get_scraper("wp_job_openings", "aspiringit.com", fetcher=fetcher)

    assert len(scraper.fetch()) == 3
    listing = [r for r in fetcher.requests if "rest_route=" in r.url]
    assert [r.url.split("&page=")[1].split("&")[0] for r in listing] == ["1", "2", "3"]
    assert all(
        r.url.startswith(
            "https://aspiringit.com/?rest_route=/wp/v2/awsm_job_openings&per_page=100&"
        )
        and "&orderby=id&order=asc&" in r.url
        for r in listing
    )
    assert scraper.truncated is None


def test_every_request_asks_with_firefoxs_fingerprint() -> None:
    # Hostinger's CDN answers 403 to every Chrome fingerprint curl_cffi offers (module docstring).
    fetcher = FakeFetcher(_route(_fixture("aspiringit")))
    get_scraper("wp_job_openings", "aspiringit.com", fetcher=fetcher).fetch()

    assert fetcher.requests
    assert {r.kwargs.get("impersonate") for r in fetcher.requests} == {"firefox"}


def test_a_listing_short_of_the_stated_total_marks_the_board_truncated() -> None:
    fixture = _fixture("aspiringit")
    route = _route(fixture)

    def short(method: str, url: str, kwargs: dict) -> FakeResponse:
        response = route(method, url, kwargs)
        if "rest_route=" in url:
            response.headers["x-wp-total"] = "30"
        return response

    scraper = get_scraper(
        "wp_job_openings", "aspiringit.com", fetcher=FakeFetcher(short)
    )
    assert len(scraper.fetch()) == 3
    assert scraper.truncated == "the REST route listed 3 of the 30 postings it states"


def test_a_listing_behind_a_byte_order_mark_is_read() -> None:
    # career.icepoweraudio.com and lacura.co.uk serve the route's JSON after a UTF-8 BOM.
    route = _route(_fixture("aspiringit"))

    def bom(method: str, url: str, kwargs: dict) -> FakeResponse:
        response = route(method, url, kwargs)
        if "rest_route=" in url:
            response.content = b"\xef\xbb\xbf" + response.content
            response.text = "﻿" + response.text
        return response

    scraper = get_scraper("wp_job_openings", "aspiringit.com", fetcher=FakeFetcher(bom))
    assert len(scraper.fetch()) == 3


def test_a_page_in_the_routes_place_fails_the_board_rather_than_empties_it() -> None:
    # A security or maintenance plugin can answer the route with an HTML page and a 200.
    def html(method: str, url: str, kwargs: dict) -> FakeResponse:
        return FakeResponse(
            text="<!DOCTYPE html><html><title>Maintenance</title></html>"
        )

    scraper = get_scraper("wp_job_openings", "finac.io", fetcher=FakeFetcher(html))
    with pytest.raises(BoardUnreadable):
        scraper.fetch()


def test_a_site_with_nothing_published_reads_empty_without_a_page_fetch() -> None:
    def empty(method: str, url: str, kwargs: dict) -> FakeResponse:
        return FakeResponse(
            text="[]", headers={"x-wp-total": "0", "x-wp-totalpages": "0"}
        )

    fetcher = FakeFetcher(empty)
    assert (
        get_scraper("wp_job_openings", "ocubeservices.com", fetcher=fetcher).fetch()
        == []
    )
    assert len(fetcher.requests) == 1


def test_a_lost_page_ships_its_job_from_the_rest_row() -> None:
    fixture = _fixture("aspiringit")
    route = _route(fixture)

    def lost(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url.endswith("/senior-credit-risk-analyst/"):
            return FakeResponse(status_code=500, text="error")
        return route(method, url, kwargs)

    scraper = get_scraper(
        "wp_job_openings", "aspiringit.com", fetcher=FakeFetcher(lost)
    )
    jobs = {job.id: job for job in scraper.fetch()}

    job = jobs["wp_job_openings:aspiringit.com:3906"]
    assert job.title == "Senior Credit Risk Analyst"
    assert job.description
    # Without its page, the location and type come off the REST row's class_list slugs.
    assert job.location == "Onsite Waterloo"
    assert job.employment_type == "Contract"
    assert sum(scraper.detail_losses.values()) == 1
    assert (
        scraper.truncated is None
    )  # the list is whole; only one Job's fields are thinner


def test_a_page_without_json_ld_is_read_off_its_specifications_block() -> None:
    # abatec.co.uk and europeobserver.net serve the plugin's specifications block with no
    # JobPosting JSON-LD (2026-09-28): the page still names the posting's places.
    fixture = _fixture("heptarc")
    for job_id, page in fixture["pages"].items():
        fixture["pages"][job_id] = page.split('<script type="application/ld+json">')[0]
    jobs = {
        job.id: job
        for job in get_scraper(
            "wp_job_openings", "www.heptarc.com", fetcher=FakeFetcher(_route(fixture))
        ).fetch()
    }

    job = jobs["wp_job_openings:www.heptarc.com:95379"]
    assert job.location == "hybrid"
    assert job.department == "AI Engineer"
    assert job.employment_type == "Contract"
    assert (
        job.company == "Heptarc"
    )  # no page states one, so the host is spelled as a name


def test_a_site_before_class_list_reads_its_specs_off_the_page() -> None:
    # heptarc.com's REST rows carry no class_list (WordPress before 6.5).
    jobs = {
        job.id: job
        for job in get_scraper(
            "wp_job_openings",
            "www.heptarc.com",
            fetcher=FakeFetcher(_route(_fixture("heptarc"))),
        ).fetch()
    }

    job = jobs["wp_job_openings:www.heptarc.com:95379"]
    assert job.title == "Senior AI Engineer"  # the trailing no-break space is cut
    assert job.department == "AI Engineer"
    assert job.employment_type == "Contract"
    assert job.location == "hybrid"
    assert job.company == "Heptarc"


def test_the_tech_gate_skips_a_non_tech_postings_page_in_the_pipeline() -> None:
    fetcher = FakeFetcher(_route(_fixture("aspiringit")))
    scraper = get_scraper(
        "wp_job_openings", "aspiringit.com", fetcher=fetcher, have_details=frozenset()
    )
    jobs = {job.id: job for job in scraper.fetch()}

    fetched = {r.url for r in fetcher.requests if "rest_route=" not in r.url}
    assert "https://aspiringit.com/jobs/senior-credit-risk-analyst/" not in fetched
    assert (
        "https://aspiringit.com/jobs/senior-ai-machine-learning-technical-leader-llms-genai/"
        in fetched
    )
    # The gated posting still ships, off its REST row.
    assert jobs["wp_job_openings:aspiringit.com:3906"].location == "Onsite Waterloo"


def test_the_tech_gate_reads_the_category_slug_the_rest_row_states() -> None:
    # tech_filter promotes a vague title on a technical department, so the gate must see the
    # listing's category: the same posting filed under an IT category (a real slug shape,
    # `job-category-information-technology`) is fetched.
    fixture = _fixture("aspiringit")
    for row in fixture["rows"]:
        if row["id"] == 3906:
            row["class_list"].append("job-category-information-technology")
    fetcher = FakeFetcher(_route(fixture))
    get_scraper(
        "wp_job_openings", "aspiringit.com", fetcher=fetcher, have_details=frozenset()
    ).fetch()

    fetched = {r.url for r in fetcher.requests if "rest_route=" not in r.url}
    assert "https://aspiringit.com/jobs/senior-credit-risk-analyst/" in fetched


def test_every_job_url_matches_the_declared_shape() -> None:
    scraper = get_scraper(
        "wp_job_openings",
        "aspiringit.com",
        fetcher=FakeFetcher(_route(_fixture("aspiringit"))),
    )
    jobs = scraper.fetch()
    assert jobs
    for job in jobs:
        assert re.fullmatch(scraper.url_shape, job.url), job.url
    for link in (
        "https://finac.io/jobs/senior-accountant-jaipur-exp-3-5-yrs/",
        "https://rajuristeels.com/current-openings/electrical-engineer-job-opening/",
        "https://stork.express/?awsm_job_openings=warehouse-associate",
    ):
        assert re.fullmatch(scraper.url_shape, scraper.job_url(link))


def test_slug_from_reads_the_host_off_either_column() -> None:
    cls = type(get_scraper("wp_job_openings", "finac.io"))
    assert cls.slug_from("finac.io", "https://finac.io/jobs/") == "finac.io"
    assert cls.slug_from("Finac.io", "") == "finac.io"
