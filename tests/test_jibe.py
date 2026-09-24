"""Tests for `headstart.scrapers.jibe`.

`jibe_api_jobs.json` holds ten real `/api/jobs` rows captured 2026-09-24 from nine client hosts,
in one envelope, with the unused `meta_data`/`qualifications`/`responsibilities` keys removed. They
cover a posting served in two languages (flyporter 5262), a disallowing and a readable iCIMS tenant
(rm, uhs), three structured salaries (petsmart hourly range, pepsico weekly value, smoothieking
lone ceiling), a repeated multi-place location (dwf), a "Month D, YYYY" date (se) and a
`location_type: ANY` remote place (incyte). `jibe_costco_facets.json` is costco's page-1 envelope
without its jobs: the `state` terms that split a Board over the window.

The robots.txt bodies below are the real ones, verbatim. Every assertion pins something measured in
`docs/jibe/2026-09-24_api-jobs-measurement.md`.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart import http, salary
from headstart.scrapers import jibe
from headstart.scrapers.jibe import JibeScraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"

# `careers.rm.com/robots.txt`, as every Jibe client host serves it (1,138 of 1,143).
JIBE_ALLOW = "User-agent: *\nAllow: /\nSitemap: http://careers.rm.com/sitemap.xml\ncrawl-delay: 5\n"
# `carrefour.jibeapply.com/robots.txt`, the one client host that opts out.
JIBE_DISALLOW = (
    "User-agent: *\nDisallow: /\n"
    "Sitemap: http://carrefour.jibeapply.com/sitemap.xml\ncrawl-delay: 5\n"
)
# `careers-rmeducation.icims.com/robots.txt`: the iCIMS scraper cannot read this tenant.
ICIMS_DISALLOW = "User-agent: *\nDisallow: /\n"
# `careers-universalhealthservices.icims.com/robots.txt`: it can.
ICIMS_ALLOW = (
    "User-agent: *\n"
    "Sitemap: https://careers-universalhealthservices.icims.com/sitemap.xml\n"
    "Disallow: /jobs/*referral\nDisallow: /jobs/referral\nDisallow: /jobs/*login\n"
    "Disallow: /jobs/login\nDisallow: /connect\n"
)
RM_TENANT = "careers-rmeducation.icims.com"
UHS_TENANT = "careers-universalhealthservices.icims.com"


def _rows() -> list[dict]:
    with open(FIXTURES / "jibe_api_jobs.json", encoding="utf-8") as fh:
        return [j["data"] for j in json.load(fh)["jobs"]]


def _row(slug: str, language: str = "en-us") -> dict:
    return next(r for r in _rows() if r["slug"] == slug and r["language"] == language)


def _jobs(rows=None, icims_readable=None, slug="demo") -> dict:
    raw = {
        "rows": _rows() if rows is None else rows,
        "icims_readable": icims_readable or {},
    }
    return {j.id.rsplit(":", 1)[1]: j for j in JibeScraper(slug).parse(raw, SCRAPED_AT)}


def test_a_row_becomes_a_job_linked_on_the_client_host():
    """The native id is the row's `slug` (the backing requisition id), and the link is the
    posting page on the client host, which answers 200 with the posting (a bad id 404s)."""
    job = _jobs(slug="rmeducation")["3713"]
    assert job.id == "jibe:rmeducation:3713"
    assert job.url == "https://rmeducation.jibeapply.com/jobs/3713"
    assert job.title == "Account Manager - UK"
    assert re.fullmatch(JibeScraper.url_shape, job.url)


def test_a_posting_served_in_two_languages_is_one_job_in_english():
    """`totalCount` counts one row per (requisition, language): flyporter serves 5262 as fr-ca
    first and en-us second. One Job per `slug`, its English row whichever comes first."""
    rows = [_row("5262", "fr-ca"), _row("5262", "en-us")]
    jobs = _jobs(rows)
    assert list(jobs) == ["5262"]
    assert jobs["5262"].title == "Flight Dispatcher"


def test_a_repeated_place_is_listed_once():
    """`full_location` already joins every place with "; " and repeats some (dwf 4773)."""
    assert _jobs()["4773"].location == "Manchester, United Kingdom"


def test_department_is_the_first_category():
    """`department` is empty on 99.5% of rows; `categories[0].name` is set on 95.9%."""
    assert _jobs()["3713"].department == "Commercial"


def test_posted_at_reads_both_date_spellings():
    """ISO with `+0000` on 147,276 rows; "Month D, YYYY" on se's 3,541."""
    jobs = _jobs()
    assert jobs["3713"].posted_at == "2026-09-18T12:07:00+00:00"
    assert jobs["134496"].posted_at == "2026-09-22"


