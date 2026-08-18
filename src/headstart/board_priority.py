"""Per-board tech-job priority: the ledger and the ordering it drives (ADR-0022).

A Board that yields tech jobs should be scraped — and its docs embedded — before the long
tail of boards that never do. The signal is a sticky EWMA of each Board's tech-job count,
persisted as ``data/state/board_priority.csv`` (``board,score,last_tech_jobs,updated_at``,
``board`` being whatever :func:`headstart.corpus.board_of` yields — the **board_key** shape,
which is *not* ``{ats}:{slug}`` wherever a scraper overrides ``board_key()``: a Workday slug is
a whole careers URL and a Personio slug the whole host. Every lookup therefore goes through
:func:`headstart.config.board_identity`, never ``f"{ats}:{slug}"`` — keying it the latter way
left all 13,714 Workday and Personio boards permanently unscored). The file
rides the pipeline's HF-dataset state round-trip; it deliberately does NOT live in the
embedding store dir, which is regenerable and gets wiped by evictions — this history must
survive them. A missing file degrades every consumer to the old behavior (pure shuffle,
corpus order).
"""

from __future__ import annotations

import csv
import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from headstart.config import CompanyRef

FIELDS = ("board", "score", "last_tech_jobs", "updated_at")
CURRENT_WEIGHT = 0.7  # EWMA weight on the night's tech count (the rest on history)
EXPLORE_FRAC = 0.7  # slice share reserved for random exploration of unscored boards
GAP_FRAC = (
    0.05  # share of that exploration tail reserved for the description gap (ADR-0062)
)
PRUNE_BELOW = 0.05  # decayed rows below this drop out (~3 zero-tech scrapes)


@dataclass(frozen=True, slots=True)
class BoardPriority:
    score: float
    last_tech_jobs: int
    updated_at: str  # ISO date of the last snapshot that included the board


def load(path: str | Path) -> dict[str, BoardPriority]:
    """The ledger as {board: BoardPriority}; {} when the file doesn't exist yet."""
    path = Path(path)
    if not path.exists():
        return {}
    rows: dict[str, BoardPriority] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["board"]] = BoardPriority(
                score=float(row["score"]),
                last_tech_jobs=int(row["last_tech_jobs"]),
                updated_at=row["updated_at"],
            )
    return rows


def load_scores(path: str | Path) -> dict[str, float]:
    """Thin {board: score} view for the harvest and embed orderings."""
    return {board: p.score for board, p in load(path).items()}


