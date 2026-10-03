"""Tests for the Avature scraper (headstart.scrapers.avature).

The fixture is real captures from `bloomberg.avature.net` (2026-09-26) — its robots.txt, three
portals' sitemap indexes and sitemaps trimmed to the rows named below, and one job page — plus
one job page from each of three other tenants, for the page layouts Bloomberg's does not use.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.ingest import board_failures
from headstart.scrapers.avature import (
    AvatureScraper,
    _page_title,
    _robots_allows,
    listing_rows,
    next_search_page,
    page_fields,
    search_rows,
)
from headstart.scrapers.base import BoardUnreadable
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
    assert rows[0]["listed_title"] == "Senior Software Engineer VAULT"


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
    jobs = scraper.parse(scraper.fetch_raw(), _SCRAPED_AT)
    assert [job.url for job in jobs] == [_EA_EN]
    assert scraper.truncated is None  # `es_ES` answered, so the portal was read


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


#: tsmc's `de_DE` sitemap entry for a posting titled "製程整合工程師 (台南)" (2026-09-29): a title
#: with no Latin letter mints no slug, as 28 of its 801 JobDetail URLs have none.
_TSMC_NO_SLUG_ENTRY = (
    '<urlset xmlns:xhtml="http://www.w3.org/1999/xhtml"><url>'
    "<loc>https://careers.tsmc.com/de_DE/careers/JobDetail/389</loc>"
    '<xhtml:link rel="alternate" href="https://careers.tsmc.com/en_US/careers/JobDetail/389" '
    'hreflang="x-default"/>'
    '<xhtml:link rel="alternate" href="https://careers.tsmc.com/de_DE/careers/JobDetail/389" '
    'hreflang="de-DE"/>'
    '<xhtml:link rel="alternate" href="https://careers.tsmc.com/en_US/careers/JobDetail/389" '
    'hreflang="en-US"/>'
    "</url></urlset>"
)


def test_a_job_url_with_no_title_slug_is_listed():
    """28 of tsmc's 801 postings were never listed: the URL pattern required a slug."""
    rows = listing_rows(_TSMC_NO_SLUG_ENTRY)
    assert rows == [
        {
            "id": "389",
            "url": "https://careers.tsmc.com/en_US/careers/JobDetail/389",
            "listed_title": "",
        }
    ]
    scraper = _scraper(_route())
    assert re.fullmatch(scraper.url_shape, rows[0]["url"])


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
_CAREERS_INDEX = (
    "<sitemapindex><sitemap><loc>https://acme.avature.net/careers/sitemap.xml</loc>"
    "</sitemap></sitemapindex>"
)
_CAREERS_SEARCH = "https://acme.avature.net/careers/SearchJobs"
_LINKS_A_POSTING = (
    '<a href="https://acme.avature.net/careers/JobDetail/Engineer/123">Engineer</a>'
)
#: Where an internal portal's search page lands (emiratesjobs's `HiringManager`, 2026-09-29).
_AT_LOGIN = FakeResponse(
    200, "<form>Sign in</form>", url="https://acme.avature.net/careers/Login/"
)


def _acme(routes: dict[str, FakeResponse], robots: str = _EMPTY_ROBOTS):
    """A tenant whose every sitemap answers the empty 200 body Avature answers at random, unless
    ``routes`` answers the URL. A search page ``routes`` does not name is gone (404), as a
    utility portal's (`CalendarInvitation`) is."""

    def route(method, url, kwargs):
        if url.endswith("robots.txt"):
            return FakeResponse(200, robots)
        if url in routes:
            return routes[url]
        if url == "https://acme.avature.net/careers/sitemap_index.xml":
            return FakeResponse(200, _CAREERS_INDEX)
        if url.endswith("/SearchJobs"):
            return FakeResponse(404, "")
        return FakeResponse(200, "")

    fetcher = FakeFetcher(route)
    scraper = get_scraper("avature", "acme", fetcher=fetcher, have_details=set())
    scraper.pacer = Pacer(0)
    scraper.fake = fetcher
    return scraper


def test_an_empty_sitemap_body_over_a_portal_that_lists_postings_is_unread():
    """mantech (420 served rows) read one such run in 31: two in a row evict every row."""
    scraper = _acme({_CAREERS_SEARCH: FakeResponse(200, _LINKS_A_POSTING)})
    with pytest.raises(BoardUnreadable, match="unread, not empty") as raised:
        scraper.fetch_raw()
    assert not board_failures.is_gone(f"{type(raised.value).__name__}: {raised.value}")


@pytest.mark.parametrize("status", [406, 429, 503, 403, 202])
def test_a_search_page_that_did_not_answer_leaves_an_empty_listing_unread(status):
    """#880's check read only a 200: Avature's own 406 wall, a 429 or a 5xx read as a 404 did, so
    the Board read empty and entered the eviction scope."""
    scraper = _acme({_CAREERS_SEARCH: FakeResponse(status, "")})
    with pytest.raises(BoardUnreadable, match=f"HTTP {status}"):
        scraper.fetch_raw()


