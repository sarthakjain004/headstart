#!/usr/bin/env python3
"""Find the Jibe clients whose every posting sits on an iCIMS Board we scrape (ADR-0240).

`JibeScraper.parse` drops a posting whose `apply_url` tenant is a Scrapable iCIMS Board, so a
client whose every posting is on one scrapes to 0 Jobs, paced at 5 s a page, every run: commonspirit
spent 515 s a run for nothing. CLAUDE.md's Jibe landing rule parks such a client, but only on its
whole listing: a client with one posting elsewhere keeps serving that one here.

Walks each Scrapable Jibe client's whole listing with the scraper itself (robots.txt honoured,
paced per host) and prints one CSV row per client as it completes:

    client,postings,on_scraped_icims,elsewhere,verdict

`postings` is what `JibeScraper.fetch` keeps plus what it drops as iCIMS-covered, so the verdict is
`parse`'s own rule: `park` when it drops postings and keeps none, `keep` otherwise, `error` when the
walk raised. Parked clients are already out of the Scrapable list, so re-running this after
landing reads only the ones still scraped.

    PYTHONPATH=src python -u scripts/validate/jibe_icims_covered_clients.py [--workers 32] \
        > experiment/jibe-icims-covered/2026-09-26_jibe-icims-covered-clients.csv
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from headstart.boards import liveness_ledger, scrapable_boards
from headstart.scrapers import jibe

LEDGER = liveness_ledger.dir_for(Path(__file__).resolve().parents[2])


def classify(client: str) -> str:
    scraper = jibe.JibeScraper(client)
    kept = len(scraper.fetch())
    dropped = scraper.telemetry.get("icims_covered", 0)
    verdict = "park" if dropped and not kept else "keep"
    return f"{client},{kept + dropped},{dropped},{kept},{verdict}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()
    clients = sorted(
        b.slug for b in scrapable_boards.load(LEDGER, min_jobs=0) if b.ats == "jibe"
    )
    jibe._scraped_icims_tenants()  # load once, before the threads race for it
    print("client,postings,on_scraped_icims,elsewhere,verdict", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(classify, c): c for c in clients}
        for future in as_completed(futures):
            try:
                print(future.result(), flush=True)
            except Exception as exc:  # noqa: BLE001 - one client's failure is reported, not fatal
                print(f"{futures[future]},,,,error", flush=True)
                print(f"{futures[future]}: {exc!r}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
