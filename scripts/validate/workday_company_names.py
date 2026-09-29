#!/usr/bin/env python3
"""Write Workday's company-name cache: one resolved name per Board (ADR-0216).

`WorkdayScraper.resolve_company` reads `data/validate/company_names/workday.csv` before it spends a
request, so a Board on file keeps the same name from run to run. This script is what fills it. Per
Board it reads the first listing page, up to `_DETAILS` posting details (their
`hiringOrganization`) and the board page, then runs `workday_company_name.board_name` — the same cascade
the scraper runs live. Only named Boards get a row, and a Board with a curated name
(`config/company_names.csv`) is skipped, since that name overrides the cache. One the cascade
cannot name keeps resolving live until it is curated or a later run names it.

After a change to the Workday ledger (a landing or a re-probe), run it with `--new-since REF`, where
REF is the commit the change started from (`origin/main` on its branch): it reads only the Boards
that are Hiring now, were not Hiring at REF, and are not yet on file. That covers a Board landed
since REF and one that started hiring since: a Board landed with no postings is not read then,
and a later re-probe that finds it hiring is the change that reads it (24 Workday Boards landed
at 0 postings between the cache's first write and 2026-09-29). Without it the script reads every
Hiring Board not yet on file, and that is not a landing's size: 3,923 on 2026-09-29, nearly all
held before the cache existed, because the ADR-0216 sweep read only the 4,175 Boards then serving
a slug. `--all`
re-reads every Hiring Board, for a periodic refresh: a Board it names gets the new row, and one it
no longer names keeps the old one. Rows are appended as each Board finishes, so an interrupted run
keeps its progress; the file is rewritten sorted at the end.

    python scripts/validate/workday_company_names.py [--new-since REF | --all]
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from headstart.boards import company_name, scrapable_boards
from headstart.scrapers import workday_company_name
from headstart.scrapers.base import USER_AGENT
from headstart.scrapers.workday import WorkdayScraper

OUT = ROOT / workday_company_name.RESOLVED_NAMES
LEDGER = "data/validate/liveness/workday.csv"
ALIASES = "data/validate/aliases/workday.csv"
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


def _hiring_at(ref: str) -> set[str]:
    """The Workday Hiring Boards (lowercased identities) of the ledger and alias file at git ``ref``.

    Both are written into a copy of the repo's layout, because `scrapable_boards.load` finds the
    alias file beside the liveness directory it is given (`alias_ledger.path_for`)."""
    with tempfile.TemporaryDirectory() as tmp:
        for path, required in ((LEDGER, True), (ALIASES, False)):
            shown = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{ref}:{path}"],
                check=required,
                capture_output=True,
                text=True,
            )
            if shown.returncode == 0:
                (Path(tmp) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / path).write_text(shown.stdout, encoding="utf-8")
        liveness = Path(tmp) / Path(LEDGER).parent
        return {
            b.lowercase_identity for b in scrapable_boards.load(liveness, min_jobs=1)
        }


def _read(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["board_key"].lower(): row for row in csv.DictReader(handle)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--all", action="store_true", help="re-read Boards already on file"
    )
    scope.add_argument(
        "--new-since",
        metavar="REF",
        help="read only Boards that were not Hiring at git REF (a ledger change's base)",
    )
    args = parser.parse_args()

    held = _read(OUT)
    hiring_before = _hiring_at(args.new_since) if args.new_since else set()
    boards = [
        b
        for b in scrapable_boards.load(ROOT / "data/validate/liveness", min_jobs=1)
        if b.ats == "workday"
        and (args.all or b.lowercase_identity not in held)
        and b.lowercase_identity not in hiring_before
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
