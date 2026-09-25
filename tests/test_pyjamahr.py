"""Tests for `headstart.scrapers.pyjamahr`.

The fixtures are three real postings from `jobs.pyjamahr.com/hunarstreet-technologies`, captured
2026-09-22: `pyjamahr_listing.json` is the API's own envelope trimmed to those three, and
`pyjamahr_details.json` maps each id to its detail response (with `job_screening_questions`
dropped so the fixture stays readable). They were chosen to cover all three `workplace_type`
values — REMOTE, HYBRID, ON_SITE — a posting with `other_locations`, a visible annual salary, a
visible monthly one, and a hidden one.

Every assertion here pins something measured in
`docs/pyjamahr/2026-09-22_career-api-measurement.md`; several pin a trap the four public
implementations found on GitHub walk into (the dead `remote` boolean, `published_internally`
rows served as public jobs, the `?job_uuid=` link that never 404s).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fake_fetcher import FakeFetcher, FakeResponse

from headstart import company_name
from headstart.jobs import experience, salary
from headstart.jobs.job import html_to_text
from headstart.scrapers.pyjamahr import (
    _API,
    _LIMIT,
    _MAX_PAGES,
    PyjamaHRScraper,
    _experience,
)
from headstart.scrapers.registry import detail_pass_atses, get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "hunarstreet-technologies"
REMOTE_ID, HYBRID_ID, ONSITE_ID = "143305", "96551", "119154"


def _listing() -> dict:
    with open(FIXTURES / "pyjamahr_listing.json", encoding="utf-8") as fh:
        return json.load(fh)


def _details() -> dict:
    with open(FIXTURES / "pyjamahr_details.json", encoding="utf-8") as fh:
        return json.load(fh)


def _raw() -> dict:
    """What `fetch_raw` returns once the detail pass has run."""
    return {"results": _listing()["results"], "details": _details()}


def _scraper(fetcher: FakeFetcher | None = None) -> PyjamaHRScraper:
    if fetcher is None:
        return get_scraper("pyjamahr", SLUG, "HunarStreet Technologies")
    return PyjamaHRScraper(SLUG, "HunarStreet Technologies", fetcher=fetcher)


def _jobs(raw: dict | None = None) -> dict:
    return {
        j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(raw or _raw(), SCRAPED_AT)
    }


# --------------------------------------------------------------------------- the URL contract


def test_the_listing_is_keyed_by_company_slug_and_asks_for_the_whole_board():
    """`company_slug`, not the `company_uuid` the original research keyed on: the slug is the
    board URL's own path segment and the sitemap's key, so nothing has to be resolved. `limit`
    is honoured with no ceiling (999,999,999 returned a 643-row Board whole), so one call reads
    every Board in the pool."""
    url = _scraper().url()
    assert url == f"{_API}?company_slug={SLUG}&limit={_LIMIT}"
    assert "company_uuid" not in url


def test_the_detail_url_carries_the_company_slug():
    """Without the company key the detail endpoint 404s, and so does another tenant's."""
    request = _scraper().detail_request({"id": int(REMOTE_ID)})
    assert request.url == f"{_API}{REMOTE_ID}/?company_slug={SLUG}"


def test_the_job_url_is_the_slug_page_and_matches_the_declared_shape():
    """`/{company}/{job-slug}` is the page's own canonical link (200, JobPosting JSON-LD). The
    numeric id and the uuid both 307 to a `?job_uuid=` form of the *board* page, which answers
    200 for any value at all — so it can never be a job link."""
    slugs = {str(r["id"]): r["slug"] for r in _listing()["results"]}
    for job_id, job in _jobs().items():
        assert job.url == f"https://jobs.pyjamahr.com/{SLUG}/{slugs[job_id]}"
        assert re.fullmatch(PyjamaHRScraper.url_shape, job.url)


def test_a_slugless_row_links_to_the_board_page():
    """6 of 8,897 rows carry no `slug` (e.g. truww's 231060). The board page lists the job, so
    that is where the link lands — still inside `url_shape`."""
    row = {"id": 231060, "slug": None, "title": "Interior Measurement Executive"}
    job = _scraper().parse({"results": [row], "details": {}}, SCRAPED_AT)[0]
    assert job.url == f"https://jobs.pyjamahr.com/{SLUG}"
    assert re.fullmatch(PyjamaHRScraper.url_shape, job.url)


