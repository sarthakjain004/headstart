"""Tests for `headstart.scrapers.cornerstone`.

`fixtures/cornerstone_boards.json` holds three real tenants' whole scrape surface, captured
2026-09-23 and trimmed by a local (uncommitted) script: the career-site
page (its live JWT swapped for an unsigned one with the same claim names), every
`careersites/{id}` answer up to the terminating 404, the pod search per active site (cut to a few
requisitions, `totalCount` set to match), and each kept posting's job ad.

- `ama-assn` — US pod (the tenant host needs the session cookie), sites 1 and 4 inactive, a
  requisition posted on both active sites, listing text that is only "see JD".
- `eckesgranini` — its job ad is empty; the listing text is the posting's only description.
- `aak` — one posting in five places.

Every assertion pins something measured in `docs/cornerstone/2026-09-23_careersite-api-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart import http
from headstart.scrapers.cornerstone import CornerstoneScraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _boards() -> dict:
    with open(FIXTURES / "cornerstone_boards.json", encoding="utf-8") as fh:
        return json.load(fh)


def _csod_response(
    status: int, body: Any = "", headers: dict | None = None
) -> FakeResponse:
    return FakeResponse(
        status, body if isinstance(body, str) else json.dumps(body), headers=headers
    )


class _FakeCsod(FakeFetcher):
    """Routes the four request shapes the scraper makes to one tenant's recorded answers."""

    def __init__(self, slug: str, board: dict) -> None:
        super().__init__(self._answer)
        self.slug = slug
        self.board = board
        self.home_status: dict[int, int] = {}
        self.fail_ads: set[str] = set()
        self.unauthorized_once: set[str] = set()

    def _answer(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        parts = urlsplit(url)
        path = parts.path
        m = re.fullmatch(r"/ux/ats/careersite/(\d+)/home", path)
        if m:
            status = self.home_status.get(
                int(m.group(1)), 200 if m.group(1) == "1" else 302
            )
            if status == 302:
                return _csod_response(302, "", {"location": "/ui/error"})
            return _csod_response(200, self.board["home"])
        if hits := [k for k in self.unauthorized_once if k in url]:
            self.unauthorized_once.discard(hits[0])
            return _csod_response(401, "")
        m = re.fullmatch(r"/services/x/career-site/v1/careersites/(\d+)", path)
        if m:
            answer = self.board["careersites"].get(
                m.group(1), {"status": 404, "body": {}}
            )
            return _csod_response(answer["status"], answer["body"])
        if path.endswith("/rec-job-search/external/jobs"):
            body = kwargs["json"]
            site = str(body["careerSitePageId"])
            page = self.board["search"].get(site)
            if page is None or body["pageNumber"] > 1:
                empty = {
                    "totalCount": (page or {"data": {"totalCount": 0}})["data"][
                        "totalCount"
                    ]
                }
                return _csod_response(200, {"data": {**empty, "requisitions": []}})
            return _csod_response(200, page)
        m = re.fullmatch(
            r"/Services/API/ATS/CareerSite/(\d+)/JobRequisitions/(\d+)", path
        )
        if m:
            key = f"{m.group(1)}/{m.group(2)}"
            if key in self.fail_ads or key not in self.board["ads"]:
                return _csod_response(500, "")
            return _csod_response(
                200, self.board["ads"][key], {"content-type": "application/json"}
            )
        raise AssertionError(f"unrouted {method} {url}")


def _scrape(slug: str, **setup: Any) -> tuple[list, _FakeCsod, CornerstoneScraper]:
    fake = _FakeCsod(slug, _boards()[slug])
    for k, v in setup.items():
        setattr(fake, k, v)
    scraper = CornerstoneScraper(slug, fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    return jobs, fake, scraper


def _set_ad(board: dict, key: str, ad_html: str) -> None:
    """Replace one recorded job ad's `ad` field, leaving the rest of the response as captured."""
    ad = json.loads(board["ads"][key])
    ad["data"][0]["items"][0]["fields"]["ad"] = ad_html
    board["ads"][key] = json.dumps(ad)


def _by_id(jobs: list) -> dict:
    return {j.id.rsplit(":", 1)[1]: j for j in jobs}


def test_a_board_is_the_union_of_its_active_sites_by_requisition():
    """ama-assn posts 4070 on sites 2 and 3; per-site Boards would serve it twice."""
    jobs, _, _ = _scrape("ama-assn")
    assert sorted(_by_id(jobs)) == ["4070", "4125", "4144", "4152"]
    assert all(j.id.startswith("cornerstone:ama-assn:") for j in jobs)


def _searched_sites(fake: _FakeCsod) -> list[int]:
    return [
        request.kwargs["json"]["careerSitePageId"]
        for request in fake.requests
        if request.method == "POST"
    ]


def _walked_sites(fake: _FakeCsod) -> list[int]:
    return [int(url.rsplit("/", 1)[1]) for url in fake.urls() if "/careersites/" in url]


def test_inactive_sites_are_walked_but_not_searched():
    """No inactive site carried a posting on any of 398 tenants; ama-assn's 1 and 4 are inactive."""
    _, fake, _ = _scrape("ama-assn")
    assert _searched_sites(fake) == [2, 3]


def test_the_site_walk_ends_at_the_first_404():
    """Site ids run 1..N and `careersites/{N+1}` is a 404 (396 of 398 tenants exactly)."""
    _, fake, _ = _scrape("ama-assn")
    assert _walked_sites(fake) == [1, 2, 3, 4, 5]


def test_a_site_answering_500_is_searched_and_the_walk_goes_on():
    """`nlb` id 6 and `myhr-ece` id 1 answer 500 with live sites after them — only a 404 ends
    the walk."""
    board = _boards()["ama-assn"]
    board["careersites"]["2"] = {"status": 500, "body": {}}
    fake = _FakeCsod("ama-assn", board)
    scraper = CornerstoneScraper("ama-assn", fetcher=fake)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert _searched_sites(fake) == [2, 3]
    assert "4125" in _by_id(jobs)


def test_the_search_goes_to_the_pages_pod_and_the_tenant_host_gets_the_session_cookie():
    """`na.api.csod.com` (upstream's default) does not resolve; the pod is
    `csod.context.endpoints.cloud`. US-pod tenant hosts 401 without `ASP.NET_SessionId`, whose
    value is the JWT's `aud` (14 of 14 US-pod tenants)."""
    _, fake, _ = _scrape("ama-assn")
    posts = [request.url for request in fake.requests if request.method == "POST"]
    assert posts and all(
        u == "https://us.api.csod.com/rec-job-search/external/jobs" for u in posts
    )
    tenant_calls = [
        request.kwargs for request in fake.requests if "/careersites/" in request.url
    ]
    assert all(
        kw["headers"]["Cookie"] == "ASP.NET_SessionId=fixture-session-ama-assn"
        for kw in tenant_calls
    )
    assert all(
        kw["headers"]["Authorization"].startswith("Bearer ") for kw in tenant_calls
    )


def test_the_job_url_is_the_lowest_site_the_posting_is_on_and_matches_the_shape():
    """A job page for a site the posting is not on is a 200 shell with no posting, so the URL
    names a site the listing returned it on — the lowest, since sites are walked in order."""
    jobs = _by_id(_scrape("ama-assn")[0])
    assert jobs["4070"].url == (
        "https://ama-assn.csod.com/ux/ats/careersite/2/home/requisition/4070?c=ama-assn"
    )
    assert jobs["4152"].url.startswith("https://ama-assn.csod.com/ux/ats/careersite/3/")
    for job in jobs.values():
        assert re.fullmatch(CornerstoneScraper.url_shape, job.url)


def test_the_description_is_the_job_ad_not_the_listing_fragment():
    """The listing's `externalDescription` is one field of the ad — on ama-assn it is "see JD";
    the ad was >20% longer on 750 of 924 postings measured."""
    jobs = _by_id(_scrape("ama-assn")[0])
    desc = jobs["4125"].description
    assert desc.startswith("Sr. Software Engineer, Platform")
    assert "American Medical Association" in desc
    assert len(desc) > 2000


def test_the_job_ad_is_fetched_from_the_site_the_posting_was_seen_on():
    _, fake, _ = _scrape("ama-assn")
    ads = sorted(
        url.split("/CareerSite/")[1].split("?")[0]
        for url in fake.urls()
        if "/JobRequisitions/" in url
    )
    assert ads == [
        "2/JobRequisitions/4070",
        "2/JobRequisitions/4125",
        "2/JobRequisitions/4144",
        "3/JobRequisitions/4152",
    ]


def test_an_empty_job_ad_falls_back_to_the_listing_text():
    """21 of 923 ads were empty; on eckesgranini the listing text is the only description."""
    job = _by_id(_scrape("eckesgranini")[0])["102"]
    assert job.description.startswith("Verantwortung für die Umsetzung")


def test_placeholder_text_is_no_description():
    """Tenant templates leave `<<INTERNAL JOB DESCRIPTION>>`, `>`, "see JD" and the like: every
    listing text under 30 word characters (once `<<…>>` tokens are removed) was one of those,
    2,406 of 24,800. An ad that is a placeholder falls back; a placeholder fallback is None."""
    board = _boards()["ama-assn"]
    _set_ad(board, "2/4125", "&lt;&lt;INTERNAL JOB DESCRIPTION&gt;&gt; &gt;")
    fake = _FakeCsod("ama-assn", board)
    scraper = CornerstoneScraper("ama-assn", fetcher=fake)
    job = _by_id(scraper.parse(scraper.fetch_raw(), SCRAPED_AT))["4125"]
    assert job.description is None  # the listing text is "see JD"


def test_every_location_is_joined_not_just_the_first():
    """`locations` named more than one place on 1,848 of 24,800 rows (up to 45); upstream kept
    only the first, which fails the location filter everywhere else."""
    job = _by_id(_scrape("aak")[0])["5327"]
    assert job.location == (
        "Jundiaí, São Paulo, BR; Zhangjiagang, Jiangsu Province, CN; Aarhus C, DK; "
        "Karlshamn, SE; Hull, GB"
    )


def test_posted_at_is_the_listing_date_read_as_us_month_first():
    """The search is always asked in en-US, which writes `postingEffectiveDate` as M/D/YYYY
    (nl-NL would write 23-09-2026). 4070 is on site 2 (7/14) and site 3 (8/28): the row read is
    the lowest site's, the same one its URL names."""
    jobs = _by_id(_scrape("ama-assn")[0])
    assert jobs["4125"].posted_at == "2026-08-27"
    assert jobs["4070"].posted_at == "2026-07-14"
    search = [
        request.kwargs["json"]
        for request in _scrape("ama-assn")[1].requests
        if request.method == "POST"
    ]
    assert all(b["cultureName"] == "en-US" for b in search)


def test_remote_is_read_from_the_location_text_only():
    """No surface states remote; tenants write it into the city (`Deutscher Standort/Remote`)."""
    jobs, _, _ = _scrape("ama-assn")
    assert {j.remote for j in jobs} == {False}
    row = _boards()["aak"]["search"]["2"]["data"]["requisitions"][0]
    row["locations"] = [{"city": "Deutscher Standort/Remote", "country": "DE"}]
    raw = {"postings": [{**row, "_site": 2}], "ads": {}}
    assert CornerstoneScraper("aak").parse(raw, SCRAPED_AT)[0].remote is True


def test_an_html_escaped_title_is_unescaped():
    """Titles holding `<` arrive escaped (`&lt;&lt;ENTER DISPLAY JOB TITLE&gt;&gt;`, 835 rows)."""
    row = {
        "requisitionId": 1,
        "displayJobTitle": "R&amp;D &lt;Lead&gt;",
        "locations": [],
        "_site": 1,
    }
    job = CornerstoneScraper("aak").parse({"postings": [row], "ads": {}}, SCRAPED_AT)[0]
    assert job.title == "R&D <Lead>"


def _ad_fetches(fake: _FakeCsod) -> list[str]:
    return sorted(
        url.split("JobRequisitions/")[1].split("?")[0]
        for url in fake.urls()
        if "/JobRequisitions/" in url
    )


def test_a_failed_job_ad_still_ships_the_job_without_a_description():
    jobs, _, scraper = _scrape("ama-assn", fail_ads={"2/4125"})
    job = _by_id(jobs)["4125"]
    assert job.description is None and job.title.startswith("Sr. Software Engineer")
    assert scraper.telemetry["detail_losses"] == 1
    assert scraper.truncated is None  # a missing field, not a missing posting


def test_the_tech_gate_and_the_description_store_skip_job_ads(monkeypatch):
    """The gate is exact: no surface states a department and the ad's `title` equals the
    listing's (58 of 58), so `is_tech(title, None)` asks what `filter_tech` will ask. The ad
    supplies only `description`, so ADR-0048's skip of an already-described Job is taken."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    fake = _FakeCsod("ama-assn", _boards()["ama-assn"])
    scraper = CornerstoneScraper("ama-assn", fetcher=fake)
    scraper.have_details = {"cornerstone:ama-assn:4070"}
    jobs = _by_id(scraper.parse(scraper.fetch_raw(), SCRAPED_AT))
    # 4144 (SR. ACCOUNT EXECUTIVE) and 4152 (Sr. Policy Analyst) are not tech; 4070 is stored.
    assert _ad_fetches(fake) == ["4125"]
    assert set(jobs) == {"4070", "4125", "4144", "4152"}
    assert jobs["4144"].description is None and jobs["4070"].description is None


def _home_fetches(fake: _FakeCsod) -> int:
    return sum(1 for url in fake.urls() if "/home?c=" in url)


def test_an_expired_token_is_refreshed_once_and_the_request_retried():
    """Tokens live 21 min to 24 h, and an expired one answers 401 on the pod and the tenant host
    alike (replayed across `exp` on sncfl). One refresh — a new page read — and one retry."""
    jobs, fake, _ = _scrape(
        "ama-assn", unauthorized_once={"rec-job-search", "JobRequisitions/4125"}
    )
    assert set(_by_id(jobs)) == {"4070", "4125", "4144", "4152"}
    assert _by_id(jobs)["4125"].description is not None
    assert _home_fetches(fake) == 3


def test_a_401_that_survives_the_refresh_fails_the_board():
    fake = _FakeCsod("ama-assn", _boards()["ama-assn"])
    tenant_route = fake.route
    fake.route = lambda method, url, kwargs: (
        _csod_response(401, "")
        if "rec-job-search" in url
        else tenant_route(method, url, kwargs)
    )
    scraper = CornerstoneScraper("ama-assn", fetcher=fake)
    with pytest.raises(http.RequestsError):
        scraper.fetch_raw()


def test_a_corp_with_no_career_site_reads_as_no_jobs():
    """An LMS-only corp answers `302 /ui/error` for every career-site page (5 tenants x ids
    1-6); three ids are tried because `myhr-ece` starts at 2."""
    jobs, fake, _ = _scrape("ama-assn", home_status={1: 302, 2: 302, 3: 302})
    assert jobs == []
    assert _home_fetches(fake) == 3
    assert not [url for url in fake.urls() if "/careersites/" in url]


def test_the_token_is_read_from_the_next_site_when_site_1_redirects():
    jobs, fake, _ = _scrape("ama-assn", home_status={1: 302, 2: 200})
    assert len(jobs) == 4
    assert _home_fetches(fake) == 2


class _PagedSearch(_FakeCsod):
    """Serves one site as `pages` pages of `_PAGE_SIZE` fake-free recorded rows, re-numbered."""

    def __init__(self, total: int, served: int) -> None:
        super().__init__("aak", _boards()["aak"])
        template = self.board["search"]["2"]["data"]["requisitions"][0]
        self.rows = [{**template, "requisitionId": i} for i in range(served)]
        self.total = total

    def _answer(self, method, url, kwargs):
        if method == "POST":
            body = kwargs["json"]
            if body["careerSitePageId"] != 2:
                return _csod_response(
                    200, {"data": {"totalCount": 0, "requisitions": []}}
                )
            size, page = body["pageSize"], body["pageNumber"]
            chunk = self.rows[(page - 1) * size : page * size]
            return _csod_response(
                200, {"data": {"totalCount": self.total, "requisitions": chunk}}
            )
        if "/JobRequisitions/" in url:
            return _csod_response(500, "")
        return super()._answer(method, url, kwargs)


def test_a_site_is_read_page_by_page_until_its_total():
    """`pageSize` clamps silently at 1,000; `totalCount` matched the rows served on the five
    largest sites measured (to 3,372)."""
    fake = _PagedSearch(total=2345, served=2345)
    scraper = CornerstoneScraper("aak", fetcher=fake)
    raw = scraper.fetch_raw()
    assert len(raw["postings"]) == 2345
    assert [
        request.kwargs["json"]["pageNumber"]
        for request in fake.requests
        if request.method == "POST" and request.kwargs["json"]["careerSitePageId"] == 2
    ] == [1, 2, 3]
    assert scraper.truncated is None


def test_a_site_that_ends_short_of_its_total_is_reported():
    fake = _PagedSearch(total=2345, served=1500)
    scraper = CornerstoneScraper("aak", fetcher=fake)
    scraper.fetch_raw()
    assert scraper.truncated and "1500 of 2345" in scraper.truncated


def test_the_slug_is_the_lowercased_corp_label_from_any_discovery_spelling():
    """Seed URLs, bare Wayback/Common Crawl labels and a mixed-case `c=` all name one Board."""
    assert CornerstoneScraper.slug_from("aak", "") == "aak"
    assert CornerstoneScraper.slug_from("ASWatsonEurope", "") == "aswatsoneurope"
    assert (
        CornerstoneScraper.slug_from(
            "", "https://thekids.csod.com/ux/ats/careersite/4/home?c=thekids"
        )
        == "thekids"
    )
    assert CornerstoneScraper.slug_from("https://aak.csod.com", "") == "aak"


def test_the_scraper_declares_its_detail_pass_and_no_salary():
    assert CornerstoneScraper.has_detail_pass is True
    assert CornerstoneScraper("aak")._salary_field({}) is None


def test_the_pooled_session_keeps_no_cornerstone_cookies():
    """The real transport is a thread-pooled session with a cookie jar. On US-pod tenants the
    jar's `ASP.NET_SessionId` *plus* the explicit header answers 401 (6 of 6 trials, ama-assn and
    alamo), and re-reading the career-site page with the jar populated redirects to `/ui/error`
    (ama-assn, aswatsoneurope). So the page's cookies are dropped as soon as it is read, and
    every tenant-host request carries its session only as the header."""
    _, fake, _ = _scrape("ama-assn")
    assert fake.cookie_clears and set(fake.cookie_clears) == {"ama-assn.csod.com"}


def test_a_refused_site_answer_fails_the_board_rather_than_walking_on():
    """Only a 404 ends the walk and only a 5xx is searched past; a 401 that survives the refresh
    must not read as 500 more empty sites."""
    board = _boards()["ama-assn"]
    board["careersites"]["2"] = {"status": 403, "body": {}}
    fake = _FakeCsod("ama-assn", board)
    with pytest.raises(http.RequestsError):
        CornerstoneScraper("ama-assn", fetcher=fake).fetch_raw()
    assert _walked_sites(fake) == [1, 2]


def test_a_site_the_search_does_not_know_lists_nothing():
    """Six tenants (metso, transgourmet, getingeacademy, …) serve a career-site page and active
    sites, but the pod search answers 404 `ResourceNotFound` for them — and the page itself, in
    Chrome, renders "Current Openings" with none listed. That is an empty site, not a failure."""
    board = _boards()["ama-assn"]

    class _Unindexed(_FakeCsod):
        def _answer(self, method, url, kwargs):
            if method == "POST":
                return _csod_response(
                    404,
                    {
                        "status": "ValidationError",
                        "error": {"code": "ResourceNotFound"},
                    },
                )
            return super()._answer(method, url, kwargs)

    scraper = CornerstoneScraper("ama-assn", fetcher=_Unindexed("ama-assn", board))
    assert scraper.listing() == []
    assert scraper.truncated is None


def test_the_page_cap_is_a_hard_truncation(monkeypatch):
    """Past `_MAX_PAGES` the rest of a site is unread, not negligible (ADR-0121)."""
    from headstart.scrapers import cornerstone

    monkeypatch.setattr(cornerstone, "_MAX_PAGES", 2)
    scraper = CornerstoneScraper("aak", fetcher=_PagedSearch(total=2345, served=2345))
    raw = scraper.fetch_raw()
    assert len(raw["postings"]) == 2000
    assert scraper.truncated and "2-page cap" in scraper.truncated


def test_the_site_cap_is_a_hard_truncation(monkeypatch):
    """A walk that never meets its 404 stopped reading; it did not reach the Board's end."""
    from headstart.scrapers import cornerstone

    monkeypatch.setattr(cornerstone, "_MAX_SITES", 2)
    board = _boards()["ama-assn"]  # sites 1-4 answer, 5 is the 404
    scraper = CornerstoneScraper("ama-assn", fetcher=_FakeCsod("ama-assn", board))
    scraper.listing()
    assert scraper.truncated and "walked 2 career sites" in scraper.truncated


def test_a_search_404_without_resource_not_found_fails_the_board():
    """Only the measured body reads as empty; any other 404 (a moved path, a misrouted pod) is
    news, and must not turn a live Board into zero jobs."""

    class _Moved(_FakeCsod):
        def _answer(self, method, url, kwargs):
            if method == "POST":
                return _csod_response(404, "<html>Not Found</html>")
            return super()._answer(method, url, kwargs)

    scraper = CornerstoneScraper(
        "ama-assn", fetcher=_Moved("ama-assn", _boards()["ama-assn"])
    )
    with pytest.raises(http.RequestsError):
        scraper.listing()


def test_resource_not_found_after_rows_were_read_fails_the_board():
    """The measured `ResourceNotFound` always answered page 1; past it, rows already read would
    otherwise pass for a whole site."""

    class _GoneMidWalk(_PagedSearch):
        def _answer(self, method, url, kwargs):
            if method == "POST" and kwargs["json"]["pageNumber"] > 1:
                return _csod_response(404, {"error": {"code": "ResourceNotFound"}})
            return super()._answer(method, url, kwargs)

    scraper = CornerstoneScraper("aak", fetcher=_GoneMidWalk(total=2345, served=2345))
    with pytest.raises(http.RequestsError):
        scraper.listing()
