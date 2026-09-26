"""Tests for the Avature scraper (headstart.scrapers.avature).

The fixture is real captures from `bloomberg.avature.net` (2026-09-26) — its robots.txt, three
portals' sitemap indexes and sitemaps trimmed to the rows named below, and one job page — plus
one job page from each of three other tenants, for the page layouts Bloomberg's does not use.
"""

from __future__ import annotations

import json
import pathlib
import re

from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.avature import listing_rows, page_fields
from headstart.scrapers.pacer import Pacer
from headstart.scrapers.registry import get_scraper

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "avature_bloomberg.json").read_text()
)
_TECH = "https://bloomberg.avature.net/careers/JobDetail/Senior-Software-Engineer-VAULT/22342"
#: Tech by its slug alone ("Engineering"), so the gate lets it through too.
_PDM = (
    "https://bloomberg.avature.net/careers/JobDetail/"
    "Product-Delivery-Manager-Engineering-COO-Office/12444"
)
_SCRAPED_AT = "2026-09-26T00:00:00+00:00"


def _route(pages: dict[str, FakeResponse] | None = None):
    """Answer from the fixture; a job page from ``pages`` first, else the fixture's own."""

    def route(method: str, url: str, kwargs: dict) -> FakeResponse:
        if url.endswith("/robots.txt"):
            return FakeResponse(200, _FIXTURE["robots"])
        match = re.search(r"avature\.net/([^/]+)/sitemap_index\.xml$", url)
        if match:
            index = _FIXTURE["index"].get(match.group(1))
            return FakeResponse(200, index) if index else FakeResponse(404, "")
        if url in _FIXTURE["sitemap"]:
            return FakeResponse(200, _FIXTURE["sitemap"][url])
        if pages and url in pages:
            return pages[url]
        if url in _FIXTURE["page"]:
            return FakeResponse(200, _FIXTURE["page"][url])
        return FakeResponse(404, "")

    return route


def _scraper(route, *, gated: bool = True):
    # `have_details` set is what arms the tech gate: it runs for the pipeline only.
    fetcher = FakeFetcher(route)
    scraper = get_scraper(
        "avature", "bloomberg", fetcher=fetcher, have_details=set() if gated else None
    )
    scraper.pacer = Pacer(0)  # the real one spaces requests a second apart
    scraper.fake = fetcher
    return scraper


def _job_pages_asked(scraper) -> list[str]:
    return [url for url in scraper.fake.urls() if "/JobDetail/" in url]


_INTERNAL_ONLY = (
    "https://bloomberg.avature.net/internalcareers/JobDetail/"
    "User-Support-Analytics-Team-Leader-Sydney/21234"
)
_TO_LOGIN = FakeResponse(
    302,
    "",
    headers={"location": "https://bloomberg.avature.net/internalcareers/Login/"},
)


def test_listing_is_every_portals_sitemap_joined_by_the_tenant_wide_id():
    scraper = _scraper(_route(), gated=False)
    scraper.fetch_raw()
    asked = [url.rsplit("/", 1)[1] for url in _job_pages_asked(scraper)]
    # careers lists 22342, 21806 and 12444; internalcareers 21806 again and 21234; timeslots none.
    # The internal portal's page did not redirect to a login, so its own id is read too.
    assert sorted(asked) == ["12444", "21234", "21234", "21806", "22342"]


def test_a_login_walled_portal_costs_one_request_not_one_per_posting():
    scraper = _scraper(_route({_INTERNAL_ONLY: _TO_LOGIN}), gated=False)
    scraper.fetch_raw()
    asked = [url.rsplit("/", 1)[1] for url in _job_pages_asked(scraper)]
    assert sorted(asked) == [
        "12444",
        "21234",
        "21806",
        "22342",
    ]  # 21234 once: the probe
    assert "not public (closed or login-walled)" not in scraper.detail_losses


def test_a_shared_id_keeps_the_public_portals_url():
    scraper = _scraper(_route(), gated=False)
    scraper.fetch_raw()
    [shared] = [url for url in _job_pages_asked(scraper) if url.endswith("/21806")]
    assert "/careers/JobDetail/" in shared


def test_the_slug_gate_fetches_only_tech_job_pages():
    scraper = _scraper(_route({_INTERNAL_ONLY: _TO_LOGIN}))
    scraper.fetch_raw()
    # Plus one probe of the internal portal, which settles it as login-walled.
    assert sorted(_job_pages_asked(scraper)) == sorted([_TECH, _PDM, _INTERNAL_ONLY])


def test_a_tech_job_page_becomes_a_job_with_its_own_fields():
    scraper = _scraper(_route())
    jobs = {job.id: job for job in scraper.parse(scraper.fetch_raw(), _SCRAPED_AT)}
    assert set(jobs) == {"avature:bloomberg:22342", "avature:bloomberg:12444"}
    job = jobs["avature:bloomberg:22342"]
    assert job.title == "Senior Software Engineer - VAULT"
    assert job.url == _TECH
    assert job.location
    assert job.department == "Engineering and CTO"
    assert job.description and len(job.description) > 500


def test_the_company_is_the_name_the_job_pages_state():
    scraper = _scraper(_route())
    scraper.fetch_raw()
    scraper.resolve_company()
    assert scraper.company == "Bloomberg"


def test_a_job_page_landing_on_error_is_a_closed_posting_not_a_lost_detail():
    closed = FakeResponse(
        302, "", headers={"location": "https://bloomberg.avature.net/careers/Error"}
    )
    scraper = _scraper(_route({_TECH: closed}))
    raw = scraper.fetch_raw()
    assert [job.id for job in scraper.parse(raw, _SCRAPED_AT)] == [
        "avature:bloomberg:12444"
    ]
    assert scraper.detail_losses == {"not public (closed or login-walled)": 1}
    assert scraper.truncated is None


