#!/usr/bin/env python3
"""Write Instahyre's one global public-marketplace source into its candidate pool.

Instahyre does not publish company Boards: its anonymous global feed has every current listing
and each row carries an Instahyre employer profile. The global listing is therefore the one
canonical source, not a tenant roster. Run this before the normal liveness checker.
"""

from __future__ import annotations

import csv
from pathlib import Path

from headstart.network import http
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.instahyre import InstahyreScraper

ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "data" / "ats-tenants-merged" / "instahyre.csv"


def write_pool(pool: Path) -> None:
    pool.parent.mkdir(parents=True, exist_ok=True)
    with pool.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ats", "tenant", "url", "source"])
        writer.writeheader()
        writer.writerow(
            {
                "ats": "instahyre",
                "tenant": "global",
                "url": InstahyreScraper("global").url(),
                "source": "public-listing",
            }
        )


def main() -> None:
    url = InstahyreScraper("global").url()
    response = http.fetch("GET", url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    total = (response.json().get("meta") or {}).get("total_count")
    if not isinstance(total, int):
        raise TypeError("Instahyre listing has no numeric total_count")
    write_pool(POOL)
    print(f"Instahyre global marketplace: {total:,} current jobs", flush=True)


if __name__ == "__main__":
    main()
