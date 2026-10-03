"""EightfoldScraper's own detail reading, beside the backing-Board tests in their own files."""

from __future__ import annotations

import json

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http
from headstart.scrapers.base import DetailWithoutDescription
from headstart.scrapers.eightfold import EightfoldScraper, _PcsxPosition


@pytest.mark.parametrize("group", ["volkscience.com", "eightfold.ai"])
def test_eightfold_can_read_its_own_official_board_without_accepting_vendor_fallthrough(
    group,
):
    page = f'<script>var _EF_GROUP_ID = "{group}";</script>'
    fetcher = FakeFetcher(lambda method, url, kwargs: FakeResponse(200, page))
    assert EightfoldScraper("app.eightfold.ai", fetcher=fetcher)._group_id() == group
    for host in ["accenture.eightfold.ai", "app.eightfold.ai.example.com"]:
        with pytest.raises(http.RequestsError, match="Eightfold's own group"):
            EightfoldScraper(host, fetcher=fetcher)._group_id()


def test_a_position_details_200_without_a_description_is_a_gap_on_the_line():
    """Kept as ``""`` for the Job, but counted — it used to land silently as a success."""
    scraper = EightfoldScraper("acme")
    item = _PcsxPosition("acme.com", {"id": 1})
    empty = FakeResponse(200, json.dumps({"data": {"id": 1}}))
    outcome = scraper.read_detail(item, empty)
    assert outcome == DetailWithoutDescription("", "no jobDescription on a 200")
    described = FakeResponse(200, json.dumps({"data": {"jobDescription": "<p>x</p>"}}))
    assert scraper.read_detail(item, described) == "<p>x</p>"
