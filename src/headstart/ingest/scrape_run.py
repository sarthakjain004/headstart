#!/usr/bin/env python3
"""Bounded nightly harvest for the CI pipeline (ADR-0020, ADR-0022) — a priority-first slice.

Builds the scrape list straight from the committed liveness ledger (``config.load_active_companies``
with ``min_jobs=0``, so a board that dropped to zero postings is still scraped and its index rows
evict), then orders it by the board-priority ledger: boards with tech-job history first (highest
score first, so a time-budget-truncated run still covers the top boards), with an exploration tail
of randomly rotated unscored boards so discovery never starves. No priority ledger yet → the old
behavior, a pure shuffle. Capped at ``--max-boards``; jobs stream to ``data/jobs/{ats}.jsonl`` via
``pipeline.scrape_all``.

Each run truncates the jsonl — the output is *this run's snapshot*, which is exactly what
``index sync`` wants: eviction is scoped to the Boards present in the snapshot, so a partial
harvest never touches the Boards it skipped (ADR-0014).

Run:  python -m headstart.ingest.scrape_run --max-boards 8000
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import signal
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from headstart import http, log
from headstart.board_priority import load_scores, pick_boards
from headstart.config import CompanyRef, board_identity, load_active_companies
from headstart.harvest import scrape_all
from headstart.ingest import HELD_DETAILS_PATH, REPO_ROOT, observability

_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_JOBS_DIR = REPO_ROOT / "data" / "jobs"
_PRIORITY = REPO_ROOT / "data" / "state" / "board_priority.csv"

_log = log.get(__name__, __spec__)


def _read_assignment(path: Path) -> list[CompanyRef]:
    """A planner-built board list (JSONL of ``{ats, slug, name}``) — the shard's exact scope."""
    companies: list[CompanyRef] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            companies.append(
                CompanyRef(ats=r["ats"], slug=r["slug"], name=r.get("name"))
            )
    return companies


def _read_have_details(path: Path) -> set[str] | None:
    """Job ids whose per-job detail we already hold, shipped beside the board list (ADR-0048).

    Absent, ``None`` — every detail is fetched, which is the pre-ADR-0048 behaviour and the right
    default whenever the planner could not publish the list (a first run, or an embed store that
    has not merged yet). Never a partial read: a truncated file would silently re-fetch details
    for the ids past the tear, which is only a cost, not a correctness problem."""
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


_SLOW_BOARD_S = 120.0  # ~10x a p90 board; anything this slow is straggler material

# What `main` returns instead of 0 when the time budget ended the shard. The shard still
# *succeeded* — banking a partial is the designed outcome, and the step must stay green — so
# `__main__` turns this into exit 0. It exists only so the entrypoint knows to leave without
# waiting on the pool's atexit join. Any other value is a real failure and exits as itself.
_BUDGET_KILLED = 100


class _Progress:
    """What the shard has done so far, kept outside ``scrape_all`` on purpose.

    ``scrape_all`` returns its tally only on a clean finish, so a shard killed by the CI time
    budget used to report nothing at all — no board count, no error summary, no sign of what
    was left undone. Exactly the runs worth diagnosing produced the least evidence. Feeding
    the per-board callback into this instead means the numbers already exist when SIGTERM
    arrives, and the shutdown path only has to print them.
    """

    def __init__(self, assigned: int) -> None:
        self.assigned = assigned
        self.seconds: list[float] = []
        self.errors: dict[str, str] = {}
        # Boards that returned a short list without raising (ADR-0053). Held here, like errors,
        # so the numbers already exist when SIGTERM lands and the shutdown path only prints them.
        self.truncated: dict[str, str] = {}
        # Boards that completed without raising — including those with zero jobs. Zero-job
        # success is invisible everywhere else (no lines in the corpus, no error in the report),
        # yet it is exactly the evidence that clears a Board's ADR-0058 gone-streak: alive and
        # empty is not gone.
        self.boards_ok: list[str] = []
        self.jobs = 0

    def on_board(
        self,
        key: str,
        jobs: int,
        error: str | None,
        seconds: float,
        truncated: str | None = None,
    ) -> None:
        self.seconds.append(seconds)
        self.jobs += jobs
        if truncated is not None:
            self.truncated[key] = truncated
        if error is not None:
            self.errors[key] = error
            _log.info(f"{key} failed after {seconds:.0f}s: {error}")
            return
        self.boards_ok.append(key)
        if seconds >= _SLOW_BOARD_S:
            _log.info(f"slow board {key}: {jobs} jobs in {seconds:.0f}s")
        else:
            _log.debug(f"{key}: {jobs} jobs in {seconds:.1f}s")

    @property
    def done(self) -> int:
        return len(self.seconds)

    @property
    def undone(self) -> int:
        """Boards the shard never got to — 0 on a clean finish, the deferred work otherwise."""
        return max(0, self.assigned - self.done)