# ------------------------------------------------------------------- what the board itself shows


def test_published_internally_rows_are_dropped():
    """The API returns them (112 of 8,897 rows, 39 tenants); the board's own page filters them
    out before rendering. They are internal postings, not public jobs."""
    internal = {
        "id": 354734,
        "slug": "senior-collections-credit-control-specialist",
        "title": "Senior Collections (Credit Control) Specialist",
        "published_internally": True,
        "workplace_type": "REMOTE",
        "location": "India",
    }
    raw = {"results": [*_listing()["results"], internal], "details": {}}
    jobs = _scraper().parse(raw, SCRAPED_AT)
    assert len(jobs) == 3
    assert "354734" not in {j.id.rsplit(":", 1)[1] for j in jobs}


def test_the_detail_pass_skips_internal_rows_too():
    """No fetch for a Job that never ships."""
    internal = {"id": 354734, "slug": "x", "title": "x", "published_internally": True}
    listing = {"count": 4, "next": None, "results": [*_listing()["results"], internal]}
    details = _details()

    def route(method, url, kwargs):
        if url.startswith(f"{_API}?"):
            return FakeResponse(text=json.dumps(listing))
        return FakeResponse(text=json.dumps(details[url[len(_API) :].split("/", 1)[0]]))

    fetcher = FakeFetcher(route)
    scraper = _scraper(fetcher)
    raw = scraper.fetch_raw()
    assert set(raw["details"]) == {REMOTE_ID, HYBRID_ID, ONSITE_ID}
    assert not any(f"{_API}354734/" in url for url in fetcher.urls())
    # The listing keeps the internal row: `count` includes it, so the shortfall check must too.
    assert len(raw["results"]) == 4
    assert scraper.truncated is None


# ------------------------------------------------------------------------------ field mapping


def test_remote_reads_workplace_type_not_the_remote_boolean():
    """The detail's `remote` was `false` on every one of 1,741 postings, including the 102 whose
    `workplace_type` was REMOTE. HYBRID is not remote (ashby's rule)."""
    jobs = _jobs()
    assert (
        _details()[REMOTE_ID]["remote"] is False
    )  # the trap, pinned from the real payload
    assert jobs[REMOTE_ID].remote is True
    assert jobs[HYBRID_ID].remote is False
    assert jobs[ONSITE_ID].remote is False


def test_remote_falls_back_to_the_location_only_when_no_type_is_stated():
    """51 of 8,897 rows state no type. Of the 7,674 stating ON_SITE or HYBRID, none names a
    remote location, so a stated type is never overridden by the guess."""
    rows = [
        {
            "id": 1,
            "slug": "a",
            "title": "a",
            "workplace_type": None,
            "location": "Remote",
        },
        {
            "id": 2,
            "slug": "b",
            "title": "b",
            "workplace_type": None,
            "location": "Pune, India",
        },
        {
            "id": 3,
            "slug": "c",
            "title": "c",
            "workplace_type": "ON_SITE",
            "location": "Remote",
        },
    ]
    jobs = _jobs({"results": rows, "details": {}})
    assert jobs["1"].remote is True
    assert jobs["2"].remote is False
    assert jobs["3"].remote is False


def test_location_joins_other_locations_after_the_primary():
    """`other_locations` is non-empty on 15.7% of rows; "; "-joined so a posting open in several
    cities matches the substring location filter on each."""
    jobs = _jobs()
    assert jobs[HYBRID_ID].location == (
        "Hyderabad, Telangana, India; Bangalore, Karnataka, India"
    )
    assert jobs[REMOTE_ID].location == "India"


def test_description_posted_at_and_employment_type_come_from_the_detail():
    """The listing states none of them. `created_at` keeps the tenant's own zone offset."""
    jobs, details = _jobs(), _details()
    for job_id in (REMOTE_ID, HYBRID_ID, ONSITE_ID):
        assert jobs[job_id].description == html_to_text(details[job_id]["description"])
        assert "<" not in jobs[job_id].description
        assert jobs[job_id].posted_at == details[job_id]["created_at"]
        assert jobs[job_id].employment_type == "Full Time"  # `FULLTIME`
    assert jobs[REMOTE_ID].posted_at.endswith("+05:30")


