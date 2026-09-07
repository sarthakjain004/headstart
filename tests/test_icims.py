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


def test_the_ld_allowlist_has_no_date_key() -> None:
    """`datePosted`/`validThrough` are fabricated per request, so they must not be readable.

    This asserts the allowlist rather than the behaviour, because the allowlist is the mechanism:
    putting a date back requires editing `_LD_KEEP`, which fails here.
    """
    assert "datePosted" not in _LD_KEEP
    assert "validThrough" not in _LD_KEEP


def test_fabricated_datePosted_never_reaches_the_job() -> None:
    """The fixture's own JSON-LD still carries the fabricated date; posted_at must ignore it."""
    page = _FIXTURE["pages"][_FIXTURE["wrapper_for_id"]]
    assert '"datePosted"' in page, (
        "fixture should still contain the field we refuse to read"
    )
    fields = _ld_fields(page)
    assert "posted_at" not in fields
    assert not any("date" in k.lower() for k in fields)

    jobs = get_scraper("icims", _HOST).parse(_raw_from_fixture(), _SCRAPED_AT)
    lastmods = {
        job_id: lastmod
        for job_id, _, lastmod in _sitemap_rows(_FIXTURE["sitemap_xml"], _HOST)
    }
    assert jobs
    for job in jobs:
        assert job.posted_at == lastmods[job.id.rsplit(":", 1)[1]]


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
