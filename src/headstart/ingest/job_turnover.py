"""Book every tech Job that arrived or left since the last tick as Opened, Closed or Recounted
(ADR-0222).

Trends showed only a company's net change, so a company that opened five jobs and closed five
read flat. A probe of Sep 18–25 (ADR-0222) found Amazon at net +17 while it opened 914–1,532.
Those were ranges only because nothing recorded when an id arrived or left. `role_trends` now diffs
last tick's served tech ids against this tick's, and this module decides what each difference was:

- **Opened**: a new id with ``first_seen`` after the previous tick, on a Board the previous tick
  already counted.
- **Closed**: an id that left because ``index sync`` evicted it, which is its second consecutive
  absence (ADR-0083). A closure therefore lands one scrape of its Board after the posting went.
  Sync queues its evictions (``EVICTED_IDS_PATH``), and nothing else is booked Closed.
- **Recounted**: every other arrival or departure, none of which is hiring. That covers a found
  Board's backlog; a row ``index prune`` removed as a duplicate or off-Board, in the pipeline or
  in ``cleanup-index``; a served row the classifier moved into or out of tech (an arrival with an
  older ``first_seen``, or a departure still in the table); and an id whose family, band or Board
  key changed, booked out of its old key and into its new one.

For every key and tick, ``Δstock = opened − closed + recounted_in − recounted_out`` exactly.
That is what lets a sentence give all three without them disagreeing.

What this cannot tell: a job a tech-filter change lets in arrives with a fresh ``first_seen``,
exactly like a new posting. The readers leave a counting change's run, and the run after it, out
of the turnover, as they already do for net (``hot_boards.counting_changes``, app.js
``stepNotes``). A job opened and closed between two scrapes of its Board is in no count at all, so
every count is a lower bound.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from headstart.ingest.role_assignments import Placement
from headstart.ingest.role_family_classifier import normalise

OPENED = "opened"
CLOSED = "closed"
RECOUNTED_IN = "recounted_in"
RECOUNTED_OUT = "recounted_out"
#: The delta-ledger metrics this module writes, beside `stock` and `new`.
METRICS = (OPENED, CLOSED, RECOUNTED_IN, RECOUNTED_OUT)
#: A marker, not turnover: one row per Board per tick whose scrape was Unauthoritative (ADR-0053),
#: so its absences were not read and none of its closures could be counted that tick.
UNSCOPED = "unscoped"

Key = tuple[
    str, str, str, str, str
]  # (board, metric, family, band, ats), as the delta ledger


def turnover(
    previous: Mapping[str, Placement],
    current: Mapping[str, Placement],
    *,
    previous_as_of: str,
    first_seen: Mapping[str, str | None],
    counted_boards: AbstractSet[str],
    evicted: AbstractSet[str],
) -> dict[Key, int]:
    """The turnover between two ticks' placements, as ``{(board, metric, family, band, ats): n}``.

    ``previous_as_of`` is the previous tick's stamp. ``first_seen`` covers the current rows.
    ``counted_boards`` holds the Boards the previous tick counted any row of. ``evicted`` holds
    the ids ``index sync`` evicted since then: only those are Closed.
    """
    booked: dict[Key, int] = {}

    def book(placed: Placement, metric: str) -> None:
        key = (placed.board, metric, placed.family, placed.band, placed.ats)
        booked[key] = booked.get(key, 0) + 1

    for job_id, now in current.items():
        was = previous.get(job_id)
        if was is None:
            seen = first_seen.get(job_id)
            # ISO-8601 UTC on both sides, so string order is time order.
            fresh = bool(seen) and seen > previous_as_of
            book(now, OPENED if fresh and now.board in counted_boards else RECOUNTED_IN)
        elif was != now:
            book(was, RECOUNTED_OUT)
            book(now, RECOUNTED_IN)
    for job_id, was in previous.items():
        if job_id not in current:
            book(was, CLOSED if job_id in evicted else RECOUNTED_OUT)
    return booked


def reposts(
    arrived: Mapping[str, tuple[str, str | None]],
    absent: Mapping[str, tuple[str, str | None]],
) -> int:
    """How many ``arrived`` ids share a Board and a normalised title with an ``absent`` one.

    Both map an id to ``(board, title)``. A repost is the same role under a new id, so it reads
    as one opened and one closed. This measures how often that happens, and changes no count.
    It is measured at the scrape that sees both: there the old id goes missing (Unconfirmed) as
    the new one arrives, a scrape before the old one is evicted."""
    gone = {(board.lower(), normalise(title)) for board, title in absent.values()}
    return sum(
        (board.lower(), normalise(title)) in gone for board, title in arrived.values()
    )