def test_a_missing_detail_payload_still_emits_the_job():
    """Enrichment, not a hard dependency: the identity fields come from the listing."""
    jobs = _jobs({"results": _listing()["results"], "details": {}})
    assert len(jobs) == 3
    job = jobs[REMOTE_ID]
    assert job.title == "Technical Support - Tier 2 | HK"
    assert job.location == "India" and job.remote is True
    assert job.experience == "5-8 years"
    assert job.description is None
    assert job.posted_at is None
    assert job.employment_type is None
    assert job.salary is None


def test_an_unobserved_job_type_passes_through_as_the_provider_spells_it():
    row = {"id": 1, "slug": "a", "title": "a"}
    jobs = _jobs({"results": [row], "details": {"1": {"job_type": "SEASONAL"}}})
    assert jobs["1"].employment_type == "SEASONAL"
    jobs = _jobs({"results": [row], "details": {"1": {"job_type": "INTERN"}}})
    assert jobs["1"].employment_type == "Internship"


def test_department_is_the_listing_department_name():
    jobs = _jobs()
    assert jobs[HYBRID_ID].department == "sales"
    assert jobs[REMOTE_ID].department is None


def test_experience_is_the_listing_bounds_as_a_string_the_field_parser_reads():
    """Both bounds are floats on every row, always integral and never inverted."""
    jobs = _jobs()
    assert jobs[REMOTE_ID].experience == "5-8 years"
    assert jobs[HYBRID_ID].experience == "0-2 years"
    assert jobs[ONSITE_ID].experience == "1-5 years"
    span = experience.from_field(jobs[REMOTE_ID].experience)
    assert (span.min_years, span.max_years) == (5, 8)
    # Coinciding bounds keep their ceiling — "3 years" would read as open-ended — then a floor
    # alone, and nothing stated.
    assert _experience({"min_experience": 3.0, "max_experience": 3.0}) == "3-3 years"
    exact = experience.from_field("3-3 years")
    assert (exact.min_years, exact.max_years) == (3, 3)
    assert _experience({"min_experience": 2.0, "max_experience": None}) == "2 years"
    assert _experience({"min_experience": None, "max_experience": 4.0}) is None


# ------------------------------------------------------------------------------------- salary


def test_salary_is_emitted_only_when_the_tenant_marks_it_visible():
    """Of 1,741 details, all 1,236 marked visible carried both bounds and none of the 505 hidden
    carried either. The hidden one here (`ONSITE_ID`) states `currency` and `salary_type` but
    no bounds — and would be refused on the gate alone even if it leaked them."""
    jobs = _jobs()
    assert jobs[REMOTE_ID].salary == "1000000-1200000 INR per-year"
    assert jobs[HYBRID_ID].salary == "10000-22000 INR per-month"
    assert jobs[ONSITE_ID].salary is None
    leaked = {**_details()[ONSITE_ID], "min_salary": 20000.0, "max_salary": 30000.0}
    assert _scraper()._salary_field(leaked) is None


def test_the_salary_spelling_is_what_the_field_parser_reads():
    """No dedicated Tier-1 parser: `_field_generic` must read the range, the code and the period
    on its own. The monthly figure has to come back multiplied, or every INR-per-month posting
    would be served 12x too low."""
    annual = salary.from_field("1000000-1200000 INR per-year", "pyjamahr")
    assert (annual.min_annual, annual.max_annual, annual.currency) == (
        1_000_000,
        1_200_000,
        "INR",
    )
    monthly = salary.from_field("10000-22000 INR per-month", "pyjamahr")
    assert (monthly.min_annual, monthly.max_annual) == (120_000, 264_000)


def test_large_amounts_are_written_as_digits_never_scientific_notation():
    """`f"{1200000.0:g}"` is `1.2e+06` — keka's salary test pins the same trap."""
    detail = {
        "is_salary_visible": True,
        "min_salary": 1000000.0,
        "max_salary": 1200000.0,
        "currency": "INR",
        "salary_type": "ANNUAL",
    }
    assert _scraper()._salary_field(detail) == "1000000-1200000 INR per-year"
    assert _scraper()._salary_field({**detail, "max_salary": 1200000.5}) == (
        "1000000-1200000.50 INR per-year"
    )


