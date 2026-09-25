"""Tests for the iCIMS scraper (headstart.scrapers.icims).

The fixture is a real capture from `canisiusuniversity-puzzlehr.icims.com` (2026-09-07): its
sitemap, two job pages fetched *with* `in_iframe=1`, and the same page fetched *without* it — the
branded wrapper, which is the measured shape of trap (c) and is what a dropped query parameter
would silently return.

Three of these tests exist because of a specific measured trap rather than a hypothetical:
`datePosted` is fabricated per request, `baseSalary` is non-standard schema.org with no stated
period, and a wrapper page is HTTP 200 with no JSON-LD at all. See the module docstring.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.jobs.salary import extract
from headstart.scrapers.icims import (
    _LD_KEEP,
    _detail_url,
    _ld_fields,
    _public_url,
    _salary,
    _sitemap_rows,
    _stated_date,
)
from headstart.scrapers.registry import get_scraper

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "icims_canisius.json").read_text()
)
_HOST = _FIXTURE["host"]
_SCRAPED_AT = "2026-09-07T00:00:00+00:00"


def _raw_from_fixture() -> list[dict]:
    """The `fetch_raw` payload, assembled from the fixture without touching the network."""
    return [
        {
            "id": job_id,
            "url": url,
            "posted_at": lastmod,
            "fields": _ld_fields(_FIXTURE["pages"][job_id]),
        }
        for job_id, url, lastmod in _sitemap_rows(_FIXTURE["sitemap_xml"])
        if job_id in _FIXTURE["pages"]
    ]


# --- the listing surface ------------------------------------------------------------------


def test_sitemap_rows_reads_id_url_and_lastmod() -> None:
    rows = _sitemap_rows(_FIXTURE["sitemap_xml"])
    assert rows, "fixture sitemap should list postings"
    for job_id, url, lastmod in rows:
        assert job_id.isdigit()
        assert url.startswith(f"https://{_HOST}/jobs/{job_id}/")
        assert lastmod is None or lastmod[:4].isdigit()


def test_sitemap_rows_skips_non_posting_urls() -> None:
    """All 35 sitemaps sampled carry a non-posting URL — `/jobs/intro` or `/jobs/search`."""
    locs = re.findall(r"<loc>([^<]+)</loc>", _FIXTURE["sitemap_xml"])
    non_postings = [u for u in locs if not re.search(r"/jobs/\d+/[^/]*/job", u)]
    assert non_postings, "fixture should contain a non-posting URL to skip"

    kept = {url for _, url, _ in _sitemap_rows(_FIXTURE["sitemap_xml"])}
    assert kept.isdisjoint(non_postings)
    assert len(kept) == len(locs) - len(non_postings)


def test_sitemap_rows_dedupes_repeated_ids() -> None:
    xml = (
        "<url><loc>https://h.icims.com/jobs/1/a/job</loc><lastmod>2026-01-01</lastmod></url>"
        "<url><loc>https://h.icims.com/jobs/1/a-renamed/job</loc><lastmod>2026-02-02</lastmod></url>"
    )
    rows = _sitemap_rows(xml)
    assert [r[0] for r in rows] == ["1"]
    assert rows[0][2] == "2026-01-01", "first occurrence wins"


# --- URL discipline: trap (c) --------------------------------------------------------------


def test_detail_url_adds_in_iframe_and_public_url_never_has_it() -> None:
    job = f"https://{_HOST}/jobs/12165/some-role/job"
    assert _detail_url(job) == f"{job}?in_iframe=1"
    assert "?" not in _public_url(f"{job}?in_iframe=1&mobile=false")


def test_job_url_delegates_to_public_url_and_matches_url_shape() -> None:
    """:meth:`ICIMSScraper.job_url` (ADR-0153) is a thin wrapper over :func:`_public_url`;
    this pins both that delegation and the result against the class's own ``url_shape``."""
    from headstart.scrapers.icims import ICIMSScraper

    job = f"https://{_HOST}/jobs/12165/some-role/job"
    scraper = get_scraper("icims", _HOST)
    built = scraper.job_url(f"{job}?in_iframe=1")
    assert built == _public_url(f"{job}?in_iframe=1") == job
    assert re.fullmatch(ICIMSScraper.url_shape, built)


def test_wrapper_page_yields_no_fields() -> None:
    """A page fetched without `in_iframe=1` is HTTP 200, ~80KB, and carries no JSON-LD.

    It must parse to None so the caller counts it as a detail gap and marks the Board truncated,
    rather than letting an empty Board read as a delisting (ADR-0053).
    """
    assert _ld_fields(_FIXTURE["wrapper_page"]) is None
    # ... while the same posting fetched WITH the parameter parses fine.
    assert _ld_fields(_FIXTURE["pages"][_FIXTURE["wrapper_for_id"]]) is not None


