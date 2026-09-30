"""Tests for the network reads the discovery miners share (scripts/discover/discovery_fetch.py):
a failed read is retried and never turned into a finding."""

import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import discovery_fetch


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://x.test/", code, "no", headers, None)


def test_a_429_or_a_timeout_is_waited_out_then_read(monkeypatch):
    pauses = []
    monkeypatch.setattr(discovery_fetch.time, "sleep", pauses.append)
    calls = iter([_http_error(429, "7"), TimeoutError("timed out"), _Response(b"body")])

    def urlopen(request, timeout):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(discovery_fetch.urllib.request, "urlopen", urlopen)
    assert (
        discovery_fetch.fetch_bytes("https://x.test/", attempts=4, pause=1) == b"body"
    )
    assert pauses == [7, 2]  # the 429's own Retry-After, then the growing pause


def test_a_source_that_never_answers_raises_rather_than_returning_an_empty_result(
    monkeypatch,
):
    monkeypatch.setattr(discovery_fetch.time, "sleep", lambda seconds: None)

    def urlopen(request, timeout):
        raise _http_error(429)

    monkeypatch.setattr(discovery_fetch.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError):
        discovery_fetch.fetch_bytes("https://x.test/", attempts=3, pause=1)


def test_save_whole_leaves_no_partial_file_and_a_cache_is_read_back(
    tmp_path, monkeypatch
):
    target = tmp_path / "cache" / "roster.yaml"
    discovery_fetch.save_whole(target, b"abc")
    assert target.read_bytes() == b"abc"
    assert [p.name for p in target.parent.iterdir()] == ["roster.yaml"]
    monkeypatch.setattr(
        discovery_fetch, "fetch_bytes", lambda url: pytest.fail("cache ignored")
    )
    assert discovery_fetch.fetch_cached("https://x.test/", target) == b"abc"
