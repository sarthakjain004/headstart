"""Tests for the Fetcher seam (headstart.fetcher, ADR-0153, ADR-0199).

Before this seam existed, faking a scraper's HTTP meant one of four structurally different
tricks: monkeypatching ``headstart.http``'s module attributes with hand-rolled response doubles,
monkeypatching a scraper's own ``_get``, monkeypatching ``fan_out``/``fan_out_async`` themselves,
or bypassing the network entirely by reconstructing ``fetch_raw``'s output by hand from a fixture
(e.g. ``tests/test_icims.py``'s ``_raw_from_fixture``). None of those let a caller simply pass a
fake fetcher in.

These tests do exactly that: construct a scraper with a fake :class:`~headstart.fetcher.Fetcher`
(and, for darwinbox, a fake browser fetcher too) and call its real ``fetch_raw``/``parse``. No
test in this file answers a request by monkeypatching ``headstart.http`` or
``headstart.browser_http`` — the whole point is that the seam makes that unnecessary. The only
patches of ``headstart.http`` make a request that bypasses the seam fail loudly
(``seam_bypass_fails``). One representative per category
from the task that motivated ADR-0153: greenhouse (a plain single-fetch board), icims (the
sitemap-plus-per-job-JSON-LD-detail-pass pattern shared with successfactors/meta), and darwinbox
(the browser adapter, both its curl-first path and its walled escalation). ADR-0199 adds the two
requests that used to bypass the seam, Workday's listing and Trakstar's feed, and checks that
every registered Scraper takes the fetcher ``get_scraper`` is given.
"""

from __future__ import annotations

import json
from collections.abc import Callable

# `FakeBrowserFetcher.fetch` takes a `json=` keyword, which shadows the module inside it.
from json import dumps as json_text
from typing import Any, Self

import pytest
from fake_fetcher import FakeFetcher, FakeRequest, FakeResponse, Route

from headstart import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.darwinbox import DarwinboxScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.icims import ICIMSScraper
from headstart.scrapers.registry import SCRAPERS, get_scraper
from headstart.scrapers.workday import _PAGE_LIMIT

SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _fetcher_answering(
    responses: dict[tuple[str, str], FakeResponse],
) -> FakeFetcher:
    """The shared fake, answering an exact ``(method, url)`` from ``responses`` — a request
    nothing scripted raises KeyError, so a test cannot pass by reaching an unexpected URL."""
    return FakeFetcher(lambda method, url, _kwargs: responses[(method, url)])


def _methods_and_urls(fake: FakeFetcher) -> list[tuple[str, str]]:
    return [(request.method, request.url) for request in fake.requests]


# --- greenhouse: a plain single-fetch board -------------------------------------------------


def test_greenhouse_fetch_raw_uses_the_injected_fetcher() -> None:
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    payload = {
        "jobs": [
            {
                "id": 42,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "content": "<p>Build things.</p>",
                "departments": [{"name": "Engineering"}],
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ],
        "meta": {"total": 1},
    }
    fake = _fetcher_answering({("GET", url): FakeResponse(200, json.dumps(payload))})

    scraper = GreenhouseScraper("acme", "Acme", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    # the seam, not a global, answered this
    assert _methods_and_urls(fake) == [("GET", url)]
    assert len(jobs) == 1
    assert jobs[0].id == "greenhouse:acme:42"
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].location == "Remote"
    assert jobs[0].department == "Engineering"
    assert jobs[0].description == "Build things."


# --- icims: sitemap + per-job JSON-LD detail pass (shared shape with successfactors/meta) ---


def _icims_ld_page(node: dict) -> str:
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "JobPosting", **node})
        + "</script>"
    )


