"""Tests for `headstart.scrapers.peoplestrong`.

The fixtures are three real postings captured 2026-09-25: two from `careers-bmwtechworks` (a
Permanent tech role with a stated experience range, and a Full Time one with none) and one from
`mpgcareers` (Muthoot Fincorp, whose location path is a sales territory rather than a country).
`peoplestrong_listing.json` is the API's own envelope trimmed to those three rows, and
`peoplestrong_details.json` maps each URL-form job code to its detail response, trimmed to the
fields around the ones `parse` reads and with the description cut to 800 characters.

Every assertion pins something measured in
`docs/peoplestrong/2026-09-25_candidate-portal-measurement.md`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.jobs import experience
from headstart.scrapers import peoplestrong
from headstart.scrapers.pacer import Pacer
from headstart.scrapers.peoplestrong import PeopleStrongScraper
from headstart.search_filters.employment_type_filter import flags

FIXTURES = Path(__file__).parent / "fixtures"
BMW = "careers-bmwtechworks"
CONCEPT_LEAD = "BTWI_CL-HBCD-D_1871096"
ADAS = "BTWI_AFD_1869995"
MUTHOOT = "MFL_BDE_1872048"


@pytest.fixture(autouse=True)
def _no_real_pacing(monkeypatch):
    """The shared pacer's 16 ms spacing would make every multi-page test sleep for real."""
    monkeypatch.setattr(PeopleStrongScraper, "pacer", Pacer(0.0))


def _listing() -> dict:
    with open(FIXTURES / "peoplestrong_listing.json", encoding="utf-8") as fh:
        return json.load(fh)


def _details() -> dict:
    with open(FIXTURES / "peoplestrong_details.json", encoding="utf-8") as fh:
        return json.load(fh)


def _board_route(listing: dict | None = None, details: dict | None = None):
    """A route answering the listing POST with ``listing`` and each detail GET from ``details``."""
    listing = listing or _listing()
    details = _details() if details is None else details

    def route(method: str, url: str, kwargs: dict):
        if method == "POST" and "/cp/jobs/v1" in url:
            return FakeResponse(200, json.dumps(listing))
        if method == "GET" and "/cp/job/" in url:
            code = url.split("/cp/job/", 1)[1].split("/", 1)[0]
            if code in details:
                return FakeResponse(200, json.dumps(details[code]))
            return FakeResponse(404, "")
        raise AssertionError(f"unexpected request {method} {url}")

    return route


def _scraper(route=None, slug: str = BMW) -> PeopleStrongScraper:
    return PeopleStrongScraper(slug, slug, fetcher=FakeFetcher(route or _board_route()))


def _jobs(scraper: PeopleStrongScraper | None = None) -> dict:
    return {j.id.rsplit(":", 1)[1]: j for j in (scraper or _scraper()).fetch()}


def test_a_board_is_read_through_its_listing_and_details_into_jobs():
    jobs = _jobs()
    assert set(jobs) == {CONCEPT_LEAD, ADAS, MUTHOOT}
    job = jobs[CONCEPT_LEAD]
    assert job.id == f"peoplestrong:{BMW}:{CONCEPT_LEAD}"
    assert job.ats == "peoplestrong"
    assert job.title == (
        "Concept Lead - HV Battery Concept Design & Early-Phase Development"
    )
    assert job.url == f"https://{BMW}.peoplestrong.com/job/detail/{CONCEPT_LEAD}"
    assert job.description and job.description.startswith("What awaits you")


def test_the_location_is_the_place_path_in_the_providers_order_without_repeats():
    """One place per posting (a ',' inside a segment is part of a place name: "Industry House,
    Bangalore"); the path runs from the tenant's own root, which is a sales territory rather than
    a country on Muthoot's Board (16,100 rows). A segment repeats ("Pune>Pune") on 23,343 of 35,732 paths."""
    jobs = _jobs()
    assert jobs[CONCEPT_LEAD].location == "India, Maharashtra, Pune"
    assert jobs[MUTHOOT].location == (
        "TERRITORY-II, NORTH-1, NCR, GHAZIABAD, LAJPAT NAGAR-GHAZIABAD, F1572-D, METRO-PLUS, "
        "UTTAR PRADESH"
    )
    # No surface states remote (the detail's `Onsite` was null on 35,732 of 35,732), so it is
    # read off the location text; a named place reads as not remote.
    assert jobs[CONCEPT_LEAD].remote is False


