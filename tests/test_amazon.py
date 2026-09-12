"""Tests for `headstart.scrapers.amazon`.

`tests/fixtures/amazon_listing.json` is three real postings captured live from
`https://www.amazon.jobs/en/search.json` on 2026-09-11: an onsite HR role (double-space-free
posted_date), an onsite AWS data-center role (a title with trailing whitespace on the wire), and
a `VIRTUAL`-located Principal PM role whose `preferred_qualifications` carries a real salary
range — chosen to cover title stripping, remote detection via `locations[].type`, and the
qualifications-into-description fold. `tests/fixtures/amazon_facets.json` is the real
`facets[]=business_category` response, trimmed to its first six buckets (real counts, real
shape). Every measurement behind the scraper's own choices — the 10,000-result offset ceiling,
`hits` being a static ES cap rather than a real total, `business_category` partitioning the
board with zero measured overlap — is in `docs/amazon/2026-09-11_api-measurement.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.amazon import (
    AmazonScraper,
    _full_description,
    _posted_at,
    _remote,
)
from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"
SLUG = "www.amazon.jobs"
HR_ID, AWS_ID, REMOTE_ID = "10537819", "10537803", "10514637"


def _listing() -> list[dict]:
    with open(FIXTURES / "amazon_listing.json", encoding="utf-8") as fh:
        return json.load(fh)


def _facets() -> dict:
    with open(FIXTURES / "amazon_facets.json", encoding="utf-8") as fh:
        return json.load(fh)


def _scraper() -> AmazonScraper:
    return get_scraper("amazon", SLUG, "Amazon")


def _jobs() -> dict[str, object]:
    return {j.id.rsplit(":", 1)[1]: j for j in _scraper().parse(_listing(), SCRAPED_AT)}


# --------------------------------------------------------------------------- identity & URLs


def test_the_slug_is_fixed_and_the_board_key_is_stable():
    scraper = _scraper()
    assert scraper.slug == SLUG
    assert scraper.board_key() == f"amazon:{SLUG}"


def test_the_listing_url_carries_no_category_filter_and_the_page_size_is_100():
    url = _scraper().url()
    assert url == f"https://{SLUG}/en/search.json?offset=0&result_limit=100&sort=recent"


def test_the_job_url_is_the_hosts_own_job_path():
    job = _jobs()[AWS_ID]
    assert job.url == (
        f"https://{SLUG}/en/jobs/{AWS_ID}/data-center-engineering-operations-technician"
    )


def test_the_scraper_declares_no_detail_pass():
    """The listing carries the full description already — nothing a second fetch would add."""
    assert AmazonScraper.has_detail_pass is False


# ------------------------------------------------------------------------------ field mapping


def test_the_core_identity_fields_come_from_the_listing():
    job = _jobs()[AWS_ID]
    assert job.id == f"amazon:{SLUG}:{AWS_ID}"
    assert job.ats == "amazon"
    assert job.company == "Amazon"


def test_the_title_is_stripped_of_trailing_whitespace_the_api_sends():
    """The wire value is `"Data Center Engineering Operations Technician "` — a real trailing
    space, not a typo in the fixture."""
    raw = next(r for r in _listing() if r["id_icims"] == AWS_ID)
    assert raw["title"].endswith(" ")
    job = _jobs()[AWS_ID]
    assert job.title == "Data Center Engineering Operations Technician"


def test_department_reads_job_category_not_business_category():
    """`business_category` (aws/retail/...) is spent entirely on subdividing the listing walk;
    `job_category` ("Operations, IT, & Support Engineering") is the role-family analogue of
    every other scraper's `department`."""
    job = _jobs()[AWS_ID]
    assert job.department == "Operations, IT, & Support Engineering"
    raw = next(r for r in _listing() if r["id_icims"] == AWS_ID)
    assert job.department != raw["business_category"]


def test_employment_type_is_the_job_schedule_type_as_stated():
    assert _jobs()[AWS_ID].employment_type == "full-time"


