#!/usr/bin/env python3
"""Write ADP's company-name cache: each Scrapable client's ``ClientName`` (ADR-0241).

`ADPScraper.resolve_company` reads the name from ``client-features``, one request per Board per
run through the process-wide pacer every ADP request shares. The name is the payroll client's
record and does not move from run to run, so the scraper reads it from
``data/validate/company_names/adp.csv`` (``cid,name,checked_at``) first and asks the host only for
a client not on file. An empty ``name`` is kept: ADP states none for that client, and asking again
every run would not change it.

Also the landing check for ADP's own test clients. A ``ClientName`` matching
``TEST_CLIENT_NAME`` (``WFNQAFR60W``, ``WFNPJL969``, ``FARM 61 BVT4``, ``NAS TEST CODE- Prod
Enablement``) is printed to stderr as ``test-client?``: read that client's postings ("BVT
Analyst_…", "RECT AUTO REQS_…", "BVT Location, Anchorage, AK") before adding its Boards to
``EXCLUDED_BOARDS``, as that list's rule asks — the name alone is not the evidence.

Appends one row per client as it lands and skips clients already on file, so a stopped run
resumes where it stopped; the file is sorted when the run ends. Re-run it after landing ADP rows.

    PYTHONPATH=src python -u scripts/validate/adp_company_names.py [--spacing 0.4]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from headstart import log
from headstart.boards import scrapable_boards
from headstart.scrapers import adp
from headstart.scrapers.pacer import Pacer

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data" / "validate" / "liveness"
CACHE = ROOT / adp.RESOLVED_NAMES
FIELDS = ["cid", "name", "checked_at"]
#: ADP's own QA and build-verification clients, as their ``ClientName`` states them on the 36
#: measured 2026-09-26: ``WFNQA…``/``WFNPJL…``/``WFNBVT…``/``WFN4PRODA1``, ``FARM 61 BVT4``,
#: ``NAS TEST CODE- Prod Enablement``, ``NAS WFN Prod Enablement -testnas030``. A lead to read,
#: never a verdict: 6 of the 36 state no such name (3 none at all) and were found by content.
TEST_CLIENT_NAME = re.compile(
    r"^wfn|\bbvt\d*\b|test ?code|prod enablement", re.IGNORECASE
)


def client_name(cid: str, cc_id: str) -> str:
    return adp.ADPScraper(f"{cid}/{cc_id}").client_name()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--spacing", type=float, default=adp._SPACING_S)
    args = parser.parse_args()
    log.setup()
    adp.ADPScraper.pacer = Pacer(args.spacing)
    centers: dict[str, str] = {}
    for board in scrapable_boards.load(LEDGER, min_jobs=0):
        if board.ats == "adp":
            cid, cc_id = board.slug.split("/", 1)
            centers.setdefault(cid, cc_id)
    held: dict[str, dict] = {}
    if CACHE.exists():
        with CACHE.open(encoding="utf-8", newline="") as fh:
            held = {row["cid"]: row for row in csv.DictReader(fh)}
    todo = sorted(set(centers) - set(held))
    print(
        f"{len(centers)} clients, {len(held)} on file, {len(todo)} to read", flush=True
    )
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    new = not CACHE.exists()
    today = datetime.now(UTC).date().isoformat()
    with CACHE.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        if new:
            writer.writeheader()
        # Threads only overlap the requests' latency; the pacer still spaces every start. Sixteen,
        # because some clients never answer `client-features` and hold a thread for the 30 s
        # timeout (9 of the first 130 read, 2026-09-26).
        with ThreadPoolExecutor(16) as pool:
            futures = {pool.submit(client_name, c, centers[c]): c for c in todo}
            for done, future in enumerate(as_completed(futures), 1):
                cid = futures[future]
                try:
                    name = future.result()
                except Exception as exc:  # noqa: BLE001 - left off file, read on the next run
                    print(f"{cid}: {exc!r}", file=sys.stderr, flush=True)
                    continue
                row = {"cid": cid, "name": name, "checked_at": today}
                writer.writerow(row)
                fh.flush()
                held[cid] = row
                if TEST_CLIENT_NAME.search(name):
                    print(f"test-client? {cid} {name!r}", file=sys.stderr, flush=True)
                if done % 500 == 0:
                    print(f"{done}/{len(todo)}", flush=True)
    with CACHE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(held[cid] for cid in sorted(held))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
