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
fragment artifact the stage already uploads. :class:`ShardReport` (ADR-0154) is the typed
shape of that JSON; :func:`write_shard` drops one beside the fragment, and the joining stage
reads them back with :func:`read_shards` and can then state per-shard facts — predicted vs
actual, retries, error classes — that no single job can see.

**Error summary.** A count of failures names no cause. :func:`error_summary` groups
``{board: "ExcType: message"}`` by exception type x ATS, so one line separates throttling from
a dead host from a parse bug. It lives here rather than in either caller because both ends of
the fan-out need the same shape: a shard summarising its own errors, and the join summarising
the run's — and the run-level view is the one that turns fifteen shards each reporting "3 board
errors" into a single named failure mode.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headstart import log
from headstart.board_identity import ats_of

_log = log.get(__name__)

# One annotation per process for each artifact write, not one per call: the same full disk or
# read-only path fails every write after the first, and `summary` runs several times per stage.
_summary_write_failed = log.FirstOnly(_log)
_scrape_health_write_failed = log.FirstOnly(_log)

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


class PreparationProgress:
    """Bounded progress for corpus preparation before encoding begins.

    Throttled by time alone. A count trigger (every 500 scanned) fired hundreds of times in
    seconds, because most of the corpus is already embedded and skips in microseconds; the
    caller's own closing line carries the final totals."""

    _EVERY_SECONDS = 10

    def __init__(self, logger) -> None:
        self._log = logger
        self._last = time.monotonic()

    def report(self, scanned: int, prepared: int, already: int, dropped: int) -> None:
        now = time.monotonic()
        if not scanned or now - self._last < self._EVERY_SECONDS:
            return
        self._last = now
        self._log.info(
            f"preparation: scanned {scanned}, prepared {prepared}, already {already}, "
            f"non-English {dropped}"
        )


@dataclass(frozen=True, slots=True)
class ShardReport:
    """Everything one scrape shard learned about its own run (ADR-0045, ADR-0154).

    Written once, in ``scrape_run``'s shutdown path, beside the shard's fragment
    (:func:`write_shard`); read back by ``scrape_join``, ``shard_speedup`` and
    ``update_ledgers`` (via :func:`read_shards`) and by :meth:`ScrapeHealth.from_reports`. Every
    field has a default so a caller that only has a handful of numbers — the synthetic
    single-shard report `scrape_run` builds for its own mid-run health check, or a test — can
    construct one without restating the rest.

    ``malformed`` is not part of the writer's contract: a shard always writes a clean report, so
    it is never set at construction time. :meth:`from_json` sets it when a *read* had to coerce
    a field into its declared shape, and :meth:`to_json` never emits it — the on-disk shape is
    unchanged from before this type existed.
    """

    shard: str | None = None
    assigned: int = 0
    done: int = 0
    undone: int = 0
    jobs: int = 0
    seconds: float = 0.0
    predicted_minutes: float | None = None
    serial_minutes: float | None = None
    killed_by_budget: bool = False
    deferred: list[str] = dataclasses.field(default_factory=list)
    board_seconds: dict[str, float] = dataclasses.field(default_factory=dict)
    retries: dict[str, int] = dataclasses.field(default_factory=dict)
    egress_ips: dict[str, int] = dataclasses.field(default_factory=dict)
    errors: dict[str, str] = dataclasses.field(default_factory=dict)
    truncated: dict[str, str] = dataclasses.field(default_factory=dict)
    boards_ok: list[str] = dataclasses.field(default_factory=list)
    observations: dict[str, dict] = dataclasses.field(default_factory=dict)
    malformed: bool = False

    def to_json(self) -> str:
        # `asdict`, not a hand-listed dict: a field added to the dataclass later must not be
        # able to go silently missing from its own on-disk report. `malformed` is the one field
        # that never belongs here — it is a read-time signal `from_json` sets, not part of what
        # a shard itself ever reports.
        fields = dataclasses.asdict(self)
        del fields["malformed"]
        return json.dumps(fields, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, raw: Any) -> ShardReport | None:
        """Coerce a shard's on-disk report into a typed, safe-to-aggregate record.

        ``None`` only when ``raw`` isn't even a JSON object. Anything else is always returned,
        with every field coerced to its declared shape and ``malformed`` set when that coercion
        had to change something — the join's real job is unioning job data, and it must not die
        because a shard's telemetry did.
        """
        if not isinstance(raw, dict):
            return None
        malformed = False

        def dict_field(name: str) -> dict:
            nonlocal malformed
            value = raw.get(name, {})
            if not isinstance(value, dict):
                malformed = True
                return {}
            return value

        truncated = dict_field("truncated")
        observations = dict_field("observations")

        safe_errors: dict[str, str] = {}
        for key, value in dict_field("errors").items():
            if not isinstance(key, str) or not isinstance(value, str):
                malformed = True
            safe_errors[str(key)] = str(value)

        def str_list(name: str) -> list[str]:
            nonlocal malformed
            value = raw.get(name, [])
            if not isinstance(value, list):
                malformed = True
                value = []
            if any(not isinstance(v, str) for v in value):
                malformed = True
            return [str(v) for v in value]

        boards_ok = str_list("boards_ok")
        deferred = str_list("deferred")

        def int_dict(name: str) -> dict[str, int]:
            nonlocal malformed
            value = raw.get(name, {})
            if not isinstance(value, dict):
                malformed = True
                return {}
            safe: dict[str, int] = {}
            for key, count in value.items():
                try:
                    safe[str(key)] = int(count)
                except (TypeError, ValueError):
                    malformed = True
            return safe

        retries = int_dict("retries")
        egress_ips = int_dict("egress_ips")

        def as_int(name: str) -> int:
            nonlocal malformed
            try:
                return int(raw.get(name) or 0)
            except (TypeError, ValueError):
                malformed = True
                return 0

        def optional_float(name: str) -> float | None:
            nonlocal malformed
            value = raw.get(name)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                malformed = True
                return None

        board_seconds_raw = raw.get("board_seconds") or {}
        if not isinstance(board_seconds_raw, dict):
            board_seconds_raw = {}
            malformed = True
        board_seconds: dict[str, float] = {}
        for key, value in board_seconds_raw.items():
            try:
                board_seconds[str(key)] = float(value)
            except (TypeError, ValueError):
                malformed = True

        return cls(
            shard=raw.get("shard"),
            assigned=as_int("assigned"),
            done=as_int("done"),
            undone=as_int("undone"),
            jobs=as_int("jobs"),
            seconds=optional_float("seconds") or 0.0,
            predicted_minutes=optional_float("predicted_minutes"),
            serial_minutes=optional_float("serial_minutes"),
            killed_by_budget=bool(raw.get("killed_by_budget")),
            deferred=deferred,
            board_seconds=board_seconds,
            retries=retries,
            egress_ips=egress_ips,
            errors=safe_errors,
            truncated=truncated,
            boards_ok=boards_ok,
            observations=observations,
            malformed=malformed,
        )


