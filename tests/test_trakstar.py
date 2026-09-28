"""Trakstar scraper tests over real captures (the older ones live in test_scrapers.py)."""

import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def test_jsapi_jobs_take_their_posted_date_from_the_job_feed(monkeypatch):
    """Real exotel jsapi objects and `/jobfeeds/exotel` items (2026-09-28): jsapi states no date,
    the feed's `pubDate` does, keyed by the same code (34 of 34 on exotel, 364 of 365 on
    demoaccount)."""
    scraper = get_scraper("trakstar", "exotel", "Exotel")
    objects = json.loads((FIXTURES / "trakstar_exotel_jsapi_objects.json").read_text())
    feed = (FIXTURES / "trakstar_exotel_jobfeed.xml").read_text()
    monkeypatch.setattr(scraper, "_careers_page", lambda: None)
    monkeypatch.setattr(scraper, "_api_listing", lambda: objects)
    monkeypatch.setattr(scraper, "_fetch_feed", lambda: feed)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert [(j.id, j.posted_at) for j in jobs] == [
        ("trakstar:exotel:fk0z8rm", "2026-09-28"),
        ("trakstar:exotel:fk0z8vb", "2026-09-22"),
    ]


def test_an_unreachable_job_feed_leaves_the_date_unknown(monkeypatch):
    scraper = get_scraper("trakstar", "exotel", "Exotel")
    objects = json.loads((FIXTURES / "trakstar_exotel_jsapi_objects.json").read_text())
    monkeypatch.setattr(scraper, "_careers_page", lambda: None)
    monkeypatch.setattr(scraper, "_api_listing", lambda: objects)
    monkeypatch.setattr(scraper, "_fetch_feed", lambda: None)
    jobs = scraper.parse(scraper.fetch_raw(), SCRAPED_AT)
    assert [j.posted_at for j in jobs] == [None, None]