def test_a_first_century_year_is_no_date():
    """smoothieking states every date in year 0026 (793 of 793 rows): that is no date we can
    trust, so it is left unset rather than guessed to be 2026."""
    assert _row("a0771354")["posted_date"].startswith("0026-")
    assert _jobs()["a0771354"].posted_at is None


def test_remote_is_read_from_the_location():
    """There is no remote field, and `location_type: ANY` marks a vague place, not a remote one."""
    jobs = _jobs()
    assert jobs["5579"].remote is True  # "Remote Location, Japan"
    assert jobs["3713"].remote is False


def test_employment_type_is_relabelled():
    """The schema.org enum (FULL_TIME on 84,722 rows), in words the employment filter reads; a
    row without the field (24.3%) has none."""
    jobs = _jobs()
    assert _row("4773")["employment_type"] == "FULL_TIME"
    assert jobs["4773"].employment_type == "Full-Time"
    assert "employment_type" not in _row("3713")
    assert jobs["3713"].employment_type is None


def test_a_board_is_named_by_the_hiring_organization_its_rows_agree_on():
    rows = [{**_row("3713"), "slug": str(i)} for i in range(9)]
    rows.append({**_row("3713"), "slug": "9", "hiring_organization": "RM plc"})
    assert _row("3713")["hiring_organization"] == "RM Education Limited"
    assert JibeScraper("rmeducation")._agreed_company(rows) == "RM Education Limited"


def test_icims_hiring_for_itself_is_named():
    """`customer0` is iCIMS's own client: its rows all state "iCIMS Talent Acquisition" (20 of 20,
    2026-09-24) and its title "Careers | ICIMS Careers" is refused. A vendor hiring on its own
    platform is a real employer, the coordinator's call; the curated map can shorten it."""
    rows = [{**_row("3713"), "hiring_organization": "iCIMS Talent Acquisition"}]
    assert JibeScraper("customer0")._agreed_company(rows) == "iCIMS Talent Acquisition"


def test_rows_that_disagree_state_no_name():
    """`hiring_organization` varies within 68 of 277 Boards (subsidiaries, brands): the fixture's
    ten rows come from nine clients, so no name reaches the agreement."""
    assert JibeScraper("rmeducation")._agreed_company(_rows()) is None


def test_a_lowercase_brand_the_rows_state_is_a_name():
    """`flydubai` states its brand lowercase: a field is what the company typed (ADR-0212)."""
    rows = [{**_row("3713"), "hiring_organization": "flydubai"}]
    assert JibeScraper("flydubai")._agreed_company(rows) == "flydubai"


def test_a_title_that_reads_as_an_identifier_is_not_a_name(clock):
    """`primowater` titles its page "primobrands", another client's id."""
    routes = _routes([_page([_row("3713")], 1)], slug="primowater")
    routes[("primowater.jibeapply.com", "/jobs")] = [
        (200, "<title>primobrands</title>")
    ]
    scraper, _ = _scraper(routes, clock, slug="primowater")
    scraper.resolve_company()
    assert scraper.company == "primowater"