def test_icims_fetch_raw_uses_the_injected_fetcher_for_listing_and_detail() -> None:
    host = "acme.icims.com"
    sitemap_url = f"https://{host}/sitemap.xml"
    job_url = f"https://{host}/jobs/42/backend-engineer/job"
    detail_url = f"{job_url}?in_iframe=1"
    sitemap_xml = (
        "<urlset><url>"
        f"<loc>{job_url}</loc><lastmod>2026-09-01T00:00:00.000Z</lastmod>"
        "</url></urlset>"
    )
    detail_html = _icims_ld_page(
        {
            "title": "Backend Engineer",
            "description": "Build things.",
            "datePosted": "2026-09-01T00:00:00.000Z",  # anchored -> real, not fabricated
        }
    )
    fake = _fetcher_answering(
        {
            ("GET", sitemap_url): FakeResponse(200, sitemap_xml),
            ("GET", detail_url): FakeResponse(200, detail_html),
        }
    )

    scraper = ICIMSScraper(host, "Acme", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert sorted(_methods_and_urls(fake)) == sorted(
        [("GET", sitemap_url), ("GET", detail_url)]
    )
    assert len(jobs) == 1
    assert jobs[0].id == "icims:acme.icims.com:42"
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].description == "Build things."
    assert jobs[0].posted_at == "2026-09-01T00:00:00.000Z"


# --- darwinbox: the browser adapter, both its curl-first path and its walled escalation -----


class FakeBrowserFetcher:
    """Satisfies :class:`~headstart.browser_http.BrowserFetcher`'s shape (``fetch`` only, a
    context manager) without touching ``headstart.browser_http`` at all — the fake a test injects
    as :class:`DarwinboxScraper`'s ``browser_fetcher`` factory."""

    def __init__(self, page_url: str, pages: list[list[dict]], calls: list) -> None:
        self.page_url = page_url
        self._pages = pages
        self._calls = calls

    def __enter__(self) -> Self:
        self._calls.append(("navigate", self.page_url))
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def fetch(
        self, method: str, url: str, *, json: dict | None = None, **_ignored: Any
    ):
        self._calls.append((method, url, json))
        if method == "GET":
            portal = {"message": {"company": {"new_careers": True}}}
            return FakeResponse(200, json_text(portal))
        page = self._pages[(json or {}).get("page", 1) - 1]
        return FakeResponse(200, json_text({"data": page}))


