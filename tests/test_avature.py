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


#: ea's `es_ES` sitemap entry for one posting (2026-09-29), trimmed to three of its 12 alternates.
_EA_ES_ENTRY = (
    '<urlset xmlns:xhtml="http://www.w3.org/1999/xhtml"><url>'
    "<loc>https://jobs.ea.com/es_ES/careers/JobDetail/Software-Engineer/215808</loc>"
    '<xhtml:link rel="alternate" href="https://jobs.ea.com/en_US/careers/JobDetail/'
    'Software-Engineer/215808" hreflang="x-default"/>'
    '<xhtml:link rel="alternate" href="https://jobs.ea.com/en_US/careers/JobDetail/'
    'Software-Engineer/215808" hreflang="en-US"/>'
    '<xhtml:link rel="alternate" href="https://jobs.ea.com/es_ES/careers/JobDetail/'
    'Software-Engineer/215808" hreflang="es-ES"/>'
    "</url></urlset>"
)
_EA_EN = "https://jobs.ea.com/en_US/careers/JobDetail/Software-Engineer/215808"


def test_a_posting_is_read_at_its_english_alternate_when_the_english_sitemap_is_empty():
    """ea's `en_US` sitemap answered 200 with an empty body and its `es_ES` one did not, so the
    postings were served from Spanish pages that failed the English gate (#706)."""
    robots = "Sitemap: https://jobs.ea.com/careers/sitemap_index.xml\n"
    index = (
        "<sitemapindex>"
        "<sitemap><loc>https://jobs.ea.com/en_US/careers/sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://jobs.ea.com/es_ES/careers/sitemap.xml</loc></sitemap>"
        "</sitemapindex>"
    )

    def route(method, url, kwargs):
        if url.endswith("robots.txt"):
            return FakeResponse(200, robots)
        if url.endswith("sitemap_index.xml"):
            return FakeResponse(200, index)
        if url == "https://jobs.ea.com/es_ES/careers/sitemap.xml":
            return FakeResponse(200, _EA_ES_ENTRY)
        if url == _EA_EN:
            return FakeResponse(200, _FIXTURE["page"][_TECH])
        return FakeResponse(200, "")  # the empty `en_US` sitemap, and any other page

    scraper = get_scraper(
        "avature", "ea", fetcher=FakeFetcher(route), have_details=set()
    )
    scraper.pacer = Pacer(0)
    [job] = scraper.parse(scraper.fetch_raw(), _SCRAPED_AT)
    assert job.url == _EA_EN


def test_a_posting_with_no_english_alternate_or_already_in_english_keeps_its_url():
    only_spanish = (
        "<urlset><url><loc>https://manpowergroupco.avature.net/es_CO/careers/JobDetail/"
        "ASESOR-SVR/57090</loc></url></urlset>"
    )
    british = _EA_ES_ENTRY.replace(
        "/es_ES/careers/JobDetail/", "/en_GB/careers/JobDetail/"
    )
    [colombian] = listing_rows(only_spanish)
    [kept] = listing_rows(british)
    assert "/es_CO/" in colombian["url"]
    assert "/en_GB/" in kept["url"]


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


def test_the_requisition_is_the_pages_reference_label():
    """Bloomberg's "Ref #", Brady's "Ref #" and Ashfield's "ID de la vacante" state the
    requisition; it was never read."""
    assert page_fields(_FIXTURE["page"][_TECH])["requisition"] == "10054136"
    layouts = _FIXTURE["layouts"]
    assert page_fields(layouts["bradyplus_careersmarketplace"])["requisition"] == "3053"
    assert page_fields(layouts["ashfieldhealthcare_careers"])["requisition"] == "23234"
    assert page_fields(layouts["astellasjapan_careers"])["requisition"] is None


# --- an empty sitemap body is not an empty listing -------------------------------------------

_EMPTY_ROBOTS = (
    "Sitemap: https://acme.avature.net/careers/sitemap_index.xml\n"
    "Sitemap: https://acme.avature.net/CalendarInvitation/sitemap_index.xml\n"
)
_EMPTY_INDEX = (
    "<sitemapindex><sitemap><loc>https://acme.avature.net/careers/sitemap.xml</loc>"
    "</sitemap></sitemapindex>"
)


def _empty_listing_scraper(search_jobs: FakeResponse):
    """A tenant whose sitemaps all answer an empty 200 body; its search page is ``search_jobs``."""

    def route(method, url, kwargs):
        if url.endswith("robots.txt"):
            return FakeResponse(200, _EMPTY_ROBOTS)
        if url == "https://acme.avature.net/careers/sitemap_index.xml":
            return FakeResponse(200, _EMPTY_INDEX)
        if url == "https://acme.avature.net/careers/SearchJobs":
            return search_jobs
        return FakeResponse(
            200, ""
        )  # every sitemap: the empty body Avature answers at random

    scraper = get_scraper(
        "avature", "acme", fetcher=FakeFetcher(route), have_details=set()
    )
    scraper.pacer = Pacer(0)
    return scraper


def test_an_empty_sitemap_body_over_a_portal_that_lists_postings_is_unread():
    """mantech (420 served rows) read one such run in 31: two in a row evict every row."""
    from headstart.ingest import board_failures
    from headstart.scrapers.base import BoardUnreadable

    page = (
        '<a href="https://acme.avature.net/careers/JobDetail/Engineer/123">Engineer</a>'
    )
    scraper = _empty_listing_scraper(FakeResponse(200, page))
    try:
        scraper.fetch_raw()
    except BoardUnreadable as exc:
        assert "unread, not empty" in str(exc)
        assert not board_failures.is_gone(f"{type(exc).__name__}: {exc}")
    else:
        raise AssertionError("an empty sitemap body read as an empty Board")


def test_empty_sitemaps_under_a_search_page_with_no_postings_are_an_empty_board():
    """A utility portal answers an empty body every time; its search page 404s or links none."""
    for search in (FakeResponse(404, ""), FakeResponse(200, "<p>0 results</p>")):
        assert _empty_listing_scraper(search).fetch_raw() == []


def test_a_well_formed_sitemap_with_no_postings_never_asks_the_search_page():
    def route(method, url, kwargs):
        if url.endswith("robots.txt"):
            return FakeResponse(200, _EMPTY_ROBOTS)
        if url.endswith("sitemap_index.xml"):
            return FakeResponse(200, _EMPTY_INDEX)
        if url.endswith("SearchJobs"):
            raise AssertionError(
                "the search page was asked for a well-formed empty listing"
            )
        return FakeResponse(200, "<urlset></urlset>")

    scraper = get_scraper(
        "avature", "acme", fetcher=FakeFetcher(route), have_details=set()
    )
    scraper.pacer = Pacer(0)
    assert scraper.fetch_raw() == []


def test_a_page_title_wrapped_in_chrome_reads_the_json_ld_title():
    """#876: metlife's `og:title` ends in a call to action; its JSON-LD states the bare title."""
    from headstart.scrapers.avature import _page_title

    assert (
        _page_title(
            "Principal Data and AI Product Engineer | Apply Now",
            "Principal, Data and AI Product Engineer",
        )
        == "Principal, Data and AI Product Engineer"
    )
    # A title that is the employer's own keeps it: no JSON-LD title, or the same one.
    assert _page_title("Developer | Equities Algorithmic Trading", "") == (
        "Developer | Equities Algorithmic Trading"
    )
    same = "Engine Overhaul Engineer III - TE.01 | EEMC"
    assert _page_title(same, same) == same
    assert _page_title(None, "Data Engineer") == "Data Engineer"