def test_a_stated_salary_reaches_extract_annualised():
    """Structured salary exists on three non-iCIMS feeds only. PetSmart's hourly range states no
    currency (empty on all its rows, Canadian ones included); PepsiCo's weekly exact figure states
    USD."""
    jobs = _jobs()
    petsmart = jobs["104479036723-41447668006"].salary
    pepsico = jobs["P1-6651123-2"].salary
    assert petsmart == "13.66-19.16 HOUR"
    assert pepsico == "1538.46-1538.46 USD WEEK"
    hourly = salary.extract(petsmart, None, ats="jibe")
    weekly = salary.extract(pepsico, None, ats="jibe")
    # The shared parser rounds a figure before annualising it (13.66 -> 14 an hour), hence rel.
    assert hourly.currency is None and weekly.currency == "USD"
    assert (hourly.min_annual, hourly.max_annual) == pytest.approx(
        (13.66 * 2080, 19.16 * 2080), rel=0.05
    )
    assert weekly.min_annual == pytest.approx(1538.46 * 52, rel=0.05)


def test_a_lone_ceiling_is_no_salary():
    """A maximum with no minimum (194 rows) must not be served as a floor."""
    assert _row("a0771354")["salary_min_value"] == 0
    assert _row("a0771354")["salary_max_value"] == 10
    assert _jobs()["a0771354"].salary is None


def test_an_unstated_salary_is_none():
    """Bounds of 0 mean "not stated" (the iCIMS-backed rows)."""
    assert _jobs()["3713"].salary is None


def test_a_posting_a_readable_icims_tenant_serves_is_dropped():
    """iCIMS's own scraper reads a tenant whose robots.txt allows its sitemap, and Jibe's `slug`
    is that same requisition id, so the posting would serve twice (option A, ADR-0189). A
    disallowing tenant's posting is kept; so is one whose apply host is not iCIMS at all."""
    readable = {RM_TENANT: False, UHS_TENANT: True}
    jobs = _jobs(icims_readable=readable)
    assert (
        "6496" not in jobs
    )  # uhs -> careers-universalhealthservices.icims.com, readable
    assert "3713" in jobs  # rm -> careers-rmeducation.icims.com, Disallow: /
    assert "P1-6651123-2" in jobs  # pepsico -> olivia.paradox.ai


# --- the network: a fake Fetcher and a fake clock -----------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0)


class _ClockedFetcher(FakeFetcher):
    """The shared fake, answering by (host, path) from `routes`; a route value is a list consumed
    one per request (the last one repeats) or a callable of the parsed query, and an answer is a
    `(status, body)` or an exception to raise. Specialised only to stamp each request with the
    fake clock: the crawl-delay tests read the gaps in `log` as (time, url)."""

    def __init__(self, routes: dict, clock: _Clock) -> None:
        super().__init__(self._answer)
        self.routes, self.clock, self.log = routes, clock, []

    def fetch(self, method, url, **kwargs):
        self.log.append((self.clock.now, url))
        return super().fetch(method, url, **kwargs)

    def _answer(self, method, url, kwargs):
        parts = urlsplit(url)
        route = self.routes[(parts.hostname, parts.path)]
        if callable(route):
            answer = route({k: v[0] for k, v in parse_qs(parts.query).items()})
        else:
            answer = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(answer, Exception):
            return answer
        status, body = answer
        return FakeResponse(status, body if isinstance(body, str) else json.dumps(body))


def _page(rows, total):
    return (200, {"jobs": [{"data": r} for r in rows], "totalCount": total})


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(jibe, "time", fake)
    monkeypatch.setattr(jibe, "_icims_verdicts", {})
    monkeypatch.setattr(jibe, "_icims_last", float("-inf"))
    return fake


def _scraper(routes, clock, slug="rmeducation"):
    fetcher = _ClockedFetcher(routes, clock)
    return JibeScraper(slug, fetcher=fetcher), fetcher


