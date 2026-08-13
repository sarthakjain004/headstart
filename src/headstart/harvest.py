"""Run all configured scrapers and assemble the combined job feed.

The shared scrape engine — ``scrape_all`` (thread pool + per-Board timing + the crash-safe
``JobWriter``) and the curated-feed builders. Two callers: ``headstart.ingest.scrape_run``, the
pipeline's stage-2 shard runner, and ``python -m headstart``, the curated ``docs/jobs.json`` feed.

Named ``harvest`` rather than ``pipeline`` (ADR-0028): "pipeline" means the 5-stage CI run
everywhere else in this repo — ``.github/workflows/pipeline.yml``, ``headstart.ingest``,
ADR-0025/0026/0027 — and this module is not that. It is one step *inside* it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Container
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from headstart import log
from headstart.board_cost import SHARD_HEADER, shard_row
from headstart.config import CompanyRef
from headstart.models import Job
from headstart.scrapers.registry import get_scraper

_log = log.get(__name__)

# Per-board scrape timings a shard hands to the join (ADR-0027). Undotted on purpose — see
# JobWriter.record_cost.
COST_FILENAME = "board_cost.csv"


def _default_workers() -> int:
    """Company-level thread count. The work is network-bound (``curl_cffi`` drops the GIL per
    request, so threads use every core), so we run far more threads than cores. Default to
    cores*4 capped at 64; override with ``HEADSTART_WORKERS=N`` to push it.

    NB: each detail-fetching scraper fans out its *own* bounded pool per board (8 workers,
    trakstar 4) to keep per-host concurrency polite, so peak in-flight requests is roughly
    ``workers + workers*8`` — raising this multiplies accordingly.
    """
    env = os.environ.get("HEADSTART_WORKERS")
    if env:
        return max(1, int(env))
    return min((os.cpu_count() or 8) * 4, 64)


@dataclass
class RunResult:
    errors: dict[str, str] = field(default_factory=dict)  # "ats:slug" -> message
    # Boards that returned a SHORT list without raising — kept apart from `errors` because they
    # did produce Jobs, and calling that a failure would make every log and count read wrong.
    # Both mean the same thing downstream: this Board's list is not authoritative (ADR-0053).
    truncated: dict[str, str] = field(default_factory=dict)  # "ats:slug" -> why
    unique: int = 0  # distinct job ids seen (all streamed to the .jsonl)
    boards: int = 0  # boards completed, including those that errored


class JobWriter:
    """Stream Jobs to per-ATS JSON Lines files (one full Job per line) under ``jobs_dir``.

    Each ``{jobs_dir}/{ats}.jsonl`` is appended to and flushed after every board — so a long
    scrape writes incrementally (never buffering to the end) and survives a crash with whatever
    finished already on disk. Single-threaded by contract: ``write``/``mark_done`` are only
    called from the scrape_all merge loop.

    Resume: a ``.done`` journal records each completed board key (``ats:slug``), flushed per
    board. A fresh run (``resume=False``) truncates the ``.jsonl`` files and the journal; a
    ``resume=True`` run opens them for append and exposes the already-done keys in ``done`` so
    the caller can skip those boards. Marking happens after the board's jobs are flushed, so a
    crash between write and mark merely re-scrapes that one board on the next resume (its lines
    re-emit — dedup by ``id`` downstream).
    """

    def __init__(
        self, jobs_dir: str | Path, atses: set[str], resume: bool = False
    ) -> None:
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._done_path = self._dir / ".done"
        self.done: set[str] = set()
        if resume and self._done_path.exists():
            self.done = {
                line.strip()
                for line in self._done_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        mode = "a" if resume else "w"
        # Open one handle per ATS up front so even an ATS that yields zero jobs this run leaves
        # an empty file (fresh) rather than a stale one. Append on resume, truncate on a fresh run.
        self._handles: dict[str, Any] = {
            ats: (self._dir / f"{ats}.jsonl").open(mode, encoding="utf-8")
            for ats in atses
        }
        self._done_handle = self._done_path.open(mode, encoding="utf-8")
        # Per-board scrape seconds (ADR-0027). Header only on a fresh run, so a resume appends.
        cost_path = self._dir / COST_FILENAME
        fresh_cost = mode == "w" or not cost_path.exists()
        self._cost_handle = cost_path.open(mode, encoding="utf-8")
        if fresh_cost:
            self._cost_handle.write(SHARD_HEADER)
            self._cost_handle.flush()

    def write(self, jobs: list[Job]) -> None:
        touched = set()
        for job in jobs:
            handle = self._handles.get(job.ats)
            if (
                handle is None
            ):  # an ats not in the company list — open it lazily, just in case
                handle = self._handles[job.ats] = (self._dir / f"{job.ats}.jsonl").open(
                    "a", encoding="utf-8"
                )
            handle.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")
            touched.add(handle)
        for handle in touched:
            handle.flush()

    def mark_done(self, board_key: str) -> None:
        """Record a board as completed (so a resume skips it). Call after its jobs are flushed."""
        self._done_handle.write(board_key + "\n")
        self._done_handle.flush()

    def record_cost(self, board_key: str, seconds: float, jobs: int) -> None:
        """Append this board's measured scrape seconds, flushed per board (ADR-0027).

        Same contract as :meth:`mark_done`: written as each board lands, never buffered, so a
        shard killed by its time budget still hands the join every timing it did measure. The
        filename is deliberately *not* dotted — ``actions/upload-artifact`` skips hidden files by
        default, which is why the ``.done`` journal never reaches the join and this must."""
        self._cost_handle.write(shard_row(board_key, seconds, jobs))
        self._cost_handle.flush()

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._done_handle.close()
        self._cost_handle.close()


def _emit_progress(
    done: int, total: int, unique: int, errors: int, start: float
) -> None:
    elapsed = time.monotonic() - start
    rate = done / elapsed if elapsed else 0.0
    _log.info(
        f"{done}/{total} boards | {unique} jobs | {errors} errors | "
        f"{elapsed:0.0f}s | {rate:0.1f} boards/s"
    )


def scrape_all(
    companies: list[CompanyRef],
    *,
    jobs_dir: str | Path,
    max_workers: int | None = None,
    progress_every: int = 0,
    resume: bool = False,
    on_board: Callable[[str, int, str | None, float, str | None], None] | None = None,
    have_details: Container[str] | None = None,
) -> RunResult:
    """Scrape every company concurrently, streaming Jobs to ``{jobs_dir}/{ats}.jsonl``.

    Each company runs in its own thread (the work is network-bound; ``max_workers`` defaults to
    :func:`_default_workers`). A company that errors is recorded in ``errors`` and skipped.
    Dedup is by job id on the main thread (collapsing duplicate slug forms of the same board,
    e.g. ``dollartree`` appearing under three slugs), so what's written stays deterministic.

    Jobs are never retained in memory — each board's fresh Jobs stream to ``{jobs_dir}/{ats}.jsonl``
    (full Job per line) as it completes, incremental and crash-safe (the ``.jsonl`` is the source of
    truth; :func:`build_feed` reads it back for the dashboard). ``progress_every`` > 0 prints a
    progress line to stderr every that-many boards. ``resume`` appends to the existing output and
    skips boards already recorded in its ``.done`` journal, so an interrupted harvest continues
    instead of restarting.

    ``have_details`` is the set of Job ids whose per-job detail fetch can be skipped because we
    already hold it (ADR-0048); scrapers with a detail pass consult it via ``needs_detail``. None
    means fetch every detail, which is what every caller outside the pipeline wants.

    ``on_board(key, n_new_jobs, error, seconds, truncated)``, if given, is called on the main
    thread as each board completes (``error`` is None on success; ``seconds`` is the board's
    measured scrape time, the same number the cost ledger records; ``truncated`` is None unless
    the scraper knows its list came back short, ADR-0053) — the hook for live per-board logging.
    """
    workers = max_workers if max_workers is not None else _default_workers()

    # Measured wall time per board, filled by the worker threads and read on the merge thread
    # (ADR-0027). Timed in `finally` so an errored board still records the seconds it burned — a
    # board that hangs 30s before raising costs 30s, and the packer must know that.
    elapsed: dict[str, float] = {}

    # Boards that returned a list they know is short: "ats:slug" -> why. Filled beside `elapsed`
    # rather than raised, because the partial Jobs are real and must still be written (ADR-0053).
    truncated: dict[str, str] = {}

    def run_one(company: CompanyRef) -> list[Job]:
        start = time.monotonic()
        key = f"{company.ats}:{company.slug}"
        scraper = get_scraper(
            company.ats, company.slug, company.name, have_details=have_details
        )
        try:
            return scraper.fetch()
        finally:
            elapsed[key] = time.monotonic() - start
            # Read after fetch, in `finally`: a scraper that truncated and *then* raised still
            # reported something worth carrying.
            if scraper.truncated:
                truncated[key] = scraper.truncated

    writer = JobWriter(jobs_dir, {c.ats for c in companies}, resume=resume)
    if writer.done:
        before = len(companies)
        companies = [c for c in companies if f"{c.ats}:{c.slug}" not in writer.done]
        _log.info(
            f"resume: {before - len(companies)} of {before} boards already done — "
            f"{len(companies)} left"
        )

    seen_ids: set[str] = set()
    errors: dict[str, str] = {}
    total, done = len(companies), 0
    start = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        # Submission is inside the guarded block too: at 1,300 Boards it is not instantaneous,
        # and a SIGTERM landing mid-submission would otherwise leave the executor un-shut-down,
        # whose atexit hook drains the whole queue — the exact behaviour this guards against.
        futures = {executor.submit(run_one, c): c for c in companies}
        for future in as_completed(futures):
            company = futures[future]
            done += 1
            key = f"{company.ats}:{company.slug}"
            n_fresh = 0
            try:
                jobs = future.result()
            except Exception as exc:  # noqa: BLE001 - isolate per-company failures
                errors[key] = f"{type(exc).__name__}: {exc}"
            else:
                fresh = [j for j in jobs if j.id not in seen_ids]
                seen_ids.update(j.id for j in fresh)
                writer.write(fresh)
                n_fresh = len(fresh)
            writer.mark_done(
                key
            )  # mark on completion (success or error): resume moves on
            seconds = elapsed.pop(key, 0.0)
            writer.record_cost(key, seconds, n_fresh)
            if on_board is not None:
                on_board(key, n_fresh, errors.get(key), seconds, truncated.get(key))
            if progress_every and done % progress_every == 0:
                _emit_progress(done, total, len(seen_ids), len(errors), start)
    finally:
        # Every Board is submitted up front, so `with ThreadPoolExecutor(...)` would drain the
        # whole queue on the way out — its __exit__ is shutdown(wait=True). Fine on a clean
        # finish, wrong on a time budget: the SIGTERM meant to end the shard would instead wait
        # for every remaining Board, blow the step timeout, and take the runner down before
        # anything could be reported. cancel_futures drops what has not started.
        #
        # A Board already running cannot be cancelled — you cannot kill a Python thread — so
        # `wait=True` blocked on the slowest in-flight one, and a straggler could outlast the 6
        # min of slack between the 60m budget and the 66m step timeout, taking the runner down
        # before anything was reported (ADR-0053, amended). Not waiting is safe because those
        # results are discarded either way: the loop that would have written them has already
        # exited, and `JobWriter` is written only from that loop and flushed per Board, so no
        # straggler is mid-write when we stop waiting.
        executor.shutdown(wait=False, cancel_futures=True)
        writer.close()
    return RunResult(
        errors=errors, truncated=truncated, unique=len(seen_ids), boards=done
    )


def build_feed(jobs_dir: str | Path, errors: dict[str, str]) -> dict[str, Any]:
    """Assemble the dashboard feed by reading the per-ATS ``.jsonl`` files back (deduped by id).

    The ``.jsonl`` files are the source of truth; this reverses them into the single JSON the
    dashboard consumes. ``errors`` is carried in from the run (it isn't in the ``.jsonl``). Loads
    every Job into memory, so it's for the small curated feed — the millions-scale harvest skips it.
    """
    seen: set[str] = set()
    jobs: list[dict[str, Any]] = []
    for path in sorted(Path(jobs_dir).glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                job = json.loads(line)
                if job["id"] in seen:  # resume can re-emit a board's lines
                    continue
                seen.add(job["id"])
                jobs.append(job)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(jobs),
        "errors": errors,
        "jobs": jobs,
    }


def write_feed(feed: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
