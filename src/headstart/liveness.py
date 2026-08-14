"""Liveness ledger: the durable ``(ats, tenant)`` -> verdict table (ADR-0012).

One CSV per ATS at ``data/validate/liveness/{ats}.csv``::

    ats,tenant,url,status,jobs,checked_at

- ``status`` — CONTEXT.md's three-state (``live`` | ``dead`` | ``unknown``), persisted for *every*
  board, not just the live/dead ones.
- ``jobs`` — the count at the last Live probe (blank for dead/unknown).
- ``checked_at`` — ISO date of the last probe; the freshness key.

The ledger is the single source of truth: the Active list is ``status == live`` (see
``config.load_active_companies``), the dead set is ``status == dead``, the unresolved set is
``status == unknown``. This module is pure CSV I/O plus the re-probe policy — no network — so both
the checker (which probes and upserts) and ``config`` (which reads the live set) can share it.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"
FIELDS = ("ats", "tenant", "url", "status", "jobs", "checked_at")

# Re-probe TTLs (days): how long a verdict is trusted before it is probed again. Env-tunable so
# the cadence changes without a code edit. ``dead`` rarely reverses (long TTL); ``live`` refreshes
# often to update job counts and catch a board that has since died.
LIVE_TTL_DAYS = int(os.environ.get("HEADSTART_LIVE_TTL_DAYS") or 7)
DEAD_TTL_DAYS = int(os.environ.get("HEADSTART_DEAD_TTL_DAYS") or 90)
# ``unknown`` used to be re-probed on every single run. That is the right instinct — we genuinely
# do not know, and boards do come back — but some fail *identically* every time: a Workday board
# answering 403 is outside the conclusive _WD_GONE set, so it survives all four escalating passes
# and is retried in full on the next run, forever. Measured 2026-08-14 on a 120-per-ATS sample:
# ~140 boards in that state, the whole four-pass cost spent to learn nothing new. A short TTL
# keeps the semantics (still rechecked twice a week, never a false verdict) without paying it
# every run. Short on purpose — this is "ask again soon", not "settled".
UNKNOWN_TTL_DAYS = int(os.environ.get("HEADSTART_UNKNOWN_TTL_DAYS") or 3)


@dataclass(frozen=True, slots=True)
class Verdict:
    """One board's last liveness verdict."""

    ats: str
    tenant: str
    url: str
    status: str
    jobs: int | None
    checked_at: str  # ISO date, e.g. "2026-07-02"


def dir_for(root: str | Path) -> Path:
    return Path(root) / "data" / "validate" / "liveness"


def path_for(root: str | Path, ats: str) -> Path:
    return dir_for(root) / f"{ats}.csv"


def load(path: str | Path) -> dict[str, Verdict]:
    """``tenant -> Verdict`` for one ATS's ledger ( ``{}`` if the file is absent)."""
    path = Path(path)
    out: dict[str, Verdict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            jobs = (r.get("jobs") or "").strip()
            out[r["tenant"]] = Verdict(
                ats=r["ats"],
                tenant=r["tenant"],
                url=r.get("url", ""),
                status=r.get("status") or UNKNOWN,
                jobs=int(jobs) if jobs else None,
                checked_at=r.get("checked_at", ""),
            )
    return out


def write(path: str | Path, verdicts: Iterable[Verdict]) -> None:
    """Write one ATS's ledger (sorted by tenant), creating the directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for v in sorted(verdicts, key=lambda v: v.tenant):
            w.writerow(
                [
                    v.ats,
                    v.tenant,
                    v.url,
                    v.status,
                    "" if v.jobs is None else v.jobs,
                    v.checked_at,
                ]
            )


def _age_days(checked_at: str, today: date) -> int | None:
    """Days since ``checked_at``; None if it won't parse (treated as infinitely stale)."""
    try:
        return (today - date.fromisoformat(checked_at)).days
    except (ValueError, TypeError):
        return None


def needs_probe(
    verdict: Verdict | None,
    today: date,
    *,
    live_ttl: int = LIVE_TTL_DAYS,
    dead_ttl: int = DEAD_TTL_DAYS,
    unknown_ttl: int = UNKNOWN_TTL_DAYS,
) -> bool:
    """Should this board be probed this run? New, or past its per-status TTL."""
    if verdict is None:
        return True
    age = _age_days(verdict.checked_at, today)
    if age is None:
        return True
    ttl = {LIVE: live_ttl, DEAD: dead_ttl}.get(verdict.status, unknown_ttl)
    return age >= ttl
