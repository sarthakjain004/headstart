"""EightfoldScraper's own detail reading, beside the backing-Board tests in their own files."""

from __future__ import annotations

import json

from fake_fetcher import FakeResponse

from headstart.scrapers.base import DetailWithoutDescription
from headstart.scrapers.eightfold import EightfoldScraper, _PcsxPosition


def test_a_position_details_200_without_a_description_is_a_gap_on_the_line():
    """Kept as ``""`` for the Job, but counted — it used to land silently as a success."""
    scraper = EightfoldScraper("acme")
    item = _PcsxPosition("acme.com", {"id": 1})
    empty = FakeResponse(200, json.dumps({"data": {"id": 1}}))
    outcome = scraper.read_detail(item, empty)
    assert outcome == DetailWithoutDescription("", "no jobDescription on a 200")
    described = FakeResponse(200, json.dumps({"data": {"jobDescription": "<p>x</p>"}}))
    assert scraper.read_detail(item, described) == "<p>x</p>"