def test_classic_html_job_page_is_a_fallback_when_jsonld_is_absent() -> None:
    """Live OVG/Spectra pages carry real classic iCIMS HTML but no JSON-LD. The fallback is
    gated on the job-page header plus content blocks so the branded wrapper remains None."""
    page = (
        pathlib.Path(__file__).parent / "fixtures" / "icims_ovg_classic.html"
    ).read_text(encoding="utf-8")

    fields = _ld_fields(page)

    assert fields is not None
    assert (
        fields["title"]
        == "Operations Staff | Part-Time | Ryan Center and Boss Ice Arena"
    )
    assert "Operations Staff Members" in fields["description"]
    assert "Perform general labor" in fields["description"]
    assert fields["location"] == "US-RI-Kingston"
    assert fields["department"] == "Operations"
    assert fields["employment_type"] == "Regular Part-Time"


def test_parse_drops_a_job_whose_page_did_not_arrive() -> None:
    scraper = get_scraper("icims", _HOST)
    raw = [
        {
            "id": "1",
            "url": "https://h/jobs/1/a/job",
            "posted_at": "2026-01-01",
            "fields": None,
        }
    ]
    assert scraper.parse(raw, _SCRAPED_AT) == []


# --- dates: trap (a) -----------------------------------------------------------------------


def test_validThrough_is_not_readable_and_datePosted_is_filtered() -> None:
    """`validThrough` is fabricated on every board measured, so it is not in the allowlist.

    `datePosted` *is* allowlisted — 78% of boards state a real one — but only reaches a Job
    through `_stated_date`, which is where the fabricating 22% are rejected.
    """
    assert "validThrough" not in _LD_KEEP
    assert "datePosted" in _LD_KEEP


def test_stated_date_rejects_a_fabricated_datePosted() -> None:
    """The fabricated value carries sub-second ms; 0 of 12 fabricating boards ended `.000Z`."""
    assert _stated_date("2024-09-07T19:20:40.774Z") is None
    assert _stated_date("2024-09-07T18:18:43.622Z") is None
    assert _stated_date(None) is None
    assert _stated_date(12345) is None


def test_stated_date_keeps_a_real_datePosted() -> None:
    """A real date is midnight- or hour-anchored; 42 of 42 real ones ended `.000Z`."""
    assert _stated_date("2026-09-04T04:00:00.000Z") == "2026-09-04T04:00:00.000Z"
    assert _stated_date("2020-01-02T00:00:00.000Z") == "2020-01-02T00:00:00.000Z"
    assert _stated_date("  2026-08-24T23:00:00.000Z  ") == "2026-08-24T23:00:00.000Z"


def test_posted_at_prefers_the_boards_own_date_over_lastmod() -> None:
    """Where a board states a real date, `lastmod` must not override it.

    On `careers-goaheadlondon` the two differ by 2,437 days, so this is the difference between
    serving a 2020 posting as 2020 and serving it as last week.
    """
    scraper = get_scraper("icims", _HOST)
    real = [
        {
            "id": "1",
            "url": "https://h/jobs/1/a/job",
            "posted_at": "2026-09-04T17:15:18-04:00",  # the sitemap's lastmod
            "fields": {"title": "T", "posted_at": "2020-01-02T00:00:00.000Z"},
        }
    ]
    assert scraper.parse(real, _SCRAPED_AT)[0].posted_at == "2020-01-02T00:00:00.000Z"


def test_posted_at_falls_back_to_lastmod_where_the_board_fabricates() -> None:
    """`_stated_date` returns None for the fabricating 22%, and lastmod carries those Jobs."""
    scraper = get_scraper("icims", _HOST)
    fabricating = [
        {
            "id": "1",
            "url": "https://h/jobs/1/a/job",
            "posted_at": "2026-09-04T17:15:18-04:00",
            "fields": {"title": "T", "posted_at": None},
        }
    ]
    assert (
        scraper.parse(fabricating, _SCRAPED_AT)[0].posted_at
        == "2026-09-04T17:15:18-04:00"
    )