def test_an_unobserved_salary_period_yields_no_salary():
    """Annual is the parser's default, so a WEEKLY figure passed through bare would be read at
    1/52 of its value. No figure beats a wrong one."""
    detail = {
        "is_salary_visible": True,
        "min_salary": 5000.0,
        "max_salary": 6000.0,
        "currency": "USD",
        "salary_type": "WEEKLY",
    }
    assert _scraper()._salary_field(detail) is None
    assert _scraper()._salary_field({**detail, "salary_type": "HOURLY"}) == (
        "5000-6000 USD per-hour"
    )


# --------------------------------------------------------------------------------- the walk


class _FakePages:
    """Serve a Board as `pages` consecutive envelopes, each naming the next by URL."""

    def __init__(self, pages: list[list[int]], count: int, endless: bool = False):
        self.pages, self.count, self.endless = pages, count, endless
        self.served: list[str] = []

    def __call__(self, url: str) -> str:
        self.served.append(url)
        n = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
        rows = self.pages[n - 1] if n <= len(self.pages) else []
        more = self.endless or n < len(self.pages)
        return json.dumps(
            {
                "count": self.count,
                "next": f"{_API}?company_slug={SLUG}&page={n + 1}" if more else None,
                "results": [
                    {"id": i, "slug": f"j{i}", "title": f"job {i}"} for i in rows
                ],
            }
        )


def _walked(monkeypatch, fake: _FakePages) -> PyjamaHRScraper:
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_get", fake)
    # Detail pass off: the listing walk is what is under test.
    monkeypatch.setattr(scraper, "fan_out", lambda items, fn, **kw: [None] * len(items))
    monkeypatch.setattr(
        scraper, "fan_out_async", lambda items, fn, **kw: [None] * len(items)
    )
    return scraper


def test_the_walk_follows_next_until_it_is_null(monkeypatch):
    """One call in practice (`_LIMIT`), but a `next` the API sets is followed — so a future
    ceiling on `limit` cannot silently shorten a Board."""
    fake = _FakePages([[1, 2], [3, 4], [5]], count=5)
    scraper = _walked(monkeypatch, fake)
    raw = scraper.fetch_raw()
    assert [r["id"] for r in raw["results"]] == [1, 2, 3, 4, 5]
    assert len(fake.served) == 3
    assert scraper.truncated is None


def test_a_walk_short_of_count_is_marked_truncated(monkeypatch):
    """`count` equalled the rows served on all 757 Boards measured, so a shortfall is the API
    changing under us — reported through the ADR-0121 tolerance, since the total is stated."""
    scraper = _walked(monkeypatch, _FakePages([[1, 2, 3]], count=10))
    scraper.fetch_raw()
    assert scraper.truncated is not None
    assert "3 of 10" in scraper.truncated


def test_hitting_the_page_cap_marks_truncated(monkeypatch):
    scraper = _walked(
        monkeypatch, _FakePages([[1]] * _MAX_PAGES, count=10_000, endless=True)
    )
    scraper.fetch_raw()
    assert scraper.truncated is not None
    assert str(_MAX_PAGES) in scraper.truncated


def test_an_unknown_slug_parses_to_no_jobs_and_is_not_an_error():
    """HTTP 200 with `count: 0` — byte-identical to a live Board with nothing open. The scraper
    does not try to tell them apart; the liveness prober does, off the board page."""
    empty = {"count": 0, "next": None, "previous": None, "results": []}
    assert _scraper().parse({**empty, "details": {}}, SCRAPED_AT) == []


# ------------------------------------------------------------------------------- detail gaps