def test_a_posting_with_no_place_path_has_no_location():
    listing = _listing()
    listing["response"][0]["locationHierarchyComplete"] = None
    jobs = _jobs(_scraper(_board_route(listing)))
    assert jobs[CONCEPT_LEAD].location is None


def test_the_department_is_the_listings_org_unit():
    """`organizationUnit` is on 35,732 of 35,732 listing rows; the detail's `departmentHierarchy`
    was null on every one, so the listing is the only department there is."""
    jobs = _jobs()
    assert jobs[CONCEPT_LEAD].department == "Digital Product Engineering"
    assert jobs[MUTHOOT].department == "Muthoot Fincorp Ltd."


def test_experience_is_the_listings_range_as_stated():
    """`expRange` ("8-14 years", 97.5% of rows) equals the detail's min-max on every posting
    measured, and is already the shape `experience.from_field` reads."""
    jobs = _jobs()
    assert jobs[CONCEPT_LEAD].experience == "8-14 years"
    span = experience.from_field(jobs[CONCEPT_LEAD].experience)
    assert (span.min_years, span.max_years) == (8, 14)
    assert jobs[ADAS].experience is None


def test_posted_at_is_the_listings_posted_date():
    """Equal to the detail's `CandidatePortalStartDate` on 35,732 of 35,732 postings, and never
    past its own closing date on a listed posting (0 of 35,732): a real date."""
    assert _jobs()[CONCEPT_LEAD].posted_at == "2026-09-25"


def test_employment_type_is_the_details_own_word():
    jobs = _jobs()
    assert jobs[CONCEPT_LEAD].employment_type == "Permanent"
    assert jobs[ADAS].employment_type == "Full Time"


def test_payroll_phrasings_the_filter_cannot_read_are_labelled_full_time():
    """ "On Roll" (397 postings), "Employee" (320) and "Regular" (45) are Indian payroll words
    for a permanent hire; `employment_type_filter.flags` reads none of them. The provider's word
    stays in the label."""
    details = _details()
    for word, label in [
        ("On Roll", "Full Time (On Roll)"),
        ("Employee", "Full Time (Employee)"),
        ("Regular", "Full Time (Regular)"),
    ]:
        details[CONCEPT_LEAD]["response"]["employmentType"] = word
        jobs = _jobs(_scraper(_board_route(details=details)))
        assert jobs[CONCEPT_LEAD].employment_type == label
        assert flags(label)["is_full_time"]


def test_a_blank_employment_type_is_none():
    details = _details()
    details[CONCEPT_LEAD]["response"]["employmentType"] = ""
    assert (
        _jobs(_scraper(_board_route(details=details)))[CONCEPT_LEAD].employment_type
        is None
    )


def test_a_stated_salary_is_not_served():
    """The job page shows `minSalary`-`maxSalary` only where the entity's display config sets
    `ctcMaxRendered` (365 of 35,732 postings), bare, with no currency or period and in mixed
    units ("800000-1000000" beside "23-37"). Muthoot's detail states 230000 here."""
    assert _details()[MUTHOOT]["response"]["minSalary"] == "230000"
    assert _jobs()[MUTHOOT].salary is None


def _paged_route(total: int, served: int | None = None):
    """A Board of ``total`` postings (the fixture's first row, re-coded) served ``served`` of them
    in pages the API's way: `limit` clamped at 99, `offset` a 0-based row offset."""
    base = _listing()["response"][0]
    rows = [
        {**base, "jobCode": f"BTWI/X/{i}"}
        for i in range(total if served is None else served)
    ]

    def route(method: str, url: str, kwargs: dict):
        if method == "POST":
            query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
            offset, limit = int(query["offset"]), min(int(query["limit"]), 99)
            page = rows[offset : offset + limit]
            return FakeResponse(
                200, json.dumps({"totalRecords": total, "response": page})
            )
        return FakeResponse(404, "")

    return route


