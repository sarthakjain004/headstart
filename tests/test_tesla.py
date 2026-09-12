"""Tests for `headstart.scrapers.tesla`.

The fixture (`tesla_careers_state.json`) is five real postings trimmed from a live capture of
`GET https://www.tesla.com/cua-api/apps/careers/state`, captured 2026-09-11 via the CDP-capture
route the module docstring describes (no ordinary HTTP client can reach this endpoint — see
`docs/tesla/2026-09-11_api-measurement.md`). They were chosen to cover all four employment types
(`y`: fulltime/parttime/intern/seasonal) and, incidentally, a location id (`32046`, "Delivery
Operations Advisor") that is genuinely absent from `lookup.locations` in the live payload — one
of the 4 (of 1,309 distinct ids referenced by a real listing set) measured missing — so `parse`
must resolve that one to `None` rather than raising or dropping the Job.

`fetch_raw`'s browser-driving half is deliberately untested here, matching `BaseScraper`'s own
split: `parse` is pure and is what tests exercise; the network/browser side is not (see
`base.py`'s class docstring, and `browser_http`'s own tests for the shape a shared browser
transport's test suite takes when the investment is warranted — this one is single-caller and
isn't).
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper
from headstart.scrapers.tesla import SLUG, TeslaScraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _raw():
    with open(FIXTURES / "tesla_careers_state.json", encoding="utf-8") as fh:
        return json.load(fh)


def _jobs():
    scraper = get_scraper("tesla", SLUG, "Tesla")
    return {j.id.rsplit(":", 1)[1]: j for j in scraper.parse(_raw(), SCRAPED_AT)}


def test_registered_under_tesla():
    assert isinstance(get_scraper("tesla", SLUG, "Tesla"), TeslaScraper)


def test_slug_is_fixed_regardless_of_the_ledger_row():
    # ADR-0139: never discovered, never varying — whatever a ledger row's own tenant/url columns
    # say, the slug is always the one fixed host.
    assert TeslaScraper.slug_from("anything", "https://not-tesla.example/") == SLUG


def test_url_is_the_careers_search_page():
    scraper = TeslaScraper(SLUG, "Tesla")
    assert scraper.url() == "https://www.tesla.com/careers/search/"


def test_alias_key_is_the_boards_own_slug():
    # No sibling tenant to alias against (ADR-0139) — must not fall through to the base
    # implementation's live probe, which would hit the same Akamai wall this scraper works
    # around (see the module docstring).
    scraper = TeslaScraper(SLUG, "Tesla")
    assert scraper.alias_key() == SLUG


def test_every_employment_type_maps_through_the_lookup():
    jobs = _jobs()
    assert jobs["224501"].employment_type == "fulltime"
    assert jobs["277989"].employment_type == "parttime"
    assert jobs["281634"].employment_type == "intern"
    assert jobs["283222"].employment_type == "seasonal"


def test_department_resolves_through_the_lookup():
    job = _jobs()["224501"]
    assert job.department == "Tesla AI"


def test_location_resolves_through_the_lookup():
    job = _jobs()["224501"]
    assert job.location == "Palo Alto, California"


def test_a_location_id_missing_from_the_lookup_resolves_to_none():
    # id 32046 (on "Delivery Operations Advisor") is one of the 4 ids a live listing set
    # referenced that `lookup.locations` did not carry (module docstring).
    job = _jobs()["282326"]
    assert job.location is None
    assert job.remote is None  # is_remote(None) — no location to judge from


def test_no_posted_at_is_ever_emitted():
    # Neither the listing nor the per-job detail payload states a posting date (module
    # docstring) — `postUntilDate`/`pu` is a deadline, not a posted-at, and must not be
    # substituted in.
    for job in _jobs().values():
        assert job.posted_at is None


def test_description_is_none_in_this_version():
    # has_detail_pass is False: no per-job description fetch is attempted (module docstring).
    for job in _jobs().values():
        assert job.description is None


def test_job_url_slugifies_the_title_and_keeps_the_trailing_id():
    job = _jobs()["224501"]
    assert (
        job.url
        == "https://www.tesla.com/careers/search/job/ai-engineer-manipulation-optimus-224501"
    )


def test_a_title_with_no_alnum_characters_falls_back_to_the_bare_id():
    from headstart.scrapers.tesla import _job_url

    assert _job_url("123", "!!!") == "https://www.tesla.com/careers/search/job/123"


def test_job_id_is_namespaced_by_ats_and_slug():
    job = _jobs()["224501"]
    assert job.id == f"tesla:{SLUG}:224501"


def test_company_is_whatever_the_caller_passed():
    job = _jobs()["224501"]
    assert job.company == "Tesla"


def test_a_listing_missing_a_title_is_dropped():
    scraper = TeslaScraper(SLUG, "Tesla")
    raw = {
        "listings": [{"id": "1", "t": "", "dp": None, "l": None, "y": None}],
        "lookup": {},
    }
    assert scraper.parse(raw, SCRAPED_AT) == []


def test_a_listing_missing_an_id_is_dropped():
    scraper = TeslaScraper(SLUG, "Tesla")
    raw = {"listings": [{"id": None, "t": "Some Role"}], "lookup": {}}
    assert scraper.parse(raw, SCRAPED_AT) == []
