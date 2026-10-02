"""The public gr8people scraper contract, over recorded Teradata postings."""

import json
import re
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http
from headstart.scrapers.base import BoardUnreadable
from headstart.scrapers.gr8people import (
    Gr8PeopleScraper,
    search_body,
    uses_google_search,
)


def test_the_browser_feature_flags_choose_its_public_search_surface():
    page = '<script id="__NEXT_DATA__">{"props":{"initialFeatureFlags":[["google-job-discovery",true],["ops-kill-switch-google-cts",false]]}}</script>'
    assert uses_google_search(page) is True
    assert "searchGoogleJobDiscovery" in search_body(google=True)["query"]
    assert (
        uses_google_search(
            page.replace(
                '"ops-kill-switch-google-cts",false',
                '"ops-kill-switch-google-cts",true',
            )
        )
        is False
    )


def test_recorded_postings_keep_descriptions_and_every_location():
    raw = json.loads(
        (Path(__file__).parent / "fixtures/gr8people_postings.json").read_text()
    )
    jobs = Gr8PeopleScraper("careers.teradata.com", "Teradata").parse(
        raw, "2026-10-02T00:00:00Z"
    )
    assert len(jobs) == 3
    assert (
        jobs[1].location == "Bengaluru, Karnataka, India; Hyderabad, Telangana, India"
    )
    assert jobs[2].remote is True
    assert jobs[0].description and "<p>" not in jobs[0].description
    assert jobs[0].posted_at
    assert jobs[0].department == "Engineering"


def postings():
    return json.loads(
        (Path(__file__).parent / "fixtures/gr8people_postings.json").read_text()
    )


def envelope(nodes, *, total=3, more=False, cursor=None):
    return {
        "data": {
            "searchJobs": {
                "results": {
                    "nodes": nodes,
                    "totalCount": total,
                    "pageInfo": {"hasNextPage": more, "endCursor": cursor},
                }
            }
        }
    }


def scraper_with_pages(pages, *, page_status=200, requests=None):
    replies = iter(pages)

    def route(method, url, kwargs):
        if method == "GET":
            return FakeResponse(
                page_status,
                "<title>Search Careers at Teradata</title>assets.gr8people.com",
            )
        if requests is not None:
            requests.append(kwargs["json"]["variables"])
        reply = next(replies)
        return (
            reply
            if isinstance(reply, FakeResponse)
            else FakeResponse(text=json.dumps(reply))
        )

    return Gr8PeopleScraper(
        "careers.teradata.com", "Teradata", fetcher=FakeFetcher(route)
    )


def test_cursor_pagination_reads_every_posting_and_omits_the_initial_cursor():
    rows = postings()
    requests = []
    scraper = scraper_with_pages(
        [envelope(rows[:2], more=True, cursor="first"), envelope(rows[2:])],
        requests=requests,
    )
    jobs = scraper.fetch()
    assert len(jobs) == 3
    assert scraper.truncated is None
    assert requests[0] == {"first": 100}
    assert requests[1]["after"] == "first"


@pytest.mark.parametrize(
    "last",
    [
        envelope([], total=3),
        FakeResponse(429),
        {"errors": [{"message": "unavailable"}]},
    ],
)
def test_an_incomplete_listing_keeps_jobs_without_confirming_closures(last):
    scraper = scraper_with_pages(
        [envelope(postings()[:1], more=True, cursor="first"), last]
    )
    assert len(scraper.fetch()) == 1
    assert scraper.truncated


def test_an_http_200_graphql_error_is_not_an_empty_board():
    scraper = scraper_with_pages([{"errors": [{"message": "unknown tenant"}]}])
    with pytest.raises(BoardUnreadable):
        scraper.fetch()


def test_a_repeating_page_stops_and_marks_the_board_incomplete():
    page = envelope(postings()[:1], more=True, cursor="first")
    scraper = scraper_with_pages([page, page])
    assert len(scraper.fetch()) == 1
    assert scraper.truncated


def test_a_false_end_flag_still_checks_the_stated_total():
    scraper = scraper_with_pages([envelope(postings()[:1], total=168)])
    assert len(scraper.fetch()) == 1
    assert scraper.truncated


def test_public_board_404_raises_even_when_the_api_could_list_jobs():
    scraper = scraper_with_pages([], page_status=404)
    with pytest.raises(http.RequestsError):
        scraper.fetch()


def test_empty_board_is_successful_and_authoritative():
    scraper = scraper_with_pages([envelope([], total=0)])
    assert scraper.fetch() == []
    assert scraper.truncated is None


def test_field_mapping_keeps_hybrid_unknown_and_does_not_invent_salary_periods():
    jobs = Gr8PeopleScraper("careers.teradata.com", "Teradata").parse(postings(), "now")
    assert jobs[0].salary == "178800-268200 USD per-year"
    assert jobs[1].remote is None
    assert jobs[2].salary is None
    assert jobs[0].employment_type == "Full Time"
    assert re.fullmatch(Gr8PeopleScraper.url_shape, jobs[0].url)


def test_deleted_nodes_do_not_hide_the_rest_of_a_valid_page():
    rows = postings()
    page = envelope([rows[1], {"key": None, "title": None}], total=3)
    page["errors"] = [
        {
            "extensions": {"code": "NOT_FOUND"},
            "path": ["searchJobs", "results", "nodes", 1, "key"],
        }
    ]
    scraper = scraper_with_pages([envelope(rows[:1], more=True, cursor="first"), page])
    assert len(scraper.fetch()) == 2
    assert scraper.truncated


def test_alias_identity_includes_the_client_within_one_vendor_organisation():
    def scraper(client):
        page = (
            '<script id="__NEXT_DATA__">'
            + json.dumps(
                {
                    "props": {
                        "pageProps": {"visit": {"orgId": "b1c84d", "clientId": client}}
                    }
                }
            )
            + "</script>"
        )
        return Gr8PeopleScraper(
            "host.workgr8.com", fetcher=FakeFetcher(lambda *_: FakeResponse(text=page))
        )

    assert scraper("1").public_client() == "b1c84d/1"
    assert scraper("2").public_client() == "b1c84d/2"


def test_board_identity_normalises_host_url_and_case():
    assert (
        Gr8PeopleScraper.slug_from("https://Careers.Teradata.com/jobs", "")
        == "careers.teradata.com"
    )
    assert (
        Gr8PeopleScraper.slug_from("teradata", "https://careers.teradata.com/jobs")
        == "careers.teradata.com"
    )