def test_the_walk_reads_pages_of_99_by_row_offset_until_a_short_page():
    """`limit` clamps at 99 whatever is asked (100 through 5,000 all returned 99) and `offset`
    counts rows: two walks of the largest Board measured then, 1,923 postings, read 1,923 unique
    each over 20 pages."""
    fetcher = FakeFetcher(_paged_route(150))
    scraper = PeopleStrongScraper(BMW, BMW, fetcher=fetcher)
    jobs = scraper.fetch()
    assert len({j.id for j in jobs}) == 150
    offsets = [
        r.url.split("offset=")[1].split("&")[0]
        for r in fetcher.requests
        if r.method == "POST"
    ]
    assert offsets == ["0", "99"]
    assert not scraper.truncated


def test_a_walk_that_ends_short_of_the_stated_total_is_marked_truncated():
    """The stated total equalled the rows served on 56 of 56 Boards walked whole, so a shortfall
    is the API changing under us: the unread rest is unread, not closed."""
    scraper = PeopleStrongScraper(
        BMW, BMW, fetcher=FakeFetcher(_paged_route(300, served=100))
    )
    assert len(scraper.fetch()) == 100
    assert scraper.truncated and "100 of 300" in scraper.truncated


def test_a_walk_that_hits_the_page_cap_is_marked_truncated(monkeypatch):
    monkeypatch.setattr(peoplestrong, "_MAX_PAGES", 2)
    scraper = PeopleStrongScraper(BMW, BMW, fetcher=FakeFetcher(_paged_route(400)))
    assert len(scraper.fetch()) == 198
    assert scraper.truncated and "page cap" in scraper.truncated


def _refusing(route, refusals: int):
    """``route`` behind the platform's rate limit: the first ``refusals`` requests get Kong's 429."""
    left = [refusals]

    def refused(method: str, url: str, kwargs: dict):
        if left[0]:
            left[0] -= 1
            return FakeResponse(429, '{"message":"API rate limit exceeded"}')
        return route(method, url, kwargs)

    return refused


def _unpaced(monkeypatch, scraper: PeopleStrongScraper) -> list[float]:
    """No real waiting in tests; returns the list every rest of the shared pacer lands in."""
    rests: list[float] = []
    monkeypatch.setattr(scraper.pacer, "rest", rests.append)
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    return rests


def test_a_429_rests_the_process_through_the_window_and_retries(monkeypatch):
    """One budget of 5,000 requests per calendar minute per client IP spans every tenant and
    endpoint (Kong's `X-RateLimit-Remaining-minute` fell across four different hosts), and a
    refusal carries no Retry-After: resting everything to the window's end is the only wait that
    works."""
    scraper = _scraper(_refusing(_board_route(), 1))
    rests = _unpaced(monkeypatch, scraper)
    assert len(scraper.fetch()) == 3
    assert rests == [peoplestrong._WINDOW_S]
    assert not scraper.truncated


def test_a_listing_refused_through_every_window_is_truncated_not_ended_short(
    monkeypatch,
):
    scraper = PeopleStrongScraper(
        BMW,
        BMW,
        fetcher=FakeFetcher(_refusing(_paged_route(150), 1 + peoplestrong._TRIES)),
    )
    _unpaced(monkeypatch, scraper)
    scraper.fetch()
    assert scraper.truncated and "rate-limited" in scraper.truncated


#: What the detail endpoint answers for a code it does not hold (a closed posting, or a code on
#: another tenant), measured 2026-09-25: a 200 whose response has no title and no description.
SKELETON = {
    "response": {
        "minBudgetSalary": "0",
        "maxSalary": "0",
        "maxBudgetSalary": "0",
        "Username": None,
        "FirstName": None,
        "isCampusJob": False,
        "JobFamilyCode": None,
        "LastName": None,
        "minSalary": "0",
        "educationSepecialization": None,
    },
    "messageCode": {"code": 200, "messages": "success"},
}