def _routes(listing, slug="rmeducation", robots=(200, JIBE_ALLOW), icims=None):
    host = f"{slug}.jibeapply.com"
    routes = {
        (host, "/robots.txt"): [robots],
        (host, "/api/jobs"): listing,
        (host, "/jobs"): [(200, "<title>RM Education Limited Careers</title>")],
    }
    for tenant, body in (icims or {}).items():
        routes[(tenant, "/robots.txt")] = [body]
    return routes


def test_every_request_to_the_client_host_is_five_seconds_apart(clock):
    """`crawl-delay: 5` on 1,138 of 1,143 client hosts: robots.txt, every listing page and the
    board page are each at least 5 s after the last, and no posting's `apply_url` is fetched."""
    rows = [_row("3713"), _row("6496")]
    listing = lambda q: _page(rows[int(q["page"]) - 1 : int(q["page"])], 2)
    scraper, fetcher = _scraper(
        _routes(
            listing,
            icims={RM_TENANT: (200, ICIMS_DISALLOW), UHS_TENANT: (200, ICIMS_ALLOW)},
        ),
        clock,
    )
    scraper.fetch()
    client = [t for t, url in fetcher.log if "rmeducation.jibeapply.com" in url]
    assert len(client) == 4  # robots.txt, pages 1 and 2, the board page
    assert all(b - a >= jibe.CRAWL_DELAY for a, b in pairwise(client))
    assert not any(
        "/login" in url or url.rstrip("/").endswith("/6496") for _, url in fetcher.log
    )


def test_the_icims_verdict_is_read_once_and_decides_each_posting(clock):
    """One robots.txt fetch per backing tenant per process; the readable tenant's posting drops."""
    rows = [_row("3713"), _row("6496")]
    routes = _routes(
        [_page(rows, 2)],
        icims={RM_TENANT: (200, ICIMS_DISALLOW), UHS_TENANT: (200, ICIMS_ALLOW)},
    )
    scraper, _ = _scraper(routes, clock)
    ids = {j.id for j in scraper.fetch()}
    assert ids == {"jibe:rmeducation:3713"}
    assert scraper.telemetry["icims_covered"] == 1
    again, fetcher2 = _scraper(routes, clock)
    again.fetch()
    assert not any("icims.com" in url for _, url in fetcher2.log)  # cached for the run


def test_a_tenant_that_flips_is_followed_on_the_next_run(clock, monkeypatch):
    """The cache lives for one process, i.e. one run. When uhs's tenant starts disallowing, its
    posting comes back to Jibe; when rm's starts allowing, its posting leaves."""
    rows = [_row("3713"), _row("6496")]
    first = _routes(
        [_page(rows, 2)],
        icims={RM_TENANT: (200, ICIMS_DISALLOW), UHS_TENANT: (200, ICIMS_ALLOW)},
    )
    assert {j.id for j in _scraper(first, clock)[0].fetch()} == {
        "jibe:rmeducation:3713"
    }
    monkeypatch.setattr(jibe, "_icims_verdicts", {})  # the next run is a new process
    flipped = _routes(
        [_page(rows, 2)],
        icims={RM_TENANT: (200, ICIMS_ALLOW), UHS_TENANT: (200, ICIMS_DISALLOW)},
    )
    assert {j.id for j in _scraper(flipped, clock)[0].fetch()} == {
        "jibe:rmeducation:6496"
    }


def test_an_unreachable_icims_robots_keeps_the_posting(clock):
    """RFC 9309: a 5xx robots.txt permits nothing, so iCIMS cannot be assumed to cover it."""
    rows = [_row("6496")]
    routes = _routes([_page(rows, 1)], icims={UHS_TENANT: (503, "")})
    assert [j.id for j in _scraper(routes, clock)[0].fetch()] == [
        "jibe:rmeducation:6496"
    ]


def test_a_client_that_disallows_the_listing_is_not_read(clock):
    """carrefour serves `Disallow: /`: no listing or board-page request is made, and no Job."""
    routes = _routes([_page([_row("3713")], 1)], robots=(200, JIBE_DISALLOW))
    scraper, fetcher = _scraper(routes, clock)
    assert scraper.fetch() == []
    assert [urlsplit(url).path for _, url in fetcher.log] == ["/robots.txt"]
    # no name was read, so the Board is its humanised slug (ADR-0212)
    assert scraper.company == "Rmeducation"


