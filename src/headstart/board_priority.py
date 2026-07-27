"""Per-board tech-job priority: the ledger and the ordering it drives (ADR-0022).

A Board that yields tech jobs should be scraped — and its docs embedded — before the long
tail of boards that never do. The signal is a sticky EWMA of each Board's tech-job count,
persisted as ``data/state/board_priority.csv`` (``board,score,last_tech_jobs,updated_at``,
``board`` being the ``{ats}:{slug}`` key from :func:`headstart.corpus.board_of`). The file
rides the pipeline's HF-dataset state round-trip; it deliberately does NOT live in the
embedding store dir, which is regenerable and gets wiped by evictions — this history must
survive them. A missing file degrades every consumer to the old behavior (pure shuffle,
corpus order).
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from headstart.config import CompanyRef

FIELDS = ("board", "score", "last_tech_jobs", "updated_at")
CURRENT_WEIGHT = 0.7  # EWMA weight on the night's tech count (the rest on history)
EXPLORE_FRAC = 0.7  # slice share reserved for random exploration of unscored boards
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
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
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


def pick_boards(
    companies: list["CompanyRef"],
    scores: Mapping[str, float],
    max_boards: int,
    *,
    explore_frac: float = EXPLORE_FRAC,
    rng: random.Random | None = None,
) -> list["CompanyRef"]:
    """The run's slice: scored boards first (score desc), then random exploration.

    The head gets ``max_boards - round(max_boards * explore_frac)`` slots of scored boards
    (stable sort over a shuffle = random tiebreak); the exploration tail fills the rest from
    the remaining boards. A short scored list rolls its unused head slots into exploration.
    With no scores (bootstrap) or ``max_boards=0`` semantics this degrades to the previous
    behavior: pure shuffle + cap, or every board (scored-first when scores exist).
    """
    rng = rng or random.Random()
    shuffled = list(companies)
    rng.shuffle(shuffled)

    key = lambda c: f"{c.ats}:{c.slug}"  # noqa: E731 - matches corpus.board_of
    known = [c for c in shuffled if scores.get(key(c), 0.0) > 0.0]
    if not known:
        return shuffled[:max_boards] if max_boards else shuffled
    known.sort(
        key=lambda c: scores[key(c)], reverse=True
    )  # stable: shuffle breaks ties

    if not max_boards or max_boards >= len(companies):
        rest = [c for c in shuffled if scores.get(key(c), 0.0) <= 0.0]
        return known + rest

    head = known[: max_boards - round(max_boards * explore_frac)]
    head_set = {key(c) for c in head}
    tail = [c for c in shuffled if key(c) not in head_set][: max_boards - len(head)]
    return head + tail