@pytest.mark.parametrize(
    "search",
    [
        # maximus's `careers`: "402 results", rendered client-side, so no link on the page.
        FakeResponse(200, "<p>402 results</p>"),
        # emiratesjobs's `careersmarketplace` hands off to the employer's own site.
        FakeResponse(
            200, "<p>Search and apply</p>", url="https://www.acme.com/search-and-apply/"
        ),
    ],
)
def test_a_search_page_that_is_neither_gone_nor_a_login_leaves_an_empty_listing_unread(
    search,
):
    """emiratesjobs (215 served rows) read 0 postings on 2026-09-29: its job portal's 1,470-
    posting sitemap answered empty, and its search page, which links none, read as an answer."""
    scraper = _acme({_CAREERS_SEARCH: search})
    with pytest.raises(BoardUnreadable, match="which is no login"):
        scraper.fetch_raw()


@pytest.mark.parametrize(
    "search", [FakeResponse(404, ""), FakeResponse(410, ""), _AT_LOGIN]
)
def test_empty_sitemaps_under_a_gone_or_login_search_page_are_an_empty_board(search):
    """A utility portal reads empty every run; its search page 404s (mantech's `veterans`,
    maximus's `CalendarInvitation`) or lands on a login (emiratesjobs's `HiringManager`)."""
    scraper = _acme({_CAREERS_SEARCH: search})
    assert scraper.fetch_raw() == []
    assert scraper.truncated is None


def test_a_well_formed_sitemap_with_no_postings_never_asks_the_search_page():
    scraper = _acme(
        {
            "https://acme.avature.net/careers/sitemap.xml": FakeResponse(
                200, "<urlset></urlset>"
            ),
            "https://acme.avature.net/CalendarInvitation/sitemap_index.xml": FakeResponse(
                200, "<sitemapindex></sitemapindex>"
            ),
        }
    )
    assert scraper.fetch_raw() == []
    assert not [url for url in scraper.fake.urls() if url.endswith("SearchJobs")]


#: A second portal that lists a posting while `careers` reads empty (deloitteus's `careersDOT`).
_SIBLING_ROBOTS = (
    _EMPTY_ROBOTS + "Sitemap: https://acme.avature.net/contractors/sitemap_index.xml\n"
)
_SIBLING = {
    "https://acme.avature.net/contractors/sitemap_index.xml": FakeResponse(
        200,
        "<sitemapindex><sitemap><loc>https://acme.avature.net/contractors/sitemap.xml"
        "</loc></sitemap></sitemapindex>",
    ),
    "https://acme.avature.net/contractors/sitemap.xml": FakeResponse(
        200,
        "<urlset><url><loc>https://acme.avature.net/contractors/JobDetail/"
        "Data-Engineer/77</loc></url></urlset>",
    ),
}


def test_a_portal_read_empty_beside_one_that_lists_postings_truncates_the_board():
    """deloitteus's `careers` sitemap read 0 bytes twice, then 1,304 ids, while `careersDOT`
    listed 45: the 45 read as the whole Board, and two such runs evict the rest."""
    scraper = _acme(
        {**_SIBLING, _CAREERS_SEARCH: FakeResponse(200, _LINKS_A_POSTING)},
        robots=_SIBLING_ROBOTS,
    )
    assert [row["id"] for row in scraper.fetch_raw()] == ["77"]
    assert "links postings" in scraper.truncated


def test_a_utility_portal_read_empty_beside_one_that_lists_postings_leaves_it_authoritative():
    """ea's `CalendarInvitation` reads empty every run; its search page 404s."""
    careers = {
        "https://acme.avature.net/careers/sitemap.xml": FakeResponse(
            200, "<urlset></urlset>"
        ),
        "https://acme.avature.net/CalendarInvitation/SearchJobs": FakeResponse(404, ""),
    }
    scraper = _acme({**_SIBLING, **careers}, robots=_SIBLING_ROBOTS)
    assert [row["id"] for row in scraper.fetch_raw()] == ["77"]
    assert scraper.truncated is None


def test_a_page_title_wrapped_in_chrome_reads_the_json_ld_title():
    """#876: metlife's `og:title` ends in a call to action; its JSON-LD states the bare title."""
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


def test_a_call_to_action_leaves_the_title_whatever_the_json_ld_says():
    """15 of metlife's 80 served titles kept "| Apply Now": their JSON-LD title is worded
    otherwise, or does not parse (captured pages, 2026-09-29)."""
    layouts = _FIXTURE["layouts"]
    assert page_fields(layouts["metlife_ml"])["title"] == "Head of Platform"
    assert (
        page_fields(layouts["metlife_ml_unparsed_json_ld"])["title"]
        == "IT Infrastructure L2\\L3 Engineer"
    )
    assert _page_title("Ingénieur logiciel | Postuler", "") == "Ingénieur logiciel"


def test_a_details_block_holding_only_css_falls_through_to_the_collapsible_sections():
    """#876's own Board: workmyway's details block is one `<style>` block, so every served row's
    description was CSS, and once `<style>` was dropped it was None."""
    description = page_fields(_FIXTURE["layouts"]["workmyway_careers"])["description"]
    assert description.startswith("Role Highlights")
    assert "We are looking for an experienced Engineering Manager" in description
    assert "{" not in description


# --- employment type: the labels and markup shapes tenants state it in ------------------------