def _ats_mix(companies: list[CompanyRef], top: int = 4) -> str:
    """The shard's composition, so a heavy shard explains itself without fetching its
    assignment file — the ATS mix is what makes one shard 3x another."""
    counts = Counter(c.ats for c in companies)
    ranked = counts.most_common()
    detail = ", ".join(f"{ats} {n}" for ats, n in ranked[:top])
    return detail + (f", +{len(ranked) - top} more" if len(ranked) > top else "")


def _shard_id(assignment: str | None) -> str | None:
    """The shard number from its assignment filename, so a report says which shard it is."""
    return Path(assignment).stem.rsplit("-", 1)[-1] if assignment else None


def _plan_minutes(assignment: str | None, field: str) -> float | None:
    """This shard's entry in one of the plan's per-shard minute lists.

    The plan ships two, and they answer different questions: ``per_shard_minutes`` is the
    predicted wall clock (what the shard should take) and ``per_shard_serial_minutes`` is the
    packed sum (what its Boards cost run end to end). Reporting both is what lets the join
    measure the fan-out's speedup against the *serial* figure rather than against the
    prediction, which is derived from that speedup and would chase its own tail (ADR-0054).

    Without any of this nothing ever compares prediction to outcome, and a cost model can drift
    by a factor of three in plain sight (it has: ~109 min predicted vs ~40 actual).
    """
    if not assignment:
        return None
    path = Path(assignment)
    shard = path.stem.rsplit("-", 1)[-1]
    plan = path.parent / "plan.json"
    try:
        minutes = json.loads(plan.read_text(encoding="utf-8"))[field]
        return float(minutes[int(shard)])
    except (OSError, json.JSONDecodeError, KeyError, IndexError, ValueError):
        return None  # an older plan, or a non-shard run: absence is not an error


def _error_summary(errors: dict[str, str]) -> str:
    """Group board errors ("ats:slug" -> "ExcType: message") by exception type x ATS.

    Renders types sorted by count desc as ``{n} {ExcType} ({ats1} n1, {ats2} n2, {ats3} n3,
    +k more)`` (top 3 ATSes), joined by "; "."""
    by_type: dict[str, Counter] = defaultdict(Counter)
    for key, message in errors.items():
        by_type[message.split(":", 1)[0]][key.split(":", 1)[0]] += 1
    parts = []
    for exc_type, atses in sorted(
        by_type.items(), key=lambda item: (-sum(item[1].values()), item[0])
    ):
        ranked = sorted(atses.items(), key=lambda item: (-item[1], item[0]))
        detail = ", ".join(f"{ats} {n}" for ats, n in ranked[:3])
        if len(ranked) > 3:
            detail += f", +{len(ranked) - 3} more"
        parts.append(f"{sum(atses.values())} {exc_type} ({detail})")
    return "; ".join(parts)


def _raise_on_term(signum: int, frame: object) -> None:
    """SIGTERM as an exception, so the shutdown path is ordinary Python and `finally` runs."""
    raise SystemExit(f"signal {signum}")


def _report(
    progress: _Progress,
    outdir: Path,
    elapsed: float,
    predicted: float | None,
    serial: float | None,
    killed: bool,
    shard: str | None = None,
) -> None:
    """Everything this shard learned, on every exit path — clean finish or time budget.

    Runs in a `finally`, so it must not raise: the shard's fragment is already on disk and
    reaching the join matters more than its telemetry.
    """
    spread = observability.percentiles(progress.seconds)
    retries = http.retry_stats()
    actual_min = elapsed / 60
    if killed:
        _log.warning(
            f"time budget reached after {actual_min:.1f} min — banking a partial fragment; "
            f"{progress.done}/{progress.assigned} boards done, {progress.undone} deferred "
            "to the next run"
        )
    if progress.errors:
        _log.warning(
            f"{len(progress.errors)} board errors: {_error_summary(progress.errors)}"
        )
    if retries:
        _log.info(
            "retries: "
            + ", ".join(f"{why} {n}" for why, n in sorted(retries.items()))
            + f" (total {sum(retries.values())})"
        )
    # Which ATSes cost this shard its Origin budget (ADR-0063). Reported for the same reason the
    # retry classes are: without it, a shard that spent its spare egress logs exactly like one that
    # never needed it, and whether the fallback is firing — or firing far more than expected — is
    # the only run-over-run signal this feature has.
    walled = http.walled_groups()
    if walled:
        _log.warning(f"spare egress spent on: {', '.join(sorted(walled))}")
    ratio = (
        f" | predicted {predicted:.1f} min, actual/predicted {actual_min / predicted:.2f}x"
        if predicted
        else ""
    )
    _log.info(
        f"done: {progress.jobs} jobs from {progress.done} boards in {elapsed:0.0f}s "
        f"({len(progress.errors)} board errors) | board seconds {spread}{ratio}"
    )
    observability.write_shard(
        outdir,
        shard=shard,
        assigned=progress.assigned,
        done=progress.done,
        undone=progress.undone,
        jobs=progress.jobs,
        seconds=round(elapsed, 1),
        predicted_minutes=predicted,
        serial_minutes=serial,
        killed_by_budget=killed,
        board_seconds=spread,
        retries=dict(retries),
        # the full map, not the top-3 digest the log line carries: the join can only
        # aggregate error classes across shards if the classes survive the runner
        errors=progress.errors,
        truncated=progress.truncated,
        # every Board that completed without raising, zero-job ones included — the evidence
        # that clears an ADR-0058 gone-streak, which neither the corpus (no lines) nor the
        # error map (no entry) can carry
        boards_ok=progress.boards_ok,
    )


