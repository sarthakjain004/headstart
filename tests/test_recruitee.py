"""Recruitee scraper tests over real captured offers (the older ones live in test_scrapers.py)."""

import json
from pathlib import Path

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _voortman_jobs():
    raw = json.loads(
        (FIXTURES / "recruitee_voortman_dutch_with_english.json").read_text("utf-8")
    )
    return get_scraper("recruitee", "voortman", "Voortman").parse(raw, SCRAPED_AT)


def test_an_english_translation_is_read_over_the_primary_language():
    """Real voortman offer 2738624 (2026-09-28): the top-level text is Dutch, `translations`
    carries `en`. The English-only search index would hold the Dutch text out."""
    job = _voortman_jobs()[0]
    assert job.title == "Lead Software Developer XR"  # the primary title stays
    assert job.description.startswith("What will you do?")
    assert "What do we ask?" in job.description  # the English requirements ride along


def test_a_hybrid_offer_is_neither_remote_nor_on_site():
    """Real voortman offer 2671414: `hybrid` and `on_site` set, `remote` not."""
    job = _voortman_jobs()[1]
    assert job.remote is None


def test_an_english_template_without_the_text_is_not_read():
    """Real voortman offer 2603217 "BBL: Logistiek": its English version is headings alone
    ("WHAT ARE YOU GOING TO DO? WE ASK WE OFFER"), 1% of the Dutch text."""
    job = _voortman_jobs()[2]
    assert job.description.startswith("Wat ga je doen?")
