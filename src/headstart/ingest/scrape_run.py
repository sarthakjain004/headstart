#!/usr/bin/env python3
"""Bounded nightly harvest for the CI pipeline (ADR-0020, ADR-0022) — a priority-first slice.

Builds the scrape list straight from the committed liveness ledger (``scrapable_boards.load``
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
from collections import Counter
from pathlib import Path

from headstart import (
    board_priority,
    fanout_stats,
    http,
    log,
    scrapable_boards,
    spare_egress,
)
from headstart.board_priority import load_scores, pick_boards
from headstart.config import CompanyRef
from headstart.harvest import scrape_all
from headstart.ingest import HELD_DETAILS_PATH, REPO_ROOT, observability, shard_plan

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
        self.observations: dict[str, dict] = {}
        self.jobs = 0

    def on_observation(self, key: str, fields: dict) -> None:
        if fields:
            self.observations[key] = fields

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
        elif not jobs:
            # INFO, not DEBUG: a clean empty Board is the one outcome whose rows ADR-0083 evicts
            # two scrapes later, and at DEBUG CI would carry no record of which Board it was.
            _log.info(f"{key}: 0 jobs in {seconds:.1f}s (scraped clean, no postings)")
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


class _BudgetKill(SystemExit):
    """The budget's SIGTERM, told apart from any other exit: only this one is a kill. A shard
    that crashed used to read as a clean finish in its own summary, because every `SystemExit`
    counted as the budget and every other exception fell through to "finished"."""


def _raise_on_term(signum: int, frame: object) -> None:
    """SIGTERM as an exception, so the shutdown path is ordinary Python and `finally` runs."""
    raise _BudgetKill(f"signal {signum}")


def _report(
    progress: _Progress,
    outdir: Path,
    elapsed: float,
    predicted: float | None,
    serial: float | None,
    killed: bool,
    shard: str | None = None,
    deferred: list[str] | None = None,
    aborted: str | None = None,
) -> None:
    """Everything this shard learned, on every exit path — clean finish, time budget, or an
    abort (``aborted`` names the exception that ended it).

    Runs in a `finally`, so it must not raise: the shard's fragment is already on disk and
    reaching the join matters more than its telemetry.
    """
    deferred = deferred or []
    # Defaults the shard report falls back to if the telemetry below raises.
    spread: dict[str, float] = {}
    retries: dict[str, int] = {}
    try:
        spread = observability.percentiles(progress.seconds)
        retries = http.retry_stats()
        actual_min = elapsed / 60
        # INFO, not WARNING: each of these three fires once per *shard*, so fifteen shards would
        # spend up to 45 of the run's 50 annotations on them (ADR-0039). The join already warns
        # run-level.
        if killed:
            _log.info(
                f"time budget reached after {actual_min:.1f} min — banking a partial fragment; "
                f"{progress.done}/{progress.assigned} boards done, {progress.undone} deferred "
                "to the next run"
            )
            # Which Boards, not just how many. A count sends the next person to diff the
            # assignment artifact against the fragment's cost rows to learn which Board ate the
            # shard — that is how `workday:dollartree/dollartreeus` was found on 2026-08-18, and
            # it should have been one log line. Capped: a shard killed early defers hundreds and
            # the list is then noise.
            if deferred:
                _log.info("deferred: " + log.named_sample(deferred))
        if progress.errors:
            _log.info(
                f"{len(progress.errors)} board errors: "
                f"{observability.error_summary(progress.errors)}"
            )
        if retries:
            _log.info(
                "retries: "
                + ", ".join(f"{why} {n}" for why, n in sorted(retries.items()))
                + f" (total {sum(retries.values())})"
            )
        # Kept off the pinned `retries:` line: a request that gave up is not one more retry, and
        # folding it in would inflate that line's total (scripts/runlog/fanout_retries.py).
        exhausted = http.exhausted_stats()
        if exhausted:
            _log.info(
                "retry budget exhausted: "
                + ", ".join(f"{why} {n}" for why, n in sorted(exhausted.items()))
            )
        # Which ATSes cost this shard its Origin budget, and what the spare egress recovered for
        # them (ADR-0063). Reported for the same reason the retry classes are: without it a shard
        # that routed everything successfully and one whose proxy carried nothing log
        # identically, and "did the fallback work?" is the only question this feature has.
        egress = spare_egress.report()
        # What each fan-out width actually bought. The ADR-0078 clamp already runs some Boards at
        # the ceiling and some at 12, so this is the only place the two are comparable — and
        # `stream_width`'s own docstring says 12 has never been re-measured.
        widths = fanout_stats.report()
        health = observability.ScrapeHealth.from_reports(
            [
                observability.ShardReport(
                    shard=shard,
                    boards_ok=progress.boards_ok,
                    errors=progress.errors,
                    truncated=progress.truncated,
                    observations=progress.observations,
                )
            ],
            # INFO here: every shard would warn for one systemic fault; the join warns once
            quiet=True,
        )
        coverage = health.coverage_line()
        losses = health.loss_lines()
        # Both are routine per-run measurement, so both are info. They were warnings only to force a
        # GitHub annotation, which is a quota and not a level: 10 per step, 50 per job, and fifteen
        # shards each claiming several of them starve the errors the annotations exist for. The step
        # summary below is the surface that was actually wanted — uncapped and on the run page.
        for line in egress + widths:
            _log.info(line)
        if coverage:
            _log.info("Board coverage by ATS: " + coverage)
            _log.info(health.verdict_line())
        for line in losses:
            _log.info(line)
        ratio = (
            f" | predicted {predicted:.1f} min, actual/predicted {actual_min / predicted:.2f}x"
            if predicted
            else ""
        )
        _log.info(
            f"done: {progress.jobs} jobs from {progress.done} boards in {elapsed:0.0f}s "
            f"({len(progress.errors)} board errors) | board seconds {spread}{ratio}"
        )
        observability.summary(
            f"Scrape shard {shard}" if shard else "Scrape",
            [
                (
                    f"- **{progress.jobs:,}** jobs from {progress.done}/{progress.assigned} "
                    f"boards in {actual_min:.1f} min{ratio}"
                ),
                f"- {len(progress.errors)} board errors"
                + (
                    f": {observability.error_summary(progress.errors)}"
                    if progress.errors
                    else ""
                ),
                f"- **{len(deferred)} deferred** (time budget reached)"
                if killed
                else f"- **aborted** by {aborted}"
                if aborted
                else "- finished within the time budget",
                f"- board seconds {spread}",
            ]
            + (
                [
                    f"- **{health.verdict_line()}**",
                    f"- Board coverage by ATS: {coverage}",
                ]
                if coverage
                else []
            )
            + [f"- {line}" for line in losses + egress + widths],
        )
    except Exception:  # noqa: BLE001 - telemetry must never cost the shard report
        # INFO, not WARNING or FirstOnly: this is once per shard *process*, so either would
        # annotate every shard for one bug. The traceback still names the line, and the shard
        # report below must be written regardless — it is what the join reads.
        _log.info(
            f"shard {shard} telemetry failed; writing its shard report anyway",
            exc_info=True,
        )
    observability.write_shard(
        outdir,
        observability.ShardReport(
            shard=shard,
            assigned=progress.assigned,
            done=progress.done,
            undone=progress.undone,
            jobs=progress.jobs,
            seconds=round(elapsed, 1),
            predicted_minutes=predicted,
            serial_minutes=serial,
            killed_by_budget=killed,
            # The Boards this shard never finished, named. `undone` counts them; the join can
            # only say *which* Board a run keeps losing if the names survive the runner.
            deferred=deferred,
            board_seconds=spread,
            retries=dict(retries),
            # The addresses this shard actually egressed from, not just how often it rotated.
            # Only a comparison of addresses can answer whether rotation works — ADR-0067 first
            # measured a genuinely different IP ~11 times in 30; ADR-0081 corrected that on 150
            # real shard-runs to 11,007 distinct IPs across 12,702 rotations — and only the join
            # can see the cross-shard picture that mattered for either measurement.
            egress_ips=dict(spare_egress.egress_ips()),
            # the full map, not the top-3 digest the log line carries: the join can only
            # aggregate error classes across shards if the classes survive the runner
            errors=progress.errors,
            truncated=progress.truncated,
            # every Board that completed without raising, zero-job ones included — the evidence
            # that clears an ADR-0058 gone-streak, which neither the corpus (no lines) nor the
            # error map (no entry) can carry
            boards_ok=progress.boards_ok,
            observations=progress.observations,
        ),
    )


def main() -> int:
    log.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-boards",
        type=int,
        default=8000,
        help="boards to scrape this run (0 = every Scrapable Board)",
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
    # After parsing, not before it: the shard is the only key that tells fifteen concurrent
    # producers apart once their logs are merged, and it is only knowable from the assignment.
    shard = shard_plan.shard_index(args.assignment)
    log.context("scrape_run", shard=shard)

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
        companies = scrapable_boards.load(_LEDGER, min_jobs=0)
        scores = load_scores(_PRIORITY)
        companies = pick_boards(companies, scores, args.max_boards)
        priority = sum(
            1 for c in companies if scores.get(board_priority.key_for(c), 0.0) > 0.0
        )
        _log.info(
            f"harvest: {len(companies)} boards this run "
            f"({priority} priority + {len(companies) - priority} exploration)"
        )

    outdir = Path(args.outdir)
    plan = (
        shard_plan.ScrapePlan.from_json(Path(args.assignment).parent / "plan.json")
        if args.assignment
        else None
    )
    if args.assignment and plan is None:
        _log.info(
            f"no prediction: {Path(args.assignment).parent / 'plan.json'} missing or unreadable"
        )
    predicted = plan.predicted_minutes(shard) if plan else None
    if plan and predicted is None:
        _log.info(
            f"no prediction for shard {shard} in {Path(args.assignment).parent / 'plan.json'} "
            "(cold start or index out of range)"
        )
    serial = plan.serial_minutes(shard) if plan else None
    _log.info(f"shard mix: {_ats_mix(companies)}")
    if predicted is not None:
        _log.info(f"planner predicted ~{predicted:.1f} min for this shard")

    progress = _Progress(len(companies))
    http.reset_retry_stats()
    fanout_stats.reset()
    # `timeout` sends SIGTERM, whose default disposition kills the process outright — which is
    # why a budget-killed shard has never reported anything. Turning it into SystemExit lets
    # the `finally` below run, so the shard still says what it did and what it left.
    signal.signal(signal.SIGTERM, _raise_on_term)

    start = time.monotonic()
    killed = False
    aborted = None
    try:
        scrape_all(
            companies,
            jobs_dir=outdir,
            progress_every=200,
            on_board=progress.on_board,
            on_observation=progress.on_observation,
            have_details=have_details,
        )
    except _BudgetKill:
        killed = True
    except BaseException as exc:
        # Fatal and once per shard, so an annotation is what it should cost. Re-raised: the step
        # must go red, and the `finally` still banks what the shard did before it died.
        aborted = f"{type(exc).__name__}: {exc}"
        _log.error(f"shard {shard} aborted by {aborted}")
        raise
    finally:
        # Assignment minus everything that reported back — the Boards this shard never finished,
        # in the order the planner listed them (priority-desc), so the first name is the most
        # expensive thing lost. `progress` holds one entry per Board that completed, success or
        # error alike, which makes the difference exactly the deferred set.
        seen = set(progress.boards_ok) | set(progress.errors)
        deferred = [k for c in companies if (k := f"{c.ats}:{c.slug}") not in seen]
        _report(
            progress,
            outdir,
            time.monotonic() - start,
            predicted,
            serial,
            killed,
            shard,
            deferred,
            aborted,
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
