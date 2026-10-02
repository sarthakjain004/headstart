"""Tests for the jobseek boards.csv miner's pure parts (scripts/discover/mine_jobseek_boards.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_jobseek_boards as miner

_BOARDS = (
    "company_slug,board_slug,board_url,monitor_type\n"
    "0x,0x-ashby,https://jobs.ashbyhq.com/0x,ashby\n"
    "100ms,100ms-pinpoint,https://100ms.pinpointhq.com,pinpoint\n"
    "11bit,11bit-recruitee,https://11bitstudios.recruitee.com/,recruitee\n"
    "x,x-empty,,none\n"
)


def test_only_sub_domain_ats_boards_become_rows_by_ats():
    assert miner.rows_by_ats(_BOARDS) == {
        "pinpoint": [("100ms", "https://100ms.pinpointhq.com")],
        "recruitee": [("11bitstudios", "https://11bitstudios.recruitee.com")],
    }


def test_board_rows_carry_the_ats_and_skip_what_no_family_names():
    assert miner.board_rows(_BOARDS) == [
        ("pinpoint", "100ms", "https://100ms.pinpointhq.com"),
        ("recruitee", "11bitstudios", "https://11bitstudios.recruitee.com"),
    ]
