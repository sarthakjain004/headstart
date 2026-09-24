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

Quarantine removes a Board from the *scrape slice* (``scrape_plan``). It deliberately does not
touch ``data/validate/liveness/``, which stays the probe-owned truth. A first-time quarantine does
not reach ``index prune`` either: a zwayam outage quarantined the whole provider at exactly
:data:`QUARANTINE_AT` while its Boards stayed live (ADR-0170). Only a verdict parole re-earns a
week later, :func:`reconfirmed`, takes the Board out of prune's keep-set and evicts its rows
(ADR-0206). And a verdict struck before its ATS's scraper replaced the surface that struck it is
void (:data:`_VOID_BEFORE`): :func:`load` drops it.

And the verdict **expires**: see :func:`paroled` and ADR-0162. A quarantined Board is never
scraped, so it can never re-enter ``produced``, so the clearing branch in :func:`update` is
unreachable and the ledger only grows — measured over the five runs of 2026-09-16, ``0 cleared by
a successful scrape`` in 5 of 5 while the total climbed 749 → 755. Parole re-admits it for one
run every :data:`PAROLE_DAYS`, and the answer decides: a fresh 404 restamps the row, anything
alive deletes it.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from headstart.board_identity import ats_of, lower_key

if TYPE_CHECKING:
    from headstart.scrapable_boards import ScrapableBoard

# Consecutive gone-runs before a Board leaves the scrape slice. Five rather than two because a
# Board only ages when it is actually scraped, and the exploration tail re-selects a given Board
# roughly one run in four — so five strikes is weeks of agreement, not an afternoon's blip.
QUARANTINE_AT = 5

# Days a gone-verdict stands before the Board is re-admitted for one run to re-earn it (ADR-0162).
#
# Seven, not the value gate's fortnight (``scrape_plan._GATE_RECHECK_DAYS``), because the two
# re-checks cost three orders of magnitude apart. That gate re-admits a Board measured at 15+ min
# of shard time; a quarantined Board's last measured scrape is p50 **0.10 s** (p90 0.94 s, 354 s
# for all 757 together — ``data/state/board_cost.csv``, 2026-09-16), because it dies on the
# listing request. So the fortnight there buys something real and would only be cargo-culted here.
#
# At 7 days and ~24 runs/day the re-admitted pool is ~31 Boards — 0.16% of a 20,000-Board slice —
# against a measured 23 of 757 quarantined Boards that answer 200 today, **12 of them serving 264
# tech postings** (5,593 raw, but ADR-0017's gate is what decides what reaches users). Not one day:
# that is 5,299 requests a week instead of 757, at origins that have already said 404 five times,
# to catch the same ~5.6 recoveries. ADR-0162 has the full arithmetic and the alternatives.
PAROLE_DAYS = 7

# "Gone" as the origin reports it. Matched against the recorded reason, which the shard reports
# carry as "{ExcType}: {message}" (e.g. "HTTPError: HTTP Error 404: ").
_GONE = re.compile(r"HTTP Error (404|410)\b")

_FIELDS = ("board", "strikes", "last_reason", "last_seen_gone")

# Per ATS, the instant its listing surface was replaced: a verdict struck before it was earned
# against a surface the scraper no longer reads, so :func:`load` drops it (ADR-0206). An entry is
# added only on measurement — the replaced surface still 404ing for Boards the new one lists.
#
# trakstar: #564 moved the listing off the HTML board onto ``jsapi.recruiterbox.com``, and the HTML
# board still answers 404 for Boards the API lists (``twonice``, 144 postings on 2026-09-24).
_VOID_BEFORE = {"trakstar": datetime.fromisoformat("2026-09-22T15:07:27+00:00")}


class Failure(NamedTuple):
    """One Board's consecutive-gone streak."""

    strikes: int
    last_reason: str
    last_seen_gone: str

    @property
    def quarantined(self) -> bool:
        return self.strikes >= QUARANTINE_AT


def key_for(board: ScrapableBoard | str) -> str:
    """The lookup form of a Board against this ledger: its identity, or a key it is handed,
    lowercased (ADR-0192). Not the stored form, which stays verbatim.

    Rows are stored as :func:`~headstart.board_identity.board_key_of` spells them, and
    :func:`update` pairs them verbatim with ``board_of`` keys from the same run. Only the planner's
    quarantine test folds, because the liveness ledger's casing and a Job id's need not agree
    (ADR-0049).
    """
    return lower_key(board) if isinstance(board, str) else board.lowercase_identity


