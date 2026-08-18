"""Per-board count of stored Jobs whose description we have never settled (ADR-0062).

The fourth per-board ledger, beside :mod:`headstart.board_priority`,
:mod:`headstart.board_cost` and :mod:`headstart.ingest.board_failures`, and keyed like the
first of them: whatever :func:`headstart.corpus.board_of` yields — the **board_key** shape, so
every lookup goes through :func:`headstart.config.board_identity` rather than ``f"{ats}:{slug}"``
(ADR-0059).

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
from pathlib import Path

FIELDS = ("board", "unsettled", "updated_at")


def load(path: str | Path) -> dict[str, int]:
    """The ledger as ``{board: unsettled Jobs}``; ``{}`` when the file doesn't exist yet.

    A missing file degrades the planner to its previous behaviour — no reserved slots — which is
    what makes this safe to ship before the first run has written one.
    """
    path = Path(path)
    if not path.exists():
        return {}
    rows: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["board"]] = int(row["unsettled"])
    return rows


def save(path: str | Path, rows: dict[str, int], *, today: str) -> None:
    """Write the ledger unsettled-desc, creating parent dirs (stable diffs on the dataset)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for board, unsettled in sorted(rows.items(), key=lambda kv: (-kv[1], kv[0])):
            writer.writerow([board, unsettled, today])