def test_a_skeleton_detail_is_a_lost_detail_and_the_job_still_ships():
    details = _details()
    details[CONCEPT_LEAD] = SKELETON
    scraper = _scraper(_board_route(details=details))
    jobs = _jobs(scraper)
    assert jobs[CONCEPT_LEAD].description is None
    assert jobs[CONCEPT_LEAD].employment_type is None
    assert jobs[CONCEPT_LEAD].title  # the listing still names it
    assert scraper.detail_losses["no jobTitle on a 200"] == 1


def test_a_detail_without_a_description_keeps_its_other_fields():
    """58 of 35,732 real details state a title and an employment type but an empty description."""
    details = _details()
    details[CONCEPT_LEAD]["response"]["jobDescription"] = ""
    scraper = _scraper(_board_route(details=details))
    job = _jobs(scraper)[CONCEPT_LEAD]
    assert job.description is None
    assert job.employment_type == "Permanent"
    assert scraper.detail_losses["no jobDescription"] == 1


def test_a_detail_that_fails_in_transport_ships_the_job_without_it():
    route = _board_route()

    def failing(method: str, url: str, kwargs: dict):
        if CONCEPT_LEAD in url:
            return FakeResponse(500, "")
        return route(method, url, kwargs)

    scraper = _scraper(failing)
    jobs = _jobs(scraper)
    assert jobs[CONCEPT_LEAD].description is None
    assert jobs[ADAS].description


def test_the_pipeline_fetches_details_only_for_postings_the_tech_filter_keeps(
    monkeypatch,
):
    """Exact, not an approximation: title and department both come off the listing row the gate
    reads, and the detail changed neither on 35,732 of 35,732 postings. 1,964 of those (5.5%)
    were tech, so the gate saves ~94% of detail requests against the shared budget."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    fetcher = FakeFetcher(_board_route())
    scraper = PeopleStrongScraper(BMW, BMW, fetcher=fetcher)
    scraper.have_details = frozenset()  # the pipeline's signal; nothing held yet
    jobs = {j.id.rsplit(":", 1)[1]: j for j in scraper.fetch()}
    fetched = {
        r.url.split("/cp/job/")[1].split("/")[0]
        for r in fetcher.requests
        if "/cp/job/" in r.url
    }
    # The ADAS posting is a C++ role `tech_filter` misses today; the gate asks the filter's own
    # question, so it is gated exactly as `filter_tech` would drop it.
    assert fetched == {CONCEPT_LEAD}
    assert jobs[MUTHOOT].description is None  # gated, still listed


def test_a_description_already_held_is_fetched_again_for_its_employment_type(
    monkeypatch,
):
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    fetcher = FakeFetcher(_board_route())
    scraper = PeopleStrongScraper(BMW, BMW, fetcher=fetcher)
    scraper.have_details = frozenset({f"peoplestrong:{BMW}:{CONCEPT_LEAD}"})
    jobs = {j.id.rsplit(":", 1)[1]: j for j in scraper.fetch()}
    assert jobs[CONCEPT_LEAD].employment_type == "Permanent"


def test_the_job_url_matches_the_declared_shape():
    """`job_url` equalled the API's own `jobDetailUrl` on 35,732 of 35,732 postings: the label,
    then the job code with each "/" written as "_" (codes carry only "/" and "-")."""
    for job in _jobs().values():
        assert re.fullmatch(PeopleStrongScraper.url_shape, job.url)


def test_the_slug_is_the_lowercased_portal_label():
    """`Careers-BMWTechWorks` reads the same Board as `careers-bmwtechworks`, and `urlinfo`
    echoes the label lowercased, so the key is lowercased; a host or URL in the ledger reduces to
    its label."""
    slug_from = PeopleStrongScraper.slug_from
    assert slug_from("Careers-BMWTechWorks", "") == BMW
    assert slug_from(BMW, f"https://{BMW}.peoplestrong.com") == BMW
    assert slug_from(f"{BMW}.peoplestrong.com", "") == BMW


def test_the_detail_pass_runs_on_threads_not_the_multiplexed_path():
    """HTTP/1.1 with `Connection: close` on every response: nothing to multiplex over."""
    assert PeopleStrongScraper.async_fanout is False
    assert not PeopleStrongScraper.async_fanout_enabled()


def test_a_detail_refused_on_the_multiplexed_path_rests_and_retries_too(monkeypatch):
    """Off for this scraper, but `_fetch_async` overrides the transport a future re-enable would
    run on, so it is pinned to the same policy as the sync one."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "1")
    monkeypatch.setattr(PeopleStrongScraper, "async_fanout", True)
    route, refused = _board_route(), [False]

    def refuse_one_detail(method: str, url: str, kwargs: dict):
        if CONCEPT_LEAD in url and not refused[0]:
            refused[0] = True
            return FakeResponse(429, '{"message":"API rate limit exceeded"}')
        return route(method, url, kwargs)

    scraper = _scraper(refuse_one_detail)
    rests: list[float] = []
    monkeypatch.setattr(scraper.pacer, "rest", rests.append)
    jobs = _jobs(scraper)
    assert rests == [peoplestrong._WINDOW_S]
    assert jobs[CONCEPT_LEAD].description


