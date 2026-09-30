"""Tests for the Common Crawl host-graph miner (scripts/discover/mine_common_crawl_host_graph.py).
A script under `scripts/discover`."""

import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_common_crawl_host_graph as miner

_LINES = [
    "1\tcom.bamboohr.acme",
    "2\tcom.example.foo",
    "3\tcom.teamtailor.na.robot",
    "4\tde.personio.jobs.foo-gmbh",
    "5\tzzz.last",
]
_SHARD = ("\n".join(_LINES) + "\n").encode()


def test_a_shard_scan_keeps_the_wanted_reversed_hosts_across_chunk_boundaries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(miner, "CHUNK", 64)  # force lines to straddle chunks
    shard = tmp_path / "shard.gz"
    shard.write_bytes(gzip.compress(_SHARD * 50))
    found, rows = miner.scan_shard(shard, miner.prefix_pattern())
    assert rows == len(_LINES) * 50
    assert sorted(set(found)) == [
        b"com.bamboohr.acme",
        b"com.teamtailor.na.robot",
        b"de.personio.jobs.foo-gmbh",
    ]
    assert len(found) == 3 * 50


def test_a_truncated_shard_is_an_error_not_a_finished_scan(tmp_path):
    """A half-downloaded gzip must never be recorded as a done shard (its checkpoint)."""
    shard = tmp_path / "shard.gz"
    shard.write_bytes(gzip.compress(_SHARD * 500)[:-40])
    with pytest.raises(EOFError):
        miner.scan_shard(shard, miner.prefix_pattern())


def _fake_download(payloads):
    """A `download` that writes the next payload and records its calls."""
    calls = []

    def download(url, dest, resume=True):
        calls.append(url)
        dest.write_bytes(payloads[min(len(calls), len(payloads)) - 1])

    return download, calls


def test_a_cut_shard_is_fetched_again_after_a_pause_then_scanned(tmp_path, monkeypatch):
    whole = gzip.compress(_SHARD * 10)
    download, calls = _fake_download([whole[:-30], whole])
    pauses = []
    monkeypatch.setattr(miner, "download", download)
    monkeypatch.setattr(miner.time, "sleep", pauses.append)
    found, rows = miner.fetch_and_scan(
        "https://x/shard", tmp_path / "s.gz", miner.prefix_pattern()
    )
    assert rows == len(_LINES) * 10 and len(found) == 30
    assert len(calls) == 2 and pauses == [miner.RESCAN_PAUSE]


def test_a_shard_that_stays_cut_fails_loudly_after_the_cap_instead_of_looping(
    tmp_path, monkeypatch
):
    """The old `while True: download; scan; except: unlink` had no cap and no sleep."""
    download, calls = _fake_download([gzip.compress(_SHARD * 10)[:-30]])
    pauses = []
    monkeypatch.setattr(miner, "download", download)
    monkeypatch.setattr(miner.time, "sleep", pauses.append)
    with pytest.raises(RuntimeError, match="cut or corrupt"):
        miner.fetch_and_scan(
            "https://x/shard", tmp_path / "s.gz", miner.prefix_pattern()
        )
    assert len(calls) == miner.SCAN_ATTEMPTS
    assert pauses == [miner.RESCAN_PAUSE * n for n in range(1, miner.SCAN_ATTEMPTS)]
    assert not (tmp_path / "s.gz").exists()  # the corrupt file is not left as a cache


def test_a_corrupt_shard_is_treated_like_a_cut_one(tmp_path, monkeypatch):
    download, calls = _fake_download([b"not gzip at all"])
    monkeypatch.setattr(miner, "download", download)
    monkeypatch.setattr(miner.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError):
        miner.fetch_and_scan(
            "https://x/shard", tmp_path / "s.gz", miner.prefix_pattern()
        )
    assert len(calls) == miner.SCAN_ATTEMPTS


def test_the_shard_listing_is_read_and_its_file_closed(tmp_path, monkeypatch):
    listing = tmp_path / "vertices.paths.gz"
    listing.write_bytes(gzip.compress(b"projects/a/part-0.gz\nprojects/a/part-1.gz\n"))
    assert miner.shard_urls("rel", tmp_path) == [
        miner.BASE + "projects/a/part-0.gz",
        miner.BASE + "projects/a/part-1.gz",
    ]


def test_staging_turns_every_done_shards_reversed_hosts_into_rows_by_ats(
    tmp_path, monkeypatch
):
    (tmp_path / "matches-00000.txt").write_text("com.bamboohr.acme\ncom.bamboohr.www\n")
    (tmp_path / "matches-00001.txt").write_text(
        "com.teamtailor.na.robot\nde.personio.jobs.foo\n"
    )
    hosts, shards = miner.mined_hosts(tmp_path)
    assert shards == 2
    assert hosts == {
        "acme.bamboohr.com",
        "www.bamboohr.com",
        "robot.na.teamtailor.com",
        "foo.jobs.personio.de",
    }
    staged = []
    monkeypatch.setattr(
        miner,
        "stage_by_ats",
        lambda rows, source: staged.append((sorted(rows), source)),
    )
    miner.stage(tmp_path)
    rows, source = staged[0]
    assert source == "common_crawl_host_graph"
    assert rows == [  # the vendor's own `www` is not a Board
        ("bamboohr", "acme", "https://acme.bamboohr.com"),
        ("personio", "foo", "https://foo.jobs.personio.de"),
        ("teamtailor", "robot.na", "https://robot.na.teamtailor.com"),
    ]
