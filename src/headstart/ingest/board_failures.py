#!/usr/bin/env python3
"""The consecutive-gone ledger: Boards that answered "this board no longer exists", run after run.

A dead Board is invisible to every existing mechanism. Nothing in ``src/headstart/`` writes the
liveness ledger — only the offline probes under ``scripts/validate/`` do — so demotion needs a
human to run one. And the priority ledger cannot demote it either: ``update_ledgers priority``
derives its snapshot from the *jobs produced*, so a Board that returns nothing is simply absent and
carries its old row unchanged (the ADR-0022 partial-harvest rule, which cannot tell "not scraped"
from "scraped and gone"). The result is a Board that 404s keeps the score it earned while healthy,
stays pinned in the priority head, and is re-scraped every two hours forever.

This ledger closes that loop, and the whole design is about **not** trusting a single run:

* Only a **gone** signal counts (HTTP 404/410 — see :func:`is_gone`). A 429, a 5xx, a timeout or a
  TLS failure says the *fetch* failed, not that the Board is gone; counting those would quarantine
  a healthy Board the moment its ATS rate-limited us. Workday alone raised 2,840 fatal 429s over
  19 runs, so this distinction is the difference between a useful ledger and a self-inflicted
  outage.
* A Board must come back gone :data:`QUARANTINE_AT` times **in a row**. Any run that produces jobs
  for it resets the count to zero.
* A run that did not scrape the Board leaves its row untouched — same partial-harvest rule the
  other two ledgers follow. Boards outside the slice must not age toward quarantine.

Quarantine only removes a Board from the *scrape slice* (``scrape_plan``). It deliberately does not
touch ``data/validate/liveness/``, which stays the probe-owned truth, and it deliberately does not
reach ``live_keep_set`` — that feeds ``index prune``, so filtering there would evict the Board's
rows from the served table as a side effect of a scraping decision.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import NamedTuple

# Consecutive gone-runs before a Board leaves the scrape slice. Five rather than two because a
# Board only ages when it is actually scraped, and the exploration tail re-selects a given Board
# roughly one run in four — so five strikes is weeks of agreement, not an afternoon's blip.
QUARANTINE_AT = 5

# "Gone" as the origin reports it. Matched against the recorded reason, which the shard reports
# carry as "{ExcType}: {message}" (e.g. "HTTPError: HTTP Error 404: ").
_GONE = re.compile(r"HTTP Error (404|410)\b")

_FIELDS = ("board", "strikes", "last_reason", "last_seen_gone")


class Failure(NamedTuple):
    """One Board's consecutive-gone streak."""

    strikes: int
    last_reason: str
    last_seen_gone: str

    @property
    def quarantined(self) -> bool:
        return self.strikes >= QUARANTINE_AT


def is_gone(reason: str) -> bool:
    """Whether a recorded scrape failure means *the Board no longer exists*, rather than *the
    fetch failed*. Only this class of failure may age a Board toward quarantine."""
    return bool(_GONE.search(reason or ""))


def board_key_of(report_key: str) -> str | None:
    """The canonical ``board_key()`` for a shard report's ``{ats}:{slug}`` key, or None if it
    will not resolve.

    The inputs to this ledger arrive in two different key spaces, and pairing them wrongly is
    silent: the shard reports key errors ``{ats}:{slug}`` (Workday's slug is a whole URL), while
    ``corpus.board_of`` yields the ``board_key`` shape the job ids were built from (ADR-0049).
    Everything here is normalised to ``board_key`` — the same conversion, and the same
    drop-what-will-not-resolve rule, that ``scrape_join.write_unauthoritative_boards`` applies.

    Unresolvable rows are dropped rather than passed through, because a key that cannot be
    resolved cannot be compared against ``produced`` either: keeping it would let a Board accrue
    strikes that no successful scrape could ever clear. That is the one place this differs from
    :func:`headstart.config.board_identity`, which falls back to the plain key because its job is
    to name every Board, not to pair two sets.
    """
    from headstart.scrapers.registry import get_scraper

    ats, sep, slug = str(report_key).partition(
        ":"
    )  # partition: a Workday slug holds colons
    if not sep or not ats or not slug:
        return None
    try:
        return get_scraper(ats, slug).board_key()
    except Exception:  # noqa: BLE001 - a malformed slug must not sink the ledger
        return None


def load(path: str | Path) -> dict[str, Failure]:
    """Read the ledger, or an empty mapping when it is absent or unreadable.

    Fails **open** on purpose: this file rides the HF state round-trip, and a missing or truncated
    copy must cost one run of memory, never quarantine a Board or stop the plan.
    """
    p = Path(path)
    if not p.exists():
        return {}
    rows: dict[str, Failure] = {}
    try:
        with p.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                board = (row.get("board") or "").strip()
                if not board:
                    continue
                try:
                    strikes = int(row.get("strikes") or 0)
                except ValueError:
                    continue  # a torn row is one Board's memory, not the file's
                rows[board] = Failure(
                    strikes=strikes,
                    last_reason=row.get("last_reason") or "",
                    last_seen_gone=row.get("last_seen_gone") or "",
                )
    except OSError:
        return {}
    return rows


def save(path: str | Path, rows: dict[str, Failure]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_FIELDS)
        for board in sorted(rows):
            row = rows[board]
            writer.writerow([board, row.strikes, row.last_reason, row.last_seen_gone])


def update(
    prev: dict[str, Failure],
    gone: dict[str, str],
    produced: set[str],
    now: str,
) -> dict[str, Failure]:
    """Blend one run's outcome into the ledger.

    ``gone`` is ``{board: reason}`` for the Boards that reported 404/410 this run; ``produced``
    is every Board that scraped alive — the shard reports' ``boards_ok`` (which includes
    zero-job successes) plus every Board with corpus lines. A Board in neither set was not
    scraped this run and keeps its row exactly as it was — the ledger only moves on evidence.
    """
    rows = dict(prev)
    for board in produced:  # alive: any output clears the streak
        rows.pop(board, None)
    for board, reason in gone.items():
        if board in produced:  # partial output beats a per-page 404
            continue
        prior = prev.get(board)
        strikes = (prior.strikes if prior else 0) + 1
        rows[board] = Failure(strikes=strikes, last_reason=reason, last_seen_gone=now)
    return rows


def quarantined(rows: dict[str, Failure]) -> set[str]:
    """The Boards that have earned their way out of the scrape slice."""
    return {board for board, row in rows.items() if row.quarantined}