#: Synthetic rows in the shape of dhlconsulting's and lenovo's real ones, for the label table.
_ROW = (
    '<div class="article__content__view__field "> '
    '<div class="article__content__view__field__label"> {label} </div> '
    '<div class="article__content__view__field__value"> {value} </div> </div>'
)


def _page_with(*pairs: tuple[str, str]) -> str:
    rows = " ".join(_ROW.format(label=k, value=v) for k, v in pairs)
    return f"<html><body><article>{rows}</article></body></html>"


_LABEL_FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent
        / "fixtures"
        / "avature_employment_type_labels.json"
    ).read_text()
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bravura_individual_contractor", "Individual Contractor"),  # fieldSet
        ("bravura_permanent", "Permanent"),  # fieldSet
        ("colorado_faculty", "Faculty"),  # fieldSet; its "Schedule" says Full-Time
        ("lululemoninc_time_type", "Full-time"),  # data-map="item-title"
        ("astellasjapan_employment_class", "Permanent"),
        ("dhlconsulting_working_time", "Full-time"),
        ("lenovo_working_time", "Full-time"),
    ],
)
def test_employment_type_is_read_from_real_pages_the_reader_missed(name, expected):
    """Each fixture is the label rows of a captured job page (2026-09-29, blank-type before)."""
    page = f"<html><body>{_LABEL_FIXTURE[name]}</body></html>"
    assert page_fields(page)["employment_type"] == expected


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("Working time", "Full-time"),
        ("Working Pattern", "Full time"),
        ("Working Schedule", "Full time"),
        ("Position Type", "Contract (12-18 month contract)"),
        ("Position Type", "Employee Regular"),
        ("Position Type", "Full Time"),
        ("Type of Contract", "Permanent"),
        ("Employment Class", "Permanent"),
        ("Full-time/Part-time", "Full Time, Part Time, Part Time/Job Share"),
        ("Post Type", "Regular"),
        ("Pay Class", "Regular Full-Time"),
        ("Hire Type", "Temporary"),
        ("Hire Type", "Employee"),
        ("Job Type", "Full Time"),
        ("Job Type", "Fixed Term"),
    ],
)
def test_a_type_label_states_the_employment_type(label, value):
    fields = page_fields(_page_with((label, value)))
    assert fields["employment_type"] == value


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("Job Type", "Experienced"),
        ("Job Type", "Store Support Centre"),
        ("Job Type", "Non Consulting"),
        ("Position Type", "Professional"),
        ("Post Type", "Internal"),
        ("Working time", "40 hours per week"),
        ("Working time", "Rotation"),
        ("Hours", "Full time role, 40 hours per week."),
        ("Scheduled Weekly Hours", "40"),
        ("Core hours", "9:00 AM - 4:30 PM (Mon-Fri)"),
    ],
)
def test_a_label_that_is_not_an_employment_type_states_none(label, value):
    assert page_fields(_page_with((label, value)))["employment_type"] is None


def test_an_employment_type_label_beats_a_job_type_that_looks_like_one():
    page = _page_with(("Job Type", "Full Time"), ("Employment Type", "Permanent"))
    assert page_fields(page)["employment_type"] == "Permanent"


# --- a location the page states outside the label rows (ADR-0345) -----------------------------
# Each snippet is the markup of a real job page read on 2026-09-29, trimmed to the place.


def _page_titled(title: str, og_title: str, body: str = "") -> str:
    return (
        f"<html><head><title>{title}</title>"
        f'<meta property="og:title" content="{og_title}"></head>'
        f"<body><article>{body}</article></body></html>"
    )


def test_deloitte_lists_every_place_a_job_is_available_in():
    body = (
        '<a class="link toggleLocations toggleLocations--show">'
        "Same job available in 3 locations</a>"
        '<div class="article__header--locations article__header--locations-none" >'
        '<div class="fluid-cols fluid-cols--cols2">'
        '<p class="paragraph">Atlanta, Georgia, United States</p>'
        '<p class="paragraph">Austin, Texas, United States</p>'
        '<p class="paragraph">Baltimore, Maryland, United States</p>'
        "</div></div>"
    )
    page = _page_titled("Architect Manager - - 366896", "Architect Manager", body)
    assert page_fields(page)["location"] == (
        "Atlanta, Georgia, United States; Austin, Texas, United States; "
        "Baltimore, Maryland, United States"
    )


def test_the_field_avature_calls_location_is_read_under_any_label():
    # macquarie labels it "Additional office locations"
    body = (
        '<div class="article__content__view__field field--location '
        'regular-field-label--hidden">'
        '<div class="article__content__view__field__label">'
        "Additional office locations</div>"
        '<div class="article__content__view__field__value"> Sydney </div></div>'
    )
    assert page_fields(_page_titled("Engineer - - 23759", "Engineer", body))[
        "location"
    ] == ("Sydney")


def test_a_location_field_written_as_one_rich_text_value_loses_its_label_and_extra_rows():
    # electronic arts: the label sits inside the value, and further places follow as a list
    body = (
        '<div class="article__content__view__field field--locations regular-fields">'
        '<div class="article__content__view__field__value">'
        "<strong>Locations</strong>: Vancouver, British Columbia, Canada&nbsp; "
        '<ul class="MultipleDataSetFields"><li class="MultipleDataSetField">'
        '<span class="MultipleDataSetFieldLabel">Location:</span> '
        '<span class="MultipleDataSetFieldValue">Toronto</span></li></ul>'
        "</div></div>"
    )
    page = _page_titled("Engineer - EA SPORTS NHL - 215808", "Engineer", body)
    assert page_fields(page)["location"] == "Vancouver, British Columbia, Canada"


