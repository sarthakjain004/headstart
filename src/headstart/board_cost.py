"""Per-board measured scrape cost: the ledger the scrape planner bin-packs on (ADR-0027).

ADR-0026 costed each Board by its **tech**-job EWMA times a per-ATS weight. That estimate carried
no signal: run 30131376268 packed 14 shards the model rated identical (``~23275`` each) and they
ran 60 s to 1,222 s. The units were wrong — scrape cost is driven by total postings and per-posting
detail latency, not by how many of those postings happen to be tech.

So cost is measured, not proxied. Each scrape shard times every Board (``pipeline.JobWriter
.record_cost``) and the join blends the shards' rows into ``data/state/board_cost.csv``
(``board,seconds,jobs,updated_at``), which rides the same HF state round-trip as the priority
ledger. The next run's planner packs on those seconds.

Same shape as :mod:`headstart.board_priority` deliberately — an EWMA over a per-Board key, a
missing file degrading to the old behavior. Two differences, both from cost being noisier than a
job count: the blend leans harder on history (:data:`CURRENT_WEIGHT`), and a Board with no
measurement yet falls back to its **ATS's median** rather than one global constant, because
per-Board scrape time varies by orders of magnitude across ATSes.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

FIELDS = ("board", "seconds", "jobs", "updated_at")
# The per-shard file a scrape writes (pipeline.JobWriter.record_cost) and read_shard_rows reads.
# Schema lives here, next to its reader, so adding a column is one edit rather than two files.
SHARD_FIELDS = ("board", "seconds", "jobs", "unfinished")
SHARD_HEADER = ",".join(SHARD_FIELDS) + "\n"


@dataclass(frozen=True, slots=True)
class ShardCost:
    """One shard's timing for one Board — what the fragment carries to the join."""

    seconds: float
    jobs: int
    # True when the shard was killed while this Board was still fetching. Then `seconds` is a
    # *lower bound* on the Board's cost, not a measurement of it, and the two must not blend the
    # same way: an EWMA would record less than the kill proved, which is how a Board too big to
    # finish kept a price low enough to be packed again every run (ADR-0064).
    unfinished: bool = False


def shard_row(
    board_key: str, seconds: float, jobs: int, *, unfinished: bool = False
) -> str:
    """One ``board_cost.csv`` line, newline included."""
    return f"{board_key},{seconds:.3f},{jobs},{int(unfinished)}\n"


# EWMA weight on this run's seconds. Lower than board_priority's 0.7 because wall time carries
# runner and network noise a tech-job count doesn't; this is the knob if shards still straggle.
CURRENT_WEIGHT = 0.5
FALLBACK_SECONDS = 5.0  # last resort: no measurement anywhere, not even for the ATS


@dataclass(frozen=True, slots=True)
class BoardCost:
    seconds: float  # EWMA of measured scrape wall time
    jobs: int  # postings the last measured scrape returned (diagnostic only)
    updated_at: str  # ISO date of the last run that measured this Board


def load(path: str | Path) -> dict[str, BoardCost]:
    """The ledger as {board: BoardCost}; {} when the file doesn't exist yet."""
    path = Path(path)
    if not path.exists():
        return {}
    rows: dict[str, BoardCost] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["board"]] = BoardCost(
                seconds=float(row["seconds"]),
                jobs=int(row["jobs"]),
                updated_at=row["updated_at"],
            )
    return rows