#: What a host that is not a registered candidate portal answers the listing, measured 2026-09-25
#: on 192 pool labels (HRMS logins such as `abfrl`, support hosts) and an invented one alike.
UNREGISTERED = {
    "response": None,
    "messageCode": {
        "code": 201,
        "messages": [
            {
                "message": "Inside getTpUrl(String url, String portalName) with url : "
                "abfrl.peoplestrong.com"
            }
        ],
    },
}


def test_an_unregistered_portal_is_noted_unreadable_not_read_as_empty(caplog):
    scraper = _scraper(
        lambda m, u, k: FakeResponse(200, json.dumps(UNREGISTERED)), slug="abfrl"
    )
    with caplog.at_level("INFO"):
        assert scraper.fetch() == []
    assert "read no jobs" in caplog.text and "getTpUrl" in caplog.text


def test_a_listing_lost_mid_walk_is_marked_truncated():
    paged = _paged_route(150)

    def lost_after_the_first_page(method: str, url: str, kwargs: dict):
        if method == "POST" and "offset=99" in url:
            return FakeResponse(200, json.dumps(UNREGISTERED))
        return paged(method, url, kwargs)

    scraper = _scraper(lost_after_the_first_page)
    assert len(scraper.fetch()) == 99
    assert scraper.truncated and "99 of 150" in scraper.truncated


def test_a_description_that_is_only_an_image_is_a_labelled_gap():
    """Two of Equitas's 1,159 descriptions are one embedded `data:image/png` and no text."""
    details = _details()
    details[CONCEPT_LEAD]["response"]["jobDescription"] = (
        '<p><img src="data:image/png;base64,iVBORw0KGgo="></p>'
    )
    scraper = _scraper(_board_route(details=details))
    job = _jobs(scraper)[CONCEPT_LEAD]
    assert job.description is None and job.employment_type == "Permanent"
    assert scraper.detail_losses["no text in jobDescription"] == 1


def test_the_alias_key_is_the_label_the_portal_names_itself(monkeypatch):
    """`dedupe_boards` groups Boards on `alias_key` and compares it with live slugs, so it must be
    a label. `urlinfo` states the portal's own host; it equalled the label on 104 of 104 live
    Boards (2026-09-25), and a label that names another portal would be that portal's alias."""

    def route(method: str, url: str, kwargs: dict):
        assert method == "GET" and url.endswith("/cp/urlinfo")
        return FakeResponse(
            200, json.dumps({"response": {"url": "HDFCErgoCareers.peoplestrong.com"}})
        )

    assert _scraper(route, slug="hdfcergo").alias_key() == "hdfcergocareers"
    unregistered = {
        "response": None,
        "messageCode": {"code": 201, "messages": "Something went wrong"},
    }
    assert (
        _scraper(
            lambda m, u, k: FakeResponse(200, json.dumps(unregistered))
        ).alias_key()
        is None
    )
    assert _scraper(lambda m, u, k: FakeResponse(503, "")).alias_key() is None
