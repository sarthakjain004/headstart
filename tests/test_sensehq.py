"""Tests for `headstart.scrapers.sensehq` added with its liveness ledger (2026-09-28).

`sensehq_three_tenants.json` holds one real listing row from each of three live Boards, captured
2026-09-28 with `description_external` cut to 200 characters: zetwerk (a single place whose office
states its country), nishith-desai (nine places in one padded string) and zee (an empty location).
The older parse and page-cap tests live in `tests/test_scrapers.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper
from headstart.scrapers.sensehq import SenseHQScraper

FIXTURES = Path(__file__).parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _jobs():
    with open(FIXTURES / "sensehq_three_tenants.json", encoding="utf-8") as fh:
        raw = json.load(fh)
    jobs = get_scraper("sensehq", "acme").parse(raw, SCRAPED_AT)
    return {j.id.rsplit(":", 1)[1]: j for j in jobs}


def test_a_single_place_carries_its_offices_country():
    assert _jobs()["56380"].location == "Rudrapur, India"


def test_several_places_are_kept_as_stated_with_their_padding_collapsed():
    """The office is one of nine places (Palo Alto, Singapore, New York among them), so its
    country would mislabel the rest; the string is kept, its runs of spaces collapsed."""
    assert _jobs()["53886"].location == (
        "Mumbai, Mumbai - BKC, Bangalore, New Delhi, Palo Alto, Singapore, Munich, New York, "
        "GIFT City – Gandhinagar"
    )


def test_an_empty_location_falls_back_to_the_offices_country():
    assert _jobs()["5800"].location == "India"


def test_the_requisition_is_the_postings_code():
    assert _jobs()["56380"].requisition == "QUA04014"


def test_a_row_without_an_id_or_title_is_skipped_not_raised_on():
    raw = {"data": {"rows": [{"title": "No id"}, {"id": 7, "title": " "}]}}
    assert get_scraper("sensehq", "acme").parse(raw, SCRAPED_AT) == []


def test_a_walk_short_of_the_stated_count_is_marked_truncated(monkeypatch):
    """A short page ends the walk; if fewer rows were read than `count` states, the rest are
    unread, not closed (ADR-0053), so the Board must not be read as complete."""
    scraper = SenseHQScraper("acme")
    monkeypatch.setattr(
        type(scraper),
        "_get",
        lambda self: json.dumps(
            {"data": {"rows": [{"id": i} for i in range(3)], "count": 40}}
        ),
    )
    scraper.fetch_raw()
    assert scraper.truncated and "3 of 40" in scraper.truncated
