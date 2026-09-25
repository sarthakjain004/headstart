"""DarwinboxScraper's envelope check, beside the parse tests in test_scrapers.py."""

from __future__ import annotations

import logging

from fake_fetcher import FakeFetcher, FakeResponse

from headstart.scrapers.darwinbox import DarwinboxScraper

_HOST = "https://acme.darwinbox.in"


def _scraper(payload: str) -> DarwinboxScraper:
    fetcher = FakeFetcher(lambda method, url, kwargs: FakeResponse(200, payload))
    return DarwinboxScraper("acme", fetcher=fetcher)


def test_a_failed_envelope_is_named_rather_than_read_as_empty(caplog):
    caplog.set_level(logging.INFO, logger="headstart.scrapers.darwinbox")
    assert _scraper('{"status": "failure"}')._alljobs(_HOST, 1) == []
    assert "expected an alljobs envelope with status \"success\", got 'failure'" in (
        caplog.text
    )


def test_an_empty_success_envelope_says_nothing(caplog):
    """An HR-only tenant answers exactly this (games24x7, 2026-09-25)."""
    caplog.set_level(logging.INFO, logger="headstart.scrapers.darwinbox")
    payload = '{"status": "success", "data": [], "job_counts": 0}'
    assert _scraper(payload)._alljobs(_HOST, 1) == []
    assert caplog.text == ""