#: Share of attempted Boards that may come back unusable before the run is called degraded.
#:
#: Measured, not chosen: the unusable share (failed + partial over attempted) ran **0.56-0.735%**
#: across the five runs of 2026-09-16 — 119/130/135/112/147 unusable of 20,000 attempted, from
#: those runs' own join logs — and 0.655% on the live `data/state/scrape_health.json`. 2% leaves
#: roughly 3x headroom over that baseline, which is what stops the ordinary run tripping it.
_DEGRADED_SHARE = 0.02
#: Where a run stops being noisy and starts being an incident. 2026-09-12's Workday collapse ran at
#: 80.8% of that provider's own attempts, which at Workday's measured 12.7% of a slice computes to
#: **10.8% overall** — only ~1.2pp above this band, so an outage of the same severity on a slightly
#: smaller provider would read DEGRADED rather than CRITICAL. The margin here is thin, unlike
#: `_DEGRADED_SHARE`'s ~3x; this band separates an incident from noise, not one incident from
#: another. It is a label on the line for whoever reads it — `scrape_join` logs DEGRADED and
#: CRITICAL at the same level, so nothing is paged differently.
_CRITICAL_SHARE = 0.10
#: Fewest attempted Boards an ATS needs before it can be named as driving the verdict. `amazon` is
#: one Board and reads 100% unusable the moment it comes back short.
_MIN_GRADED_BOARDS = 25