def test_posted_at_parses_the_human_date_string():
    assert _jobs()[AWS_ID].posted_at == "2026-09-11"


def test_posted_at_handles_the_apis_own_double_space_on_a_single_digit_day():
    """Measured live across a 500-posting sample: a single-digit day emits a double space
    ("September  9, 2026"), which `%B %d, %Y` cannot parse without normalizing first."""
    assert _posted_at("September  9, 2026") == "2026-09-09"
    assert _posted_at("September 11, 2026") == "2026-09-11"


def test_posted_at_is_none_for_unparseable_or_missing_input():
    assert _posted_at(None) is None
    assert _posted_at("") is None
    assert _posted_at("not a date") is None


# ---------------------------------------------------------------------------------- remote


def test_remote_is_true_for_a_virtual_location_entry():
    """The Principal PM fixture's `locations` carries one `VIRTUAL` entry (Nevada) alongside
    an `ONSITE` one (Seattle) — any VIRTUAL entry marks the whole posting remote."""
    job = _jobs()[REMOTE_ID]
    assert job.remote is True


def test_remote_is_false_when_every_location_is_onsite():
    job = _jobs()[AWS_ID]
    assert job.remote is False


def test_remote_falls_back_to_the_location_string_with_no_locations_array():
    assert _remote({"locations": []}, "Remote - India") is True
    assert _remote({}, "US, VA, Ashburn") is False
    assert _remote({}, None) is None


def test_remote_ignores_an_unparseable_locations_entry():
    assert _remote({"locations": ["not json"]}, None) is None


# ------------------------------------------------------------------------------ description


def test_description_folds_in_both_qualifications_blocks():
    """The listing already carries the full description (no detail pass) plus
    `basic_qualifications`/`preferred_qualifications` as separate blobs — folded in so
    `experience.extract()`/`salary.extract()`, which read `description`, can reach the "years
    of experience" and salary-range phrasing that lives only in those blocks."""
    raw = next(r for r in _listing() if r["id_icims"] == AWS_ID)
    job = _jobs()[AWS_ID]
    assert job.description is not None
    assert (
        "2+ years of practical experience" in job.description
    )  # from basic_qualifications
    assert (
        "Knowledge of network design" in job.description
    )  # from preferred_qualifications
    assert "</" not in job.description  # html_to_text stripped the tags
    assert raw["basic_qualifications"] not in job.description  # HTML, not the raw blob


def test_full_description_is_none_for_a_record_with_no_text_at_all():
    assert _full_description({}) is None


def test_full_description_handles_a_record_with_only_the_base_description():
    assert _full_description({"description": "Plain text."}) == "Plain text."


# --------------------------------------------------------------------------- rows dropped


def test_a_record_missing_its_native_id_is_dropped():
    (job,) = AmazonScraper(SLUG, "Amazon").parse(
        [{"id_icims": "", "title": "X"}, {"id_icims": "1", "title": "Y"}], SCRAPED_AT
    )
    assert job.id.endswith(":1")


def test_a_record_missing_its_title_is_dropped():
    (job,) = AmazonScraper(SLUG, "Amazon").parse(
        [{"id_icims": "1", "title": "  "}, {"id_icims": "2", "title": "Y"}], SCRAPED_AT
    )
    assert job.id.endswith(":2")


# -------------------------------------------------------------------------------- pagination


def test_offsets_for_page_a_small_category_in_100s():
    assert AmazonScraper._offsets_for(0) == []
    assert AmazonScraper._offsets_for(1) == [0]
    assert AmazonScraper._offsets_for(100) == [0]
    assert AmazonScraper._offsets_for(101) == [0, 100]
    assert AmazonScraper._offsets_for(250) == [0, 100, 200]