def test_darwinbox_fetch_raw_uses_the_injected_fetcher_when_unwalled() -> None:
    """The ordinary curl-first path needs only the plain fetcher — no browser at all."""
    url = "https://acme.darwinbox.in/ms/candidateapi/job/alljobs?companyId=main"
    portal_url = "https://acme.darwinbox.in/ms/candidateapi/companyinfo?companyId=main"
    jobs_payload = {
        "data": [
            {
                "id": "abc123",
                "title": "Backend Engineer",
                "locations": "Bangalore",
                "posted_on": "01-Sep-2026",
            }
        ]
    }
    fake = _fetcher_answering(
        {
            ("POST", url): FakeResponse(200, json.dumps(jobs_payload)),
            ("GET", portal_url): FakeResponse(
                200, json.dumps({"message": {"company": {"new_careers": True}}})
            ),
        }
    )

    scraper = DarwinboxScraper("acme", "Acme", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert ("POST", url) in _methods_and_urls(fake)
    assert len(jobs) == 1
    assert jobs[0].id == "darwinbox:acme:abc123"
    assert jobs[0].title == "Backend Engineer"


def test_darwinbox_wall_escalates_to_the_injected_browser_fetcher() -> None:
    """A persistent 403 on the tenant's real TLD escalates to the browser fetcher — proven here
    with a fake `fetcher` for the curl attempts AND a fake `browser_fetcher` for the escalation,
    neither of which touches `headstart.http` or `headstart.browser_http`."""
    in_url = "https://acme.darwinbox.in/ms/candidateapi/job/alljobs?companyId=main"
    com_url = "https://acme.darwinbox.com/ms/candidateapi/job/alljobs?companyId=main"
    fake_http = _fetcher_answering(
        {
            ("POST", in_url): FakeResponse(403, "cloudflare wall"),
            ("POST", com_url): FakeResponse(500, "Invalid subdomain"),
        }
    )
    browser_calls: list = []
    pages = [[{"id": "abc123", "title": "Backend Engineer", "locations": "Bangalore"}]]

    def browser_factory(page_url: str) -> FakeBrowserFetcher:
        return FakeBrowserFetcher(page_url, pages, browser_calls)

    scraper = DarwinboxScraper(
        "acme", "Acme", fetcher=fake_http, browser_fetcher=browser_factory
    )
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    # both curl attempts happened first (the wall is discovered, not assumed) ...
    assert ("POST", in_url) in _methods_and_urls(fake_http)
    assert ("POST", com_url) in _methods_and_urls(fake_http)
    # ... then the escalation navigated the walled TLD's careers page, not the other one.
    assert (
        "navigate",
        "https://acme.darwinbox.in/ms/candidate/careers",
    ) in browser_calls
    assert len(jobs) == 1
    assert jobs[0].id == "darwinbox:acme:abc123"
    assert jobs[0].title == "Backend Engineer"


# --- the seam reaches every Scraper (ADR-0199) ------------------------------------------------


@pytest.mark.parametrize("ats", sorted(SCRAPERS))
def test_get_scraper_hands_its_fetcher_to_every_scraper(ats: str) -> None:
    """`get_scraper` once took no fetcher, and seven of the nine `__init__` overrides dropped
    one passed to the constructor directly."""
    fake = FakeFetcher(lambda _method, _url, _kwargs: FakeResponse(404))
    scraper = get_scraper(ats, "acme/careers", "Acme", fetcher=fake)
    assert scraper._fetcher is fake


@pytest.fixture
def seam_bypass_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any request that skips the injected fetcher for the module-global client fails loudly,
    instead of quietly reaching the network."""

    def bypassed(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a request bypassed the injected fetcher")

    monkeypatch.setattr(http, "fetch", bypassed)
    monkeypatch.setattr(http, "fetch_async", bypassed)
    monkeypatch.setattr(http, "session", bypassed)


# --- workday: listing POSTs, sync and async, and the stale-cookie reset ------------------------

_WORKDAY_BOARD = "https://acme.wd1.myworkdayjobs.com/External"
_WORKDAY_LISTING = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs"
_WORKDAY_POSTINGS = [
    {"title": f"Backend Engineer {n}", "externalPath": f"/job/Remote/Backend_R{n}"}
    for n in range(
        _PAGE_LIMIT + 1
    )  # one page past the limit: page 2 rides `_post_async`
]


def _workday_board(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
    """A healthy Board of `_WORKDAY_POSTINGS`: the listing pages them, every detail answers."""
    if url == _WORKDAY_LISTING:
        offset, limit = kwargs["json"]["offset"], kwargs["json"]["limit"]
        page = _WORKDAY_POSTINGS[offset : offset + limit]
        total = len(_WORKDAY_POSTINGS)
        return FakeResponse(200, json.dumps({"total": total, "jobPostings": page}))
    info = {"title": "Backend Engineer", "jobDescription": f"<p>{url}</p>"}
    return FakeResponse(200, json.dumps({"jobPostingInfo": info}))


def _stale_cookie_until_cleared(
    fake: FakeFetcher, stale: Callable[[str, dict[str, Any]], bool]
) -> Route:
    """`_workday_board`, except that a request `stale` picks answers ADR-0103's stale-cookie 400
    until `fake`'s cookies have been cleared."""

    def route(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if not fake.cookie_clears and stale(method, kwargs):
            return FakeResponse(400, '{"errorCode": "S22"}')
        return _workday_board(method, url, kwargs)

    return route


def _is_listing_page(method: str, kwargs: dict[str, Any]) -> bool:
    return method == "POST" and kwargs["json"]["limit"] == _PAGE_LIMIT  # not the probe


def _is_detail(method: str, _kwargs: dict[str, Any]) -> bool:
    return method == "GET"


def test_workday_listing_pages_reach_the_injected_fetcher_with_their_egress_kwargs(
    seam_bypass_fails: None,
) -> None:
    fake = FakeFetcher(_workday_board)
    scraper = get_scraper("workday", _WORKDAY_BOARD, "Acme", fetcher=fake)

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert len(jobs) == len(_WORKDAY_POSTINGS)
    assert all(job.description for job in jobs)
    listing = [request for request in fake.requests if request.url == _WORKDAY_LISTING]
    # the instance probe (limit 1), then page 1 (sync `_post`) and page 2 (`_post_async`)
    offsets = [request.kwargs["json"]["offset"] for request in listing]
    assert offsets == [0, 0, _PAGE_LIMIT]
    for request in listing[1:]:
        assert request.method == "POST"
        sent_without_body = {
            name: value for name, value in request.kwargs.items() if name != "json"
        }
        assert sent_without_body == {
            "headers": {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "timeout": 30,
            "egress_group": "workday",
            "egress_on": frozenset({429}),
            "egress_board": "workday:acme/External",
        }


def test_workday_listing_retry_via_direct_egress_sends_no_egress_kwargs(
    seam_bypass_fails: None,
) -> None:
    """A transient non-JSON page is refetched once with `direct=True`, which deliberately
    carries none of `_egress()` — not even the Board attribution."""
    empty_board = FakeResponse(200, json.dumps({"total": 0, "jobPostings": []}))
    listing_answers = iter(
        [FakeResponse(200, "<html><title>Just a moment...</title></html>"), empty_board]
    )

    def route(method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        if kwargs["json"]["limit"] == 1:  # the instance probe
            return empty_board
        return next(listing_answers)

    fake = FakeFetcher(route)
    scraper = get_scraper("workday", _WORKDAY_BOARD, "Acme", fetcher=fake)

    assert scraper.parse(scraper.fetch_raw(), SCRAPED_AT) == []

    _probe, first_page, direct_retry = fake.requests
    assert "egress_board" in first_page.kwargs
    assert set(direct_retry.kwargs) == {"json", "headers", "timeout"}


def test_workday_listing_400_clears_the_injected_fetchers_cookies(
    seam_bypass_fails: None,
) -> None:
    """ADR-0103's stale-cookie reset reaches the fetcher's jar, not the module global's."""
    fake = FakeFetcher(_workday_board)
    fake.route = _stale_cookie_until_cleared(fake, _is_listing_page)
    scraper = get_scraper("workday", _WORKDAY_BOARD, "Acme", fetcher=fake)

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert len(jobs) == len(_WORKDAY_POSTINGS)
    assert fake.cookie_clears == [None]  # the whole jar, once


def test_workday_sync_detail_400_clears_the_injected_fetchers_cookies(
    seam_bypass_fails: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sync detail pass (`HEADSTART_ASYNC_FANOUT=0`) resets the same jar on a detail 400.
    Its worker threads can each see a 400 before the first reset lands, so the count may exceed
    one; every reset is still the whole jar, through the fetcher."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    fake = FakeFetcher(_workday_board)
    fake.route = _stale_cookie_until_cleared(fake, _is_detail)
    scraper = get_scraper("workday", _WORKDAY_BOARD, "Acme", fetcher=fake)

    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert len(jobs) == len(_WORKDAY_POSTINGS)
    assert all(job.description for job in jobs)  # every stale detail was recovered
    assert fake.cookie_clears and set(fake.cookie_clears) == {None}


# --- trakstar: the RSS feed rides the same fetcher as the rest of the Board ------------------


def test_trakstar_feed_reaches_the_injected_fetcher_with_board_attribution_only(
    seam_bypass_fails: None,
) -> None:
    feed_url = "https://acme.hire.trakstar.com/jobfeeds/acme"
    feed = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    fake = _fetcher_answering({("GET", feed_url): FakeResponse(200, feed)})
    scraper = get_scraper("trakstar", "acme", "Acme", fetcher=fake)

    assert scraper.fetch_via_feed(SCRAPED_AT) == []
    assert fake.requests == [
        FakeRequest(
            "GET",
            feed_url,
            {
                "timeout": 30,
                "headers": {"User-Agent": USER_AGENT},
                "egress_board": "trakstar:acme",
            },
        )
    ]
