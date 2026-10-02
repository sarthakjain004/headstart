#!/usr/bin/env python3
"""Find gr8people hosts sharing a public client and an identical posting set.

Re-run after refreshing the gr8people ledger. Organisation alone is not Board
identity: ActOne's independent brands share an org but not a client. A matching
client is a lead, not proof; each pair's complete posting ids are read before
writing shared-reqs aliases. An incomplete read aborts instead of replacing
previously recorded aliases. --apply writes the alias ledger; default is dry-run.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from headstart.boards import alias_ledger, liveness_ledger
from headstart.scrapers.gr8people import Gr8PeopleScraper

ROOT = Path(__file__).resolve().parents[2]
# The descriptive public brand hosts, over their legacy vendor spellings.
PREFER = {"randstad-sourceright.gr8people.com", "randstadnorthamerica.workgr8.com"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ledger = liveness_ledger.load(liveness_ledger.dir_for(ROOT) / "gr8people.csv")
    live = sorted(
        {row.tenant for row in ledger.values() if row.status == liveness_ledger.LIVE}
    )
    clients = defaultdict(list)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(Gr8PeopleScraper(host).public_client): host for host in live
        }
        for future in as_completed(futures):
            host = futures[future]
            client = future.result()
            if not client:
                raise SystemExit(
                    f"{host}: public client unresolved; refusing a partial alias scan"
                )
            clients[client].append(host)
            print(f"{host}: {client}", flush=True)
    aliases = []
    for client, hosts in clients.items():
        if len(hosts) < 2:
            continue
        canonical = min(hosts, key=lambda host: (host not in PREFER, host))
        id_sets = {}
        for host in hosts:
            scraper = Gr8PeopleScraper(host)
            jobs = scraper.fetch()
            if scraper.truncated or len(jobs) != scraper.listing_total:
                raise SystemExit(
                    f"{host}: {len(jobs)}/{scraper.listing_total}, {scraper.truncated}; refusing a partial alias scan"
                )
            id_sets[host] = {job.id.rsplit(":", 1)[1] for job in jobs}
        for host in hosts:
            if host != canonical and id_sets[host] == id_sets[canonical]:
                aliases.append(
                    alias_ledger.Alias(
                        "gr8people",
                        host,
                        canonical,
                        "shared-reqs",
                        client,
                        datetime.now(UTC).date().isoformat(),
                    )
                )
                print(
                    f"bury {host} -> {canonical}: {len(id_sets[host])} identical postings",
                    flush=True,
                )
            elif host != canonical:
                print(
                    f"diverged {host} / {canonical}: {len(id_sets[host] - id_sets[canonical])} / {len(id_sets[canonical] - id_sets[host])} ids unique to each; removing any previous alias",
                    flush=True,
                )
    print(f"{len(aliases)} aliases", flush=True)
    if args.apply:
        alias_ledger.write(
            alias_ledger.path_for(liveness_ledger.dir_for(ROOT), "gr8people"), aliases
        )


if __name__ == "__main__":
    main()
