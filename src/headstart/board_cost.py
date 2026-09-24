"""Per-board measured scrape cost: the ledger the scrape planner bin-packs on (ADR-0027).

ADR-0026 costed each Board by its **tech**-job EWMA times a per-ATS weight. That estimate carried
no signal: run 30131376268 packed 14 shards the model rated identical (``~23275`` each) and they
ran 60 s to 1,222 s. The units were wrong — scrape cost is driven by total postings and per-posting
detail latency, not by how many of those postings happen to be tech.

So cost is measured, not proxied. Each scrape shard times every Board (``pipeline.JobWriter
.record_cost``) and the join blends the shards' rows into ``data/state/board_cost.csv``
(``board,seconds,jobs,updated_at``), which rides the same HF state round-trip as the priority
ledger. The next run's planner packs on those seconds.

Same shape as :mod:`headstart.board_priority` deliberately, and since ADR-0096 the **same key**:
both are an EWMA over ``board_identity``. They disagreed until 2026-08-28 — this one written by
`harvest` under the scraper's raw ``{ats}:{slug}``, the other from Job ids under ``board_key()`` —
so the two ledgers described the same Boards under names that could never be joined, and a Workday
tenant migrating between pods orphaned its own cost history. A missing file degrades to the old
behavior. Two differences, both from cost being noisier than a
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
from typing import TYPE_CHECKING

from headstart.board_identity import ats_of

if TYPE_CHECKING:
    from headstart.scrapable_boards import ScrapableBoard

FIELDS = ("board", "seconds", "jobs", "updated_at")
# The per-shard file a scrape writes (pipeline.JobWriter.record_cost) and read_shard_rows reads.
# Schema lives here, next to its reader, so adding a column is one edit rather than two files.
SHARD_FIELDS = ("board", "seconds", "jobs", "unfinished", "errored")
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
    # True when the Board's scrape RAISED. `seconds` is still a real measurement — `harvest` times
    # it in a `finally` on purpose, so a Board that burns an hour and then fails is priced for it —
    # but `jobs` is not: `harvest` initialises `n_fresh = 0` before the try and records it
    # regardless of outcome, so an errored Board writes a 0 that means "we never found out", not
    # "there was nothing". Nothing distinguished those until ADR-0145 made the difference
    # load-bearing, and `run_one`'s own comment had already named the hazard: "the value gate would
    # drop it forever, on a Board that failed instantly".
    errored: bool = False


def shard_row(
    board_key: str,
    seconds: float,
    jobs: int,
    *,
    unfinished: bool = False,
    errored: bool = False,
) -> str:
    """One ``board_cost.csv`` line, newline included."""
    return f"{board_key},{seconds:.3f},{jobs},{int(unfinished)},{int(errored)}\n"


# EWMA weight on this run's seconds. Lower than board_priority's 0.7 because wall time carries
# runner and network noise a tech-job count doesn't; this is the knob if shards still straggle.
CURRENT_WEIGHT = 0.5
FALLBACK_SECONDS = 5.0  # last resort: no measurement anywhere, not even for the ATS


def key_for(board: ScrapableBoard | str) -> str:
    """This Board's key in the ledger: its identity exactly as its scraper cases it (ADR-0192).

    A key passes through unchanged. The same key as :func:`headstart.board_priority.key_for`
    (ADR-0096), so one key reads both ledgers. Not case-folded: the file holds 1,956 groups of
    case-variant keys (HF state, 2026-09-24), each its own row that a folded load would merge.
    """
    return board if isinstance(board, str) else board.identity


@dataclass(frozen=True, slots=True)
class BoardCost:
    seconds: float  # EWMA of measured scrape wall time
    # Fresh postings the last **complete** scrape returned, or None when no complete scrape has
    # ever measured this Board. Not "all postings": `harvest` counts ids not already seen this
    # shard, so a Board whose every id was a duplicate records 0 after a healthy read.
    #
    # None is not decoration. This was `int`, and a 0 meant four different things — a real empty
    # Board, a raise, a first-ever budget kill, and a full dedupe. That was harmless while the
    # field was diagnostic; ADR-0145 made it a control input for the value gate, and a guard
    # cannot rest on a value with four meanings. The same empty-CSV-field idea the liveness
    # ledger already uses for an unknown count.
    jobs: int | None
    # ISO date of the last run that *looked at* this Board — which is what `_GATE_RECHECK_DAYS`
    # wants, since a failed look is still a look. Note it no longer dates `jobs`: an errored or
    # unfinished run refreshes this and the seconds while carrying the count forward, so a row can
    # pair today's date with a count from days ago. That is the intended trade — a stale count is
    # better than a 0 that means "we never found out" — but it means this date must not be read as
    # the age of the yield.
    updated_at: str


def load(path: str | Path) -> dict[str, BoardCost]:
    """The ledger as {board: BoardCost}; {} when the file doesn't exist yet.

    The key is read verbatim, as :func:`headstart.board_priority.load` reads its own: since
    ADR-0096 the file is written under ``board_identity`` and every row on HF is now that shape,
    so there is nothing left to normalise. Until 2026-09-09 a read-time shim re-keyed legacy
    ``{ats}:{slug}`` rows here and collapsed a Board carried under both spellings; the ledger
    self-migrated on the first run after that ADR shipped, and its own trigger
    (``update_ledgers cost``'s legacy-key count) read 0 against the live file before this went.

    So a repeated key is now last-row-wins rather than newest-wins, and that is not a semantics
    worth defending: :func:`save` writes from a dict, so the only writer cannot emit one, and the
    live ledger holds none. The collapse existed to merge a Board carried under *two spellings*,
    which is a state only the migration could produce.
    """
    path = Path(path)
    if not path.exists():
        return {}
    rows: dict[str, BoardCost] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["board"]] = BoardCost(
                seconds=float(row["seconds"]),
                jobs=int(row["jobs"]) if row["jobs"] not in ("", None) else None,
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
            writer.writerow(
                [
                    board,
                    f"{c.seconds:.3f}",
                    "" if c.jobs is None else c.jobs,
                    c.updated_at,
                ]
            )


def read_shard_rows(path: str | Path) -> dict[str, ShardCost]:
    """One shard's ``board_cost.csv`` as {board: ShardCost}.

    Tolerates a truncated final line: a shard killed mid-write by its time budget can leave one,
    and dropping just that row is strictly better than losing the shard's whole measurement set.
    A fragment written before the ``unfinished`` column existed reads as all-measured, which is
    what it was — the column is absent, not false. ``errored``, added later, needs the **same**
    schema-aware guard for a sharper reason: a row torn after ``unfinished`` but before ``errored``
    would otherwise read as a complete scrape that found nothing, which is exactly the finding
    ADR-0145's veto acts on — a 14-day exclusion handed to a Board that merely died mid-write.
    Absence of the column across the whole file means "written before it existed"; absence in one
    row of a file that has it means "torn".
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
        fields = reader.fieldnames or ()
        has_flag = "unfinished" in fields
        has_errored = "errored" in fields
        for row in reader:
            try:
                flag = row.get("unfinished")
                errored = row.get("errored")
                if (has_flag and flag is None) or (has_errored and errored is None):
                    continue  # torn tail row
                out[row["board"]] = ShardCost(
                    seconds=float(row["seconds"]),
                    jobs=int(row["jobs"]),
                    unfinished=bool(int(flag or 0)),
                    errored=bool(int(errored or 0)),
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

    **An errored Board is the same case and was not being treated as one.** Its seconds are a real
    measurement — deliberately, so a Board that burns an hour before failing is priced for it — but
    `harvest` records ``n_fresh`` as 0 whatever the outcome, so its ``jobs`` says "we never found
    out", not "there was nothing". That 0 used to be written straight over the last good count. It
    now carries the previous value exactly as the unfinished branch does, and where there is no
    previous value it writes **None**: a Board whose only measurement failed has no known yield,
    and saying so is the whole reason ADR-0145's veto can be trusted.
    """
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    rows = dict(prev)
    for board, now in measured.items():
        if (
            now.seconds <= 0
        ):  # never measured (or a clock artifact) — don't poison the EWMA
            continue
        before = prev.get(board)
        # Neither an unfinished nor an errored run learned this Board's yield, so neither may
        # overwrite a count that a complete run did learn. They differ only in how the SECONDS
        # are treated: a kill proves a floor, a failure is a real elapsed measurement.
        known_jobs = before.jobs if before else None
        if now.unfinished:
            floor = max(now.seconds, before.seconds) if before else now.seconds
            rows[board] = BoardCost(seconds=floor, jobs=known_jobs, updated_at=today)
            continue
        blended = (
            now.seconds
            if before is None
            else current_weight * now.seconds + (1 - current_weight) * before.seconds
        )
        rows[board] = BoardCost(
            seconds=blended,
            jobs=known_jobs if now.errored else now.jobs,
            updated_at=today,
        )
    return rows


def ats_medians(rows: Mapping[str, BoardCost]) -> dict[str, float]:
    """Median measured seconds per ATS — the fallback for a Board with no history of its own.

    Since ADR-0096 a cost key is ``board_identity``, the same one the priority ledger uses, so
    :func:`~headstart.board_identity.ats_of` reads the ATS half of either interchangeably. They
    were not before, and the cost of getting it wrong is on record: ADR-0059 found a stale
    "matches corpus.board_of" comment of exactly this shape, and of the 13,402 Boards whose keys
    could not match, the 4,611 holding a priority row were scored 0.0. The ATS half is unaffected
    either way — it is the part both spellings share.
    """
    by_ats: dict[str, list[float]] = {}
    for board, c in rows.items():
        by_ats.setdefault(ats_of(board), []).append(c.seconds)
    return {ats: median(vals) for ats, vals in by_ats.items() if vals}


def costs_for(
    board_keys: Iterable[str],
    rows: Mapping[str, BoardCost],
    *,
    fallback: float = FALLBACK_SECONDS,
) -> list[float]:
    """Expected seconds for each ``board_identity`` key, best available estimate per Board.

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
            out.append(medians.get(ats_of(key), overall) or fallback)
    return out
