"""Tests for the JazzHR sitemap miner's pure parts (scripts/discover/mine_jazzhr_sitemaps.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_jazzhr_sitemaps as miner
from board_hosts import row_for_host, sitemap_hosts

_ROBOTS = (
    "User-agent: *\nDisallow: /cb\n"
    "Sitemap: http://app.jazz.co/feeds/google/xml/0\n"
    "sitemap: http://app.jazz.co/feeds/google/xml/1\n"
)
_FEED = (
    "<urlset>"
    "<url><loc>https://easeinc.applytojob.com/apply/AbC123/Engineer?source=GS</loc></url>"
    "<url><loc>https://easeinc.applytojob.com/apply/Zzz999/Nurse?source=GS</loc></url>"
    "<url><loc>https://pitchup.applytojob.com/apply/Q1/Cook?source=GS</loc></url>"
    "</urlset>"
)


def test_the_sitemaps_are_the_ones_robots_txt_names():
    assert miner.sitemap_urls(_ROBOTS) == [
        "http://app.jazz.co/feeds/google/xml/0",
        "http://app.jazz.co/feeds/google/xml/1",
    ]


def test_a_feed_names_each_board_host_once_and_maps_to_a_ledger_row():
    hosts = sitemap_hosts(_FEED)
    assert hosts == {"easeinc.applytojob.com", "pitchup.applytojob.com"}
    assert row_for_host("easeinc.applytojob.com") == (
        "jazzhr",
        "easeinc",
        "https://easeinc.applytojob.com",
    )


def test_refresh_reads_the_feed_again_and_the_default_reads_the_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(miner, "CACHE", tmp_path)
    reads = []
    monkeypatch.setattr(
        miner,
        "fetch_cached",
        lambda url, path, refresh=False: reads.append((path.name, refresh)) or b"body",
    )
    assert miner.cached("http://app.jazz.co/feeds/google/xml/0") == "body"
    assert miner.cached("http://app.jazz.co/feeds/google/xml/0", refresh=True) == "body"
    assert reads == [("feeds_google_xml_0", False), ("feeds_google_xml_0", True)]
