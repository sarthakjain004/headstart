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

from headstart.salary import extract
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
        for job_id, url, lastmod in _sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)
        if job_id in _FIXTURE["pages"]
    ]


# --- the listing surface ------------------------------------------------------------------


def test_sitemap_rows_reads_id_url_and_lastmod() -> None:
    rows = _sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)
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

    kept = {url for _, url, _ in _sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)}
    assert kept.isdisjoint(non_postings)
    assert len(kept) == len(locs) - len(non_postings)


def test_sitemap_rows_dedupes_repeated_ids() -> None:
    xml = (
        "<url><loc>https://h.icims.com/jobs/1/a/job</loc><lastmod>2026-01-01</lastmod></url>"
        "<url><loc>https://h.icims.com/jobs/1/a-renamed/job</loc><lastmod>2026-02-02</lastmod></url>"
    )
    rows = _sitemap_rows(xml, "h.icims.com")
    assert [r[0] for r in rows] == ["1"]
    assert rows[0][2] == "2026-01-01", "first occurrence wins"


# --- URL discipline: trap (c) --------------------------------------------------------------


def test_detail_url_adds_in_iframe_and_public_url_never_has_it() -> None:
    job = f"https://{_HOST}/jobs/12165/some-role/job"
    assert _detail_url(job) == f"{job}?in_iframe=1"
    assert "?" not in _public_url(f"{job}?in_iframe=1&mobile=false")


def test_wrapper_page_yields_no_fields() -> None:
    """A page fetched without `in_iframe=1` is HTTP 200, ~80KB, and carries no JSON-LD.

    It must parse to None so the caller counts it as a detail gap and marks the Board truncated,
    rather than letting an empty Board read as a delisting (ADR-0053).
    """
    assert _ld_fields(_FIXTURE["wrapper_page"]) is None
    # ... while the same posting fetched WITH the parameter parses fine.
    assert _ld_fields(_FIXTURE["pages"][_FIXTURE["wrapper_for_id"]]) is not None


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
        job_id: lastmod
        for job_id, _, lastmod in _sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)
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


def _ld_page(node: dict) -> str:
    """Wrap a JobPosting node in the script tag the parser looks for."""
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "JobPosting", "title": "T", **node})
        + "</script>"
    )