def save(path: str | Path, rows: dict[str, BoardCost]) -> None:
    """Write the ledger cost-desc (stable diffs on the dataset), creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for board, c in sorted(rows.items(), key=lambda kv: (-kv[1].seconds, kv[0])):
            writer.writerow([board, f"{c.seconds:.3f}", c.jobs, c.updated_at])


def read_shard_rows(path: str | Path) -> dict[str, ShardCost]:
    """One shard's ``board_cost.csv`` as {board: ShardCost}.

    Tolerates a truncated final line: a shard killed mid-write by its time budget can leave one,
    and dropping just that row is strictly better than losing the shard's whole measurement set.
    A fragment written before the ``unfinished`` column existed reads as all-measured, which is
    what it was — the column is absent, not false.
    """
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[str, ShardCost] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        # Whether the *file* has the column, not whether a row does. A row missing it means two
        # different things: in a pre-``unfinished`` fragment the row is a complete measurement,
        # but in a current one it is a tail torn mid-write — and a torn floor row read as a
        # measurement gets EWMA-blended, which is the one thing the floor exists to prevent.
        has_flag = "unfinished" in (reader.fieldnames or ())
        for row in reader:
            try:
                flag = row.get("unfinished")
                if has_flag and flag is None:
                    continue  # torn tail row
                out[row["board"]] = ShardCost(
                    seconds=float(row["seconds"]),
                    jobs=int(row["jobs"]),
                    unfinished=bool(int(flag or 0)),
                )
            except (TypeError, ValueError):
                continue  # half-written tail row
    return out


def update(
    prev: dict[str, BoardCost],
    measured: Mapping[str, ShardCost],
    *,
    current_weight: float = CURRENT_WEIGHT,
    today: str | None = None,
) -> dict[str, BoardCost]:
    """Blend this run's measured seconds into the ledger.

    Boards measured this run get ``current_weight·now + (1-current_weight)·prev`` (a first
    measurement adopts the raw value, since there is no history to blend). Boards the run didn't
    scrape carry their row unchanged — a partial harvest must not decay what it never timed.
    Nothing is pruned: unlike a tech-yield score, a Board being expensive is not a reason to
    forget it, and the row is tiny.

    An **unfinished** row does not blend. Its seconds are a lower bound — the Board ran that long
    and still had work left — so the ledger takes ``max(stored, burned)``: a bound may raise a
    Board's price, never lower it. Blending one instead records less than the kill proved, and
    for the Board this matters for that is the difference between being re-packed every run and
    being priced honestly (ADR-0064). Its ``jobs`` count is left as it was, because the Board
    banked no complete listing this run and a 0 there would erase what the last full scrape saw.
    """
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    rows = dict(prev)
    for board, now in measured.items():
        if (
            now.seconds <= 0
        ):  # never measured (or a clock artifact) — don't poison the EWMA
            continue
        before = prev.get(board)
        if now.unfinished:
            floor = max(now.seconds, before.seconds) if before else now.seconds
            rows[board] = BoardCost(
                seconds=floor,
                jobs=before.jobs if before else now.jobs,
                updated_at=today,
            )
            continue
        blended = (
            now.seconds
            if before is None
            else current_weight * now.seconds + (1 - current_weight) * before.seconds
        )
        rows[board] = BoardCost(seconds=blended, jobs=now.jobs, updated_at=today)
    return rows


def _ats_of(board_key: str) -> str:
    """The ATS half of an ``{ats}:{slug}`` Board key (``corpus.board_of``'s key space)."""
    return board_key.split(":", 1)[0]


def ats_medians(rows: Mapping[str, BoardCost]) -> dict[str, float]:
    """Median measured seconds per ATS — the fallback for a Board with no history of its own."""
    by_ats: dict[str, list[float]] = {}
    for board, c in rows.items():
        by_ats.setdefault(_ats_of(board), []).append(c.seconds)
    return {ats: median(vals) for ats, vals in by_ats.items() if vals}


def costs_for(
    board_keys: Iterable[str],
    rows: Mapping[str, BoardCost],
    *,
    fallback: float = FALLBACK_SECONDS,
) -> list[float]:
    """Expected seconds for each ``{ats}:{slug}`` key, best available estimate per Board.

    Measured EWMA if we have one, else the ATS's median, else the global median, else
    ``fallback``. The cascade matters: an unmeasured Workday board and an unmeasured Personio
    board have wildly different expected cost, and collapsing both to one constant is exactly
    what made the ADR-0026 estimate useless.
    """
    medians = ats_medians(rows)
    overall = median(medians.values()) if medians else fallback
    out: list[float] = []
    for key in board_keys:
        row = rows.get(key)
        if row is not None:
            out.append(row.seconds)
        else:
            out.append(medians.get(_ats_of(key), overall) or fallback)
    return out
