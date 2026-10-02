"""Tests for the network reads the discovery miners share (scripts/discover/discovery_fetch.py):
a failed read is retried and never turned into a finding, and a big download resumes only a file
whose remote is unchanged. A script under `scripts/discover`, imported by name."""

import http.client
import io
import json
import shutil
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _stub_urlopen(monkeypatch, outcomes):
    calls = iter(outcomes)

    def urlopen(request, timeout):
        outcome = next(calls)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(discovery_fetch.urllib.request, "urlopen", urlopen)


def test_a_429_or_a_timeout_is_waited_out_then_read(monkeypatch):
    pauses = []
    monkeypatch.setattr(discovery_fetch.time, "sleep", pauses.append)
    _stub_urlopen(
        monkeypatch,
        [_http_error(429, "7"), TimeoutError("timed out"), _Response(b"body")],
    )
    assert (
        discovery_fetch.fetch_bytes("https://x.test/", attempts=4, pause=1) == b"body"
    )
    assert pauses == [7, 2]  # the 429's own Retry-After, then the growing pause


def test_a_truncated_body_is_retried_like_any_other_transport_failure(monkeypatch):
    """`IncompleteRead` is an HTTPException, not an OSError, and used to escape the retry."""
    pauses = []
    monkeypatch.setattr(discovery_fetch.time, "sleep", pauses.append)
    _stub_urlopen(
        monkeypatch,
        [http.client.IncompleteRead(b"ab", 10), _Response(b"whole")],
    )
    assert (
        discovery_fetch.fetch_bytes("https://x.test/", attempts=3, pause=1) == b"whole"
    )
    assert pauses == [1]


def test_a_huge_retry_after_is_capped(monkeypatch):
    """Workable once sent 24,255 s; past MAX_RETRY_AFTER waiting costs more than the read buys."""
    pauses = []
    monkeypatch.setattr(discovery_fetch.time, "sleep", pauses.append)
    _stub_urlopen(monkeypatch, [_http_error(429, "24255"), _Response(b"ok")])
    discovery_fetch.fetch_bytes("https://x.test/", attempts=2, pause=1)
    assert pauses == [discovery_fetch.MAX_RETRY_AFTER]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_a_permanent_http_error_raises_at_once_without_a_retry(monkeypatch, status):
    pauses = []
    monkeypatch.setattr(discovery_fetch.time, "sleep", pauses.append)
    _stub_urlopen(monkeypatch, [_http_error(status)])
    with pytest.raises(discovery_fetch.FetchFailed):
        discovery_fetch.fetch_bytes("https://x.test/", attempts=6, pause=15)
    assert pauses == []


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503])
def test_a_retryable_status_is_retried_until_the_attempts_are_spent(
    monkeypatch, status
):
    monkeypatch.setattr(discovery_fetch.time, "sleep", lambda seconds: None)
    _stub_urlopen(monkeypatch, [_http_error(status)] * 3)
    with pytest.raises(discovery_fetch.FetchFailed):
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


def test_refresh_reads_the_cache_again(tmp_path, monkeypatch):
    target = tmp_path / "roster.yaml"
    discovery_fetch.save_whole(target, b"old")
    monkeypatch.setattr(discovery_fetch, "fetch_bytes", lambda url: b"new")
    assert (
        discovery_fetch.fetch_cached("https://x.test/", target, refresh=True) == b"new"
    )


# --- download, against a local server that honours Range and If-Range -----------------------


class _Origin:
    """A one-file HTTP server whose content can be republished, recording each request."""

    def __init__(self):
        self.content = b""
        self.etag = '"v1"'
        self.status = 200
        self.requests: list[tuple[str, str | None]] = []
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self, send_body):
                origin.requests.append((self.command, self.headers.get("Range")))
                if origin.status != 200:
                    self.send_response(origin.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = origin.content
                start = 0
                ranged = self.headers.get("Range", "")
                if_range = self.headers.get("If-Range")
                if ranged.startswith("bytes=") and (if_range in (None, origin.etag)):
                    start = int(ranged[6:].split("-")[0])
                if start >= len(body) and start:
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206 if start else 200)
                if start:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}"
                    )
                self.send_header("ETag", origin.etag)
                self.send_header("Content-Length", str(len(body) - start))
                self.end_headers()
                if send_body:
                    self.wfile.write(body[start:])

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/file.bin"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def origin(monkeypatch):
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    monkeypatch.setattr(discovery_fetch.time, "sleep", lambda seconds: None)
    server = _Origin()
    yield server
    server.close()


def _begin_partial(origin, dest, first_bytes):
    """A partial `dest` as an interrupted `download` leaves it: its head plus the validators."""
    dest.write_bytes(origin.content[:first_bytes])
    remote = discovery_fetch._remote(origin.url)
    discovery_fetch._sidecar(dest).write_text(
        json.dumps({"url": origin.url, **remote}), encoding="utf-8"
    )
    origin.requests.clear()


