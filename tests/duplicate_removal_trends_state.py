"""A small Trends history holding one duplicate removal, written through ``record_tick``: the
state ``test_trend_history`` and ``test_trend_reading`` read a removal from.

Micro (one Eightfold Board) hires 2 tech openings a tick over 17 ticks, twelve hours apart. On
tick ``REMOVAL`` duplicate removal takes out 119 rows: 109 of its 218 tech openings and 10 of its
20 non-tech ones, so the ratio is (238 − 119) / 238 = 0.5. Beta (one Greenhouse Board) hires 1 a
tick and removes nothing. Nothing about how HeadStart counts changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from headstart import roles, trend_history

MICRO = "eightfold:careers.micro.com"
BETA = "greenhouse:beta"
START = datetime.fromisoformat("2026-09-10T00:00:00+00:00")
TICKS = [(START + timedelta(hours=12 * k)).isoformat() for k in range(17)]
REMOVAL = 10
REMOVED_ROWS = 119
REMOVED_TECH = 109


def micro_tech(k: int) -> int:
    """Micro's tech openings at tick ``k``: 200, then 2 more a tick, halved at the removal."""
    if k < REMOVAL:
        return 200 + 2 * k
    return (200 + 2 * (REMOVAL - 1)) - REMOVED_TECH + 2 * (k - REMOVAL + 1)


def write(state: Path) -> None:
    methodology = trend_history.Methodology("fingerprint", 3, 5, 15, 5)
    for k, ts in enumerate(TICKS):
        levels = {
            (MICRO, "stock", "software-engineering", "mid"): micro_tech(k),
            (MICRO, "stock", roles.NON_TECH, "all"): 20 if k < REMOVAL else 10,
            (BETA, "stock", "software-engineering", "mid"): 50 + k,
        }
        trend_history.record_tick(state, ts, levels, {}, methodology)
    (state / "dedup_evictions.csv").write_text(
        f"board,ts,count\n{MICRO},{TICKS[REMOVAL]},{REMOVED_ROWS}\n", encoding="utf-8"
    )
    companies = [
        {"name": "Micro", "boards": [MICRO], "operator": "employer"},
        {"name": "Beta", "boards": [BETA], "operator": "employer"},
    ]
    (state / "company_directory.json").write_text(
        json.dumps({"companies": companies}), encoding="utf-8"
    )
