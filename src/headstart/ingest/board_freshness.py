"""Measure freshness withheld by Unauthoritative Boards; never change eviction policy.

Times describe sync observations, not exact ATS fetch times. An unknown prior authoritative
run stays unknown. Unselected Boards retain their history; leaving the Live set drops it.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from headstart import log
from headstart.ingest.index_plan import resolve_board

_log = log.get(__name__)


@dataclass
class Freshness:
    last_authoritative: str = ""
    excluded_since: str = ""
    consecutive_exclusions: int = 0


def update(
    state_dir: Path,
    live: dict[str, str],
    authoritative: set[str],
    unauthoritative: dict[str, str],
    index_ids: list[str],
    corpus_ids: set[str],
    observed_at: str,
) -> dict:
    """Persist per-Board history and report row counts by ATS for unresolved exclusions."""
    path = state_dir / "board_freshness.csv.gz"
    history: dict[str, Freshness] = {}
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                if row["board"] in live:
                    history[row["board"]] = Freshness(
                        row["last_authoritative"], row["excluded_since"],
                        int(row["consecutive_exclusions"]),
                    )
    excluded = {board.lower(): why for board, why in unauthoritative.items()}
    for board in {b.lower() for b in authoritative} - excluded.keys():
        if board in live:
            history[board] = Freshness(last_authoritative=observed_at)
    for board in excluded:
        if board in live:
            held = history.setdefault(board, Freshness())
            held.excluded_since = held.excluded_since or observed_at
            held.consecutive_exclusions += 1

    unresolved = {b: h for b, h in history.items() if h.consecutive_exclusions}
    protected: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    for job_id in index_ids:
        board = resolve_board(job_id, live).lower()
        if board in unresolved:
            protected[board] += 1
            if board in excluded and job_id not in corpus_ids:
                missing[board] += 1

    at = datetime.fromisoformat(observed_at)
    by_ats: dict[str, dict] = {}
    boards = []
    for board, held in sorted(unresolved.items()):
        age = round((at - datetime.fromisoformat(held.last_authoritative)).total_seconds() / 86400, 2) if held.last_authoritative else None
        ats = board.split(":", 1)[0]
        totals = by_ats.setdefault(ats, {
            "excluded_boards": 0, "protected_rows": 0, "not_reseen_rows": 0,
            "unknown_authoritative_age_boards": 0, "oldest_authoritative_run_days": None,
        })
        totals["excluded_boards"] += 1
        totals["protected_rows"] += protected[board]
        totals["not_reseen_rows"] += missing[board]
        if age is None:
            totals["unknown_authoritative_age_boards"] += 1
        else:
            prior = totals["oldest_authoritative_run_days"]
            totals["oldest_authoritative_run_days"] = age if prior is None else max(age, prior)
        boards.append({
            "board": live[board], **asdict(held), "authoritative_age_days": age,
            "protected_rows": protected[board],
            "not_reseen_rows": missing[board] if board in excluded else None,
            "attempted_this_observation": board in excluded,
        })
    for ats, totals in sorted(by_ats.items()):
        _log.info(f"withheld freshness {ats}: {totals}")

    report = {"observed_at": observed_at, "ats": by_ats, "boards": boards}
    state_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["board", "last_authoritative", "excluded_since", "consecutive_exclusions"])
        writer.writeheader()
        writer.writerows({"board": board, **asdict(held)} for board, held in sorted(history.items()))
    temporary.replace(path)
    (state_dir / "board_freshness_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