def test_the_fixture_board_states_real_dates_and_they_win() -> None:
    """End-to-end on the real capture: this board is one of the 78% that state a real date.

    Its `datePosted` values are hour-anchored (`2026-09-02T04:00:00.000Z`) and differ from the
    sitemap's `lastmod`, so this asserts the preference on real data rather than a constructed
    dict — and it fails if `_stated_date` ever starts rejecting genuine dates.
    """
    page = _FIXTURE["pages"][_FIXTURE["wrapper_for_id"]]
    assert '"datePosted"' in page
    stated = _ld_fields(page)["posted_at"]
    assert stated is not None and stated.endswith(".000Z")

    jobs = get_scraper("icims", _HOST).parse(_raw_from_fixture(), _SCRAPED_AT)
    lastmods = {
        job_id: lastmod for job_id, _, lastmod in _sitemap_rows(_FIXTURE["sitemap_xml"])
    }
    assert jobs
    differed = 0
    for job in jobs:
        job_id = job.id.rsplit(":", 1)[1]
        assert job.posted_at.endswith(".000Z"), (
            "the board's own date must win over lastmod"
        )
        differed += job.posted_at != lastmods[job_id]
    assert differed, (
        "fixture should contain a Job whose real date differs from its lastmod"
    )


# --- salary: trap (b) ----------------------------------------------------------------------


def test_salary_reads_the_non_standard_shape() -> None:
    """iCIMS puts min/max directly on the node; schema.org nests them under `value`."""
    assert _salary({"minValue": 95000, "maxValue": 125000, "currency": "USD"}) == (
        "USD 95000-125000 yearly"
    )
    assert _salary(
        {"value": {"minValue": 95000, "maxValue": 125000}, "currency": "USD"}
    ) == ("USD 95000-125000 yearly")


def test_salary_infers_period_from_magnitude() -> None:
    """`unitText` is never present (0/56 measured) and values span 14 to 215,800."""
    assert (
        _salary({"minValue": 30, "maxValue": 45, "currency": "USD"})
        == "USD 30-45 hourly"
    )
    assert (
        _salary({"minValue": 70000, "maxValue": 70000, "currency": "USD"})
        == "USD 70000 yearly"
    )


def test_salary_refuses_a_ceiling_with_no_floor() -> None:
    """2 of 56 captured nodes state only `maxValue`.

    No spelling makes `salary.extract` read a lone figure as a ceiling, so emitting one would
    serve a job's maximum as its minimum and match a `min_salary` filter it should fail.
    """
    assert _salary({"maxValue": 149780.8, "currency": "USD"}) is None
    assert _salary({"minValue": 149780.8, "currency": "USD"}) == "USD 149780.8 yearly"


def test_salary_refuses_figures_straddling_the_period_boundary() -> None:
    """`{"minValue": 16.9, "maxValue": 39520}` is real, captured, and states no coherent range."""
    assert _salary({"minValue": 16.9, "maxValue": 39520, "currency": "USD"}) is None
    assert _salary({"minValue": 16.9, "maxValue": 39.5, "currency": "USD"}) == (
        "USD 16.9-39.5 hourly"
    )


def test_salary_does_not_round_the_figure() -> None:
    """`f"{n:g}"` collapses to 6 significant digits, silently editing a published number."""
    assert _salary({"minValue": 149780.8, "currency": "USD"}) == "USD 149780.8 yearly"
    assert _salary({"minValue": 70000.0, "currency": "USD"}) == "USD 70000 yearly"


def test_salary_is_none_without_a_figure() -> None:
    """27% of postings carry `baseSalary`, and a currency-only node states no amount."""
    assert _salary({"currency": "USD"}) is None
    assert _salary(None) is None


def test_emitted_salary_round_trips_through_the_repo_parser() -> None:
    """The spelling is load-bearing, not cosmetic.

    `salary.extract()` reads "USD 30 hourly" but returns None for "USD 30.00/hour". Since 35 of
    the 55 measured figures are hourly, the slash spelling would null the majority case while
    annual salaries landed — a silent, measurable data loss.
    """
    for node, want_min in (
        ({"minValue": 30, "maxValue": 45, "currency": "USD"}, 62400),  # 30/hr x 2080
        ({"minValue": 95000, "maxValue": 125000, "currency": "USD"}, 95000),
    ):
        span = extract(_salary(node), None, "icims")
        assert span is not None, f"{_salary(node)!r} must be readable by salary.extract"
        assert span.min_annual == want_min

    for job in get_scraper("icims", _HOST).parse(_raw_from_fixture(), _SCRAPED_AT):
        if job.salary:
            assert extract(job.salary, None, "icims") is not None, job.salary


# --- the remaining fields ------------------------------------------------------------------


def test_fields_from_a_real_job_page() -> None:
    fields = _ld_fields(_FIXTURE["pages"][_FIXTURE["wrapper_for_id"]])
    assert fields["title"]
    assert fields["description"] and len(fields["description"]) > 500
    assert fields["location"]
    assert fields["employment_type"]


