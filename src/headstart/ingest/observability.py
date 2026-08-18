"""What a pipeline run tells you about itself, beyond the raw log lines.

Three seams, each closing a gap that made a real run undiagnosable:

**Run context.** GitHub prefixes every raw log line with an ISO timestamp, so the missing
correlation is not the date — it is *which* run, attempt and shard a log belongs to once it
is off the Actions page. :func:`context` prints that once per stage.

**Step summary.** ``$GITHUB_STEP_SUMMARY`` was unused, so answering "what did this run
actually do?" meant opening ~20 job logs across five stages. :func:`summary` appends
markdown that GitHub renders on the run page; every job's contribution lands there together.
It no-ops off CI, so local runs are unaffected.

**Shard report.** A fan-out stage's numbers die with its runner unless they ride the
fragment artifact the stage already uploads. :func:`write_shard` drops one JSON beside the
fragment; the joining stage reads them back with :func:`read_shards` and can then state
per-shard facts — predicted vs actual, retries, error classes — that no single job can see.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from headstart import log

_log = log.get(__name__)

_SHARD_REPORT = "_shard_report.json"


def context(stage: str, **extra: Any) -> None:
    """One line naming the run this log belongs to. Silent off CI, where it is noise.

    No bracketed prefix of its own: ADR-0039 fixes one line format whose only tag is the
    module's name, which the formatter already supplies. ``stage`` rides as a field.
    """
    run = os.environ.get("GITHUB_RUN_ID")
    if not run:
        return
    bits = {
        "stage": stage,
        "run": run,
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "sha": (os.environ.get("GITHUB_SHA") or "")[:7],
        **{k: v for k, v in extra.items() if v is not None},
    }
    _log.info(" ".join(f"{k}={v}" for k, v in bits.items()))


def summary(title: str, lines: list[str]) -> None:
    """Append a section to the run's step summary — the run-level view that did not exist.

    Failure to write is swallowed deliberately: a summary is an observability nicety, and a
    full disk or a read-only path must never be the thing that fails a scrape."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    body = f"### {title}\n\n" + "\n".join(lines) + "\n\n"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
    except OSError as exc:
        _log.warning(f"could not write the step summary: {exc}")


def write_shard(outdir: Path, **fields: Any) -> None:
    """Record this shard's own numbers beside its fragment, so the join can aggregate them.

    Same swallow-on-failure reasoning as :func:`summary`, and for a stronger reason here: this
    runs in the shutdown path of a shard that may already be dying on its time budget.
    """
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / _SHARD_REPORT).write_text(
            json.dumps(fields, indent=1, sort_keys=True), encoding="utf-8"
        )
    except OSError as exc:
        _log.warning(f"could not write the shard report: {exc}")


def read_shards(fragments: Path) -> list[dict]:
    """Every shard report under ``fragments``, newest-run-first order not guaranteed.

    A missing or corrupt report is skipped with a warning rather than raising: the join's job
    is to union job data, and it must not die because a shard's telemetry did."""
    out: list[dict] = []
    for path in sorted(fragments.glob(f"*/{_SHARD_REPORT}")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(f"unreadable shard report {path}: {exc}")
    return out


def named_sample(items: list[str], cap: int = 10) -> str:
    """``a, b, c, +N more`` — a warning that names what it is about, without becoming a dump.

    A count sends the reader to diff two artifacts to learn *which* Board a run lost; a full
    list of several hundred is skipped. Every caller wants the same compromise, so they share
    one, and they agree on the cap by sharing its default.
    """
    shown = ", ".join(items[:cap])
    rest = len(items) - cap
    return shown + (f", +{rest} more" if rest > 0 else "")


def percentiles(values: list[float]) -> dict[str, float]:
    """p50/p90/p99/max — the shape that separates a sum-bound stage from a floor-bound one.

    A mean cannot: 1,330 boards averaging 1.8s alongside one board at 2,237s reads as a
    healthy shard right up until it owns the run's critical path. Nearest-rank, no
    interpolation, so every number returned is a board that really took that long.
    """
    if not values:
        return {}
    ordered = sorted(values)
    at = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]
    return {
        "p50": round(at(0.50), 1),
        "p90": round(at(0.90), 1),
        "p99": round(at(0.99), 1),
        "max": round(ordered[-1], 1),
    }