@pytest.mark.parametrize(
    ("title", "og_title", "place"),
    [
        (
            "Software Engineer II - Kuala Lumpur, Malaysia - 19849 - MetLife",
            "Software Engineer II | Apply Now",
            "Kuala Lumpur, Malaysia",
        ),
        (
            "Field Engineer - Maryland, Columbia - 3080",
            "Field Engineer",
            "Maryland, Columbia",
        ),
        (
            "Windows Consultant - Charlotte, NC - WorkMyWay",
            "Windows Consultant",
            "Charlotte, NC",
        ),
        (
            "Sr. Process Engineer - Columbia, South Carolina, United States - 1037 - AESC",
            "Sr. Process Engineer",
            "Columbia, South Carolina, United States",
        ),
    ],
)
def test_the_place_avature_puts_in_the_page_title_is_the_location(
    title, og_title, place
):
    assert page_fields(_page_titled(title, og_title))["location"] == place


@pytest.mark.parametrize(
    ("title", "og_title"),
    [
        # the slot is empty
        ("Front Office Engineer - - 23759 - Macquarie Group", "Front Office Engineer"),
        # a subtitle of the title, not a place
        (
            "Game Modes Engineer - EA SPORTS NHL - 215808 - Electronic Arts",
            "Game Modes Engineer - EA SPORTS NHL",
        ),
        ("Engineer - Python, SQL - 123 - Acme", "Engineer"),
        # no separator after the title
        (
            "Lead Engineer - Job detail | Careers Portal - TotalEnergies",
            "Lead Engineer",
        ),
    ],
)
def test_a_page_title_that_names_no_place_states_no_location(title, og_title):
    assert page_fields(_page_titled(title, og_title))["location"] is None


def test_a_definition_list_states_the_location_and_nothing_else():
    # totalenergies: dt/dd rows. Only the place is read from them: their "Type of contract" would
    # otherwise move employment_type on rows this change does not concern.
    rows = "".join(
        f'<dl class="article__content__view__field ">'
        f'<dt class="article__content__view__field__label"> {k} </dt>'
        f'<dd class="article__content__view__field__value"> {v} </dd></dl>'
        for k, v in (
            ("Country", "Malaysia"),
            ("City", "KUALA LUMPUR"),
            ("Type of contract", "Regular position"),
        )
    )
    fields = page_fields(
        _page_titled("Lead Engineer - Job detail", "Lead Engineer", rows)
    )
    assert fields["location"] == "KUALA LUMPUR"
    assert fields["employment_type"] is None


def test_a_label_row_still_beats_every_other_surface():
    body = (
        '<div class="article__content__view__field__label">Location</div>'
        '<div class="article__content__view__field__value">Pune, India</div>'
        '<p class="paragraph">Mumbai, India</p>'
    )
    page = _page_titled("Engineer - Berlin, Germany - 12", "Engineer", body)
    assert page_fields(page)["location"] == "Pune, India"


# --- a portal whose sitemap lists no posting is read from its search pages -------------------

_SEARCH_PAGES = json.loads(
    (
        pathlib.Path(__file__).parent / "fixtures" / "avature_search_pages.json"
    ).read_text()
)
#: An Avature tenant's robots.txt: every portal it lists is whitelisted, then everything else
#: is disallowed. Siemens's `/en_US/externaljobs/SearchJobs` is reached under the locale rule.
_WHITELIST_ROBOTS = (
    "User-agent: *\n"
    "Allow: /$\n"
    "Allow: /careers\n"
    "Disallow: /careers/*qtvc=\n"
    "Allow: /*/careers\n"
    "Disallow: /*/careers/*qtvc=\n"
    "Sitemap: https://acme.avature.net/careers/sitemap_index.xml\n"
    "Allow: /CheckIn\n"
    "Sitemap: https://acme.avature.net/CheckIn/sitemap_index.xml\n"
    "Disallow: /"
)
#: What Siemens's and Two Sigma's own sitemaps list: page names, among them a bare `/JobDetail`,
#: and not one posting.
_JOB_PORTAL_SITEMAP = (
    "<urlset>"
    "<url><loc>https://acme.avature.net/careers/JobDetail</loc></url>"
    "<url><loc>https://acme.avature.net/careers/JobDetailApplied</loc></url>"
    "<url><loc>https://acme.avature.net/careers/Login</loc></url>"
    "</urlset>"
)
#: What Epic's utility portals list: no job page at all.
_UTILITY_SITEMAP = (
    "<urlset><url><loc>https://acme.avature.net/CheckIn/Login</loc></url></urlset>"
)
_SEARCH = "https://acme.avature.net/careers/SearchJobs"
_LOCALE_SEARCH = "https://acme.avature.net/en_US/careers/SearchJobs/"