@dataclass
class ScrapeHealth:
    """One reporting contract for shard and run-level Board coverage and scrape losses."""

    coverage: dict[str, Counter[str]]
    losses: dict[str, Counter[str]]
    causes: Counter[tuple[str, str, str]]
    cause_boards: dict[tuple[str, str, str], set[str]]
    report_count: int
    expected_report_count: int
    malformed_report_count: int

    @classmethod
    def from_reports(
        cls,
        reports: list[ShardReport],
        expected_reports: int | None = None,
        *,
        quiet: bool = False,
    ) -> ScrapeHealth:
        """Aggregate shard reports into one verdict; a malformed field is kept out and named.

        ``quiet`` reports malformed fields at INFO, as :func:`read_shards` does: a shard reading
        its own report would otherwise warn once per shard, and the join warns for the run."""
        coverage: dict[str, Counter[str]] = defaultdict(Counter)
        losses: dict[str, Counter[str]] = defaultdict(Counter)
        causes: Counter[tuple[str, str, str]] = Counter()
        cause_boards: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        malformed_shards: list[str] = []
        # `shard:where` for each coercion, so the one warning names the field and not just "?"
        malformed_fields: list[str] = []
        for report in reports:
            shard = str(report.shard or "?")
            malformed = report.malformed
            if malformed:
                # `from_json` records only that something was coerced, not which field
                malformed_fields.append(f"{shard}:report")
            for key in report.boards_ok:
                coverage[ats_of(key)]["successful"] += 1
            for key in report.errors:
                coverage[ats_of(key)]["failed"] += 1
            for key in report.truncated:
                coverage[ats_of(key)]["partial"] += 1
            for board, observation in report.observations.items():
                if not isinstance(observation, dict):
                    malformed = True
                    malformed_fields.append(f"{shard}:{board}")
                    continue
                ats = ats_of(board)
                for field in _LOSS_FIELDS:
                    try:
                        losses[ats][field] += int(observation.get(field) or 0)
                    except (TypeError, ValueError):
                        malformed = True
                        malformed_fields.append(f"{shard}:{board}.{field}")
                for kind, field in (
                    ("listing", "listing_loss_causes"),
                    ("detail", "detail_loss_causes"),
                ):
                    cause_map = observation.get(field) or {}
                    if not isinstance(cause_map, dict):
                        malformed = True
                        malformed_fields.append(f"{shard}:{board}.{field}")
                        continue
                    for cause, count in cause_map.items():
                        key = (kind, ats, str(cause))
                        try:
                            causes[key] += int(count)
                        except (TypeError, ValueError):
                            malformed = True
                            malformed_fields.append(f"{shard}:{board}.{field}")
                            continue
                        cause_boards[key].add(str(board))
            if malformed:
                malformed_shards.append(shard)
        malformed_reports = len(malformed_shards)
        if malformed_reports:
            (_log.info if quiet else _log.warning)(
                f"{malformed_reports} shard report(s) carried malformed scrape-health fields; "
                "valid fields were kept and fresh coverage is marked degraded: "
                + log.named_sample(malformed_fields)
            )
        expected = max(len(reports), expected_reports or len(reports))
        return cls(
            dict(coverage),
            dict(losses),
            causes,
            dict(cause_boards),
            len(reports),
            expected,
            malformed_reports,
        )

    @property
    def unusable_share(self) -> float:
        """Fraction of attempted Boards whose list this run could not use — failed or partial.

        The number the verdict is graded on. Attempted is ``successful + failed``.

        **The numerator is an upper bound, and is clamped.** ``failed`` and ``partial`` are counted
        from two sets in the same report and a Board can be in both: ``harvest`` records truncation
        in a ``finally`` on purpose ("a scraper that truncated and *then* raised still reported
        something worth carrying"), and ``on_board`` then routes that Board into ``errors`` as well.
        The counters are per-ATS totals, not sets, so the overlap cannot be subtracted here — and
        without the clamp one such Board on a one-Board ATS reported ``200.00% unusable``. Overlap
        only ever *shrinks* the true count, so ``min(...)`` is the conservative reading and keeps
        this a fraction.
        """
        attempted = sum(c["successful"] + c["failed"] for c in self.coverage.values())
        if not attempted:
            return 0.0
        unusable = sum(c["failed"] + c["partial"] for c in self.coverage.values())
        return min(unusable, attempted) / attempted

    def worst_atses(self, limit: int = 3) -> list[tuple[str, float, int]]:
        """The ATSes driving the share, largest first, as ``(ats, share, attempted)``.

        Only ATSes with at least :data:`_MIN_GRADED_BOARDS` attempted Boards. Per-ATS baselines are
        far noisier than the overall one — measured on the live ledger, keka sits at 9.95% and
        oracle at 4.90% while the run as a whole is at 0.655%, and ``amazon`` is a single Board that
        reads 100% the moment it comes back short. Ranking without a floor would name the noise.
        """
        ranked = []
        for ats, counts in self.coverage.items():
            attempted = counts["successful"] + counts["failed"]
            if attempted < _MIN_GRADED_BOARDS:
                continue
            unusable = counts["failed"] + counts["partial"]
            if unusable:
                ranked.append((ats, unusable / attempted, attempted))
        return sorted(ranked, key=lambda row: -row[1])[:limit]

    @property
    def degraded(self) -> bool:
        """Whether this run's coverage is bad enough to be worth saying so.

        **Graded, not a zero threshold.** This was ``any(failed or partial)`` until 2026-09-16,
        which over a ~20,000-Board slice (80,000 since ADR-0229) and 31 ATSes is always true: it
        read DEGRADED on 7 of 7 runs sampled across four days, and would have printed the identical
        word on 2026-09-12 when Workday failed 80.8% of its Board attempts and tech output fell
        ~87.7%. An alarm that is always on cannot raise one, and this is the pipeline's only
        run-level coverage verdict.

        Incomplete or malformed shard telemetry still degrades unconditionally — that is a
        different failure from a noisy scrape, and it has no share to grade.
        """
        return not self.complete or self.unusable_share > _DEGRADED_SHARE

    @property
    def complete(self) -> bool:
        return (
            self.report_count == self.expected_report_count
            and not self.malformed_report_count
        )

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
        share = self.unusable_share
        # One spelling of the threshold rule: `degraded` owns it, and this only adds the top band.
        if share > _CRITICAL_SHARE:
            verdict = "CRITICAL"
        elif self.degraded:
            verdict = "DEGRADED"
        else:
            verdict = "healthy"
        line = (
            f"Fresh coverage: {verdict} — {failed} failed and {partial} partial of "
            f"{attempted} attempted Boards ({share:.2%} unusable)"
        )
        if verdict != "healthy":
            worst = self.worst_atses()
            if worst:
                named = ", ".join(f"{ats} {rate:.1%} of {n}" for ats, rate, n in worst)
                line += f"; worst: {named}"
        if not self.complete:
            line += (
                f"; shard telemetry incomplete: {self.report_count}/"
                f"{self.expected_report_count} reports, "
                f"{self.malformed_report_count} malformed"
            )
        return line

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
                    # Every cause, largest first — no cap. `base.loss_breakdown` dropped the
                    # identical `[:4]` + `+N more causes` shape in #457 on the grounds that the
                    # cause vocabulary is closed (status codes plus a handful of parse-shape
                    # labels), so no line grows unreadable. This run-level copy survived that
                    # change, and it is the one that hurts most: it aggregates across every Board,
                    # so the tail it hid was the long tail. Measured over the five runs of
                    # 2026-09-16, **27** lines hit the cap — 12 in the join roll-up and 15 more in the
                    # shard logs, which the first count missed — and the widest had 8 distinct causes —
                    # the cap was discarding counts to save four entries.
                    shown = "; ".join(
                        f"{cause} x{count} on {boards} Board(s)"
                        for cause, count, boards in ranked
                    )
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
            "complete": self.complete,
            "reports": {
                "received": self.report_count,
                "expected": self.expected_report_count,
                "malformed": self.malformed_report_count,
            },
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
        path.write_text(
            json.dumps(health.to_dict(), indent=1, sort_keys=True), encoding="utf-8"
        )
    except OSError as exc:
        _scrape_health_write_failed.report(
            f"could not write scrape health to {path}: {exc}"
        )


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
        _summary_write_failed.report(
            f"could not write the step summary to {path}: {exc}"
        )


