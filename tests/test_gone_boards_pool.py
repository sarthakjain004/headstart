"""Tests for scripts/validate/gone_boards_pool.py: the pool of Boards the scrape keeps finding gone.

The property that matters: every ledger row of a Board at the strike threshold (consecutive gone
scrapes, ADR-0058) goes to the probe, casing variants and other Workday data centres included. A
row left out keeps its old `live` verdict, and one such row is enough to keep the Board Scrapable
(ADR-0219).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "gone_boards_pool", ROOT / "scripts" / "validate" / "gone_boards_pool.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "".join(f"{r}\n" for r in rows), encoding="utf-8")


def test_every_row_of_a_struck_board_and_nothing_else(mod, tmp_path):
    """A Board's rows all go, across casings and data centres, once it reaches the strike
    threshold. A Board under it, one never found gone, and one with no live row left are not
    re-probed."""
    ledger, failures = tmp_path / "liveness", tmp_path / "board_failures.csv"
    header = "ats,tenant,url,status,jobs,checked_at"
    _write(
        ledger / "greenhouse.csv",
        header,
        [
            "greenhouse,goneco,https://boards.greenhouse.io/goneco,live,4,2026-07-02",
            "greenhouse,flaky,https://boards.greenhouse.io/flaky,live,9,2026-07-02",
            "greenhouse,fine,https://boards.greenhouse.io/fine,live,3,2026-07-02",
            "greenhouse,deadco,https://boards.greenhouse.io/deadco,dead,,2026-09-28",
        ],
    )
    _write(
        ledger / "workday.csv",
        header,
        [
            "workday,a1,https://acme.wd1.myworkdayjobs.com/External,live,5,2026-07-02",
            "workday,a2,https://acme.wd5.myworkdayjobs.com/external,live,5,2026-08-02",
            "workday,b1,https://acme.wd5.myworkdayjobs.com/Other,live,2,2026-08-02",
        ],
    )
    _write(
        failures,
        "board,strikes,last_reason,last_seen_gone",
        [
            "greenhouse:goneco,21,HTTPError: HTTP Error 404: ,2026-09-28T00:00:00+00:00",
            "greenhouse:flaky,3,HTTPError: HTTP Error 404: ,2026-09-28T00:00:00+00:00",
            "greenhouse:deadco,25,HTTPError: HTTP Error 404: ,2026-09-28T00:00:00+00:00",
            "workday:acme/External,20,HTTPError: HTTP Error 404: ,2026-09-28T00:00:00+00:00",
        ],
    )

    pool = mod.pool_rows(ledger, failures, min_strikes=6)

    assert pool == {
        "greenhouse": [("goneco", "https://boards.greenhouse.io/goneco")],
        "workday": [
            ("a1", "https://acme.wd1.myworkdayjobs.com/External"),
            ("a2", "https://acme.wd5.myworkdayjobs.com/external"),
        ],
    }
    assert "flaky" in {t for t, _ in mod.pool_rows(ledger, failures, 3)["greenhouse"]}