def _result(job_id: str, title: str) -> str:
    """One result as Siemens's and a2milkkf's search pages state it, trimmed to its header."""
    return (
        '<article class="article article--result"><div class="article__header">'
        '<div class="article__header__text">'
        '<h3 class="article__header__text__title title title--h3">'
        f'<a class="link" href="https://acme.avature.net/en_US/careers/JobDetail/{job_id}">'
        f"{title}</a></h3></div></div></article>"
    )


def _search_page(results: list[tuple[str, str]], *, total: str, next_page: str = ""):
    paging = (
        '<li class="list-controls__pagination__item paginationNextLink">'
        f'<a href="{next_page}" aria-label="Go to Next Page">Next &gt;&gt;</a></li>'
        if next_page
        else ""
    )
    body = "".join(_result(job_id, title) for job_id, title in results)
    return f'<div class="list-controls__text">{total}</div>{body}{paging}'


def _page_two(offset: int) -> str:
    return f"{_LOCALE_SEARCH}?folderRecordsPerPage=2&folderOffset={offset}"


def _job_page(title: str) -> str:
    return f'<meta property="og:title" content="{title}"><main>{title} at Acme</main>'


def _portal(routes: dict[str, FakeResponse], robots: str = _WHITELIST_ROBOTS):
    """A tenant whose `careers` sitemap lists page names and no posting, and whose utility
    portal lists nothing; ``routes`` answers the search and job pages."""

    def route(method, url, kwargs):
        if url.endswith("robots.txt"):
            return FakeResponse(200, robots)
        if url in routes:
            return routes[url]
        if url.endswith("/careers/sitemap_index.xml"):
            return FakeResponse(
                200,
                "<sitemapindex><sitemap><loc>https://acme.avature.net/en_US/careers/"
                "sitemap.xml</loc></sitemap></sitemapindex>",
            )
        if url.endswith("/CheckIn/sitemap_index.xml"):
            return FakeResponse(
                200,
                "<sitemapindex><sitemap><loc>https://acme.avature.net/en_US/CheckIn/"
                "sitemap.xml</loc></sitemap></sitemapindex>",
            )
        if url.endswith("/careers/sitemap.xml"):
            return FakeResponse(200, _JOB_PORTAL_SITEMAP)
        if url.endswith("/CheckIn/sitemap.xml"):
            return FakeResponse(200, _UTILITY_SITEMAP)
        return FakeResponse(404, "")

    fetcher = FakeFetcher(route)
    scraper = get_scraper("avature", "acme", fetcher=fetcher, have_details=set())
    scraper.pacer = Pacer(0)
    scraper.fake = fetcher
    return scraper


def _asked(scraper, marker: str) -> list[str]:
    return [url for url in scraper.fake.urls() if marker in url]


_ENGINEER, _SALES, _DATA = (
    ("524237", "Software Engineer"),
    ("524234", "Sales Director"),
    ("524200", "Data Engineer"),
)


def _two_pages(total: str) -> dict[str, FakeResponse]:
    """Three results over two pages, then the tech ones' job pages."""
    return {
        _SEARCH: FakeResponse(
            200,
            _search_page([_ENGINEER, _SALES], total=total, next_page=_page_two(2)),
            url=_LOCALE_SEARCH,
        ),
        _page_two(2): FakeResponse(200, _search_page([_DATA], total=total)),
        "https://acme.avature.net/en_US/careers/JobDetail/524237": FakeResponse(
            200, _job_page("Software Engineer")
        ),
        "https://acme.avature.net/en_US/careers/JobDetail/524200": FakeResponse(
            200, _job_page("Data Engineer")
        ),
    }


def test_a_board_whose_sitemaps_list_no_posting_is_read_from_its_search_pages():
    """Siemens's sitemap lists 58 page URLs and no posting, while its search page states 999+
    results; every one of its URLs is `…/JobDetail/{id}`, with no slug to gate on."""
    scraper = _portal(_two_pages("1 - 2 of 3 results"))
    raw = scraper.fetch_raw()
    assert [item["id"] for item in raw] == ["524237", "524200"]
    # The gate ran on the title the search page states: no URL here carries a slug.
    assert sorted(_asked(scraper, "/JobDetail/")) == [
        "https://acme.avature.net/en_US/careers/JobDetail/524200",
        "https://acme.avature.net/en_US/careers/JobDetail/524237",
    ]
    assert scraper.truncated is None
    job = scraper.parse(raw, _SCRAPED_AT)[0]
    assert (job.id, job.title) == ("avature:acme:524237", "Software Engineer")


def test_folder_detail_templates_activate_search_and_read_the_postings():
    """Bain lists a bare FolderDetail template, then real jobs only on SearchJobs.

    Its page titles and pagination use the ordinary Avature layout. Reading the
    whole scraper catches both the template gate and the posting URL parser.
    """
    routes = {
        url.replace("JobDetail", "FolderDetail"): FakeResponse(
            response.status_code,
            response.text.replace("JobDetail", "FolderDetail"),
            url=response.url.replace("JobDetail", "FolderDetail"),
        )
        for url, response in _two_pages("1 - 2 of 3 results").items()
    }
    routes["https://acme.avature.net/en_US/careers/sitemap.xml"] = FakeResponse(
        200, _JOB_PORTAL_SITEMAP.replace("JobDetail", "FolderDetail")
    )
    scraper = _portal(routes)
    raw = scraper.fetch_raw()
    assert [row["id"] for row in raw] == ["524237", "524200"]
    assert scraper.truncated is None
    jobs = scraper.parse(raw, _SCRAPED_AT)
    assert [job.title for job in jobs] == ["Software Engineer", "Data Engineer"]
    assert all("/FolderDetail/" in job.url for job in jobs)
    assert all(re.fullmatch(scraper.url_shape, job.url) for job in jobs)