def is_gone(reason: str) -> bool:
    """Whether a recorded scrape failure means *the Board no longer exists*, rather than *the
    fetch failed*. Only this class of failure may age a Board toward quarantine."""
    return bool(_GONE.search(reason or ""))


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
                failure = Failure(
                    strikes=strikes,
                    last_reason=row.get("last_reason") or "",
                    last_seen_gone=row.get("last_seen_gone") or "",
                )
                if not _void(board, failure):
                    rows[board] = failure
    except OSError:
        return {}
    return rows


def _void(board: str, row: Failure) -> bool:
    """Whether this verdict was struck before its ATS's scraper replaced the surface that struck
    it (:data:`_VOID_BEFORE`). An unreadable stamp on such an ATS is void too: this module fails
    open, so doubt must never keep a Board quarantined."""
    cutoff = _VOID_BEFORE.get(ats_of(board))
    if cutoff is None:
        return False
    try:
        return datetime.fromisoformat(row.last_seen_gone) < cutoff
    except (TypeError, ValueError):
        return True


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


def reconfirmed(rows: dict[str, Failure]) -> set[str]:
    """The quarantined Boards whose gone-verdict parole has re-earned — the only ones whose served
    rows ``index prune`` evicts (ADR-0206).

    A quarantined Board is out of the slice, so a strike past :data:`QUARANTINE_AT` can only come
    from a parole scrape, :data:`PAROLE_DAYS` or more after the verdict: two gone-verdicts a week
    apart, ADR-0170's third prerequisite. A first-time quarantine is not enough — a zwayam outage
    on 2026-09-19 quarantined the whole provider at exactly five strikes while its Boards stayed
    live (ADR-0170).
    """
    return {board for board, row in rows.items() if row.strikes > QUARANTINE_AT}


def paroled(rows: dict[str, Failure], now: str) -> set[str]:
    """The quarantined Boards whose gone-verdict has expired — back in the *candidate* pool.

    A verdict is evidence with an age, not a fact. Nothing re-probes a quarantined Board, so
    without this the ledger records forever what one afternoon found: re-probed live on
    2026-09-16, **23 of the 757** Boards then quarantined answered 200 again, and **12 of those
    served 264 tech postings** — coverage the product had lost with no metric reporting the loss.
    Count the tech subset, not the raw 5,593: one non-tech Board (`greenhouse:svetness`, a
    personal-training franchise) is 4,980 of that total and contributes nothing to the index.

    Only a *quarantined* row is eligible; one still accruing strikes is in the slice anyway. The
    caller re-admits these and :func:`update` judges what comes back, so a Board that 404s again
    simply restamps its row and serves another :data:`PAROLE_DAYS`.

    Re-admitted is not scraped. ``pick_boards`` still has to choose the Board, and an unscored one
    goes into the random exploration tail, which selected at p = 0.144 when this was measured
    (14,000 explore slots over a 97,254-Board tail pool). So a parole cohort drains over several
    runs rather than being probed in one — expect roughly one in seven of it per run.

    A Board whose re-probe fails some *other* way (timeout, TLS, 429) is neither gone nor
    produced, so its row is untouched and it stays paroled until a verdict arrives. That is the
    right direction: the premise of quarantine is *confirmed* gone, and a Board we can no longer
    confirm is not one we have grounds to keep excluding.
    """
    return {
        board
        for board, row in rows.items()
        if row.quarantined and _verdict_age_days(row, now) >= PAROLE_DAYS
    }


def _verdict_age_days(row: Failure, now: str) -> float:
    """How long ago this row's gone-verdict was earned; ``inf`` when either stamp is unreadable.

    Named for the thing rather than for the subtraction, deliberately: ``scrape_plan._days_since``
    already does date arithmetic one import away, and two near-identical names in one traceback is
    the hazard CLAUDE.md names. The ``inf``-on-unreadable contract is the same as that one's, and
    for the same reason — this module fails open everywhere, so a bad date must re-admit a Board,
    never strand it. A naive stamp is the realistic bad case (every row written here is tz-aware),
    and subtracting one raises ``TypeError`` rather than ``ValueError``.
    """
    try:
        return float(
            (
                datetime.fromisoformat(now) - datetime.fromisoformat(row.last_seen_gone)
            ).days
        )
    except (TypeError, ValueError):
        return float("inf")
