"""Recruitee scraper tests over real captured offers (the older ones live in test_scrapers.py)."""

import json
from pathlib import Path

import pytest
from test_scrapers import _AliasResp

from headstart.scrapers.registry import get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"


def _voortman_job(offer_id):
    raw = json.loads(
        (FIXTURES / "recruitee_voortman_dutch_with_english.json").read_text("utf-8")
    )
    jobs = get_scraper("recruitee", "voortman", "Voortman").parse(raw, SCRAPED_AT)
    return next(j for j in jobs if j.id == f"recruitee:voortman:{offer_id}")


def test_an_english_translation_is_read_over_the_primary_language():
    """Real voortman offer 2738624 (2026-09-28): the top-level text is Dutch, `translations`
    carries `en`. The English-only search index would hold the Dutch text out."""
    job = _voortman_job(2738624)
    assert job.title == "Lead Software Developer XR"  # the primary title stays
    assert job.description.startswith("What will you do?")
    assert "What do we ask?" in job.description  # the English requirements ride along


def test_a_hybrid_offer_is_neither_remote_nor_on_site():
    """Real voortman offer 2671414: `hybrid` and `on_site` set, `remote` not."""
    job = _voortman_job(2671414)
    assert job.remote is None


def test_an_english_template_without_the_text_is_not_read():
    """Real voortman offer 2603217 "BBL: Logistiek": its English version is headings alone
    ("WHAT ARE YOU GOING TO DO? WE ASK WE OFFER"), 1% of the Dutch text."""
    job = _voortman_job(2603217)
    assert job.description.startswith("Wat ga je doen?")


def test_alias_key_is_the_label_a_redirect_lands_on(monkeypatch):
    """Measured 2026-09-29: `thesjefgroup.recruitee.com/api/offers/` answers 302 to
    `elockers.recruitee.com/api/offers/`. The key must be `elockers`, the ledger's own slug, or
    `dedupe_boards.py` labels every Board `migrated` and finds no duplicate."""
    monkeypatch.setattr(
        "headstart.network.http.fetch",
        lambda method, url, **kw: _AliasResp(
            "https://elockers.recruitee.com/api/offers/"
        ),
    )
    assert get_scraper("recruitee", "thesjefgroup", "x").alias_key() == "elockers"


def test_alias_key_of_a_board_nothing_redirects_is_its_own_slug(monkeypatch):
    monkeypatch.setattr(
        "headstart.network.http.fetch", lambda method, url, **kw: _AliasResp(url)
    )
    assert get_scraper("recruitee", "elockers", "x").alias_key() == "elockers"


def test_alias_key_of_a_landing_off_recruitee_is_the_whole_host(monkeypatch):
    """No live slug matches a foreign host, so `alias_ledger.resolve` reports it as moved."""
    monkeypatch.setattr(
        "headstart.network.http.fetch",
        lambda method, url, **kw: _AliasResp("https://careers.acme.com/api/offers/"),
    )
    assert get_scraper("recruitee", "acme", "x").alias_key() == "careers.acme.com"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_alias_key_of_a_rate_limited_or_failing_label_is_no_verdict(
    monkeypatch, status
):
    """Recruitee rate-limits per address (37 of 48 answered 429 at 24 concurrent requests,
    2026-09-29), and a 429 is settled rather than raised, on the label's own URL. Keyed as its
    own label, a buried duplicate read as "no redirect" and `--apply` dropped its row unreported.
    None makes `alias_ledger.resolve` report it as `unreachable` instead."""
    monkeypatch.setattr(
        "headstart.network.http.fetch",
        lambda method, url, **kw: _AliasResp(url, status_code=status),
    )
    assert get_scraper("recruitee", "thesjefgroup", "x").alias_key() is None
