#!/usr/bin/env python3
"""Union gr8people/workgr8 archive hosts and supplied public career sites.

Wayback and Common Crawl expose vendor-hosted Boards; vanity domains have no
enumerable namespace and enter through careers-page fingerprints or --seed.
This builds a candidate pool, not a verdict: archived infrastructure and
employee-only sites are deliberately left to the liveness probe.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from headstart.scrapers.gr8people import Gr8PeopleScraper

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", default=[])
    args = parser.parse_args()
    target = ROOT / "data/ats-tenants-merged/gr8people.csv"
    rows: dict[str, dict] = {}
    for path, source in (
        (target, "existing"),
        (ROOT / "data/wayback-ats/gr8people.csv", "wayback"),
        (ROOT / "data/discover/cc_ats_tenants.csv", "commoncrawl"),
    ):
        added = 0
        if not path.exists():
            print(f"{source}: input unavailable", flush=True)
            continue
        with path.open() as fh:
            for row in csv.DictReader(fh):
                if row.get("ats") != "gr8people":
                    continue
                host = Gr8PeopleScraper.slug_from(row["tenant"], row.get("url", ""))
                if host not in rows:
                    added += 1
                    rows[host] = {
                        "ats": "gr8people",
                        "tenant": host,
                        "url": f"https://{host}/jobs",
                        "source": row.get("source") or source,
                    }
        print(f"{source}: {added} new hosts", flush=True)
    for url in args.seed:
        host = Gr8PeopleScraper.slug_from(url, url)
        rows.setdefault(
            host,
            {
                "ats": "gr8people",
                "tenant": host,
                "url": f"https://{host}/jobs",
                "source": "public-careers",
            },
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as fh:
        writer = csv.DictWriter(fh, fieldnames=("ats", "tenant", "url", "source"))
        writer.writeheader()
        for host in sorted(rows):
            writer.writerow(rows[host])
    print(f"pool: {len(rows)} hosts -> {target}", flush=True)


if __name__ == "__main__":
    main()