def test_location_drops_the_unavailable_literal() -> None:
    """iCIMS writes the string `UNAVAILABLE` into unset address parts."""
    node = {
        "jobLocation": {
            "address": {
                "addressLocality": "Buffalo",
                "addressRegion": "UNAVAILABLE",
                "addressCountry": "US",
            }
        }
    }
    assert _ld_fields(_ld_page(node))["location"] == "Buffalo, US"


def test_remote_reads_telecommute() -> None:
    assert _ld_fields(_ld_page({"jobLocationType": "TELECOMMUTE"}))["remote"] is True
    assert _ld_fields(_ld_page({}))["remote"] is None


def test_parse_builds_jobs_off_the_fixture() -> None:
    jobs = get_scraper("icims", _HOST).parse(_raw_from_fixture(), _SCRAPED_AT)
    assert jobs
    for job in jobs:
        assert job.ats == "icims"
        assert job.id.startswith(f"icims:{_HOST}:")
        assert job.title and job.description
        assert "in_iframe" not in job.url, "Job.url is the user-facing page"
        assert job.scraped_at == _SCRAPED_AT


# --- the slug is the host ------------------------------------------------------------------


def test_slug_from_normalises_a_deep_link_to_the_host() -> None:
    """A ledger row can carry a job deep link; `url()` would otherwise append to its path."""
    slug_from = type(get_scraper("icims", _HOST)).slug_from
    assert slug_from("x", f"https://{_HOST}/jobs/23719/a-role/job?in_iframe=1") == _HOST
    assert slug_from("x", f"https://{_HOST}/") == _HOST


def test_url_is_the_sitemap() -> None:
    assert get_scraper("icims", _HOST).url() == f"https://{_HOST}/sitemap.xml"


# --- the detail pass -----------------------------------------------------------------------


@pytest.mark.parametrize("async_fanout", ["1", "0"])
def test_fetch_raw_reads_every_listed_page_and_names_each_loss(
    monkeypatch, async_fanout
) -> None:
    """The Detail pass over the fixture Board on either transport: every listed page is asked
    for with `in_iframe=1`, a page that arrives as the branded wrapper and a page that 404s are
    each a named loss, and a lost page marks the Board truncated, since the page is the Job."""
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", async_fanout)
    rows = _sitemap_rows(_FIXTURE["sitemap_xml"])
    gone_id = next(job_id for job_id, _, _ in rows if job_id not in _FIXTURE["pages"])

    def route(method, url, kwargs):
        if url == f"https://{_HOST}/sitemap.xml":
            return FakeResponse(text=_FIXTURE["sitemap_xml"])
        job_id = url.split("/jobs/", 1)[1].split("/", 1)[0]
        if job_id == gone_id:
            return FakeResponse(404)
        return FakeResponse(
            text=_FIXTURE["pages"].get(job_id, _FIXTURE["wrapper_page"])
        )

    fetcher = FakeFetcher(route)
    scraper = get_scraper("icims", _HOST, fetcher=fetcher)

    raw = scraper.fetch_raw()

    assert sorted(fetcher.urls()[1:]) == sorted(_detail_url(url) for _, url, _ in rows)
    assert {item["id"] for item in raw if item["fields"]} == set(_FIXTURE["pages"])
    assert scraper.detail_losses == {"no JSON-LD on a 200": 4, "HTTP 404": 1}
    assert scraper.truncated.startswith("5/7 job pages unreadable")
    assert len(scraper.parse(raw, _SCRAPED_AT)) == 2


@pytest.mark.parametrize(
    ("lost", "authoritative"),
    [
        # 199/200 = 99.5%: the sitemap's own count measures it, ADR-0083 absorbs it
        pytest.param(1, True, id="99.5%-stays-authoritative"),
        # 197/200 = 98.5%: below MIN_AUTHORITATIVE_SHARE, the Board still leaves scope
        pytest.param(3, False, id="98.5%-truncates"),
    ],
)
def test_a_measured_detail_shortfall_is_tolerated_only_when_negligible(
    lost: int, authoritative: bool
) -> None:
    """The sitemap states the Board's whole set, so an unreadable page is a *measured* shortfall
    and goes through ADR-0121's tolerance, not the unconditional verdict. Observed 2026-09-24:
    `securitycareers-alliedbarton` (1/9199) and `frfrench-equans` (1/1633) each lost their whole
    eviction scope over a single unreadable page."""
    host = "careers-acme.icims.com"
    sitemap = "".join(
        f"<url><loc>https://{host}/jobs/{n}/role-{n}/job</loc></url>"
        for n in range(200)
    )
    page = _ld_page({"description": "d"})

    def route(method, url, kwargs):
        if url == f"https://{host}/sitemap.xml":
            return FakeResponse(text=sitemap)
        job_id = int(url.split("/jobs/", 1)[1].split("/", 1)[0])
        return FakeResponse(404) if job_id < lost else FakeResponse(text=page)

    scraper = get_scraper("icims", host, fetcher=FakeFetcher(route))
    scraper.fetch_raw()

    if authoritative:
        assert scraper.truncated is None
    else:
        assert scraper.truncated == (
            f"{lost}/200 job pages unreadable — those Jobs are listed but unbuilt"
        )


