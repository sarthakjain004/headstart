"""Per-board count of stored Jobs whose description we have never settled (ADR-0062).

The fourth per-board ledger, beside :mod:`headstart.board_priority`,
:mod:`headstart.board_cost` and :mod:`headstart.ingest.board_failures`. Keyed on the
**board_key** shape that :func:`headstart.corpus.board_of` yields — not ``f"{ats}:{slug}"``
(ADR-0059) — and then **lowercased**, which the other three ledgers are not.

That lowercasing is the whole reason :func:`key_for` exists rather than each caller spelling out
``board_identity(c).lower()``. Measured against a real store, 1,693 of 13,708 gap Boards — 45,375
Jobs, 23% of the backlog — matched the live slice only case-insensitively, because the casing baked
into a Job id and the casing in the liveness ledger need not agree (ADR-0049). It also folds
ADR-0023's case-variant pairs (``.../External`` and ``.../external`` are one Board) into a single
row instead of two half-counts. :func:`save` and :func:`load` normalise, so a hand-edited or
older file cannot reintroduce a key no lookup will reach.

A row means *this Board holds N embedded Jobs whose description the ADR-0050 store does not
settle*. Those Jobs cannot have their derived columns repaired, because a re-derivation without
the text a value came from could only downgrade it (ADR-0061) — so the only way to fix them is to
scrape their Board again. ``scrape_plan`` reserves a slice of every run for exactly these Boards,
and the ledger is what tells it which they are.

**It self-cancels.** The count is recomputed from scratch each run, so a Board drops out the moment
its descriptions settle, and an empty ledger reserves no slots at all — leaving the slice
byte-identical to what it was before this existed.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from headstart.config import CompanyRef

FIELDS = ("board", "unsettled", "updated_at")


def key_for(company: "CompanyRef") -> str:
    """This Board's key in the ledger — the one form every reader and writer must agree on."""
    from headstart.config import board_identity

    return board_identity(company).lower()


def load(path: str | Path) -> dict[str, int]:
    """The ledger as ``{board: unsettled Jobs}``; ``{}`` when the file doesn't exist yet.

    A missing file degrades the planner to its previous behaviour — no reserved slots — which is
    what makes this safe to ship before the first run has written one.
    """
    path = Path(path)
    if not path.exists():
        return {}
    rows: Counter[str] = Counter()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["board"].lower()] += int(row["unsettled"])
    return dict(rows)


def save(path: str | Path, rows: dict[str, int], *, today: str) -> None:
    """Write the ledger, most-unsettled first (stable diffs on the dataset), creating parent dirs.

    Counts are summed per lowercased key, so two case-variants of one Board become one row rather
    than two the slice can only half-match.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    folded: Counter[str] = Counter()
    for board, unsettled in rows.items():
        folded[board.lower()] += unsettled
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for board, unsettled in sorted(folded.items(), key=lambda kv: (-kv[1], kv[0])):
            writer.writerow([board, unsettled, today])
