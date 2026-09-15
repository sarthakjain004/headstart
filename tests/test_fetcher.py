"""Tests for the Fetcher seam (headstart.fetcher, ADR-0153).

Before this seam existed, faking a scraper's HTTP meant one of four structurally different
tricks: monkeypatching ``headstart.http``'s module attributes with hand-rolled response doubles,
monkeypatching a scraper's own ``_get``, monkeypatching ``fan_out``/``fan_out_async`` themselves,
or bypassing the network entirely by reconstructing ``fetch_raw``'s output by hand from a fixture
(e.g. ``tests/test_icims.py``'s ``_raw_from_fixture``). None of those let a caller simply pass a
fake fetcher in.

These tests do exactly that: construct a scraper with a fake :class:`~headstart.fetcher.Fetcher`
(and, for darwinbox, a fake browser fetcher too) and call its real ``fetch_raw``/``parse``. No
test in this file monkeypatches ``headstart.http`` or ``headstart.browser_http`` — the whole
point is that the seam makes that unnecessary. One representative per category from the task that
motivated ADR-0153: greenhouse (a plain single-fetch board), icims (the sitemap-plus-per-job-
JSON-LD-detail-pass pattern shared with successfactors/meta), and darwinbox (the browser adapter,
both its curl-first path and its walled escalation).
"""

from __future__ import annotations

import json
from typing import Any, Self

from headstart.scrapers.darwinbox import DarwinboxScraper
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.icims import ICIMSScraper

SCRAPED_AT = "2026-01-01T00:00:00+00:00"


class FakeResponse:
    """The slice of ``curl_cffi``'s ``Response`` surface every scraper in this repo reads:
    ``.status_code``, ``.text``, ``.json()``, ``.raise_for_status()``."""

    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHTTPError(self)


class _FakeHTTPError(Exception):
    """Carries ``.response`` like ``curl_cffi``'s own ``HTTPError`` does, since darwinbox's wall
    detection (``_is_wall``) reads the status off exactly that attribute."""

    def __init__(self, response: FakeResponse) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


class FakeFetcher:
    """A ``headstart.fetcher.Fetcher`` a test can hand straight to a scraper's constructor.

    ``responses`` maps an exact ``(method, url)`` pair to the :class:`FakeResponse` ``.fetch()``
    should hand back for it; every call is recorded in ``.calls`` so a test can also assert the
    seam is genuinely being reached rather than short-circuited. ``fetch_async`` delegates to the
    same table, so an ATS whose detail pass defaults to the multiplexed path (icims's does) needs
    no separate fake.
    """

    def __init__(self, responses: dict[tuple[str, str], FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def fetch(self, method: str, url: str, **_kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        return self.responses[(method, url)]

    async def fetch_async(
        self, session: Any, method: str, url: str, **kwargs: Any
    ) -> FakeResponse:
        return self.fetch(method, url, **kwargs)


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
    fake = FakeFetcher({("GET", url): FakeResponse(200, json.dumps(payload))})

    scraper = GreenhouseScraper("acme", "Acme", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert fake.calls == [("GET", url)]  # the seam, not a global, answered this
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
    fake = FakeFetcher(
        {
            ("GET", sitemap_url): FakeResponse(200, sitemap_xml),
            ("GET", detail_url): FakeResponse(200, detail_html),
        }
    )

    scraper = ICIMSScraper(host, "Acme", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert sorted(fake.calls) == sorted([("GET", sitemap_url), ("GET", detail_url)])
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
            return _FakeBrowserResponse({"message": {"company": {"new_careers": True}}})
        page = self._pages[(json or {}).get("page", 1) - 1]
        return _FakeBrowserResponse({"data": page})


class _FakeBrowserResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


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
    fake = FakeFetcher(
        {
            ("POST", url): FakeResponse(200, json.dumps(jobs_payload)),
            ("GET", portal_url): FakeResponse(
                200, json.dumps({"message": {"company": {"new_careers": True}}})
            ),
        }
    )

    scraper = DarwinboxScraper("acme", "Acme", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)

    assert ("POST", url) in fake.calls
    assert len(jobs) == 1
    assert jobs[0].id == "darwinbox:acme:abc123"
    assert jobs[0].title == "Backend Engineer"


def test_darwinbox_wall_escalates_to_the_injected_browser_fetcher() -> None:
    """A persistent 403 on the tenant's real TLD escalates to the browser fetcher — proven here
    with a fake `fetcher` for the curl attempts AND a fake `browser_fetcher` for the escalation,
    neither of which touches `headstart.http` or `headstart.browser_http`."""
    in_url = "https://acme.darwinbox.in/ms/candidateapi/job/alljobs?companyId=main"
    com_url = "https://acme.darwinbox.com/ms/candidateapi/job/alljobs?companyId=main"
    fake_http = FakeFetcher(
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
    assert ("POST", in_url) in fake_http.calls
    assert ("POST", com_url) in fake_http.calls
    # ... then the escalation navigated the walled TLD's careers page, not the other one.
    assert (
        "navigate",
        "https://acme.darwinbox.in/ms/candidate/careers",
    ) in browser_calls
    assert len(jobs) == 1
    assert jobs[0].id == "darwinbox:acme:abc123"
    assert jobs[0].title == "Backend Engineer"