def _ld_page(node: dict) -> str:
    """Wrap a JobPosting node in the script tag the parser looks for."""
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "JobPosting", "title": "T", **node})
        + "</script>"
    )


# --- the Board's company name ---------------------------------------------------------------


def test_the_board_page_is_the_listing_the_template_titles():
    scraper = get_scraper("icims", "careers-peraton.icims.com")
    assert scraper.board_page() == (
        "https://careers-peraton.icims.com/jobs/search?ss=1&in_iframe=1"
    )


def test_a_job_page_states_its_hiring_organization():
    page = _ld_page(
        {"hiringOrganization": {"@type": "Organization", "name": " SYSTRA "}}
    )
    assert _ld_fields(page)["company"] == "SYSTRA"
    assert _ld_fields(_ld_page({}))["company"] is None


def _named(
    *names: str | None, company: str | None = None, title: str = "Job Listings"
) -> str:
    """The Board's company once `fetch_raw` has read job pages stating ``names`` and
    `resolve_company` has read a listing page titled ``title`` ("Job Listings" names no one)."""
    pages = {str(i): name for i, name in enumerate(names)}
    rows = "".join(
        f"<url><loc>https://{_HOST}/jobs/{i}/x/job</loc></url>" for i in pages
    )

    def route(method, url, kwargs):
        if url.endswith("/sitemap.xml"):
            return FakeResponse(text=rows)
        if "/jobs/search" in url:
            return FakeResponse(text=f"<title>{title}</title>")
        job_id = url.split("/jobs/")[1].split("/")[0]
        org = {"hiringOrganization": {"name": pages[job_id]}} if pages[job_id] else {}
        return FakeResponse(text=_ld_page(org))

    scraper = get_scraper("icims", _HOST, company, fetcher=FakeFetcher(route))
    scraper.fetch_raw()
    scraper.resolve_company()
    return scraper.company


def test_the_hiring_organization_names_a_board_whose_title_did_not():
    # careers-systra: its listing title is empty; nine in ten postings is the floor
    assert _named(*["SYSTRA"] * 9, "Systra USA") == "SYSTRA"


def test_postings_that_disagree_leave_the_slug():
    # careers-emcorgroup: its subsidiaries each state their own name
    assert _named(*["EMCOR Group"] * 8, "EMCOR Services", "Dynalectric") == _HOST


def test_the_six_general_dynamics_hosts_agree():
    """One tenant served under six hosts (careers-gdms, careers-c4s, cybercareers-gdms,
    university-gd-ais, careers-gd-ais, cybercareers-gd-ais) must read one name. All six title
    their listing the same, and the title outranks the pages' legal name."""
    title = (
        "Find a Job - General Dynamics Mission Systems Job Listings at General Dynamics "
        "Mission Systems"
    )
    legal = "General Dynamics Mission Systems, Inc"
    assert _named(legal, title=title) == "General Dynamics Mission Systems"


def test_unavailable_states_nothing():
    # six of 52 Boards state only the placeholder; it counts on neither side
    assert _named("UNAVAILABLE", "UNAVAILABLE") == _HOST
    assert _named("UNAVAILABLE", "Allan Myers", "Allan Myers") == "Allan Myers"


def test_the_listing_title_outranks_the_postings():
    # careers-unitedshore: the brand first, the user's call
    assert _named("United Wholesale Mortgage", title="Job Listings at UWM") == "UWM"


def test_a_title_naming_an_office_falls_through_to_the_postings():
    # careers-abilegroup: "Job Listings at Abile Headquarters", pages "Abile Group, Inc."
    title = "Job Listings at Abile Headquarters"
    assert _named("Abile Group, Inc.", title=title) == "Abile Group, Inc."
    assert _named("MACNY's Job Board") == _HOST


def test_a_ledger_name_outranks_both():
    assert _named("SYSTRA", company="Systra Group") == "Systra Group"