def test_offsets_for_never_crosses_the_10000_offset_ceiling():
    """`offset + result_limit` past 10,000 errors outright — the last valid offset is 9,900."""
    offsets = AmazonScraper._offsets_for(50_000)
    assert offsets[-1] == 9_900
    assert max(offsets) + 100 <= 10_000


def test_categories_reads_the_facet_into_a_flat_value_to_count_map(monkeypatch):
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_get", lambda url=None: json.dumps(_facets()))
    cats = scraper._categories()
    assert cats["aws"] == 8237
    assert cats["fulfillment-ops"] == 1801
    assert len(cats) == 6


def test_fetch_raw_walks_every_category_and_aggregates_the_pages(monkeypatch):
    """Two tiny categories, one page each — exercises the multi-category fan-out end to end
    the way a live run shapes the walk (facets first, then one task per (category, offset))."""
    scraper = _scraper()
    pages_by_offset = {
        ("a", 0): [
            {"id_icims": "1", "title": "X"},
            {"id_icims": "2", "title": "Y"},
            {"id_icims": "3", "title": "Z"},
        ],
        ("b", 0): [{"id_icims": "4", "title": "W"}],
    }
    monkeypatch.setattr(scraper, "_categories", lambda: {"a": 3, "b": 1})
    monkeypatch.setattr(scraper, "_page", lambda task: pages_by_offset.get(task, []))
    monkeypatch.setattr(
        scraper, "fan_out", lambda items, fn, **kw: [fn(item) for item in items]
    )
    monkeypatch.setattr(scraper, "async_fanout_enabled", lambda: False)
    raw = scraper.fetch_raw()
    ids = sorted(j["id_icims"] for j in raw)
    assert ids == ["1", "2", "3", "4"]
    assert scraper.truncated is None


def test_fetch_raw_dedupes_a_native_id_seen_on_more_than_one_page(monkeypatch):
    """Every job carries exactly one `business_category` on the live data measured (module
    docstring), but the aggregation dedupes by native id anyway — the same defensive posture
    every other subdivided scraper here takes rather than trusting a partition to be perfect
    on a board that keeps changing underneath the walk."""
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_categories", lambda: {"a": 2})
    monkeypatch.setattr(
        scraper,
        "_page",
        lambda task: [
            {"id_icims": "1", "title": "X"},
            {"id_icims": "1", "title": "X-again"},
        ],
    )
    monkeypatch.setattr(
        scraper, "fan_out", lambda items, fn, **kw: [fn(item) for item in items]
    )
    monkeypatch.setattr(scraper, "async_fanout_enabled", lambda: False)
    raw = scraper.fetch_raw()
    assert [j["id_icims"] for j in raw] == ["1"]


def test_a_category_past_the_offset_ceiling_is_marked_truncated_unconditionally(
    monkeypatch,
):
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_categories", lambda: {"huge": 20_000})
    monkeypatch.setattr(scraper, "_page", lambda task: [])
    monkeypatch.setattr(
        scraper, "fan_out", lambda items, fn, **kw: [fn(item) for item in items]
    )
    monkeypatch.setattr(scraper, "async_fanout_enabled", lambda: False)
    scraper.fetch_raw()
    assert scraper.truncated and "no offset past 10,000" in scraper.truncated


def test_a_materially_short_walk_is_marked_truncated(monkeypatch):
    """The board-level shortfall check: facet counts said 300 postings, the walk only turned
    up 100 — well outside ADR-0121's 99% tolerance."""
    scraper = _scraper()
    monkeypatch.setattr(scraper, "_categories", lambda: {"a": 300})
    monkeypatch.setattr(
        scraper,
        "_page",
        lambda task: [{"id_icims": "1", "title": "X"}] if task[1] == 0 else [],
    )
    monkeypatch.setattr(
        scraper, "fan_out", lambda items, fn, **kw: [fn(item) for item in items]
    )
    monkeypatch.setattr(scraper, "async_fanout_enabled", lambda: False)
    scraper.fetch_raw()
    assert scraper.truncated and "1 of 300" in scraper.truncated
