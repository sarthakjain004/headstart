"""Tests for the Pinpoint sitemap-index miner's pure parts
(scripts/discover/mine_pinpoint_sitemap_index.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_pinpoint_sitemap_index as miner
from tenant_hosts import row_for_host


def test_the_index_names_one_host_per_tenant_sitemap():
    index = (
        '<?xml version="1.0"?><sitemapindex>'
        "<sitemap><loc>https://100ms.pinpointhq.com/sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://acme-old.pinpointhq.com/sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://100ms.pinpointhq.com/sitemap.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    hosts = miner.tenant_sitemap_hosts(index)
    assert hosts == {"100ms.pinpointhq.com", "acme-old.pinpointhq.com"}
    assert row_for_host("100ms.pinpointhq.com") == (
        "pinpoint",
        "100ms",
        "https://100ms.pinpointhq.com",
    )