def test_a_failed_detail_is_a_counted_gap_not_a_failed_board():
    """A posting that closed between the listing and the detail call 404s with the same body an
    unknown id does. The Job is still listed and still emitted; the Board is not truncated."""
    listing = {"count": 3, "next": None, "results": _listing()["results"]}

    def route(method, url, kwargs):
        if url.startswith(f"{_API}?"):
            return FakeResponse(text=json.dumps(listing))
        return FakeResponse(404, '{"detail": "Not found."}')

    scraper = _scraper(FakeFetcher(route))
    raw = scraper.fetch_raw()
    assert raw["details"] == {}
    assert scraper.detail_losses == {"HTTP 404": 3}
    assert scraper.truncated is None
    assert len(scraper.parse(raw, SCRAPED_AT)) == 3


def test_the_tech_gate_skips_non_tech_details_but_still_emits_the_job(monkeypatch):
    """ADR-0166's gate, taken as an exact site: `parse` reads `title` and `department_name` off
    the listing row and the detail overrides neither. Armed only inside the pipeline
    (`have_details` set), it spares the fetch for a posting `filter_tech` will drop — and that
    posting still ships as a Job, without a description."""
    monkeypatch.delenv("HEADSTART_TECH_GATE", raising=False)
    rows = [
        {"id": 1, "slug": "swe", "title": "Backend Engineer", "department_name": None},
        {"id": 2, "slug": "chef", "title": "Head Chef", "department_name": "Kitchen"},
    ]
    listing = {"count": 2, "next": None, "results": rows}

    def route(method, url, kwargs):
        if url.startswith(f"{_API}?"):
            return FakeResponse(text=json.dumps(listing))
        return FakeResponse(
            text=json.dumps({"description": "<p>body</p>", "job_type": "FULLTIME"})
        )

    fetcher = FakeFetcher(route)
    scraper = _scraper(fetcher)
    scraper.have_details = frozenset()  # the pipeline's signal; nothing held yet
    raw = scraper.fetch_raw()
    assert set(raw["details"]) == {"1"}
    assert not any(f"{_API}2/" in url for url in fetcher.urls())
    jobs = {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(raw, SCRAPED_AT)}
    assert set(jobs) == {"1", "2"}
    assert jobs["1"].description == "body"
    assert jobs["2"].description is None
    assert scraper.telemetry.get("tech_gated_details") == 1


def test_the_scraper_declares_a_detail_pass():
    """`description` comes from a second fetch, so it can go missing (ADR-0050)."""
    assert PyjamaHRScraper.has_detail_pass is True
    assert "pyjamahr" in detail_pass_atses()


# ------------------------------------------------------------------------------ company name


def test_the_company_name_is_the_board_page_title():
    """The `<title>` is the bare name — equal to the SSR payload's `companyDetails.name` on 757
    of 757 live tenants — so the shared resolver reads it through a catch-all, and its shared
    refusals still hold: a separator or a title that is the slug itself names nothing. A name
    written as a domain is the company's own (ADR-0212)."""
    assert _scraper().board_page() == f"https://jobs.pyjamahr.com/{SLUG}"
    assert company_name.from_title("pyjamahr", "Octa Byte AI Pvt Ltd", "8byte") == (
        "Octa Byte AI Pvt Ltd"
    )
    assert company_name.from_title("pyjamahr", "RealPage | Rexera", "rexera") is None
    assert (
        company_name.from_title("pyjamahr", "aainacareers.com", "aainacareerscom")
        == "aainacareers.com"
    )
    assert company_name.from_title("pyjamahr", "smallcase", "smallcase") is None


def test_a_row_with_no_id_is_a_labelled_unattempted_detail_not_a_silent_drop():
    """No request can be formed without the id, so the row is counted and named rather than
    vanishing from the pass — and nothing is sent for it."""
    rows = [{"id": 1, "slug": "swe", "title": "Backend Engineer"}, {"slug": "no-id"}]
    listing = {"count": 2, "next": None, "results": rows}

    def route(method, url, kwargs):
        if url.startswith(f"{_API}?"):
            return FakeResponse(text=json.dumps(listing))
        return FakeResponse(text=json.dumps({"description": "<p>body</p>"}))

    fetcher = FakeFetcher(route)
    scraper = _scraper(fetcher)
    raw = scraper.fetch_raw()
    assert set(raw["details"]) == {"1"}
    assert len(fetcher.urls()) == 2  # the listing and one detail
    assert scraper.detail_losses == {"no job id": 1}
    assert scraper.telemetry["detail_attempted"] == 1
