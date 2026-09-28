"""Dormant Boards: a Board whose newest posting is more than two years old is not hiring (ADR-0248).

An ATS serves a posting until someone closes it, and nobody closes anything on a Board nobody
tends. SmartRecruiters still serves all 6,519 of SonsoftInc's postings as open, every one from
2016-17, and 40 of 40 sampled postings on small silent Boards across 13 ATSes answered 200 (#570).
On served table v448 (2026-09-28), 55,101 rows (10.3%) sat on 3,896 such Boards, and they were 40%
of the rows whose title says "Java".

`scrape_join` judges every Board as the snapshot streams past and writes the verdict to
:data:`headstart.ingest.DORMANT_BOARDS_PATH`, `filter_tech` leaves a Dormant Board's rows out of the
Tech subset, and `index sync` names the Boards apart when their rows go Unconfirmed. The rest follows
from machinery that already exists: the Board was scraped, so its rows missing from the Tech subset
are evicted through the Unconfirmed grace period (ADR-0083), and the priority ledger, which counts
the Tech subset, decays the Board out of the head. A Board that
posts again is not Dormant on its next scrape, and its postings come back with it.

A Board is judged only on evidence. It is not Dormant when its scrape this run was not
authoritative, since a truncated list may have lost the newest page. It is not Dormant when any
posting on it has no usable date, since an undated posting may be last week's. A Jibe Board is
never judged, because its lines are not its whole listing (:data:`_PARTIAL_LISTING_ATSES`).

Judged per Board, never per posting. A real opening on a Board that still posts can carry an old
date: Databricks' 2021 "Senior Software Engineer - Database Engine Internals", or Netlight's
"Software Engineering Consultant (2026/27 Graduate)" dated 2021. A posting-age cutoff would
delete them.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import date, timedelta
from pathlib import Path

from headstart.boards.board_identity import ats_of, lower_key
from headstart.search_filters.posted_date_guard import is_comparable

#: A Board whose newest posting is older than this is Dormant.
DORMANT_AFTER = timedelta(days=730)

# Below this a date is a placeholder, not a posting date: keka serves `0001-01-01` and `1900-01-01`
# (19 rows in served v448). It reads as undated, so it can never make a Board look Dormant.
_EARLIEST_POSTING_DATE = "2000-01-01"

# ATSes whose scraper drops postings before they become lines, so a Board's lines are not its whole
# listing and its newest posting may be among the dropped ones. Jibe drops every posting a Scrapable
# iCIMS Board already serves (ADR-0240). Their Boards read as undated, so none is ever Dormant.
_PARTIAL_LISTING_ATSES = frozenset({"jibe"})


def posting_date(posted_at: object) -> str | None:
    """The ``YYYY-MM-DD`` a posting went up, or None when its ``posted_at`` gives no usable date."""
    if not isinstance(posted_at, str) or not is_comparable(posted_at):
        return None
    day = posted_at[:10]
    return day if day >= _EARLIEST_POSTING_DATE else None


class PostingDates:
    """Each Board's newest posting date, gathered one posting at a time. A Board with any undated
    posting holds None, and nothing seen after that changes it."""

    def __init__(self) -> None:
        self._newest: dict[str, str | None] = {}
        self._jobs: dict[str, int] = {}

    def see(self, board: str, posted_at: object) -> None:
        key = lower_key(board)
        self._jobs[key] = self._jobs.get(key, 0) + 1
        day = None if ats_of(key) in _PARTIAL_LISTING_ATSES else posting_date(posted_at)
        if day is None:
            self._newest[key] = None
        elif (
            key not in self._newest
            or (newest := self._newest[key]) is not None
            and day > newest
        ):
            self._newest[key] = day

    def jobs(self, board: str) -> int:
        """How many Jobs this Board's scrape emitted."""
        return self._jobs.get(lower_key(board), 0)

    def dormant(self, today: date, unauthoritative: Collection[str]) -> dict[str, str]:
        """``{lowercased Board: its newest posting date}`` for every Board judged Dormant.

        ``unauthoritative`` holds lowercased Board keys, as ``scrape_join`` already has them.
        """
        cutoff = (today - DORMANT_AFTER).isoformat()
        return {
            board: newest
            for board, newest in sorted(self._newest.items())
            if newest is not None and newest < cutoff and board not in unauthoritative
        }


def write(verdict: dict[str, str], path: Path) -> None:
    """Always written, even empty: `filter_tech` reads a missing file as "no verdict this run"."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verdict, indent=1, sort_keys=True), encoding="utf-8")


def read(path: Path) -> frozenset[str] | None:
    """The lowercased Dormant Board keys at ``path``, or None when there is no usable verdict.

    None leaves every Board in, which is the Tech subset as it was before this rule. That is the
    safe direction to fail: a lost verdict costs one run of stale rows, while a wrong one would
    evict live postings.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return frozenset(lower_key(str(board)) for board in data)
