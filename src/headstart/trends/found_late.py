"""Postings HeadStart found late: first seen weeks after their posted date (ADR-0351, ADR-0369).

A company's jobs **Opened** are ids new on a Board already counted, and some were posted weeks
before HeadStart first saw them: a Board read again after a gap, postings listed again under new
ids, a scraper or filter change that let old postings in. Deloitte US opened 509 in the week to
2026-09-30, and 410 of its postings first seen that week were posted more than 14 days before;
its Avature Board had just begun to be read whole. This module is the one place that says which
postings were found late, counts them per Board, and decides when a figure of postings opened
was mostly found late, so the Hot ranking, ``/trends`` and every agent tool read one split under
one rule.

The counts are of **served** postings first seen since turnover began: a posting opened and
closed inside a window is in neither count, so the reader judges them against ``opened``
(:func:`mostly_found_late`). Opened itself is unchanged; redefining it by posted date belongs to
the restated Trends (ADR-0330).
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, NamedTuple

from headstart.boards.board_identity import board_end, lower_key
from headstart.search_filters import posted_date_guard

if TYPE_CHECKING:
    from headstart.trends.trend_history import TrendQuestion

#: A posting first seen more than this many days after its posted date was found late, not newly
#: posted (ADR-0351). Of Workday's postings first seen since 2026-09-01 on a Board already served
#: 3 days before, 74% were first seen within 2 days of posting, 81% within 14, and 13% over 30:
#: a gap of weeks is a posting found late, not a slow read.
FOUND_LATE_DAYS = 14

#: Below this many postings opened, a figure is never called mostly found late: one posting moves
#: the share too far. Box read 8 found late of 11 opened on 2026-09-29, each of the 8 first
#: published on Greenhouse in July or August (ADR-0351).
MIN_OPENED = 10


class Split(NamedTuple):
    """Served postings first seen in a span: posted within ``FOUND_LATE_DAYS`` of first sight or
    undated (``fresh``), and posted longer before (``found_late``)."""

    fresh: int
    found_late: int


def posting_found_late(seen: str, posted: str | None) -> bool:
    """Whether a posting first seen at ``seen`` was posted more than ``FOUND_LATE_DAYS`` before;
    False where its posted date cannot be read, so an undated posting counts as fresh."""
    if not posted_date_guard.is_comparable(posted):
        return False
    try:
        age = date.fromisoformat(seen[:10]) - date.fromisoformat(posted[:10])
    except ValueError:
        return False
    return age.days > FOUND_LATE_DAYS


def mostly_found_late(opened: int | None, fresh: int | None, late: int | None) -> bool:
    """Whether most of ``opened`` was found late (ADR-0351): of ``MIN_OPENED`` or more opened,
    the found-late postings number at least half, and the fresh ones fewer than half. Both are
    needed, as the counts are of postings still served, first seen in any run: the first alone
    would flag a company whose found-late postings mostly never reached its opened (Accenture
    Federal Services, 234 found late on 38 opened, 19 fresh), the second alone one whose opened
    left no served posting at all (New York Life's re-listed ids). False where any is unknown."""
    if opened is None or fresh is None or late is None:
        return False
    return opened >= MIN_OPENED and 2 * fresh < opened <= 2 * late


def clause(turnover: Mapping[str, Any] | None) -> str | None:
    """What an answer says of a line whose ``turnover`` (``opened``, ``opened_fresh``,
    ``opened_found_late``) was mostly found late, as a clause to go on that line; None where it
    was not, or its postings went uncounted. Every agent tool that reports a company's opened
    says it in these words (ADR-0369)."""
    turnover = turnover or {}
    opened = turnover.get("opened")
    fresh, late = turnover.get("opened_fresh"), turnover.get("opened_found_late")
    if not mostly_found_late(opened, fresh, late):
        return None
    # Counted over the postings still listed, which can outnumber opened: a posting first seen in
    # a run whose counting changed is listed but not opened. So the counts are not "of" opened.
    return (
        f"most of the {opened:,} postings opened were found, not newly posted: of the postings "
        f"HeadStart first saw in these runs and still lists, {late:,} were posted over "
        f"{FOUND_LATE_DAYS} days before HeadStart first saw them and {fresh:,} within "
        f"{FOUND_LATE_DAYS} days or with no date"
    )


def sentence(turnover: Mapping[str, Any] | None) -> str | None:
    """:func:`clause` as sentences of their own, saying what it means for hiring."""
    said = clause(turnover)
    return (
        f"{said[0].upper()}{said[1:]}. So most of this opened is not hiring."
        if said
        else None
    )


class FirstSeenPostings:
    """The served postings first seen since turnover began, counted per Board and first-seen
    stamp as fresh and found late, so any span of any Boards is summed without the postings.

    ``rows`` are ``(id, first_seen, posted_at)``; each id is matched to its Board among
    ``boards`` (Board keys, any case), so a native id holding a colon names its real Board
    (ADR-0049). An id on no Board of ``boards``, or never seen, is left out."""

    def __init__(
        self,
        rows: Iterable[tuple[str, str | None, str | None]],
        boards: Iterable[str],
    ) -> None:
        known = {lower_key(board) for board in boards}
        counted: dict[str, Counter[tuple[str, bool]]] = defaultdict(Counter)
        for job_id, seen, posted in rows:
            if not seen:
                continue
            end = board_end(job_id, known)
            if end is None:
                continue
            counted[lower_key(job_id[:end])][
                seen, posting_found_late(seen, posted)
            ] += 1
        # Per Board, its first-seen stamps in order, and the running (fresh, late) at each.
        self._stamps: dict[str, list[str]] = {}
        self._running: dict[str, list[tuple[int, int]]] = {}
        for board, counts in counted.items():
            stamps = sorted({seen for seen, _ in counts})
            running, fresh, late = [(0, 0)], 0, 0
            for stamp in stamps:
                fresh += counts[stamp, False]
                late += counts[stamp, True]
                running.append((fresh, late))
            self._stamps[board], self._running[board] = stamps, running

    def split(self, boards: Iterable[str], since: str, to: str) -> Split:
        """``boards``' postings first seen after ``since`` and by ``to``."""
        fresh = late = 0
        for board in {lower_key(b) for b in boards}:
            stamps = self._stamps.get(board)
            if not stamps:
                continue
            running = self._running[board]
            before = running[bisect_right(stamps, since)]
            through = running[bisect_right(stamps, to)]
            fresh += through[0] - before[0]
            late += through[1] - before[1]
        return Split(fresh, late)


def attach(
    payload: dict[str, Any],
    question: TrendQuestion,
    postings: FirstSeenPostings | None,
) -> dict[str, Any]:
    """``payload`` (a ``/trends`` answer with its reading) with each company line's turnover
    given ``opened_fresh`` and ``opened_found_late`` (ADR-0369): the first row where companies
    are picked, and each company's line under the company split.

    Each pick counts its postings first seen after the latest of the window's first tick, the
    first tick with turnover, and the pick's own first count, and by the window's last tick: the
    runs its opened is summed over. Given only where opened is every tech posting of the picks'
    whole Boards: not under a category or an ATS filter, comparable coverage or measure new,
    whose opened counts a part of what the postings do. Nothing is given where the postings
    went unread."""
    reading = payload.get("reading") or {}
    window = reading.get("window") or {}
    picks = {c["key"]: c["board_keys"] for c in payload.get("companies") or []}
    if (
        postings is None
        or not picks
        or not window
        or payload.get("metric") != "stock"
        or payload.get("coverage") != "all"
        or payload.get("family")
        or question.ats
    ):
        return payload
    began = payload.get("turnover_since")
    counted = payload.get("counted_since") or {}

    def split_of(keys: Iterable[str]) -> Split:
        parts = [
            postings.split(
                picks[key],
                max(window["from"], began or window["from"], counted.get(key) or ""),
                window["to"],
            )
            for key in keys
        ]
        return Split(sum(p.fresh for p in parts), sum(p.found_late for p in parts))

    def given(move: Mapping[str, Any] | None, keys: Iterable[str]) -> None:
        turnover = (move or {}).get("turnover")
        if turnover:
            split = split_of(keys)
            turnover["opened_fresh"] = split.fresh
            turnover["opened_found_late"] = split.found_late

    given((reading.get("total") or {}).get("move"), picks)
    if payload.get("split_by") == "company":
        for line in reading.get("lines") or []:
            if line.get("name") in picks:
                given(line.get("move"), [line["name"]])
    return payload
