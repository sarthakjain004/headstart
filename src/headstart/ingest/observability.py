"""What a pipeline run tells you about itself, beyond the raw log lines.

Three seams, each closing a gap that made a real run undiagnosable. The fourth, the
``stage= run= attempt=`` correlation line, moved to :func:`headstart.log.context` when
``alerts/`` needed it too and could not import from this package; what is left here is the
artifacts a run leaves behind rather than the lines it writes.

**Step summary.** ``$GITHUB_STEP_SUMMARY`` was unused, so answering "what did this run
actually do?" meant opening ~20 job logs across five stages. :func:`summary` appends
markdown that GitHub renders on the run page; every job's contribution lands there together.
It no-ops off CI, so local runs are unaffected.

**Shard report.** A fan-out stage's numbers die with its runner unless they ride the
fragment artifact the stage already uploads. :func:`write_shard` drops one JSON beside the
fragment; the joining stage reads them back with :func:`read_shards` and can then state
per-shard facts — predicted vs actual, retries, error classes — that no single job can see.

**Error summary.** A count of failures names no cause. :func:`error_summary` groups
``{board: "ExcType: message"}`` by exception type x ATS, so one line separates throttling from
a dead host from a parse bug. It lives here rather than in either caller because both ends of
the fan-out need the same shape: a shard summarising its own errors, and the join summarising
the run's — and the run-level view is the one that turns fifteen shards each reporting "3 board
errors" into a single named failure mode.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headstart import log

_log = log.get(__name__)

_SHARD_REPORT = "_shard_report.json"
_LOSS_FIELDS = (
    "listing_pages",
    "listing_page_losses",
    "listing_fetch_calls",
    "listing_status_failures",
    "listing_request_failures",
    "detail_jobs",
    "detail_attempted",
    "detail_losses",
    "detail_http_failures",
    "detail_breaker_skips",
)


@dataclass
class ScrapeHealth:
    """One reporting contract for shard and run-level Board coverage and scrape losses."""

    coverage: dict[str, Counter[str]]
    losses: dict[str, Counter[str]]
    causes: Counter[tuple[str, str, str]]
    cause_boards: dict[tuple[str, str, str], set[str]]
    report_count: int

    @classmethod
    def from_reports(cls, reports: list[dict]) -> ScrapeHealth:
        coverage: dict[str, Counter[str]] = defaultdict(Counter)
        losses: dict[str, Counter[str]] = defaultdict(Counter)
        causes: Counter[tuple[str, str, str]] = Counter()
        cause_boards: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for report in reports:
            for key in report.get("boards_ok") or []:
                coverage[str(key).split(":", 1)[0]]["successful"] += 1
            for key in report.get("errors") or {}:
                coverage[str(key).split(":", 1)[0]]["failed"] += 1
            for key in report.get("truncated") or {}:
                coverage[str(key).split(":", 1)[0]]["partial"] += 1
            for board, observation in (report.get("observations") or {}).items():
                if not isinstance(observation, dict):
                    continue
                ats = str(board).split(":", 1)[0]
                for field in _LOSS_FIELDS:
                    losses[ats][field] += int(observation.get(field) or 0)
                for kind, field in (
                    ("listing", "listing_loss_causes"),
                    ("detail", "detail_loss_causes"),
                ):
                    for cause, count in (observation.get(field) or {}).items():
                        key = (kind, ats, str(cause))
                        causes[key] += int(count)
                        cause_boards[key].add(str(board))
        return cls(dict(coverage), dict(losses), causes, dict(cause_boards), len(reports))

    @property
    def degraded(self) -> bool:
        return any(c["failed"] or c["partial"] for c in self.coverage.values())

    def coverage_line(self) -> str:
        return "; ".join(
            f"{ats} attempted {counts['successful'] + counts['failed']}, "
            f"successful {counts['successful']}, failed {counts['failed']}, "
            f"partial {counts['partial']}"
            for ats, counts in sorted(self.coverage.items())
        )

    def verdict_line(self) -> str:
        if not self.report_count:
            return "Fresh coverage: unavailable — no shard reports arrived"
        successful = sum(c["successful"] for c in self.coverage.values())
        failed = sum(c["failed"] for c in self.coverage.values())
        partial = sum(c["partial"] for c in self.coverage.values())
        attempted = successful + failed
        verdict = "DEGRADED" if self.degraded else "healthy"
        return (
            f"Fresh coverage: {verdict} — {failed} failed and {partial} partial of "
            f"{attempted} attempted Boards"
        )

    def loss_lines(self) -> list[str]:
        lines: list[str] = []
        for ats, totals in sorted(self.losses.items()):
            if totals["listing_pages"] or totals["listing_page_losses"]:
                lines.append(
                    f"{ats} listing-page loss events: {totals['listing_page_losses']}/"
                    f"{totals['listing_pages']} pages; fetch calls "
                    f"{totals['listing_fetch_calls']}, status failures "
                    f"{totals['listing_status_failures']}, request failures "
                    f"{totals['listing_request_failures']}"
                )
            if totals["detail_jobs"] or totals["detail_losses"]:
                lines.append(
                    f"{ats} detail loss events: {totals['detail_losses']}/"
                    f"{totals['detail_jobs']} Jobs; attempted {totals['detail_attempted']}, "
                    f"HTTP failures {totals['detail_http_failures']}, circuit-breaker skips "
                    f"{totals['detail_breaker_skips']} — emitted events, not unique Jobs or "
                    "additional Board errors"
                )
            for kind in ("listing", "detail"):
                ranked = sorted(
                    (
                        (cause, count, len(self.cause_boards[(kind, ats, cause)]))
                        for (seen_kind, seen_ats, cause), count in self.causes.items()
                        if seen_kind == kind and seen_ats == ats
                    ),
                    key=lambda item: (-item[1], item[0]),
                )
                if ranked:
                    shown = "; ".join(
                        f"{cause} x{count} on {boards} Board(s)"
                        for cause, count, boards in ranked[:4]
                    )
                    if len(ranked) > 4:
                        shown += f"; +{len(ranked) - 4} more causes"
                    lines.append(f"{ats} {kind} loss causes: {shown}")
        return lines

    def to_dict(self) -> dict[str, Any]:
        causes: dict[str, dict[str, dict[str, dict[str, int]]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for (kind, ats, cause), events in self.causes.items():
            causes[kind][ats][cause] = {
                "events": events,
                "boards": len(self.cause_boards[(kind, ats, cause)]),
            }
        return {
            "available": bool(self.report_count),
            "degraded": self.degraded,
            "verdict": self.verdict_line(),
            "coverage": {ats: dict(counts) for ats, counts in self.coverage.items()},
            "losses": {ats: dict(counts) for ats, counts in self.losses.items()},
            "causes": {kind: dict(atses) for kind, atses in causes.items()},
        }


def write_scrape_health(path: Path, health: ScrapeHealth) -> None:
    """Persist the small run-level verdict so publication can report it beside its receipts."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(health.to_dict(), indent=1, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        _log.warning(f"could not write scrape health: {exc}")


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
    unreadable: list[str] = []
    for path in sorted(fragments.glob(f"*/{_SHARD_REPORT}")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            unreadable.append(f"{path.parent.name} ({type(exc).__name__})")
    if unreadable:
        # One warning for the set, not one per shard. A fan-out has ~15 shards and a WARNING
        # is an annotation under Actions, capped at 10 per step — so the per-shard form could
        # spend the join's whole budget reporting that telemetry was missing, and bury the
        # join's own errors doing it. The names still ride, via `log.named_sample`.
        _log.warning(
            f"{len(unreadable)} shard report(s) unreadable, so their telemetry is missing "
            f"from this run's totals: {log.named_sample(unreadable)}"
        )
    return out


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


def error_summary(errors: dict[str, str]) -> str:
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
