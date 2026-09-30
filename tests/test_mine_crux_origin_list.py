"""Tests for the CrUX origin-list miner's pure parts (scripts/discover/mine_crux_origin_list.py)."""

import json
import lzma
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_crux_origin_list as miner


def test_an_archive_yields_its_tenant_hosts_once_and_counts_every_origin(tmp_path):
    origins = (
        "https://acme.teamtailor.com\n"
        "http://acme.teamtailor.com\n"
        "https://robot.na.teamtailor.com\n"
        "https://www.bamboohr.com\n"
        "https://example.com\n"
        "https://jobs.pinpointhq.com\n"
        "https://widgets.pinpointhq.com"
    )
    archive = tmp_path / "5000000.txt.xz"
    archive.write_bytes(lzma.compress(origins.encode()))
    hosts, rows = miner.tenant_hosts_in(archive)
    assert rows == 7
    assert hosts == [
        "acme.teamtailor.com",
        "robot.na.teamtailor.com",
        "widgets.pinpointhq.com",
    ]


def test_the_buckets_and_their_ranks_come_from_the_months_meta(tmp_path, monkeypatch):
    def fake_download(url, dest, resume=True):
        dest.write_text(
            json.dumps(
                {
                    "files": [
                        {"file": "1000000.txt.xz", "rank": 1000000},
                        {"file": "5000000.txt.xz", "rank": 5000000},
                    ]
                }
            )
        )

    monkeypatch.setattr(miner, "download", fake_download)
    assert miner.month_files("202608", tmp_path) == [
        ("1000000.txt.xz", 1000000),
        ("5000000.txt.xz", 5000000),
    ]
