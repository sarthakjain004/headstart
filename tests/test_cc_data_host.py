"""Tests for reading Common Crawl's index off data.commoncrawl.org (scripts/discover/cc_data_host.py).

Three things are pinned, each a way this path has returned nothing while looking healthy:

1. The SURT range a CDX ``matchType=domain`` target covers: the host and its subdomains, not a
   neighbouring domain that shares its prefix.
2. The sparse-block boundary. cluster.idx names one block per ~3000 index lines, keyed by the
   block's *first* key, so the block holding the start of a range normally starts below it.
   Dropping it returned "no captures" for Ashby (docs/discovery/common-crawl-mining.md).
3. Where the forward read starts. It must start on a line boundary: the first version read from
   1 KB before it, and a torn first line whose key sorted past the range ended the scan at once.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys

import pytest

_DISCOVER = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "discover"
sys.path.insert(0, str(_DISCOVER))

import cc_data_host


def test_domain_range_holds_the_host_and_its_subdomains_only():
    lo, hi = cc_data_host.domain_range("hrmdirect.com")
    assert (lo, hi) == (b"com,hrmdirect)", b"com,hrmdirect-")
    inside = [
        b"com,hrmdirect)/",
        b"com,hrmdirect,acme)/employment",
        b"com,hrmdirect,www)/",
    ]
    outside = [
        b"com,hrmdirec,x)/",
        b"com,hrmdirect-cdn,x)/",
        b"com,hrmdirecta,x)/",
        b"com,hrmdirect.x)/",
    ]
    assert all(lo <= k < hi for k in inside)
    assert not any(lo <= k < hi for k in outside)


def test_surt_host_reverses_labels_and_lowercases():
    assert cc_data_host.surt_host("Jobs.Lever.co") == "co,lever,jobs"


def _idx_line(key: str, block: int) -> bytes:
    return f"{key} 20260901000000\tcdx-00001.gz\t{block * 100}\t100\t{block}".encode()


LINES = [
    _idx_line("com,hrmdirea,x)/", 0),
    _idx_line("com,hrmdirect,aaa)/", 1),
    _idx_line("com,hrmdirect,mmm)/", 2),
    _idx_line("com,hrmdirf,x)/", 3),
]


def _blocks(lines, lo, hi):
    return [off // 100 for _, off, _ in cc_data_host.select_blocks(lines, lo, hi)]


def test_the_block_straddling_the_start_of_the_range_is_kept():
    # Range [com,hrmdirect,b, ...): its first keys live in block 1, which starts at `aaa`.
    assert _blocks(LINES, b"com,hrmdirect,b", b"com,hrmdirect-") == [1, 2]


def test_a_range_inside_one_block_returns_that_block():
    assert _blocks(LINES, b"com,hrmdirect,c", b"com,hrmdirect,d") == [1]


def test_the_range_ends_at_the_first_key_at_or_past_hi():
    lo, hi = cc_data_host.domain_range("hrmdirect.com")
    assert _blocks(LINES, lo, hi) == [0, 1, 2]


def test_a_range_past_every_line_sits_in_the_last_block():
    assert _blocks(LINES, b"com,zzz)", b"com,zzz-") == [3]


def test_a_range_before_every_line_is_empty():
    assert _blocks(LINES, b"aaa)", b"aaa-") == []


class _Resp:
    def __init__(self, content: bytes, total: int):
        self.content = content
        self.headers = {"content-range": f"bytes 0-0/{total}"}


def _serve(monkeypatch, files: dict[str, bytes]):
    """Serve `files` (by URL suffix) to `_get`, honouring its Range bounds."""

    def fake_get(url, *, start=None, end=None, tries=6):
        body = next(v for k, v in files.items() if url.endswith(k))
        return _Resp(body[start : end + 1], len(body))

    monkeypatch.setattr(cc_data_host, "_get", fake_get)


@pytest.mark.parametrize("window", [64, 150, 1 << 20])
def test_cc_blocks_finds_the_straddling_block_across_windows(monkeypatch, window):
    # Many filler lines either side, so the binary search has something to search and the forward
    # read has to cross window boundaries (a 64-byte window is smaller than one line). A read
    # starting 1 KB before the range, as the first version did, lands mid-way through a filler
    # line, reads its torn tail `z\t18100\t100\t181` as a line keyed `z`, which sorts past the
    # range and ends the scan with nothing: this test fails on that behaviour.
    keys = [f"com,aaa{i:04d})/" for i in range(200)]
    keys += ["com,hrmdirea,x)/", "com,hrmdirect,mmm)/", "com,hrmdirf,x)/"]
    keys += [f"com,zzz{i:04d})/" for i in range(200)]
    idx = b"\n".join(_idx_line(k, n) for n, k in enumerate(keys)) + b"\n"
    _serve(monkeypatch, {"cluster.idx": idx})
    monkeypatch.setattr(cc_data_host, "WINDOW", window)
    lo, hi = cc_data_host.domain_range("hrmdirect.com")
    got = cc_data_host.cc_blocks("CC-MAIN-2026-39", lo, hi)
    assert [off // 100 for _, off, _ in got] == [200, 201]


def test_capture_urls_keeps_only_keys_inside_the_range(monkeypatch):
    rows = [
        ("com,hrmdirea,x)/", "https://x.hrmdirea.com/"),
        ("com,hrmdirect,acme)/employment", "https://acme.hrmdirect.com/employment/"),
        ("com,hrmdirect-cdn,x)/", "https://x.hrmdirect-cdn.com/"),
    ]
    block = gzip.compress(
        b"\n".join(f"{k} 20260901 {json.dumps({'url': u})}".encode() for k, u in rows)
    )
    idx = _idx_line("com,hrmdirea,x)/", 0) + b"\n" + _idx_line("com,zzz)/", 1) + b"\n"
    _serve(monkeypatch, {"cluster.idx": idx, "cdx-00001.gz": block})
    monkeypatch.setattr(
        cc_data_host, "cc_blocks", lambda *_: [("cdx-00001.gz", 0, len(block))]
    )
    assert cc_data_host.capture_urls("CC-MAIN-2026-39", "hrmdirect.com") == [
        "https://acme.hrmdirect.com/employment/"
    ]


def test_known_hosts_share_sparse_blocks_and_failed_blocks_stay_incomplete(monkeypatch):
    from types import SimpleNamespace

    urls = [
        "https://a.example.com/jobs",
        "https://b.example.com/jobs",
        "https://other.example.com/",
    ]
    body = gzip.compress(
        b"\n".join(
            f"{cc_data_host.surt_host(u.split('/')[2])})/ 20260901 {json.dumps({'url': u})}".encode()
            for u in urls
        )
    )
    idx = (
        f"com,example)/ 20260901\tcdx.gz\t0\t{len(body)}\t0\n"
        "com,z)/ 20260901\tlast.gz\t0\t100\t1\n"
    ).encode()
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(content=idx if url.endswith("cluster.idx") else body)

    monkeypatch.setattr(cc_data_host, "_get", get)
    targets = {
        "a.example.com",
        "www.a.example.com",
        "b.example.com",
        "absent.example.com",
    }
    notified = {}
    result = cc_data_host.capture_known_hosts(
        "CC-MAIN-test",
        targets,
        on_host=lambda host, urls: notified.update({host: urls}),
    )
    assert notified == result
    assert result == {
        "a.example.com": [urls[0]],
        "www.a.example.com": [urls[0]],
        "b.example.com": [urls[1]],
        "absent.example.com": [],
    }
    assert len(calls) == 2  # one sparse index, one shared block

    monkeypatch.setattr(
        cc_data_host,
        "_get",
        lambda url, **kwargs: get(url) if url.endswith("cluster.idx") else None,
    )
    assert cc_data_host.capture_known_hosts("CC-MAIN-test", targets) == dict.fromkeys(
        targets
    )


@pytest.mark.parametrize("wall_status", [403, 429])
def test_data_host_429_backoff_and_rotation_use_shared_client(monkeypatch, wall_status):
    from types import SimpleNamespace

    from headstart.network import http, spare_egress

    spare_egress.reset()
    spare_egress.use_daemon(
        spare_egress.InMemoryEgressDaemon("socks5h://127.0.0.1:40000")
    )
    calls, sleeps, rotations = [], [], []
    outcomes = iter([wall_status, 503, 206])

    def request(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=next(outcomes), headers={"Retry-After": "7"})

    monkeypatch.setattr(http, "session", lambda: SimpleNamespace(request=request))
    monkeypatch.setattr(http.RoutePacer, "claim_delay", lambda *args: 0)
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        spare_egress, "rotate", lambda *args, **kwargs: rotations.append(True)
    )
    assert (
        cc_data_host._get(
            "https://data.commoncrawl.org/test", start=10, end=20, tries=3
        ).status_code
        == 206
    )
    assert not calls[0].get("proxies")
    assert calls[1]["proxies"]["https"].startswith("socks5h://")
    assert calls[2]["proxies"] == calls[1]["proxies"]
    assert calls[2]["headers"]["Range"] == "bytes=10-20"
    assert sleeps == [7, 7]
    assert rotations == [True]


def test_www_and_apex_use_the_same_surt_range():
    assert cc_data_host.surt_host("www.example.com") == "com,example"
    assert cc_data_host.domain_range("www.example.com") == cc_data_host.domain_range(
        "example.com"
    )