def test_an_unreachable_client_robots_fails_the_board(clock):
    """A 5xx robots.txt permits nothing, and reading it as "no jobs" would evict the Board, so the
    Board fails for the run instead."""
    routes = _routes([_page([_row("3713")], 1)], robots=(503, ""))
    with pytest.raises(RuntimeError, match="robots.txt unreachable"):
        _scraper(routes, clock)[0].fetch()


def test_a_missing_client_robots_allows_everything(clock):
    """RFC 9309: a 4xx robots.txt means no rule applies."""
    routes = _routes(
        [_page([_row("3713")], 1)],
        robots=(404, "not found"),
        icims={RM_TENANT: (200, ICIMS_DISALLOW)},
    )
    assert [j.id for j in _scraper(routes, clock)[0].fetch()] == [
        "jibe:rmeducation:3713"
    ]


def test_a_transient_status_is_retried_at_the_crawl_delay(clock):
    """The shared retry ladder backs off from 0.75 s, so the scraper retries on its own, paced."""
    routes = _routes(
        [(503, ""), _page([_row("3713")], 1)], icims={RM_TENANT: (200, ICIMS_DISALLOW)}
    )
    scraper, fetcher = _scraper(routes, clock)
    assert [j.id for j in scraper.fetch()] == ["jibe:rmeducation:3713"]
    listing = [t for t, url in fetcher.log if "/api/jobs" in url]
    assert len(listing) == 2 and listing[1] - listing[0] >= jibe.CRAWL_DELAY


def test_the_company_is_the_board_page_title(clock):
    routes = _routes(
        [_page([_row("3713")], 1)], icims={RM_TENANT: (200, ICIMS_DISALLOW)}
    )
    scraper, _ = _scraper(routes, clock)
    assert scraper.fetch()[0].company == "RM Education Limited"


# --- pagination, the window and the facet split -------------------------------------------------


def _synthetic(n, prefix="r"):
    """`n` distinct rows cloned from one real row, differing only in `slug` — the listing's shape
    at a size no fixture can carry."""
    base = _row("3713")
    return [{**base, "slug": f"{prefix}{i}", "apply_url": ""} for i in range(n)]


