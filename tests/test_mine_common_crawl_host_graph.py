"""Tests for the Common Crawl host-graph miner's shard scan
(scripts/discover/mine_common_crawl_host_graph.py). A script under `scripts/discover`."""

import gzip
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_common_crawl_host_graph as miner
from tenant_hosts import reversed_prefixes

_LINES = [
    "1\tcom.bamboohr.acme",
    "2\tcom.example.foo",
    "3\tcom.teamtailor.na.robot",
    "4\tde.personio.jobs.foo-gmbh",
    "5\tzzz.last",
]


def _pattern():
    prefixes = sorted(reversed_prefixes(), key=len, reverse=True)
    alternatives = b"|".join(re.escape(p).encode() for p in prefixes)
    return re.compile(rb"^\d+\t((?:" + alternatives + rb")[^\t\n]*)$", re.MULTILINE)


def test_a_shard_scan_keeps_the_wanted_reversed_hosts_across_chunk_boundaries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(miner, "CHUNK", 64)  # force lines to straddle chunks
    shard = tmp_path / "shard.gz"
    shard.write_bytes(gzip.compress(("\n".join(_LINES) + "\n").encode() * 50))
    found, rows = miner.scan_shard(shard, _pattern())
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
    whole = gzip.compress(("\n".join(_LINES) + "\n").encode() * 500)
    shard.write_bytes(whole[:-40])
    with pytest.raises(EOFError):
        miner.scan_shard(shard, _pattern())
