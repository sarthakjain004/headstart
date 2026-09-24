"""Which held descriptions the next scrape re-fetches (ADR-0211).

Five Scrapers skip a Job's detail fetch once the ADR-0050 store holds its description (ADR-0048):
ADP, Apple, Cornerstone, Eightfold and Phenom. On those, an edited posting was never fetched again,
so no edit could reach the store or the served table (ADR-0207). This rotation takes a slice of
their held Jobs off the skip-list each run, so every held Job is fetched again within
:data:`PERIOD_DAYS` of its last fetch, provided its Board is scraped in that time.

The state is one ledger, ``data/state/description_checked.tsv.gz``: a held Job's id and the UTC
date a fetch last reached it. A Job is **due** once that date is :data:`PERIOD_DAYS` old. A Job the
ledger does not know (every held Job on the day this ships, or all of them if the ledger is ever
lost) is due only on its own day of the cycle, ``(day + crc32(id)) % PERIOD_DAYS == 0``, so an
empty ledger spreads the first round over :data:`PERIOD_DAYS` days instead of re-fetching every
held Job at once.

A due Job stays off the skip-list until its Board is scraped. Its fetch is recorded as a check
whatever it returns: a fetch that comes back empty or fails leaves the held text in place
(ADR-0050, ADR-0089) and is not retried until the next cycle, which keeps a posting whose detail
always answers empty from being fetched on every scrape.

**Zwayam is left out.** Its detail path sits behind an Akamai per-IP request quota (refusals from
~500 cumulative requests, 2026-09-17), 27.5% of its Boards were last scraped more than a day ago
on 2026-09-24, and a live re-fetch of 15 held Jobs on careers.microland.com returned 6 whose only
difference was `’` read back as `?`, a worse rendering rather than an edit. ADR-0211 has the
measurements behind the period and the per-ATS budget it implies.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

from headstart.board_identity import ats_of
from headstart.ingest import REPO_ROOT

#: The Scrapers whose Detail pass skips held Jobs and whose re-fetch cost was measured to fit.
ATSES = frozenset({"adp", "apple", "cornerstone", "eightfold", "phenom"})

#: Days between two fetches of one held Job: ~30 extra detail requests a run for ADP, ~240 for
#: Eightfold across its shards, fewer for the rest (ADR-0211).
PERIOD_DAYS = 7

CHECKED_PATH = REPO_ROOT / "data" / "state" / "description_checked.tsv.gz"
#: The held Jobs the last published skip-list left out. Read back by the next run's
#: `update_descriptions` to tell "fetched and came back empty" from "never asked for".
DUE_PATH = REPO_ROOT / "data" / "state" / "refetch_due.txt"


def today() -> date:
    """The UTC date the cycle is counted in. A function so a test can pin it."""
    return datetime.now(UTC).date()


def read_checked(path: Path) -> dict[str, date]:
    """The ledger, or ``{}`` when there is none yet."""
    if not path.exists():
        return {}
    checked: dict[str, date] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                job_id, day = line.rstrip("\n").split("\t")
                checked[job_id] = date.fromisoformat(day)
    return checked


def write_checked(path: Path, checked: dict[str, date]) -> None:
    """Rewrite the ledger through a temp file, so a kill mid-write keeps the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for job_id in sorted(checked):
            fh.write(f"{job_id}\t{checked[job_id].isoformat()}\n")
    tmp.replace(path)


def is_due(job_id: str, checked: dict[str, date], day: date) -> bool:
    """Whether a held Job of one of :data:`ATSES` is fetched again on the next scrape."""
    last = checked.get(job_id)
    if last is not None:
        return (day - last).days >= PERIOD_DAYS
    return (day.toordinal() + zlib.crc32(job_id.encode("utf-8"))) % PERIOD_DAYS == 0


def record(
    checked: dict[str, date],
    rows: Iterable[tuple[str, bool]],
    asked: set[str],
    day: date,
) -> None:
    """Stamp ``day`` on every Job of :data:`ATSES` a fetch reached this run.

    ``rows`` is this run's corpus as ``(Job id, carried fresh text)``. A row with fresh text was
    fetched. A row without it was fetched too if it was in ``asked``, the due set the scrape was
    given: its Board was scraped, so its detail was requested, whatever came back.
    """
    for job_id, fresh in rows:
        if ats_of(job_id) in ATSES and (fresh or job_id in asked):
            checked[job_id] = day