def test_an_unchanged_partial_file_is_resumed_from_where_it_stopped(origin, tmp_path):
    origin.content = bytes(range(200))
    dest = tmp_path / "file.bin"
    _begin_partial(origin, dest, 60)
    discovery_fetch.download(origin.url, dest)
    assert dest.read_bytes() == origin.content
    assert ("GET", "bytes=60-") in origin.requests


def test_a_file_republished_longer_is_fetched_whole_not_spliced_onto_the_old_head(
    origin, tmp_path
):
    """100 bytes republished as 160 came back as the old 100 plus the new tail."""
    origin.content = b"a" * 100
    dest = tmp_path / "file.bin"
    _begin_partial(origin, dest, 60)
    origin.content, origin.etag = b"b" * 160, '"v2"'
    discovery_fetch.download(origin.url, dest)
    assert dest.read_bytes() == b"b" * 160


def test_a_same_size_republish_does_not_stay_stale(origin, tmp_path):
    origin.content = b"a" * 100
    dest = tmp_path / "file.bin"
    _begin_partial(origin, dest, 100)  # the whole file is already on disk
    origin.content, origin.etag = b"b" * 100, '"v2"'
    discovery_fetch.download(origin.url, dest)
    assert dest.read_bytes() == b"b" * 100


def test_a_complete_unchanged_file_is_left_as_it_is(origin, tmp_path):
    origin.content = b"c" * 100
    dest = tmp_path / "file.bin"
    _begin_partial(origin, dest, 100)
    discovery_fetch.download(origin.url, dest)
    assert dest.read_bytes() == b"c" * 100
    assert all(
        method == "HEAD" or rng == "bytes=100-" for method, rng in origin.requests
    )


def test_a_partial_file_whose_remote_cannot_be_verified_starts_over(
    origin, tmp_path, monkeypatch
):
    origin.content = b"d" * 100
    dest = tmp_path / "file.bin"
    _begin_partial(origin, dest, 40)
    monkeypatch.setattr(discovery_fetch, "_remote", lambda url: None)
    discovery_fetch.download(origin.url, dest)
    assert dest.read_bytes() == b"d" * 100
    assert ("GET", None) in origin.requests  # a whole read, no Range


def test_a_partial_file_with_no_recorded_validators_starts_over(origin, tmp_path):
    origin.content = b"e" * 100
    dest = tmp_path / "file.bin"
    dest.write_bytes(b"stale head that no run recorded a remote for")
    discovery_fetch.download(origin.url, dest)
    assert dest.read_bytes() == b"e" * 100


def test_resume_false_drops_a_partial_file_and_fetches_it_whole(origin, tmp_path):
    origin.content = b"f" * 100
    dest = tmp_path / "file.bin"
    dest.write_bytes(b"f" * 30)
    discovery_fetch.download(origin.url, dest, resume=False)
    assert dest.read_bytes() == b"f" * 100
    assert all(method == "GET" and rng is None for method, rng in origin.requests)


def test_a_permanent_404_raises_at_once_instead_of_retrying_forty_times(
    origin, tmp_path
):
    origin.status = 404
    with pytest.raises(discovery_fetch.FetchFailed):
        discovery_fetch.download(origin.url, tmp_path / "file.bin", resume=False)
    assert len([r for r in origin.requests if r[0] == "GET"]) == 1


def test_a_permanent_status_is_judged_by_what_curl_printed_not_by_its_exit_code(
    tmp_path, monkeypatch
):
    """The same 404 exits 22 from a local server and 56 from raw.githubusercontent.com."""
    runs = []
    monkeypatch.setattr(
        discovery_fetch,
        "_curl",
        lambda *args: runs.append(args) or (56, "404"),
    )
    with pytest.raises(discovery_fetch.FetchFailed, match="HTTP 404"):
        discovery_fetch.download("https://x.test/f", tmp_path / "f", resume=False)
    assert len(runs) == 1


def test_a_download_that_never_adds_a_byte_stops_after_the_stalled_attempts(
    origin, tmp_path
):
    origin.status = 503
    with pytest.raises(discovery_fetch.FetchFailed):
        discovery_fetch.download(origin.url, tmp_path / "file.bin", resume=False)
    gets = len([r for r in origin.requests if r[0] == "GET"])
    assert gets <= discovery_fetch.NO_RESUME_ATTEMPTS


def test_remove_download_forgets_the_file_and_its_validators(origin, tmp_path):
    origin.content = b"g" * 10
    dest = tmp_path / "file.bin"
    discovery_fetch.download(origin.url, dest)
    assert discovery_fetch._sidecar(dest).exists()
    discovery_fetch.remove_download(dest)
    assert not dest.exists() and not discovery_fetch._sidecar(dest).exists()