def _slugged(scraper, slugs: list[str]) -> None:
    """Answer the `careers` sitemap with one posting per slug, `…/JobDetail/{slug}/{n}`."""
    route = scraper.fake.route
    urls = "".join(
        f"<url><loc>https://acme.avature.net/en_US/careers/JobDetail/{slug}/{n}</loc></url>"
        for n, slug in enumerate(slugs, 1)
    )

    def slugged(method, url, kwargs):
        if url.endswith("/careers/sitemap.xml"):
            return FakeResponse(200, f"<urlset>{urls}</urlset>")
        return route(method, url, kwargs)

    scraper.fake.route = slugged


@pytest.mark.parametrize(
    "slugs",
    [
        # mt: 521 of 531 postings are `…/JobDetail/1/{id}`, the rest name a title
        ["1"] * 22 + ["Sales-Manager", "Field-Technician"],
        # ucsf: 81% of 949 are slugged by the posting's location
        ["San-Francisco-CA-United-States"] * 20 + [f"Oakland-{n}" for n in range(4)],
        # pomerleau: the slug is a number
        [str(1000 + n) for n in range(24)],
    ],
)
def test_a_sitemap_whose_slugs_state_no_title_is_read_from_its_search_pages(slugs):
    """The tech gate reads the slug, so these Boards' postings all read as non-tech and the Board
    as zero Jobs; their search pages state each title."""
    scraper = _portal(_two_pages("1 - 2 of 3 results"))
    _slugged(scraper, slugs)
    raw = scraper.fetch_raw()
    assert [item["id"] for item in raw] == ["524237", "524200"]
    assert scraper.truncated is None


def test_a_sitemap_whose_slugs_state_titles_never_asks_the_search_page():
    slugs = [f"Engineer-{n}" for n in range(24)]
    scraper = _portal(_two_pages("1 - 2 of 3 results"))
    _slugged(scraper, slugs)
    scraper.fetch_raw()
    assert not _asked(scraper, "SearchJobs")
    assert len(_asked(scraper, "/JobDetail/")) == 24  # the sitemap's own tech slugs


def test_a_small_board_repeating_one_title_is_still_read_from_its_sitemap():
    """Three postings all titled "Data Engineer" say nothing about their slugs: under 20 rows the
    share of the commonest one is not read."""
    scraper = _portal(_two_pages("1 - 2 of 3 results"))
    _slugged(scraper, ["Data-Engineer"] * 3)
    scraper.fetch_raw()
    assert not _asked(scraper, "SearchJobs")
    assert len(_asked(scraper, "/JobDetail/")) == 3


def test_a_sitemap_whose_slugs_state_no_title_keeps_its_rows_where_the_search_is_gone():
    scraper = _portal({_SEARCH: FakeResponse(404, "")})
    _slugged(scraper, ["1"] * 24)
    scraper.fetch_raw()
    assert not _asked(scraper, "/JobDetail/")  # the gate reads no title, as before
    assert scraper.truncated is None


def test_the_search_pages_are_read_one_request_each():
    scraper = _portal(_two_pages("1 - 2 of 3 results"))
    scraper.fetch_raw()
    assert _asked(scraper, "SearchJobs") == [_SEARCH, _page_two(2)]


def test_a_search_listing_that_states_a_capped_total_truncates_the_board():
    """Siemens's page says "999+ results" and stops serving pages past its 2,000th: the total
    is not stated, so a posting missing from the list is not known to be closed."""
    scraper = _portal(_two_pages("1 - 2 of 999+ results"))
    assert [item["id"] for item in scraper.fetch_raw()] == ["524237", "524200"]
    assert "999+" in scraper.truncated


def test_a_search_listing_read_short_of_its_stated_total_truncates_the_board():
    scraper = _portal(_two_pages("1 - 2 of 30 results"))
    scraper.fetch_raw()
    assert "30" in scraper.truncated


def test_a_search_listing_with_no_stated_total_is_read_to_its_last_page():
    scraper = _portal(_two_pages(""))
    assert [item["id"] for item in scraper.fetch_raw()] == ["524237", "524200"]
    assert scraper.truncated is None


def test_search_paging_stops_at_the_cap_and_truncates_the_board(monkeypatch):
    monkeypatch.setattr("headstart.scrapers.avature._SEARCH_PAGES_MAX", 2)
    routes = _two_pages("1 - 2 of 99 results")
    routes[_page_two(2)] = FakeResponse(
        200, _search_page([_DATA], total="", next_page=_page_two(3))
    )
    scraper = _portal(routes)
    scraper.fetch_raw()
    assert len(_asked(scraper, "SearchJobs")) == 2
    assert "cap" in scraper.truncated


