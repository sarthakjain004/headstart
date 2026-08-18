"""How much faster a scrape shard runs than the sum of its Boards' measured seconds (ADR-0054).

``scrape_plan`` packs on measured per-Board seconds (:mod:`headstart.board_cost`, ADR-0027) and
predicted the makespan as the packed **sum** — a shard's serial time. But ``harvest.scrape_all``
runs a shard's Boards through a thread pool, so the wall clock is that sum divided by whatever
concurrency the run actually achieves. Predicting serial against a parallel shard overstated every
run by ~3x (measured 2026-08-14: ~120 min predicted, ~42 min slowest shard), so the "exceeds the
60 min shard budget" warning fired on all twelve runs reviewed and no shard ever banked a partial.
A warning that is always on cannot signal the run where it is true.

The divisor cannot be the nominal worker count. Nominal is ``cores*4`` (16 on a hosted runner) but
the observed speedup is ~2.8x: per-host politeness, ATS rate limits and one straggler Board eat
most of the theoretical parallelism, and how much they eat shifts with the slice's ATS mix. So it
is **measured** — an EWMA over ``serial_minutes / wall_clock_minutes``, persisted as
``data/state/shard_speedup.csv`` and blended each run from the shard reports the join aggregates.

Two things the ratio must not be built from, both of which make it lie:

* **Not the shard's own prediction.** The obvious source, the report's ``predicted_minutes``, is
  the number this model produces. Feeding ``predicted/actual`` back in makes the estimate chase
  its own tail and settle on ``sqrt(true_speedup)`` — permanently wrong, and wrong in the
  direction that looks converged. The planner therefore ships the packed **serial** sum as its
  own field and the ratio is measured against that.
* **Not a shard that ran out of time.** A budget-killed shard's wall clock measures the budget,
  not the work, so its ratio is inflated (120 min packed, killed at 60, having done half →
  reports 2.0x against a real ~1.0x). Blending it teaches a speedup the fan-out never achieved
  and silences the budget warning in the one situation it exists for.
  :func:`ratios_from_reports` drops them on ``killed_by_budget``.

Same shape as :mod:`headstart.board_priority` and :mod:`headstart.board_cost` deliberately: an
EWMA, an ISO stamp, a missing file degrading to old behavior, riding the same HF state round-trip.
One row, not one per Board — this measures the *fan-out*, not any Board.

The prediction it feeds is ``max(slowest_Board_on_the_shard, serial / speedup)``: no concurrency
takes a shard below its slowest single Board, which is why the floor is not merely a safety clamp
but frequently the binding term (the 2026-08-14 runs floored at 30-35 min against 42 min actual,
far closer than the 120 min serial sum).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

FIELDS = ("speedup", "shards", "updated_at")
CURRENT_WEIGHT = 0.5  # EWMA weight on this run (the rest on history)
# Used until the ledger has a measurement. 1.0 reproduces the old serial prediction exactly, so a
# cold start is no worse than the behavior this replaces and never *under*-predicts on no evidence.
DEFAULT = 1.0
# A shard cannot finish faster than its Boards run concurrently allow, but it also cannot run
# *slower* than serial: a ratio under 1.0 means the sample is measuring something other than
# concurrency (a stalled host, a clock problem) and is noise, not a speedup.
MIN_RATIO = 1.0


@dataclass(frozen=True, slots=True)
class Speedup:
    """The learned fan-out speedup, and the run that last moved it.

    ``shards`` and ``updated_at`` are not read by the planner — they are there so a human reading
    the ledger can tell a one-run estimate from a settled one, the same way ``board_priority``
    carries ``last_tech_jobs`` beside its score.
    """

    ratio: float
    shards: int
    updated_at: str  # ISO date of the last run that contributed


def load(path: str | Path) -> Speedup:
    """The ledger, or a cold-start :data:`DEFAULT` when there is no usable file yet."""
    cold = Speedup(DEFAULT, 0, "")
    path = Path(path)
    if not path.exists():
        return cold
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            row = next(iter(csv.DictReader(handle)))
        return Speedup(
            max(MIN_RATIO, float(row["speedup"])),
            int(row["shards"]),
            row["updated_at"],
        )
    except (KeyError, ValueError, StopIteration, OSError):
        # A corrupt ledger must not sink the planner: the whole run depends on it, and the cost
        # of ignoring one bad file is a single run predicted the old way.
        return cold


def ratios_from_reports(reports: list[dict]) -> list[float]:
    """Per-shard ``serial / wall_clock`` from the run's shard reports.

    Skips a shard that has no serial figure (an older plan), that reports no time, and — the one
    that matters — any shard ``killed_by_budget``, whose wall clock measures the budget rather
    than the work. ``.get(...)`` throughout: a truncated report must not raise here.
    """
    ratios = []
    for report in reports:
        if report.get("killed_by_budget"):
            continue
        serial = float(report.get("serial_minutes") or 0)
        actual = float(report.get("seconds") or 0) / 60
        if serial > 0 and actual > 0:
            ratios.append(serial / actual)
    return ratios


def blend(previous: float, ratios: list[float]) -> float:
    """EWMA this run's per-shard ratios into the stored speedup.

    Averages the run's shards before blending rather than folding each in turn, so a 15-shard run
    and a 3-shard run move the estimate by the same amount.
    """
    usable = [r for r in ratios if r >= MIN_RATIO]
    if not usable:
        return previous
    current = sum(usable) / len(usable)
    return CURRENT_WEIGHT * current + (1 - CURRENT_WEIGHT) * previous


def save(path: str | Path, ratio: float, shards: int) -> None:
    """Write the one-row ledger, creating the parent directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "speedup": f"{ratio:.4f}",
                "shards": shards,
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
            }
        )


def predict_minutes(serial_minutes: float, floor_minutes: float, ratio: float) -> float:
    """Wall-clock minutes a shard holding ``serial_minutes`` of work should take.

    ``floor_minutes`` is the shard's own slowest Board — concurrency cannot divide it away, so it
    is the hard floor on that shard's makespan.
    """
    return max(floor_minutes, serial_minutes / max(ratio, MIN_RATIO))
