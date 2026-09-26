"""Which held descriptions the next scrape re-fetches (ADR-0211).

Six Scrapers skip a Job's detail fetch once the ADR-0050 store holds its description (ADR-0048):
ADP, Apple, Cornerstone, Eightfold, Phenom and Tesla. On those, an edited posting was never fetched again,
so no edit could reach the store or the served table (ADR-0207). This rotation takes a slice of
their held Jobs off the skip-list each run, so every held Job is fetched again once its last fetch
is :data:`PERIOD_DAYS` old, on the first scrape of its Board after that.

The state is one ledger, ``data/state/description_checked.tsv.gz``: a held Job's id and the UTC
hour a fetch last reached it. Hours, not days, so the Jobs one run fetched fall due together in one
run a period later, rather than a whole day's fetches falling due in the first run of a day. A held
Job the ledger does not know (every held Job on the day this ships, or all of them if the ledger is
ever lost) is written in with an hour spread over the past period by its id's CRC, so the first
round is spread over the period and no run re-fetches every held Job at once. Once written, it stays
due until a scrape reaches it.

A due Job's fetch is recorded as a check whatever it returns: a fetch that comes back empty or fails
leaves the held text in place (ADR-0050, ADR-0089) and is not retried until the next period, which
keeps a posting whose detail always answers empty from being fetched on every scrape.

**Zwayam is left out.** Its detail path sits behind an Akamai per-IP request quota (refusals from
~500 cumulative requests, 2026-09-17), 27.5% of its Boards were last scraped more than a day ago
on 2026-09-24, and a live re-fetch of 15 held Jobs on careers.microland.com returned 6 whose only
difference was `’` read back as `?`, a worse rendering rather than an edit. ADR-0211 has the
measurements behind the period and the per-run budget it implies.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from headstart import log
from headstart.boards.board_identity import ats_of

_log = log.get(__name__, __spec__)

#: The Scrapers whose Detail pass skips held Jobs and whose re-fetch cost was measured to fit.
#: Tesla's detail pass skips held Jobs too (`tesla.py`, `skip_held=True`) and was left off until
#: 2026-09-26, so no edit to a held Tesla posting could reach the store.
ATSES = frozenset({"adp", "apple", "cornerstone", "eightfold", "phenom", "tesla"})

#: Days between two fetches of one held Job: about 1/190 of each ATS's held Jobs a run, ~240 for
#: Eightfold across its shards (ADR-0211).
PERIOD_DAYS = 7
_PERIOD = timedelta(days=PERIOD_DAYS)
_PERIOD_HOURS = PERIOD_DAYS * 24


class CorpusRow(NamedTuple):
    """One Job in this run's corpus, as the rotation needs it."""

    job_id: str
    #: Whether the scrape carried text for it, i.e. a fetch reached it and returned something.
    fetched: bool


def now() -> datetime:
    """The UTC hour the rotation is counted in. A function so a test can pin it."""
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def read_checked(path: Path) -> dict[str, datetime]:
    """The ledger, or ``{}`` when there is none yet.

    Never fatal: a malformed line is skipped and an unreadable file starts again from empty,
    whose Jobs :func:`plan` re-seeds spread over a period, rather than failing the run.
    """
    if not path.exists():
        return {}
    checked: dict[str, datetime] = {}
    malformed = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                fields = line.rstrip("\n").split("\t")
                if len(fields) != 2:
                    malformed += 1
                    continue
                try:
                    checked[fields[0]] = datetime.fromisoformat(fields[1])
                except ValueError:
                    malformed += 1
                    continue
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        _log.warning(f"{path} is unreadable ({exc}); re-seeding the rotation")
        return {}
    if malformed:
        _log.info(f"{path}: skipped {malformed} malformed line(s)")
    return checked


def write_checked(path: Path, checked: dict[str, datetime]) -> None:
    """Rewrite the ledger through a temp file, so a kill mid-write keeps the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for job_id in sorted(checked):
            fh.write(f"{job_id}\t{checked[job_id].isoformat(timespec='hours')}\n")
    tmp.replace(path)


def _seeded(job_id: str, at: datetime) -> datetime:
    """A last-fetch hour for a Job the ledger does not know, spread over the past period."""
    return at - timedelta(hours=zlib.crc32(job_id.encode("utf-8")) % _PERIOD_HOURS)


def record(
    checked: dict[str, datetime],
    rows: Iterable[CorpusRow],
    asked: set[str],
    at: datetime,
) -> None:
    """Stamp ``at`` on every Job of :data:`ATSES` a fetch reached this run.

    A row that carried text was fetched. A row without text was fetched too if it was in
    ``asked``, the due set the scrape was given: its Board was scraped, so its detail was
    requested, whatever came back.
    """
    for row in rows:
        if ats_of(row.job_id) in ATSES and (row.fetched or row.job_id in asked):
            checked[row.job_id] = at


def plan(
    held_by_ats: dict[str, set[str]],
    checked: dict[str, datetime],
    at: datetime,
    live: set[str] | None = None,
) -> set[str]:
    """The held Jobs the next scrape fetches again. Narrows ``checked`` in place to held Jobs,
    seeding the ones it does not know, so the ledger never outgrows the store.

    ``live`` is the ids the index serves. The store keeps a description after its Job is
    evicted, and no scrape re-emits an evicted Job, so only live ones are made due: on 2026-09-26
    4,652 of the 4,896 due were evicted ids, and the log line said the rotation was ~20x bigger
    than it was. Empty or None (no index metadata yet) counts every held Job, as before."""
    for job_id in [i for i in checked if i not in held_by_ats.get(ats_of(i), ())]:
        del checked[job_id]
    due: set[str] = set()
    for ats in sorted(held_by_ats):
        held = held_by_ats[ats]
        for job_id in held:
            checked.setdefault(job_id, _seeded(job_id, at))
        served = held & live if live else held
        ats_due = {i for i in served if at - checked[i] >= _PERIOD}
        due |= ats_due
        _log.info(
            f"re-fetch rotation: {ats}: {len(ats_due):,} of {len(served):,} held descriptions "
            f"of served Jobs due, left off the skip-list (every {PERIOD_DAYS} days, ADR-0211); "
            f"{len(held) - len(served):,} held for evicted Jobs not counted"
        )
    return due
