"""Tests for `scripts/discover/mine_adp.py`: career-center links read out of free text."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "discover"))

import mine_adp

CID = "7d58836c-11dd-4415-9de0-63b918b88652"
PAGE = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"


def test_an_html_link_names_its_career_center_whatever_the_key_order():
    text = (
        f'<a href="{PAGE}?lang=en_US&amp;ccId=9200471107142_2&amp;cid={CID}">Jobs</a> '
        f"and plain {PAGE}?cid={CID}&ccId=19000101_000001&jobId=968476&source=IN"
    )
    assert mine_adp.boards_in(text) == {
        f"{CID}/9200471107142_2": f"{PAGE}?cid={CID}&ccId=9200471107142_2",
        f"{CID}/19000101_000001": f"{PAGE}?cid={CID}&ccId=19000101_000001",
    }


def test_a_link_without_a_career_center_names_nobody():
    assert mine_adp.boards_in(f"{PAGE}?cid={CID}&lang=en_US") == {}
    assert mine_adp.boards_in("https://workforcenow.adp.com/theme/index.html") == {}
