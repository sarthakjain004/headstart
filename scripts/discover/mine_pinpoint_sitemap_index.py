#!/usr/bin/env python3
"""Pinpoint miner: the sitemap index Pinpoint's own app host publishes for Google for Jobs.

`https://app.pinpointhq.com/robots.txt` is `Disallow: /` with one deliberate exception,
`Allow: /integrations/google/sitemap_index.xml`, and names that file as its `Sitemap:`. It is a
`<sitemapindex>` of `https://{tenant}.pinpointhq.com/sitemap.xml`, one per customer with a live
board: Pinpoint publishes its own roster. Measured 2026-09-29: 1,042 Boards, 157 unheld (15.1%),
covering 95% of the ledger's live rows. The earlier check
(`docs/pinpoint/2026-09-23_postings-api-measurement.md`, "Vendor roster: none found") read
`www.pinpointhq.com`, not this host.

One GET (8.6 KB on the wire), cached under `data/wayback-ats/.pinpoint_sitemap_index/` so a rerun
is free (`--refresh` reads it again). The index lists Boards that are not worth landing, and the
research (2026-09-29, 2026-09-30) says to drop them before staging rather than let a probe write
them `live`:

- `{name}-old` (`RENAMED_SUFFIX`): what a renamed account leaves behind. Of the 12 the index
  listed, 11 probed live with 0 postings (the probe lands a row that never serves a job) and 1
  probed dead (measured 2026-09-30).
- `restrata` (`KNOWN_DEAD`): probed dead.

It also names Boards whose account has since gone (10 of 10 held-but-dead Boards it named probed
dead); those are `check_liveness.py`'s to settle, not this miner's.

Run:   python -u scripts/discover/mine_pinpoint_sitemap_index.py [--refresh]
Then:  python scripts/validate/check_liveness.py --dir data/wayback-ats pinpoint
"""

from __future__ import annotations

import argparse

from board_hosts import row_for_host, sitemap_hosts
from candidate_pool import POOL, stage_unheld
from discovery_fetch import fetch_cached

INDEX = "https://app.pinpointhq.com/integrations/google/sitemap_index.xml"
CACHE = POOL / ".pinpoint_sitemap_index" / "sitemap_index.xml"
RENAMED_SUFFIX = "-old"
KNOWN_DEAD = frozenset({"restrata"})


def skipped(tenant: str) -> bool:
    """A Board the research says not to stage: a renamed account's leftover or a known-dead one."""
    return tenant.endswith(RENAMED_SUFFIX) or tenant in KNOWN_DEAD


def index_rows(index_xml: str) -> list[tuple[str, str]]:
    """`(tenant, url)` for each Board the sitemap index lists, minus the skipped ones."""
    rows = (row_for_host(host) for host in sorted(sitemap_hosts(index_xml)))
    return [(row[1], row[2]) for row in rows if row and not skipped(row[1])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--refresh", action="store_true", help="read the sitemap index again"
    )
    args = parser.parse_args()
    xml = fetch_cached(INDEX, CACHE, refresh=args.refresh).decode("utf-8")
    rows = index_rows(xml)
    print(f"{len(rows)} Boards in the index after the skips", flush=True)
    print(dict(stage_unheld("pinpoint", rows, "pinpoint_sitemap_index")), flush=True)


if __name__ == "__main__":
    main()
