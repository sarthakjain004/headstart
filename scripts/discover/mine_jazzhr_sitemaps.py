#!/usr/bin/env python3
"""JazzHR miner: the Google-for-Jobs sitemaps JazzHR's own app host publishes.

`https://app.jazz.co/robots.txt` allows every path but `/cb` and names five sitemaps,
`Sitemap: http://app.jazz.co/feeds/google/xml/{0..4}` (a sixth is an empty `<urlset>`). Each is a
`<urlset>` of `https://{tenant}.applytojob.com/apply/{id}/{title}` job links, about 4.1 MB, listing
every JazzHR customer with an open posting: JazzHR feeds them to Google for Jobs, so the vendor
publishes the whole roster itself. Measured 2026-09-29: 114,182 job URLs over 7,540 Boards, 2,715
of them no ledger row holds (36.0%). `docs/jazzhr/2026-09-07_surface-investigation.md` section 10
found the same feeds and nobody mined them.

This reads robots.txt for the sitemap URLs, fetches each once (cached under
`data/wayback-ats/.jazzhr_sitemaps/`, so a rerun costs nothing; `--refresh` reads them again, and
the feeds are a daily snapshot), maps every `<loc>` host to a ledger row through `board_hosts` and
stages what no ledger row holds through `candidate_pool`. Liveness is `check_liveness.py`'s job: a
feed still lists customers whose career page now reads "JazzHR - Inactive Career Page" (about 10%
of the unheld side), which the probe settles as dead.

Run:   python -u scripts/discover/mine_jazzhr_sitemaps.py [--refresh]
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats jazzhr
"""

from __future__ import annotations

import argparse
import re
from urllib.parse import urlsplit

from board_hosts import row_for_host, sitemap_hosts
from candidate_pool import POOL, stage_by_ats
from discovery_fetch import fetch_cached

ROBOTS = "https://app.jazz.co/robots.txt"
CACHE = POOL / ".jazzhr_sitemaps"
_SITEMAP = re.compile(r"^Sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def sitemap_urls(robots: str) -> list[str]:
    """The `Sitemap:` lines of a robots.txt."""
    return _SITEMAP.findall(robots)


def cached(url: str, refresh: bool = False) -> str:
    name = urlsplit(url).path.strip("/").replace("/", "_") or "root"
    return fetch_cached(url, CACHE / name, refresh=refresh).decode("utf-8", "replace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--refresh", action="store_true", help="read robots.txt and the feeds again"
    )
    args = parser.parse_args()
    sitemaps = sitemap_urls(cached(ROBOTS, args.refresh))
    print(f"robots.txt names {len(sitemaps)} sitemaps", flush=True)
    hosts: set[str] = set()
    for url in sitemaps:
        hosts |= sitemap_hosts(cached(url, args.refresh))
        print(f"  {url}: {len(hosts)} distinct hosts so far", flush=True)
    stage_by_ats(
        (row for host in sorted(hosts) if (row := row_for_host(host))),
        "jazzhr_sitemaps",
    )


if __name__ == "__main__":
    main()
