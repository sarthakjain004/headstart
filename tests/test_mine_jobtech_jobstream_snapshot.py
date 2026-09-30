"""Tests for the JobTech snapshot miner (scripts/discover/mine_jobtech_jobstream_snapshot.py): the
streaming array reader, the host scan, and that a cut snapshot is never kept as the cache."""

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


# --- scan and main ---------------------------------------------------------------------------


def _point_at(tmp_path, monkeypatch):
    work = tmp_path / "jobtech"
    monkeypatch.setattr(miner, "WORK", work)
    monkeypatch.setattr(miner, "SNAPSHOT", work / "snapshot.json")
    monkeypatch.setattr(miner, "CUT", work / "snapshot.cut.json")
    monkeypatch.setattr(miner, "HOSTS", work / "hosts.txt")
    staged = []
    monkeypatch.setattr(
        miner,
        "stage_by_ats",
        lambda rows, source: staged.append((sorted(rows), source)),
    )
    return work, staged


def _snapshot(ads, cut=False):
    text = json.dumps(ads)
    return text[:-60] if cut else text


def test_each_new_board_host_reaches_disk_as_it_is_found_even_if_the_read_dies(
    tmp_path, monkeypatch
):
    """Hosts used to sit in memory until the whole 300 MB was read."""

    def dying(path):
        yield ADS[0]
        yield ADS[1]
        yield ADS[0]  # a repeat host is written once
        raise RuntimeError("disk error")

    monkeypatch.setattr(miner, "ads", dying)
    hosts_path = tmp_path / "hosts.txt"
    with pytest.raises(RuntimeError):
        miner.scan(tmp_path / "snapshot.json", hosts_path)
    assert hosts_path.read_text().split() == [
        "acme0.teamtailor.com",
        "acme1.teamtailor.com",
    ]


def test_a_scan_reports_progress_and_only_counts_hosts_of_a_board_family(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(miner, "PROGRESS_EVERY_ADS", 5)
    ads = [
        *ADS[:9],
        {"application_details": {"url": "https://example.com/apply"}},
        {"id": "no url"},
    ]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(ads), encoding="utf-8")
    result = miner.scan(path, tmp_path / "hosts.txt")
    assert result == miner.Scan(ads=11, hosts=9, cut=False)
    assert "5 ads read" in capsys.readouterr().out


def test_a_cut_download_is_never_kept_as_the_cache_but_what_it_held_is_staged(
    tmp_path, monkeypatch
):
    work, staged = _point_at(tmp_path, monkeypatch)
    downloads = []

    def download(url, dest, resume=True):
        downloads.append(resume)
        dest.write_text(_snapshot(ADS, cut=True), encoding="utf-8")

    monkeypatch.setattr(miner, "download", download)
    miner.main()
    assert not (work / "snapshot.json").exists()
    assert (work / "snapshot.cut.json").exists()
    assert downloads == [False]  # a generated export is never resumed
    rows, source = staged[0]
    assert (
        source == "jobtech_jobstream_snapshot" and rows
    )  # the label equals the module suffix
    miner.main()  # a rerun downloads again instead of trusting the cut file
    assert downloads == [False, False]


def test_a_whole_cached_snapshot_is_read_without_downloading(tmp_path, monkeypatch):
    work, staged = _point_at(tmp_path, monkeypatch)
    work.mkdir()
    (work / "snapshot.json").write_text(_snapshot(ADS), encoding="utf-8")
    monkeypatch.setattr(
        miner, "download", lambda *a, **k: pytest.fail("downloaded a whole cache")
    )
    miner.main()
    assert (work / "snapshot.json").exists()
    assert len(staged[0][0]) == len(ADS)


def test_a_cut_cached_snapshot_is_moved_aside_and_downloaded_again(
    tmp_path, monkeypatch
):
    work, staged = _point_at(tmp_path, monkeypatch)
    work.mkdir()
    (work / "snapshot.json").write_text(_snapshot(ADS, cut=True), encoding="utf-8")

    def download(url, dest, resume=True):
        dest.write_text(_snapshot(ADS), encoding="utf-8")

    monkeypatch.setattr(miner, "download", download)
    miner.main()
    assert (work / "snapshot.cut.json").exists() and (work / "snapshot.json").exists()
    assert len(staged[0][0]) == len(ADS)  # the whole download, not the cut cache
