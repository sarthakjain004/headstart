"""A small Trends history holding one duplicate removal, written through ``record_tick``: the
state ``test_trend_history`` and ``test_trend_reading`` read a removal from.

Micro (one Eightfold Board) hires 2 tech openings a tick over 17 ticks, twelve hours apart. On
tick ``REMOVAL`` duplicate removal takes out 119 rows: 109 of its 218 tech openings and 10 of its
20 non-tech ones, so the ratio is (238 − 119) / 238 = 0.5. Beta (one Greenhouse Board) hires 1 a
tick and removes nothing. Nothing about how HeadStart counts changes.

With ``board_found_later``, Micro also has a Board HeadStart first counts at tick
``FOUND_LATER`` (30 openings), where duplicate removal takes out ``LATE_REMOVED_ROWS`` rows at
tick ``LATE_REMOVAL``: a Board a comparable cohort leaves out, with a removal of its own.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from headstart.trends import role_taxonomy, trend_history

MICRO = "eightfold:careers.micro.com"
BETA = "greenhouse:beta"
START = datetime.fromisoformat("2026-09-10T00:00:00+00:00")
TICKS = [(START + timedelta(hours=12 * k)).isoformat() for k in range(17)]
REMOVAL = 10
REMOVED_ROWS = 119
REMOVED_TECH = 109
LATE_BOARD = "greenhouse:micro"
FOUND_LATER = 5
LATE_REMOVAL = 13
LATE_REMOVED_ROWS = 6


def micro_tech(k: int) -> int:
    """Micro's tech openings at tick ``k``: 200, then 2 more a tick, halved at the removal."""
    if k < REMOVAL:
        return 200 + 2 * k
    return (200 + 2 * (REMOVAL - 1)) - REMOVED_TECH + 2 * (k - REMOVAL + 1)


def write(state: Path, board_found_later: bool = False) -> None:
    methodology = trend_history.Methodology("fingerprint", 3, 5, 15, 5)
    for k, ts in enumerate(TICKS):
        levels = {
            (MICRO, "stock", "software-engineering", "mid"): micro_tech(k),
            (MICRO, "stock", role_taxonomy.NON_TECH, "all"): 20 if k < REMOVAL else 10,
            (BETA, "stock", "software-engineering", "mid"): 50 + k,
        }
        if board_found_later and k >= FOUND_LATER:
            late = 30 if k < LATE_REMOVAL else 30 - LATE_REMOVED_ROWS
            levels[(LATE_BOARD, "stock", "software-engineering", "mid")] = late
        trend_history.record_tick(state, ts, levels, {}, methodology)
    evictions = f"board,ts,count\n{MICRO},{TICKS[REMOVAL]},{REMOVED_ROWS}\n"
    if board_found_later:
        evictions += f"{LATE_BOARD},{TICKS[LATE_REMOVAL]},{LATE_REMOVED_ROWS}\n"
    (state / "dedup_evictions.csv").write_text(evictions, encoding="utf-8")
    micro_boards = [MICRO, LATE_BOARD] if board_found_later else [MICRO]
    companies = [
        {"name": "Micro", "boards": micro_boards, "operator": "employer"},
        {"name": "Beta", "boards": [BETA], "operator": "employer"},
    ]
    (state / "company_directory.json").write_text(
        json.dumps({"companies": companies}), encoding="utf-8"
    )