def test_a_job_page_landing_on_login_is_skipped():
    walled = FakeResponse(
        302,
        "",
        headers={"location": "https://bloomberg.avature.net/internalcareers/Login/"},
    )
    scraper = _scraper(_route({_TECH: walled}))
    jobs = scraper.parse(scraper.fetch_raw(), _SCRAPED_AT)
    assert [job.id for job in jobs] == ["avature:bloomberg:12444"]


def test_a_failed_job_page_is_a_labelled_loss_that_truncates_the_board():
    scraper = _scraper(_route({_TECH: FakeResponse(500, "")}))
    raw = scraper.fetch_raw()
    assert [job.id for job in scraper.parse(raw, _SCRAPED_AT)] == [
        "avature:bloomberg:12444"
    ]
    assert scraper.detail_losses == {"HTTP 500": 1}
    assert scraper.truncated


def test_a_job_page_moved_to_another_job_page_is_followed():
    # cyclecarriage's sitemap names `/en_US/careers/...` URLs that 302 to `/careers/...`.
    moved = FakeResponse(302, "", headers={"location": _TECH})
    localised = _TECH.replace("avature.net/careers/", "avature.net/en_US/careers/")
    route = _route({localised: moved})
    fx_sitemap = _FIXTURE["sitemap"][
        "https://bloomberg.avature.net/careers/sitemap.xml"
    ]

    def localised_route(method, url, kwargs):
        if url == "https://bloomberg.avature.net/careers/sitemap.xml":
            return FakeResponse(200, fx_sitemap.replace(_TECH, localised))
        return route(method, url, kwargs)

    scraper = _scraper(localised_route)
    jobs = {job.id: job for job in scraper.parse(scraper.fetch_raw(), _SCRAPED_AT)}
    assert jobs["avature:bloomberg:22342"].title == "Senior Software Engineer - VAULT"
    assert not scraper.detail_losses


def test_job_pages_are_fetched_without_cookies():
    scraper = _scraper(_route())
    scraper.fetch_raw()
    asked = [r for r in scraper.fake.requests if "/JobDetail/" in r.url]
    assert asked and all(r.kwargs.get("discard_cookies") is True for r in asked)


def test_listing_rows_skip_pages_that_are_not_postings():
    rows = listing_rows(
        _FIXTURE["sitemap"]["https://bloomberg.avature.net/careers/sitemap.xml"]
    )
    assert {row["id"] for row in rows} == {"22342", "21806", "12444"}
    assert rows[0]["slug_title"] == "Senior Software Engineer VAULT"


def test_json_ld_layout():
    fields = page_fields(_FIXTURE["layouts"]["ashfieldhealthcare_careers"])
    assert fields["title"] == "Medical Scientific Liaison Manager (m/w/d)"
    assert fields["company"] == "Ashfield Healthcare"
    assert fields["posted_at"] == "2026-04-29"
    assert fields["location"]  # "Región", a label in the tenant's own language
    assert fields["description"]


def test_open_graph_only_layout():
    fields = page_fields(_FIXTURE["layouts"]["astellasjapan_careers"])
    assert fields["title"].endswith(
        "Analytical Development Lead, Pharmaceutical Developability"
    )
    assert fields["location"] == "Tsukuba, Ibaraki"  # 勤務地
    assert fields["description"]


def test_a_title_stated_only_as_a_label():
    fields = page_fields(_FIXTURE["layouts"]["bradyplus_careersmarketplace"])
    assert (
        fields["title"] == "Inventory Control Specialist"
    )  # og:title empty; "Name" states it


def test_url_shape_matches_every_job_url():
    scraper = _scraper(_route(), gated=False)
    for row in scraper.fetch_raw():
        assert re.fullmatch(scraper.url_shape, scraper.job_url(row["url"]))


def test_a_robots_txt_naming_no_sitemap_is_an_unreadable_board_not_an_empty_one():
    def route(method, url, kwargs):
        return FakeResponse(200, "<html><body>Maintenance</body></html>")

    scraper = _scraper(route)
    assert scraper.fetch_raw() == []
    assert scraper.truncated


def test_portals_listing_no_job_pages_are_an_empty_board():
    # intuit: its portals' sitemaps list profile and login pages, no JobDetail.
    robots = "Sitemap: https://intuit.avature.net/externalCareers/sitemap_index.xml\n"
    index = (
        "<sitemapindex><sitemap><loc>https://intuit.avature.net/en_US/externalCareers/"
        "sitemap.xml</loc></sitemap></sitemapindex>"
    )
    sitemap = "<urlset><url><loc>https://intuit.avature.net/en_US/externalCareers/Login</loc></url></urlset>"

    def route(method, url, kwargs):
        if url.endswith("robots.txt"):
            return FakeResponse(200, robots)
        return FakeResponse(200, index if url.endswith("_index.xml") else sitemap)

    scraper = get_scraper(
        "avature", "intuit", fetcher=FakeFetcher(route), have_details=set()
    )
    scraper.pacer = Pacer(0)
    assert scraper.fetch_raw() == []
    assert scraper.truncated is None


def test_a_sitemap_two_portals_share_is_read_once():
    # L'Oréal: every portal's index redirects to one shared index.
    scraper = _scraper(_route())
    shared = "https://bloomberg.avature.net/careers/sitemap.xml"
    index = _FIXTURE["index"]["careers"]
    scraper.fake.route = lambda m, u, k: (
        FakeResponse(200, index)
        if u.endswith("sitemap_index.xml")
        else _route()(m, u, k)
    )
    scraper.fetch_raw()
    assert scraper.fake.urls().count(shared) == 1
