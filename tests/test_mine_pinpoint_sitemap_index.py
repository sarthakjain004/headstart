"""Tests for the Pinpoint sitemap-index miner's pure parts
(scripts/discover/mine_pinpoint_sitemap_index.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_pinpoint_sitemap_index as miner

_INDEX = (
    '<?xml version="1.0"?><sitemapindex>'
    "<sitemap><loc>https://100ms.pinpointhq.com/sitemap.xml</loc></sitemap>"
    "<sitemap><loc>https://acme-old.pinpointhq.com/sitemap.xml</loc></sitemap>"
    "<sitemap><loc>https://restrata.pinpointhq.com/sitemap.xml</loc></sitemap>"
    "<sitemap><loc>https://100ms.pinpointhq.com/sitemap.xml</loc></sitemap>"
    "<sitemap><loc>https://www.pinpointhq.com/sitemap.xml</loc></sitemap>"
    "</sitemapindex>"
)


def test_the_index_rows_are_each_board_once_in_the_ledgers_spelling():
    assert miner.index_rows(_INDEX) == [("100ms", "https://100ms.pinpointhq.com")]


def test_a_renamed_accounts_leftover_and_a_known_dead_board_are_not_staged():
    """The research says drop `*-old` (11 of 12 probed live with 0 postings) and `restrata`."""
    assert miner.skipped("acme-old")
    assert miner.skipped("restrata")
    assert not miner.skipped("old-school-toys")  # only the suffix marks a leftover
    assert not miner.skipped("100ms")
    assert miner.RENAMED_SUFFIX == "-old"
    assert miner.KNOWN_DEAD == frozenset({"restrata"})
