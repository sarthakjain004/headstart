"""The Instahyre miner has one canonical global marketplace source."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_instahyre


def test_write_pool_keeps_one_global_marketplace_row(tmp_path):
    pool = tmp_path / "instahyre.csv"

    mine_instahyre.write_pool(pool)

    with pool.open(newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f)) == [
            {
                "ats": "instahyre",
                "tenant": "global",
                "url": "https://www.instahyre.com/api/v1/job_search?limit=35&offset=0",
                "source": "public-listing",
            }
        ]
