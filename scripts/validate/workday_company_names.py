#!/usr/bin/env python3
"""Write Workday's company-name cache: one resolved name per Board (ADR-0210).

`WorkdayScraper.resolve_company` reads `data/validate/company_names/workday.csv` before it spends a
request, so a Board on file keeps the same name from run to run. This script is what fills it. Per
Board it reads the first listing page, up to `_DETAILS` posting details (their
`hiringOrganization`) and the board page, then runs `workday_company_name.board_name` — the same cascade
the scraper runs live. Only named Boards get a row, and a Board with a curated name
(`config/company_names.csv`) is skipped, since that name overrides the cache. One the cascade
cannot name keeps resolving live until it is curated or a later run names it.

By default it reads the Workday Hiring Boards not yet on file, so re-run it after a Workday landing.
`--all` re-reads every Hiring Board, for a periodic refresh: a Board it names gets the new row, and
one it no longer names keeps the old one. Rows are appended
as each Board finishes, so an interrupted run keeps its progress; the file is rewritten sorted at
the end.

    python scripts/validate/workday_company_names.py [--all]
"""

from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart import company_name, scrapable_boards
from headstart.scrapers import workday_company_name
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.workday import WorkdayScraper

OUT = ROOT / workday_company_name.RESOLVED_NAMES
FIELDS = ("board_key", "name", "source", "checked_at")
#: Details read per Board: enough postings to vote over, measured at 8-12 on 2026-09-24.
_DETAILS = 8
#: Boards read at once. Each reads its own pages one after another; 20 held without a 429 over
#: the 4,175-Board sweep of 2026-09-24.
_WORKERS = 20


def resolve(slug: str) -> tuple[str, str | None, str]:
    """``(board_key, name, source)`` for one Board, read live."""
    scraper = WorkdayScraper(slug)
    scraper._resolve_instance()  # a migrated tenant's own wdN host no longer answers
    tenant, _instance, site = scraper._parts()
    listing = scraper._post({}, 0, raise_gone=True) or {}
    paths = [
        p["externalPath"]
        for p in listing.get("jobPostings") or []
        if p.get("externalPath")
    ]
    details = [scraper._job_detail(path) for path in paths[:_DETAILS]]
    response = scraper._fetch(
        "GET", scraper.job_url(""), headers={"User-Agent": USER_AGENT}, timeout=30
    )
    page = response.text if response.status_code == 200 else None
    name, source = workday_company_name.board_name(
        [d.get("hiringOrganization") for d in details if d], page, f"{tenant}/{site}"
    )
    return scraper.board_key(), name, source


def _read(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["board_key"].lower(): row for row in csv.DictReader(handle)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--all", action="store_true", help="re-read Boards already on file"
    )
    args = parser.parse_args()

    held = _read(OUT)
    boards = [
        b
        for b in scrapable_boards.load(ROOT / "data/validate/liveness", min_jobs=1)
        if b.ats == "workday"
        and (args.all or b.lowercase_identity not in held)
        # A curated name overrides the cache, so a Board with one needs no cached answer.
        and not company_name.curated(b.identity)
    ]
    print(f"{len(boards)} Workday Boards to read ({len(held)} on file)", flush=True)
    today = datetime.now(UTC).date().isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    new_file = not OUT.exists()
    named = 0
    with (
        OUT.open("a", newline="", encoding="utf-8") as handle,
        ThreadPoolExecutor(_WORKERS) as pool,
    ):
        writer = csv.DictWriter(handle, FIELDS, lineterminator="\n")
        if new_file:
            writer.writeheader()
        futures = {pool.submit(resolve, b.slug): b for b in boards}
        for done, future in enumerate(as_completed(futures), 1):
            board = futures[future]
            try:
                key, name, source = future.result()
            except Exception as exc:  # noqa: BLE001 - one Board's failure is a line, not an abort
                print(f"[{done}/{len(boards)}] {board.identity}: {exc!r}", flush=True)
                continue
            print(f"[{done}/{len(boards)}] {key}: {name or '-'} ({source})", flush=True)
            if name:
                named += 1
                writer.writerow(
                    {
                        "board_key": key,
                        "name": name,
                        "source": source,
                        "checked_at": today,
                    }
                )
                handle.flush()
    # The appends above may repeat a Board `--all` re-read; the last row for a key is the newest.
    rows = sorted(_read(OUT).values(), key=lambda row: row["board_key"].lower())
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"named {named} of {len(boards)}; {len(rows)} Boards on file -> {OUT}",
        flush=True,
    )


if __name__ == "__main__":
    main()
