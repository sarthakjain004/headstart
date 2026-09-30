"""Tests for the JobTech snapshot miner's streaming array reader
(scripts/discover/mine_jobtech_jobstream_snapshot.py). A script under `scripts/discover`."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import mine_jobtech_jobstream_snapshot as miner

ADS = [
    {
        "id": str(n),
        "application_details": {"url": f"https://acme{n}.teamtailor.com/jobs/{n}"},
        "headline": "Montör",
    }
    for n in range(20)
]


def test_the_snapshot_array_is_read_one_ad_at_a_time_across_chunk_boundaries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(miner, "CHUNK", 16)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(ADS), encoding="utf-8")
    assert list(miner.ads(path)) == ADS


def test_a_file_that_is_not_an_array_is_refused(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("<html>rate limited</html>", encoding="utf-8")
    with pytest.raises(ValueError):
        list(miner.ads(path))


def test_a_cut_stream_yields_every_complete_ad_then_says_it_was_cut(
    tmp_path, monkeypatch
):
    """The server ends a slow transfer mid-ad and curl reports success, so the array's closing
    bracket is the only proof the snapshot is whole."""
    monkeypatch.setattr(miner, "CHUNK", 16)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(ADS)[:-60], encoding="utf-8")
    reader = miner.ads(path)
    seen = []
    with pytest.raises(miner.SnapshotCut):
        while True:
            seen.append(next(reader))
    assert seen == ADS[: len(seen)]
    assert 0 < len(seen) < len(ADS)
