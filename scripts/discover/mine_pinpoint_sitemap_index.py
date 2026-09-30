#!/usr/bin/env python3
"""Pinpoint miner: the sitemap index Pinpoint's own app host publishes for Google for Jobs.

`https://app.pinpointhq.com/robots.txt` is `Disallow: /` with one deliberate exception,
`Allow: /integrations/google/sitemap_index.xml`, and names that file as its `Sitemap:`. It is a
`<sitemapindex>` of `https://{tenant}.pinpointhq.com/sitemap.xml`, one per customer with a live
board: Pinpoint publishes its own roster. Measured 2026-09-29: 1,042 tenants, 157 unheld (15.1%),
covering 95% of the ledger's live rows. The earlier check
(`docs/pinpoint/2026-09-23_postings-api-measurement.md`, "Vendor roster: none found") read
`www.pinpointhq.com`, not this host.

One GET (8.6 KB on the wire), cached under `data/wayback-ats/.pinpoint_sitemap_index/` so a rerun
is free. The index still lists tenants whose board has since gone (10 of 10 held-but-dead tenants it
named probed dead), and tenants left behind by a rename (`{name}-old`, 11 of 12 live with 0 postings),
so liveness stays `check_liveness.py`'s job.

Run:   python -u scripts/discover/mine_pinpoint_sitemap_index.py
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats pinpoint
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from board_heldness import POOL, stage_unheld
from discovery_fetch import fetch_cached
from tenant_hosts import row_for_host

INDEX = "https://app.pinpointhq.com/integrations/google/sitemap_index.xml"
CACHE = POOL / ".pinpoint_sitemap_index" / "sitemap_index.xml"
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def tenant_sitemap_hosts(index: str) -> set[str]:
    """The distinct hosts of the index's `<loc>` tenant sitemaps."""
    return {urlsplit(loc).hostname or "" for loc in _LOC.findall(index)}


def main() -> None:
    hosts = tenant_sitemap_hosts(fetch_cached(INDEX, CACHE).decode("utf-8"))
    print(f"{len(hosts)} tenant sitemaps in the index", flush=True)
    rows = [(row[1], row[2]) for host in sorted(hosts) if (row := row_for_host(host))]
    print(dict(stage_unheld("pinpoint", rows, "pinpoint_sitemap_index")), flush=True)


if __name__ == "__main__":
    main()