def test_a_search_page_that_fails_after_the_first_truncates_the_board():
    routes = _two_pages("1 - 2 of 3 results")
    routes[_page_two(2)] = FakeResponse(503, "")
    scraper = _portal(routes)
    assert [item["id"] for item in scraper.fetch_raw()] == ["524237"]
    assert "503" in scraper.truncated


@pytest.mark.parametrize("status", [406, 429, 503, 202])
def test_a_first_search_page_that_did_not_answer_leaves_the_board_unread(status):
    scraper = _portal({_SEARCH: FakeResponse(status, "")})
    with pytest.raises(BoardUnreadable, match=f"HTTP {status}"):
        scraper.fetch_raw()


@pytest.mark.parametrize(
    "search",
    [
        # two sigma's `careers` has no SearchJobs page: its listing is a custom `OpenRoles` page
        FakeResponse(404, ""),
        FakeResponse(
            200, "<form>Sign in</form>", url="https://acme.avature.net/careers/Login/"
        ),
        # a search page stating no result
        FakeResponse(200, _search_page([], total="0 results"), url=_LOCALE_SEARCH),
    ],
)
def test_a_search_page_that_lists_nothing_leaves_the_board_empty(search):
    scraper = _portal({_SEARCH: search})
    assert scraper.fetch_raw() == []
    assert scraper.truncated is None


def test_a_portal_whose_sitemap_names_no_job_page_never_asks_its_search_page():
    """Epic's fifteen portals are onboarding and event pages: reading their search pages would
    cost fifteen requests a run to find nothing."""
    scraper = _portal({})
    route = scraper.fake.route

    def utility_only(method, url, kwargs):
        if url.endswith("/careers/sitemap.xml"):
            return FakeResponse(200, _UTILITY_SITEMAP)
        return route(method, url, kwargs)

    scraper.fake.route = utility_only
    assert scraper.fetch_raw() == []
    assert not _asked(scraper, "SearchJobs")


def test_a_portal_read_empty_is_settled_by_its_sitemap_path_not_the_search_listing():
    """An empty body over a portal whose search page links postings stays unread (#880): the
    listing is paged only for a sitemap that answered and listed no posting."""
    scraper = _portal({_SEARCH: FakeResponse(200, _LINKS_A_POSTING)})
    route = scraper.fake.route

    def empty_bodies(method, url, kwargs):
        if url.endswith("/careers/sitemap.xml"):
            return FakeResponse(200, "")
        return route(method, url, kwargs)

    scraper.fake.route = empty_bodies
    with pytest.raises(BoardUnreadable, match="unread, not empty"):
        scraper.fetch_raw()
    assert len(_asked(scraper, "SearchJobs")) == 1


def test_search_pages_are_not_asked_when_the_sitemaps_list_a_posting():
    scraper = _scraper(_route(), gated=False)
    scraper.fetch_raw()
    assert not [url for url in scraper.fake.urls() if "SearchJobs" in url]


def test_search_pages_are_not_read_where_robots_txt_disallows_them():
    robots = (
        "User-agent: *\n"
        "Sitemap: https://acme.avature.net/careers/sitemap_index.xml\n"
        "Disallow: /careers/SearchJobs\n"
    )
    scraper = _portal(_two_pages("1 - 2 of 3 results"), robots=robots)
    assert scraper.fetch_raw() == []
    assert not _asked(scraper, "SearchJobs")
    # a Board that may not be read is not a Board with nothing open
    assert "disallowed by robots.txt" in scraper.truncated


def test_a_later_search_page_that_robots_txt_disallows_truncates_the_board():
    robots = (
        "User-agent: *\n"
        "Sitemap: https://acme.avature.net/careers/sitemap_index.xml\n"
        "Disallow: /*folderOffset=2\n"
    )
    scraper = _portal(_two_pages("1 - 2 of 3 results"), robots=robots)
    scraper.fetch_raw()
    assert _asked(scraper, "SearchJobs") == [_SEARCH]
    assert "disallowed by robots.txt" in scraper.truncated


_VANITY_ROBOTS = (
    "User-agent: *\nSitemap: https://careers.acme.com/careers/sitemap_index.xml\n"
)


def _vanity(vanity_robots: FakeResponse):
    """A tenant whose portal is served from a vanity host (mt's `careers.mt.com`, Siemens's
    `jobs.siemens.com`), which states its own robots.txt."""
    on_vanity = lambda text: text.replace("acme.avature.net", "careers.acme.com")
    pages = _two_pages("1 - 2 of 3 results")
    routes = {
        url.replace("acme.avature.net", "careers.acme.com"): FakeResponse(
            response.status_code, on_vanity(response.text), url=on_vanity(response.url)
        )
        for url, response in pages.items()
    }

    def route(method, url, kwargs):
        if url == "https://acme.avature.net/robots.txt":
            return FakeResponse(200, _VANITY_ROBOTS)
        if url == "https://careers.acme.com/robots.txt":
            return vanity_robots
        if url.endswith("/careers/sitemap_index.xml"):
            return FakeResponse(
                200,
                "<sitemapindex><sitemap><loc>https://careers.acme.com/en_US/careers/"
                "sitemap.xml</loc></sitemap></sitemapindex>",
            )
        if url.endswith("/careers/sitemap.xml"):
            return FakeResponse(200, on_vanity(_JOB_PORTAL_SITEMAP))
        return routes.get(url, FakeResponse(404, ""))

    fetcher = FakeFetcher(route)
    scraper = get_scraper("avature", "acme", fetcher=fetcher, have_details=set())
    scraper.pacer = Pacer(0)
    scraper.fake = fetcher
    return scraper