def main() -> int:
    log.setup()
    observability.context("scrape")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-boards",
        type=int,
        default=8000,
        help="boards to scrape this run (0 = all live boards)",
    )
    ap.add_argument(
        "--assignment",
        help="scrape a planner-built board list (JSONL of {ats,slug,name}) instead of "
        "selecting a slice — the scrape-shard mode (ADR-0026)",
    )
    ap.add_argument(
        "--outdir",
        default=str(_JOBS_DIR),
        help="output dir (default: data/jobs; a scrape shard writes its own fragment)",
    )
    args = ap.parse_args()

    have_details: set[str] | None = None
    if (
        args.assignment
    ):  # ADR-0026 scrape-shard mode — the planner already selected these boards
        companies = _read_assignment(Path(args.assignment))
        _log.info(f"harvest: {len(companies)} boards from {args.assignment} (shard)")
        have_details = _read_have_details(
            Path(args.assignment).parent / HELD_DETAILS_PATH.name
        )
        _log.info(
            f"detail skip-list: {len(have_details):,} Job details already held"
            if have_details is not None
            else "detail skip-list: absent — every detail will be fetched"
        )
    else:
        companies = load_active_companies(_LEDGER, min_jobs=0)
        scores = load_scores(_PRIORITY)
        companies = pick_boards(companies, scores, args.max_boards)
        priority = sum(1 for c in companies if scores.get(board_identity(c), 0.0) > 0.0)
        _log.info(
            f"harvest: {len(companies)} boards this run "
            f"({priority} priority + {len(companies) - priority} exploration)"
        )

    outdir = Path(args.outdir)
    shard = _shard_id(args.assignment)
    predicted = _plan_minutes(args.assignment, "per_shard_minutes")
    serial = _plan_minutes(args.assignment, "per_shard_serial_minutes")
    _log.info(f"shard mix: {_ats_mix(companies)}")
    if predicted is not None:
        _log.info(f"planner predicted ~{predicted:.1f} min for this shard")

    progress = _Progress(len(companies))
    http.reset_retry_stats()
    # `timeout` sends SIGTERM, whose default disposition kills the process outright — which is
    # why a budget-killed shard has never reported anything. Turning it into SystemExit lets
    # the `finally` below run, so the shard still says what it did and what it left.
    signal.signal(signal.SIGTERM, _raise_on_term)

    start = time.monotonic()
    killed = False
    try:
        scrape_all(
            companies,
            jobs_dir=outdir,
            progress_every=200,
            on_board=progress.on_board,
            have_details=have_details,
        )
    except SystemExit:
        killed = True
    finally:
        _report(
            progress, outdir, time.monotonic() - start, predicted, serial, killed, shard
        )
    return _BUDGET_KILLED if killed else 0


def _exit_without_joining_stragglers() -> None:
    """Leave the process now, without waiting on threads that are still fetching.

    Everything durable is already written — the corpus is flushed per Board, and `_report` ran
    in the `finally` above. What remains is a straggler thread parked on a socket, and
    `ThreadPoolExecutor` registers an atexit hook that joins its threads, so an ordinary return
    hands that straggler the process again and lets it burn the 6 min of slack to the step
    timeout. That is what killed three shards on 2026-08-13. `os._exit` skips the join.

    Called from `__main__` rather than `main`, so `main` keeps returning its status and stays
    testable — a function that never returns cannot be asserted on.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    status = main()
    if status == _BUDGET_KILLED:
        _exit_without_joining_stragglers()
    raise SystemExit(status)
