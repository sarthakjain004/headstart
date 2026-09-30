"""Tests for the JazzHR sitemap miner's pure parts (scripts/discover/mine_jazzhr_sitemaps.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_jazzhr_sitemaps as miner
from tenant_hosts import row_for_host

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


def test_a_feed_names_each_tenant_host_once_and_maps_to_a_ledger_row():
    hosts = miner.job_link_hosts(_FEED)
    assert hosts == {"easeinc.applytojob.com", "pitchup.applytojob.com"}
    assert row_for_host("easeinc.applytojob.com") == (
        "jazzhr",
        "easeinc",
        "https://easeinc.applytojob.com",
    )