def write_shard(outdir: Path, report: ShardReport) -> None:
    """Record this shard's own numbers beside its fragment, so the join can aggregate them.

    Same swallow-on-failure reasoning as :func:`summary`, and for a stronger reason here: this
    runs in the shutdown path of a shard that may already be dying on its time budget.
    """
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / _SHARD_REPORT).write_text(report.to_json(), encoding="utf-8")
    except OSError as exc:
        # INFO: this runs once per shard *process*, so a FirstOnly would still warn per shard.
        # The join says it run-level — a missing report degrades its coverage verdict, which
        # warns.
        _log.info(f"could not write the shard report to {outdir}: {exc}")


def read_shards(fragments: Path, *, quiet: bool = False) -> list[ShardReport]:
    """Every shard report under ``fragments``, newest-run-first order not guaranteed.

    A missing or corrupt report is skipped with a warning rather than raising: the join's job
    is to union job data, and it must not die because a shard's telemetry did.

    ``quiet`` reports the same skips at INFO, for a second reader in the same job: the join has
    already annotated them, and a second copy spends budget restating one fault."""
    report_skip = _log.info if quiet else _log.warning
    out: list[ShardReport] = []
    unreadable: list[str] = []
    wrong_shape: list[str] = []
    for path in sorted(fragments.glob(f"*/{_SHARD_REPORT}")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            unreadable.append(f"{path.parent.name} ({type(exc).__name__})")
            continue
        report = ShardReport.from_json(raw)
        if report is None:
            wrong_shape.append(path.parent.name)
            continue
        out.append(report)
    if unreadable:
        # One warning for the set, not one per shard. A fan-out has ~15 shards and a WARNING
        # is an annotation under Actions, capped at 10 per step — so the per-shard form could
        # spend the join's whole budget reporting that telemetry was missing, and bury the
        # join's own errors doing it. The names still ride, via `log.named_sample`.
        report_skip(
            f"{len(unreadable)} shard report(s) unreadable, so their telemetry is missing "
            f"from this run's totals: {log.named_sample(unreadable)}"
        )
    if wrong_shape:
        report_skip(
            f"{len(wrong_shape)} shard report(s) were valid JSON but not objects and were "
            f"skipped: {log.named_sample(wrong_shape)}"
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
        by_type[message.split(":", 1)[0]][ats_of(key)] += 1
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