def test_search_pages_on_a_vanity_host_answer_to_that_hosts_robots_txt():
    """The tenant's robots.txt allows the portal; the vanity host's, which is the host asked, does
    not."""
    scraper = _vanity(
        FakeResponse(200, "User-agent: *\nDisallow: /careers/SearchJobs\n")
    )
    assert scraper.fetch_raw() == []
    assert not _asked(scraper, "SearchJobs")
    assert "disallowed by robots.txt" in scraper.truncated
    assert _asked(scraper, "robots.txt") == [
        "https://acme.avature.net/robots.txt",
        "https://careers.acme.com/robots.txt",
    ]


def test_a_vanity_host_with_no_robots_txt_states_no_rule():
    scraper = _vanity(FakeResponse(404, ""))
    scraper.fetch_raw()
    assert len(_asked(scraper, "SearchJobs")) == 2
    assert scraper.truncated is None


@pytest.mark.parametrize("answer", [FakeResponse(503, ""), FakeResponse(202, "")])
def test_a_vanity_host_whose_robots_txt_did_not_answer_is_not_read(answer):
    """A 5xx or a challenge says nothing about what may be read: nothing is."""
    scraper = _vanity(answer)
    assert scraper.fetch_raw() == []
    assert not _asked(scraper, "SearchJobs")
    assert "disallowed by robots.txt" in scraper.truncated


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        # the portal's own path, and the locale path it redirects to
        ("https://acme.avature.net/careers/SearchJobs", True),
        ("https://acme.avature.net/en_US/careers/SearchJobs/?folderOffset=6", True),
        # the tracking parameter every portal disallows
        ("https://acme.avature.net/careers/SearchJobs/?qtvc=1", False),
        # a portal robots.txt does not whitelist falls to the catch-all
        ("https://acme.avature.net/hiddenportal/SearchJobs", False),
    ],
)
def test_robots_txt_is_read_as_avature_writes_it(url, allowed):
    assert _robots_allows(_WHITELIST_ROBOTS, url) is allowed


def test_a_robots_txt_with_no_rule_for_the_path_allows_it():
    assert _robots_allows("User-agent: *\nDisallow: /admin\n", _SEARCH)


def test_search_rows_read_the_id_url_and_title_of_each_result():
    siemens = search_rows(
        _SEARCH_PAGES["siemens_externaljobs_page_1"],
        "https://jobs.siemens.com/en_US/externaljobs/SearchJobs",
    )
    assert [(row["id"], row["listed_title"]) for row in siemens[:2]] == [
        ("524237", "Técnico Procesos de Moldeo"),
        ("524234", "Director of Sales Operations and Strategic Programs"),
    ]
    assert len(siemens) == 3
    assert siemens[0]["url"] == (
        "https://jobs.siemens.com/en_US/externaljobs/JobDetail/524237"
    )
    small = search_rows(
        _SEARCH_PAGES["a2milkkf_careers_only_page"],
        "https://a2milkkf.avature.net/careers/SearchJobs/",
    )
    assert [row["id"] for row in small] == ["422", "420", "415", "385"]
    assert small[0]["listed_title"] == "Health, Safety & Environment Manager"
    assert re.fullmatch(AvatureScraper.url_shape, small[0]["url"])
    # mt's results are a list, not articles: `<div class="list__item__text__title"><a …>`
    mt = search_rows(
        _SEARCH_PAGES["mt_careers_page_1"],
        "https://careers.mt.com/en_US/careers/SearchJobs/",
    )
    assert [(row["id"], row["listed_title"]) for row in mt] == [
        ("23230", "Telesales Representative"),
        ("23304", "Field Service Technician"),
        (mt[2]["id"], mt[2]["listed_title"]),
    ]
    assert re.fullmatch(AvatureScraper.url_shape, mt[0]["url"])


def test_the_next_search_page_is_the_paging_item_whichever_tag_carries_the_class():
    siemens = next_search_page(
        _SEARCH_PAGES["siemens_externaljobs_page_1"],
        "https://jobs.siemens.com/en_US/externaljobs/SearchJobs",
    )
    assert siemens == (
        "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/"
        "?folderRecordsPerPage=6&folderOffset=6"
    )
    # the class sits on the anchor itself on Two Sigma's own listing pages
    twosigma = next_search_page(
        _SEARCH_PAGES["twosigma_openroles_page_1"],
        "https://careers.twosigma.com/careers/OpenRoles",
    )
    assert twosigma.endswith("OpenRoles/?jobRecordsPerPage=10&jobOffset=10")
    # and after its href on mt's
    assert next_search_page(
        _SEARCH_PAGES["mt_careers_page_1"],
        "https://careers.mt.com/en_US/careers/SearchJobs/",
    ) == ("https://careers.mt.com/en_US/careers/SearchJobs/?jobOffset=10")
    assert (
        next_search_page(_SEARCH_PAGES["a2milkkf_careers_only_page"], _SEARCH) is None
    )
