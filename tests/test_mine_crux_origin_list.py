"""Tests for the CrUX origin-list miner (scripts/discover/mine_crux_origin_list.py)."""

import json
import lzma
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_crux_origin_list as miner


def test_an_archive_yields_its_board_hosts_once_and_counts_every_origin(tmp_path):
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
    hosts, rows = miner.board_hosts_in(archive)
    assert rows == 7
    assert hosts == [
        "acme.teamtailor.com",
        "robot.na.teamtailor.com",
        "widgets.pinpointhq.com",
    ]


def test_the_buckets_and_their_ranks_come_from_the_months_meta_read_fresh(monkeypatch):
    asked = []

    def fake_fetch(url):
        asked.append(url)
        return json.dumps(
            {
                "files": [
                    {"file": "1000000.txt.xz", "rank": 1000000},
                    {"file": "5000000.txt.xz", "rank": 5000000},
                ]
            }
        ).encode()

    monkeypatch.setattr(miner, "fetch_bytes", fake_fetch)
    assert miner.month_files("202608") == [
        ("1000000.txt.xz", 1000000),
        ("5000000.txt.xz", 5000000),
    ]
    assert asked == [miner.REPO + "2026/08/meta.json"]


def test_the_latest_month_reads_the_mutable_root_meta_fresh_and_never_resumes_it(
    monkeypatch,
):
    """The root `meta.json` grows a month at a time: it is fetched whole every run, not resumed
    onto an older copy and not cached."""
    body = {
        "years": [
            {"months": [{"id": "202607"}, {"id": "202608"}]},
            {"months": [{"id": "202512"}]},
        ]
    }
    asked = []
    monkeypatch.setattr(
        miner, "fetch_bytes", lambda url: asked.append(url) or json.dumps(body).encode()
    )
    monkeypatch.setattr(
        miner,
        "download",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("meta.json must not be resumed")
        ),
    )
    assert miner.latest_month() == "202608"
    assert asked == [miner.REPO + "meta.json"]


def _fake_download(hosts_by_name):
    def download(url, dest, resume=True):
        name = url.rsplit("/", 1)[-1]
        dest.write_bytes(lzma.compress("\n".join(hosts_by_name[name]).encode()))

    return download


def test_a_done_bucket_is_skipped_and_refresh_reads_it_again(tmp_path, monkeypatch):
    monkeypatch.setattr(
        miner,
        "download",
        _fake_download({"5000000.txt.xz": ["https://one.teamtailor.com"]}),
    )
    miner.mine_bucket("202608", tmp_path, "5000000.txt.xz", 5_000_000, refresh=False)
    matches = tmp_path / "matches-5000000.txt.xz.txt"
    assert matches.read_text() == "one.teamtailor.com\n"
    assert not (
        tmp_path / "5000000.txt.xz"
    ).exists()  # the archive is deleted after its scan
    # the same month republished with another Board: skipped without --refresh, read with it
    monkeypatch.setattr(
        miner,
        "download",
        _fake_download({"5000000.txt.xz": ["https://two.teamtailor.com"]}),
    )
    miner.mine_bucket("202608", tmp_path, "5000000.txt.xz", 5_000_000, refresh=False)
    assert matches.read_text() == "one.teamtailor.com\n"
    miner.mine_bucket("202608", tmp_path, "5000000.txt.xz", 5_000_000, refresh=True)
    assert matches.read_text() == "two.teamtailor.com\n"


def test_staging_reads_every_buckets_checkpoint(tmp_path, monkeypatch, capsys):
    (tmp_path / "matches-a.txt").write_text("one.teamtailor.com\nx.bamboohr.com\n")
    (tmp_path / "matches-b.txt").write_text("one.teamtailor.com\n")
    staged = []
    monkeypatch.setattr(
        miner,
        "stage_by_ats",
        lambda rows, source: staged.append((sorted(rows), source)),
    )
    miner.stage(tmp_path, 2)
    assert staged == [
        (
            [
                ("bamboohr", "x", "https://x.bamboohr.com"),
                ("teamtailor", "one", "https://one.teamtailor.com"),
            ],
            "crux_origin_list",
        )
    ]
    assert "2 distinct Board hosts in 2 buckets" in capsys.readouterr().out