def _paged(rows, total=None, window=None):
    """A listing over `rows`: page N is rows[(N-1)*100 : N*100]; past `window` rows every page
    repeats the last reachable one, as costco's does from page 51."""
    total = len(rows) if total is None else total

    def answer(q):
        page = int(q["page"])
        if window is not None:
            page = min(page, window // jibe.PAGE_SIZE)
        start = (page - 1) * jibe.PAGE_SIZE
        return _page(rows[start : start + jibe.PAGE_SIZE], total)

    return answer


def test_the_walk_ends_at_the_stated_total(clock):
    rows = _synthetic(250)
    scraper, fetcher = _scraper(_routes(_paged(rows)), clock)
    assert len(scraper.fetch()) == 250
    assert sum("/api/jobs" in url for _, url in fetcher.log) == 3
    assert scraper.truncated is None


def test_an_empty_page_ends_the_walk_and_a_shortfall_is_measured(clock):
    """flyporter serves 59 of 61 rows under their total when the total moves mid-walk: a small
    shortfall stays authoritative (ADR-0121), a large one does not."""
    small, _ = _scraper(_routes(_paged(_synthetic(199), total=200)), clock)
    assert len(small.fetch()) == 199 and small.truncated is None
    large, _ = _scraper(_routes(_paged(_synthetic(150), total=300)), clock)
    assert len(large.fetch()) == 150 and large.truncated


def test_a_repeated_page_under_the_window_size_is_the_window(clock):
    """A page with no new (slug, language) pair is the window's signature: stop, and say so."""
    rows = _synthetic(400)
    scraper, fetcher = _scraper(_routes(_paged(rows, window=300)), clock)
    assert len(scraper.fetch()) == 300
    assert "window" in scraper.truncated
    assert sum("/api/jobs" in url for _, url in fetcher.log) == 4


def _facet_listing(by_state, total, window=None):
    """costco's real `filter` envelope (49 `state` terms summing to its 20,093 total) over rows
    per state; an unfiltered query answers page 1 of every row."""
    with open(FIXTURES / "jibe_costco_facets.json", encoding="utf-8") as fh:
        facets = json.load(fh)["filter"]
    everything = [r for rows in by_state.values() for r in rows]

    def answer(q):
        rows = by_state.get(q["state"], []) if "state" in q else everything
        status, body = _paged(rows, window=window)(q)
        body["totalCount"] = len(rows) if "state" in q else total
        if int(q["page"]) == 1 and "state" not in q:
            body["filter"] = facets
        return status, body

    return answer


def test_a_board_over_the_window_is_read_one_state_at_a_time(clock, monkeypatch):
    """Past row 5,100 costco repeats one page, but each `state` query has a window of its own."""
    by_state = {"California": _synthetic(120, "ca"), "Texas": _synthetic(80, "tx")}
    monkeypatch.setattr(jibe, "WINDOW", 150)  # 200 rows stand in for costco's 20,093
    scraper, fetcher = _scraper(_routes(_facet_listing(by_state, total=200)), clock)
    jobs = scraper.fetch()
    assert len(jobs) == 200 and scraper.truncated is None
    states = {
        parse_qs(urlsplit(url).query).get("state", [None])[0] for _, url in fetcher.log
    }
    assert {"California", "Texas"} <= states


def test_a_state_slice_over_the_window_is_a_hard_cap(clock, monkeypatch):
    by_state = {"California": _synthetic(400, "ca")}
    monkeypatch.setattr(jibe, "WINDOW", 150)
    scraper, _ = _scraper(
        _routes(_facet_listing(by_state, total=400, window=200)), clock
    )
    jobs = scraper.fetch()
    assert len(jobs) == 200
    assert "California" in scraper.truncated


def test_rows_no_state_reaches_are_a_measured_shortfall(clock, monkeypatch):
    """The union is checked against the Board's own total: rows no state term selects are missing."""
    by_state = {"California": _synthetic(100, "ca")}
    monkeypatch.setattr(jibe, "WINDOW", 50)
    scraper, _ = _scraper(_routes(_facet_listing(by_state, total=300)), clock)
    assert len(scraper.fetch()) == 100
    assert scraper.truncated == "read 100 of 300 rows"


def test_a_board_without_a_state_facet_is_split_by_category(clock, monkeypatch):
    """UHS exposes no `state` facet; its categories overlap (106 of them sum to 11,990 over 6,056
    rows), so the slices are unioned by posting."""
    rows = _synthetic(120)
    by_category = {"Nursing": rows[:80], "Behavioral Health": rows[60:]}
    facets = {
        "categories": {
            "all": [{"category": c, "numJobs": len(r)} for c, r in by_category.items()]
        }
    }

    def answer(q):
        part = by_category.get(q.get("categories"), rows)
        status, body = _paged(part)(q)
        if "categories" not in q:
            body["filter"] = facets
            body["totalCount"] = 120
        return status, body

    monkeypatch.setattr(jibe, "WINDOW", 100)
    scraper, fetcher = _scraper(_routes(answer), clock)
    assert len(scraper.fetch()) == 120 and scraper.truncated is None
    asked = {
        parse_qs(urlsplit(url).query).get("categories", [None])[0]
        for _, url in fetcher.log
    }
    assert {"Nursing", "Behavioral Health"} <= asked


def test_a_redirect_off_the_client_host_is_not_followed(clock):
    """regiscorp's board page redirects to `www.regiscorp.com/careers`, a host whose robots.txt
    this Board never read: the redirect is left unfollowed, and the name comes from the rows."""
    routes = _routes(
        [_page([_row("3713")], 1)], icims={RM_TENANT: (200, ICIMS_DISALLOW)}
    )
    off_host = SimpleNamespace(
        status_code=302,
        text="",
        headers={"location": "https://www.regiscorp.com/careers"},
        raise_for_status=lambda: None,
    )
    scraper, fetcher = _scraper(routes, clock)
    real = fetcher.fetch
    fetcher.fetch = lambda m, url, **kw: (
        off_host if url.endswith("/jobs") else real(m, url, **kw)
    )
    assert scraper.fetch()[0].company == "RM Education Limited"
    assert not any("regiscorp" in url for _, url in fetcher.log)


def test_a_departed_client_fails_on_dns_not_on_robots(clock):
    """A client with no A record (101 of 1,268 labels) fails as unresolvable, the error the
    ledger's dead verdict rests on, not as an unreachable robots.txt."""
    dns = http.RequestsError("Could not resolve host", code=6)
    routes = _routes([_page([_row("3713")], 1)], robots=dns)
    with pytest.raises(type(dns), match="resolve"):
        _scraper(routes, clock)[0].fetch()


def test_a_robots_redirect_is_followed_even_off_host(clock):
    """RFC 9309 §2.3.1.2 asks a crawler to follow robots.txt's redirects (3 `career.page` vanity
    hosts redirect theirs); every other redirect off the client host stays unfollowed."""
    routes = _routes(
        [_page([_row("3713")], 1)], icims={RM_TENANT: (200, ICIMS_DISALLOW)}
    )
    routes[("www.example-parent.com", "/robots.txt")] = [(200, JIBE_DISALLOW)]
    moved = SimpleNamespace(
        status_code=301,
        text="",
        headers={"location": "https://www.example-parent.com/robots.txt"},
    )
    scraper, fetcher = _scraper(routes, clock)
    real = fetcher.fetch
    fetcher.fetch = lambda m, url, **kw: (
        moved
        if url == "https://rmeducation.jibeapply.com/robots.txt"
        else real(m, url, **kw)
    )
    assert scraper.fetch() == []  # the redirected file disallows everything
    assert any("example-parent.com/robots.txt" in url for _, url in fetcher.log)


def test_a_robots_redirect_to_a_page_is_read_once_not_recursively(clock):
    """A robots.txt that redirects to a homepage (on or off host) is read as that page — no rules,
    so no restriction — in one chain of at most five hops, never by re-asking robots.txt."""
    routes = _routes(
        [_page([_row("3713")], 1)], icims={RM_TENANT: (200, ICIMS_DISALLOW)}
    )
    routes[("www.example-parent.com", "/")] = [
        (200, "<html><title>Parent</title></html>")
    ]
    moved = SimpleNamespace(
        status_code=301,
        text="",
        headers={"location": "https://www.example-parent.com/"},
    )
    scraper, fetcher = _scraper(routes, clock)
    real = fetcher.fetch
    fetcher.fetch = lambda m, url, **kw: (
        moved
        if url == "https://rmeducation.jibeapply.com/robots.txt"
        else real(m, url, **kw)
    )
    assert [j.id for j in scraper.fetch()] == ["jibe:rmeducation:3713"]
    assert sum(url == "https://www.example-parent.com/" for _, url in fetcher.log) == 1


def test_icims_robots_fetches_are_spaced(clock):
    """One process asks each backing tenant once, one at a time, a second apart."""
    rows = [_row("3713"), _row("6496")]
    routes = _routes(
        [_page(rows, 2)],
        icims={RM_TENANT: (200, ICIMS_DISALLOW), UHS_TENANT: (200, ICIMS_ALLOW)},
    )
    scraper, fetcher = _scraper(routes, clock)
    scraper.fetch()
    icims = [t for t, url in fetcher.log if ".icims.com/robots.txt" in url]
    assert len(icims) == 2 and icims[1] - icims[0] >= jibe._ICIMS_INTERVAL