def save(path: str | Path, rows: dict[str, BoardPriority]) -> None:
    """Write the ledger score-desc (stable diffs on the dataset), creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for board, p in sorted(rows.items(), key=lambda kv: (-kv[1].score, kv[0])):
            writer.writerow([board, f"{p.score:.4f}", p.last_tech_jobs, p.updated_at])


def update(
    prev: dict[str, BoardPriority],
    tech_counts: Mapping[str, int],
    snapshot_boards: set[str],
    *,
    current_weight: float = CURRENT_WEIGHT,
    prune_below: float = PRUNE_BELOW,
    today: str | None = None,
) -> dict[str, BoardPriority]:
    """Blend the night's tech counts into the ledger.

    Boards present in the snapshot get ``current_weight·count + (1-current_weight)·prev``
    (unseen boards start from 0 via the same formula); boards absent from the snapshot
    carry their row unchanged — a partial harvest must not decay what it didn't look at.
    Rows decayed below ``prune_below`` drop out; exploration re-adds a board that hires
    tech again.
    """
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    rows = dict(prev)
    for board in snapshot_boards:
        current = tech_counts.get(board, 0)
        prev_score = prev[board].score if board in prev else 0.0
        score = current_weight * current + (1 - current_weight) * prev_score
        if score < prune_below:
            rows.pop(board, None)
        else:
            rows[board] = BoardPriority(
                score=score, last_tech_jobs=current, updated_at=today
            )
    return rows


def _gap_picks(
    companies: list[CompanyRef],
    unsettled: Mapping[str, int],
    taken: set[str],
    slots: int,
) -> list[CompanyRef]:
    """The gap quota's boards: cheapest class first, then most unsettled Jobs (ADR-0062).

    Listing-only ATSes come first because their descriptions arrive *with the listing* — one
    request per Board settles every Job on it — while a detail-pass ATS needs a fetch per Job.
    Draining the cheap half first buys a third of the backlog for a few percent of the cost and
    keeps the makespan risk away from the first runs. Within a class, the Board holding the most
    unsettled Jobs goes first, so each slot repairs as many rows as it can.
    """
    from headstart.board_description_gap import key_for
    from headstart.config import board_identity
    from headstart.scrapers.registry import detail_pass_atses

    detail_pass = detail_pass_atses()
    candidates = [
        c
        for c in companies
        if key_for(c) in unsettled and board_identity(c) not in taken
    ]
    # `False < True`, so listing-only sorts ahead of detail-pass. An ATS missing from the registry
    # cannot be scraped at all, so where it lands is moot — it is treated as the expensive class
    # rather than special-cased.
    candidates.sort(key=lambda c: (c.ats in detail_pass, -unsettled[key_for(c)]))
    return candidates[:slots]


def pick_boards(
    companies: list[CompanyRef],
    scores: Mapping[str, float],
    max_boards: int,
    *,
    explore_frac: float = EXPLORE_FRAC,
    unsettled: Mapping[str, int] | None = None,
    gap_frac: float = GAP_FRAC,
    rng: random.Random | None = None,
) -> list[CompanyRef]:
    """The run's slice: scored boards first (score desc), a gap quota, then random exploration.

    The head gets ``max_boards - round(max_boards * explore_frac)`` slots of scored boards
    (stable sort over a shuffle = random tiebreak); the exploration tail fills the rest from
    the remaining boards. A short scored list rolls its unused head slots into exploration.
    With no scores (bootstrap) or ``max_boards=0`` semantics this degrades to the previous
    behavior: pure shuffle + cap, or every board (scored-first when scores exist).

    ``unsettled`` is the ADR-0062 description-gap ledger, ``{board: Jobs whose description we
    have never settled}``. When given, ``round(tail * gap_frac)`` of the *exploration* slots are
    reserved for those Boards — the priority head is never touched, because a random exploration
    pick is strictly worse than a Board we already know is worth visiting. It self-cancels: an
    empty or absent ledger reserves nothing and the slice is byte-identical to before.
    """
    from headstart.config import board_identity

    rng = rng or random.Random()
    shuffled = list(companies)
    rng.shuffle(shuffled)

    # `board_identity`, not `f"{ats}:{slug}"`. The ledger is written by `update_ledgers priority`
    # from `corpus.board_of(job_id)`, which yields the **board_key** shape — and Workday and
    # Personio override `board_key()` (a Workday slug is a whole careers URL, a Personio slug the
    # whole host), so the old key could never match one of their rows. It even carried a comment
    # claiming it matched `corpus.board_of`.
    #
    # 13,402 boards — 20.1% of the scrape list — are keyed that way. What it cost is the subset
    # that had actually earned a score: against a local ledger snapshot (2026-08-16; the live one
    # rides HF, so treat this as indicative) 4,611 of them held a row — 3,784 Workday, 827
    # Personio — every one scoring 0.0 whatever it had earned, reachable only through the random
    # exploration tail. No board loses a score from this change; 4,611 regain one.
    known = [c for c in shuffled if scores.get(board_identity(c), 0.0) > 0.0]
    # Sorting an empty list is a no-op, so the bootstrap case (no ledger yet) falls through the
    # same path rather than returning early. It has to: the gap quota is reserved out of the
    # exploration slots, and an early return skipped it entirely whenever nothing was scored —
    # which is exactly the state a fresh or lost priority ledger leaves behind.
    known.sort(
        key=lambda c: scores[board_identity(c)], reverse=True
    )  # stable: shuffle breaks ties

    if not max_boards or max_boards >= len(companies):
        rest = [c for c in shuffled if scores.get(board_identity(c), 0.0) <= 0.0]
        return known + rest

    head = known[: max_boards - round(max_boards * explore_frac)]
    head_set = {board_identity(c) for c in head}
    explore_slots = max_boards - len(head)
    gap = (
        _gap_picks(shuffled, unsettled, head_set, round(explore_slots * gap_frac))
        if unsettled
        else []
    )
    picked = head_set | {board_identity(c) for c in gap}
    tail = [c for c in shuffled if board_identity(c) not in picked][
        : explore_slots - len(gap)
    ]
    return head + gap + tail
