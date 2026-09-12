"""Tests for the Meta (metacareers.com) scraper (headstart.scrapers.meta).

The fixture is a real capture (2026-09-11): a trimmed `/jobsearch/sitemap.xml` naming three
postings, two real job pages fetched straight (no special headers/params, unlike icims), and a
third page — a sitemap entry one id past a real posting — captured live and found to answer
HTTP 200 with no JSON-LD `JobPosting` block at all, the module docstring's "stale sitemap entry"
detail-gap shape. See the module docstring for what these captures measured.
"""

from __future__ import annotations

import json
import pathlib

from headstart.scrapers.meta import _ld_fields, _sitemap_rows
from headstart.scrapers.registry import get_scraper

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "meta_careers.json").read_text()
)
_HOST = _FIXTURE["host"]
_SCRAPED_AT = "2026-09-11T00:00:00+00:00"


def _raw_from_fixture() -> list[dict]:
    """The `fetch_raw` payload, assembled from the fixture without touching the network."""
    rows = []
    for job_id, url, lastmod in _sitemap_rows(_FIXTURE["sitemap_xml"]):
        page = _FIXTURE["pages"].get(job_id) or (
            _FIXTURE["empty_page"] if job_id == _FIXTURE["empty_id"] else None
        )
        rows.append(
            {
                "id": job_id,
                "url": url,
                "lastmod": lastmod,
                "fields": _ld_fields(page) if page else None,
            }
        )
    return rows


# --- the listing surface ------------------------------------------------------------------


def test_sitemap_rows_reads_id_url_and_lastmod() -> None:
    rows = _sitemap_rows(_FIXTURE["sitemap_xml"])
    assert len(rows) == 3
    for job_id, url, lastmod in rows:
        assert job_id.isdigit()
        assert url == f"https://{_HOST}/profile/job_details/{job_id}/"
        assert lastmod is not None


def test_sitemap_rows_dedupes_repeated_ids() -> None:
    xml = (
        "<url><loc>https://www.metacareers.com/profile/job_details/1/</loc>"
        "<lastmod>2026-01-01T00:00:00-07:00</lastmod></url>"
        "<url><loc>https://www.metacareers.com/profile/job_details/1/</loc>"
        "<lastmod>2026-02-02T00:00:00-07:00</lastmod></url>"
    )
    rows = _sitemap_rows(xml)
    assert [r[0] for r in rows] == ["1"]
    assert rows[0][2] == "2026-01-01T00:00:00-07:00", "first occurrence wins"


def test_slug_from_prefers_the_url_host_over_a_display_name_tenant() -> None:
    """The ledger's one row holds `tenant="Meta"` (a real name) and the host in `url` — the
    inverse of most ATSes, so the host must come from `url`, not `tenant`."""
    from headstart.scrapers.meta import MetaScraper

    assert (
        MetaScraper.slug_from("Meta", "https://www.metacareers.com/jobs")
        == "www.metacareers.com"
    )


# --- the detail surface: no JSON-LD is a gap, not an error ------------------------------------


def test_a_stale_sitemap_entry_yields_no_fields() -> None:
    """A sitemap entry whose posting has since closed answers HTTP 200 with no JobPosting
    JSON-LD at all — captured live one id past a real posting. It must parse to None so the
    caller counts it as a detail gap and marks the Board truncated (ADR-0053), rather than a
    silently short list reading as a delisting."""
    assert _ld_fields(_FIXTURE["empty_page"]) is None
    # ... while a real posting's page parses fine.
    assert _ld_fields(_FIXTURE["pages"]["4454815524774630"]) is not None


def test_parse_drops_a_job_whose_detail_fetch_did_not_arrive() -> None:
    scraper = get_scraper("meta", _HOST)
    raw = [
        {
            "id": "1",
            "url": "https://h/profile/job_details/1/",
            "lastmod": None,
            "fields": None,
        }
    ]
    assert scraper.parse(raw, _SCRAPED_AT) == []


# --- dates ------------------------------------------------------------------------------------


def test_posted_at_uses_the_boards_own_date() -> None:
    """`datePosted` was measured stable across two fetches 4 seconds apart (module docstring),
    unlike `validThrough`, which is not read at all."""
    scraper = get_scraper("meta", _HOST)
    raw = [
        {
            "id": "1",
            "url": "https://h/profile/job_details/1/",
            "lastmod": "2026-01-01T00:00:00-07:00",
            "fields": {"title": "T", "posted_at": "2025-06-10T13:32:30-07:00"},
        }
    ]
    assert scraper.parse(raw, _SCRAPED_AT)[0].posted_at == "2025-06-10T13:32:30-07:00"


def test_posted_at_falls_back_to_the_sitemaps_lastmod() -> None:
    scraper = get_scraper("meta", _HOST)
    raw = [
        {
            "id": "1",
            "url": "https://h/profile/job_details/1/",
            "lastmod": "2026-01-01T00:00:00-07:00",
            "fields": {"title": "T", "posted_at": None},
        }
    ]
    assert scraper.parse(raw, _SCRAPED_AT)[0].posted_at == "2026-01-01T00:00:00-07:00"


# --- locations: often a list, only the first is used ------------------------------------------


def test_first_location_is_used_when_a_posting_lists_many() -> None:
    """This posting's captured JSON-LD lists 9 sites; only the first is kept (module docstring:
    no ranking signal exists to prefer one, matching icims/eightfold)."""
    fields = _ld_fields(_FIXTURE["pages"]["998357492128826"])
    assert fields["location"] == "Sunnyvale, CA, US"


def test_telecommute_job_location_type_sets_remote_true() -> None:
    fields = _ld_fields(_FIXTURE["pages"]["998357492128826"])
    assert fields["remote"] is True


def test_no_job_location_type_leaves_remote_unset() -> None:
    """Falls through to `is_remote(location)` in `parse`, not decided at the JSON-LD layer."""
    fields = _ld_fields(_FIXTURE["pages"]["4454815524774630"])
    assert fields["remote"] is None


# --- description: three JSON-LD keys, not one --------------------------------------------------


def test_description_folds_in_responsibilities_and_qualifications() -> None:
    """Meta ships these as three separate JSON-LD keys rather than one blob (module docstring:
    measured present on every one of 80 sampled postings) — a plain `description` read alone
    would drop the job's actual responsibilities and requirements."""
    scraper = get_scraper("meta", _HOST)
    jobs = scraper.parse(_raw_from_fixture(), _SCRAPED_AT)
    job = next(j for j in jobs if j.id.endswith("4454815524774630"))
    assert "Responsibilities:" in job.description
    assert "Minimum Qualifications:" in job.description


# --- end to end on the real capture -------------------------------------------------------------


def test_the_fixture_board_end_to_end() -> None:
    scraper = get_scraper("meta", _HOST, "Meta")
    jobs = scraper.parse(_raw_from_fixture(), _SCRAPED_AT)
    # Two real postings parse; the stale (no-JSON-LD) sitemap entry is dropped, not fabricated.
    assert len(jobs) == 2
    ids = {j.id for j in jobs}
    assert ids == {
        f"meta:{_HOST}:4454815524774630",
        f"meta:{_HOST}:998357492128826",
    }
    for job in jobs:
        assert job.ats == "meta"
        assert job.company == "Meta"
        assert job.url.startswith("https://www.metacareers.com/profile/job_details/")
        assert job.department is None  # not exposed by this surface (module docstring)
        assert job.description
